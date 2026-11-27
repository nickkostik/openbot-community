use super::*;

const PNG: &str =
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jhO0AAAAASUVORK5CYII=";

#[test]
fn messages_preserves_caption_inline_image_and_remote_image_in_order() {
    let fixture = json!({
        "messages":[{"role":"user","content":[
            {"type":"text","text":"Compare these two images."},
            {"type":"image_url","image_url":{"url":format!("data:image/png;base64,{PNG}")}},
            {"type":"image_url","image_url":{"url":"https://images.example.invalid/image.png"}},
        ]}],
        "stop":"END",
        "max_completion_tokens":512
    });
    let native = encode(Protocol::Anthropic, fixture, false, false).unwrap();
    assert_eq!(
        native["messages"][0]["content"],
        json!([
            {"type":"text","text":"Compare these two images."},
            {"type":"image","source":{"type":"base64","media_type":"image/png","data":PNG}},
            {"type":"image","source":{"type":"url","url":"https://images.example.invalid/image.png"}}
        ])
    );
    assert_eq!(native["max_tokens"], 512);
    assert_eq!(native["stop_sequences"], json!(["END"]));
}

#[test]
fn messages_keeps_parallel_results_together_with_matching_ids() {
    let long_id = "ノ".repeat(60);
    let fixture = json!({
        "messages":[
            {"role":"system","content":"Keep the tools in order."},
            {"role":"assistant","tool_calls":[
                {"id":long_id,"function":{"name":"first","arguments":"{\"id\": 7}"}},
                {"id":"short","function":{"name":"second","arguments":"{}"}}
            ]},
            {"role":"tool","tool_call_id":long_id,"content":{"id":7}},
            {"role":"tool","tool_call_id":"short","content":"done"},
            {"role":"user","content":"Continue"},
        ]
    });
    let native = encode(Protocol::Anthropic, fixture, false, false).unwrap();
    let turns = native["messages"].as_array().unwrap();
    assert_eq!(turns.len(), 3);
    assert_eq!(native["system"], "Keep the tools in order.");
    let call = &turns[0]["content"][0];
    let results = turns[1]["content"].as_array().unwrap();
    assert_eq!(results.len(), 2);
    assert!(call["id"].as_str().unwrap().len() <= MAX_TOOL_ID_BYTES);
    assert_eq!(call["id"], results[0]["tool_use_id"]);
    assert_eq!(call["input"], json!({"id":7}));
    assert_eq!(results[0]["content"], "{\"id\":7}");
    assert_eq!(results[1]["tool_use_id"], "short");
    assert_eq!(turns[2]["content"], "Continue");
}

#[test]
fn messages_reports_an_invalid_image_instead_of_sending_an_empty_turn() {
    let fixture = json!({"messages":[{"role":"user","content":[
        {"type":"text","text":"Show me this image"},
        {"type":"image_url","image_url":{"url":"file:///example.png"}}
    ]}]});
    assert_eq!(
        encode(Protocol::Anthropic, fixture, false, false),
        Err("unsupported image URL")
    );
}

#[test]
fn responses_keeps_native_input_and_treats_codex_flags_separately() {
    let input = json!({"input":"existing prompt","instructions":"Retain this",
        "max_tokens":2048,"stream":false,"temperature":0.4});
    let api_key = encode(Protocol::Responses, input.clone(), false, false).unwrap();
    assert_eq!(api_key["input"], "existing prompt");
    assert_eq!(api_key["max_output_tokens"], 2048);
    assert_eq!(api_key["stream"], false);
    let codex = encode(Protocol::Responses, input, false, true).unwrap();
    assert_eq!(codex["input"], "existing prompt");
    assert_eq!(codex["instructions"], "Retain this");
    assert_eq!(codex["stream"], true);
    assert_eq!(codex["store"], false);
    assert!(codex.get("max_output_tokens").is_none());
    assert!(codex.get("temperature").is_none());
}

#[test]
fn responses_preserves_images_tools_and_deepseek_reasoning() {
    let input = json!({
        "messages":[
            {"role":"user","content":[{"type":"text","text":"caption"},
                {"type":"image_url","image_url":{"url":"https://images.example.invalid/a.png","detail":"high"}}]},
            {"role":"assistant","content":"","reasoning_content":"reading",
                "tool_calls":[{"id":"call_1","function":{"name":"tool","arguments":"{}"}}]},
            {"role":"tool","tool_call_id":"call_1","content":"result"}
        ],
        "reasoning_effort":"high"
    });
    let native = encode(Protocol::Responses, input, true, false).unwrap();
    assert_eq!(native["reasoning"]["effort"], "high");
    let rows = native["input"].as_array().unwrap();
    assert_eq!(rows[0]["content"][0]["type"], "input_text");
    assert_eq!(rows[0]["content"][1]["detail"], "high");
    assert_eq!(rows[1]["content"][0]["text"], "reading");
    assert_eq!(rows[3]["call_id"], rows[4]["call_id"]);
    assert_eq!(rows[4]["output"], "result");
}

#[test]
fn chat_completions_preserves_tool_and_object_order() {
    let raw = r#"{"model":"fixture","tools":[{"type":"function","function":{"name":"check","parameters":{"type":"object","properties":{"z":{},"a":{}}}}}],"stream":true}"#;
    let native = encode(
        Protocol::ChatCompletions,
        serde_json::from_str(raw).unwrap(),
        false,
        false,
    )
    .unwrap();
    assert_eq!(native.to_string(), raw);
}
