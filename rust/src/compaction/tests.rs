use std::{fs, path::Path, sync::atomic::AtomicUsize};

use axum::{
    Json, Router,
    extract::State as AxumState,
    http::{StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::post,
};
use serde_json::json;
use tokio::sync::Notify;

use crate::{
    auth::CredentialStore,
    logs::Logs,
    server::{self, State},
};

use super::*;

fn model(window: usize) -> Plan {
    Plan {
        window,
        output_tokens: 0,
        routes: Vec::new(),
        routing_key: format!("fixture-{window}"),
    }
}

fn chat(rounds: usize, chars: usize) -> Value {
    let mut messages =
        vec![json!({"role":"system","content":"Preserve rules and current context."})];
    for index in 0..rounds {
        messages.push(json!({"role":"user","content":format!("Decision {index}: use fixture-archive and keep job-{index} pending. {}"," a".repeat(chars/2))}));
        messages.push(json!({"role":"assistant","content":format!("Acknowledged {index}. {}"," b".repeat(chars/2))}));
    }
    messages.push(json!({"role":"user","content":"Keep this pending task and image."}));
    json!({"conversationId":"fixture-thread","messages":messages,"tools":[
        {"type":"function","function":{"name":"staged_tool","parameters":{"type":"object"}}}
    ]})
}

fn fallback(compactor: &Compactor, body: &Value, target: usize) -> Option<(Vec<Value>, Compacted)> {
    compactor
        .plan(
            body.as_object().unwrap(),
            Some("fixture-thread"),
            Some("fixture-bot"),
            model(target),
        )
        .unwrap()
}

#[test]
fn receipt_rebases_only_one_exact_suffix_after_history_eviction() {
    let old = (0..9)
        .map(|i| json!({"role":"user","content":i}))
        .collect::<Vec<_>>();
    let receipt = Receipt::of(&old[..7]);
    assert_eq!(receipt.matching(&old), Some(receipt.clone()));
    assert_eq!(receipt.matching(&old[2..]), Some(Receipt::of(&old[2..7])));
    assert!(receipt.matching(&old[5..]).is_none());
    let mut edited = old[2..].to_vec();
    edited[1]["content"] = json!(-1);
    assert!(receipt.matching(&edited).is_none());
    let repeating = [json!(1), json!(2), json!(3)];
    let ambiguous = std::iter::once(json!(0))
        .chain(repeating.iter().cloned())
        .chain(repeating.iter().cloned())
        .collect::<Vec<_>>();
    assert!(Receipt::of(&ambiguous).matching(&ambiguous[1..]).is_none());
}

#[test]
fn compaction_preserves_tool_groups_and_stays_stable_after_restart() {
    let dir = tempfile::tempdir().unwrap();
    let mut chat = chat(33, 750);
    let messages = chat["messages"].as_array_mut().unwrap();
    let latest = vec![
        json!({"role":"user","content":[{"type":"text","text":"Newest request"},{"type":"image_url","image_url":{"url":"https://example.test/image.png"}}]}),
        json!({"role":"assistant","content":null,"tool_calls":[{"id":"call_first","function":{"name":"one","arguments":"{}"}},{"id":"call_second","function":{"name":"two","arguments":"{}"}}]}),
        json!({"role":"tool","tool_call_id":"call_first","content":"first result"}),
        json!({"role":"tool","tool_call_id":"call_second","content":"second result"}),
    ];
    messages.pop();
    messages.extend(latest.clone());
    let compactor = Compactor::new(dir.path()).unwrap();
    let (first, measured) = fallback(&compactor, &chat, 16_000).unwrap();
    assert_eq!(first[0], chat["messages"][0]);
    assert_eq!(first[first.len() - latest.len()..], latest);
    assert!(measured.measurement.estimated_input_tokens > 13_600);
    assert!(measured.measurement.estimated_retained_tokens <= 5_600);
    assert!(first[1]["content"].as_str().unwrap().contains("pending"));
    assert!(Compactor::new(dir.path()).is_err());
    drop(compactor);

    let restarted = Compactor::new(dir.path()).unwrap();
    chat["messages"].as_array_mut().unwrap().last_mut().unwrap()["content"] =
        json!("second result with updated suffix");
    let (again, second) = fallback(&restarted, &chat, 16_000).unwrap();
    assert_eq!(first[..first.len() - 1], again[..again.len() - 1]);
    assert_eq!(
        measured.measurement.covered_messages,
        second.measurement.covered_messages
    );
}

#[test]
fn handles_a_smaller_model_window_and_leaves_native_responses_input_intact() {
    let dir = tempfile::tempdir().unwrap();
    let compactor = Compactor::new(dir.path()).unwrap();
    let chat = chat(20, 385);
    let before = History::from_body(chat.as_object().unwrap())
        .unwrap()
        .tokens_from(0);
    assert!((2100..5100).contains(&before));
    assert!(fallback(&compactor, &chat, 20_000).is_none());
    let (_, measured) = fallback(&compactor, &chat, 6000).unwrap();
    assert_eq!(measured.measurement.target_input_tokens, 2100);
    assert!(measured.measurement.estimated_retained_tokens <= 2100);
    let mut native = chat;
    native["input"] = json!([{"role":"user","content":"already encoded"}]);
    assert!(fallback(&compactor, &native, 6000).is_none());
}

#[test]
fn imports_only_a_matching_legacy_brief_and_preserves_the_source_files() {
    let dir = tempfile::tempdir().unwrap();
    let mut chat = chat(32, 850);
    let rest = &chat["messages"].as_array().unwrap()[1..];
    let receipt = Receipt::of(&rest[..55]);
    let hash = ring::digest::digest(
        &ring::digest::SHA1_FOR_LEGACY_USE_ONLY,
        b"conversation\0fixture-thread",
    );
    let hash: String = hash.as_ref()[..8]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    let old = json!({
        "lastContextTokens":16000,"compactedPrefixCount":receipt.count,"compactedPrefixHash":receipt.hash,
        "compactedPrefixHashes":receipt.messages,"summaryForDropped":receipt.count,"summaryPrefixHash":receipt.hash,
        "summaryPrefixHashes":receipt.messages,"summary":"known saved decision","summarySource":"fixture:staged-model",
        "summaryJob":{"version":2,"cursor":20}
    }).to_string();
    let folder = dir.path().join("openbot/compact-state");
    fs::create_dir_all(&folder).unwrap();
    let legacy = folder.join(format!("{hash}.json"));
    fs::write(&legacy, &old).unwrap();
    let compactor = Compactor::new(dir.path()).unwrap();
    let (messages, readback) = fallback(&compactor, &chat, 16_000).unwrap();
    assert_eq!(
        readback.measurement.summary_source.as_deref(),
        Some("fixture:staged-model")
    );
    assert_eq!(
        messages[1]["content"].as_str().unwrap().lines().last(),
        Some("known saved decision")
    );
    chat["messages"][5]["content"] = json!("Changed saved history");
    let (new, readback) = fallback(&compactor, &chat, 16_000).unwrap();
    assert!(readback.measurement.summary_source.is_none());
    assert!(
        !new[1]["content"]
            .as_str()
            .unwrap()
            .contains("known saved decision")
    );
    assert_eq!(fs::read_to_string(legacy).unwrap(), old);
}

#[test]
fn respects_tool_boundaries_and_preserves_prior_identifiers() {
    let mut request = chat(3, 90);
    let messages = request["messages"].as_array_mut().unwrap();
    messages.insert(
        2,
        json!({"role":"assistant","tool_calls":[{"id":"call_a"},{"id":"call_b"}]}),
    );
    messages.insert(
        3,
        json!({"role":"tool","tool_call_id":"call_a","content":"result A"}),
    );
    messages.insert(
        4,
        json!({"role":"tool","tool_call_id":"call_b","content":"result B"}),
    );
    let history = History::from_body(request.as_object().unwrap()).unwrap();
    assert!(history.allows_cut(1));
    assert!(!history.allows_cut(2));
    assert!(!history.allows_cut(3));
    assert!(history.allows_cut(4));
    assert!(transcript(history.rest).contains("tool result for call_b"));
    let next = keep_identifiers("Keep result A.".into(),"## Facts and identifiers\n- Prior `fixture-archive`\n- `job-id` pending\n## Decisions\n- `optional`").unwrap();
    assert!(
        next.contains("fixture-archive") && next.contains("job-id") && !next.contains("optional")
    );
    assert_eq!(chunk_end("é🙂tail", 0, 2), "é🙂".len());
}

struct Listener {
    base: String,
    task: JoinHandle<io::Result<()>>,
}

impl Listener {
    async fn start(app: impl FnOnce(String) -> Router) -> Self {
        let socket = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let authority = socket.local_addr().unwrap().to_string();
        Self {
            base: format!("http://{authority}"),
            task: tokio::spawn(axum::serve(socket, app(authority)).into_future()),
        }
    }

    async fn stop(self) {
        self.task.abort();
        let _ = self.task.await;
    }
}

#[derive(Default)]
struct Upstream {
    chats: Mutex<Vec<Value>>,
    summaries: Mutex<Vec<(String, Value)>>,
    completed: AtomicUsize,
    returned_first: Notify,
    wait_second: Notify,
    block_second: AtomicBool,
    overflow_primary: AtomicBool,
}

fn response(text: &str) -> Response {
    Json(json!({
        "id":"fixture-reply","choices":[{"index":0,"message":{"role":"assistant","content":text},"finish_reason":"stop"}],
        "usage":{"prompt_tokens":1500,"total_tokens":1512,"completion_tokens":12,"prompt_cache_hit_tokens":1400}
    })).into_response()
}

async fn upstream(
    AxumState(mock): AxumState<Arc<Upstream>>,
    uri: Uri,
    Json(body): Json<Value>,
) -> Response {
    let is_summary = body["messages"][0]["content"]
        .as_str()
        .is_some_and(|text| text.starts_with("Summarize the supplied transcript."));
    if !is_summary {
        mock.chats.lock().unwrap().push(body);
        return response("chat worked");
    }
    let index = {
        let mut data = mock.summaries.lock().unwrap();
        data.push((uri.path().to_owned(), body.clone()));
        data.len()
    };
    if uri.path().starts_with("/primary") && mock.overflow_primary.load(Ordering::SeqCst) {
        return (StatusCode::BAD_REQUEST,Json(json!({"error":{"code":"context_length_exceeded","message":"fixture context limit"}}))).into_response();
    }
    if index >= 2 && mock.block_second.load(Ordering::SeqCst) {
        mock.wait_second.notified().await;
    }
    mock.completed.fetch_add(1, Ordering::SeqCst);
    mock.returned_first.notify_one();
    response(
        "## Task state\nContinue the fixture.\n## Decisions\nKeep result A.\n## Facts and identifiers\n- `fixture-archive`\n- `job-id` still needs work.\n## Open loops\nFinish the remaining task.\n## Speaker roster\nUser and assistant.",
    )
}

fn save_fixture(root: &Path, upstream: &str, window: usize, fallback: bool) {
    let mut providers = vec![json!({"id":"fixture","origin":format!("{upstream}/primary")})];
    let mut models = vec![
        json!({"id":"fixture:staged-model","providerId":"fixture","slug":"staged-model",
        "contextTokens":window,"maxOutputTokens":4096,"reasoningLevels":["default","high"],"activeReasoning":"high"}),
    ];
    if fallback {
        providers.push(json!({"id":"second","origin":format!("{upstream}/fallback")}));
        models.push(json!({"id":"second:deepseek-v4.1-flash","providerId":"second","slug":"deepseek-v4.1-flash",
            "contextTokens":32000,"maxOutputTokens":2048}));
    }
    fs::write(
        root.join("openbot-plan.json"),
        json!({"kind":"custom","catalog":{"providers":providers,"models":models,"bindings":[]}})
            .to_string(),
    )
    .unwrap();
    fs::write(
        root.join("secrets.json"),
        r#"{"providers":{"fixture":"fixture-only","second":"fallback-fixture"}}"#,
    )
    .unwrap();
    fs::write(root.join("openbot-mode"), "custom\n").unwrap();
    fs::write(root.join("openbot-logs.json"), r#"{"loggingEnabled":true}"#).unwrap();
}

async fn start_runtime(root: &Path) -> (Listener, Compactor, Logs) {
    let state = State::new(root.into(), false).unwrap();
    let compactor = Compactor::new(root).unwrap();
    let logs = Logs::new(root, true);
    let hop = Hop::new(state.clone(), CredentialStore::new(root, false).unwrap())
        .unwrap()
        .with_compactor(Some(compactor.clone()))
        .with_logs(logs.clone());
    let api = logs.clone().routes();
    let listener = Listener::start(move |authority| {
        server::router(state, authority.into(), [hop.routes(), api])
    })
    .await;
    (listener, compactor, logs)
}

async fn turn(listener: &Listener, request: &Value) -> Value {
    let response = reqwest::Client::new()
        .post(format!("{}/v1/chat/completions", listener.base))
        .json(request)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap();
    response.json().await.unwrap()
}

fn read_state(root: &Path) -> Value {
    fs::read_dir(root.join("openbot/compact-state"))
        .ok()
        .into_iter()
        .flatten()
        .flatten()
        .find(|entry| entry.file_name().to_string_lossy().ends_with(".rust.json"))
        .and_then(|entry| fs::read(entry.path()).ok())
        .and_then(|data| serde_json::from_slice(&data).ok())
        .unwrap_or_default()
}

async fn wait_for(mut ready: impl FnMut() -> bool) {
    tokio::time::timeout(Duration::from_secs(4), async {
        while !ready() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn http_compaction_uses_direct_fallback_only_on_overflow_and_reads_saved_brief() {
    let dir = tempfile::tempdir().unwrap();
    let mock = Arc::new(Upstream::default());
    mock.overflow_primary.store(true, Ordering::SeqCst);
    let shared = mock.clone();
    let upstream =
        Listener::start(move |_| Router::new().fallback(post(upstream)).with_state(shared)).await;
    save_fixture(dir.path(), &upstream.base, 16_000, true);
    let (runtime, compactor, logs) = start_runtime(dir.path()).await;
    let mut request = chat(32, 900);
    request["model"] = json!("fixture:staged-model");
    let completion = turn(&runtime, &request).await;
    assert_eq!(
        completion["choices"][0]["message"]["content"],
        "chat worked"
    );
    wait_for(|| read_state(dir.path())["brief"].is_object()).await;
    let body = read_state(dir.path());
    assert_eq!(body["brief"]["source"], "second:deepseek-v4.1-flash");
    assert!(body["pending"].is_null());
    turn(&runtime, &request).await;
    {
        let chats = mock.chats.lock().unwrap();
        assert_eq!(chats.len(), 2);
        assert_eq!(chats[0]["messages"][0], request["messages"][0]);
        assert!(
            chats[0]["messages"][1]["content"]
                .as_str()
                .unwrap()
                .contains("earlier excerpts")
        );
        assert!(
            chats[1]["messages"][1]["content"]
                .as_str()
                .unwrap()
                .contains("fixture-archive")
        );
        assert_eq!(chats[1]["tools"], request["tools"]);
    }
    {
        let summaries = mock.summaries.lock().unwrap();
        assert!(summaries.len() >= 2 && summaries.len() % 2 == 0);
        assert!(
            summaries
                .chunks_exact(2)
                .all(|pair| pair[0].0.starts_with("/primary/")
                    && pair[1].0.starts_with("/fallback/"))
        );
        assert_eq!(summaries[0].1["reasoning_effort"], "high");
        assert!(
            summaries
                .iter()
                .all(|(_, body)| body.get("openbot_internal_model").is_none())
        );
    }
    logs.wait_for_writes().await;
    let usage = reqwest::get(format!("{}/api/logs/usage", runtime.base))
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert!(
        usage["byPurpose"]
            .as_array()
            .unwrap()
            .iter()
            .any(|row| row["key"] == "compaction")
    );
    let logs = reqwest::get(format!("{}/api/logs", runtime.base))
        .await
        .unwrap()
        .json::<Value>()
        .await
        .unwrap();
    assert!(
        logs["items"]
            .as_array()
            .unwrap()
            .iter()
            .any(|row| row["purpose"] == "chat"
                && row["compaction"]["summarySource"] == "second:deepseek-v4.1-flash")
    );
    compactor.stop();
    runtime.stop().await;
    upstream.stop().await;
}

#[tokio::test]
async fn completion_reserve_compacts_before_the_normal_trigger_and_keeps_the_latest_turn() {
    let dir = tempfile::tempdir().unwrap();
    let mock = Arc::new(Upstream::default());
    let shared = mock.clone();
    let upstream =
        Listener::start(move |_| Router::new().fallback(post(upstream)).with_state(shared)).await;
    let window = 6400;
    save_fixture(dir.path(), &upstream.base, window, false);
    let (runtime, compactor, _) = start_runtime(dir.path()).await;

    let mut request = chat(20, 400);
    request["model"] = json!("fixture:staged-model");
    request["max_tokens"] = json!(4096);
    let before = request_tokens(request.as_object().unwrap()).unwrap();
    assert!(before < window * 85 / 100 && before + 4096 > window);
    turn(&runtime, &request).await;

    let single = json!({"model":"fixture:staged-model","max_tokens":4096,
        "conversationId":"one-turn",
        "messages":[{"role":"system","content":"x".repeat(12_000)},
                    {"role":"user","content":"Keep my question and reply briefly."}]});
    let minimum = request_tokens(single.as_object().unwrap()).unwrap();
    assert!(minimum + 4096 > window && minimum < window);
    turn(&runtime, &single).await;

    {
        let chats = mock.chats.lock().unwrap();
        let compacted = &chats[0];
        let after = request_tokens(compacted.as_object().unwrap()).unwrap();
        assert!(after < before && after + 4096 <= window);
        assert_eq!(compacted["max_tokens"], 4096);
        assert_eq!(compacted["messages"][0], request["messages"][0]);
        assert_eq!(
            compacted["messages"].as_array().unwrap().last(),
            request["messages"].as_array().unwrap().last()
        );
        assert_eq!(chats[1]["messages"], single["messages"]);
        assert_eq!(chats[1]["max_tokens"], window - minimum);
    }
    compactor.stop();
    runtime.stop().await;
    upstream.stop().await;
}

#[tokio::test]
async fn completed_chunk_resumes_after_restart_without_resending_it() {
    let dir = tempfile::tempdir().unwrap();
    let mock = Arc::new(Upstream::default());
    mock.block_second.store(true, Ordering::SeqCst);
    let shared = mock.clone();
    let upstream =
        Listener::start(move |_| Router::new().fallback(post(upstream)).with_state(shared)).await;
    save_fixture(dir.path(), &upstream.base, 4_000, false);
    let (runtime, compactor, _) = start_runtime(dir.path()).await;
    let mut input = chat(28, 640);
    input["model"] = json!("fixture:staged-model");
    turn(&runtime, &input).await;
    wait_for(|| {
        read_state(dir.path())["pending"]["cursor"]
            .as_u64()
            .is_some_and(|cursor| cursor > 0)
            && mock.summaries.lock().unwrap().len() == 2
    })
    .await;
    let first = read_state(dir.path());
    let sent = mock.summaries.lock().unwrap()[1].1["messages"][1]["content"].clone();
    compactor.stop();
    runtime.stop().await;
    drop(compactor);
    wait_for(|| Compactor::new(dir.path()).is_ok()).await;
    assert_eq!(
        read_state(dir.path())["pending"]["cursor"],
        first["pending"]["cursor"]
    );

    mock.block_second.store(false, Ordering::SeqCst);
    let (runtime, compactor, _) = start_runtime(dir.path()).await;
    turn(&runtime, &input).await;
    wait_for(|| read_state(dir.path())["brief"].is_object()).await;
    assert_eq!(
        mock.summaries.lock().unwrap()[2].1["messages"][1]["content"],
        sent
    );
    assert!(read_state(dir.path())["pending"].is_null());
    assert!(
        fs::read_dir(dir.path().join("openbot/compact-state"))
            .unwrap()
            .flatten()
            .all(|entry| !entry.file_name().to_string_lossy().ends_with(".transcript"))
    );
    compactor.stop();
    runtime.stop().await;
    upstream.stop().await;
}

#[test]
fn editing_history_or_switching_effort_retires_only_the_superseded_checkpoint() {
    let dir = tempfile::tempdir().unwrap();
    let service = Compactor::new(dir.path()).unwrap();
    let mut input = chat(22, 600);
    let plans = |effort| Plan {
        window: 6000,
        output_tokens: 0,
        routes: vec![SummaryRoute {
            model_id: "fixture:staged-model".into(),
            origin: "http://127.0.0.1:1".into(),
            effort,
            window: 6000,
            output: 1500,
        }],
        routing_key: format!("{effort:?}"),
    };
    let inspect = |input: &Value, effort| {
        service
            .plan(
                input.as_object().unwrap(),
                Some("fixture-thread"),
                Some("fixture-bot"),
                plans(effort),
            )
            .unwrap()
            .unwrap()
            .1
            .job
            .unwrap()
    };
    let first = inspect(&input, Effort::Low);
    let repeated = inspect(&input, Effort::Low);
    assert_eq!(first.id, repeated.id);
    let scoped_path = |job: &Job| {
        dir.path()
            .join("openbot/compact-state")
            .join(format!("{}.{}.rust.transcript", job.key, job.id))
    };
    assert!(scoped_path(&first).is_file());
    let changed_effort = inspect(&input, Effort::High);
    assert_ne!(changed_effort.id, first.id);
    assert!(!scoped_path(&first).exists());
    assert!(scoped_path(&changed_effort).is_file());

    input["messages"][6]["content"] = json!("Updated quoted conversation data.");
    let edited = inspect(&input, Effort::High);
    assert_ne!(edited.id, changed_effort.id);
    assert!(!scoped_path(&changed_effort).exists());
    assert!(scoped_path(&edited).is_file());
    assert_eq!(read_state(dir.path())["pending"]["id"], edited.id);
}

#[tokio::test]
async fn only_new_checkpoint_is_committed_after_a_concurrent_request_invalidates_old_history() {
    let dir = tempfile::tempdir().unwrap();
    let service = Compactor::new(dir.path()).unwrap();
    let mut input = chat(24, 700);
    let plans = || Plan {
        window: 6500,
        output_tokens: 0,
        routes: vec![SummaryRoute {
            model_id: "fixture:staged-model".into(),
            origin: "http://127.0.0.1:1".into(),
            effort: Effort::Low,
            window: 6500,
            output: 1500,
        }],
        routing_key: "direct-fixture".into(),
    };
    let prepare = |input: &Value| {
        service
            .plan(
                input.as_object().unwrap(),
                Some("fixture-thread"),
                Some("fixture-bot"),
                plans(),
            )
            .unwrap()
            .unwrap()
            .1
            .job
            .unwrap()
    };
    let old = prepare(&input);
    let progress = service
        .0
        .store
        .load(&old.key)
        .unwrap()
        .unwrap()
        .pending
        .unwrap();
    input["messages"][8]["content"] = json!("Reordered or edited history.");
    let new = prepare(&input);
    let stale = service
        .checkpoint(&old, progress, Some("fixture:staged-model".into()))
        .await
        .unwrap();
    assert!(!stale);
    let after = service.0.store.load(&new.key).unwrap().unwrap();
    assert!(after.brief.is_none());
    assert_eq!(after.pending.unwrap().id, new.id);
}
