use std::{
    io,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

use axum::{
    body::Bytes,
    http::{HeaderMap, HeaderValue, StatusCode, header},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use url::Url;

use crate::{
    catalog::Protocol,
    reasoning,
    routing::{self, CredentialKind},
    server::{State, read_json},
};

use super::failure::Failure;

pub const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;

pub struct Prepared {
    pub url: String,
    pub protocol: Protocol,
    pub model: String,
    pub body: Bytes,
    pub headers: HeaderMap,
    pub secret: String,
    pub upstream_stream: bool,
    pub client_stream: bool,
}

const CONVERSATION_FIELDS: &[&str] = &[
    "conversationId",
    "conversation_id",
    "sessionId",
    "session_id",
    "chatId",
    "chat_id",
    "openbotConversationId",
];
const INTERNAL_FIELDS: &[&str] = &[
    "__openbot_api_type",
    "openbot_internal_model",
    "openbot_internal_auxiliary",
    "openbotRequestId",
    "openbotBotId",
    "openbotChatType",
    "openbotEpochId",
    "epochId",
    "epoch_id",
];

impl Prepared {
    pub fn load(
        state: &State,
        body: Value,
        incoming: &HeaderMap,
        version: &str,
    ) -> Result<Self, Failure> {
        let root = state.root();
        if !custom_mode(root).map_err(|_| config_failure())? {
            return Err(Failure::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "official_mode",
                "OpenBot uses the stock model in Official mode.",
            ));
        }
        let Value::Object(mut body) = body else {
            return Err(Failure::new(
                StatusCode::BAD_REQUEST,
                "invalid_request",
                "Chat request must be an object.",
            ));
        };
        let bot = bot_id(&body).map(str::to_owned);
        check_paused(root, bot.as_deref())?;
        let requested = body
            .get("model")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                Failure::new(
                    StatusCode::BAD_REQUEST,
                    "invalid_request",
                    "Select a model for the request.",
                )
            })?;
        let selected_bot = bot
            .as_deref()
            .filter(|_| body.get("openbot_internal_model") != Some(&Value::Bool(true)));
        let (catalog, settings) = state.saved().map_err(|_| config_failure())?;
        let selection =
            routing::select(&catalog, &settings, requested, selected_bot).map_err(|err| {
                Failure::new(StatusCode::BAD_REQUEST, "invalid_model", &err.to_string())
            })?;
        let credential =
            Credential::read(root, &selection.provider.id, &selection.provider.origin)?;
        let route = selection.route(credential.kind);
        let client_stream = body.get("stream") == Some(&Value::Bool(true));
        let conversation = CONVERSATION_FIELDS.iter().find_map(|key| {
            body.get(*key)?
                .as_str()
                .filter(|value| !value.is_empty() && value.len() <= 128)
        });
        let has_conversation = conversation.is_some();
        let scope = scope(
            conversation.or(bot.as_deref()),
            body.get("openbot_internal_auxiliary") == Some(&Value::Bool(true)),
        );
        let session_key = scope
            .as_deref()
            .map(|scope| stable_cache_key(&selection.provider.id, scope));

        for key in CONVERSATION_FIELDS.iter().chain(INTERNAL_FIELDS) {
            body.shift_remove(*key);
        }
        reasoning::apply(&mut body, &selection, route.protocol);
        let mut outbound = route
            .encode_request(Value::Object(body), credential.kind)
            .map_err(|err| Failure::new(StatusCode::BAD_REQUEST, "invalid_request", err))?;
        if credential.kind == CredentialKind::ChatGptPlan {
            outbound["store"] = json!(false);
            outbound["stream"] = json!(true);
        }

        let host = Url::parse(&route.upstream)
            .ok()
            .and_then(|url| url.host_str().map(str::to_owned));
        let provider = selection.provider.id.as_str();
        let go = provider == "opencode"
            || provider.starts_with("opencode-")
            || host.as_deref() == Some("opencode.ai");
        if let Some(key) = &session_key
            && (credential.kind == CredentialKind::OpenAiOauth
                || host.as_deref() == Some("api.openai.com")
                || (go && route.protocol == Protocol::Responses))
        {
            outbound["prompt_cache_key"] = json!(key);
        }

        let upcoming_stream = outbound["stream"] == true;
        let info = Metadata {
            provider,
            go,
            session_key: session_key.as_deref(),
            conversation_scope: scope.as_deref().filter(|_| has_conversation),
            stream: upcoming_stream,
        };
        let headers = credential.headers(incoming, version, route.protocol, info)?;
        let payload = serde_json::to_vec(&outbound).map_err(|_| config_failure())?;
        if payload.len() > MAX_REQUEST_BYTES {
            return Err(Failure::new(
                StatusCode::PAYLOAD_TOO_LARGE,
                "request_too_large",
                "Encoded inference request is too large.",
            ));
        }
        Ok(Self {
            url: route.upstream,
            protocol: route.protocol,
            model: selection.model.slug.clone(),
            body: Bytes::from(payload),
            headers,
            secret: credential.token,
            upstream_stream: upcoming_stream,
            client_stream,
        })
    }
}

fn custom_mode(root: &Path) -> io::Result<bool> {
    use std::io::Read;
    let file = match std::fs::File::open(root.join("openbot-mode")) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    let mut contents = String::new();
    file.take(65).read_to_string(&mut contents)?;
    Ok(contents.len() <= 64 && contents.trim() == "custom")
}

fn check_paused(root: &Path, bot: Option<&str>) -> Result<(), Failure> {
    if read_json(&root.join("openbot-pause.json"), 4 * 1024)
        .is_ok_and(|value| value.is_some_and(|row| row["paused"] == true))
    {
        return Err(Failure::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "paused",
            "OpenBot gateway is paused.",
        ));
    }
    if let Some(bot) = bot
        && read_json(&root.join("openbot-pause-bots.json"), 1024 * 1024).is_ok_and(|value| {
            value.is_some_and(|row| {
                row["pausedBotIds"]
                    .as_array()
                    .is_some_and(|bots| bots.iter().any(|entry| entry == bot))
            })
        })
    {
        return Err(Failure::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "bot_paused",
            "OpenBot bot is paused.",
        ));
    }
    Ok(())
}

fn config_failure() -> Failure {
    Failure::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "unavailable_settings",
        "OpenBot could not read the provider settings.",
    )
}

fn bot_id(body: &Map<String, Value>) -> Option<&str> {
    body.get("openbotBotId")
        .and_then(Value::as_str)
        .filter(|id| {
            !id.is_empty()
                && id.len() <= 128
                && id
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        })
        .or_else(|| {
            body.get("messages")?
                .as_array()?
                .iter()
                .take(5)
                .find_map(|message| profile_id(&message["content"]))
        })
}

fn profile_id(content: &Value) -> Option<&str> {
    if let Some(text) = content.as_str() {
        return text
            .split("/home/box/agent-data/agents/")
            .skip(1)
            .find_map(|tail| {
                let (id, rest) = tail.split_at_checked(36)?;
                (rest.starts_with("/profile.json")
                    && id.bytes().enumerate().all(|(offset, byte)| {
                        if matches!(offset, 8 | 13 | 18 | 23) {
                            byte == b'-'
                        } else {
                            byte.is_ascii_hexdigit()
                        }
                    }))
                .then_some(id)
            });
    }
    if let Some(parts) = content.as_array() {
        return parts.iter().find_map(profile_id);
    }
    profile_id(content.get("text").or_else(|| content.get("content"))?)
}

fn scope(scope: Option<&str>, auxiliary: bool) -> Option<String> {
    scope.map(|scope| {
        if auxiliary {
            format!("{scope}\0auxiliary")
        } else {
            scope.into()
        }
    })
}

fn stable_cache_key(provider: &str, scope: &str) -> String {
    let encoded = format!(
        "{:x}",
        Sha256::digest(format!("openbot-prompt-cache\0{provider}\0{scope}"))
    );
    encoded[..40].to_owned()
}

fn opencode_session(provider: &str, scope: &str) -> String {
    let mut bytes = Sha256::digest(format!("openbot-opencode-session\0{provider}\n{scope}"));
    bytes[6] = (bytes[6] & 15) | 0x50;
    bytes[8] = (bytes[8] & 63) | 0x80;
    let encoded: String = bytes[..16]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    format!(
        "{}-{}-{}-{}-{}",
        &encoded[..8],
        &encoded[8..12],
        &encoded[12..16],
        &encoded[16..20],
        &encoded[20..]
    )
}

struct Credential {
    kind: CredentialKind,
    token: String,
    account: Option<String>,
}

struct Metadata<'a> {
    provider: &'a str,
    go: bool,
    session_key: Option<&'a str>,
    conversation_scope: Option<&'a str>,
    stream: bool,
}

impl Credential {
    fn read(root: &Path, provider: &str, origin: &str) -> Result<Self, Failure> {
        let store = read_json(&root.join("secrets.json"), 4 * 1024 * 1024)
            .map_err(|_| config_failure())?
            .unwrap_or_default();
        let raw = store["providers"][provider]
            .as_str()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                Failure::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "missing_credential",
                    "No credential for this provider.",
                )
            })?;
        if raw.starts_with('{') {
            let record = serde_json::from_str::<Value>(raw)
                .map_err(|_| sign_in("The saved provider credential is invalid."))?;
            match record["kind"].as_str() {
                Some("openai-oauth") => {
                    return Self::oauth(record, CredentialKind::OpenAiOauth, origin);
                }
                Some("chatgpt-plan") => {
                    return Self::oauth(record, CredentialKind::ChatGptPlan, origin);
                }
                _ => return Err(sign_in("The saved provider credential is unsupported.")),
            }
        }
        Ok(Self {
            kind: CredentialKind::ApiKey,
            token: raw.into(),
            account: None,
        })
    }

    fn oauth(record: Value, kind: CredentialKind, origin: &str) -> Result<Self, Failure> {
        if kind == CredentialKind::ChatGptPlan {
            if !matches!(
                origin.trim_end_matches('/'),
                "https://api.openai.com" | "https://api.openai.com/v1"
            ) {
                return Err(sign_in(
                    "ChatGPT plan sign-in requires the direct OpenAI API.",
                ));
            }
            let valid_client = record["clientId"].as_str().is_some_and(|client| {
                client.len() <= 256
                    && client.strip_prefix("oaiapp_").is_some_and(|suffix| {
                        !suffix.is_empty()
                            && suffix.bytes().all(|byte| {
                                byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_')
                            })
                    })
            });
            let valid_scopes = record["scopes"].as_array().is_some_and(|scopes| {
                ["resource.invoke", "chatgpt.tokens.use.direct"]
                    .iter()
                    .all(|scope| scopes.iter().any(|value| value == *scope))
            });
            if !valid_client
                || !valid_scopes
                || ["subject", "idToken", "refreshToken"].iter().any(|key| {
                    record[key]
                        .as_str()
                        .is_none_or(|value| value.trim().is_empty())
                })
            {
                return Err(sign_in("The saved ChatGPT plan sign-in is incomplete."));
            }
        }
        let token = record["accessToken"]
            .as_str()
            .map(str::trim)
            .filter(|token| !token.is_empty())
            .ok_or_else(|| sign_in("The saved OpenAI sign-in is incomplete."))?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        if record["expiresAt"].as_u64().unwrap_or(0) <= now.saturating_add(60) {
            return Err(sign_in(
                "The saved OpenAI sign-in has expired. Renew it in the current panel.",
            ));
        }
        let account = (kind == CredentialKind::OpenAiOauth)
            .then(|| account_id(&record))
            .flatten();
        Ok(Self {
            kind,
            token: token.into(),
            account,
        })
    }

    fn headers(
        &self,
        incoming: &HeaderMap,
        version: &str,
        protocol: Protocol,
        info: Metadata<'_>,
    ) -> Result<HeaderMap, Failure> {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/json"),
        );
        headers.insert(
            header::ACCEPT_ENCODING,
            HeaderValue::from_static("identity"),
        );
        headers.insert(
            header::ACCEPT,
            HeaderValue::from_static(if info.stream {
                "text/event-stream, application/json"
            } else {
                "application/json"
            }),
        );
        let version_header =
            HeaderValue::from_str(version).unwrap_or_else(|_| HeaderValue::from_static("unknown"));
        headers.insert("x-openbot-version", version_header);
        let secret = if protocol == Protocol::Anthropic {
            headers.insert("anthropic-version", HeaderValue::from_static("2023-06-01"));
            ("x-api-key", self.token.clone())
        } else {
            ("authorization", format!("Bearer {}", self.token))
        };
        let mut value = HeaderValue::from_str(&secret.1).map_err(|_| {
            sign_in("The saved provider credential cannot be sent as a request header.")
        })?;
        value.set_sensitive(true);
        headers.insert(secret.0, value);
        if self.kind == CredentialKind::OpenAiOauth {
            // These headers belong to the existing OAuth client contract.
            headers.insert(header::USER_AGENT, HeaderValue::from_static(
                "codex-tui/0.153.3 (Mac OS 26.5.1; arm64) iTerm.app/3.6.11 (codex-tui; 0.153.3)"
            ));
            headers.insert("originator", HeaderValue::from_static("codex-tui"));
            if let Some(account) = self
                .account
                .as_deref()
                .and_then(|account| HeaderValue::from_str(account).ok())
            {
                headers.insert("chatgpt-account-id", account);
            }
            if let Some(session) = info
                .session_key
                .and_then(|session| HeaderValue::from_str(session).ok())
            {
                headers.insert("session-id", session);
            }
        } else if let Some(agent) = incoming
            .get(header::USER_AGENT)
            .filter(|agent| agent.to_str().is_ok_and(|value| !value.trim().is_empty()))
        {
            headers.insert(header::USER_AGENT, agent.clone());
        } else if info.go {
            let version: String = version
                .chars()
                .take(128)
                .map(|character| {
                    if character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '-') {
                        character
                    } else {
                        '_'
                    }
                })
                .collect();
            if let Ok(agent) = HeaderValue::from_str(&format!("OpenBot/{version}")) {
                headers.insert(header::USER_AGENT, agent);
            }
        }
        if info.go {
            let id = if let Some(scope) = info.conversation_scope {
                opencode_session(info.provider, scope)
            } else if let Some(session) = info.session_key {
                session.into()
            } else if info.provider.starts_with("opencode") {
                stable_cache_key(info.provider, info.provider)
            } else {
                opencode_session(info.provider, "auxiliary")
            };
            if let Ok(id) = HeaderValue::from_str(&id) {
                headers.insert("x-opencode-session", id);
            }
        }
        if info.provider == "openrouter" {
            headers.insert(
                "http-referer",
                HeaderValue::from_static("https://openbot.local"),
            );
            headers.insert("x-title", HeaderValue::from_static("OpenBot"));
        }
        Ok(headers)
    }
}

fn account_id(record: &Value) -> Option<String> {
    if let Some(account) = record["chatgptAccountId"]
        .as_str()
        .map(str::trim)
        .filter(|account| !account.is_empty())
    {
        return Some(account.into());
    }
    ["idToken", "accessToken"].iter().find_map(|key| {
        let payload = record[key].as_str()?.split('.').nth(1)?;
        let claims: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(payload).ok()?).ok()?;
        Some(
            claims["https://api.openai.com/auth"]["chatgpt_account_id"]
                .as_str()?
                .to_owned(),
        )
    })
}

fn sign_in(message: &str) -> Failure {
    Failure::new(
        StatusCode::SERVICE_UNAVAILABLE,
        "provider_sign_in_required",
        message,
    )
}
