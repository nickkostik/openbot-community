pub(crate) mod pauses;

use std::{
    fs::File,
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard},
};

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Request, State as AxumState, rejection::JsonRejection},
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use serde::Serialize;
use serde_json::{Value, json};

use crate::{
    bot_models::{Settings, View},
    catalog::Catalog,
    ingress::Policy,
    lease::Lease,
    routing::{self, CredentialKind, Route},
};

pub const PLAN_MAX_BYTES: u64 = 16 * 1024 * 1024;
pub const BOT_MODELS_MAX_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Clone)]
pub struct State(Arc<Store>);

struct Store {
    root: PathBuf,
    _writer: Option<Lease>,
    writable: bool,
    writes: Mutex<()>,
}

impl State {
    pub fn new(root: PathBuf, writable: bool) -> io::Result<Self> {
        Self::with_catalog_writer(root, writable, false)
    }

    pub fn with_catalog_writer(
        root: PathBuf,
        writable: bool,
        catalog_writer: bool,
    ) -> io::Result<Self> {
        let root = root.canonicalize()?;
        if !root.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotADirectory,
                "expected an OpenBot state directory",
            ));
        }
        let writer = (writable || catalog_writer)
            .then(|| Lease::acquire(&root.join(".openbot-rust-bot-settings.lock")))
            .transpose()?;
        Ok(Self(Arc::new(Store {
            root,
            _writer: writer,
            writable,
            writes: Mutex::new(()),
        })))
    }

    pub fn resolve_routes(
        &self,
        models: &[String],
        bot_id: Option<&str>,
        credential: CredentialKind,
    ) -> io::Result<Vec<Route>> {
        let (catalog, settings) = self.saved()?;
        models
            .iter()
            .map(|model| {
                routing::resolve(&catalog, &settings, model, bot_id, credential)
                    .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error.to_string()))
            })
            .collect()
    }

    pub fn root(&self) -> &Path {
        &self.0.root
    }

    pub fn saved(&self) -> io::Result<(Catalog, Settings)> {
        Ok((self.0.catalog()?, self.0.settings(false)?))
    }

    pub fn catalog(&self) -> io::Result<Catalog> {
        self.0.catalog()
    }

    pub fn lock_bot_settings(&self) -> io::Result<MutexGuard<'_, ()>> {
        self.0
            .writes
            .lock()
            .map_err(|_| io::Error::other("Bot settings writer is unavailable"))
    }
}

impl Store {
    fn catalog(&self) -> io::Result<Catalog> {
        let plan =
            read_json(&self.root.join("openbot-plan.json"), PLAN_MAX_BYTES)?.unwrap_or_default();
        Ok(Catalog::from_plan(&plan))
    }

    fn settings_path(&self) -> PathBuf {
        self.root.join("openbot-bot-models.json")
    }

    fn settings(&self, strict: bool) -> io::Result<Settings> {
        let record = match read_json(&self.settings_path(), BOT_MODELS_MAX_BYTES) {
            Ok(Some(Value::Object(record))) => record,
            Ok(None) => Default::default(),
            Ok(_) if strict => return Err(io::ErrorKind::InvalidData.into()),
            Err(error) if strict || error.kind() != io::ErrorKind::InvalidData => {
                return Err(error);
            }
            _ => Default::default(),
        };
        Ok(Settings::from_saved(record))
    }

    fn save(&self, settings: &Settings) -> io::Result<()> {
        self.write_json(
            &self.settings_path(),
            &settings.saved()?,
            BOT_MODELS_MAX_BYTES,
        )
    }

    fn write_json(&self, path: &Path, value: &impl Serialize, maximum: u64) -> io::Result<()> {
        let mut bytes = serde_json::to_vec_pretty(value)?;
        bytes.push(b'\n');
        if bytes.len() as u64 > maximum {
            return Err(io::ErrorKind::FileTooLarge.into());
        }
        let mut temp = tempfile::NamedTempFile::new_in(&self.root)?;
        temp.write_all(&bytes)?;
        temp.as_file().sync_all()?;
        temp.persist(path)?;
        #[cfg(unix)]
        File::open(&self.root)?.sync_all()?;
        Ok(())
    }
}

pub fn read_json(path: &Path, maximum: u64) -> io::Result<Option<Value>> {
    read_bytes(path, maximum)?
        .map(|bytes| {
            serde_json::from_slice(&bytes)
                .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
        })
        .transpose()
}

pub fn read_bytes(path: &Path, maximum: u64) -> io::Result<Option<Vec<u8>>> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    if !file.metadata()?.file_type().is_file() {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    let mut bytes = Vec::new();
    file.take(maximum + 1).read_to_end(&mut bytes)?;
    if (bytes.len() as u64) > maximum {
        return Err(io::ErrorKind::FileTooLarge.into());
    }
    Ok(Some(bytes))
}

pub fn read_mode(root: &Path) -> io::Result<&'static str> {
    let file = match File::open(root.join("openbot-mode")) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok("official"),
        Err(error) => return Err(error),
    };
    let mut contents = String::new();
    file.take(65).read_to_string(&mut contents)?;
    Ok(if contents.len() <= 64 && contents.trim() == "custom" {
        "custom"
    } else {
        "official"
    })
}

pub fn router(state: State, policy: Policy, services: impl IntoIterator<Item = Router>) -> Router {
    let mut router = Router::new()
        .route("/api/bot-models", get(bot_models).put(update_bot_models))
        .route("/api/pause", get(pauses::global).put(pauses::save_global))
        .route("/api/pause-bots", get(pauses::bots).put(pauses::save_bots))
        .route(
            "/healthz",
            get(|| async { Json(json!({"ok": true, "service": "openbot"})) }),
        )
        .layer(DefaultBodyLimit::max(BOT_MODELS_MAX_BYTES as usize))
        .with_state(state);
    for service in services {
        router = router.merge(service);
    }
    router.layer(middleware::from_fn_with_state(policy, panel_requests))
}

async fn panel_requests(
    AxumState(policy): AxumState<Policy>,
    request: Request,
    next: Next,
) -> Response {
    let mut response = if request.uri().authority().is_some() {
        error(
            StatusCode::FORBIDDEN,
            "OpenBot requires a panel-relative request.",
        )
    } else {
        match policy
            .authorize(request.headers(), request.method(), request.uri().path())
            .await
        {
            Ok(()) => next.run(request).await,
            Err(denial) => {
                let (status, message) = denial.response();
                error(status, message)
            }
        }
    };
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        header::HeaderValue::from_static("no-store"),
    );
    response.headers_mut().insert(
        header::REFERRER_POLICY,
        header::HeaderValue::from_static("no-referrer"),
    );
    response
}

async fn bot_models(AxumState(state): AxumState<State>) -> Response {
    let view = tokio::task::spawn_blocking(move || -> io::Result<View> {
        let catalog = state.0.catalog()?;
        Ok(state.0.settings(false)?.view(&catalog))
    })
    .await;
    match view {
        Ok(Ok(view)) => Json(view).into_response(),
        _ => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Could not read OpenBot state.",
        ),
    }
}

async fn update_bot_models(
    AxumState(state): AxumState<State>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    if !state.0.writable {
        return error(
            StatusCode::METHOD_NOT_ALLOWED,
            "The comparison listener is read-only.",
        );
    }
    let Json(body) = match body {
        Ok(body) => body,
        Err(rejection) if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE => {
            return error(rejection.status(), "Bot settings request is too large.");
        }
        Err(_) => return error(StatusCode::BAD_REQUEST, "invalid json"),
    };
    let update = tokio::task::spawn_blocking(move || {
        let store = &state.0;
        let _transaction = store
            .writes
            .lock()
            .map_err(|_| io::Error::other("write lock poisoned"))?;
        let catalog = store.catalog()?;
        let mut settings = store.settings(true)?;
        if let Err(message) = settings.update(&body, &catalog) {
            return Ok::<_, io::Error>(Err(message));
        }
        store.save(&settings)?;
        Ok(Ok(settings.view(&catalog)))
    })
    .await;
    match update {
        Ok(Ok(Ok(view))) => Json(view).into_response(),
        Ok(Ok(Err(message))) => error(StatusCode::BAD_REQUEST, &message),
        _ => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "Could not save OpenBot state.",
        ),
    }
}

fn error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({ "error": message }))).into_response()
}

#[cfg(test)]
mod tests;
