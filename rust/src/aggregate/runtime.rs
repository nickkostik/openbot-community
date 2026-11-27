use std::{
    io,
    path::Path,
    sync::{Arc, Mutex, MutexGuard},
    time::Duration,
};

use axum::{
    Json, Router,
    extract::{Query, State as AxumState},
    http::StatusCode,
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use tokio::sync::watch;

use super::{Inventory, MAX_CACHE_BYTES, plans, public::PublicCatalog};
use crate::{
    auth::{AuthError, CredentialStore, blocking},
    discovery::Discovery,
    server::{State, read_json},
};

const CACHE_FILE: &str = "openbot-model-catalog.json";
const SETTINGS_FILE: &str = "openbot-model-refresh.json";
const STATUS_FILE: &str = "openbot-model-refresh-status.json";
const STATUS_MAX_BYTES: u64 = 64 * 1024;
const STARTUP_DELAY: Duration = Duration::from_secs(15);
const CHECK_INTERVAL: Duration = Duration::from_secs(60);

#[derive(Clone)]
pub struct CatalogService {
    state: State,
    data: Arc<Mutex<Readback>>,
    job: Option<Arc<Job>>,
    stopping: watch::Sender<bool>,
}

struct Readback {
    inventory: Arc<Inventory>,
    status: Value,
    flight: Option<Flight>,
    error: Option<String>,
}

struct Flight {
    started_at: String,
    finished: watch::Receiver<bool>,
}

struct Job {
    discovery: Discovery,
    credentials: CredentialStore,
    public: PublicCatalog,
}

#[derive(Clone, Copy)]
struct Schedule {
    enabled: bool,
    interval: Duration,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Lookup {
    model_id: Option<String>,
    provider_id: Option<String>,
}

impl CatalogService {
    pub fn new(state: State, refresh: Option<(Discovery, CredentialStore)>) -> io::Result<Self> {
        let job = refresh
            .map(|(discovery, credentials)| {
                PublicCatalog::new().map(|public| Job {
                    discovery,
                    credentials,
                    public,
                })
            })
            .transpose()?;
        Ok(Self::with_job(state, job))
    }

    fn with_job(state: State, job: Option<Job>) -> Self {
        let inventory = Arc::new(Inventory::load(state.root()));
        let mut status = read_json(&state.root().join(STATUS_FILE), STATUS_MAX_BYTES)
            .ok()
            .flatten()
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        if status["state"] == "running"
            && let Some(status) = status.as_object_mut()
        {
            status.shift_remove("nextAttemptAt");
        }
        status["state"] = json!("idle");
        Self {
            state,
            data: Arc::new(Mutex::new(Readback {
                inventory,
                status,
                flight: None,
                error: None,
            })),
            job: job.map(Arc::new),
            stopping: watch::Sender::new(false),
        }
    }

    pub fn routes(&self) -> Router {
        Router::new()
            .route("/api/model-catalog", get(lookup))
            .route("/api/model-catalog/refresh", post(refresh))
            .with_state(self.clone())
    }

    pub fn start(&self) {
        if self.job.is_none() {
            return;
        }
        let service = self.clone();
        let mut stopping = self.stopping.subscribe();
        tokio::spawn(async move {
            let mut delay = STARTUP_DELAY;
            loop {
                tokio::select! {
                    _ = stopping.wait_for(|stopped| *stopped) => return,
                    _ = tokio::time::sleep(delay) => {}
                }
                let _ = service.check_schedule().await;
                delay = CHECK_INTERVAL;
            }
        });
    }

    pub fn stop(&self) {
        self.stopping.send_replace(true);
    }

    pub async fn wait_idle(&self) {
        let finished = self
            .read()
            .ok()
            .and_then(|data| data.flight.as_ref().map(|run| run.finished.clone()));
        if let Some(mut finished) = finished {
            let _ = finished.wait_for(|done| *done).await;
        }
    }

    async fn check_schedule(&self) -> Result<Option<String>, AuthError> {
        let config = self.schedule().await?;
        if !config.enabled {
            return Ok(None);
        }
        self.enqueue(config, true)
    }

    async fn schedule(&self) -> Result<Schedule, AuthError> {
        let state = self.state.clone();
        blocking(move || Ok(Schedule::read(state.root()))).await
    }

    fn read(&self) -> Result<MutexGuard<'_, Readback>, AuthError> {
        self.data
            .lock()
            .map_err(|_| AuthError::unavailable("Model catalog is unavailable."))
    }

    fn enqueue(&self, config: Schedule, scheduled: bool) -> Result<Option<String>, AuthError> {
        let Some(job) = self.job.clone() else {
            return Err(AuthError {
                status: StatusCode::METHOD_NOT_ALLOWED,
                message: "Catalog refresh is disabled on this listener.".into(),
                upstream: None,
            });
        };
        let mut data = self.read()?;
        if let Some(flight) = &data.flight {
            return Ok(Some(flight.started_at.clone()));
        }
        if *self.stopping.borrow() {
            return Err(AuthError::unavailable("OpenBot is shutting down."));
        }
        let now = OffsetDateTime::now_utc();
        if scheduled && !is_due(&data.status, now) {
            return Ok(None);
        }
        let stamp = iso(now)?;
        let mut status = data.status.clone();
        status["state"] = json!(if config.enabled {
            "running"
        } else {
            "disabled"
        });
        if config.enabled {
            status["lastAttemptAt"] = json!(stamp);
            status["nextAttemptAt"] = json!(iso(now + config.interval)?);
        }
        data.status = status.clone();
        let (finished, completion) = watch::channel(false);
        data.flight = Some(Flight {
            started_at: stamp.clone(),
            finished: completion,
        });
        let inventory = data.inventory.clone();
        drop(data);
        let service = self.clone();
        let started = stamp.clone();
        tokio::spawn(async move {
            service
                .execute(job, inventory, config, &started, status)
                .await;
            finished.send_replace(true);
        });
        Ok(Some(stamp))
    }

    async fn execute(
        &self,
        job: Arc<Job>,
        inventory: Arc<Inventory>,
        config: Schedule,
        stamp: &str,
        mut status: Value,
    ) {
        let mut stopping = self.stopping.subscribe();
        let result = self
            .run(&job, inventory, config, stamp, &status, &mut stopping)
            .await;
        match result {
            Ok(Some((counts, providers))) => {
                status["state"] = json!("completed");
                if let Ok(now) = iso(OffsetDateTime::now_utc()) {
                    status["lastCompletedAt"] = json!(now);
                }
                status["added"] = json!(counts.added);
                status["updated"] = json!(counts.updated);
                status["reasoningHeld"] = json!(counts.reasoning_held);
                status["providers"] = json!(providers);
            }
            Ok(None) if *self.stopping.borrow() => {
                status["state"] = json!("idle");
                if let Some(status) = status.as_object_mut() {
                    status.shift_remove("nextAttemptAt");
                }
            }
            Ok(None) => status["state"] = json!("disabled"),
            Err(error) => {
                status["state"] = json!("failed");
                if let Ok(mut data) = self.read() {
                    data.error = Some(error.message);
                }
            }
        }
        if persist(
            job.credentials.clone(),
            STATUS_FILE,
            status.clone(),
            STATUS_MAX_BYTES,
        )
        .await
        .is_err()
        {
            status["state"] = json!("failed");
        }
        if let Ok(mut data) = self.read() {
            data.status = status;
            data.flight = None;
        }
    }

    async fn run(
        &self,
        job: &Job,
        current: Arc<Inventory>,
        config: Schedule,
        stamp: &str,
        status: &Value,
        stopping: &mut watch::Receiver<bool>,
    ) -> Result<Option<(plans::Changes, Vec<Value>)>, AuthError> {
        persist(
            job.credentials.clone(),
            STATUS_FILE,
            status.clone(),
            STATUS_MAX_BYTES,
        )
        .await?;
        let results = tokio::select! {
            biased;
            _ = stopping.wait_for(|stop| *stop) => return Ok(None),
            results = job.public.fetch() => results,
        };
        let public_error = results
            .iter()
            .find_map(|result| result.as_ref().err().cloned());
        let fallback = current.clone();
        let stamp = stamp.to_owned();
        let credentials = job.credentials.clone();
        let inventory = blocking(move || {
            if let Some(next) = super::public::combine(&current, results, &stamp) {
                if let Some(data) = &next.data {
                    credentials.save_cache(CACHE_FILE, data, MAX_CACHE_BYTES)?;
                }
                Ok(Arc::new(next))
            } else {
                Ok(fallback)
            }
        })
        .await?;
        {
            let mut data = self.read()?;
            data.inventory = inventory.clone();
            data.error = public_error;
        }
        if !config.enabled || *stopping.borrow() {
            return Ok(None);
        }
        let state = self.state.clone();
        let targets = blocking(move || plans::targets(&state)).await?;
        let lists = tokio::select! {
            biased;
            _ = stopping.wait_for(|stop| *stop) => return Ok(None),
            listings = plans::collect(&job.discovery, targets) => listings,
        };
        let summary: Vec<_> = lists.iter().map(plans::Listing::status).collect();
        let credentials = job.credentials.clone();
        let state = self.state.clone();
        let stop = self.stopping.subscribe();
        blocking(move || {
            if *stop.borrow() || !Schedule::read(state.root()).enabled {
                Ok(None)
            } else {
                plans::update(&state, &credentials, &inventory, &lists)
                    .map(|counts| Some((counts, summary)))
            }
        })
        .await
    }
}

impl Schedule {
    fn read(root: &Path) -> Self {
        let settings = read_json(&root.join(SETTINGS_FILE), STATUS_MAX_BYTES)
            .ok()
            .flatten()
            .unwrap_or_default();
        let hours = settings["intervalHours"]
            .as_f64()
            .filter(|hours| hours.is_finite())
            .map_or(6.0, |hours| hours.clamp(1.0, 168.0));
        Self {
            enabled: settings["enabled"] != false,
            interval: Duration::from_secs_f64(hours * 3600.0),
        }
    }
}

fn iso(time: OffsetDateTime) -> Result<String, AuthError> {
    time.format(&Rfc3339)
        .map_err(|_| AuthError::unavailable("Model catalog time is unavailable."))
}

fn is_due(status: &Value, now: OffsetDateTime) -> bool {
    status["nextAttemptAt"]
        .as_str()
        .and_then(|time| OffsetDateTime::parse(time, &Rfc3339).ok())
        .is_none_or(|due| due <= now)
}

async fn persist(
    credentials: CredentialStore,
    path: &'static str,
    data: Value,
    limit: u64,
) -> Result<(), AuthError> {
    blocking(move || credentials.save_cache(path, &data, limit)).await
}

async fn lookup(
    AxumState(catalog): AxumState<CatalogService>,
    Query(request): Query<Lookup>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let internal = |error: AuthError| {
        (
            error.status,
            Json(json!({"error":{"kind":"internal","message":error.message}})),
        )
    };
    let origin = if let Some(id) = request.provider_id {
        let state = catalog.state.clone();
        let found = blocking(move || {
            let providers = state.catalog().map_err(|_| {
                AuthError::unavailable("OpenBot could not read the provider catalog.")
            })?;
            Ok(providers
                .providers
                .into_iter()
                .find(|provider| provider.id == id))
        })
        .await
        .map_err(internal)?;
        Some(found.ok_or_else(|| {
            (StatusCode::NOT_FOUND, Json(json!({"error":{"kind":"provider-not-found","message":"Provider not found."}})))
        })?.origin)
    } else {
        None
    };
    let schedule = if catalog.job.is_some() {
        Some(catalog.schedule().await.map_err(internal)?)
    } else {
        None
    };
    let (inventory, running, error, status) = {
        let data = catalog.read().map_err(internal)?;
        (
            data.inventory.clone(),
            data.flight.is_some(),
            data.error.clone(),
            data.status.clone(),
        )
    };
    let mut result = inventory.snapshot(request.model_id.as_deref(), origin.as_deref());
    if running {
        result["status"] = json!("loading");
        if let Some(result) = result.as_object_mut() {
            result.shift_remove("error");
        }
    } else if inventory.data.is_none()
        && let Some(message) = error
    {
        result["error"] = json!({"kind":"unreachable","message":message});
    }
    if let Some(config) = schedule {
        let mut status = status;
        status["enabled"] = json!(config.enabled);
        status["intervalHours"] = json!(config.interval.as_secs_f64() / 3600.0);
        result["autoRefresh"] = status;
    }
    Ok(Json(result))
}

async fn refresh(
    AxumState(catalog): AxumState<CatalogService>,
) -> Result<(StatusCode, Json<Value>), AuthError> {
    let stamp = catalog
        .enqueue(catalog.schedule().await?, false)?
        .ok_or_else(|| AuthError::unavailable("Could not start catalog refresh."))?;
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({"ok":true,"status":"loading","startedAt":stamp})),
    ))
}

#[cfg(test)]
mod tests;
