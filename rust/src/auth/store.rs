use std::{
    collections::HashMap,
    fs::File,
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard},
};

use axum::http::StatusCode;
use serde_json::{Value, json};
use tokio::sync::watch;

use super::{
    AuthError,
    credential::{AccountRevision, Credential, OAuth, openai_origin, seconds},
    network::Network,
    unix_seconds,
};
use crate::{
    catalog::Catalog,
    lease::Lease,
    routing::CredentialKind,
    server::{PLAN_MAX_BYTES, read_json},
};

const STORE_MAX_BYTES: u64 = 4 * 1024 * 1024;
type Renewal = watch::Receiver<Option<Result<(), AuthError>>>;

#[derive(Clone)]
pub struct CredentialStore(Arc<Store>);

struct Store {
    root: PathBuf,
    network: Network,
    writer: Option<Lease>,
    writes: Mutex<()>,
    renewals: Mutex<HashMap<String, Renewal>>,
}

pub struct Authorized {
    pub kind: CredentialKind,
    pub token: String,
    pub account: Option<String>,
}

impl CredentialStore {
    pub fn new(root: &Path, writable: bool) -> io::Result<Self> {
        Self::with_network(root, writable, Network::new()?)
    }

    pub(super) fn with_network(root: &Path, writable: bool, network: Network) -> io::Result<Self> {
        let writer = writable
            .then(|| Lease::acquire(&root.join(".openbot-rust-secrets.lock")))
            .transpose()?;
        Ok(Self(Arc::new(Store {
            root: root.to_owned(),
            network,
            writer,
            writes: Mutex::new(()),
            renewals: Mutex::new(HashMap::new()),
        })))
    }

    pub(super) fn network(&self) -> &Network {
        &self.0.network
    }

    pub fn root(&self) -> &Path {
        &self.0.root
    }

    pub fn public_view(&self) -> Result<Value, AuthError> {
        Ok(public_fields(&self.plan()?, &self.load()?))
    }

    pub async fn authorize(&self, provider: &str, origin: &str) -> Result<Authorized, AuthError> {
        let (raw, credential) = self.read(provider, origin).await?;
        let oauth = match credential {
            Credential::Key(token) => return Ok(Authorized::key(token)),
            Credential::OAuth(oauth) => oauth,
        };
        let now = unix_seconds();
        if !oauth.needs_refresh(now) {
            return Ok(Authorized::oauth(&oauth));
        }
        let valid_until = seconds(&oauth.record["expiresAt"]).unwrap_or(0);
        if self.0.writer.is_none() {
            if valid_until > now {
                return Ok(Authorized::oauth(&oauth));
            }
            return Err(AuthError::unavailable(
                "The saved OpenAI sign-in has expired. Renew it in the current panel.",
            ));
        }
        if seconds(&oauth.record["earliestRefreshAt"]).is_some_and(|time| time > now) {
            return Err(AuthError::unavailable(
                "OpenAI has not opened the renewal window. Try again later.",
            ));
        }

        let mut pending = self.renewal(provider, raw, oauth)?;
        let result = pending
            .wait_for(Option::is_some)
            .await
            .map_err(|_| AuthError::unavailable("OpenAI renewal did not finish."))?
            .clone()
            .ok_or_else(|| AuthError::unavailable("OpenAI renewal did not finish."))?;
        let (_, current) = self.read(provider, origin).await?;
        match current {
            Credential::Key(token) => Ok(Authorized::key(token)),
            Credential::OAuth(oauth)
                if seconds(&oauth.record["expiresAt"])
                    .is_some_and(|time| time > unix_seconds()) =>
            {
                Ok(Authorized::oauth(&oauth))
            }
            Credential::OAuth(_) => {
                result?;
                Err(AuthError::unavailable(
                    "The saved OpenAI sign-in has expired. Sign in again.",
                ))
            }
        }
    }

    pub async fn wait_for_renewals(&self) {
        let Ok(mut pending) = self
            .0
            .renewals
            .lock()
            .map(|renewals| renewals.values().cloned().collect::<Vec<_>>())
        else {
            return;
        };
        for renewal in &mut pending {
            let _ = renewal.wait_for(Option::is_some).await;
        }
    }

    async fn read(&self, provider: &str, origin: &str) -> Result<(String, Credential), AuthError> {
        let store = self.clone();
        let provider = provider.to_owned();
        let raw = blocking(move || {
            raw_secret(&store.load()?, &provider)
                .map(str::to_owned)
                .ok_or_else(|| {
                    AuthError::new(StatusCode::NOT_FOUND, "No credential for this provider.")
                })
        })
        .await?;
        let credential = Credential::parse(&raw)?;
        if let Credential::OAuth(oauth) = &credential {
            oauth.check_origin(origin)?;
        }
        Ok((raw, credential))
    }

    fn renewal(&self, provider: &str, raw: String, oauth: OAuth) -> Result<Renewal, AuthError> {
        let mut renewals = self.0.renewals.lock().map_err(|_| store_error())?;
        if let Some(pending) = renewals.get(provider) {
            return Ok(pending.clone());
        }
        let (complete, pending) = watch::channel(None);
        renewals.insert(provider.into(), pending.clone());
        let store = self.clone();
        let provider = provider.to_owned();
        tokio::spawn(async move {
            let result = store.renew(&provider, raw, oauth).await;
            if let Ok(mut renewals) = store.0.renewals.lock() {
                renewals.remove(&provider);
                complete.send_replace(Some(result));
            }
        });
        Ok(pending)
    }

    async fn renew(&self, provider: &str, raw: String, oauth: OAuth) -> Result<(), AuthError> {
        let check = self.clone();
        let id = provider.to_owned();
        let expected = raw.clone();
        let staged = blocking(move || {
            if raw_secret(&check.load()?, &id) != Some(&expected) {
                return Ok(None);
            }
            // Confirm we can stage the replacement before using the rotating token.
            tempfile::NamedTempFile::new_in(check.root())
                .map(Some)
                .map_err(|_| store_error())
        })
        .await?;
        let Some(staged) = staged else {
            return Ok(());
        };
        let payload = self.network().refresh(&oauth).await?;
        let updated = Credential::OAuth(oauth.refreshed(&payload)?).into_saved();
        let store = self.clone();
        let provider = provider.to_owned();
        blocking(move || {
            let _write = store.write_lock()?;
            let mut current = store.load()?;
            if raw_secret(&current, &provider) == Some(&raw) {
                current["providers"][provider] = json!(updated);
                let staged = stage_json(staged, &current, STORE_MAX_BYTES)?;
                store.commit(staged, "secrets.json")?;
            }
            Ok(())
        })
        .await
    }

    pub(super) fn account(&self, id: &str) -> Result<(String, AccountRevision), AuthError> {
        let plan = self.plan()?;
        let catalog = Catalog::from_plan(&plan);
        let origin = catalog
            .providers
            .into_iter()
            .find(|provider| provider.id == id)
            .ok_or_else(|| AuthError::input("Select an existing OpenAI provider."))?
            .origin;
        Ok((origin, AccountRevision::of(raw_secret(&self.load()?, id))))
    }

    pub(super) fn save_secret(
        &self,
        id: &str,
        secret: &str,
        expected: Option<&AccountRevision>,
    ) -> Result<Value, AuthError> {
        let _write = self.write_lock()?;
        let plan = self.plan()?;
        let catalog = Catalog::from_plan(&plan);
        let provider = catalog
            .providers
            .iter()
            .find(|provider| provider.id == id)
            .ok_or_else(|| AuthError::input("Select an existing OpenAI provider."))?;
        if !openai_origin(&provider.origin) {
            return Err(AuthError::input(
                "Select an existing direct OpenAI provider.",
            ));
        }
        let mut store = self.load()?;
        if expected.is_some_and(|expected| *expected != AccountRevision::of(raw_secret(&store, id)))
        {
            return Err(AuthError::conflict(
                "The selected OpenAI account changed. Start sign-in again.",
            ));
        }
        let saved = Credential::import(secret, &provider.origin)?.into_saved();
        store["providers"][id] = json!(saved);
        self.store(&store)?;
        Ok(save_reply(&plan, &store, None))
    }

    pub(super) fn add_openai(
        &self,
        name: &str,
        origin: &str,
        protocol: &str,
        secret: &str,
    ) -> Result<Value, AuthError> {
        let _write = self.write_lock()?;
        let mut plan = self.plan()?;
        let mut store = self.load()?;
        let secret = Credential::import(secret, origin)?.into_saved();
        let ids = plan["catalog"]["providers"]
            .as_array()
            .ok_or_else(store_error)?;
        let candidates = std::iter::once(String::from("openai"))
            .chain((2..=10000).map(|suffix| format!("openai-{suffix}")));
        let id = candidates
            .into_iter()
            .find(|id| {
                raw_secret(&store, id).is_none() && !ids.iter().any(|entry| entry["id"] == *id)
            })
            .ok_or_else(|| AuthError::unavailable("OpenBot has too many OpenAI account IDs."))?;
        plan["catalog"]["providers"]
            .as_array_mut()
            .ok_or_else(store_error)?
            .push(json!({
                "id": id, "name": name, "origin": origin, "apiType": protocol,
                "maxTokensDefault": 65536, "mapFile": "provider-maps.cjs"
            }));
        store["providers"][&id] = json!(secret);
        let staged_plan = stage(self.root(), &plan, PLAN_MAX_BYTES)?;
        let staged_store = stage(self.root(), &store, STORE_MAX_BYTES)?;
        // The provider becomes visible only after its credential is in place.
        // An interrupted addition reserves its ID without making it selectable.
        self.commit(staged_store, "secrets.json")?;
        self.commit(staged_plan, "openbot-plan.json")?;
        Ok(save_reply(&plan, &store, Some(id)))
    }

    fn write_lock(&self) -> Result<MutexGuard<'_, ()>, AuthError> {
        if self.0.writer.is_none() {
            return Err(AuthError::new(
                StatusCode::METHOD_NOT_ALLOWED,
                "The credential store is read-only.",
            ));
        }
        self.0.writes.lock().map_err(|_| store_error())
    }

    fn load(&self) -> Result<Value, AuthError> {
        let result = read_json(&self.root().join("secrets.json"), STORE_MAX_BYTES)
            .map_err(|_| store_error())?
            .unwrap_or_else(|| json!({"providers":{}}));
        if result["providers"]
            .as_object()
            .is_none_or(|items| items.values().any(|value| !value.is_string()))
        {
            return Err(store_error());
        }
        Ok(result)
    }

    fn plan(&self) -> Result<Value, AuthError> {
        let plan = read_json(&self.root().join("openbot-plan.json"), PLAN_MAX_BYTES)
            .map_err(|_| store_error())?
            .unwrap_or_else(|| {
                json!({
                    "kind":"custom", "hop":{"host":"127.0.0.1","port":9280},
                    "hopBaseUrl":"http://127.0.0.1:9280/v1", "agents":{},
                    "catalog":{"providers":[],"models":[],"bindings":[]}
                })
            });
        if plan["kind"] != "custom"
            || ["providers", "models", "bindings"]
                .iter()
                .any(|key| !plan["catalog"][*key].is_array())
        {
            return Err(store_error());
        }
        Ok(plan)
    }

    fn store(&self, value: &Value) -> Result<(), AuthError> {
        self.commit(stage(self.root(), value, STORE_MAX_BYTES)?, "secrets.json")
    }

    fn commit(&self, staged: tempfile::NamedTempFile, filename: &str) -> Result<(), AuthError> {
        staged
            .persist(self.root().join(filename))
            .map_err(|_| store_error())?;
        #[cfg(unix)]
        File::open(self.root())
            .and_then(|directory| directory.sync_all())
            .map_err(|_| store_error())?;
        Ok(())
    }
}

impl Authorized {
    fn key(token: String) -> Self {
        Self {
            kind: CredentialKind::ApiKey,
            token,
            account: None,
        }
    }

    fn oauth(oauth: &OAuth) -> Self {
        Self {
            kind: oauth.kind,
            token: oauth.access().into(),
            account: oauth.account(),
        }
    }
}

pub(crate) async fn blocking<T: Send + 'static>(
    task: impl FnOnce() -> Result<T, AuthError> + Send + 'static,
) -> Result<T, AuthError> {
    tokio::task::spawn_blocking(task)
        .await
        .map_err(|_| store_error())?
}

fn raw_secret<'a>(store: &'a Value, id: &str) -> Option<&'a str> {
    store["providers"][id].as_str()
}

fn stage(root: &Path, value: &Value, limit: u64) -> Result<tempfile::NamedTempFile, AuthError> {
    let file = tempfile::NamedTempFile::new_in(root).map_err(|_| store_error())?;
    stage_json(file, value, limit)
}

fn stage_json(
    mut file: tempfile::NamedTempFile,
    value: &Value,
    limit: u64,
) -> Result<tempfile::NamedTempFile, AuthError> {
    let mut bytes = serde_json::to_vec_pretty(value).map_err(|_| store_error())?;
    bytes.push(b'\n');
    if bytes.len() as u64 > limit {
        return Err(AuthError::unavailable(
            "OpenBot account settings exceed the storage limit.",
        ));
    }
    file.write_all(&bytes)
        .and_then(|()| file.as_file().sync_all())
        .map_err(|_| store_error())?;
    Ok(file)
}

fn public_fields(plan: &Value, store: &Value) -> Value {
    let active = plan["catalog"]["bindings"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|row| row["conversation"]["kind"] == "wildcard")
        .and_then(|row| row["modelId"].as_str());
    let keyed: Vec<_> = store["providers"]
        .as_object()
        .into_iter()
        .flat_map(|entries| entries.keys())
        .collect();
    json!({
        "providers":plan["catalog"]["providers"],
        "models":plan["catalog"]["models"], "keyedProviders":keyed, "activeModelId":active
    })
}

fn save_reply(plan: &Value, store: &Value, created: Option<String>) -> Value {
    let mut reply = public_fields(plan, store);
    reply["ok"] = json!(true);
    reply["wrapBytesChanged"] = json!(false);
    if let Some(id) = created {
        reply["createdProviderId"] = json!(id);
    }
    reply
}

fn store_error() -> AuthError {
    AuthError::unavailable("OpenBot could not read or save its provider account settings.")
}
