use std::borrow::Cow;

use axum::http::HeaderMap;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use super::reminders;

const HIDDEN: &str = "[SAND_HIDDEN_PROMPT]";
const INCOMPLETE_RESULT: &str = "[tool call did not complete: no result recorded]";

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum Debt {
    NoTouch,
    TouchThenTool,
    TouchNoTool,
}

impl Debt {
    pub(super) fn name(self) -> &'static str {
        match self {
            Self::NoTouch => "no-touch",
            Self::TouchThenTool => "touch-then-tool",
            Self::TouchNoTool => "touch-no-tool",
        }
    }

    pub(super) fn owed(self) -> bool {
        self != Self::TouchNoTool
    }

    pub(super) fn nudge(self) -> Option<(&'static str, &'static str)> {
        match self {
            Self::NoTouch => Some(("reply-nudge", reminders::REPLY)),
            Self::TouchThenTool => Some(("closing-send", reminders::CLOSING)),
            Self::TouchNoTool => None,
        }
    }
}

pub(super) struct History {
    pub debt: Debt,
    pub text_sends: usize,
    pub tools: usize,
    pub after_touch: Option<usize>,
    pub boundary: usize,
    pub key: Option<[u8; 32]>,
}

impl History {
    pub(super) fn from_request(
        body: &Map<String, Value>,
        headers: &HeaderMap,
        bot_id: Option<&str>,
        conversation: Option<&str>,
    ) -> Result<Self, &'static str> {
        let flag = |names: &[&str]| {
            names
                .iter()
                .any(|name| body.get(*name) == Some(&Value::Bool(true)))
        };
        if flag(&[
            "openbot_internal_auxiliary",
            "openbot_internal_model",
            "internalLane",
            "internal_lane",
        ]) {
            return Err("internal_lane");
        }
        if flag(&["isRoutine", "routine", "automation", "isAutomation"]) {
            return Err("routine_or_automation");
        }
        if flag(&["hidden"]) {
            return Err("hidden_context");
        }
        if flag(&["isSubagent"]) {
            return Err("subagent_context");
        }
        if flag(&["isSilenceAllowed"]) {
            return Err("silence_allowed");
        }
        if flag(&["groupFlag", "isGroup", "isGroupMemberTurn"]) {
            return Err("group_chat");
        }
        if body
            .get("requestSource")
            .and_then(Value::as_str)
            .is_some_and(|source| source != "person")
        {
            return Err("non_person");
        }
        let labels = ["openbotChatType", "chatType", "chat_type"]
            .into_iter()
            .filter_map(|key| body.get(key).and_then(Value::as_str))
            .chain(header(headers, "x-openbot-chat-type"));
        let mut direct = flag(&["directChat", "direct_chat"]);
        for label in labels {
            match label {
                "group" => return Err("group_chat"),
                "routine" | "automation" => return Err("routine_or_automation"),
                "dm" | "direct" => direct = true,
                _ => return Err("unknown_chat_type"),
            }
        }

        let bot = bounded(bot_id)
            .or_else(|| bounded(body.get("botId").and_then(Value::as_str)))
            .or_else(|| bounded(header(headers, "x-openbot-bot-id")))
            .ok_or("no_bot_id_in_request")?;
        let conversation = bounded(conversation).ok_or("missing_conversation_id")?;
        let rows = body
            .get("messages")
            .and_then(Value::as_array)
            .ok_or("no_user_boundary")?;
        let mut real = None;
        for (index, row) in rows
            .iter()
            .enumerate()
            .rev()
            .filter(|(_, row)| row["role"] == "user")
        {
            let content = text(&row["content"]);
            if known_reminder(&content) {
                continue;
            }
            if content.trim_start().starts_with(HIDDEN) {
                return Err("hidden_context");
            }
            if let Some((_, query)) = content.split_once("<user_query>") {
                let query = query
                    .split_once("</user_query>")
                    .map_or(query, |(query, _)| query);
                let query = without_reminders(query);
                if query.starts_with("[Group chat:") {
                    return Err("group_chat");
                }
                if query.is_empty()
                    || query
                        .get(..9)
                        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("[routine]"))
                {
                    return Err("routine_or_automation");
                }
                direct = true;
            }
            real = Some(index);
            break;
        }
        if !direct {
            return Err("unknown_chat_type");
        }
        let boundary = real.ok_or("no_user_boundary")?;
        if !has_send_tool(body) {
            return Err("delivery_tool_unavailable");
        }
        let key = ["openbotEpochId", "epochId", "epoch_id"]
            .into_iter()
            .find_map(|name| bounded(body.get(name).and_then(Value::as_str)))
            .or_else(|| bounded(header(headers, "x-openbot-epoch-id")))
            .map(|epoch| {
                let mut hash = Sha256::new();
                for component in [bot, conversation, epoch] {
                    hash.update([0]);
                    hash.update(component.as_bytes());
                }
                // The stock adapter derives its epoch from text. A later user
                // message with the same text still starts a different turn.
                if let Some(id) = bounded(rows[boundary]["id"].as_str()) {
                    hash.update([1]);
                    hash.update(id.as_bytes());
                } else {
                    hash.update([2]);
                    hash.update((boundary as u64).to_be_bytes());
                }
                hash.finalize().into()
            });
        Ok(Self::classify(rows, boundary, key))
    }

    fn classify(rows: &[Value], boundary: usize, key: Option<[u8; 32]>) -> Self {
        let mut tools = 0;
        let mut after_touch = None;
        let mut text_sends = 0;
        for row in &rows[boundary + 1..] {
            let content = row["content"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|part| {
                    matches!(
                        part["type"].as_str(),
                        Some("tool-call" | "tool_use" | "function_call")
                    )
                });
            for call in row["tool_calls"]
                .as_array()
                .into_iter()
                .flatten()
                .chain(content)
            {
                let name = tool_name(call);
                if text_delivery(name) || name.eq_ignore_ascii_case("ReactToMessage") {
                    after_touch = Some(0);
                    if text_delivery(name) && text_call(call) {
                        text_sends += 1;
                    }
                } else {
                    tools += 1;
                    if let Some(count) = &mut after_touch {
                        *count += 1;
                    }
                }
            }
            if row["role"] == "tool" && !text(&row["content"]).starts_with(INCOMPLETE_RESULT) {
                tools += 1;
                if let Some(count) = &mut after_touch {
                    *count += 1;
                }
            }
        }
        let debt = match after_touch {
            None => Debt::NoTouch,
            Some(0) => Debt::TouchNoTool,
            Some(_) => Debt::TouchThenTool,
        };
        Self {
            debt,
            text_sends,
            tools,
            after_touch,
            boundary,
            key,
        }
    }
}

fn header<'a>(headers: &'a HeaderMap, name: &'static str) -> Option<&'a str> {
    headers.get(name)?.to_str().ok()
}

fn bounded(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| {
        !value.is_empty() && value.len() <= 128 && !value.chars().any(char::is_control)
    })
}

fn has_send_tool(body: &Map<String, Value>) -> bool {
    let choice = body.get("tool_choice");
    if choice.and_then(Value::as_str) == Some("none")
        || choice.and_then(|choice| choice["type"].as_str()) == Some("none")
    {
        return false;
    }
    if let Some(choice) = choice.filter(|choice| choice.is_object()) {
        let name = tool_name(choice);
        if !name.is_empty() && !name.eq_ignore_ascii_case("SendToUser") {
            return false;
        }
    }
    body.get("tools")
        .and_then(Value::as_array)
        .is_some_and(|tools| {
            tools
                .iter()
                .any(|tool| tool_name(tool).eq_ignore_ascii_case("SendToUser"))
        })
}

fn tool_name(call: &Value) -> &str {
    ["name", "toolName", "tool_name"]
        .into_iter()
        .find_map(|name| call[name].as_str())
        .or_else(|| call["function"]["name"].as_str())
        .unwrap_or_default()
}

fn text_delivery(name: &str) -> bool {
    name.eq_ignore_ascii_case("SendToUser") || name.eq_ignore_ascii_case("SendMessage")
}

fn text_call(call: &Value) -> bool {
    let Some(args) = call
        .get("args")
        .or_else(|| call["function"].get("arguments"))
        .or_else(|| call.get("arguments"))
    else {
        return false;
    };
    args["type"] == "text"
        || args.as_str().is_some_and(|raw| {
            serde_json::from_str::<Value>(raw).is_ok_and(|value| value["type"] == "text")
        })
}

fn without_reminders(mut query: &str) -> &str {
    loop {
        query = query.trim();
        let Some(reminder) = query.strip_prefix("<system_reminder>") else {
            return query;
        };
        let Some((_, tail)) = reminder.split_once("</system_reminder>") else {
            return "";
        };
        query = tail;
    }
}

pub(super) fn text(value: &Value) -> Cow<'_, str> {
    if let Some(text) = value.as_str() {
        Cow::Borrowed(text)
    } else if let Some(parts) = value.as_array() {
        Cow::Owned(
            parts
                .iter()
                .map(text)
                .filter(|part| !part.is_empty())
                .collect::<Vec<_>>()
                .join("\n"),
        )
    } else if let Some(part) = value.get("text").or_else(|| value.get("content")) {
        text(part)
    } else {
        Cow::Borrowed("")
    }
}

fn known_reminder(text: &str) -> bool {
    [
        reminders::ACK,
        reminders::SILENCE,
        reminders::RESULT,
        reminders::OPENING,
        reminders::REPLY,
        reminders::CLOSING,
    ]
    .contains(&text)
        || text
            .strip_prefix(HIDDEN)
            .is_some_and(|body| [reminders::REPLY, reminders::CLOSING].contains(&body))
}

pub(super) fn add_nudge(rows: &mut Vec<Value>, body: &str) {
    let nudge = format!("{HIDDEN}{body}");
    if rows
        .last()
        .is_none_or(|row| row["role"] != "user" || text(&row["content"]) != nudge)
    {
        rows.push(json!({"role":"user", "content":nudge}));
    }
}

pub(super) fn add_ack(rows: &mut Vec<Value>, opening: bool, reminder: &'static str) -> bool {
    if opening {
        let opening = rows.iter_mut().find(|row| {
            row["role"] == "user" && {
                let body = text(&row["content"]);
                !body.starts_with(HIDDEN) && !known_reminder(&body)
            }
        });
        let Some(row) = opening else { return false };
        if text(&row["content"]).contains(reminders::OPENING_TEXT) {
            return false;
        }
        match &mut row["content"] {
            Value::String(body) => body.push_str(reminder),
            Value::Array(parts) => parts.push(json!({"type":"text", "text":reminder})),
            _ => return false,
        }
    } else {
        if rows
            .last()
            .is_some_and(|last| last["role"] == "user" && known_reminder(&text(&last["content"])))
        {
            return false;
        }
        rows.push(json!({"role":"user", "content":reminder}));
    }
    true
}
