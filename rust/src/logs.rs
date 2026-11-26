mod maintenance;
mod read;
mod record;
mod report;
mod usage;

use std::{
    io,
    path::{Path, PathBuf},
    sync::Arc,
};

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Path as AxumPath, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Serialize;
use serde_json::{Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use tokio::sync::Semaphore;

use crate::server::read_json;

pub use record::Trace;

const REQUESTS: &str = "openbot-requests.jsonl";
const EVENTS: &str = "openbot-events.jsonl";
const BODIES: &str = "openbot-request-bodies";
const SETTINGS: &str = "openbot-logs.json";
const PENDING_WRITES: u32 = 32;

#[derive(Clone)]
pub struct Logs {
    root: Arc<PathBuf>,
    writes: Option<Arc<Semaphore>>,
}

#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    logging_enabled: bool,
    log_bodies: bool,
    log_bodies_on_error: bool,
    log_retention_days: u64,
    max_body_capture_bytes: usize,
    max_records: u64,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            logging_enabled: false,
            log_bodies: false,
            log_bodies_on_error: true,
            log_retention_days: 7,
            max_body_capture_bytes: 65_536,
            max_records: 2000,
        }
    }
}

impl Settings {
    pub fn load(root: &Path) -> Self {
        let raw = read_json(&root.join(SETTINGS), 4096)
            .ok()
            .flatten()
            .unwrap_or_default();
        Self::from_json(&raw).unwrap_or_default()
    }

    fn from_json(raw: &Value) -> Option<Self> {
        let base = Self::default();
        let integer = |key: &str, bounds: std::ops::RangeInclusive<u64>, fallback| {
            let field = &raw[key];
            if field.is_null() {
                return Some(fallback);
            }
            field
                .as_u64()
                .or_else(|| field.as_str()?.trim().parse().ok())
                .filter(|number| bounds.contains(number))
        };
        Some(Self {
            logging_enabled: raw["loggingEnabled"]
                .as_bool()
                .unwrap_or(base.logging_enabled),
            log_bodies: raw["logBodies"].as_bool().unwrap_or(base.log_bodies),
            log_bodies_on_error: raw["logBodiesOnError"]
                .as_bool()
                .unwrap_or(base.log_bodies_on_error),
            log_retention_days: integer("logRetentionDays", 1..=365, base.log_retention_days)?,
            max_body_capture_bytes: integer(
                "maxBodyCaptureBytes",
                1024..=1_048_576,
                base.max_body_capture_bytes as u64,
            )? as usize,
            max_records: integer("maxRecords", 1..=10_000, base.max_records)?,
        })
    }
}

impl Logs {
    pub fn new(root: &Path, writable: bool) -> Self {
        Self {
            root: Arc::new(root.to_path_buf()),
            writes: writable.then(|| Arc::new(Semaphore::new(PENDING_WRITES as usize))),
        }
    }

    pub fn routes(self) -> Router {
        Router::new()
            .route("/api/logs", get(list))
            .route("/api/logs/{id}", get(detail))
            .route("/api/logs/settings", get(settings).put(maintenance::update))
            .route("/api/logs/cleanup", post(maintenance::cleanup))
            .route("/api/logs/strip-bodies", post(maintenance::strip))
            .route("/api/logs/clear", post(maintenance::clear))
            .route("/api/logs/events", get(events))
            .route("/api/logs/facets", get(facets))
            .route("/api/logs/stats", get(stats))
            .route("/api/logs/usage", get(usage))
            .layer(DefaultBodyLimit::max(4096))
            .with_state(self)
    }

    pub async fn wait_for_writes(&self) {
        if let Some(writes) = &self.writes {
            let _drained = writes.acquire_many(PENDING_WRITES).await;
        }
    }
}

pub(crate) fn historical_bot_ids(root: &Path) -> Vec<String> {
    read::Rows::load(root, REQUESTS).map_or_else(
        |_| Vec::new(),
        |rows| {
            rows.values
                .into_iter()
                .rev()
                .take(report::FACETS_SAMPLE)
                .filter_map(|row| row["botId"].as_str().map(str::to_owned))
                .collect()
        },
    )
}

fn iso(time: OffsetDateTime) -> String {
    // Preserve the canonical millisecond UTC format used by the Node logger.
    let formatted = time.format(&Rfc3339).unwrap_or_default();
    if let Some(without_zone) = formatted.strip_suffix('Z') {
        let (seconds, fraction) = without_zone.split_once('.').unwrap_or((without_zone, ""));
        let ms = fraction.get(..3).unwrap_or(fraction);
        return format!("{seconds}.{ms:0<3}Z");
    }
    formatted
}

fn at(value: &str) -> Option<OffsetDateTime> {
    OffsetDateTime::parse(value, &Rfc3339).ok()
}

async fn read_api(
    logs: Logs,
    job: impl FnOnce(&Path) -> io::Result<Option<Value>> + Send + 'static,
) -> Response {
    match tokio::task::spawn_blocking(move || job(&logs.root)).await {
        Ok(Ok(Some(value))) => Json(value).into_response(),
        Ok(Ok(None)) => (
            StatusCode::NOT_FOUND,
            Json(json!({"error":"Log record not found."})),
        )
            .into_response(),
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"Could not read OpenBot logs."})),
        )
            .into_response(),
    }
}

async fn settings(State(logs): State<Logs>) -> Response {
    read_api(logs, |root| Ok(Some(json!(Settings::load(root))))).await
}

async fn list(State(logs): State<Logs>, Query(query): Query<read::Filter>) -> Response {
    read_api(logs, move |root| {
        Ok(Some(read::Rows::load(root, REQUESTS)?.list(query)))
    })
    .await
}

async fn detail(State(logs): State<Logs>, AxumPath(id): AxumPath<String>) -> Response {
    read_api(logs, move |root| read::detail(root, &id)).await
}

async fn events(State(logs): State<Logs>, Query(query): Query<read::EventFilter>) -> Response {
    read_api(logs, move |root| {
        Ok(Some(read::Rows::load(root, EVENTS)?.events(query)))
    })
    .await
}

async fn facets(State(logs): State<Logs>) -> Response {
    read_api(logs, |root| {
        Ok(Some(report::facets(&read::Rows::load(root, REQUESTS)?)))
    })
    .await
}

async fn stats(State(logs): State<Logs>) -> Response {
    read_api(logs, |root| {
        Ok(Some(report::stats(
            root,
            &read::Rows::load(root, REQUESTS)?,
        )))
    })
    .await
}

async fn usage(State(logs): State<Logs>, Query(query): Query<report::UsageFilter>) -> Response {
    read_api(logs, move |root| {
        Ok(Some(report::usage(
            &read::Rows::load(root, REQUESTS)?,
            query,
        )))
    })
    .await
}

#[cfg(test)]
mod tests;
