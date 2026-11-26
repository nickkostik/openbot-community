use std::{
    collections::HashSet,
    fs::{self, Metadata},
    io::{self, BufRead, BufReader, BufWriter, Read, Write},
    path::Path,
    time::{Duration, SystemTime},
};

use axum::{
    Json,
    extract::{State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
use tempfile::NamedTempFile;
use time::OffsetDateTime;
use tokio::task::JoinHandle;

use crate::{
    auth::unique_id,
    server::{read_json, read_mode},
};

use super::{
    BODIES, EVENTS, Logs, REQUESTS, SETTINGS, Settings, at, iso,
    read::{MAX_ROW_BYTES, regular_file, safe_id},
    record::{AppendLock, append, ensure_regular},
};

enum Operation {
    Cleanup,
    Strip,
    Clear,
}

#[derive(Default)]
struct Report {
    removed: u64,
    kept: u64,
    stripped: u64,
}

type TaskResult = Result<Value, (StatusCode, &'static str)>;

impl Logs {
    pub fn schedule_cleanup(&self) -> Option<JoinHandle<()>> {
        self.writes.as_ref()?;
        let logs = self.clone();
        Some(tokio::spawn(async move {
            let first = tokio::time::Instant::now() + Duration::from_secs(60);
            let mut timer = tokio::time::interval_at(first, Duration::from_secs(30 * 60));
            timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                timer.tick().await;
                if let Err((status, _)) = logs.run(archive_job(Operation::Cleanup)).await {
                    eprintln!("Scheduled OpenBot log cleanup stopped ({status}).");
                }
            }
        }))
    }

    async fn run(&self, job: impl FnOnce(&Path) -> TaskResult + Send + 'static) -> TaskResult {
        let Some(queue) = self.writes.clone() else {
            return Err((
                StatusCode::METHOD_NOT_ALLOWED,
                "The log archive is read-only.",
            ));
        };
        let permit = queue
            .try_acquire_owned()
            .map_err(|_| (StatusCode::SERVICE_UNAVAILABLE, "The log archive is busy."))?;
        let root = self.root.clone();
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            job(&root)
        })
        .await
        .map_err(|_| failed())?
    }
}

pub async fn update(
    State(logs): State<Logs>,
    input: Result<Json<Value>, JsonRejection>,
) -> Response {
    let Ok(Json(input)) = input else {
        return reply(Err((StatusCode::BAD_REQUEST, "Invalid log settings.")));
    };
    if !input.is_object() {
        return reply(Err((
            StatusCode::BAD_REQUEST,
            "Log settings must be an object.",
        )));
    }
    reply(logs.run(move |root| save_settings(root, input)).await)
}

pub async fn cleanup(State(logs): State<Logs>) -> Response {
    reply(logs.run(archive_job(Operation::Cleanup)).await)
}

pub async fn strip(State(logs): State<Logs>) -> Response {
    reply(logs.run(archive_job(Operation::Strip)).await)
}

pub async fn clear(State(logs): State<Logs>) -> Response {
    reply(logs.run(archive_job(Operation::Clear)).await)
}

fn archive_job(operation: Operation) -> impl FnOnce(&Path) -> TaskResult {
    move |root| {
        let _lock = lock(root)?;
        let retention = if matches!(operation, Operation::Cleanup) {
            saved_settings(root)?.log_retention_days
        } else {
            Settings::default().log_retention_days
        };
        let report = rewrite(root, &operation, retention).map_err(write_error)?;
        let (event, severity, message, response) = match operation {
            Operation::Cleanup => (
                "logs.cleanup",
                "INFO",
                format!(
                    "Removed {} expired requests and kept {}.",
                    report.removed, report.kept
                ),
                json!({"ok":true,"removedByRetention":report.removed,"removedByCap":0,"kept":report.kept}),
            ),
            Operation::Strip => (
                "logs.strip-bodies",
                "WARN",
                format!("Stripped {} captured bodies.", report.stripped),
                json!({"ok":true,"stripped":report.stripped}),
            ),
            Operation::Clear => (
                "logs.clear",
                "WARN",
                "Cleared all request records and captured bodies.".into(),
                json!({"ok":true}),
            ),
        };
        log_event(root, event, severity, &message, &response);
        Ok(response)
    }
}

fn save_settings(root: &Path, input: Value) -> TaskResult {
    let _lock = lock(root)?;
    let path = root.join(SETTINGS);
    let previous = saved_settings(root)?;
    let mut raw = json!(previous);
    for field in ["loggingEnabled", "logBodies", "logBodiesOnError"] {
        if input[field].is_boolean() {
            raw[field] = input[field].clone();
        }
    }
    for field in ["logRetentionDays", "maxBodyCaptureBytes"] {
        if let Some(value) = input.get(field).filter(|value| !value.is_null()) {
            raw[field] = value.clone();
        }
    }
    let updated = Settings::from_json(&raw).ok_or((
        StatusCode::BAD_REQUEST,
        "Log retention or capture limit is out of range.",
    ))?;
    let toggled = updated.logging_enabled != previous.logging_enabled;
    if toggled && read_mode(root).map_err(write_error)? != "custom" {
        return Err((
            StatusCode::CONFLICT,
            "Change stock recording through the Official host controls.",
        ));
    }
    let mut response = json!(updated);
    let mut disk = response.clone();
    disk.as_object_mut()
        .expect("settings serialize as an object")
        .shift_remove("maxRecords");
    let mut temp = NamedTempFile::new_in(root).map_err(write_error)?;
    serde_json::to_writer_pretty(&mut temp, &disk).map_err(|_| failed())?;
    temp.write_all(b"\n").map_err(write_error)?;
    save(temp, &path).map_err(write_error)?;
    if updated.log_retention_days != previous.log_retention_days
        && let Err(error) = rewrite(root, &Operation::Cleanup, updated.log_retention_days)
    {
        eprintln!(
            "OpenBot could not apply log retention immediately ({:?}).",
            error.kind()
        );
    }
    if toggled {
        let message = if updated.logging_enabled {
            "Request recording enabled."
        } else {
            "Request recording disabled."
        };
        log_event(root, "logs.settings", "INFO", message, &Value::Null);
    }
    response["wrapBytesChanged"] = json!(false);
    Ok(response)
}

fn saved_settings(root: &Path) -> Result<Settings, (StatusCode, &'static str)> {
    let path = root.join(SETTINGS);
    ensure_regular(&path).map_err(write_error)?;
    let broken = (StatusCode::CONFLICT, "The saved log settings are invalid.");
    match read_json(&path, 4096).map_err(|error| {
        if error.kind() == io::ErrorKind::InvalidData {
            broken
        } else {
            write_error(error)
        }
    })? {
        Some(Value::Object(mut fields)) => {
            fields.shift_remove("maxRecords");
            Settings::from_json(&Value::Object(fields)).ok_or(broken)
        }
        Some(_) => Err(broken),
        None => Ok(Settings::default()),
    }
}

fn rewrite(root: &Path, operation: &Operation, days: u64) -> io::Result<Report> {
    let mut report = Report::default();
    let path = root.join(REQUESTS);
    let start = SystemTime::now();
    let cutoff = OffsetDateTime::now_utc() - time::Duration::days(days as i64);
    let mut keep_bodies = HashSet::new();
    let mut expired_bodies = HashSet::new();
    let mut unrecognized = false;
    if let Some(file) = regular_file(&path)? {
        let original = file.metadata()?;
        let bytes = if matches!(operation, Operation::Clear) {
            0
        } else {
            original.len()
        };
        let mut input = BufReader::new(file.take(bytes));
        let mut output = NamedTempFile::new_in(root)?;
        let mut writer = BufWriter::new(&mut output);
        let mut line = Vec::new();
        loop {
            line.clear();
            let size = (&mut input)
                .take((MAX_ROW_BYTES + 1) as u64)
                .read_until(b'\n', &mut line)?;
            if size == 0 {
                break;
            }
            if size > MAX_ROW_BYTES {
                // Preserve the archive when it includes rows outside the
                // supported maintenance bound.
                return Err(io::ErrorKind::FileTooLarge.into());
            }
            let mut row = serde_json::from_slice::<Value>(&line)
                .ok()
                .filter(Value::is_object);
            unrecognized |= row.is_none() && line.iter().any(|byte| !byte.is_ascii_whitespace());
            if matches!(operation, Operation::Cleanup)
                && row
                    .as_ref()
                    .and_then(|row| row["startedAt"].as_str())
                    .and_then(at)
                    .is_some_and(|time| time < cutoff)
            {
                report.removed += 1;
                if let Some(id) = row
                    .as_ref()
                    .and_then(|row| row["id"].as_str())
                    .filter(|id| safe_id(id))
                {
                    expired_bodies.insert(id.to_owned());
                }
                continue;
            }
            if let Some(row) = &mut row {
                report.kept += 1;
                if let Some(id) = row["id"].as_str().filter(|id| safe_id(id)) {
                    keep_bodies.insert(id.to_owned());
                }
                if matches!(operation, Operation::Strip) {
                    strip_row(row);
                    serde_json::to_writer(&mut writer, row)?;
                    writer.write_all(b"\n")?;
                    continue;
                }
            }
            writer.write_all(&line)?;
        }
        writer.flush()?;
        drop(writer);
        output.as_file().sync_data()?;
        if !same_file(&original, &path)? {
            // Older processes may append without a lock on timeout.
            return Err(io::ErrorKind::WouldBlock.into());
        }
        if !matches!(operation, Operation::Cleanup) || report.removed > 0 {
            output.persist(&path).map_err(|error| error.error)?;
        }
    }
    report.stripped = remove_bodies(root, start, |id| {
        !matches!(operation, Operation::Cleanup)
            || (!keep_bodies.contains(id) && (!unrecognized || expired_bodies.contains(id)))
    })?;
    Ok(report)
}

fn strip_row(row: &mut Value) {
    row["hasRequest"] = json!(false);
    row["hasResponse"] = json!(false);
    let fields = row.as_object_mut().expect("only JSON objects are rows");
    for field in [
        "bodyBytes",
        "requestTruncated",
        "responseTruncated",
        "bodyWriteFailed",
    ] {
        fields.shift_remove(field);
    }
}

fn same_file(original: &Metadata, path: &Path) -> io::Result<bool> {
    let current = fs::symlink_metadata(path)?;
    if !current.is_file()
        || original.len() != current.len()
        || original.modified()? != current.modified()?
    {
        return Ok(false);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(original.dev() == current.dev() && original.ino() == current.ino())
    }
    #[cfg(not(unix))]
    Ok(true)
}

fn remove_bodies(
    root: &Path,
    start: SystemTime,
    should_remove: impl Fn(&str) -> bool,
) -> io::Result<u64> {
    let dir = root.join(BODIES);
    match fs::symlink_metadata(&dir) {
        Ok(meta) if meta.is_dir() => {}
        Ok(_) => return Err(io::ErrorKind::InvalidInput.into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(error),
    }
    let mut removed = 0;
    for entry in fs::read_dir(&dir)? {
        let entry = entry?;
        let path = entry.path();
        let Some(id) = path
            .file_stem()
            .and_then(|id| id.to_str())
            .filter(|id| safe_id(id))
        else {
            continue;
        };
        if path.extension().is_none_or(|extension| extension != "json") || !should_remove(id) {
            continue;
        }
        let meta = match fs::symlink_metadata(&path) {
            Ok(meta) if meta.is_file() => meta,
            Ok(_) => continue,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        // A legacy writer may have prepared a new sidecar while waiting for
        // this lock. It is outside the request snapshot.
        if meta.modified()? > start {
            continue;
        }
        match fs::remove_file(&path) {
            Ok(()) => removed += 1,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    Ok(removed)
}

fn save(file: NamedTempFile, path: &Path) -> io::Result<()> {
    file.as_file().sync_data()?;
    file.persist(path).map_err(|error| error.error)?;
    Ok(())
}

fn lock(root: &Path) -> Result<AppendLock, (StatusCode, &'static str)> {
    let id = unique_id().map_err(|_| failed())?;
    AppendLock::acquire(root, &id).map_err(write_error)
}

fn log_event(root: &Path, kind: &str, severity: &str, message: &str, metadata: &Value) {
    if let Ok(id) = unique_id() {
        let mut event = json!({"id":id,"at":iso(OffsetDateTime::now_utc()),"type":kind,"severity":severity,"message":message});
        if !metadata.is_null() {
            event["metadata"] = metadata.clone();
        }
        if let Err(error) = append(&root.join(EVENTS), &event) {
            eprintln!(
                "OpenBot could not write a log maintenance event ({:?}).",
                error.kind()
            );
        }
    }
}

fn failed() -> (StatusCode, &'static str) {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        "Could not update the log archive.",
    )
}

fn write_error(error: io::Error) -> (StatusCode, &'static str) {
    match error.kind() {
        io::ErrorKind::AlreadyExists | io::ErrorKind::WouldBlock => (
            StatusCode::CONFLICT,
            "The log archive changed. Retry the operation.",
        ),
        _ => failed(),
    }
}

fn reply(result: TaskResult) -> Response {
    match result {
        Ok(value) => Json(value).into_response(),
        Err((status, message)) => (status, Json(json!({"error":message}))).into_response(),
    }
}
