mod models;

use std::{io, time::Duration};

use axum::{
    Json, Router,
    extract::{Path, State as AxumState},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::post,
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use url::Url;

use crate::{
    auth::{AuthError, Authorized, CODEX_USER_AGENT, CredentialStore},
    catalog::Provider,
    routing::{CredentialKind, direct_go, parse_origin},
    server::State,
};

const MAX_REPLY_BYTES: usize = 8 * 1024 * 1024;
const GO_LIST: &str = "https://opencode.ai/inference/go/v1/models";
const OPENAI_LIST: &str = "https://api.openai.com/v1/models";
const CODEX_LIST: &str = "https://chatgpt.com/backend-api/codex/models?client_version=0.153.3";

#[derive(Clone)]
pub struct Discovery {
    state: State,
    credentials: CredentialStore,
    client: reqwest::Client,
}

impl Discovery {
    pub fn new(state: State, credentials: CredentialStore) -> io::Result<Self> {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(io::Error::other)?;
        Ok(Self {
            state,
            credentials,
            client,
        })
    }

    pub fn routes(self) -> Router {
        Router::new()
            .route("/api/providers/{id}/fetch-models", post(fetch))
            .with_state(self)
    }

    async fn list(&self, provider: &Provider) -> Result<Value, ListError> {
        let origin = parse_origin(&provider.origin).ok_or_else(invalid_origin)?;
        let go = direct_go(&origin);
        if (provider.id == "opencode" || provider.id.starts_with("opencode-")) && !go {
            return Err(ListError::new(
                StatusCode::BAD_REQUEST,
                "not-supported",
                "OpenCode accounts require the direct OpenCode Go endpoint.",
            ));
        }
        let authorized = self
            .credentials
            .authorize(&provider.id, &provider.origin)
            .await
            .map_err(ListError::from_auth)?;
        let plan = authorized.kind == CredentialKind::ChatGptPlan;
        let url = endpoint(origin.clone(), authorized.kind, go)?;
        let response = self
            .client
            .get(url)
            .headers(outbound(&authorized, go, &provider.id)?)
            .send()
            .await
            .map_err(ListError::network)?;
        let raw = decode(response, plan).await?;
        let normalized = models::normalize(&raw, &provider.origin, go, plan).ok_or_else(|| {
            ListError::upstream("parse-error", "Provider response is not a model list.")
        })?;
        let fetched_at = OffsetDateTime::now_utc().format(&Rfc3339).map_err(|_| {
            ListError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal",
                "OpenBot could not format the fetch time.",
            )
        })?;
        Ok(json!({
            "ok":true, "providerId":provider.id, "source":"provider",
            "fetchedAt":fetched_at, "models":normalized.models,
            "skipped":normalized.skipped.len(), "skippedReasons":normalized.skipped
        }))
    }
}

async fn fetch(
    AxumState(discovery): AxumState<Discovery>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ListError> {
    let state = discovery.state.clone();
    let provider = tokio::task::spawn_blocking(move || {
        state.catalog().map(|catalog| {
            catalog
                .providers
                .into_iter()
                .find(|provider| provider.id == id)
        })
    })
    .await
    .ok()
    .and_then(Result::ok)
    .ok_or_else(|| {
        ListError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "internal",
            "OpenBot could not read the provider catalog.",
        )
    })?
    .ok_or_else(|| {
        ListError::new(
            StatusCode::NOT_FOUND,
            "provider-not-found",
            "Provider not found.",
        )
    })?;
    discovery.list(&provider).await.map(Json)
}

fn endpoint(mut origin: Url, credential: CredentialKind, go: bool) -> Result<Url, ListError> {
    let known = match credential {
        CredentialKind::ChatGptPlan => Some(OPENAI_LIST),
        CredentialKind::OpenAiOauth => Some(CODEX_LIST),
        CredentialKind::ApiKey if go => Some(GO_LIST),
        CredentialKind::ApiKey => None,
    };
    if let Some(known) = known {
        return Url::parse(known).map_err(|_| invalid_origin());
    }
    let path = origin.path().trim_end_matches('/');
    let lower = path.to_ascii_lowercase();
    let path = if origin.scheme() == "https"
        && origin.port().is_none()
        && origin.host_str() == Some("api.deepseek.com")
        && matches!(
            path,
            "" | "/v1"
                | "/v1/chat/completions"
                | "/v1/responses"
                | "/chat/completions"
                | "/responses"
                | "/anthropic"
                | "/anthropic/v1"
                | "/anthropic/v1/messages"
        ) {
        String::from("/v1/models")
    } else if ["/v1", "/v4", "/chat/completions"]
        .iter()
        .any(|suffix| lower.ends_with(suffix))
    {
        format!("{path}/models")
    } else {
        format!("{path}/v1/models")
    };
    origin.set_path(&path);
    Ok(origin)
}

fn outbound(authorized: &Authorized, go: bool, id: &str) -> Result<HeaderMap, ListError> {
    let mut headers = HeaderMap::new();
    let mut bearer =
        HeaderValue::from_str(&format!("Bearer {}", authorized.token)).map_err(|_| {
            ListError::new(
                StatusCode::UNAUTHORIZED,
                "unauthorized",
                "The saved provider credential cannot be sent as a header.",
            )
        })?;
    bearer.set_sensitive(true);
    headers.insert(header::AUTHORIZATION, bearer);
    headers.insert(header::ACCEPT, HeaderValue::from_static("application/json"));
    if authorized.kind == CredentialKind::OpenAiOauth {
        headers.insert(
            header::USER_AGENT,
            HeaderValue::from_static(CODEX_USER_AGENT),
        );
        headers.insert("originator", HeaderValue::from_static("codex-tui"));
        if let Some(account) = authorized
            .account
            .as_deref()
            .and_then(|value| HeaderValue::from_str(value).ok())
        {
            headers.insert("chatgpt-account-id", account);
        }
    }
    if go {
        let session = format!(
            "{:x}",
            Sha256::digest(format!("openbot-opencode-models\0{id}"))
        );
        headers.insert(
            header::USER_AGENT,
            HeaderValue::from_static("OpenBot/catalog"),
        );
        if let Ok(session) = HeaderValue::from_str(&session[..32]) {
            headers.insert("x-opencode-session", session);
        }
    }
    Ok(headers)
}

async fn decode(mut response: reqwest::Response, plan: bool) -> Result<Value, ListError> {
    let status = response.status();
    if !status.is_success() {
        return Err(ListError::from_upstream(status, plan));
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(ListError::network)? {
        if bytes.len() + chunk.len() > MAX_REPLY_BYTES {
            return Err(ListError::upstream(
                "parse-error",
                "Provider returned an oversized model list.",
            ));
        }
        bytes.extend_from_slice(&chunk);
    }
    serde_json::from_slice(&bytes)
        .map_err(|_| ListError::upstream("parse-error", "Provider returned an invalid model list."))
}

#[derive(Debug)]
struct ListError {
    status: StatusCode,
    kind: &'static str,
    message: String,
    upstream: Option<u16>,
}

impl ListError {
    fn new(status: StatusCode, kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            status,
            kind,
            message: message.into(),
            upstream: None,
        }
    }

    fn upstream(kind: &'static str, message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_GATEWAY, kind, message)
    }

    fn from_auth(error: AuthError) -> Self {
        let mut result = match error.status {
            StatusCode::NOT_FOUND => Self::new(StatusCode::CONFLICT, "no-secret", error.message),
            _ => Self::new(StatusCode::UNAUTHORIZED, "unauthorized", error.message),
        };
        result.upstream = error.upstream;
        result
    }

    fn from_upstream(status: StatusCode, plan: bool) -> Self {
        let mut error = match status.as_u16() {
            401 | 403 => Self::new(
                StatusCode::UNAUTHORIZED,
                "unauthorized",
                if plan {
                    "OpenAI rejected the ChatGPT login. Sign in again."
                } else {
                    "Provider rejected the credential."
                },
            ),
            404 | 405 | 501 => {
                Self::upstream("not-supported", "Provider does not expose a model list.")
            }
            status => Self::upstream("unreachable", format!("Provider returned HTTP {status}.")),
        };
        error.upstream = Some(status.as_u16());
        error
    }

    fn network(error: reqwest::Error) -> Self {
        Self::upstream(
            "unreachable",
            if error.is_timeout() {
                "Provider model request timed out."
            } else {
                "OpenBot could not reach the provider model list."
            },
        )
    }
}

impl IntoResponse for ListError {
    fn into_response(self) -> Response {
        let mut body = json!({"error":{"kind":self.kind,"message":self.message}});
        if let Some(status) = self.upstream {
            body["error"]["upstreamStatus"] = json!(status);
        }
        (self.status, Json(body)).into_response()
    }
}

fn invalid_origin() -> ListError {
    ListError::new(
        StatusCode::BAD_REQUEST,
        "not-supported",
        "Provider origin is invalid.",
    )
}

#[cfg(test)]
mod tests;
