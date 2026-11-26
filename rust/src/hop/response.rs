use std::{
    collections::BTreeMap,
    time::{SystemTime, UNIX_EPOCH},
};

use serde_json::{Map, Value, json};

use crate::catalog::Protocol;

use super::failure::Failure;

pub struct Envelope {
    id: String,
    model: String,
    created: u64,
}

impl Envelope {
    pub fn new(model: &str) -> Self {
        Self {
            id: String::new(),
            model: model.into(),
            created: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
        }
    }

    pub fn observe(&mut self, value: &Value) {
        if let Some(id) = value["id"].as_str().filter(|value| !value.is_empty()) {
            self.id = id.into();
        }
        if let Some(model) = value["model"].as_str().filter(|value| !value.is_empty()) {
            self.model = model.into();
        }
        if let Some(created) = value["created_at"]
            .as_u64()
            .or_else(|| value["created"].as_u64())
        {
            self.created = created;
        }
    }

    pub fn chunk(&self, delta: Value, finish: Option<&str>, usage: Option<Value>) -> Value {
        let mut chunk = json!({"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]});
        if let Some(usage) = usage {
            chunk["usage"] = usage;
        }
        chunk
    }

    fn completion(&self, choices: Vec<Value>, usage: Option<Value>) -> Value {
        let mut result = json!({"id": self.id, "object": "chat.completion", "created": self.created, "model": self.model,
            "choices": choices});
        if let Some(usage) = usage {
            result["usage"] = usage;
        }
        result
    }
}

#[derive(Default)]
pub struct Parts {
    pub text: String,
    pub reasoning: String,
    pub tools: Vec<Value>,
}

impl Parts {
    fn message(self) -> Value {
        let content = (!self.text.is_empty()).then_some(self.text);
        let mut message = json!({"role": "assistant", "content": content});
        if !self.reasoning.is_empty() {
            message["reasoning_content"] = json!(self.reasoning);
        }
        if !self.tools.is_empty() {
            message["tool_calls"] = Value::Array(self.tools);
        }
        message
    }
}

pub fn usage(protocol: Protocol, value: &Value) -> Option<Value> {
    if !value.is_object() {
        return None;
    }
    let count = |name| value[name].as_u64().unwrap_or(0);
    let mut input = count("input_tokens");
    let output = count("output_tokens");
    let mut result = json!({"prompt_tokens": input, "completion_tokens": output});
    if protocol == Protocol::Anthropic {
        let read = count("cache_read_input_tokens");
        let created = count("cache_creation_input_tokens");
        input = input.saturating_add(read).saturating_add(created);
        result["prompt_tokens"] = json!(input);
        if read != 0 || created != 0 {
            result["prompt_tokens_details"] =
                json!({"cached_tokens": read, "cache_creation_tokens": created});
        }
    } else {
        for (source, dest) in [
            ("input_tokens_details", "prompt_tokens_details"),
            ("output_tokens_details", "completion_tokens_details"),
        ] {
            if value[source].is_object() {
                result[dest] = value[source].clone();
            }
        }
    }
    result["total_tokens"] = json!(
        value["total_tokens"]
            .as_u64()
            .unwrap_or_else(|| input.saturating_add(output))
    );
    Some(result)
}

pub fn tool(id: &Value, name: &Value, arguments: &str) -> Value {
    json!({"id": id, "type": "function", "function": {"name": name, "arguments": arguments}})
}

fn list(value: &Value) -> impl Iterator<Item = &Value> {
    value.as_array().into_iter().flatten()
}

fn push_text(buffer: &mut String, value: &Value) {
    if let Some(text) = value.as_str() {
        buffer.push_str(text);
    }
}

pub fn response_parts<'a>(items: impl IntoIterator<Item = &'a Value>) -> Parts {
    let mut parts = Parts::default();
    for item in items {
        match item["type"].as_str() {
            Some("function_call") => {
                let id = item
                    .get("call_id")
                    .filter(|id| id.is_string())
                    .unwrap_or(&item["id"]);
                parts.tools.push(tool(
                    id,
                    &item["name"],
                    item["arguments"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .unwrap_or("{}"),
                ));
            }
            Some("reasoning") => {
                for content in list(&item["summary"]).chain(list(&item["content"])) {
                    push_text(&mut parts.reasoning, &content["text"]);
                }
            }
            Some("message") => {
                for content in list(&item["content"]) {
                    push_text(
                        &mut parts.text,
                        content.get("text").unwrap_or(&content["refusal"]),
                    );
                }
            }
            _ => {}
        }
    }
    parts
}

pub fn messages_finish(reason: &Value) -> &'static str {
    match reason.as_str() {
        Some("tool_use") => "tool_calls",
        Some("max_tokens") => "length",
        Some("refusal") => "content_filter",
        _ => "stop",
    }
}

pub fn responses_finish(response: &Value, has_tools: bool) -> &'static str {
    let reason = response["incomplete_details"]["reason"]
        .as_str()
        .unwrap_or_default();
    if reason == "content_filter" {
        "content_filter"
    } else if response["status"] == "incomplete"
        || reason.contains("output_tokens")
        || reason.contains("max_tokens")
    {
        "length"
    } else if has_tools {
        "tool_calls"
    } else {
        "stop"
    }
}

pub fn normalize(protocol: Protocol, model: &str, output: Value) -> Result<Value, Failure> {
    let mut envelope = Envelope::new(model);
    envelope.observe(&output);
    let (parts, finish) = match protocol {
        Protocol::ChatCompletions
            if output["choices"].as_array().is_some_and(|choices| {
                !choices.is_empty() && choices.iter().all(|choice| choice["message"].is_object())
            }) =>
        {
            return Ok(output);
        }
        Protocol::Responses if output["output"].is_array() => {
            let parts = response_parts(list(&output["output"]));
            let finish = responses_finish(&output, !parts.tools.is_empty());
            (parts, finish)
        }
        Protocol::Anthropic if output["content"].is_array() => {
            let mut parts = Parts::default();
            for block in list(&output["content"]) {
                match block["type"].as_str() {
                    Some("text") => push_text(&mut parts.text, &block["text"]),
                    Some("thinking") => push_text(&mut parts.reasoning, &block["thinking"]),
                    Some("tool_use") => {
                        let arguments = block["input"].as_object().map_or_else(
                            || "{}".into(),
                            |input| Value::Object(input.clone()).to_string(),
                        );
                        parts
                            .tools
                            .push(tool(&block["id"], &block["name"], &arguments));
                    }
                    _ => {}
                }
            }
            (parts, messages_finish(&output["stop_reason"]))
        }
        _ => {
            return Err(Failure::protocol(
                "Upstream returned a JSON body for a different inference protocol.",
            ));
        }
    };
    Ok(envelope.completion(
        vec![json!({"index": 0, "message": parts.message(), "finish_reason": finish})],
        usage(protocol, &output["usage"]),
    ))
}

#[derive(Default)]
struct Choice {
    parts: Parts,
    finish: Option<String>,
    tools: BTreeMap<u64, Value>,
}

pub struct Buffer {
    envelope: Envelope,
    choices: BTreeMap<u64, Choice>,
    usage: Option<Value>,
}

impl Buffer {
    pub fn new(model: &str) -> Self {
        Self {
            envelope: Envelope::new(model),
            choices: BTreeMap::new(),
            usage: None,
        }
    }

    pub fn push(&mut self, chunk: &Value) {
        self.envelope.observe(chunk);
        for choice in list(&chunk["choices"]) {
            let index = choice["index"].as_u64().unwrap_or(0);
            let row = self.choices.entry(index).or_default();
            let delta = &choice["delta"];
            push_text(&mut row.parts.text, &delta["content"]);
            push_text(&mut row.parts.reasoning, &delta["reasoning_content"]);
            if let Some(finish) = choice["finish_reason"].as_str() {
                row.finish = Some(finish.into());
            }
            for tool_delta in list(&delta["tool_calls"]) {
                let index = tool_delta["index"].as_u64().unwrap_or(0);
                let call = row
                    .tools
                    .entry(index)
                    .or_insert_with(|| tool(&Value::Null, &Value::Null, ""));
                append(&mut call["id"], &tool_delta["id"]);
                append(
                    &mut call["function"]["name"],
                    &tool_delta["function"]["name"],
                );
                append(
                    &mut call["function"]["arguments"],
                    &tool_delta["function"]["arguments"],
                );
            }
        }
        if chunk["usage"].is_object() {
            self.usage = Some(chunk["usage"].clone());
        }
    }

    pub fn completed(self) -> Result<Value, Failure> {
        if self.choices.is_empty() || self.choices.values().any(|choice| choice.finish.is_none()) {
            return Err(Failure::incomplete());
        }
        let choices = self.choices.into_iter().map(|(index, mut choice)| {
            choice.parts.tools = choice.tools.into_values().collect();
            json!({"index": index, "message": choice.parts.message(), "finish_reason": choice.finish})
        }).collect();
        Ok(self.envelope.completion(choices, self.usage))
    }
}

fn append(target: &mut Value, part: &Value) {
    if let Some(part) = part.as_str() {
        if let Value::String(current) = target {
            current.push_str(part);
        } else {
            *target = json!(part);
        }
    }
}

pub fn json_frames(mut completion: Value) -> Vec<Value> {
    let mut envelope = Map::new();
    for field in ["id", "model", "created"] {
        if let Some(value) = completion
            .as_object_mut()
            .and_then(|row| row.shift_remove(field))
        {
            envelope.insert(field.into(), value);
        }
    }
    envelope.insert("object".into(), json!("chat.completion.chunk"));
    let mut deltas = Vec::new();
    let mut finishes = Vec::new();
    for choice in list(&completion["choices"]) {
        let mut message = choice["message"].clone();
        if let Some(calls) = message["tool_calls"].as_array_mut() {
            for (index, call) in calls
                .iter_mut()
                .enumerate()
                .filter(|(_, call)| call.is_object())
            {
                call["index"] = json!(index);
            }
        }
        deltas.push(json!({"index": choice["index"], "delta": message, "finish_reason": null}));
        finishes.push(json!({"index": choice["index"], "delta": {}, "finish_reason": choice["finish_reason"]}));
    }
    let mut first = envelope.clone();
    first.insert("choices".into(), Value::Array(deltas));
    envelope.insert("choices".into(), Value::Array(finishes));
    if let Some(usage) = completion.get("usage") {
        envelope.insert("usage".into(), usage.clone());
    }
    vec![Value::Object(first), Value::Object(envelope)]
}
