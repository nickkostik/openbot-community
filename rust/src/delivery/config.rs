use std::{
    fs, io,
    path::{Path, PathBuf},
    time::Duration,
};

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, State as AxumState, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::{Map, Value, json};

use crate::server::{State, read_json};

const FILE: &str = "openbot-injection.json";
const MAX_BYTES: u64 = 64 * 1024;
const LAYERS: [&str; 3] = ["l1", "l2", "l3"];
const ENV_LAYERS: [&str; 3] = [
    "OPENBOT_INJECTION_L1_ENABLED",
    "OPENBOT_INJECTION_L2_ENABLED",
    "OPENBOT_INJECTION_L3_ENABLED",
];

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(super) enum Mode {
    #[default]
    Off,
    DryRun,
    Enforce,
}

impl Mode {
    fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "off" => Self::Off,
            "dry-run" => Self::DryRun,
            "enforce" => Self::Enforce,
            _ => return None,
        })
    }

    pub(super) fn name(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::DryRun => "dry-run",
            Self::Enforce => "enforce",
        }
    }
}

#[derive(Clone, Copy)]
pub(super) struct Ack {
    pub enabled: bool,
    pub start: usize,
    pub silence: usize,
    pub result: usize,
}

#[derive(Clone, Copy)]
pub(super) struct Followup {
    pub enabled: bool,
    pub max_runs: u8,
    pub decision: Duration,
    pub timeout: Duration,
    pub prompt_cap: usize,
    pub completion_cap: u64,
    pub cost_cap: f64,
}

#[derive(Clone, Copy)]
pub(super) struct Redrive {
    pub enabled: bool,
    pub max_runs: u8,
    pub ttl: Duration,
}

#[derive(Clone, Copy)]
pub(crate) struct Config {
    pub(super) mode: Mode,
    pub(super) l1: Ack,
    pub(super) l2: Followup,
    pub(super) l3: Redrive,
}

#[derive(Clone)]
pub(crate) struct ConfigFile {
    path: PathBuf,
    mode: Option<Mode>,
    layers: [Option<bool>; 3],
}

impl ConfigFile {
    pub(crate) fn new(root: &Path) -> Self {
        Self {
            // Use the explicitly selected Rust state directory by default.
            path: std::env::var_os("OPENBOT_INJECTION")
                .filter(|path| !path.is_empty())
                .map(PathBuf::from)
                .unwrap_or_else(|| root.join(FILE)),
            mode: std::env::var("OPENBOT_INJECTION_MODE")
                .ok()
                .and_then(|mode| Mode::parse(&mode)),
            layers: ENV_LAYERS
                .map(|name| std::env::var(name).ok().and_then(|value| env_bool(&value))),
        }
    }

    fn raw(&self) -> io::Result<Option<Map<String, Value>>> {
        match fs::metadata(&self.path) {
            Ok(info) if info.is_file() => {}
            Ok(_) => return Err(io::ErrorKind::InvalidInput.into()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(error),
        }
        match read_json(&self.path, MAX_BYTES) {
            Ok(Some(Value::Object(value))) => Ok(Some(value)),
            Ok(_) => Ok(None),
            Err(error) if error.kind() == io::ErrorKind::InvalidData => Ok(None),
            Err(error) => Err(error),
        }
    }

    pub(crate) fn load(&self) -> Config {
        self.effective(self.raw().ok().flatten().as_ref())
    }

    fn effective(&self, raw: Option<&Map<String, Value>>) -> Config {
        let layer = LAYERS.map(|name| raw.and_then(|raw| block(raw, name)));
        let enabled = std::array::from_fn::<_, 3, _>(|i| {
            self.layers[i].unwrap_or_else(|| {
                layer[i].is_some_and(|record| {
                    record
                        .get("enabled")
                        .and_then(Value::as_bool)
                        .unwrap_or(true)
                })
            })
        });
        let [l1, l2, l3] = layer;
        let timeout = integer(l2, "timeoutMs", 1000, 600000, 120000);
        let budget = if l2.is_some_and(|block| block.contains_key("retryBudgetMs")) {
            integer(l2, "retryBudgetMs", 1000, 600000, 120000)
        } else {
            timeout
        };
        Config {
            mode: self
                .mode
                .or_else(|| Mode::parse(raw?.get("mode")?.as_str()?))
                .unwrap_or_default(),
            l1: Ack {
                enabled: enabled[0],
                start: integer(l1, "startOfTurnAckThreshold", 1, 1_000_000, 1) as usize,
                silence: integer(l1, "watchingSilenceThreshold", 1, 1_000_000, 6) as usize,
                result: integer(l1, "earlyResultThreshold", 0, 1_000_000, 0) as usize,
            },
            l2: Followup {
                enabled: enabled[1],
                max_runs: integer(l2, "maxAdditionalRuns", 0, 1, 1) as u8,
                decision: Duration::from_millis(integer(
                    l2,
                    "terminalDecisionTimeoutMs",
                    1,
                    2000,
                    250,
                )),
                timeout: Duration::from_millis(timeout.min(budget)),
                prompt_cap: integer(l2, "maxAdditionalPromptTokens", 8192, 1_048_576, 131_072)
                    as usize,
                completion_cap: integer(l2, "maxAdditionalCompletionTokens", 128, 16_384, 2048),
                cost_cap: l2
                    .and_then(|l2| numeric(l2.get("maxAdditionalCostUsd")?))
                    .filter(|n| (0.0..=10.0).contains(n))
                    .unwrap_or(0.10),
            },
            l3: Redrive {
                enabled: enabled[2],
                max_runs: integer(l3, "maxRedrivesPerEpoch", 0, 3, 1) as u8,
                ttl: Duration::from_millis(integer(l3, "ttlMs", 60_000, 900_000, 300_000)),
            },
        }
    }

    fn report(&self) -> Value {
        let raw = self.raw().ok().flatten();
        let current = self.effective(raw.as_ref());
        let source = if self.mode.is_some() {
            "env"
        } else if raw.is_some() {
            "file"
        } else {
            "default"
        };
        json!({
            "mode": current.mode.name(), "source": source, "path": self.path,
            "exists": self.path.exists(), "envOverride": self.mode.is_some(),
            "layers": {"l1": current.l1.enabled, "l2": current.l2.enabled, "l3": current.l3.enabled},
            "maxAdditionalRuns": current.l2.max_runs,
        })
    }

    fn save(&self, update: Update) -> io::Result<Value> {
        let mut raw = self.raw()?.unwrap_or_default();
        raw.insert("mode".into(), json!(update.mode.name()));
        if let Some(changes) = update.layers {
            let nested = !LAYERS
                .iter()
                .any(|name| raw.get(*name).is_some_and(Value::is_object));
            for (name, enabled) in LAYERS.into_iter().zip(changes) {
                let Some(enabled) = enabled else { continue };
                let mut next = block(&raw, name).cloned().unwrap_or_default();
                next.insert("enabled".into(), json!(enabled));
                if nested {
                    if !raw.get("layers").is_some_and(Value::is_object) {
                        raw.insert("layers".into(), json!({}));
                    }
                    raw["layers"][name] = Value::Object(next);
                } else {
                    raw.insert(name.into(), Value::Object(next));
                }
            }
        }
        let mut bytes = serde_json::to_vec_pretty(&raw)?;
        bytes.push(b'\n');
        if bytes.len() as u64 > MAX_BYTES {
            return Err(io::ErrorKind::FileTooLarge.into());
        }
        let parent = self
            .path
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        fs::create_dir_all(parent)?;
        crate::service::replace(
            &parent.join(self.path.file_name().ok_or(io::ErrorKind::InvalidInput)?),
            &bytes,
        )?;
        let mut result = self.report();
        result["ok"] = json!(true);
        Ok(result)
    }
}

fn block<'a>(raw: &'a Map<String, Value>, name: &str) -> Option<&'a Map<String, Value>> {
    raw.get(name)
        .and_then(Value::as_object)
        .or_else(|| raw.get("layers")?.get(name)?.as_object())
}

fn env_bool(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "true" | "1" | "on" | "yes" => Some(true),
        "false" | "0" | "off" | "no" => Some(false),
        _ => None,
    }
}

fn numeric(value: &Value) -> Option<f64> {
    match value {
        Value::String(s) if s.trim().is_empty() => Some(0.0),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(flag) => Some(u8::from(*flag) as f64),
        Value::Null => Some(0.0),
        value => value.as_f64(),
    }
}

fn integer(raw: Option<&Map<String, Value>>, key: &str, min: u64, max: u64, fallback: u64) -> u64 {
    raw.and_then(|raw| numeric(raw.get(key)?))
        .filter(|n| n.fract() == 0.0 && (min as f64..=max as f64).contains(n))
        .map_or(fallback, |n| n as u64)
}

struct Update {
    mode: Mode,
    layers: Option<[Option<bool>; 3]>,
}

impl Update {
    fn parse(body: Value) -> Result<Self, &'static str> {
        let record = body.as_object().ok_or("body must be an object")?;
        let mode = record
            .get("mode")
            .and_then(Value::as_str)
            .and_then(Mode::parse)
            .ok_or("mode must be one of off, dry-run, enforce")?;
        let layers = match record.get("layers") {
            None | Some(Value::Null) => (mode != Mode::Off).then_some([Some(true); 3]),
            Some(Value::Object(raw)) => {
                let mut layers = [None; 3];
                for (index, name) in LAYERS.into_iter().enumerate() {
                    if let Some(value) = raw.get(name) {
                        layers[index] = Some(value.as_bool().ok_or(match name {
                            "l1" => "layers.l1 must be a boolean",
                            "l2" => "layers.l2 must be a boolean",
                            _ => "layers.l3 must be a boolean",
                        })?);
                    }
                }
                if mode == Mode::Off {
                    return Err("layers cannot be set while mode is off");
                }
                Some(layers)
            }
            _ => return Err("layers must be an object"),
        };
        Ok(Self { mode, layers })
    }
}

#[derive(Clone)]
struct Panel {
    file: ConfigFile,
    state: State,
    writable: bool,
}

pub(crate) fn routes(state: State, writable: bool) -> Router {
    let file = ConfigFile::new(state.root());
    Router::new()
        .route("/api/settings/delivery", get(status).put(save))
        .layer(DefaultBodyLimit::max(MAX_BYTES as usize))
        .with_state(Panel {
            file,
            state,
            writable,
        })
}

async fn status(AxumState(panel): AxumState<Panel>) -> Response {
    match tokio::task::spawn_blocking(move || panel.file.report()).await {
        Ok(value) => Json(value).into_response(),
        Err(_) => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Could not read delivery settings.",
        ),
    }
}

async fn save(
    AxumState(panel): AxumState<Panel>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    if !panel.writable {
        return error(StatusCode::FORBIDDEN, "Delivery settings are read-only.");
    }
    let update = match body {
        Ok(Json(body)) => match Update::parse(body) {
            Ok(update) => update,
            Err(message) => return error(StatusCode::BAD_REQUEST, message),
        },
        Err(rejection) if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE => {
            return error(
                rejection.status(),
                "Delivery settings request is too large.",
            );
        }
        Err(_) => return error(StatusCode::BAD_REQUEST, "invalid json"),
    };
    match tokio::task::spawn_blocking(move || {
        let _lock = panel.state.lock_bot_settings()?;
        panel.file.save(update)
    })
    .await
    {
        Ok(Ok(value)) => Json(value).into_response(),
        _ => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Could not save delivery settings.",
        ),
    }
}

fn error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"error": message}))).into_response()
}

#[cfg(test)]
mod tests;
