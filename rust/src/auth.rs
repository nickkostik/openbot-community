mod credential;
mod network;
mod sessions;
mod store;

use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use ring::rand::{SecureRandom, SystemRandom};
use serde_json::json;
use std::time::{SystemTime, UNIX_EPOCH};

pub use sessions::Accounts;
pub use store::{Authorized, CredentialStore};

pub const CODEX_USER_AGENT: &str =
    "codex-tui/0.153.3 (Mac OS 26.5.1; arm64) iTerm.app/3.6.11 (codex-tui; 0.153.3)";

#[derive(Clone, Debug)]
pub struct AuthError {
    pub status: StatusCode,
    pub message: String,
    pub upstream: Option<u16>,
}

impl AuthError {
    fn new(status: StatusCode, message: impl Into<String>) -> Self {
        Self {
            status,
            message: message.into(),
            upstream: None,
        }
    }

    fn input(message: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, message)
    }

    fn unavailable(message: impl Into<String>) -> Self {
        Self::new(StatusCode::SERVICE_UNAVAILABLE, message)
    }

    fn conflict(message: impl Into<String>) -> Self {
        Self::new(StatusCode::CONFLICT, message)
    }
}

impl IntoResponse for AuthError {
    fn into_response(self) -> Response {
        let mut body = json!({"error": self.message});
        if let Some(status) = self.upstream {
            body["upstreamStatus"] = json!(status);
        }
        (self.status, Json(body)).into_response()
    }
}

fn unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn random<const N: usize>() -> Result<[u8; N], AuthError> {
    let mut bytes = [0u8; N];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| AuthError::unavailable("OpenBot could not initialize secure sign-in."))?;
    Ok(bytes)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn unique_id() -> Result<String, AuthError> {
    let mut bytes = random::<16>()?;
    bytes[6] = (bytes[6] & 15) | 0x40;
    bytes[8] = (bytes[8] & 63) | 0x80;
    let id = hex(&bytes);
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &id[..8],
        &id[8..12],
        &id[12..16],
        &id[16..20],
        &id[20..]
    ))
}

#[cfg(test)]
mod tests;
