use std::time::{Duration, SystemTime};

use axum::{
    Json,
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};
use serde_json::{Value, json};

use crate::auth::AuthError;

#[derive(Debug)]
pub struct Failure {
    status: StatusCode,
    code: String,
    message: String,
    upstream_status: Option<StatusCode>,
    retry_after: Option<String>,
    request_id: Option<String>,
    retry: Retry,
}

#[derive(Clone, Copy, Debug)]
enum Retry {
    Never,
    RateLimit,
    Transient,
}

impl Failure {
    pub fn credential(error: AuthError) -> Self {
        let code = if error.status == StatusCode::NOT_FOUND {
            "missing_credential"
        } else {
            "provider_sign_in_required"
        };
        let mut failure = Self::new(StatusCode::SERVICE_UNAVAILABLE, code, &error.message);
        failure.upstream_status = error
            .upstream
            .and_then(|status| StatusCode::from_u16(status).ok());
        failure
    }

    pub fn new(status: StatusCode, code: &str, message: &str) -> Self {
        Self {
            status,
            code: code.into(),
            message: message.into(),
            upstream_status: None,
            retry_after: None,
            request_id: None,
            retry: Retry::Never,
        }
    }

    pub fn protocol(message: &str) -> Self {
        Self::new(StatusCode::BAD_GATEWAY, "upstream_protocol_error", message)
    }

    pub fn incomplete() -> Self {
        let mut result = Self::new(
            StatusCode::BAD_GATEWAY,
            "upstream_incomplete",
            "Upstream stream ended before completing the turn.",
        );
        result.retry = Retry::Transient;
        result
    }

    pub fn network(error: &reqwest::Error) -> Self {
        let (status, code, message) = if error.is_timeout() {
            (
                StatusCode::GATEWAY_TIMEOUT,
                "upstream_timeout",
                "Upstream request timed out.",
            )
        } else {
            (
                StatusCode::BAD_GATEWAY,
                "upstream_connection_error",
                "Could not read the upstream response.",
            )
        };
        let mut result = Self::new(status, code, message);
        if error.is_timeout() || error.is_connect() || error.is_body() {
            result.retry = Retry::Transient;
        }
        result
    }

    pub fn upstream(
        status: StatusCode,
        response: &Value,
        headers: &HeaderMap,
        secret: &str,
    ) -> Self {
        let payload = if response["error"].is_object() {
            &response["error"]
        } else {
            response
        };
        let code = payload["code"]
            .as_str()
            .or(payload["type"].as_str())
            .unwrap_or("upstream_error");
        let message = payload["message"]
            .as_str()
            .or(response["detail"].as_str())
            .unwrap_or("Upstream provider returned an error.");
        let display_status = if status.is_client_error() || status.is_server_error() {
            status
        } else {
            StatusCode::BAD_GATEWAY
        };
        let retry = match status.as_u16() {
            429 if ![
                "usage_limit_reached",
                "insufficient_quota",
                "GoUsageLimitError",
            ]
            .iter()
            .any(|code| payload["code"] == *code || payload["type"] == *code) =>
            {
                Retry::RateLimit
            }
            500 | 502 | 503 | 504 | 520 | 522 | 523 | 524 | 526 | 527 => Retry::Transient,
            _ => Retry::Never,
        };
        let header_value = |name| headers.get(name).and_then(|value| value.to_str().ok());
        let request_id = ["request_id", "requestId"]
            .into_iter()
            .find_map(|key| payload[key].as_str().or(response[key].as_str()))
            .or_else(|| header_value("x-request-id"))
            .or_else(|| header_value("request-id"));
        Self {
            status: display_status,
            code: safe_value(code, secret, 128),
            message: safe_value(message, secret, 2048),
            upstream_status: Some(status),
            retry_after: header_value("retry-after").map(|value| safe_value(value, secret, 128)),
            request_id: request_id.map(|value| safe_value(value, secret, 128)),
            retry,
        }
    }

    pub fn event(event: &Value, headers: &HeaderMap, secret: &str) -> Self {
        let payload = if event["error"].is_object() {
            &event["error"]
        } else {
            event
        };
        let error_code = payload["code"]
            .as_str()
            .or(payload["type"].as_str())
            .unwrap_or_default();
        let status = match error_code {
            "overloaded_error" | "server_is_overloaded" | "internal_error" | "server_error" => {
                StatusCode::SERVICE_UNAVAILABLE
            }
            "rate_limit_error"
            | "rate_limit_exceeded"
            | "usage_limit_reached"
            | "insufficient_quota"
            | "GoUsageLimitError" => StatusCode::TOO_MANY_REQUESTS,
            "invalid_request_error" => StatusCode::BAD_REQUEST,
            "authentication_error" | "invalid_api_key" => StatusCode::UNAUTHORIZED,
            "permission_error" => StatusCode::FORBIDDEN,
            _ => StatusCode::BAD_GATEWAY,
        };
        let mut failure = Self::upstream(status, event, headers, secret);
        if status == StatusCode::BAD_GATEWAY {
            failure.retry = Retry::Never;
        }
        // Event errors came in an HTTP success response. Report the mapped status
        // to the caller while preserving the actual upstream HTTP status.
        failure.upstream_status = Some(StatusCode::OK);
        failure
    }

    pub fn delay(&self, attempt: u32, waited: Duration) -> Option<Duration> {
        let (attempts, budget, factor, cap) = match self.retry {
            Retry::Never => return None,
            Retry::RateLimit => (3, Duration::from_secs(30), 2u64, 8_000),
            Retry::Transient => (2, Duration::from_secs(10), 3u64, 5_000),
        };
        if attempt >= attempts || waited >= budget {
            return None;
        }
        let delay = self
            .retry_after
            .as_deref()
            .and_then(retry_after)
            .unwrap_or_else(|| Duration::from_millis((500 * factor.pow(attempt)).min(cap)));
        Some(delay.min(budget - waited))
    }

    pub fn body(&self) -> Value {
        let mut error = json!({"message": self.message, "type": self.code, "code": self.code});
        if let Some(status) = self.upstream_status {
            error["upstream_status"] = json!(status.as_u16());
        }
        if let Some(request_id) = &self.request_id {
            error["request_id"] = json!(request_id);
        }
        json!({"error": error})
    }
}

impl IntoResponse for Failure {
    fn into_response(self) -> Response {
        let mut response = (self.status, Json(self.body())).into_response();
        if let Some(after) = self
            .retry_after
            .and_then(|value| HeaderValue::from_str(&value).ok())
        {
            response.headers_mut().insert(header::RETRY_AFTER, after);
        }
        if let Some(id) = self
            .request_id
            .and_then(|value| HeaderValue::from_str(&value).ok())
        {
            response.headers_mut().insert("x-request-id", id);
        }
        response
    }
}

fn retry_after(raw: &str) -> Option<Duration> {
    let raw = raw.trim();
    if let Ok(seconds) = raw.parse::<u64>() {
        return Some(Duration::from_secs(seconds));
    }
    httpdate::parse_http_date(raw)
        .ok()
        .map(|time| time.duration_since(SystemTime::now()).unwrap_or_default())
}

fn safe_value(value: &str, secret: &str, limit: usize) -> String {
    let redacted = if secret.is_empty() {
        value.into()
    } else {
        value.replace(secret, "[redacted]")
    };
    redacted
        .chars()
        .filter(|character| !character.is_control() || *character == '\n')
        .take(limit)
        .collect()
}
