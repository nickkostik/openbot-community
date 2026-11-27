use super::*;

const SETTINGS: &str = "/api/settings/delivery";

fn recording(fixture: &Fixture) {
    file(
        fixture.path(),
        "openbot-logs.json",
        json!({"loggingEnabled":true,"logBodies":false}),
    );
}

async fn set(fixture: &Fixture, body: Value) -> Value {
    fixture
        .client
        .put(format!("{}{SETTINGS}", fixture.rust.base))
        .json(&body)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap()
}

fn user(model: &str, stream: bool, epoch: &str) -> Value {
    let mut body = user_request(model, stream);
    body["messages"][1]["content"] = json!("<user_query>Check the fixture.</user_query>");
    body["conversationId"] = json!("private-fixture-thread");
    body["epochId"] = json!(epoch);
    body["tools"] = json!([{"type":"function","function":{"name":"SendToUser",
        "parameters":{"type":"object","properties":{"type":{"type":"string"}}}}}]);
    body
}

fn call() -> Value {
    json!({"id":"call-delivery-fixture","type":"function",
        "function":{"name":"SendToUser","arguments":r#"{"type":"text"}"#}})
}

fn silent() -> Value {
    let mut result = chat_body();
    result["choices"][0]["message"]["reasoning_content"] = json!("first reasoning");
    result["usage"] = json!({"prompt_tokens":20,"completion_tokens":2,"total_tokens":22,
        "prompt_tokens_details":{"cached_tokens":4}});
    result
}

fn delivered() -> Value {
    json!({"id":"followup-fixture","choices":[{"index":0,"finish_reason":"tool_calls",
        "message":{"role":"assistant","content":null,"reasoning_content":" second reasoning","tool_calls":[call()]}}],
        "usage":{"prompt_tokens":22,"completion_tokens":3,"total_tokens":25,
        "prompt_tokens_details":{"cached_tokens":9}}})
}

fn has_nudge(value: &Value) -> bool {
    value
        .to_string()
        .contains("[SAND_HIDDEN_PROMPT]Your previous turn")
}

async fn json_reply(fixture: &Fixture, body: Value) -> Value {
    fixture
        .turn(body)
        .await
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap()
}

#[tokio::test]
async fn settings_hot_reload_dry_run_then_enforce_with_exact_usage_and_one_retry() {
    let fixture = Fixture::new().await;
    recording(&fixture);
    assert_eq!(fixture.log(SETTINGS).await["mode"], "off");
    let body = user("opencode:synthetic", false, "epoch-switch");

    fixture.mock.push(Json(silent()).into_response());
    assert_eq!(json_reply(&fixture, body.clone()).await, silent());

    let dry = set(&fixture, json!({"mode":"dry-run","layers":{"l2":true}})).await;
    assert_eq!(dry["layers"], json!({"l1":false,"l2":true,"l3":false}));
    fixture.mock.push(Json(silent()).into_response());
    assert_eq!(json_reply(&fixture, body.clone()).await, silent());
    let rows = fixture.log("/api/logs").await;
    let observed = rows["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["injection"]["injectionMode"] == "dry-run")
        .unwrap();
    assert_eq!(
        observed["injection"]["injectionFamilies"],
        json!(["l2.reply-nudge"])
    );
    assert_eq!(observed["injection"]["l2Attempted"], false);

    let enforced = set(&fixture, json!({"mode":"enforce","layers":{}})).await;
    assert_eq!(enforced["layers"], dry["layers"]);
    fixture.mock.push(Json(silent()).into_response());
    fixture.mock.push(Json(delivered()).into_response());
    let reply = json_reply(&fixture, body.clone()).await;
    assert_eq!(reply["id"], "fixture-reply");
    assert_eq!(reply["choices"][0]["message"]["content"], "ready");
    assert_eq!(
        reply["choices"][0]["message"]["reasoning_content"],
        "first reasoning second reasoning"
    );
    assert_eq!(
        reply["choices"][0]["message"]["tool_calls"],
        json!([call()])
    );
    assert_eq!(reply["choices"][0]["finish_reason"], "tool_calls");
    assert_eq!(reply["usage"]["total_tokens"], 47);
    assert_eq!(reply["usage"]["prompt_tokens_details"]["cached_tokens"], 13);

    fixture.mock.push(Json(silent()).into_response());
    assert_eq!(json_reply(&fixture, body).await, silent());
    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 5);
        for index in [0, 1, 2, 4] {
            assert_eq!(seen[index].body["messages"], seen[0].body["messages"]);
            assert!(seen[index].body.get("epochId").is_none());
        }
        assert_eq!(seen[3].path, seen[2].path);
        for field in [
            "authorization",
            "x-opencode-session",
            "x-openbot-version",
            "accept",
            "user-agent",
        ] {
            assert_eq!(seen[3].headers[field], seen[2].headers[field]);
        }
        assert_eq!(seen[3].body["max_tokens"], 2048);
        assert!(has_nudge(&seen[3].body));
        assert_eq!(seen[3].body["messages"][1], seen[2].body["messages"][1]);
    });
    let rows = fixture.log("/api/logs").await;
    let repaired = rows["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["injection"]["l2Outcome"] == "second-success")
        .unwrap();
    assert_eq!(repaired["totalTokens"], 47);
    assert_eq!(repaired["cachedTokens"], 13);
    assert_eq!(repaired["attemptCount"], 2);
    assert_eq!(repaired["injection"]["l2PromptTokensActual"], 22);
    assert_eq!(repaired["injection"]["heldTerminalBytes"], 0);
    assert!(repaired.get("hasRequest").is_some());
    let stats = fixture.log("/api/logs/stats").await;
    assert_eq!(stats["injection"]["extraCalls"], 1);
    assert_eq!(stats["injection"]["applied"], 1);
}

#[tokio::test]
async fn streaming_delivers_first_output_while_retry_is_in_flight_and_has_one_terminal() {
    let fixture = Fixture::new().await;
    set(&fixture, json!({"mode":"enforce","layers":{"l2":true}})).await;
    let first = events(&[
        json!({"id":"first-stream","model":"synthetic","choices":[{"index":0,"delta":{"content":"first text "},"finish_reason":null}]}),
        json!({"id":"first-stream","model":"synthetic","choices":[{"index":0,"delta":{"content":"terminal text"},"finish_reason":"stop"}]}),
        json!({"id":"first-stream","model":"synthetic","choices":[],"usage":{"prompt_tokens":20,"completion_tokens":2,"total_tokens":22}}),
    ]) + "data: [DONE]\n\n";
    fixture.mock.push(sse_body(Body::from(first), &[]));
    let (release, next) = tokio::sync::oneshot::channel::<()>();
    let second = Body::from_stream(futures_util::stream::once(async move {
        next.await.unwrap();
        let completion = events(&[
            json!({"id":"second-stream","model":"synthetic","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,
                "id":"tool-stream","type":"function","function":{"name":"SendToUser","arguments":"{}"}}]},"finish_reason":null}]}),
            json!({"id":"second-stream","model":"synthetic","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],
                "usage":{"prompt_tokens":22,"completion_tokens":3,"total_tokens":25}}),
        ]) + "data: [DONE]\n\n";
        Ok::<_, Infallible>(Bytes::from(completion))
    }));
    fixture.mock.push(sse_body(second, &[]));
    let mut reply = fixture
        .turn(user("opencode:synthetic", true, "stream-epoch"))
        .await;
    let prefix = tokio::time::timeout(Duration::from_secs(2), reply.chunk())
        .await
        .expect("initial output must arrive before follow-up returns")
        .unwrap()
        .unwrap();
    let prefix = String::from_utf8(prefix.to_vec()).unwrap();
    assert!(prefix.contains("first text"));
    assert!(!prefix.contains("\"finish_reason\":\"stop\""));
    release.send(()).unwrap();
    let all = prefix + &reply.text().await.unwrap();
    assert_eq!(all.matches("[DONE]").count(), 1);
    assert_eq!(all.matches("tool-stream").count(), 1);
    assert_eq!(all.matches("terminal text").count(), 1);
    assert!(!all.contains("second-stream"));
    let frames = decoded(&all);
    let finishes: Vec<_> = frames
        .iter()
        .filter_map(|frame| frame["choices"][0]["finish_reason"].as_str())
        .collect();
    assert_eq!(finishes, ["tool_calls"]);
    assert_eq!(frames.last().unwrap()["usage"]["total_tokens"], 47);
    fixture.mock.inspect(|seen| assert_eq!(seen.len(), 2));
}

#[tokio::test]
async fn followup_unavailable_preserves_first_completion_and_arms_one_next_request_redrive() {
    let fixture = Fixture::new().await;
    recording(&fixture);
    set(
        &fixture,
        json!({"mode":"enforce","layers":{"l2":true,"l3":true}}),
    )
    .await;
    let body = user("opencode:synthetic", false, "fallback-epoch");
    fixture.mock.push(Json(silent()).into_response());
    fixture.mock.push(
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":{"message":"fixture overloaded"}})),
        )
            .into_response(),
    );
    assert_eq!(json_reply(&fixture, body.clone()).await, silent());

    for _ in 0..2 {
        fixture.mock.push(Json(delivered()).into_response());
        assert_eq!(json_reply(&fixture, body.clone()).await, delivered());
    }
    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 4);
        assert!(!has_nudge(&seen[0].body));
        assert!(has_nudge(&seen[1].body));
        assert!(has_nudge(&seen[2].body));
        assert!(!has_nudge(&seen[3].body));
    });
    let stats = fixture.log("/api/logs/stats").await;
    assert_eq!(stats["injection"]["fallbackOriginalTerminal"], 1);
    assert_eq!(stats["injection"]["extraCalls"], 1);
    let rows = fixture.log("/api/logs").await;
    let fallback = rows["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["injection"]["l2Outcome"] == "fallback-original-terminal")
        .unwrap();
    assert_eq!(fallback["attempts"][1]["status"], 503);
    assert_eq!(fallback["status"], 200);
}

#[tokio::test]
async fn blocked_contexts_do_not_generate_or_change_prompts_and_multimodal_opening_keeps_parts() {
    let fixture = Fixture::new().await;
    set(&fixture, json!({"mode":"enforce"})).await;
    let mut group = user("opencode:synthetic", false, "group");
    group["messages"][1]["content"] =
        json!("<user_query>[Group chat: \"Fleet\"] Hello</user_query>");
    group["openbotChatType"] = json!("dm");
    let mut routine = user("opencode:synthetic", false, "routine");
    routine["messages"][1]["content"] = json!("<user_query> [routine] Check logs.</user_query>");
    let mut hidden = user("opencode:synthetic", false, "hidden");
    hidden["messages"]
        .as_array_mut()
        .unwrap()
        .push(json!({"role":"user","content":"[SAND_HIDDEN_PROMPT] Internal continuation."}));
    let mut auxiliary = user("opencode:synthetic", false, "aux");
    auxiliary["openbot_internal_auxiliary"] = json!(true);
    let mut no_tools = user("opencode:synthetic", false, "tools");
    no_tools["tools"] = json!([]);
    for blocked in [group, routine, hidden, auxiliary, no_tools] {
        fixture.mock.push(Json(silent()).into_response());
        assert_eq!(json_reply(&fixture, blocked).await, silent());
    }

    let mut opening = user("opencode:synthetic", false, "opening");
    opening["opening"] = json!(true);
    opening["messages"][1]["content"] = json!([
        {"type":"text","text":"<user_query>Look at this image.</user_query>"},
        {"type":"image_url","image_url":{"url":"data:image/png;base64,AAAA"}}
    ]);
    fixture.mock.push(Json(delivered()).into_response());
    assert_eq!(json_reply(&fixture, opening.clone()).await, delivered());
    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 6);
        assert!(seen[..5].iter().all(|row| !has_nudge(&row.body)));
        assert_eq!(
            seen[5].body["messages"][1]["content"][1],
            opening["messages"][1]["content"][1]
        );
        assert!(
            seen[5].body["messages"][1]["content"][2]["text"]
                .as_str()
                .unwrap()
                .starts_with("<system_reminder>")
        );
    });
}

#[tokio::test]
async fn responses_and_messages_followups_keep_protocol_and_selected_effort() {
    let fixture = Fixture::new().await;
    set(&fixture, json!({"mode":"enforce","layers":{"l2":true}})).await;
    fixture.settings(json!({"botId":PROFILE,"reasoningModelId":"opencode-2:synthetic","reasoningEffort":"high"})).await;
    fixture.mock.push(Json(responses_body()).into_response());
    fixture.mock.push(Json(json!({"id":"retry-responses","status":"completed","model":"synthetic",
        "output":[{"type":"function_call","call_id":"fixture-responses-send","name":"SendToUser","arguments":"{}"}]})).into_response());
    let responses = json_reply(&fixture, user("opencode-2:synthetic", false, "responses")).await;
    assert_eq!(
        responses["choices"][0]["message"]["tool_calls"][0]["id"],
        "fixture-responses-send"
    );

    fixture.mock.push(
        Json(
            json!({"id":"fixture-msg","type":"message","role":"assistant","model":"synthetic",
        "content":[{"type":"text","text":"first"}],"stop_reason":"end_turn"}),
        )
        .into_response(),
    );
    fixture.mock.push(Json(json!({"id":"fixture-msg-nudge","type":"message","role":"assistant","model":"synthetic",
        "content":[{"type":"tool_use","id":"fixture-messages-send","name":"SendToUser","input":{}}],"stop_reason":"tool_use"})).into_response());
    let messages = json_reply(&fixture, user("messages-test:synthetic", false, "messages")).await;
    assert_eq!(
        messages["choices"][0]["message"]["tool_calls"][0]["id"],
        "fixture-messages-send"
    );

    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 4);
        assert!(seen[1].path.ends_with("/responses"));
        assert!(seen[3].path.ends_with("/messages"));
        assert_eq!(seen[1].body["reasoning"], seen[0].body["reasoning"]);
        assert_eq!(seen[1].body["reasoning"]["effort"], "high");
        assert_eq!(
            seen[1].body["prompt_cache_key"],
            seen[0].body["prompt_cache_key"]
        );
        assert_eq!(seen[1].body["max_output_tokens"], 256);
        assert_eq!(seen[3].body["max_tokens"], 2048);
        assert!(has_nudge(&seen[1].body) && has_nudge(&seen[3].body));
    });
}

#[tokio::test]
async fn concurrent_turns_share_one_followup_reservation() {
    let fixture = Fixture::new().await;
    set(&fixture, json!({"mode":"enforce","layers":{"l2":true}})).await;
    let body = user("opencode:synthetic", false, "shared-epoch");
    fixture.mock.push(Json(silent()).into_response());
    let (release, wait) = tokio::sync::oneshot::channel::<()>();
    let (started, running) = tokio::sync::oneshot::channel::<()>();
    let mut response = Body::from_stream(futures_util::stream::once(async move {
        started.send(()).unwrap();
        wait.await.unwrap();
        Ok::<_, Infallible>(Bytes::from(delivered().to_string()))
    }))
    .into_response();
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    fixture.mock.push(response);
    fixture.mock.push(Json(silent()).into_response());

    let a = json_reply(&fixture, body.clone());
    let b = async {
        tokio::time::timeout(Duration::from_secs(3), running)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(json_reply(&fixture, body).await, silent());
        release.send(()).unwrap();
    };
    let (a, ()) = tokio::join!(a, b);
    assert_eq!(a["choices"][0]["message"]["tool_calls"], json!([call()]));
    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 3);
        assert_eq!(seen.iter().filter(|row| has_nudge(&row.body)).count(), 1);
    });
}

#[tokio::test]
async fn followup_timeout_drops_partial_second_stream_and_preserves_original_terminal() {
    let fixture = Fixture::new().await;
    recording(&fixture);
    file(
        fixture.path(),
        "openbot-injection.json",
        json!({"mode":"enforce","l2":{"timeoutMs":1000}}),
    );
    let original = events(&[
        json!({"id":"first-timeout","choices":[{"index":0,"delta":{"content":"original"},"finish_reason":null}]}),
        json!({"id":"first-timeout","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}),
    ]) + "data: [DONE]\n\n";
    fixture.mock.push(sse_body(Body::from(original), &[]));
    let partial = Bytes::from(events(&[json!({
        "id":"partial-second","choices":[{"index":0,"delta":{"tool_calls":[{
            "index":0,"id":"unsafe-partial-fixture","type":"function",
            "function":{"name":"SendToUser","arguments":"{"},
        }]},"finish_reason":null}],
    })]));
    use futures_util::StreamExt;
    let upstream = futures_util::stream::iter([Ok::<_, Infallible>(partial)])
        .chain(futures_util::stream::pending());
    fixture
        .mock
        .push(sse_body(Body::from_stream(upstream), &[]));
    let response = tokio::time::timeout(Duration::from_secs(3), async {
        fixture
            .turn(user("opencode:synthetic", true, "timeout"))
            .await
            .text()
            .await
            .unwrap()
    })
    .await
    .unwrap();
    assert_eq!(response.matches("[DONE]").count(), 1);
    assert_eq!(response.matches("original").count(), 1);
    assert!(!response.contains("unsafe-partial-fixture"));
    assert_eq!(
        decoded(&response).last().unwrap()["choices"][0]["finish_reason"],
        "stop"
    );
    let rows = fixture.log("/api/logs").await;
    assert_eq!(rows["items"][0]["attempts"][1]["status"], 504);
    assert_eq!(
        rows["items"][0]["injection"]["l2Outcome"],
        "fallback-original-terminal"
    );
    fixture.mock.inspect(|seen| assert_eq!(seen.len(), 2));
}

#[tokio::test]
async fn prompt_budget_blocks_extra_generation_and_official_mode_never_runs_the_strategy() {
    let fixture = Fixture::new().await;
    recording(&fixture);
    file(
        fixture.path(),
        "openbot-injection.json",
        json!({"mode":"enforce","l2":{"maxAdditionalPromptTokens":8192}}),
    );
    let mut body = user("opencode:synthetic", false, "budget");
    body["messages"][1]["content"] =
        json!(format!("<user_query>{}</user_query>", "x".repeat(40_000)));
    fixture.mock.push(Json(silent()).into_response());
    assert_eq!(json_reply(&fixture, body.clone()).await, silent());
    let rows = fixture.log("/api/logs").await;
    assert_eq!(rows["items"][0]["injection"]["skipReason"], "budget_prompt");

    fs::write(fixture.path().join("openbot-mode"), "official\n").unwrap();
    let official = fixture.turn(body).await;
    assert_eq!(official.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        official.json::<Value>().await.unwrap()["error"]["code"],
        "official_mode"
    );
    fixture.mock.inspect(|seen| assert_eq!(seen.len(), 1));
}

#[tokio::test]
async fn repeated_user_text_starts_a_new_epoch_boundary_and_work_after_ack_selects_closing_send() {
    let fixture = Fixture::new().await;
    set(&fixture, json!({"mode":"enforce","layers":{"l2":true}})).await;
    let mut body = user("opencode:synthetic", false, "same-text-epoch");
    let user_row = body["messages"][1].clone();
    for reply in [silent(), delivered()] {
        fixture.mock.push(Json(reply).into_response());
    }
    assert_eq!(
        json_reply(&fixture, body.clone()).await["choices"][0]["finish_reason"],
        "tool_calls"
    );
    body["messages"].as_array_mut().unwrap().extend([
        json!({"role":"assistant","tool_calls":[call()]}),
        user_row,
        json!({"role":"assistant","tool_calls":[call()]}),
        json!({"role":"assistant","tool_calls":[{"id":"later-work","type":"function","function":{"name":"Read","arguments":"{}"}}]}),
    ]);
    for reply in [silent(), delivered()] {
        fixture.mock.push(Json(reply).into_response());
    }
    assert_eq!(
        json_reply(&fixture, body).await["choices"][0]["finish_reason"],
        "tool_calls"
    );
    fixture.mock.inspect(|seen| {
        assert_eq!(seen.len(), 4);
        assert!(has_nudge(&seen[1].body));
        assert!(
            seen[3].body["messages"].as_array().unwrap().last().unwrap()["content"]
                .as_str()
                .unwrap()
                .starts_with("[SAND_HIDDEN_PROMPT]Your previous turn acknowledged the user")
        );
    });
}
