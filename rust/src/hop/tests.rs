use std::{
    fs,
    path::Path,
    sync::{Arc, Mutex},
};

use axum::{
    body::Bytes,
    http::{Uri, header::HeaderName},
    routing::post,
};
use serde_json::json;
use tempfile::TempDir;

use super::*;
use crate::server;

const PROFILE: &str = "00000000-0000-4000-8000-000000000001";
const CHAT: &str = "/v1/chat/completions";

struct TestServer {
    base: String,
    task: tokio::task::JoinHandle<io::Result<()>>,
}

impl TestServer {
    async fn start(router: impl FnOnce(&str) -> Router) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let authority = listener.local_addr().unwrap().to_string();
        let app = router(&authority);
        let task = tokio::spawn(async move { axum::serve(listener, app).await });
        Self {
            base: format!("http://{authority}"),
            task,
        }
    }
}

impl Drop for TestServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

#[derive(Clone, Default)]
struct Mock(Arc<Mutex<MockState>>);

#[derive(Default)]
struct MockState {
    requests: Vec<Seen>,
    replies: VecDeque<Response>,
}

struct Seen {
    path: String,
    headers: HeaderMap,
    body: Value,
}

impl Mock {
    async fn start() -> (Self, TestServer) {
        let mock = Self::default();
        let upstream = TestServer::start(|_| {
            Router::new()
                .fallback(post(mock_reply))
                .with_state(mock.clone())
        })
        .await;
        (mock, upstream)
    }

    fn push(&self, response: Response) {
        self.0.lock().unwrap().replies.push_back(response);
    }

    fn inspect(&self, test: impl FnOnce(&[Seen])) {
        let state = self.0.lock().unwrap();
        test(&state.requests);
        assert!(state.replies.is_empty(), "fixture response remained unused");
    }
}

async fn mock_reply(
    AxumState(mock): AxumState<Mock>,
    uri: Uri,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let mut mock = mock.0.lock().unwrap();
    mock.requests.push(Seen {
        path: uri.path().into(),
        headers,
        body,
    });
    mock.replies.pop_front().unwrap_or_else(|| {
        (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": {"message": "Unexpected fixture call"}})),
        )
            .into_response()
    })
}

struct Fixture {
    _dir: TempDir,
    rust: TestServer,
    mock: Mock,
    _upstream: TestServer,
    client: reqwest::Client,
    logs: Logs,
}

impl Fixture {
    async fn new() -> Self {
        let (mock, upstream) = Mock::start().await;
        let dir = tempfile::tempdir().unwrap();
        let base = &upstream.base;
        let plan = json!({"kind": "custom", "catalog": {
            "providers": [
                {"id":"opencode","origin": format!("{base}/first/v1"), "apiType":"chat-completions"},
                {"id":"opencode-2","origin": format!("{base}/second/v1"), "apiType":"responses"},
                {"id":"messages-test","origin": format!("{base}/messages/v1"), "apiType":"anthropic"}
            ],
            "models": [
                {"id":"opencode:synthetic","providerId":"opencode","slug":"synthetic", "reasoningLevels":["default","high","max"]},
                {"id":"opencode-2:synthetic","providerId":"opencode-2","slug":"synthetic", "reasoningLevels":["default","high","max"],"maxOutputTokens":256},
                {"id":"messages-test:synthetic","providerId":"messages-test","slug":"synthetic"}
            ], "bindings": []
        }});
        file(dir.path(), "openbot-plan.json", plan);
        file(
            dir.path(),
            "secrets.json",
            json!({"providers": {
                "opencode": "fixture-key-one", "opencode-2": "fixture-key-two", "messages-test": "fixture-key-messages"
            }}),
        );
        fs::write(dir.path().join("openbot-mode"), "custom\n").unwrap();
        let state = State::new(dir.path().into(), true).unwrap();
        let logs = Logs::new(state.root(), true);
        let hop = Hop::new(
            state.clone(),
            CredentialStore::new(state.root(), false).unwrap(),
        )
        .unwrap()
        .with_logs(logs.clone());
        let logs_router = logs.clone().routes();
        let rust = TestServer::start(move |authority| {
            server::router(state, authority.into(), [hop.routes(), logs_router])
        })
        .await;
        let client = reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(8))
            .build()
            .unwrap();
        Self {
            _dir: dir,
            rust,
            mock,
            _upstream: upstream,
            client,
            logs,
        }
    }

    fn path(&self) -> &Path {
        self._dir.path()
    }

    async fn turn(&self, body: Value) -> reqwest::Response {
        self.client
            .post(format!("{}{CHAT}", self.rust.base))
            .json(&body)
            .send()
            .await
            .unwrap()
    }

    async fn settings(&self, body: Value) {
        let reply = self
            .client
            .put(format!("{}/api/bot-models", self.rust.base))
            .json(&body)
            .send()
            .await
            .unwrap();
        assert!(
            reply.status().is_success(),
            "{}",
            reply.text().await.unwrap()
        );
    }

    async fn log(&self, path: &str) -> Value {
        self.logs.wait_for_writes().await;
        self.client
            .get(format!("{}{}", self.rust.base, path))
            .send()
            .await
            .unwrap()
            .error_for_status()
            .unwrap()
            .json()
            .await
            .unwrap()
    }
}

fn file(root: &Path, name: &str, contents: Value) {
    fs::write(root.join(name), contents.to_string()).unwrap();
}

fn chat_body() -> Value {
    json!({"id":"fixture-reply","object":"chat.completion","model":"synthetic","choices":[
        {"index": 0, "message":{"role":"assistant","content":"ready"}, "finish_reason":"stop"}]})
}

fn responses_body() -> Value {
    json!({"id":"fixture-responses","model":"synthetic","status":"completed",
        "output":[{"type":"message","content":[{"type":"output_text","text":"ready"}]}],
        "usage":{"input_tokens":18,"output_tokens":2,"input_tokens_details":{"cached_tokens":10}}})
}

fn user_request(model: &str, stream: bool) -> Value {
    json!({"model":model,"stream":stream,"messages":[
        {"role":"system","content":[{"type":"text","text":format!("Read /home/box/agent-data/agents/{PROFILE}/profile.json")}]},
        {"role":"user","content":"Continue."}]})
}

fn sse_body(body: Body, headers: &[(&'static str, &'static str)]) -> Response {
    let mut response = body.into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream"),
    );
    for (name, value) in headers {
        response.headers_mut().insert(
            HeaderName::from_static(name),
            HeaderValue::from_static(value),
        );
    }
    response
}

fn events(frames: &[Value]) -> String {
    frames
        .iter()
        .map(|event| format!("data: {event}\r\n\r\n"))
        .collect()
}

fn decoded(stream: &str) -> Vec<Value> {
    stream
        .lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter(|line| *line != "[DONE]")
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

#[tokio::test]
async fn saved_model_and_effort_switch_go_accounts_and_reload() {
    let fixture = Fixture::new().await;
    fixture.mock.push(Json(chat_body()).into_response());
    for _ in 0..3 {
        fixture.mock.push(Json(responses_body()).into_response());
    }
    fixture.mock.push(Json(chat_body()).into_response());
    let mut input = user_request("opencode:synthetic", false);
    input["conversationId"] = json!("synthetic-conversation");
    input["epochId"] = json!("synthetic-epoch");
    input["max_tokens"] = json!(999);
    let first = fixture.turn(input.clone()).await;
    assert_eq!(first.status(), StatusCode::OK);
    fixture
        .settings(
            json!({"botId": PROFILE, "modelId":"opencode-2:synthetic", "reasoningEffort":"max"}),
        )
        .await;
    input["reasoning_effort"] = json!("low");
    let next = fixture.turn(input.clone()).await;
    assert_eq!(next.status(), StatusCode::OK);
    assert_eq!(
        next.json::<Value>().await.unwrap()["usage"]["prompt_tokens_details"]["cached_tokens"],
        10
    );
    input["messages"][1]["content"] = json!("A changed suffix.");
    let reply = fixture
        .client
        .post(format!("{}{CHAT}", fixture.rust.base))
        .header(header::USER_AGENT, "OpenBot fixture caller")
        .header("x-opencode-session", "ignored-user-session")
        .header(header::AUTHORIZATION, "Bearer inbound-not-a-provider-key")
        .json(&input)
        .send()
        .await
        .unwrap();
    assert_eq!(reply.status(), StatusCode::OK);
    input["openbot_internal_auxiliary"] = json!(true);
    assert_eq!(fixture.turn(input.clone()).await.status(), StatusCode::OK);
    fixture.settings(json!({"botId": PROFILE, "modelId":null,"reasoningModelId":"opencode-2:synthetic","reasoningEffort":null})).await;
    assert_eq!(fixture.turn(input).await.status(), StatusCode::OK);

    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 5);
        assert_eq!(seen[0].path, "/first/v1/chat/completions");
        assert_eq!(
            seen[0].headers[header::AUTHORIZATION],
            "Bearer fixture-key-one"
        );
        assert_eq!(seen[1].path, "/second/v1/responses");
        assert_eq!(
            seen[1].headers[header::AUTHORIZATION],
            "Bearer fixture-key-two"
        );
        assert_eq!(seen[1].body["model"], "synthetic");
        assert_eq!(seen[1].body["reasoning"]["effort"], "max");
        assert_eq!(seen[1].body["max_output_tokens"], 256);
        assert_ne!(
            seen[0].headers["x-opencode-session"],
            seen[1].headers["x-opencode-session"]
        );
        assert_eq!(
            seen[1].headers["x-opencode-session"],
            seen[2].headers["x-opencode-session"]
        );
        assert_ne!(
            seen[2].headers["x-opencode-session"],
            seen[3].headers["x-opencode-session"]
        );
        assert_eq!(
            seen[1].body["prompt_cache_key"],
            seen[2].body["prompt_cache_key"]
        );
        assert_ne!(
            seen[2].body["prompt_cache_key"],
            seen[3].body["prompt_cache_key"]
        );
        assert_eq!(
            seen[2].headers[header::USER_AGENT],
            "OpenBot fixture caller"
        );
        assert!(
            seen[1].headers[header::USER_AGENT]
                .to_str()
                .unwrap()
                .starts_with("OpenBot/")
        );
        assert!(!seen[1].headers["x-openbot-version"].is_empty());
        assert_eq!(seen[4].path, "/first/v1/chat/completions");
        assert!(seen.iter().all(|row| {
            ["conversationId", "epochId", "openbot_internal_auxiliary"]
                .iter()
                .all(|field| row.body.get(*field).is_none())
        }));
    });
}

#[tokio::test]
async fn messages_stream_is_live_and_preserves_tools_and_usage() {
    let fixture = Fixture::new().await;
    let gate = Arc::new(tokio::sync::Notify::new());
    let first = events(&[
        json!({"type":"message_start","message":{"id":"fixture-messages","model":"synthetic","usage":{
            "input_tokens":20,"cache_creation_input_tokens":8,"cache_read_input_tokens":30}}}),
        json!({"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"call_weather","name":"weather","input":{}}}),
    ]);
    let later = events(&[
        json!({"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"city\":\""}}),
        json!({"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"Zürich\"}"}}),
        json!({"type":"content_block_stop","index":1}),
        json!({"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":7}}),
        json!({"type":"message_stop"}),
    ]);
    let second = Arc::clone(&gate);
    let chunks = futures_util::stream::unfold((Some(first), Some(later)), move |(first, later)| {
        let gate = Arc::clone(&second);
        async move {
            if let Some(first) = first {
                Some((Ok::<_, Infallible>(Bytes::from(first)), (None, later)))
            } else {
                let later = later?;
                gate.notified().await;
                Some((Ok(Bytes::from(later)), (None, None)))
            }
        }
    });
    fixture.mock.push(sse_body(Body::from_stream(chunks), &[]));
    let mut input = user_request("messages-test:synthetic", true);
    input["messages"][1]["content"] = json!([
        {"type":"text","text":"Image context"},
        {"type":"image_url","image_url":{"url":"data:image/png;base64,AQID"}}
    ]);
    let mut reply = fixture.turn(input).await;
    assert_eq!(reply.status(), StatusCode::OK);
    let first_byte = tokio::time::timeout(Duration::from_secs(2), reply.chunk())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    gate.notify_one();
    let mut body = String::from_utf8(first_byte.to_vec()).unwrap();
    body.push_str(&reply.text().await.unwrap());
    assert!(body.ends_with("data: [DONE]\n\n"));
    let frames = decoded(&body);
    let call = &frames[1]["choices"][0]["delta"]["tool_calls"][0];
    assert_eq!(call["id"], "call_weather");
    assert_eq!(call["function"]["name"], "weather");
    assert_eq!(
        frames[2]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"],
        "{\"city\":\""
    );
    assert_eq!(
        frames[3]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"],
        "Zürich\"}"
    );
    let end = frames.last().unwrap();
    assert_eq!(end["choices"][0]["finish_reason"], "tool_calls");
    assert_eq!(end["usage"]["prompt_tokens"], 58);
    assert_eq!(
        end["usage"]["prompt_tokens_details"],
        json!({"cached_tokens":30,"cache_creation_tokens":8})
    );
    assert_eq!(end["usage"]["completion_tokens"], 7);
    fixture.mock.inspect(|seen| {
        let request = &seen[0];
        assert_eq!(request.path, "/messages/v1/messages");
        assert_eq!(request.headers["x-api-key"], "fixture-key-messages");
        assert_eq!(request.headers["anthropic-version"], "2023-06-01");
        assert!(!request.headers.contains_key(header::AUTHORIZATION));
        assert_eq!(
            request.body["messages"][0]["content"][0]["text"],
            "Image context"
        );
        assert_eq!(
            request.body["messages"][0]["content"][1]["source"]["data"],
            "AQID"
        );
        assert!(request.body["system"].as_str().unwrap().contains(PROFILE));
    });
}

#[tokio::test]
async fn responses_stream_assembles_tool_arguments_for_a_buffered_caller() {
    let fixture = Fixture::new().await;
    let native = events(&[
        json!({"type":"response.created","response":{"id":"fixture-two","model":"synthetic","created_at":42}}),
        json!({"type":"response.reasoning_summary_text.delta","delta":"Check."}),
        json!({"type":"response.output_item.added","output_index":1,"item":{"type":"function_call","id":"item_a","call_id":"call_a","name":"lookup","arguments":""}}),
        json!({"type":"response.function_call_arguments.delta","output_index":1,"delta":"{\"a\":"}),
        json!({"type":"response.function_call_arguments.done","output_index":1,"arguments":"{\"a\":1}"}),
        json!({"type":"response.output_item.done","output_index":1,"item":{"type":"function_call","call_id":"call_a","name":"lookup","arguments":"{\"a\":1}"}}),
        json!({"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":120,"output_tokens":5,"input_tokens_details":{"cached_tokens":90}}}}),
    ]);
    // Separate transport frames can end halfway through any line.
    let source = native.into_bytes();
    let pieces: Vec<_> = source
        .chunks(11)
        .map(Bytes::copy_from_slice)
        .map(Ok::<_, Infallible>)
        .collect();
    fixture.mock.push(sse_body(
        Body::from_stream(futures_util::stream::iter(pieces)),
        &[],
    ));
    let reply = fixture
        .turn(user_request("opencode-2:synthetic", false))
        .await;
    assert_eq!(reply.status(), StatusCode::OK);
    let output: Value = reply.json().await.unwrap();
    assert_eq!(output["created"], 42);
    let choice = &output["choices"][0];
    assert_eq!(choice["message"]["reasoning_content"], "Check.");
    assert_eq!(
        choice["message"]["tool_calls"][0],
        json!({"id":"call_a","type":"function",
        "function":{"name":"lookup","arguments":"{\"a\":1}"}})
    );
    assert_eq!(choice["finish_reason"], "tool_calls");
    assert_eq!(
        output["usage"]["prompt_tokens_details"]["cached_tokens"],
        90
    );
    fixture
        .mock
        .inspect(|seen| assert_eq!(seen[0].path, "/second/v1/responses"));
}

fn upstream_error(status: StatusCode, code: &str, message: &str) -> Response {
    let mut response = (
        status,
        Json(json!({"error":{"code":code,"message":message}})),
    )
        .into_response();
    response
        .headers_mut()
        .insert(header::RETRY_AFTER, HeaderValue::from_static("0"));
    response
        .headers_mut()
        .insert("x-request-id", HeaderValue::from_static("fixture-request"));
    response
}

#[tokio::test]
async fn transient_failure_retries_and_quota_and_protocol_failures_are_visible() {
    let fixture = Fixture::new().await;
    fixture.mock.push(upstream_error(
        StatusCode::SERVICE_UNAVAILABLE,
        "overloaded_error",
        "Provider is busy.",
    ));
    fixture.mock.push(Json(chat_body()).into_response());
    fixture.mock.push(upstream_error(
        StatusCode::TOO_MANY_REQUESTS,
        "GoUsageLimitError",
        "Quota for fixture-key-one exhausted.",
    ));
    fixture.mock.push(Json(responses_body()).into_response());
    fixture.mock.push(Json(chat_body()).into_response());
    let body = user_request("opencode:synthetic", false);
    assert_eq!(fixture.turn(body.clone()).await.status(), StatusCode::OK);
    let quota = fixture.turn(body.clone()).await;
    assert_eq!(quota.status(), StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(quota.headers()[header::RETRY_AFTER], "0");
    let quota: Value = quota.json().await.unwrap();
    assert_eq!(quota["error"]["code"], "GoUsageLimitError");
    assert_eq!(quota["error"]["message"], "Quota for [redacted] exhausted.");
    assert_eq!(quota["error"]["request_id"], "fixture-request");
    let mismatch = fixture.turn(body.clone()).await;
    assert_eq!(mismatch.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(
        mismatch.json::<Value>().await.unwrap()["error"]["code"],
        "upstream_protocol_error"
    );
    assert_eq!(fixture.turn(body).await.status(), StatusCode::OK);
    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 5);
        assert_eq!(seen[0].body, seen[1].body);
        assert_eq!(seen[0].headers, seen[1].headers);
    });
}

#[tokio::test]
async fn streaming_retry_stops_after_output_and_next_turn_recovers() {
    let fixture = Fixture::new().await;
    file(
        fixture.path(),
        "openbot-logs.json",
        json!({"loggingEnabled":true,"logBodies":true}),
    );
    let early = events(&[
        json!({"type":"response.created","response":{"id":"unsuccessful","model":"synthetic"}}),
        json!({"type":"response.failed","response":{"error":{"code":"server_error","message":"retryable"}}}),
    ]);
    fixture
        .mock
        .push(sse_body(Body::from(early), &[("retry-after", "0")]));
    let partial = events(&[
        json!({"type":"response.created","response":{"id":"partial","model":"synthetic"}}),
        json!({"type":"response.output_text.delta","delta":"partial"}),
    ]);
    fixture.mock.push(sse_body(Body::from(partial), &[]));
    fixture.mock.push(Json(responses_body()).into_response());
    let mut request = user_request("opencode-2:synthetic", true);
    request["openbotRequestId"] = json!("fixture-stream-retry");
    let reply = fixture.turn(request.clone()).await;
    assert_eq!(reply.status(), StatusCode::OK);
    let frames = reply.text().await.unwrap();
    assert!(frames.contains("partial"));
    assert!(frames.contains("upstream_incomplete"));
    assert!(!frames.contains("unsuccessful"));
    assert!(!frames.contains("[DONE]"));
    request["openbotRequestId"] = json!("fixture-stream-recovery");
    let next = fixture.turn(request).await;
    assert_eq!(next.status(), StatusCode::OK);
    let next = next.text().await.unwrap();
    assert!(next.contains("\"content\":\"ready\""));
    assert!(next.ends_with("data: [DONE]\n\n"));
    fixture.mock.inspect(|seen| assert_eq!(seen.len(), 3));
    let errors = fixture.log("/api/logs?ok=false&channel=custom").await;
    assert_eq!(errors["total"], 1);
    let failed = &errors["items"][0];
    assert_eq!(failed["attemptCount"], 2);
    assert_eq!(failed["requestId"], "fixture-stream-retry");
    assert_eq!(failed["status"], 502);
    assert!(failed["firstTokenMs"].is_u64());
    let id = failed["id"].as_str().unwrap();
    let detail = fixture.log(&format!("/api/logs/{id}")).await;
    assert_eq!(detail["response"]["error"]["code"], "upstream_incomplete");
    assert!(detail["response"]["events"].to_string().contains("partial"));
    let recovered = fixture
        .log("/api/logs?ok=true&q=fixture-stream-recovery")
        .await;
    assert_eq!(recovered["total"], 1);
    assert_eq!(recovered["items"][0]["cachedTokens"], 10);
    assert_eq!(recovered["items"][0]["totalTokens"], 20);
    assert_eq!(recovered["items"][0]["botId"], PROFILE);
    assert_eq!(recovered["items"][0]["modelId"], "opencode-2:synthetic");
    assert_eq!(recovered["items"][0]["apiType"], "responses");
}

#[tokio::test]
async fn official_mode_and_global_and_bot_pauses_block_dispatch() {
    let fixture = Fixture::new().await;
    let request = user_request("opencode:synthetic", false);
    fs::write(fixture.path().join("openbot-mode"), "official\n").unwrap();
    let reply = fixture.turn(request.clone()).await;
    assert_eq!(reply.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        reply.json::<Value>().await.unwrap()["error"]["code"],
        "official_mode"
    );
    fs::write(fixture.path().join("openbot-mode"), "custom\n").unwrap();
    file(
        fixture.path(),
        "openbot-pause.json",
        json!({"paused": true}),
    );
    let reply = fixture.turn(request.clone()).await;
    assert_eq!(
        reply.json::<Value>().await.unwrap()["error"]["code"],
        "paused"
    );
    file(
        fixture.path(),
        "openbot-pause.json",
        json!({"paused": false}),
    );
    file(
        fixture.path(),
        "openbot-pause-bots.json",
        json!({"pausedBotIds": [PROFILE]}),
    );
    let reply = fixture.turn(request).await;
    assert_eq!(
        reply.json::<Value>().await.unwrap()["error"]["code"],
        "bot_paused"
    );
    fixture.mock.inspect(|seen| assert!(seen.is_empty()));
}

#[tokio::test]
async fn credentials_are_never_forwarded_across_redirects() {
    let fixture = Fixture::new().await;
    let mut reply = StatusCode::FOUND.into_response();
    reply.headers_mut().insert(
        header::LOCATION,
        HeaderValue::from_static("/other/v1/chat/completions"),
    );
    fixture.mock.push(reply);
    let reply = fixture
        .turn(user_request("opencode:synthetic", false))
        .await;
    assert_eq!(reply.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(
        reply.json::<Value>().await.unwrap()["error"]["upstream_status"],
        302
    );
    fixture.mock.inspect(|seen| assert_eq!(seen.len(), 1));
}

#[tokio::test]
async fn saved_openai_credentials_are_scoped_to_the_existing_outbound_contracts() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("openbot-mode"), "custom\n").unwrap();
    file(
        dir.path(),
        "openbot-plan.json",
        json!({"kind":"custom","catalog":{
            "providers":[{"id":"openai","origin":"https://api.openai.com/v1","apiType":"responses"}],
            "models":[{"id":"openai:gpt-synthetic","slug":"gpt-synthetic","providerId":"openai"}],"bindings":[]
        }}),
    );
    let state = State::new(dir.path().into(), false).unwrap();
    let mut body = user_request("openai:gpt-synthetic", false);
    body["conversationId"] = json!("fixture-conversation");
    for kind in ["chatgpt-plan", "openai-oauth"] {
        let saved = json!({"kind":kind,"clientId":"oaiapp_fixture","subject":"fixture-subject",
            "idToken":"fixture-id","refreshToken":"fixture-refresh","accessToken":"fixture-token",
            "scopes":["resource.invoke","chatgpt.tokens.use.direct"],"expiresAt":4102444800u64,
            "chatgptAccountId":"fixture-account"});
        file(
            dir.path(),
            "secrets.json",
            json!({"providers":{"openai":saved.to_string()}}),
        );
        let credentials = CredentialStore::new(state.root(), false).unwrap();
        let prepared = Prepared::load(
            &state,
            &credentials,
            body.clone(),
            &HeaderMap::new(),
            "fixture",
            Intent::Chat(None),
        )
        .await
        .unwrap();
        let outbound: Value = serde_json::from_slice(&prepared.body).unwrap();
        assert!(!prepared.client_stream);
        assert!(prepared.upstream_stream);
        assert_eq!(outbound["store"], false);
        assert!(outbound["prompt_cache_key"].is_string());
        assert_eq!(
            prepared.headers[header::AUTHORIZATION],
            "Bearer fixture-token"
        );
        assert!(prepared.headers[header::AUTHORIZATION].is_sensitive());
        if kind == "chatgpt-plan" {
            assert_eq!(prepared.url, "https://api.openai.com/v1/responses");
            assert_eq!(outbound["max_output_tokens"], 65_536);
            assert!(!prepared.headers.contains_key("chatgpt-account-id"));
        } else {
            assert_eq!(
                prepared.url,
                "https://chatgpt.com/backend-api/codex/responses"
            );
            assert!(outbound.get("max_output_tokens").is_none());
            assert_eq!(prepared.headers["chatgpt-account-id"], "fixture-account");
        }
    }
}

#[tokio::test]
async fn responses_emits_done_only_items_once_alongside_streamed_items() {
    let fixture = Fixture::new().await;
    let first = json!({"type":"message","content":[{"type":"output_text","text":"First "}]});
    let second = json!({"type":"message","content":[{"type":"output_text","text":"second."}]});
    let native = events(&[
        json!({"type":"response.created","response":{"id":"fixture-two","model":"synthetic"}}),
        json!({"type":"response.output_text.delta","output_index":0,"delta":"First "}),
        json!({"type":"response.output_item.done","output_index":0,"item":first}),
        json!({"type":"response.output_item.done","output_index":1,"item":second}),
        json!({"type":"response.completed","response":{"status":"completed","output":[first,second]}}),
    ]);
    fixture.mock.push(sse_body(Body::from(native), &[]));
    let reply = fixture
        .turn(user_request("opencode-2:synthetic", false))
        .await;
    assert_eq!(reply.status(), StatusCode::OK);
    assert_eq!(
        reply.json::<Value>().await.unwrap()["choices"][0]["message"]["content"],
        "First second."
    );
}

#[tokio::test]
async fn abandoning_a_stream_releases_the_upstream_connection() {
    struct Dropped(Arc<tokio::sync::Notify>);
    impl Drop for Dropped {
        fn drop(&mut self) {
            self.0.notify_one();
        }
    }
    let fixture = Fixture::new().await;
    let released = Arc::new(tokio::sync::Notify::new());
    file(
        fixture.path(),
        "openbot-logs.json",
        json!({"loggingEnabled":true,"logBodiesOnError":false}),
    );
    let started = events(&[json!({"choices":[{
        "index":0,"delta":{"content":"First"},"finish_reason":null
    }]})]);
    let chunks = futures_util::stream::unfold(
        (Some(started), Dropped(released.clone())),
        |(first, guard)| async move {
            if let Some(first) = first {
                Some((Ok::<_, Infallible>(Bytes::from(first)), (None, guard)))
            } else {
                let _guard = guard;
                std::future::pending().await
            }
        },
    );
    fixture.mock.push(sse_body(Body::from_stream(chunks), &[]));
    let mut reply = fixture.turn(user_request("opencode:synthetic", true)).await;
    assert_eq!(reply.status(), StatusCode::OK);
    assert!(reply.chunk().await.unwrap().is_some());
    drop(reply);
    tokio::time::timeout(Duration::from_secs(3), released.notified())
        .await
        .unwrap();
    fixture.mock.inspect(|seen| assert_eq!(seen.len(), 1));
    let logged = fixture.log("/api/logs").await;
    assert_eq!(logged["total"], 1);
    let row = &logged["items"][0];
    assert_eq!(row["status"], 499);
    assert_eq!(row["ok"], false);
    assert_eq!(row["hasResponse"], false);
    assert!(row["firstTokenMs"].is_u64());
}

async fn direct_body(
    origin: &str,
    slug: &str,
    protocol: &str,
    saved_effort: &str,
    request: Value,
) -> Value {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("openbot-mode"), "custom\n").unwrap();
    file(
        dir.path(),
        "openbot-plan.json",
        json!({"kind":"custom","catalog":{
            "providers":[{"id":"direct","origin":origin,"apiType":protocol}],
            "models":[{"id":"direct:model","slug":slug,"providerId":"direct","maxOutputTokens":65536,
                "reasoningLevels":["default","none","low","high","xhigh","max"]}],"bindings":[]
        }}),
    );
    file(
        dir.path(),
        "secrets.json",
        json!({"providers":{"direct":"fixture-key"}}),
    );
    file(
        dir.path(),
        "openbot-bot-models.json",
        json!({"reasoning": { (PROFILE): {"direct:model":saved_effort} }}),
    );
    let state = State::new(dir.path().into(), false).unwrap();
    let credentials = CredentialStore::new(state.root(), false).unwrap();
    let prepared = Prepared::load(
        &state,
        &credentials,
        request,
        &HeaderMap::new(),
        "fixture",
        Intent::Chat(None),
    )
    .await
    .unwrap();
    serde_json::from_slice(&prepared.body).unwrap()
}

#[tokio::test]
async fn direct_provider_efforts_keep_their_native_fields_and_limits() {
    let mut request = user_request("direct:model", false);
    request["max_tokens"] = json!(65536);
    request["thinking"] = json!({"type":"disabled", "clear_thinking":false});
    request["reasoning_effort"] = json!("medium");
    request["thinking_budget"] = json!(99);

    let zai = direct_body(
        "https://api.z.ai/api/coding/paas/v4",
        "glm-5.3",
        "chat-completions",
        "high",
        request.clone(),
    )
    .await;
    assert_eq!(
        zai["thinking"],
        json!({"clear_thinking":false,"type":"enabled"})
    );
    assert_eq!(zai["reasoning_effort"], "high");
    let qwen = direct_body(
        "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "qwen4-max",
        "chat-completions",
        "none",
        request.clone(),
    )
    .await;
    assert_eq!(qwen["reasoning_effort"], "none");
    assert!(qwen.get("thinking_budget").is_none());
    assert!(qwen.get("thinking").is_none());

    request["chat_template_kwargs"] =
        json!({"enable_thinking":false,"reasoning_effort":"low","custom_flag":true});
    let nvidia = direct_body(
        "https://integrate.api.nvidia.com/v1",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "chat-completions",
        "high",
        request.clone(),
    )
    .await;
    assert_eq!(
        nvidia["chat_template_kwargs"],
        json!({"custom_flag":true,"enable_thinking":true})
    );
    assert_eq!(nvidia["max_tokens"], 32768);
    assert!(nvidia.get("reasoning_effort").is_none());
    let super_vl = direct_body(
        "https://integrate.api.nvidia.com/v1",
        "nvidia/nemotron-3.5-super-vl-120b-a12b",
        "chat-completions",
        "medium",
        request.clone(),
    )
    .await;
    assert_eq!(
        super_vl["chat_template_kwargs"],
        json!({"custom_flag":true})
    );
    assert_eq!(super_vl["reasoning_effort"], "low");

    let kimi = direct_body(
        "https://api.kimi.com/coding/v1",
        "k3.1-pro",
        "chat-completions",
        "xhigh",
        request.clone(),
    )
    .await;
    assert_eq!(kimi["reasoning_effort"], "max");
    assert!(kimi.get("thinking").is_none());
    let deepseek = direct_body(
        "https://api.deepseek.com/v1",
        "deepseek-v4-flash",
        "responses",
        "none",
        request.clone(),
    )
    .await;
    assert_eq!(deepseek["reasoning"]["effort"], "none");
    let deepseek = direct_body(
        "https://api.deepseek.com/v1",
        "deepseek-v4-flash",
        "anthropic",
        "max",
        request,
    )
    .await;
    assert_eq!(deepseek["output_config"]["effort"], "max");
    assert_eq!(deepseek["thinking"]["type"], "enabled");
}

#[tokio::test]
async fn log_capture_applies_policy_redacts_secrets_and_limits_large_bodies() {
    let fixture = Fixture::new().await;
    let mut request = user_request("opencode:synthetic", false);
    request["messages"][1]["content"] = json!("fixture-key-one and Bearer synthetic-extra-secret.");
    request["provider_options"] =
        json!({"api_key":"embedded-synthetic-secret","refreshToken":"synthetic-renewal-secret"});

    fixture.mock.push(Json(chat_body()).into_response());
    assert_eq!(fixture.turn(request.clone()).await.status(), StatusCode::OK);
    assert_eq!(fixture.log("/api/logs").await["total"], 0);

    file(
        fixture.path(),
        "openbot-logs.json",
        json!({"loggingEnabled":true,"maxBodyCaptureBytes":1024}),
    );
    fixture.mock.push(Json(chat_body()).into_response());
    assert_eq!(fixture.turn(request.clone()).await.status(), StatusCode::OK);
    let success = fixture.log("/api/logs?ok=true").await;
    assert_eq!(success["items"][0]["hasRequest"], false);
    assert_eq!(success["items"][0]["hasResponse"], false);

    fixture.mock.push(upstream_error(
        StatusCode::BAD_REQUEST,
        "bad_request",
        "fixture-key-one and Bearer provider-synthetic-secret",
    ));
    assert_eq!(
        fixture.turn(request.clone()).await.status(),
        StatusCode::BAD_REQUEST
    );
    let errors = fixture.log("/api/logs?ok=false").await;
    assert_eq!(errors["total"], 1);
    let row = &errors["items"][0];
    assert_eq!(row["hasRequest"], true);
    assert_eq!(row["hasResponse"], true);
    let detail = fixture
        .log(&format!("/api/logs/{}", row["id"].as_str().unwrap()))
        .await;
    let text = detail.to_string();
    for secret in [
        "fixture-key-one",
        "synthetic-extra-secret",
        "embedded-synthetic-secret",
        "synthetic-renewal-secret",
        "provider-synthetic-secret",
    ] {
        assert!(!text.contains(secret));
    }
    assert!(text.contains("[redacted]"));

    file(
        fixture.path(),
        "openbot-logs.json",
        json!({"loggingEnabled":true,"logBodies":true,"maxBodyCaptureBytes":1024}),
    );
    request["messages"][1]["content"] = json!("synthetic payload".repeat(100));
    request["openbotRequestId"] = json!("over-capture-limit");
    let mut reply = chat_body();
    reply["choices"][0]["message"]["content"] = json!("synthetic reply".repeat(100));
    fixture.mock.push(Json(reply.clone()).into_response());
    assert_eq!(
        fixture
            .turn(request.clone())
            .await
            .json::<Value>()
            .await
            .unwrap()["choices"],
        reply["choices"]
    );
    let rows = fixture.log("/api/logs?q=over-capture-limit").await;
    let row = &rows["items"][0];
    assert_eq!(row["requestTruncated"], true);
    assert_eq!(row["responseTruncated"], true);
    assert!(row["bodyBytes"].as_u64().unwrap() <= 2048);

    // Leave metadata readable when a broken body archive prevents capture.
    let bodies = fixture.path().join("openbot-request-bodies");
    fs::rename(&bodies, fixture.path().join("archived-bodies")).unwrap();
    fs::write(&bodies, "unusable body directory").unwrap();
    request["openbotRequestId"] = json!("body-directory-failure");
    fixture.mock.push(Json(reply).into_response());
    assert_eq!(fixture.turn(request).await.status(), StatusCode::OK);
    let rows = fixture.log("/api/logs?q=body-directory-failure").await;
    assert_eq!(rows["items"][0]["hasResponse"], false);
    assert_eq!(rows["items"][0]["bodyWriteFailed"], true);
}

#[tokio::test]
async fn zero_anthropic_cache_is_distinct_from_missing_telemetry() {
    let fixture = Fixture::new().await;
    file(
        fixture.path(),
        "openbot-logs.json",
        json!({"loggingEnabled":true}),
    );
    for (id, usage) in [
        (
            "known-zero",
            json!({"input_tokens":20,"output_tokens":5,"cache_read_input_tokens":0,"cache_creation_input_tokens":2}),
        ),
        (
            "unknown-cache",
            json!({"input_tokens":20,"output_tokens":5}),
        ),
    ] {
        fixture.mock.push(Json(json!({
            "id":"fixture-anthropic","type":"message","role":"assistant","model":"synthetic","stop_reason":"end_turn",
            "content":[{"type":"text","text":"ready"}],"usage":usage
        })).into_response());
        let mut request = user_request("messages-test:synthetic", false);
        request["openbotRequestId"] = json!(id);
        assert_eq!(fixture.turn(request).await.status(), StatusCode::OK);
    }
    let known = fixture.log("/api/logs?q=known-zero").await;
    assert_eq!(known["items"][0]["cachedTokens"], 0);
    assert_eq!(known["items"][0]["promptTokens"], 22);
    let unknown = fixture.log("/api/logs?q=unknown-cache").await;
    assert!(unknown["items"][0].get("cachedTokens").is_none());
    let usage = fixture.log("/api/logs/usage?provider=messages-test").await;
    assert_eq!(usage["summary"]["cacheKnownInputTokens"], 22);
    assert_eq!(usage["summary"]["cacheUnknownInputTokens"], 20);
    assert_eq!(usage["summary"]["cacheHitRate"], 0.0);
}
