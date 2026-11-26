use std::{collections::BTreeSet, io, path::Path};

use axum::{
    Json,
    extract::{State as AxumState, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};
use time::OffsetDateTime;

use super::{State, error, read_json};

const GLOBAL: &str = "openbot-pause.json";
const BOTS: &str = "openbot-pause-bots.json";
const GLOBAL_LIMIT: u64 = 4096;
const BOTS_LIMIT: u64 = 1024 * 1024;

fn global_state(root: &Path) -> Value {
    read_json(&root.join(GLOBAL), GLOBAL_LIMIT)
        .ok()
        .flatten()
        .unwrap_or_default()
}

pub fn paused(root: &Path) -> bool {
    global_state(root)["paused"] == true
}

pub fn bot_ids(root: &Path) -> BTreeSet<String> {
    read_bots(root).unwrap_or_default()
}

fn read_bots(root: &Path) -> io::Result<BTreeSet<String>> {
    let Some(value) = read_json(&root.join(BOTS), BOTS_LIMIT)? else {
        return Ok(BTreeSet::new());
    };
    let ids = value["pausedBotIds"]
        .as_array()
        .ok_or(io::ErrorKind::InvalidData)?;
    Ok(ids
        .iter()
        .filter_map(Value::as_str)
        .filter(|id| !id.trim().is_empty())
        .map(str::to_owned)
        .collect())
}

pub async fn global(AxumState(state): AxumState<State>) -> Response {
    on_disk(move || {
        let current = global_state(state.root());
        let field = |name: &str| current[name].as_str().filter(|value| !value.is_empty());
        Ok(json!({"paused":current["paused"] == true, "note":field("note"), "at":field("at")}))
    })
    .await
}

pub async fn bots(AxumState(state): AxumState<State>) -> Response {
    on_disk(move || Ok(json!({"pausedBotIds":bot_ids(state.root())}))).await
}

pub async fn save_global(
    AxumState(state): AxumState<State>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    if !state.0.writable {
        return readonly();
    }
    let Ok(Json(body)) = body else {
        return error(StatusCode::BAD_REQUEST, "Provide a pause settings object.");
    };
    let Some(paused) = body["paused"].as_bool() else {
        return error(StatusCode::BAD_REQUEST, "paused must be a boolean.");
    };
    if body.get("note").is_some_and(|value| !value.is_string()) {
        return error(StatusCode::BAD_REQUEST, "note must be a string.");
    }
    let note = body["note"]
        .as_str()
        .map(str::trim)
        .filter(|note| !note.is_empty())
        .map(|note| note.chars().take(500).collect::<String>());
    on_disk(move || {
        let value =
            json!({"paused":paused, "at":crate::logs::iso(OffsetDateTime::now_utc()),"note":note});
        {
            let _guard = state.lock_bot_settings()?;
            state
                .0
                .write_json(&state.root().join(GLOBAL), &value, GLOBAL_LIMIT)?;
        }
        crate::logs::append_pause_event(state.root(), paused);
        Ok(value)
    })
    .await
}

enum Update {
    Replace(BTreeSet<String>),
    Bot(String, bool),
}

pub async fn save_bots(
    AxumState(state): AxumState<State>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    if !state.0.writable {
        return readonly();
    }
    let Ok(Json(body)) = body else {
        return error(StatusCode::BAD_REQUEST, "Provide bot pause settings.");
    };
    let update = if let Some(ids) = body.get("pausedBotIds") {
        let Some(ids) = ids.as_array().and_then(|items| {
            items
                .iter()
                .map(|id| valid_id(id).map(str::to_owned))
                .collect::<Option<BTreeSet<_>>>()
        }) else {
            return error(
                StatusCode::BAD_REQUEST,
                "pausedBotIds must be an array of bot IDs.",
            );
        };
        Update::Replace(ids)
    } else if let (Some(id), Some(paused)) = (valid_id(&body["botId"]), body["paused"].as_bool()) {
        Update::Bot(id.into(), paused)
    } else {
        return error(StatusCode::BAD_REQUEST, "botId and paused are required.");
    };
    on_disk(move || {
        let _guard = state.lock_bot_settings()?;
        let ids = match update {
            Update::Replace(ids) => ids,
            Update::Bot(id, paused) => {
                let mut ids = read_bots(state.root())?;
                if paused {
                    ids.insert(id);
                } else {
                    ids.remove(&id);
                }
                ids
            }
        };
        let result = json!({"pausedBotIds":ids});
        state
            .0
            .write_json(&state.root().join(BOTS), &result, BOTS_LIMIT)?;
        Ok(result)
    })
    .await
}

fn valid_id(value: &Value) -> Option<&str> {
    value
        .as_str()
        .map(str::trim)
        .filter(|id| !id.is_empty() && id.len() <= 128 && !id.chars().any(char::is_control))
}

async fn on_disk(job: impl FnOnce() -> io::Result<Value> + Send + 'static) -> Response {
    match tokio::task::spawn_blocking(job).await {
        Ok(Ok(value)) => Json(value).into_response(),
        _ => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Could not read or update the pause settings.",
        ),
    }
}

fn readonly() -> Response {
    error(
        StatusCode::METHOD_NOT_ALLOWED,
        "The comparison listener is read-only.",
    )
}
