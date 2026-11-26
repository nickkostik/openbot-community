use std::{
    fs::{self, File},
    io::{self, BufRead, BufReader, Read, Seek, SeekFrom},
    path::Path,
};

use serde::Deserialize;
use serde_json::{Value, json};

use super::{BODIES, REQUESTS, at};

const MAX_LOG_SCAN_BYTES: u64 = 64 * 1024 * 1024;
pub(super) const MAX_ROW_BYTES: usize = 128 * 1024;
const MAX_LEGACY_BODY_BYTES: u64 = 64 * 1024 * 1024;

pub struct Rows {
    pub values: Vec<Value>,
    pub approximate: bool,
    pub disk_bytes: u64,
}

impl Rows {
    pub fn load(root: &Path, name: &str) -> io::Result<Self> {
        let Some(mut file) = regular_file(&root.join(name))? else {
            return Ok(Self {
                values: vec![],
                approximate: false,
                disk_bytes: 0,
            });
        };
        let disk_bytes = file.metadata()?.len();
        let start = disk_bytes.saturating_sub(MAX_LOG_SCAN_BYTES);
        // Start one byte earlier so an exact line boundary does not lose a row.
        file.seek(SeekFrom::Start(start.saturating_sub(1)))?;
        let mut lines = BufReader::new(file.take(disk_bytes - start.saturating_sub(1)));
        let mut values = Vec::new();
        let mut approximate = start > 0;
        if start > 0 {
            lines.skip_until(b'\n')?;
        }
        let mut line = Vec::new();
        loop {
            line.clear();
            let count = (&mut lines)
                .take((MAX_ROW_BYTES + 1) as u64)
                .read_until(b'\n', &mut line)?;
            if count == 0 {
                break;
            }
            if count > MAX_ROW_BYTES {
                approximate = true;
                if !line.ends_with(b"\n") {
                    lines.skip_until(b'\n')?;
                }
                continue;
            }
            if let Ok(row) = serde_json::from_slice::<Value>(&line)
                && row.is_object()
            {
                values.push(row);
            }
        }
        Ok(Self {
            values,
            approximate,
            disk_bytes,
        })
    }

    pub fn list(mut self, mut query: Filter) -> Value {
        query.q = query.q.trim().to_lowercase();
        let dates = DateRange::new(&query.from, &query.to);
        self.values.retain(|row| query.matches(row, &dates));
        newest_first(&mut self.values, "startedAt");
        let total = self.values.len();
        let page = positive(&query.page, 1);
        let page_size = positive(&query.page_size, 50).min(100);
        let items: Vec<_> = self
            .values
            .into_iter()
            .skip((page - 1).saturating_mul(page_size))
            .take(page_size)
            .collect();
        json!({"items":items,"total":total,"page":page,"pageSize":page_size,"approximate":self.approximate})
    }

    pub fn events(mut self, query: EventFilter) -> Value {
        self.values.retain(|row| {
            (query.severity.is_empty()
                || string(row, "severity").eq_ignore_ascii_case(&query.severity))
                && filter(row, "type", &query.kind)
                && filter(row, "requestId", &query.request_id)
        });
        newest_first(&mut self.values, "at");
        let total = self.values.len();
        self.values.truncate(positive(&query.limit, 100).min(500));
        json!({"items":self.values,"total":total,"approximate":self.approximate})
    }
}

fn newest_first(rows: &mut [Value], field: &str) {
    rows.sort_by_cached_key(|row| {
        std::cmp::Reverse(at(string(row, field)).map(|date| date.unix_timestamp_nanos()))
    });
}

fn positive(value: &str, fallback: usize) -> usize {
    value
        .parse()
        .ok()
        .filter(|value| *value > 0)
        .unwrap_or(fallback)
}

pub fn string<'a>(row: &'a Value, field: &str) -> &'a str {
    row[field].as_str().unwrap_or_default()
}

pub fn filter(row: &Value, field: &str, query: &str) -> bool {
    query.is_empty() || row[field] == query
}

pub struct DateRange<'a> {
    from: (&'a str, Option<time::OffsetDateTime>),
    to: (&'a str, Option<time::OffsetDateTime>),
}

impl<'a> DateRange<'a> {
    pub fn new(from: &'a str, to: &'a str) -> Self {
        Self {
            from: (from, at(from)),
            to: (to, at(to)),
        }
    }

    pub fn contains(&self, value: &str) -> bool {
        if self.from.0.is_empty() && self.to.0.is_empty() {
            return true;
        }
        if value.is_empty() {
            return false;
        }
        let date = at(value);
        let compare = |(raw, bound): (&str, Option<time::OffsetDateTime>)| {
            date.zip(bound)
                .map_or_else(|| value.cmp(raw), |(value, bound)| value.cmp(&bound))
        };
        (self.from.0.is_empty() || !compare(self.from).is_lt())
            && (self.to.0.is_empty() || !compare(self.to).is_gt())
    }
}

#[derive(Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct Filter {
    pub q: String,
    model: String,
    bot_id: String,
    bot_name: String,
    chat_type: String,
    from: String,
    to: String,
    channel: String,
    ok: String,
    page: String,
    page_size: String,
}

impl Filter {
    fn matches(&self, row: &Value, dates: &DateRange<'_>) -> bool {
        let channel = match self.channel.as_str() {
            "" => true,
            "custom" => row["channel"] != "official",
            channel => row["channel"] == channel,
        };
        if !channel
            || !filter(row, "model", &self.model)
            || !(filter(row, "botId", &self.bot_id) || row["botName"] == self.bot_id)
            || !filter(row, "botName", &self.bot_name)
            || !filter(row, "chatType", &self.chat_type)
            || self.ok.parse::<bool>().is_ok_and(|ok| row["ok"] != ok)
            || !dates.contains(string(row, "startedAt"))
        {
            return false;
        }
        self.q.is_empty()
            || [
                "id",
                "model",
                "error",
                "providerId",
                "providerName",
                "channel",
                "inboundEndpoint",
                "upstreamEndpoint",
                "clientName",
                "userAgent",
                "conversationId",
                "requestId",
                "botId",
                "botName",
                "chatType",
                "chatName",
            ]
            .iter()
            .map(|field| string(row, field))
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase()
            .contains(&self.q)
    }
}

#[derive(Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct EventFilter {
    severity: String,
    #[serde(rename = "type")]
    kind: String,
    request_id: String,
    limit: String,
}

pub fn safe_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
}

pub fn detail(root: &Path, id: &str) -> io::Result<Option<Value>> {
    if !safe_id(id) {
        return Ok(None);
    }
    let Some(mut row) = Rows::load(root, REQUESTS)?
        .values
        .into_iter()
        .find(|row| row["id"] == id)
    else {
        return Ok(None);
    };
    let dir = root.join(BODIES);
    let bodies = match fs::symlink_metadata(&dir) {
        Ok(info) if info.is_dir() => regular_file(&dir.join(format!("{id}.json")))
            .ok()
            .flatten()
            .and_then(|file| {
                let mut bytes = Vec::new();
                file.take(MAX_LEGACY_BODY_BYTES + 1)
                    .read_to_end(&mut bytes)
                    .ok()?;
                (bytes.len() as u64 <= MAX_LEGACY_BODY_BYTES)
                    .then(|| serde_json::from_slice::<Value>(&bytes).ok())
                    .flatten()
            })
            .unwrap_or_default(),
        _ => Value::Null,
    };
    for field in ["request", "response", "requestFull", "responseFull"] {
        if let Some(value) = bodies.get(field) {
            row[field] = value.clone();
        }
    }
    if (row["hasRequest"] == true && bodies.get("request").is_none())
        || (row["hasResponse"] == true && bodies.get("response").is_none())
    {
        row["bodyMissing"] = json!(true);
    }
    Ok(Some(row))
}

pub(super) fn regular_file(path: &Path) -> io::Result<Option<File>> {
    match fs::symlink_metadata(path) {
        Ok(info) if info.is_file() => File::open(path).map(Some),
        Ok(_) => Err(io::ErrorKind::InvalidInput.into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}
