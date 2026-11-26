mod redact;

use std::{
    fs::{self, OpenOptions},
    io::{self, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant},
};

use axum::{
    body::Bytes,
    http::{HeaderMap, StatusCode, header},
};
use serde_json::{Map, Value, json};
use time::OffsetDateTime;
use url::Url;

use crate::auth::unique_id;

use super::{BODIES, Logs, REQUESTS, Settings, iso, usage};

pub struct Trace {
    logs: Logs,
    settings: Settings,
    started: Instant,
    row: Map<String, Value>,
    attempts: Vec<Value>,
    capture: Capture,
    active_secret: Option<String>,
    complete: bool,
}

#[derive(Default)]
struct Capture {
    request: Option<Bytes>,
    response: Option<Bytes>,
    chunks: Vec<Value>,
    chunk_bytes: usize,
    truncated: bool,
}

impl Logs {
    pub async fn begin(&self) -> Option<Trace> {
        self.writes.as_ref()?;
        let started = Instant::now();
        let wall_time = OffsetDateTime::now_utc();
        let root = self.root.clone();
        let settings = tokio::task::spawn_blocking(move || Settings::load(&root))
            .await
            .ok()?;
        if !settings.logging_enabled {
            return None;
        }
        let row = Map::from_iter([
            ("id".into(), json!(unique_id().ok()?)),
            ("startedAt".into(), json!(iso(wall_time))),
            ("channel".into(), json!("hop")),
            ("inboundEndpoint".into(), json!("/v1/chat/completions")),
            ("stream".into(), json!(false)),
        ]);
        Some(Trace {
            logs: self.clone(),
            settings,
            started,
            row,
            attempts: Vec::new(),
            capture: Capture::default(),
            active_secret: None,
            complete: false,
        })
    }

    fn submit(&self, make: impl FnOnce(&Path) -> io::Result<()> + Send + 'static) {
        let permit = self
            .writes
            .as_ref()
            .and_then(|queue| queue.clone().try_acquire_owned().ok());
        let Some(permit) = permit else {
            eprintln!("OpenBot request log write queue is full.");
            return;
        };
        let root = self.root.clone();
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            if let Err(error) = make(&root) {
                eprintln!("OpenBot could not record a request ({:?}).", error.kind());
            }
        });
    }
}

impl Trace {
    pub fn inbound(&mut self, body: &Value, headers: &HeaderMap) {
        self.row
            .insert("stream".into(), json!(body["stream"] == true));
        let user_agent = headers
            .get(header::USER_AGENT)
            .and_then(|value| value.to_str().ok());
        let conversation = crate::hop::request::conversation(body);
        let request_id = body["openbotRequestId"]
            .as_str()
            .filter(|id| !id.trim().is_empty())
            .or_else(|| {
                headers
                    .get("x-request-id")
                    .and_then(|value| value.to_str().ok())
            });
        self.text("userAgent", user_agent, 512);
        self.text("model", body["model"].as_str(), 200);
        self.text("conversationId", conversation, 128);
        self.text("requestId", request_id, 128);
        let purpose = if conversation.is_some_and(|id| id.starts_with("openbot-compaction:")) {
            "compaction"
        } else if body["openbot_internal_auxiliary"] == true {
            "auxiliary"
        } else {
            "chat"
        };
        self.text("purpose", Some(purpose), 16);
        let chat_type = body["openbotChatType"]
            .as_str()
            .filter(|kind| matches!(*kind, "group" | "dm" | "routine"));
        self.text("chatType", chat_type, 16);
    }

    pub fn route(&mut self, prepared: &crate::hop::request::Prepared) {
        if let Some(compaction) = &prepared.compaction {
            self.row
                .insert("compaction".into(), json!(compaction.measurement));
        }
        self.text("providerId", Some(&prepared.provider_id), 128);
        self.text("providerName", prepared.provider_name.as_deref(), 200);
        self.text("model", Some(&prepared.model), 200);
        self.text("modelId", Some(&prepared.model_id), 256);
        self.text("botId", prepared.bot_id.as_deref(), 128);
        self.row.insert("apiType".into(), json!(prepared.protocol));
        self.row
            .insert("reasoningEffort".into(), json!(prepared.reasoning_effort));
        if let Ok(url) = Url::parse(&prepared.url)
            && url.has_host()
        {
            let endpoint = format!("{}{}", url.origin().ascii_serialization(), url.path());
            self.text("upstreamEndpoint", Some(&endpoint), 512);
        }
        self.active_secret = Some(prepared.secret.clone());
        if self.wants_body() {
            self.capture.request = Some(
                if prepared.body.len() > self.settings.max_body_capture_bytes {
                    Bytes::new()
                } else {
                    prepared.body.clone()
                },
            );
        }
    }

    pub fn attempt(
        &mut self,
        status: StatusCode,
        started: Instant,
        failure: Option<&Value>,
        retry: Option<Duration>,
    ) {
        let mut row = json!({"status":status.as_u16(),"latencyMs":millis(started.elapsed())});
        if let Some(error) = failure
            .and_then(|value| value.pointer("/error/message"))
            .and_then(Value::as_str)
        {
            row["error"] = json!(error.chars().take(1024).collect::<String>());
        }
        if let Some(delay) = retry {
            row["retryAfterMs"] = json!(millis(delay));
        }
        self.attempts.push(row);
    }

    pub fn frame(&mut self, data: &Value, bytes: usize, visible: bool) {
        usage::observe(&mut self.row, &data["usage"]);
        if visible && !self.row.contains_key("firstTokenMs") {
            self.row
                .insert("firstTokenMs".into(), json!(millis(self.started.elapsed())));
        }
        if self.wants_body() && !self.capture.truncated {
            self.capture.chunk_bytes += bytes;
            if self.capture.chunk_bytes <= self.settings.max_body_capture_bytes {
                self.capture.chunks.push(data.clone());
            } else {
                self.capture.truncated = true;
            }
        }
    }

    pub fn buffered(&mut self, data: &Value) {
        usage::observe(&mut self.row, &data["usage"]);
        if self.wants_body() {
            let mut body = Limited::new(self.settings.max_body_capture_bytes);
            self.capture.response = Some(if serde_json::to_writer(&mut body, data).is_ok() {
                body.data.into()
            } else {
                Bytes::new()
            });
        }
    }

    pub fn finish(mut self, status: StatusCode, failure: Option<Value>) {
        self.complete = true;
        self.enqueue(status, failure);
    }

    fn enqueue(&mut self, status: StatusCode, failure: Option<Value>) {
        self.row
            .insert("completedAt".into(), json!(iso(OffsetDateTime::now_utc())));
        self.row
            .insert("latencyMs".into(), json!(millis(self.started.elapsed())));
        self.row.insert("status".into(), json!(status.as_u16()));
        self.row
            .insert("ok".into(), json!(status.is_success() && failure.is_none()));
        if let Some(error) = failure
            .as_ref()
            .and_then(|value| value.pointer("/error/message"))
            .and_then(Value::as_str)
        {
            self.text("error", Some(error), 1024);
        }
        if let Some(status) = failure
            .as_ref()
            .and_then(|value| value.pointer("/error/upstream_status"))
            .and_then(Value::as_u64)
        {
            self.row.insert("upstreamStatus".into(), json!(status));
        }
        if !self.attempts.is_empty() {
            self.row
                .insert("attemptCount".into(), json!(self.attempts.len()));
            self.row
                .insert("attempts".into(), json!(std::mem::take(&mut self.attempts)));
        }
        if !self.row.contains_key("totalTokens")
            && let (Some(input), Some(output)) = (
                self.row.get("promptTokens").and_then(Value::as_u64),
                self.row.get("completionTokens").and_then(Value::as_u64),
            )
        {
            self.row
                .insert("totalTokens".into(), json!(input.saturating_add(output)));
        }
        let row = std::mem::take(&mut self.row);
        let capture = std::mem::take(&mut self.capture);
        let active_secret = self.active_secret.take();
        let settings = self.settings;
        self.logs
            .submit(move |root| write(root, row, capture, active_secret, settings, failure));
    }

    fn wants_body(&self) -> bool {
        self.settings.log_bodies || self.settings.log_bodies_on_error
    }

    fn text(&mut self, field: &str, text: Option<&str>, max: usize) {
        if let Some(text) = text.map(str::trim).filter(|text| !text.is_empty()) {
            self.row.insert(
                field.into(),
                json!(text.chars().take(max).collect::<String>()),
            );
        }
    }
}

impl Drop for Trace {
    fn drop(&mut self) {
        if !self.complete {
            self.enqueue(StatusCode::from_u16(499).expect("valid cancellation code"),
                Some(json!({"error":{"message":"Client disconnected before the request finished.","code":"client_cancelled"}})));
        }
    }
}

fn millis(value: Duration) -> u64 {
    u64::try_from(value.as_millis()).unwrap_or(u64::MAX)
}

struct Limited {
    max: usize,
    data: Vec<u8>,
}

impl Limited {
    fn new(max: usize) -> Self {
        Self {
            max,
            data: Vec::new(),
        }
    }
}

impl Write for Limited {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.len() > self.max.saturating_sub(self.data.len()) {
            return Err(io::ErrorKind::FileTooLarge.into());
        }
        self.data.extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn write(
    root: &Path,
    row: Map<String, Value>,
    capture: Capture,
    active: Option<String>,
    start: Settings,
    failure: Option<Value>,
) -> io::Result<()> {
    let current = Settings::load(root);
    if !current.logging_enabled {
        return Ok(());
    }
    let redactor = redact::Redactor::load(root, active);
    let id = row["id"]
        .as_str()
        .ok_or(io::ErrorKind::InvalidData)?
        .to_owned();
    let mut row = Value::Object(row);
    // Preserve correlation IDs and enum values while scrubbing all free text.
    for field in [
        "error",
        "attempts",
        "userAgent",
        "conversationId",
        "requestId",
        "model",
        "modelId",
        "providerId",
        "providerName",
        "botId",
        "upstreamEndpoint",
        "compaction",
    ] {
        if let Some(value) = row.get_mut(field) {
            redactor.value(value);
        }
    }
    let failed = row["ok"] != true;
    let mut bodies = Map::new();
    if (start.log_bodies || (start.log_bodies_on_error && failed))
        && (current.log_bodies || (current.log_bodies_on_error && failed))
    {
        let cap = start
            .max_body_capture_bytes
            .min(current.max_body_capture_bytes);
        if let Some(request) = capture.request {
            let (body, truncated) = bounded(&redactor, serde_json::from_slice(&request).ok(), cap);
            bodies.insert("request".into(), body);
            if truncated {
                row["requestTruncated"] = json!(true);
            }
        }
        let had_buffered_response = capture.response.is_some();
        let response = if !capture.chunks.is_empty() || capture.truncated {
            Some(
                json!({"stream":true,"events":capture.chunks,"error":failure.as_ref().and_then(|value| value.get("error")),"truncated":capture.truncated}),
            )
        } else if let Some(response) = capture.response {
            serde_json::from_slice(&response).ok()
        } else {
            failure.clone()
        };
        if response.is_some() || had_buffered_response {
            let (body, truncated) = bounded(&redactor, response, cap);
            bodies.insert("response".into(), body);
            if truncated || capture.truncated {
                row["responseTruncated"] = json!(true);
            }
        }
    }
    let _lock = AppendLock::acquire(root, &id)?;
    let mut body_path = None;
    row["hasRequest"] = json!(false);
    row["hasResponse"] = json!(false);
    if !bodies.is_empty() {
        match write_body(root, &id, &bodies) {
            Ok((path, bytes)) => {
                row["hasRequest"] = json!(bodies.contains_key("request"));
                row["hasResponse"] = json!(bodies.contains_key("response"));
                row["bodyBytes"] = json!(bytes);
                body_path = Some(path);
            }
            Err(error) => {
                row["bodyWriteFailed"] = json!(true);
                eprintln!(
                    "OpenBot could not capture a request body ({:?}).",
                    error.kind()
                );
            }
        }
    }
    if let Err(error) = append(&root.join(REQUESTS), &row) {
        if let Some(path) = body_path {
            let _ = fs::remove_file(path);
        }
        return Err(error);
    }
    Ok(())
}

fn bounded(redactor: &redact::Redactor, body: Option<Value>, max: usize) -> (Value, bool) {
    if let Some(mut body) = body {
        redactor.value(&mut body);
        if serde_json::to_writer(Limited::new(max), &body).is_ok() {
            return (body, false);
        }
    }
    (
        json!({"truncated":true,"message":"The body exceeds the capture limit."}),
        true,
    )
}

fn write_body(root: &Path, id: &str, body: &Map<String, Value>) -> io::Result<(PathBuf, usize)> {
    let dir = root.join(BODIES);
    match fs::create_dir(&dir) {
        Ok(()) => {}
        Err(error)
            if error.kind() == io::ErrorKind::AlreadyExists
                && fs::symlink_metadata(&dir)?.is_dir() => {}
        Err(error) => return Err(error),
    }
    let path = dir.join(format!("{id}.json"));
    let bytes = serde_json::to_vec(body)?;
    let mut temp = tempfile::NamedTempFile::new_in(&dir)?;
    temp.write_all(&bytes)?;
    temp.persist_noclobber(&path).map_err(|error| error.error)?;
    Ok((path, bytes.len()))
}

pub(super) fn append(path: &Path, row: &Value) -> io::Result<()> {
    ensure_regular(path)?;
    let mut file = options().read(true).append(true).create(true).open(path)?;
    let mut line = Vec::new();
    if file.metadata()?.len() > 0 {
        file.seek(SeekFrom::End(-1))?;
        let mut last = [0];
        file.read_exact(&mut last)?;
        if last[0] != b'\n' {
            line.push(b'\n');
        }
    }
    serde_json::to_writer(&mut line, row)?;
    line.push(b'\n');
    file.write_all(&line)
}

pub(super) fn ensure_regular(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(info) if info.is_file() => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Ok(_) => Err(io::ErrorKind::InvalidInput.into()),
        Err(error) => Err(error),
    }
}

fn options() -> OpenOptions {
    let mut options = OpenOptions::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
}

pub(super) struct AppendLock {
    path: PathBuf,
    owner: String,
}

impl AppendLock {
    pub(super) fn acquire(root: &Path, id: &str) -> io::Result<Self> {
        let path = root.join("openbot-requests.lock");
        let owner = format!("{}\n{id}\n", std::process::id());
        let started = Instant::now();
        loop {
            match options().write(true).create_new(true).open(&path) {
                Ok(mut file) => {
                    if let Err(error) = file.write_all(owner.as_bytes()) {
                        let _ = fs::remove_file(&path);
                        return Err(error);
                    }
                    return Ok(Self { path, owner });
                }
                Err(error)
                    if error.kind() == io::ErrorKind::AlreadyExists
                        && started.elapsed() < Duration::from_secs(2) =>
                {
                    thread::sleep(Duration::from_millis(20));
                }
                Err(error) => return Err(error),
            }
        }
    }
}

impl Drop for AppendLock {
    fn drop(&mut self) {
        if fs::symlink_metadata(&self.path)
            .is_ok_and(|metadata| metadata.is_file() && metadata.len() == self.owner.len() as u64)
            && fs::read(&self.path).is_ok_and(|value| value == self.owner.as_bytes())
        {
            let _ = fs::remove_file(&self.path);
        }
    }
}
