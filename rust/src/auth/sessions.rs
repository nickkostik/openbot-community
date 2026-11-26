use std::{
    collections::HashMap,
    fs::File,
    io::{self, Read, Write},
    path::Path,
    sync::{Arc, Mutex, MutexGuard},
    time::{Duration, Instant},
};

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, RawQuery, State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use url::Url;

use super::{
    AuthError, CredentialStore,
    credential::{self, AccountRevision, CODEX_CLIENT, Credential, OAuth, RESOURCE},
    hex, random,
    store::blocking,
    unique_id,
};
use crate::routing::CredentialKind;

const LEGACY_CALLBACK: &str = "http://localhost:1455/auth/callback";
const INITIAL_CLIENT: &str = "dynamic_agent_client";
const LIFETIME: Duration = Duration::from_secs(1800);
const MAX_ATTEMPTS: usize = 32;
const MAX_CALLBACK_BYTES: usize = 16 * 1024;

#[derive(Clone)]
pub struct Accounts(Arc<AccountsInner>);

struct AccountsInner {
    store: CredentialStore,
    callback: Url,
    sessions: Mutex<Sessions>,
}

#[derive(Default)]
struct Sessions {
    attempts: HashMap<String, Attempt>,
    drafts: HashMap<String, Draft>,
}

struct Attempt {
    login: Login,
    phase: Phase,
}

#[derive(Clone)]
struct Login {
    state: String,
    verifier: String,
    deadline: Instant,
    flow: Flow,
}

#[derive(Clone)]
enum Flow {
    Plan {
        nonce: String,
        target: Option<Target>,
    },
    Legacy,
}

#[derive(Clone)]
struct Target {
    id: String,
    revision: AccountRevision,
}

enum Phase {
    Pending,
    Exchanging,
    Ready(String),
    Failed(String),
}

struct Draft {
    secret: String,
    session: String,
    target: Option<Target>,
    deadline: Instant,
}

impl Accounts {
    pub fn new(store: CredentialStore, authority: &str) -> io::Result<Self> {
        let callback =
            Url::parse(&format!("http://{authority}/auth/callback")).map_err(io::Error::other)?;
        if callback.host_str() != Some("127.0.0.1") || callback.port().is_none() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "OpenAI sign-in requires a loopback port other than 80",
            ));
        }
        Ok(Self(Arc::new(AccountsInner {
            store,
            callback,
            sessions: Mutex::default(),
        })))
    }

    pub fn routes(self) -> Router {
        Router::new()
            .route("/api/oauth/chatgpt/start", post(start_plan))
            .route("/api/oauth/chatgpt/status", post(status))
            .route("/auth/callback", get(callback))
            .route("/api/oauth/openai/start", post(start_legacy))
            .route("/api/oauth/openai/complete", post(complete_legacy))
            .route("/api/save", post(save))
            .layer(DefaultBodyLimit::max(2 * credential::MAX_CREDENTIAL_BYTES))
            .with_state(self)
    }

    fn sessions(&self) -> Result<MutexGuard<'_, Sessions>, AuthError> {
        let mut sessions = self.0.sessions.lock().map_err(|_| sign_in_error())?;
        let now = Instant::now();
        sessions
            .attempts
            .retain(|_, attempt| attempt.login.deadline > now);
        sessions.drafts.retain(|_, draft| draft.deadline > now);
        Ok(sessions)
    }

    fn begin(&self, flow: Flow, host: Option<String>) -> Result<Value, AuthError> {
        let mut sessions = self.sessions()?;
        if sessions.attempts.len() >= MAX_ATTEMPTS {
            return Err(AuthError::new(
                StatusCode::TOO_MANY_REQUESTS,
                "Too many pending OpenAI sign-ins. Finish one or wait for it to expire.",
            ));
        }
        let session = unique_id()?;
        let login = Login {
            state: hex(&random::<32>()?),
            verifier: URL_SAFE_NO_PAD.encode(random::<48>()?),
            deadline: Instant::now() + LIFETIME,
            flow,
        };
        let challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(login.verifier.as_bytes()));
        let network = self.0.store.network();
        let mut url = match &login.flow {
            Flow::Plan { nonce, target } => {
                let mut url = network.plan_authorize.clone();
                let returning = target.as_ref().and_then(|target| match &target.revision {
                    AccountRevision::Plan { client_id, .. } => Some(client_id.as_str()),
                    _ => None,
                });
                let scopes = format!(
                    "openid profile email offline_access {}",
                    credential::REQUIRED_SCOPES.join(" ")
                );
                let host = host.as_deref().ok_or_else(sign_in_error)?;
                url.query_pairs_mut().extend_pairs([
                    ("client_id", returning.unwrap_or(INITIAL_CLIENT)),
                    ("redirect_uri", self.0.callback.as_str()),
                    ("ext_agent_host_id", host),
                    ("nonce", nonce),
                    ("scope", &scopes),
                    ("resource", RESOURCE),
                ]);
                if returning.is_none() {
                    url.query_pairs_mut()
                        .append_pair("agent_name_hint", "OpenBot");
                }
                url
            }
            Flow::Legacy => {
                let mut url = network.codex_authorize.clone();
                url.query_pairs_mut().extend_pairs([
                    ("client_id", CODEX_CLIENT),
                    ("redirect_uri", LEGACY_CALLBACK),
                    ("scope", "openid profile email offline_access"),
                    ("prompt", "login"),
                    ("id_token_add_organizations", "true"),
                    ("codex_cli_simplified_flow", "true"),
                ]);
                url
            }
        };
        url.query_pairs_mut().extend_pairs([
            ("response_type", "code"),
            ("state", &login.state),
            ("code_challenge", &challenge),
            ("code_challenge_method", "S256"),
        ]);
        sessions.attempts.insert(
            session.clone(),
            Attempt {
                login,
                phase: Phase::Pending,
            },
        );
        Ok(
            json!({"sessionId":session, "authorizationUrl":url.as_str(), "expiresIn":LIFETIME.as_secs()}),
        )
    }

    fn claim(&self, session: Option<&str>, state: &str) -> Result<(String, Login), AuthError> {
        let mut sessions = self.sessions()?;
        let (id, attempt) = sessions
            .attempts
            .iter_mut()
            .find(|(id, attempt)| {
                let belongs = match &attempt.login.flow {
                    Flow::Plan { .. } => session.is_none(),
                    Flow::Legacy => session.is_some_and(|requested| *id == requested),
                };
                belongs && matches!(attempt.phase, Phase::Pending) && attempt.login.state == state
            })
            .ok_or_else(|| {
                AuthError::input(
                    "OpenAI sign-in is invalid, expired, or already used. Start again.",
                )
            })?;
        attempt.phase = Phase::Exchanging;
        Ok((id.clone(), attempt.login.clone()))
    }

    async fn finish(&self, id: String, login: Login, url: Url) -> Result<String, AuthError> {
        let accounts = self.clone();
        // A browser navigating away cannot cancel the one-time exchange.
        tokio::spawn(async move {
            let outcome = accounts.exchange(&login, &url).await;
            let mut sessions = accounts.sessions()?;
            let Some(attempt) = sessions.attempts.get_mut(&id) else {
                return Err(AuthError::input("OpenAI sign-in expired. Start again."));
            };
            let secret = match outcome {
                Ok(secret) => secret,
                Err(error) => {
                    attempt.phase = Phase::Failed(error.message.clone());
                    return Err(error);
                }
            };
            let credential = unique_id()?;
            let target = match login.flow {
                Flow::Plan { target, .. } => target,
                Flow::Legacy => None,
            };
            attempt.phase = Phase::Ready(credential.clone());
            sessions.drafts.insert(
                credential.clone(),
                Draft {
                    secret,
                    session: id,
                    target,
                    deadline: Instant::now() + LIFETIME,
                },
            );
            Ok(credential)
        })
        .await
        .map_err(|_| sign_in_error())?
    }

    async fn exchange(&self, login: &Login, url: &Url) -> Result<String, AuthError> {
        if parameter(url, "error")?.is_some() {
            return Err(AuthError::input(
                "OpenAI sign-in was declined or cancelled.",
            ));
        }
        let code = parameter(url, "code")?
            .ok_or_else(|| AuthError::input("OpenAI returned no sign-in code."))?;
        let network = self.0.store.network();
        let mut form = vec![
            ("grant_type", "authorization_code"),
            ("code", code.as_str()),
            ("code_verifier", &login.verifier),
        ];
        let (kind, token, mut record) = match &login.flow {
            Flow::Plan { nonce, target } => {
                let issued = parameter(url, "client_id")?;
                let previous = target.as_ref().and_then(|target| match &target.revision {
                    AccountRevision::Plan { client_id, subject } => {
                        Some((client_id.as_str(), subject.as_str()))
                    }
                    _ => None,
                });
                let client = previous
                    .map(|(client, _)| client)
                    .or(issued.as_deref())
                    .filter(|client| credential::client_id(&json!(client)))
                    .filter(|client| issued.as_deref().is_none_or(|issued| issued == *client))
                    .ok_or_else(|| {
                        AuthError::input("OpenAI did not return the expected OpenBot registration.")
                    })?;
                form.extend([
                    ("client_id", client),
                    ("redirect_uri", self.0.callback.as_str()),
                    ("resource", RESOURCE),
                ]);
                let kind = CredentialKind::ChatGptPlan;
                let token = network.exchange(kind, &form).await?;
                let id = credential::text(&token["id_token"]).ok_or_else(|| {
                    AuthError::input("OpenAI returned an incomplete ChatGPT login.")
                })?;
                let scopes = credential::scope_array(&token["scope"]);
                if !credential::has_plan_scopes(&scopes) {
                    return Err(AuthError::input(
                        "ChatGPT plan usage permission was not granted.",
                    ));
                }
                let subject = network.verified_subject(id, client, nonce).await?;
                if previous.is_some_and(|(_, expected)| subject != expected) {
                    return Err(AuthError::conflict(
                        "Sign in to the ChatGPT account already linked to this provider.",
                    ));
                }
                let record = json!({"kind":"chatgpt-plan", "clientId":client, "subject":subject, "idToken":id, "scopes":scopes});
                (kind, token, record)
            }
            Flow::Legacy => {
                form.extend([
                    ("client_id", CODEX_CLIENT),
                    ("redirect_uri", LEGACY_CALLBACK),
                ]);
                let kind = CredentialKind::OpenAiOauth;
                let token = network.exchange(kind, &form).await?;
                let mut record = json!({"kind":"openai-oauth"});
                if let Some(account) = credential::account_id(
                    credential::text(&token["id_token"]).unwrap_or_default(),
                    credential::text(&token["access_token"]).unwrap_or_default(),
                ) {
                    record["chatgptAccountId"] = json!(account);
                }
                (kind, token, record)
            }
        };
        let required = |field| {
            credential::text(&token[field])
                .ok_or_else(|| AuthError::input("OpenAI returned an incomplete sign-in."))
        };
        record["accessToken"] = json!(required("access_token")?);
        record["refreshToken"] = json!(required("refresh_token")?);
        record["expiresAt"] = json!(credential::expiry(&token, kind)?);
        credential::update_earliest(&mut record, &token);
        let saved = Credential::OAuth(OAuth::checked(kind, record)?).into_saved();
        if saved.len() > credential::MAX_CREDENTIAL_BYTES {
            return Err(AuthError::input("OpenAI returned an oversized credential."));
        }
        Ok(saved)
    }
}

async fn start_plan(
    State(accounts): State<Accounts>,
    body: Result<Json<Value>, JsonRejection>,
) -> Result<Json<Value>, AuthError> {
    let body = read_object(body)?;
    let store = accounts.0.store.clone();
    let (host, target) = blocking(move || {
        let target = if body.get("providerId").is_some() {
            let id = required(&body, "providerId")?;
            let (origin, revision) = store.account(id)?;
            if !credential::direct_origin(&origin) {
                return Err(AuthError::input(
                    "Select an existing direct OpenAI provider.",
                ));
            }
            Some(Target {
                id: id.to_owned(),
                revision,
            })
        } else {
            None
        };
        Ok((host_id(store.root())?, target))
    })
    .await?;
    accounts
        .begin(
            Flow::Plan {
                target,
                nonce: hex(&random::<32>()?),
            },
            Some(host),
        )
        .map(Json)
}

async fn start_legacy(State(accounts): State<Accounts>) -> Result<Json<Value>, AuthError> {
    accounts.begin(Flow::Legacy, None).map(Json)
}

async fn status(
    State(accounts): State<Accounts>,
    body: Result<Json<Value>, JsonRejection>,
) -> Result<Json<Value>, AuthError> {
    let body = read_object(body)?;
    let id = required(&body, "sessionId")?;
    let sessions = accounts.sessions()?;
    let result = match sessions
        .attempts
        .get(id)
        .filter(|attempt| matches!(attempt.login.flow, Flow::Plan { .. }))
    {
        None => json!({"phase":"expired"}),
        Some(attempt) => match &attempt.phase {
            Phase::Pending | Phase::Exchanging => json!({"phase":"pending"}),
            Phase::Ready(id) => json!({"phase":"ready", "credentialId":id}),
            Phase::Failed(message) => json!({"phase":"failed", "error":message}),
        },
    };
    Ok(Json(result))
}

async fn callback(State(accounts): State<Accounts>, RawQuery(raw): RawQuery) -> Response {
    let result = async {
        let query = raw
            .filter(|query| query.len() <= MAX_CALLBACK_BYTES)
            .ok_or_else(sign_in_error)?;
        let mut url = accounts.0.callback.clone();
        url.set_query(Some(&query));
        let state = parameter(&url, "state")?.ok_or_else(sign_in_error)?;
        let (id, login) = accounts.claim(None, &state)?;
        accounts.finish(id, login, url).await
    }
    .await;
    if result.is_ok() {
        (
            StatusCode::OK,
            "ChatGPT sign-in complete. Return to OpenBot to save the account.",
        )
            .into_response()
    } else {
        (
            StatusCode::BAD_REQUEST,
            "ChatGPT sign-in did not complete. Return to OpenBot.",
        )
            .into_response()
    }
}

async fn complete_legacy(
    State(accounts): State<Accounts>,
    body: Result<Json<Value>, JsonRejection>,
) -> Result<Json<Value>, AuthError> {
    let body = read_object(body)?;
    let requested = required(&body, "sessionId")?;
    let raw = required(&body, "callbackUrl")?;
    let callback = Url::parse(raw)
        .ok()
        .filter(|url| {
            raw.len() <= MAX_CALLBACK_BYTES
                && url.scheme() == "http"
                && url.host_str() == Some("localhost")
                && url.port() == Some(1455)
                && url.path() == "/auth/callback"
                && url.username().is_empty()
                && url.password().is_none()
                && url.fragment().is_none()
        })
        .ok_or_else(|| {
            AuthError::input("Use the OpenAI callback from http://localhost:1455/auth/callback.")
        })?;
    let state = parameter(&callback, "state")?.ok_or_else(sign_in_error)?;
    let (id, login) = accounts.claim(Some(requested), &state)?;
    let credential = accounts.finish(id, login, callback).await?;
    Ok(Json(
        json!({"ok":true, "credentialId":credential, "expiresIn":LIFETIME.as_secs()}),
    ))
}

async fn save(
    State(accounts): State<Accounts>,
    body: Result<Json<Value>, JsonRejection>,
) -> Result<Json<Value>, AuthError> {
    let body = read_object(body)?;
    blocking(move || {
        let mut sessions = accounts.sessions()?;
        let id = body
            .get("credentialId")
            .map(|_| required(&body, "credentialId"))
            .transpose()?;
        if id.is_some() == body.get("secret").is_some() {
            return Err(AuthError::input(
                "Use one completed sign-in or one credential.",
            ));
        }
        let draft = id
            .map(|id| {
                sessions.drafts.get(id).ok_or_else(|| {
                    AuthError::input("That OpenAI sign-in expired or was already saved.")
                })
            })
            .transpose()?;
        let secret = match draft {
            Some(draft) => draft.secret.as_str(),
            None => required(&body, "secret")?,
        };
        let target = draft.and_then(|draft| draft.target.as_ref());
        let store = &accounts.0.store;
        let reply = match body["kind"].as_str() {
            Some("set-secret") => {
                let provider = required(&body, "providerId")?;
                if draft.is_some() && target.is_none_or(|target| target.id != provider) {
                    return Err(AuthError::input(
                        "Save the login under the provider that started sign-in.",
                    ));
                }
                store.save_secret(provider, secret, target.map(|target| &target.revision))?
            }
            Some("add-provider") if body["familyId"] == "openai" && target.is_none() => {
                let name = required(&body, "name")?;
                if name.chars().count() > 128 {
                    return Err(AuthError::input(
                        "An account name can have up to 128 characters.",
                    ));
                }
                let origin = required(&body, "origin")?;
                if !credential::openai_origin(origin) {
                    return Err(AuthError::input(
                        "OpenAI accounts require a direct OpenAI endpoint.",
                    ));
                }
                let protocol = match body["apiType"].as_str().unwrap_or("chat-completions") {
                    protocol @ ("chat-completions" | "responses" | "anthropic") => protocol,
                    _ => return Err(AuthError::input("Select a supported API protocol.")),
                };
                store.add_openai(name, origin, protocol, secret)?
            }
            _ => {
                return Err(AuthError::input(
                    "Use an OpenAI account save on this listener.",
                ));
            }
        };
        if let Some(id) = id
            && let Some(draft) = sessions.drafts.remove(id)
        {
            sessions.attempts.remove(&draft.session);
        }
        Ok(reply)
    })
    .await
    .map(Json)
}

fn parameter(url: &Url, name: &str) -> Result<Option<String>, AuthError> {
    let mut items = url.query_pairs().filter(|(key, _)| key == name);
    let value = items.next().map(|(_, value)| value.into_owned());
    if items.next().is_some() || value.as_ref().is_some_and(|value| value.trim().is_empty()) {
        return Err(sign_in_error());
    }
    Ok(value)
}

fn read_object(body: Result<Json<Value>, JsonRejection>) -> Result<Value, AuthError> {
    let Json(body) =
        body.map_err(|err| AuthError::new(err.status(), "Provide an OpenAI sign-in JSON object."))?;
    if !body.is_object() {
        return Err(AuthError::input("Provide an OpenAI sign-in JSON object."));
    }
    Ok(body)
}

fn required<'a>(body: &'a Value, key: &str) -> Result<&'a str, AuthError> {
    credential::text(&body[key]).ok_or_else(|| AuthError::input(format!("{key} is required.")))
}

fn host_id(root: &Path) -> Result<String, AuthError> {
    let path = root.join("openbot-chatgpt-host-id");
    if !path.exists() {
        let mut draft = tempfile::NamedTempFile::new_in(root).map_err(|_| sign_in_error())?;
        writeln!(draft, "urn:uuid:{}", unique_id()?).map_err(|_| sign_in_error())?;
        draft.as_file().sync_all().map_err(|_| sign_in_error())?;
        match draft.persist_noclobber(&path) {
            Ok(_) => {
                #[cfg(unix)]
                File::open(root)
                    .and_then(|file| file.sync_all())
                    .map_err(|_| sign_in_error())?;
            }
            Err(error) if error.error.kind() == io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err(sign_in_error()),
        }
    }
    let mut stored = String::new();
    File::open(path)
        .and_then(|file| file.take(65).read_to_string(&mut stored))
        .map_err(|_| sign_in_error())?;
    let stored = stored.trim();
    if !stored.strip_prefix("urn:uuid:").is_some_and(|id| {
        id.len() == 36
            && id.bytes().enumerate().all(|(offset, byte)| {
                if matches!(offset, 8 | 13 | 18 | 23) {
                    byte == b'-'
                } else {
                    byte.is_ascii_hexdigit()
                }
            })
    }) {
        return Err(AuthError::input(
            "The saved OpenBot host identity is invalid.",
        ));
    }
    Ok(stored.into())
}

fn sign_in_error() -> AuthError {
    AuthError::input("OpenAI sign-in could not be completed. Start again.")
}
