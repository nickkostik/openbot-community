use std::collections::{BTreeMap, BTreeSet, VecDeque};

use axum::{body::Bytes, http::HeaderMap};
use serde_json::{Map, Value, json};

use crate::catalog::Protocol;

use super::{
    failure::Failure,
    response::{self, Envelope},
};

const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024;
const MAX_TOOL_BYTES: usize = 16 * 1024 * 1024;

struct Reader {
    upstream: reqwest::Response,
    chunk: Bytes,
    line: Vec<u8>,
    data: Vec<u8>,
    after_cr: bool,
    first_line: bool,
}

impl Reader {
    fn new(upstream: reqwest::Response) -> Self {
        Self {
            upstream,
            chunk: Bytes::new(),
            line: Vec::new(),
            data: Vec::new(),
            after_cr: false,
            first_line: true,
        }
    }

    async fn next(&mut self) -> Result<Option<Vec<u8>>, Failure> {
        loop {
            if self.chunk.is_empty() {
                let Some(chunk) = self
                    .upstream
                    .chunk()
                    .await
                    .map_err(|err| Failure::network(&err))?
                else {
                    self.take_line();
                    return Ok(self.take_data());
                };
                self.chunk = chunk;
            }
            if self.after_cr {
                self.after_cr = false;
                if self.chunk.first() == Some(&b'\n') {
                    self.chunk = self.chunk.slice(1..);
                }
            }
            let newline = self
                .chunk
                .iter()
                .position(|byte| matches!(byte, b'\r' | b'\n'));
            let end = newline.unwrap_or(self.chunk.len());
            if self.data.len() + self.line.len() + end > MAX_FRAME_BYTES {
                return Err(Failure::protocol(
                    "Upstream event exceeded the supported size.",
                ));
            }
            self.line.extend_from_slice(&self.chunk[..end]);
            if let Some(newline) = newline {
                self.after_cr = self.chunk[newline] == b'\r';
                self.chunk = self.chunk.slice(newline + 1..);
                if self.line.is_empty() {
                    if let Some(event) = self.take_data() {
                        return Ok(Some(event));
                    }
                } else {
                    self.take_line();
                }
            } else {
                self.chunk = Bytes::new();
            }
        }
    }

    fn take_line(&mut self) {
        let line = if self.first_line {
            self.first_line = false;
            self.line
                .strip_prefix(b"\xef\xbb\xbf")
                .unwrap_or(&self.line)
        } else {
            &self.line
        };
        if let Some(data) = line.strip_prefix(b"data:") {
            self.data
                .extend_from_slice(data.strip_prefix(b" ").unwrap_or(data));
            self.data.push(b'\n');
        }
        self.line.clear();
    }

    fn take_data(&mut self) -> Option<Vec<u8>> {
        self.data.pop()?;
        Some(std::mem::take(&mut self.data))
    }
}

pub enum Frame {
    Data(Value),
    Done,
}

impl Frame {
    pub fn visible(&self) -> bool {
        match self {
            Self::Done => true,
            Self::Data(chunk) => chunk["choices"].as_array().is_some_and(|choices| {
                choices.iter().any(|choice| {
                    let delta = &choice["delta"];
                    ["content", "reasoning_content", "refusal"]
                        .iter()
                        .any(|key| delta[key].as_str().is_some_and(|text| !text.is_empty()))
                        || delta["tool_calls"]
                            .as_array()
                            .is_some_and(|calls| !calls.is_empty())
                        || choice["finish_reason"].is_string()
                })
            }),
        }
    }

    pub fn bytes(&self) -> Bytes {
        match self {
            Self::Done => Bytes::from_static(b"data: [DONE]\n\n"),
            Self::Data(data) => Bytes::from(format!("data: {data}\n\n")),
        }
    }
}

pub struct Live {
    reader: Reader,
    codec: Codec,
    outgoing: VecDeque<Frame>,
    done: bool,
    headers: HeaderMap,
    secret: String,
}

impl Live {
    pub fn new(upstream: reqwest::Response, protocol: Protocol, model: &str, secret: &str) -> Self {
        let headers = upstream.headers().clone();
        Self {
            reader: Reader::new(upstream),
            codec: Codec::new(protocol, model),
            outgoing: VecDeque::new(),
            done: false,
            headers,
            secret: secret.into(),
        }
    }

    pub async fn next(&mut self) -> Result<Option<Frame>, Failure> {
        while self.outgoing.is_empty() && !self.done {
            if self.codec.complete {
                self.outgoing.push_back(Frame::Done);
                self.done = true;
                break;
            }
            let data = self.reader.next().await?;
            if data.is_none()
                || data
                    .as_deref()
                    .is_some_and(|bytes| bytes.trim_ascii() == b"[DONE]")
            {
                if self.codec.protocol == Protocol::ChatCompletions
                    && !self.codec.choices.is_empty()
                    && self.codec.choices.values().all(|finished| *finished)
                {
                    self.codec.complete = true;
                    continue;
                }
                return Err(Failure::incomplete());
            }
            let data = data.unwrap_or_default();
            if data.iter().all(u8::is_ascii_whitespace) {
                continue;
            }
            let event: Value = serde_json::from_slice(&data).map_err(|_| {
                Failure::protocol("Upstream sent a malformed event instead of inference data.")
            })?;
            self.codec
                .apply(event, &self.headers, &self.secret, &mut self.outgoing)?;
        }
        Ok(self.outgoing.pop_front())
    }
}

struct Codec {
    protocol: Protocol,
    envelope: Envelope,
    finish: Option<String>,
    complete: bool,
    started: bool,
    usage: Map<String, Value>,
    choices: BTreeMap<u64, bool>,
    seen_text: BTreeSet<u64>,
    seen_reasoning: BTreeSet<u64>,
    tools: BTreeMap<u64, String>,
    tool_bytes: usize,
}

impl Codec {
    fn new(protocol: Protocol, model: &str) -> Self {
        Self {
            protocol,
            envelope: Envelope::new(model),
            finish: None,
            complete: false,
            started: false,
            usage: Map::new(),
            choices: BTreeMap::new(),
            seen_text: BTreeSet::new(),
            seen_reasoning: BTreeSet::new(),
            tools: BTreeMap::new(),
            tool_bytes: 0,
        }
    }

    fn apply(
        &mut self,
        event: Value,
        headers: &HeaderMap,
        secret: &str,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        let kind = event["type"].as_str().unwrap_or_default();
        if event["error"].is_object() || kind == "error" {
            return Err(Failure::event(&event, headers, secret));
        }
        if kind == "response.failed" || kind == "response.cancelled" {
            return Err(Failure::event(&event["response"], headers, secret));
        }
        if kind == "ping" {
            return Ok(());
        }
        match self.protocol {
            Protocol::ChatCompletions => {
                let choices = event["choices"].as_array().ok_or_else(|| {
                    Failure::protocol("Upstream sent a stream for a different inference protocol.")
                })?;
                for row in choices {
                    let finished = self
                        .choices
                        .entry(row["index"].as_u64().unwrap_or(0))
                        .or_default();
                    *finished |= row["finish_reason"].is_string();
                }
                out.push_back(Frame::Data(event));
            }
            Protocol::Responses if kind.starts_with("response.") => {
                self.responses(&event, kind, out)?
            }
            Protocol::Anthropic
                if kind.starts_with("message_") || kind.starts_with("content_block_") =>
            {
                self.anthropic(&event, kind, out)?;
            }
            _ => {
                return Err(Failure::protocol(
                    "Upstream sent a stream for a different inference protocol.",
                ));
            }
        }
        Ok(())
    }

    fn emit(&self, value: Value, out: &mut VecDeque<Frame>) {
        out.push_back(Frame::Data(self.envelope.chunk(value, None, None)));
    }

    fn begin(&mut self, value: &Value, out: &mut VecDeque<Frame>) {
        self.envelope.observe(value);
        if !self.started {
            self.started = true;
            self.emit(json!({"role": "assistant"}), out);
        }
    }

    fn text(&mut self, text: &Value, reasoning: bool, out: &mut VecDeque<Frame>) {
        if text.as_str().is_some_and(|text| !text.is_empty()) {
            if reasoning {
                self.emit(json!({"reasoning_content": text}), out);
            } else {
                self.emit(json!({"content": text}), out);
            }
        }
    }

    fn start_tool(
        &mut self,
        index: u64,
        id: &Value,
        name: &Value,
        arguments: &str,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        if self.tools.contains_key(&index) {
            return Ok(());
        }
        if !id.is_string() || !name.is_string() {
            return Err(Failure::protocol(
                "Upstream tool call is missing its identity.",
            ));
        }
        self.tool_bytes = self.tool_bytes.saturating_add(
            arguments.len()
                + id.as_str().unwrap_or_default().len()
                + name.as_str().unwrap_or_default().len(),
        );
        if self.tool_bytes > MAX_TOOL_BYTES {
            return Err(Failure::protocol(
                "Upstream tool calls exceeded the supported size.",
            ));
        }
        self.tools.insert(index, arguments.into());
        let mut call = response::tool(id, name, arguments);
        call["index"] = json!(index);
        self.emit(json!({"tool_calls": [call]}), out);
        Ok(())
    }

    fn tool_delta(
        &mut self,
        index: u64,
        text: &str,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        let arguments = self.tools.get_mut(&index).ok_or_else(|| {
            Failure::protocol("Upstream sent tool arguments before declaring the tool call.")
        })?;
        self.tool_bytes = self.tool_bytes.saturating_add(text.len());
        if self.tool_bytes > MAX_TOOL_BYTES {
            return Err(Failure::protocol(
                "Upstream tool calls exceeded the supported size.",
            ));
        }
        if !text.is_empty() {
            arguments.push_str(text);
            self.emit(
                json!({"tool_calls": [{"index": index, "function": {"arguments": text}}]}),
                out,
            );
        }
        Ok(())
    }

    fn complete_tool(
        &mut self,
        index: u64,
        full: &str,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        let part = self
            .tools
            .get(&index)
            .ok_or_else(|| Failure::protocol("Upstream finished an undeclared tool call."))?;
        if let Some(remaining) = full.strip_prefix(part) {
            return self.tool_delta(index, remaining, out);
        }
        if let (Ok(full), Ok(sent)) = (
            serde_json::from_str::<Value>(full),
            serde_json::from_str::<Value>(part),
        ) && full == sent
        {
            return Ok(());
        }
        Err(Failure::protocol(
            "Upstream tool arguments changed during the stream.",
        ))
    }

    fn responses(
        &mut self,
        event: &Value,
        kind: &str,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        let index = event["output_index"].as_u64().unwrap_or(0);
        let item = &event["item"];
        match kind {
            "response.created" | "response.in_progress" => self.begin(&event["response"], out),
            "response.output_text.delta" | "response.refusal.delta" => {
                self.response_text(index, &event["delta"], false, out);
            }
            "response.reasoning_text.delta" | "response.reasoning_summary_text.delta" => {
                self.response_text(index, &event["delta"], true, out);
            }
            "response.output_item.added" if item["type"] == "function_call" => {
                self.response_tool(index, item, out)?;
            }
            "response.function_call_arguments.delta" => {
                self.tool_delta(index, event["delta"].as_str().unwrap_or_default(), out)?;
            }
            "response.function_call_arguments.done" => {
                if let Some(full) = event["arguments"].as_str() {
                    self.complete_tool(index, full, out)?;
                }
            }
            "response.output_item.done" => {
                self.response_item(index, item, out)?;
            }
            "response.completed" | "response.incomplete" => {
                let value = &event["response"];
                if !value.is_object() {
                    return Err(Failure::protocol(
                        "Upstream completed an invalid Responses stream.",
                    ));
                }
                self.begin(value, out);
                if let Some(items) = value["output"].as_array() {
                    for (index, item) in items.iter().enumerate() {
                        self.response_item(index as u64, item, out)?;
                    }
                }
                let finish = if kind == "response.incomplete"
                    && value["status"].is_null()
                    && value["incomplete_details"]["reason"] != "content_filter"
                {
                    "length"
                } else {
                    response::responses_finish(value, !self.tools.is_empty())
                };
                self.finish(finish, response::usage(self.protocol, &value["usage"]), out);
            }
            _ => {}
        }
        Ok(())
    }

    fn response_text(
        &mut self,
        index: u64,
        text: &Value,
        reasoning: bool,
        out: &mut VecDeque<Frame>,
    ) {
        if text.as_str().is_some_and(|text| !text.is_empty()) {
            if reasoning {
                self.seen_reasoning.insert(index);
            } else {
                self.seen_text.insert(index);
            }
            self.text(text, reasoning, out);
        }
    }

    fn response_item(
        &mut self,
        index: u64,
        item: &Value,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        if item["type"] == "function_call" {
            return self.response_tool(index, item, out);
        }
        let parts = response::response_parts([item]);
        if !self.seen_text.contains(&index) {
            self.response_text(index, &json!(parts.text), false, out);
        }
        if !self.seen_reasoning.contains(&index) {
            self.response_text(index, &json!(parts.reasoning), true, out);
        }
        Ok(())
    }

    fn response_tool(
        &mut self,
        index: u64,
        item: &Value,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        let id = item
            .get("call_id")
            .filter(|value| value.is_string())
            .unwrap_or(&item["id"]);
        let arguments = item["arguments"].as_str().unwrap_or_default();
        if self.tools.contains_key(&index) && !arguments.is_empty() {
            self.complete_tool(index, arguments, out)
        } else {
            self.start_tool(index, id, &item["name"], arguments, out)
        }
    }

    fn anthropic(
        &mut self,
        event: &Value,
        kind: &str,
        out: &mut VecDeque<Frame>,
    ) -> Result<(), Failure> {
        let index = event["index"].as_u64().unwrap_or(0);
        let delta = &event["delta"];
        match kind {
            "message_start" => {
                if !event["message"].is_object() {
                    return Err(Failure::protocol(
                        "Upstream sent an invalid Messages preamble.",
                    ));
                }
                self.begin(&event["message"], out);
                self.merge_usage(&event["message"]["usage"]);
            }
            "content_block_start" => {
                let block = &event["content_block"];
                match block["type"].as_str() {
                    Some("tool_use") => {
                        let args = block["input"]
                            .as_object()
                            .filter(|input| !input.is_empty())
                            .map_or_else(String::new, |_| block["input"].to_string());
                        self.start_tool(index, &block["id"], &block["name"], &args, out)?;
                    }
                    Some("text") => self.text(&block["text"], false, out),
                    Some("thinking") => self.text(&block["thinking"], true, out),
                    _ => {}
                }
            }
            "content_block_delta" => match delta["type"].as_str() {
                Some("text_delta") => self.text(&delta["text"], false, out),
                Some("thinking_delta") => self.text(&delta["thinking"], true, out),
                Some("input_json_delta") => self.tool_delta(
                    index,
                    delta["partial_json"].as_str().unwrap_or_default(),
                    out,
                )?,
                _ => {}
            },
            "content_block_stop" if self.tools.get(&index).is_some_and(String::is_empty) => {
                self.tool_delta(index, "{}", out)?;
            }
            "message_delta" => {
                self.merge_usage(&event["usage"]);
                if delta["stop_reason"].is_string() {
                    self.finish = Some(response::messages_finish(&delta["stop_reason"]).into());
                }
            }
            "message_stop" => {
                let finish = self.finish.take().ok_or_else(Failure::incomplete)?;
                let usage = if self.usage.is_empty() {
                    None
                } else {
                    response::usage(
                        self.protocol,
                        &Value::Object(std::mem::take(&mut self.usage)),
                    )
                };
                self.finish(&finish, usage, out);
            }
            _ => {}
        }
        Ok(())
    }

    fn merge_usage(&mut self, usage: &Value) {
        if let Some(usage) = usage.as_object() {
            self.usage.extend(usage.clone());
        }
    }

    fn finish(&mut self, reason: &str, usage: Option<Value>, out: &mut VecDeque<Frame>) {
        out.push_back(Frame::Data(self.envelope.chunk(
            json!({}),
            Some(reason),
            usage,
        )));
        self.complete = true;
    }
}
