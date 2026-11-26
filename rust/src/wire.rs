use serde_json::{Map, Value, json};
use url::Url;

use crate::catalog::Protocol;

const MAX_TOOL_ID_BYTES: usize = 64;

pub fn encode(
    protocol: Protocol,
    body: Value,
    replay_reasoning: bool,
    codex: bool,
) -> Result<Value, &'static str> {
    let Value::Object(body) = body else {
        return Err("chat request must be an object");
    };
    match protocol {
        Protocol::ChatCompletions => Ok(Value::Object(body)),
        Protocol::Responses => Ok(Value::Object(responses(&body, replay_reasoning, codex))),
        Protocol::Anthropic => Ok(Value::Object(messages(&body)?)),
    }
}

fn select(source: &Map<String, Value>, fields: &[&str]) -> Map<String, Value> {
    fields
        .iter()
        .filter_map(|key| source.get(*key).map(|value| ((*key).into(), value.clone())))
        .collect()
}

fn items(value: Option<&Value>) -> impl Iterator<Item = &Value> {
    value.and_then(Value::as_array).into_iter().flatten()
}

fn string_payload(value: &Value) -> String {
    match value {
        Value::Null => "\"\"".into(),
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

fn clamp_tool_id(value: &Value) -> String {
    let id = value
        .as_str()
        .filter(|id| !id.is_empty())
        .unwrap_or("call_0");
    if id.len() <= MAX_TOOL_ID_BYTES {
        return id.into();
    }
    let mut end = 56;
    while !id.is_char_boundary(end) {
        end -= 1;
    }
    let mut hash = id.bytes().fold(2_166_136_261u32, |hash, byte| {
        (hash ^ u32::from(byte)).wrapping_mul(16_777_619)
    });
    const ALPHABET: &[u8; 36] = b"0123456789abcdefghijklmnopqrstuvwxyz";
    let mut suffix = [b'0'; 7];
    for digit in suffix.iter_mut().rev() {
        *digit = ALPHABET[(hash % 36) as usize];
        hash /= 36;
    }
    format!("{}_{}", &id[..end], String::from_utf8_lossy(&suffix))
}

fn call_content(call: &Value) -> Option<(&Value, &Value)> {
    let function = &call["function"];
    if !function.is_object() {
        return None;
    }
    Some((&function["name"], &function["arguments"]))
}

fn responses_input(message: &Value) -> Value {
    match &message["content"] {
        Value::Null => Value::String(String::new()),
        Value::Array(parts) => Value::Array(parts.iter().map(|part| match part["type"].as_str() {
            Some("text") => {
                let mut part = part.clone();
                part["type"] = if message["role"] == "assistant" { json!("output_text") } else { json!("input_text") };
                part
            }
            Some("image_url") => json!({
                "type": "input_image",
                "image_url": part["image_url"]["url"].as_str().filter(|s| !s.is_empty())
                    .or(part["url"].as_str()).unwrap_or_default(),
                "detail": part["image_url"]["detail"].as_str().filter(|s| !s.is_empty()).unwrap_or("auto"),
            }),
            _ => part.clone(),
        }).collect()),
        content => content.clone(),
    }
}

fn responses(body: &Map<String, Value>, replay: bool, codex: bool) -> Map<String, Value> {
    let mut out = select(
        body,
        &[
            "model",
            "stream",
            "temperature",
            "top_p",
            "metadata",
            "store",
            "user",
            "parallel_tool_calls",
            "tool_choice",
            "previous_response_id",
            "prompt",
            "conversation",
            "instructions",
        ],
    );
    if let Some(function) = body
        .get("tool_choice")
        .and_then(|choice| choice["function"].as_object())
    {
        out.insert(
            "tool_choice".into(),
            json!({"type": "function", "name": function.get("name")}),
        );
    }
    if let Some(tokens) = ["max_output_tokens", "max_completion_tokens", "max_tokens"]
        .iter()
        .find_map(|key| body.get(*key))
    {
        out.insert("max_output_tokens".into(), tokens.clone());
    }
    if let Some(effort) = body.get("reasoning_effort").and_then(Value::as_str) {
        out.insert("reasoning".into(), json!({"effort": effort}));
    }
    if body.get("tools").is_some_and(Value::is_array) {
        let tools = items(body.get("tools"))
            .map(|tool| {
                if let Some(function) = tool["function"].as_object() {
                    let mut native = json!({"type": "function", "strict": false});
                    native
                        .as_object_mut()
                        .expect("constructed object")
                        .extend(function.clone());
                    native
                } else {
                    tool.clone()
                }
            })
            .collect();
        out.insert("tools".into(), Value::Array(tools));
    }
    let mut input = Vec::new();
    for message in items(body.get("messages")).filter(|row| row.is_object()) {
        if message["role"] == "tool" {
            input.push(json!({"type": "function_call_output", "call_id": clamp_tool_id(&message["tool_call_id"]),
                "output": string_payload(&message["content"])}));
            continue;
        }
        if replay
            && message["role"] == "assistant"
            && let Some(text) = message["reasoning_content"]
                .as_str()
                .filter(|text| !text.is_empty())
        {
            input.push(
                json!({"type": "reasoning", "content": [{"type": "reasoning_text", "text": text}]}),
            );
        }
        input.push(json!({"role": message["role"], "content": responses_input(message)}));
        for call in items(message.get("tool_calls")) {
            if let Some((name, args)) = call_content(call) {
                input.push(json!({"type": "function_call", "call_id": clamp_tool_id(&call["id"]),
                    "name": name, "arguments": args.as_str().filter(|value| !value.is_empty()).unwrap_or("{}")}));
            }
        }
    }
    if !input.is_empty() {
        out.insert("input".into(), Value::Array(input));
    } else if let Some(input) = body.get("input").filter(|input| {
        input.as_str().is_some_and(|text| !text.is_empty())
            || input.as_array().is_some_and(|rows| !rows.is_empty())
    }) {
        out.insert("input".into(), input.clone());
    } else if !["previous_response_id", "prompt", "conversation"]
        .iter()
        .any(|key| {
            out.get(*key)
                .is_some_and(|value| !value.is_null() && value != "")
        })
    {
        out.insert("input".into(), Value::Array(Vec::new()));
    }
    if codex {
        out.insert("stream".into(), json!(true));
        out.insert("store".into(), json!(false));
        if !out.get("instructions").is_some_and(Value::is_string) {
            out.insert("instructions".into(), json!(""));
        }
        for key in ["max_output_tokens", "temperature", "top_p"] {
            out.shift_remove(key);
        }
    }
    out
}

fn message_image(part: &Value) -> Result<Value, &'static str> {
    let url = part["image_url"]["url"]
        .as_str()
        .or(part["url"].as_str())
        .ok_or("image URL is missing")?;
    let source = if let Some(data) = url.strip_prefix("data:") {
        let (mime, bytes) = data
            .split_once(";base64,")
            .ok_or("image data URL must contain base64")?;
        if !matches!(
            mime,
            "image/jpeg" | "image/png" | "image/gif" | "image/webp"
        ) || bytes.is_empty()
            || !bytes
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'/' | b'='))
        {
            return Err("invalid Messages image data");
        }
        json!({"type": "base64", "media_type": mime, "data": bytes})
    } else {
        let checked = Url::parse(url).map_err(|_| "invalid image URL")?;
        if !matches!(checked.scheme(), "http" | "https")
            || !checked.username().is_empty()
            || checked.password().is_some()
        {
            return Err("unsupported image URL");
        }
        json!({"type": "url", "url": url})
    };
    Ok(json!({"type": "image", "source": source}))
}

fn messages(body: &Map<String, Value>) -> Result<Map<String, Value>, &'static str> {
    let mut out = select(body, &["model", "temperature", "top_p"]);
    out.insert(
        "max_tokens".into(),
        json!(
            ["max_tokens", "max_completion_tokens"]
                .iter()
                .find_map(|key| body
                    .get(*key)
                    .and_then(Value::as_u64)
                    .filter(|value| *value > 0))
                .unwrap_or(65_536)
        ),
    );
    out.insert(
        "stream".into(),
        json!(body.get("stream").and_then(Value::as_bool).unwrap_or(false)),
    );
    for key in ["thinking", "output_config"] {
        if let Some(value) = body.get(key).filter(|value| value.is_object()) {
            out.insert(key.into(), value.clone());
        }
    }
    if let Some(effort) = body
        .get("reasoning_effort")
        .and_then(Value::as_str)
        .filter(|effort| *effort != "none")
    {
        let config = out.entry("output_config").or_insert_with(|| json!({}));
        config["effort"] = json!(effort);
        out.entry("thinking")
            .or_insert_with(|| json!({"type": "adaptive"}));
    }
    if let Some(stop) = body.get("stop") {
        let sequences = match stop {
            Value::String(sequence) => json!([sequence]),
            other => other.clone(),
        };
        out.insert("stop_sequences".into(), sequences);
    }
    if body.get("tools").is_some_and(Value::is_array) {
        let tools = items(body.get("tools")).map(|tool| {
            let function = &tool["function"];
            if !function.is_object() {
                return tool.clone();
            }
            let mut result = json!({"name": function["name"], "input_schema":
                function.get("parameters").cloned().unwrap_or_else(|| json!({"type": "object", "properties": {}}))
            });
            if let Some(description) = function.get("description") {
                result["description"] = description.clone();
            }
            result
        }).collect();
        out.insert("tools".into(), Value::Array(tools));
    }
    let mut system = Vec::new();
    let mut turns = Vec::new();
    let mut tool_results = Vec::new();
    for message in items(body.get("messages")).filter(|row| row.is_object()) {
        match message["role"].as_str() {
            Some("system") => {
                if let Some(text) = message["content"].as_str() {
                    system.push(text);
                }
                continue;
            }
            Some("tool") => {
                tool_results.push(json!({"type": "tool_result", "tool_use_id": clamp_tool_id(&message["tool_call_id"]),
                    "content": string_payload(&message["content"])}));
                continue;
            }
            _ => {}
        }
        flush_tool_results(&mut turns, &mut tool_results);
        let mut blocks = Vec::new();
        let thinking = message["reasoning_content"]
            .as_str()
            .or(message["thinking"].as_str());
        if let Some(thinking) = thinking.filter(|text| !text.is_empty()) {
            blocks.push(json!({"type": "thinking", "thinking": thinking}));
        }
        match &message["content"] {
            Value::String(text) if !text.is_empty() => {
                blocks.push(json!({"type": "text", "text": text}))
            }
            Value::Array(parts) => {
                for part in parts {
                    match part["type"].as_str() {
                        Some("text") if part["text"].is_string() => {
                            blocks.push(json!({"type": "text", "text": part["text"]}))
                        }
                        Some("image_url") => blocks.push(message_image(part)?),
                        _ => return Err("unsupported Messages content block"),
                    }
                }
            }
            _ => {}
        }
        for call in items(message.get("tool_calls")) {
            if let Some((name, args)) = call_content(call) {
                let decoded = args
                    .as_str()
                    .and_then(|args| serde_json::from_str::<Value>(args).ok());
                let input = decoded
                    .filter(Value::is_object)
                    .unwrap_or_else(|| json!({}));
                blocks.push(json!({"type": "tool_use", "id": clamp_tool_id(&call["id"]), "name": name, "input": input}));
            }
        }
        let role = if message["role"] == "assistant" {
            "assistant"
        } else {
            "user"
        };
        let content = if blocks.len() == 1 && blocks[0]["type"] == "text" {
            blocks[0]["text"].clone()
        } else {
            Value::Array(blocks)
        };
        turns.push(json!({"role": role, "content": content}));
    }
    flush_tool_results(&mut turns, &mut tool_results);
    if !system.is_empty() {
        out.insert("system".into(), json!(system.join("\n\n")));
    }
    out.insert("messages".into(), Value::Array(turns));
    Ok(out)
}

fn flush_tool_results(turns: &mut Vec<Value>, results: &mut Vec<Value>) {
    if !results.is_empty() {
        turns.push(json!({"role": "user", "content": std::mem::take(results)}));
    }
}

#[cfg(test)]
mod tests;
