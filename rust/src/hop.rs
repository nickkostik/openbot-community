mod failure;
mod request;
mod response;
mod stream;

use std::{
    collections::VecDeque,
    convert::Infallible,
    io,
    path::Path,
    process::{Command, Stdio},
    time::Duration,
};

use axum::{
    Json, Router,
    body::{Body, Bytes},
    extract::{DefaultBodyLimit, State as AxumState, rejection::JsonRejection},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde_json::{Value, json};

use crate::{
    auth::CredentialStore,
    server::{State, read_json},
};

use self::{
    failure::Failure,
    request::{MAX_REQUEST_BYTES, Prepared},
    stream::{Frame, Live},
};

const MAX_REPLY_BYTES: usize = 32 * 1024 * 1024;
const MAX_ERROR_BYTES: usize = 256 * 1024;

#[derive(Clone)]
pub struct Hop {
    state: State,
    credentials: CredentialStore,
    client: reqwest::Client,
    version: String,
}

impl Hop {
    pub fn new(state: State, credentials: CredentialStore) -> io::Result<Self> {
        let timeout = std::env::var("OPENBOT_HOP_TIMEOUT")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|millis| *millis > 0)
            .map_or(Duration::from_secs(1800), Duration::from_millis);
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .no_proxy()
            .connect_timeout(timeout.min(Duration::from_secs(30)))
            .read_timeout(timeout)
            .timeout(timeout)
            .build()
            .map_err(io::Error::other)?;
        Ok(Self {
            state,
            credentials,
            client,
            version: version(),
        })
    }

    pub fn routes(self) -> Router {
        Router::new()
            .route(
                "/healthz",
                get(|| async { Json(json!({"ok": true, "service": "openbot"})) }),
            )
            .route("/v1/chat/completions", post(completions))
            .layer(DefaultBodyLimit::max(MAX_REQUEST_BYTES))
            .with_state(self)
    }

    async fn dispatch(&self, prepared: &Prepared) -> Result<Response, Failure> {
        let mut attempt = 0;
        let mut waited = Duration::ZERO;
        loop {
            match self.attempt(prepared).await {
                Ok(response) => return Ok(response),
                Err(failure) => {
                    let Some(delay) = failure.delay(attempt, waited) else {
                        return Err(failure);
                    };
                    tokio::time::sleep(delay).await;
                    attempt += 1;
                    waited += delay;
                }
            }
        }
    }

    async fn attempt(&self, prepared: &Prepared) -> Result<Response, Failure> {
        let response = self
            .client
            .post(&prepared.url)
            .headers(prepared.headers.clone())
            .body(prepared.body.clone())
            .send()
            .await
            .map_err(|err| Failure::network(&err))?;
        let status = response.status();
        let headers = response.headers().clone();
        if !status.is_success() {
            let data = read_reply(response, MAX_ERROR_BYTES).await.ok();
            let payload = data
                .and_then(|data| serde_json::from_slice(&data).ok())
                .unwrap_or_default();
            return Err(Failure::upstream(
                status,
                &payload,
                &headers,
                &prepared.secret,
            ));
        }
        if is_sse(&headers, prepared.upstream_stream) {
            let live = Live::new(
                response,
                prepared.protocol,
                &prepared.model,
                &prepared.secret,
            );
            if prepared.client_stream {
                return begin_stream(live).await;
            }
            return Ok(Json(collect_stream(live, &prepared.model).await?).into_response());
        }
        let data = read_reply(response, MAX_REPLY_BYTES).await?;
        let output: Value = serde_json::from_slice(&data).map_err(|_| {
            Failure::protocol(
                "Upstream returned a body that is neither inference JSON nor an event stream.",
            )
        })?;
        if output["error"].is_object() || output["status"] == "failed" {
            return Err(Failure::event(&output, &headers, &prepared.secret));
        }
        let completion = response::normalize(prepared.protocol, &prepared.model, output)?;
        if prepared.client_stream {
            let mut frames: VecDeque<_> = response::json_frames(completion)
                .into_iter()
                .map(|value| Frame::Data(value).bytes())
                .collect();
            frames.push_back(Frame::Done.bytes());
            Ok(stream_reply(frames, None))
        } else {
            Ok(Json(completion).into_response())
        }
    }
}

async fn completions(
    AxumState(hop): AxumState<Hop>,
    headers: HeaderMap,
    body: Result<Json<Value>, JsonRejection>,
) -> Result<Response, Failure> {
    let Json(body) = body.map_err(|error| {
        if error.status() == StatusCode::PAYLOAD_TOO_LARGE {
            Failure::new(
                error.status(),
                "request_too_large",
                "Inference request is too large.",
            )
        } else {
            Failure::new(
                StatusCode::BAD_REQUEST,
                "invalid_json",
                "Inference request must be JSON.",
            )
        }
    })?;
    let prepared =
        Prepared::load(&hop.state, &hop.credentials, body, &headers, &hop.version).await?;
    hop.dispatch(&prepared).await
}

fn is_sse(headers: &HeaderMap, requested: bool) -> bool {
    let media = headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.split(';').next())
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    media == "text/event-stream"
        || (requested
            && matches!(
                media.as_str(),
                "" | "text/plain" | "application/octet-stream"
            ))
}

async fn read_reply(mut response: reqwest::Response, limit: usize) -> Result<Vec<u8>, Failure> {
    let mut data = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|err| Failure::network(&err))?
    {
        if data.len() + chunk.len() > limit {
            return Err(Failure::protocol(
                "Upstream response exceeded the supported size.",
            ));
        }
        data.extend_from_slice(&chunk);
    }
    Ok(data)
}

async fn collect_stream(mut live: Live, model: &str) -> Result<Value, Failure> {
    let mut buffer = response::Buffer::new(model);
    let mut bytes = 0;
    while let Some(Frame::Data(chunk)) = live.next().await? {
        bytes += chunk.to_string().len();
        if bytes > MAX_REPLY_BYTES {
            return Err(Failure::protocol(
                "Buffered upstream response exceeded the supported size.",
            ));
        }
        buffer.push(&chunk);
    }
    buffer.completed()
}

async fn begin_stream(mut live: Live) -> Result<Response, Failure> {
    let mut prefix = VecDeque::new();
    let mut bytes = 0;
    while let Some(frame) = live.next().await? {
        let visible = frame.visible();
        let data = frame.bytes();
        bytes += data.len();
        if bytes > MAX_REPLY_BYTES {
            return Err(Failure::protocol(
                "Upstream preamble exceeded the supported size.",
            ));
        }
        prefix.push_back(data);
        // Errors before the first output are still retryable. After the
        // returned body starts, the response owns the original connection.
        if visible {
            return Ok(stream_reply(prefix, Some(Box::new(live))));
        }
    }
    Err(Failure::incomplete())
}

fn stream_reply(prefix: VecDeque<Bytes>, live: Option<Box<Live>>) -> Response {
    let chunks =
        futures_util::stream::unfold((prefix, live), |(mut prefix, mut live)| async move {
            if let Some(data) = prefix.pop_front() {
                return Some((Ok::<_, Infallible>(data), (prefix, live)));
            }
            match live.as_mut()?.next().await {
                Ok(Some(frame)) => Some((Ok(frame.bytes()), (prefix, live))),
                Ok(None) => None,
                Err(failure) => Some((Ok(Frame::Data(failure.body()).bytes()), (prefix, None))),
            }
        });
    let mut response = Body::from_stream(chunks).into_response();
    let headers = response.headers_mut();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream"),
    );
    headers.insert("x-accel-buffering", HeaderValue::from_static("no"));
    response
}

fn version() -> String {
    if let Ok(commit) = std::env::var("OPENBOT_COMMIT")
        && !commit.trim().is_empty()
    {
        return commit.trim().into();
    }
    let source = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap_or_else(|| Path::new("."));
    if let Ok(Some(stamp)) = read_json(&source.join("payload/version.json"), 4096)
        && let Some(commit) = stamp["commit"]
            .as_str()
            .map(str::trim)
            .filter(|commit| !commit.is_empty())
    {
        return commit.into();
    }
    Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(source)
        .stderr(Stdio::null())
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|commit| commit.trim().to_string())
        .filter(|commit| {
            (7..=40).contains(&commit.len()) && commit.bytes().all(|byte| byte.is_ascii_hexdigit())
        })
        .or_else(|| option_env!("OPENBOT_COMMIT").map(str::to_owned))
        .unwrap_or_else(|| "unknown".into())
}

#[cfg(test)]
mod tests;
