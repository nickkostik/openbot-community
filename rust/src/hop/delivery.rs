use std::{
    collections::{BTreeSet, VecDeque},
    time::Instant,
};

use axum::http::StatusCode;
use serde_json::{Value, json};

use crate::{delivery::Terminal, logs::Trace};

use super::{
    Hop, Live, Reply,
    failure::Failure,
    request::{Prepared, PreparedDelivery},
    response,
    stream::Frame,
};

const MAX_HELD_FRAMES: usize = 32;
const MAX_HELD_BYTES: usize = 4 * 1024 * 1024;

#[derive(Default)]
struct Observation {
    choice_zero: bool,
    additional_choice: bool,
    stop: bool,
    other_finish: bool,
    assistant_role: bool,
    delivery_call: bool,
    tools: BTreeSet<(u64, u64)>,
    usage: Option<Value>,
}

impl Observation {
    fn observe(&mut self, data: &Value, field: &str) {
        for choice in data["choices"].as_array().into_iter().flatten() {
            let index = choice["index"].as_u64().unwrap_or(0);
            self.choice_zero |= index == 0;
            self.additional_choice |= index != 0;
            self.assistant_role |= choice[field]["role"] == "assistant";
            if let Some(finish) = choice["finish_reason"].as_str() {
                self.stop |= finish == "stop";
                self.other_finish |= finish != "stop";
            }
            if let Some(calls) = choice[field]["tool_calls"].as_array() {
                for (offset, call) in calls.iter().enumerate() {
                    self.delivery_call |= call["function"]["name"].as_str().is_some_and(|name| {
                        ["SendToUser", "SendMessage", "ReactToMessage"]
                            .iter()
                            .any(|known| name.eq_ignore_ascii_case(known))
                    });
                    if self.tools.len() < 2048 {
                        self.tools
                            .insert((index, call["index"].as_u64().unwrap_or(offset as u64)));
                    }
                }
            }
        }
        if data["usage"].is_object() {
            self.usage = Some(data["usage"].clone());
        }
    }

    fn terminal(&self) -> Terminal {
        Terminal {
            silent: self.stop && !self.other_finish && self.tools.is_empty(),
            single_choice: self.choice_zero && !self.additional_choice,
            tool_calls: self.tools.len(),
            delivery_call: self.delivery_call,
        }
    }
}

impl PreparedDelivery {
    async fn try_followup(
        &mut self,
        hop: &Hop,
        prepared: &Prepared,
        first: Terminal,
        trace: &mut Option<Trace>,
    ) -> Option<Value> {
        let terminal_at = Instant::now();
        let Some(retry) = self
            .turn
            .terminal(first, self.followup.is_some(), terminal_at)
        else {
            self.log(trace);
            return None;
        };
        let packet = self.followup.take()?;
        self.log(trace);
        let started = Instant::now();
        let result = match tokio::time::timeout(
            retry.timeout,
            hop.attempt(prepared, &packet, false),
        )
        .await
        {
            Ok(Ok((status, Reply::Buffered(value)))) if valid_followup(&value) => {
                Ok((status, value))
            }
            Ok(Err(failure)) => Err(failure),
            Ok(_) => Err(Failure::protocol(
                "Delivery follow-up returned an invalid completion.",
            )),
            Err(_) => Err(Failure::new(
                StatusCode::GATEWAY_TIMEOUT,
                "delivery_timeout",
                "Delivery follow-up timed out.",
            )),
        };
        let value = match result {
            Ok((status, value)) => {
                if let Some(trace) = trace {
                    trace.attempt(status, started, None, None);
                }
                let mut second = Observation::default();
                second.observe(&value, "message");
                self.turn
                    .settled(Some((second.terminal(), &value["usage"])), started);
                Some(value)
            }
            Err(failure) => {
                if let Some(trace) = trace {
                    trace.attempt(failure.status(), started, Some(&failure.body()), None);
                }
                self.turn.settled(None, started);
                None
            }
        };
        drop(retry);
        self.log(trace);
        value
    }

    fn log(&self, trace: &mut Option<Trace>) {
        if let Some(trace) = trace {
            trace.delivery(self.turn.metadata());
        }
    }

    pub(super) async fn buffered(
        mut self,
        hop: &Hop,
        prepared: &Prepared,
        mut current: Value,
        trace: &mut Option<Trace>,
    ) -> Value {
        let mut seen = Observation::default();
        seen.observe(&current, "message");
        if current["choices"]
            .as_array()
            .is_none_or(|choices| choices.len() != 1)
            || !text_message(&current["choices"][0]["message"])
        {
            self.followup = None;
        }
        if let Some(mut next) = self
            .try_followup(hop, prepared, seen.terminal(), trace)
            .await
        {
            let current_choice = &mut current["choices"][0];
            let next_choice = &mut next["choices"][0];
            let left = &mut current_choice["message"];
            let right = &mut next_choice["message"];
            for field in ["content", "reasoning_content", "refusal"] {
                if let Some(prefix) = left[field].as_str().filter(|text| !text.is_empty()) {
                    let suffix = right[field].as_str().unwrap_or_default();
                    right[field] = json!(format!("{prefix}{suffix}"));
                }
            }
            next_choice["index"] = current_choice["index"].clone();
            carry_envelope(&current, &mut next);
            merge_usage(&mut next, seen.usage);
            return next;
        }
        current
    }
}

fn text_message(message: &Value) -> bool {
    message.is_object() && (message["content"].is_null() || message["content"].is_string())
}

fn valid_followup(response: &Value) -> bool {
    let Some(choices) = response["choices"]
        .as_array()
        .filter(|choices| choices.len() == 1)
    else {
        return false;
    };
    let choice = &choices[0];
    let message = &choice["message"];
    if choice["index"].as_u64().unwrap_or(0) != 0 || !text_message(message) {
        return false;
    }
    if !message["tool_calls"].is_null() && !message["tool_calls"].is_array() {
        return false;
    }
    let tools = message["tool_calls"].as_array();
    let has_tools = tools.is_some_and(|tools| !tools.is_empty());
    let terminal = match choice["finish_reason"].as_str() {
        Some("tool_calls") => has_tools,
        Some("stop") => true,
        Some("length" | "content_filter") => !has_tools,
        _ => false,
    };
    terminal
        && tools.is_none_or(|tools| {
            tools.iter().all(|call| {
                call["id"].as_str().is_some_and(|id| !id.is_empty())
                    && call["function"]["name"]
                        .as_str()
                        .is_some_and(|name| !name.is_empty())
                    && call["function"]["arguments"].as_str().is_some_and(|args| {
                        serde_json::from_str::<Value>(args).is_ok_and(|args| args.is_object())
                    })
            })
        })
}

fn carry_envelope(first: &Value, next: &mut Value) {
    for key in ["id", "created", "model"] {
        if let Some(value) = first.get(key) {
            next[key] = value.clone();
        }
    }
}

fn merge_usage(next: &mut Value, first: Option<Value>) {
    if let Some(first) = first {
        fn add(right: &mut Value, left: Value) {
            match (right, left) {
                (right @ Value::Null, left) => *right = left,
                (Value::Number(right), Value::Number(left)) => {
                    if let (Some(r), Some(l)) = (right.as_u64(), left.as_u64()) {
                        *right = r.saturating_add(l).into();
                    }
                }
                (Value::Object(right), Value::Object(left)) => {
                    for (key, value) in left {
                        add(right.entry(key).or_insert(Value::Null), value);
                    }
                }
                _ => (),
            }
        }
        add(&mut next["usage"], first);
    }
}

pub(super) struct Input {
    pub prefix: VecDeque<Frame>,
    pub live: Option<Box<Live>>,
}

impl Input {
    pub(super) async fn next(&mut self) -> Result<Option<Frame>, Failure> {
        if let Some(frame) = self.prefix.pop_front() {
            return Ok(Some(frame));
        }
        match &mut self.live {
            Some(live) => live.next().await,
            None => Ok(None),
        }
    }
}

pub(super) struct StreamDelivery {
    hop: Hop,
    prepared: Prepared,
    strategy: PreparedDelivery,
    observation: Observation,
    held: VecDeque<Frame>,
    ready: VecDeque<Frame>,
    held_bytes: usize,
    deadline: Option<tokio::time::Instant>,
}

impl StreamDelivery {
    pub(super) fn new(hop: Hop, prepared: Prepared, strategy: PreparedDelivery) -> Self {
        Self {
            hop,
            prepared,
            strategy,
            observation: Observation::default(),
            held: VecDeque::new(),
            ready: VecDeque::new(),
            held_bytes: 0,
            deadline: None,
        }
    }

    pub(super) async fn next(
        &mut self,
        source: &mut Input,
        trace: &mut Option<Trace>,
    ) -> Result<Option<Frame>, Failure> {
        loop {
            if let Some(frame) = self.ready.pop_front() {
                return Ok(Some(frame));
            }
            let current = if let Some(deadline) = self.deadline {
                match tokio::time::timeout_at(deadline, source.next()).await {
                    Ok(result) => result,
                    Err(_) => {
                        self.release_held("terminal_decision_timeout");
                        continue;
                    }
                }
            } else {
                source.next().await
            };
            let frame = match current {
                Ok(Some(frame)) => frame,
                output => {
                    self.strategy.log(trace);
                    return output;
                }
            };
            match &frame {
                Frame::Done => {
                    self.finish(trace).await;
                    self.ready.push_back(Frame::Done);
                }
                Frame::Data(value) => {
                    self.observation.observe(value, "delta");
                    let terminal = self.observation.terminal();
                    if self.held.is_empty()
                        && !(self.strategy.followup.is_some()
                            && terminal.silent
                            && terminal.single_choice)
                    {
                        return Ok(Some(frame));
                    }
                    self.held_bytes += frame.bytes().len();
                    self.held.push_back(frame);
                    if !terminal.silent
                        || !terminal.single_choice
                        || self.held_bytes > MAX_HELD_BYTES
                        || self.held.len() > MAX_HELD_FRAMES
                    {
                        self.release_held("terminal_hold_released");
                    } else if self.deadline.is_none() {
                        self.deadline = Some(
                            tokio::time::Instant::now() + self.strategy.turn.decision_budget(),
                        );
                    }
                }
            }
        }
    }

    async fn finish(&mut self, trace: &mut Option<Trace>) {
        self.deadline = None;
        if let Some(mut next) = self
            .strategy
            .try_followup(
                &self.hop,
                &self.prepared,
                self.observation.terminal(),
                trace,
            )
            .await
        {
            if let Some(Frame::Data(terminal)) = self.held.front() {
                carry_envelope(terminal, &mut next);
            }
            merge_usage(&mut next, self.observation.usage.take());
            for frame in self.held.drain(..) {
                if let Frame::Data(mut data) = frame {
                    if let Some(fields) = data.as_object_mut() {
                        fields.shift_remove("usage");
                    }
                    if let Some(choices) = data["choices"].as_array_mut() {
                        for choice in choices {
                            choice["finish_reason"] = Value::Null;
                        }
                    }
                    let frame = Frame::Data(data);
                    if frame.visible() {
                        self.ready.push_back(frame);
                    }
                }
            }
            let mut additional = response::json_frames(next);
            if self.observation.assistant_role
                && let Some(frame) = additional.first_mut()
                && let Some(delta) = frame
                    .pointer_mut("/choices/0/delta")
                    .and_then(Value::as_object_mut)
            {
                delta.shift_remove("role");
            }
            self.ready.extend(additional.into_iter().map(Frame::Data));
        } else {
            self.ready.append(&mut self.held);
        }
    }

    fn release_held(&mut self, reason: &'static str) {
        self.deadline = None;
        self.strategy.followup = None;
        self.strategy.turn.skip_packet(reason);
        self.ready.append(&mut self.held);
    }
}
