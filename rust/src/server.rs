use std::{
    fs::{File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Request, State as AxumState, rejection::JsonRejection},
    http::{StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use serde_json::{Value, json};

use crate::{
    bot_models::{Settings, View},
    catalog::Catalog,
    hop::Hop,
    routing::{self, CredentialKind, Route},
};

const PLAN_MAX_BYTES: u64 = 16 * 1024 * 1024;
const BOT_MODELS_MAX_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Clone)]
pub struct State(Arc<Store>);

struct Store {
    root: PathBuf,
    writer: Option<File>,
    writes: Mutex<()>,
}

impl State {
    pub fn new(root: PathBuf, writable: bool) -> io::Result<Self> {
        let root = root.canonicalize()?;
        if !root.is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotADirectory,
                "expected an OpenBot state directory",
            ));
        }
        let writer = if writable {
            let file = OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(root.join(".openbot-rust-bot-settings.lock"))?;
            file.try_lock()?;
            Some(file)
        } else {
            None
        };
        Ok(Self(Arc::new(Store {
            root,
            writer,
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
        let mut bytes = serde_json::to_vec_pretty(&settings.saved()?)?;
        bytes.push(b'\n');
        if bytes.len() as u64 > BOT_MODELS_MAX_BYTES {
            return Err(io::ErrorKind::FileTooLarge.into());
        }
        let mut temp = tempfile::NamedTempFile::new_in(&self.root)?;
        temp.write_all(&bytes)?;
        temp.as_file().sync_all()?;
        temp.persist(self.settings_path())?;
        #[cfg(unix)]
        File::open(&self.root)?.sync_all()?;
        Ok(())
    }
}

pub fn read_json(path: &Path, maximum: u64) -> io::Result<Option<Value>> {
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
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

pub fn router(state: State, authority: String, hop: Option<Hop>) -> Router {
    let mut router = Router::new()
        .route("/api/bot-models", get(bot_models).put(update_bot_models))
        .layer(DefaultBodyLimit::max(BOT_MODELS_MAX_BYTES as usize))
        .with_state(state);
    if let Some(hop) = hop {
        router = router.merge(hop.routes());
    }
    router.layer(middleware::from_fn_with_state(authority, local_requests))
}

async fn local_requests(
    AxumState(authority): AxumState<String>,
    request: Request,
    next: Next,
) -> Response {
    let headers = request.headers();
    let origin = format!("http://{authority}");
    if headers
        .get(header::HOST)
        .and_then(|value| value.to_str().ok())
        != Some(&authority)
        || headers
            .get(header::ORIGIN)
            .is_some_and(|value| value != &origin)
    {
        return error(
            StatusCode::FORBIDDEN,
            "OpenBot requires its loopback origin.",
        );
    }
    let mut response = next.run(request).await;
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        header::HeaderValue::from_static("no-store"),
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
    if state.0.writer.is_none() {
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
