use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use url::Url;

use super::{AuthError, unix_seconds};
use crate::routing::CredentialKind;

pub const RESOURCE: &str = "https://api.openai.com/v1";
pub const REQUIRED_SCOPES: [&str; 2] = ["resource.invoke", "chatgpt.tokens.use.direct"];
pub const CODEX_CLIENT: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
pub const MAX_CREDENTIAL_BYTES: usize = 256 * 1024;

pub enum Credential {
    Key(String),
    OAuth(OAuth),
}

pub struct OAuth {
    pub kind: CredentialKind,
    pub record: Value,
}

#[derive(Clone, Eq, PartialEq)]
pub enum AccountRevision {
    Missing,
    Plan { client_id: String, subject: String },
    Other([u8; 32]),
}

impl AccountRevision {
    pub fn of(raw: Option<&str>) -> Self {
        let Some(raw) = raw else { return Self::Missing };
        if let Ok(Credential::OAuth(oauth)) = Credential::parse(raw)
            && oauth.kind == CredentialKind::ChatGptPlan
        {
            return Self::Plan {
                client_id: text(&oauth.record["clientId"]).unwrap_or_default().into(),
                subject: text(&oauth.record["subject"]).unwrap_or_default().into(),
            };
        }
        Self::Other(Sha256::digest(raw.as_bytes()).into())
    }
}

impl Credential {
    pub fn parse(raw: &str) -> Result<Self, AuthError> {
        if raw.len() > MAX_CREDENTIAL_BYTES {
            return Err(AuthError::input("Credential exceeds 256 KiB."));
        }
        let raw = raw.trim();
        if raw.is_empty() {
            return Err(AuthError::input("Credential is empty."));
        }
        if !raw.starts_with('{') {
            return Ok(Self::Key(raw.into()));
        }
        let record: Value = serde_json::from_str(raw)
            .map_err(|_| AuthError::input("The saved provider credential is invalid."))?;
        let kind = match record["kind"].as_str() {
            Some("openai-oauth") => CredentialKind::OpenAiOauth,
            Some("chatgpt-plan") => CredentialKind::ChatGptPlan,
            _ => {
                return Err(AuthError::input(
                    "The saved provider credential is unsupported.",
                ));
            }
        };
        Ok(Self::OAuth(OAuth::checked(kind, record)?))
    }

    pub fn import(raw: &str, origin: &str) -> Result<Self, AuthError> {
        if raw.len() > MAX_CREDENTIAL_BYTES {
            return Err(AuthError::input("Credential exceeds 256 KiB."));
        }
        let raw = raw.trim();
        if !raw.starts_with('{') {
            return Self::parse(raw);
        }
        let record: Value = serde_json::from_str(raw)
            .map_err(|_| AuthError::input("Credential file is not valid JSON."))?;
        if matches!(
            record["kind"].as_str(),
            Some("chatgpt-plan" | "openai-oauth")
        ) {
            let Self::OAuth(oauth) = Self::parse(raw)? else {
                return Err(AuthError::input(
                    "The saved provider credential is invalid.",
                ));
            };
            if text(&oauth.record["refreshToken"]).is_none() {
                return Err(AuthError::input(
                    "Select an OpenAI login with a refresh token.",
                ));
            }
            oauth.check_origin(origin)?;
            return Ok(Self::OAuth(oauth));
        }
        if record["type"] == "api"
            && let Some(key) = text(&record["key"])
        {
            return Ok(Self::Key(key.into()));
        }
        if !record["tokens"].is_object()
            && let Some(key) = text(&record["OPENAI_API_KEY"])
        {
            return Ok(Self::Key(key.into()));
        }
        let tokens = if record["tokens"].is_object() {
            &record["tokens"]
        } else {
            &record
        };
        let select = |names: &[&str]| names.iter().find_map(|name| text(&tokens[*name]));
        let Some(access) = select(&["access_token", "accessToken", "access"]) else {
            return Err(AuthError::input(
                "Select an API key or an OpenAI login with access and refresh tokens.",
            ));
        };
        let refresh = select(&["refresh_token", "refreshToken", "refresh"])
            .ok_or_else(|| AuthError::input("Select an OpenAI login with a refresh token."))?;
        let raw_expiry = ["expiresAt", "expires_at", "expires"]
            .iter()
            .find_map(|name| tokens[*name].as_f64())
            .or_else(|| jwt_claims(access).and_then(|claims| claims["exp"].as_f64()))
            .unwrap_or_default();
        let expiry = if raw_expiry > 1e11 {
            raw_expiry / 1000.0
        } else {
            raw_expiry
        };
        let mut saved = json!({"kind":"openai-oauth","accessToken":access,"refreshToken":refresh,
            "expiresAt":expiry.max(0.0) as u64});
        let id_token = select(&["id_token", "idToken"]).unwrap_or_default();
        if let Some(account) = select(&["account_id", "accountId", "chatgptAccountId"])
            .map(str::to_owned)
            .or_else(|| account_id(id_token, access))
        {
            saved["chatgptAccountId"] = json!(account);
        }
        let oauth = OAuth::checked(CredentialKind::OpenAiOauth, saved)?;
        oauth.check_origin(origin)?;
        Ok(Self::OAuth(oauth))
    }

    pub fn into_saved(self) -> String {
        match self {
            Self::Key(value) => value,
            Self::OAuth(value) => value.record.to_string(),
        }
    }
}

impl OAuth {
    pub fn checked(kind: CredentialKind, record: Value) -> Result<Self, AuthError> {
        if text(&record["accessToken"]).is_none() {
            return Err(AuthError::input("The saved OpenAI sign-in is incomplete."));
        }
        if kind == CredentialKind::ChatGptPlan
            && (!client_id(&record["clientId"])
                || !has_plan_scopes(&record["scopes"])
                || ["subject", "idToken", "refreshToken"]
                    .iter()
                    .any(|key| text(&record[*key]).is_none()))
        {
            return Err(AuthError::input(
                "The saved ChatGPT plan sign-in is incomplete.",
            ));
        }
        Ok(Self { kind, record })
    }

    pub fn check_origin(&self, origin: &str) -> Result<(), AuthError> {
        let allowed = if self.kind == CredentialKind::ChatGptPlan {
            direct_origin(origin)
        } else {
            openai_origin(origin)
        };
        if !allowed {
            return Err(AuthError::input(
                "OpenAI sign-in requires a direct OpenAI endpoint.",
            ));
        }
        Ok(())
    }

    pub fn access(&self) -> &str {
        text(&self.record["accessToken"]).unwrap_or_default()
    }

    pub fn needs_refresh(&self, now: u64) -> bool {
        let expiry = seconds(&self.record["expiresAt"]).unwrap_or(0);
        let earliest = seconds(&self.record["earliestRefreshAt"]).unwrap_or(0);
        expiry <= now || (expiry <= now.saturating_add(60) && earliest <= now)
    }

    pub fn account(&self) -> Option<String> {
        (self.kind == CredentialKind::OpenAiOauth)
            .then(|| {
                text(&self.record["chatgptAccountId"])
                    .map(str::to_owned)
                    .or_else(|| {
                        account_id(
                            text(&self.record["idToken"]).unwrap_or_default(),
                            self.access(),
                        )
                    })
            })
            .flatten()
    }

    pub fn refreshed(mut self, token: &Value) -> Result<Self, AuthError> {
        let access = text(&token["access_token"]).ok_or_else(|| {
            AuthError::unavailable("OpenAI returned no access token. Sign in again.")
        })?;
        if self.kind == CredentialKind::ChatGptPlan {
            let refresh = text(&token["refresh_token"]).ok_or_else(|| {
                AuthError::unavailable(
                    "OpenAI returned no replacement refresh token. Sign in again.",
                )
            })?;
            self.record["refreshToken"] = json!(refresh);
            if let Some(scope) = token.get("scope") {
                let scopes = scope_array(scope);
                if !has_plan_scopes(&scopes) {
                    return Err(AuthError::unavailable(
                        "ChatGPT plan usage permission was lost. Sign in again.",
                    ));
                }
                self.record["scopes"] = scopes;
            }
            if let Some(subject) =
                jwt_claims(access).and_then(|claim| text(&claim["sub"]).map(str::to_owned))
                && self.record["subject"] != subject
            {
                return Err(AuthError::conflict(
                    "The refreshed ChatGPT account differs from the saved account.",
                ));
            }
        } else {
            if let Some(refresh) = text(&token["refresh_token"]) {
                self.record["refreshToken"] = json!(refresh);
            }
            if let Some(account) = account_id(text(&token["id_token"]).unwrap_or_default(), access)
            {
                if let Some(previous) = self.account()
                    && previous != account
                {
                    return Err(AuthError::conflict(
                        "The refreshed OpenAI account differs from the saved account.",
                    ));
                }
                self.record["chatgptAccountId"] = json!(account);
            }
        }
        self.record["accessToken"] = json!(access);
        self.record["expiresAt"] = json!(expiry(token, self.kind)?);
        update_earliest(&mut self.record, token);
        Ok(self)
    }
}

pub fn direct_origin(origin: &str) -> bool {
    matches!(
        origin.trim_end_matches('/'),
        "https://api.openai.com" | RESOURCE
    )
}

pub fn openai_origin(origin: &str) -> bool {
    Url::parse(origin).is_ok_and(|url| {
        url.scheme() == "https"
            && url.port_or_known_default() == Some(443)
            && url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none()
            && (url.host_str() == Some("api.openai.com")
                || (url.host_str() == Some("chatgpt.com")
                    && (url.path() == "/backend-api/codex"
                        || url.path().starts_with("/backend-api/codex/"))))
    })
}

pub fn client_id(value: &Value) -> bool {
    text(value).is_some_and(|value| {
        value.len() <= 256
            && value.strip_prefix("oaiapp_").is_some_and(|suffix| {
                !suffix.is_empty()
                    && suffix
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
            })
    })
}

pub fn has_plan_scopes(value: &Value) -> bool {
    value.as_array().is_some_and(|scopes| {
        scopes.iter().all(Value::is_string)
            && REQUIRED_SCOPES
                .iter()
                .all(|scope| scopes.iter().any(|value| value == *scope))
    })
}

pub fn scope_array(value: &Value) -> Value {
    json!(
        value
            .as_str()
            .unwrap_or_default()
            .split_whitespace()
            .collect::<Vec<_>>()
    )
}

pub fn text(value: &Value) -> Option<&str> {
    value
        .as_str()
        .map(str::trim)
        .filter(|text| !text.is_empty())
}

pub fn seconds(value: &Value) -> Option<u64> {
    value
        .as_f64()
        .filter(|n| n.is_finite() && *n >= 0.0 && *n < u64::MAX as f64)
        .map(|n| n as u64)
}

pub fn expiry(payload: &Value, kind: CredentialKind) -> Result<u64, AuthError> {
    let lifetime = seconds(&payload["expires_in"]).filter(|seconds| *seconds > 0);
    let lifetime = if kind == CredentialKind::ChatGptPlan {
        lifetime.ok_or_else(|| AuthError::input("OpenAI returned no valid token expiry."))?
    } else {
        lifetime.unwrap_or(3600)
    };
    Ok(unix_seconds().saturating_add(lifetime))
}

pub fn update_earliest(record: &mut Value, payload: &Value) {
    if let Some(earliest) = seconds(&payload["earliest_refresh_at"]) {
        record["earliestRefreshAt"] = json!(earliest);
    } else if let Some(record) = record.as_object_mut() {
        record.shift_remove("earliestRefreshAt");
    }
}

fn jwt_claims(token: &str) -> Option<Value> {
    if token.len() > 32_768 {
        return None;
    }
    let claim = token.split('.').nth(1)?;
    serde_json::from_slice(&URL_SAFE_NO_PAD.decode(claim).ok()?).ok()
}

pub fn account_id(id: &str, access: &str) -> Option<String> {
    [id, access].iter().find_map(|token| {
        let claims = jwt_claims(token)?;
        text(&claims["https://api.openai.com/auth"]["chatgpt_account_id"]).map(str::to_owned)
    })
}
