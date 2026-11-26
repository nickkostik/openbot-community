use std::{collections::BTreeMap, fs, path::Path};

use serde::Deserialize;
use serde_json::{Value, json};
use time::{Duration, OffsetDateTime};

use super::{
    BODIES, at, iso,
    read::{DateRange, Rows, filter, string},
    usage::{Totals, count},
};

const STATS_SAMPLE: usize = 1000;
pub(super) const FACETS_SAMPLE: usize = 5000;
const FACET_OPTIONS: usize = 200;
const USAGE_SAMPLE: usize = 5000;
const USAGE_GROUPS: usize = 50;
const DISK_SAMPLE: usize = 5000;

pub fn stats(root: &Path, rows: &Rows) -> Value {
    let sample = &rows.values[rows.values.len().saturating_sub(STATS_SAMPLE)..];
    let mut totals = Totals::default();
    let mut body_bytes: u64 = 0;
    let mut ok = 0;
    for row in sample {
        ok += usize::from(row["ok"] == true);
        if row["channel"] != "custom-host" {
            totals.add(row);
        }
        body_bytes = body_bytes.saturating_add(count(row, "bodyBytes"));
    }
    let (body_files, body_disk_bytes) = body_disk_size(&root.join(BODIES));
    json!({
        "records":rows.values.len(),"scanned":sample.len(),"approximate":rows.approximate || sample.len()<rows.values.len(),
        "ok":ok,"errors":sample.len()-ok,"tokenBasis":"provider","providerSampleRecords":totals.requests,
        "excludedHarnessRecords":sample.len()-totals.requests,"promptTokens":totals.prompt_tokens,
        "completionTokens":totals.completion_tokens,"totalTokens":totals.total_tokens,
        "cachedTokens":totals.cached_tokens,"reasoningTokens":totals.reasoning_tokens,"bodyBytes":body_bytes,
        "bodyFiles":body_files,"bodyDiskBytes":body_disk_bytes,"bodiesApproximate":body_files>DISK_SAMPLE,
        "diskBytes":rows.disk_bytes.saturating_add(body_disk_bytes),
    })
}

fn body_disk_size(dir: &Path) -> (usize, u64) {
    if !fs::symlink_metadata(dir).is_ok_and(|metadata| metadata.is_dir()) {
        return (0, 0);
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return (0, 0);
    };
    let mut files = 0;
    let mut bytes: u64 = 0;
    for entry in entries.flatten() {
        if entry.file_type().is_ok_and(|kind| kind.is_file()) {
            files += 1;
            if files <= DISK_SAMPLE
                && let Ok(metadata) = entry.metadata()
            {
                bytes = bytes.saturating_add(metadata.len());
            }
        }
    }
    if files > DISK_SAMPLE {
        bytes = ((bytes as f64 / DISK_SAMPLE as f64) * files as f64).round() as u64;
    }
    (files, bytes)
}

pub fn facets(rows: &Rows) -> Value {
    let sample = &rows.values[rows.values.len().saturating_sub(FACETS_SAMPLE)..];
    let approximate = rows.approximate || sample.len() < rows.values.len();
    let mut bots = BTreeMap::new();
    for row in sample {
        let id = string(row, "botId");
        let name = string(row, "botName");
        if id.is_empty() && name.is_empty() {
            continue;
        }
        let option = bots
            .entry((id, if id.is_empty() { name } else { "" }))
            .or_insert_with(|| row.clone());
        if option["botName"].is_null() && !name.is_empty() {
            option["botName"] = json!(name);
        }
    }
    let mut bots: Vec<_> = bots
        .into_values()
        .map(|row| {
            let mut entry = serde_json::Map::new();
            for field in ["botId", "botName"] {
                if let Some(text) = row[field].as_str().filter(|text| !text.is_empty()) {
                    entry.insert(field.into(), json!(text));
                }
            }
            (
                row["botName"]
                    .as_str()
                    .unwrap_or_else(|| string(&row, "botId"))
                    .to_lowercase(),
                entry,
            )
        })
        .collect();
    bots.sort_by(|a, b| a.0.cmp(&b.0));
    let bots: Vec<_> = bots
        .into_iter()
        .take(FACET_OPTIONS)
        .map(|(_, entry)| entry)
        .collect();
    let chat_types: Vec<_> = ["group", "dm", "routine"]
        .into_iter()
        .filter(|kind| sample.iter().any(|row| row["chatType"] == *kind))
        .collect();
    json!({
        "sampled":sample.len(),"total":rows.values.len(),
        "model":facet(sample,"model",approximate),"provider":facet(sample,"providerId",approximate),
        "channel":facet(sample,"channel",approximate),"status":facet(sample,"status",approximate),
        "bots":bots,"chatTypes":chat_types,
    })
}

fn facet(rows: &[Value], field: &str, approximate: bool) -> Value {
    let mut values = BTreeMap::new();
    for row in rows {
        let value = if field == "status" {
            count(row, "status").to_string()
        } else {
            string(row, field).to_owned()
        };
        if !value.is_empty() {
            *values.entry(value).or_insert(0_usize) += 1;
        }
    }
    let mut values: Vec<_> = values.into_iter().collect();
    values.sort_by_key(|(_, count)| std::cmp::Reverse(*count));
    let approximate = approximate || values.len() > FACET_OPTIONS;
    let values: Vec<_> = values
        .into_iter()
        .take(FACET_OPTIONS)
        .map(|(value, count)| json!({"value":value,"count":count}))
        .collect();
    json!({"values":values,"approximate":approximate})
}

#[derive(Default, Deserialize)]
#[serde(default)]
pub struct UsageFilter {
    pub range: String,
    pub from: String,
    pub to: String,
    pub model: String,
    pub provider: String,
}

pub fn usage(rows: &Rows, mut query: UsageFilter) -> Value {
    let (hours, bucket_seconds) = match query.range.as_str() {
        "1h" => (1, 300),
        "6h" => (6, 900),
        "24h" => (24, 3600),
        "7d" => (168, 21_600),
        "30d" => (720, 86_400),
        _ => (0, 86_400),
    };
    if hours > 0 && query.from.is_empty() && query.to.is_empty() {
        let now = OffsetDateTime::now_utc();
        query.from = iso(now - Duration::hours(hours));
        query.to = iso(now);
    }
    let dates = DateRange::new(&query.from, &query.to);
    let mut approximate = rows.approximate;
    let mut excluded = 0;
    let mut totals = Totals::default();
    let mut day = BTreeMap::new();
    let mut by_model = BTreeMap::new();
    let mut by_provider = BTreeMap::new();
    let mut by_purpose = BTreeMap::new();
    let mut buckets = BTreeMap::new();
    for row in rows.values.iter().rev() {
        let started = string(row, "startedAt");
        let provider = provider(row);
        let model = model(row, provider);
        if !dates.contains(started)
            || !(filter(row, "model", &query.model) || model == query.model)
            || (!query.provider.is_empty() && provider != query.provider)
        {
            continue;
        }
        if row["channel"] == "custom-host" {
            excluded += 1;
            continue;
        }
        if totals.requests >= USAGE_SAMPLE {
            approximate = true;
            continue;
        }
        totals.add(row);
        day.entry(started.get(..10).unwrap_or("(unknown)").to_owned())
            .or_insert_with(Totals::default)
            .add(row);
        by_model
            .entry(model)
            .or_insert_with(Totals::default)
            .add(row);
        by_provider
            .entry(provider.to_owned())
            .or_insert_with(Totals::default)
            .add(row);
        by_purpose
            .entry(purpose(row).to_owned())
            .or_insert_with(Totals::default)
            .add(row);
        let bucket = at(started)
            .and_then(|date| {
                OffsetDateTime::from_unix_timestamp(
                    date.unix_timestamp().div_euclid(bucket_seconds) * bucket_seconds,
                )
                .ok()
            })
            .map_or_else(|| "(unknown)".into(), iso);
        buckets
            .entry(bucket)
            .or_insert_with(Totals::default)
            .add(row);
    }
    let by_day = groups(day, false, &mut approximate);
    let by_model = groups(by_model, true, &mut approximate);
    let by_provider = groups(by_provider, true, &mut approximate);
    let by_purpose = groups(by_purpose, false, &mut approximate);
    let buckets = groups(buckets, false, &mut approximate);
    let mut summary = totals.view("summary");
    summary["successRate"] = json!(if totals.requests == 0 {
        0.0
    } else {
        totals.ok as f64 / totals.requests as f64
    });
    json!({
        "approximate":approximate,"scanned":totals.requests,"total":rows.values.len(),
        "basis":"provider","excludedHarnessRecords":excluded,"from":query.from,"to":query.to,
        "byDay":by_day,"byModel":by_model,"byProvider":by_provider,"byPurpose":by_purpose,
        "summary":summary,"buckets":buckets,"bucketMs":bucket_seconds*1000,
    })
}

fn provider(row: &Value) -> &str {
    ["providerId", "providerName"]
        .iter()
        .map(|field| string(row, field))
        .find(|value| !value.is_empty())
        .unwrap_or("(unknown)")
}

fn model(row: &Value, provider: &str) -> String {
    let model = row["model"]
        .as_str()
        .filter(|model| !model.is_empty())
        .unwrap_or("(unknown)");
    if provider == "(unknown)"
        || model
            .strip_prefix(provider)
            .is_some_and(|rest| rest.starts_with(':'))
    {
        model.into()
    } else {
        format!("{provider}:{model}")
    }
}

fn purpose(row: &Value) -> &str {
    match string(row, "purpose") {
        known @ ("chat" | "auxiliary" | "compaction") => known,
        _ if string(row, "conversationId").starts_with("openbot-compaction:") => "compaction",
        _ => "chat",
    }
}

fn groups(map: BTreeMap<String, Totals>, most_used: bool, approximate: &mut bool) -> Vec<Value> {
    let mut groups: Vec<_> = map.into_iter().collect();
    if most_used {
        groups.sort_by_key(|(_, totals)| std::cmp::Reverse(totals.requests));
    }
    *approximate |= groups.len() > USAGE_GROUPS;
    groups
        .into_iter()
        .take(USAGE_GROUPS)
        .map(|(key, totals)| totals.view(&key))
        .collect()
}
