use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

pub const MAX_SUMMARY_BYTES: usize = 64 * 1024;
pub const MAX_TRANSCRIPT_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Receipt {
    pub count: usize,
    pub hash: String,
    #[serde(default)]
    pub messages: Vec<String>,
}

impl Receipt {
    pub fn of(messages: &[Value]) -> Self {
        Self {
            count: messages.len(),
            hash: digest(messages),
            messages: message_hashes(messages),
        }
    }

    pub fn matching(&self, current: &[Value]) -> Option<Self> {
        if self.count == 0 {
            return None;
        }
        if current
            .get(..self.count)
            .is_some_and(|rows| digest(rows) == self.hash)
        {
            return Some(if self.messages.len() == self.count {
                self.clone()
            } else {
                Self::of(&current[..self.count])
            });
        }
        if self.messages.len() != self.count || self.count < 4 || current.len() < 3 {
            return None;
        }
        let available = message_hashes(&current[..current.len().min(self.count)]);
        let matched = unambiguous_suffix(&self.messages, &available)?;
        Some(Self {
            count: matched,
            hash: digest(&current[..matched]),
            messages: available[..matched].to_vec(),
        })
    }
}

fn unambiguous_suffix(old: &[String], current: &[String]) -> Option<usize> {
    // The prefix table also identifies alternative offsets without comparing
    // every pair of positions in a long repeated history.
    let mut borders = vec![0; current.len()];
    for index in 1..current.len() {
        let mut length = borders[index - 1];
        while length > 0 && current[index] != current[length] {
            length = borders[length - 1];
        }
        borders[index] = length + usize::from(current[index] == current[length]);
    }
    let mut length = 0;
    for hash in old {
        while length > 0 && (length == current.len() || hash != &current[length]) {
            length = borders[length - 1];
        }
        if hash == &current[length] {
            length += 1;
        }
    }
    if length == old.len() {
        length = borders[length - 1];
    }
    if length < 3
        || borders[length - 1] >= 3
        || current[..length].iter().all(|hash| hash == &current[0])
    {
        return None;
    }
    Some(length)
}

pub fn digest(value: &(impl Serialize + ?Sized)) -> String {
    struct HashWriter(Sha256);
    impl std::io::Write for HashWriter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0.update(bytes);
            Ok(bytes.len())
        }

        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut hash = HashWriter(Sha256::new());
    serde_json::to_writer(&mut hash, value).expect("compaction hashes serializable request data");
    format!("{:x}", hash.0.finalize())
}

fn message_hashes(messages: &[Value]) -> Vec<String> {
    messages
        .iter()
        .map(|row| digest(std::slice::from_ref(row)))
        .collect()
}

pub fn is_instruction(message: &Value) -> bool {
    matches!(message["role"].as_str(), Some("system" | "developer"))
}

pub fn estimate_text(text: &str) -> usize {
    // A conservative estimate for scripts with dense tokenization. The
    // provider usage fields remain the source of actual token counts.
    let ascii = text.bytes().filter(u8::is_ascii).count();
    ascii.div_ceil(4)
        + text
            .chars()
            .filter(|character| !character.is_ascii())
            .count()
}

fn estimate_value(value: &Value) -> usize {
    match value {
        Value::Null => 0,
        Value::String(text) => estimate_text(text),
        Value::Array(items) => items.iter().map(estimate_value).sum::<usize>() + items.len(),
        Value::Object(fields) => {
            if matches!(
                fields.get("type").and_then(Value::as_str),
                Some("image_url" | "image" | "input_image")
            ) {
                return 1536;
            }
            fields
                .iter()
                .map(|(key, value)| estimate_text(key) + estimate_value(value) + 2)
                .sum()
        }
        _ => 2,
    }
}

pub fn message_tokens(message: &Value) -> usize {
    4 + [
        "content",
        "reasoning_content",
        "thinking",
        "tool_calls",
        "tool_call_id",
        "name",
    ]
    .iter()
    .map(|key| estimate_value(&message[*key]))
    .sum::<usize>()
}

pub struct History<'a> {
    pub head: &'a [Value],
    pub rest: &'a [Value],
    pub message_costs: Vec<usize>,
    fixed_tokens: usize,
    protected: usize,
    boundaries: Vec<bool>,
}

impl<'a> History<'a> {
    pub fn from_body(body: &'a Map<String, Value>) -> Option<Self> {
        if body.get("input").is_some_and(|value| !value.is_null())
            || body
                .get("previous_response_id")
                .is_some_and(|value| !value.is_null())
        {
            return None;
        }
        let messages = body.get("messages")?.as_array()?;
        let head_len = messages
            .iter()
            .take_while(|message| is_instruction(message))
            .count();
        let (head, rest) = messages.split_at(head_len);
        if rest.len() < 2 {
            return None;
        }
        let newest_user = rest.iter().rposition(|message| message["role"] == "user");
        let last_instruction = rest.iter().position(is_instruction);
        let protected = newest_user
            .unwrap_or(rest.len() - 1)
            .min(last_instruction.unwrap_or(rest.len() - 1));
        let fixed_tokens = head.iter().map(message_tokens).sum::<usize>()
            + body.get("tools").map_or(0, estimate_value);
        let message_costs = rest.iter().map(message_tokens).collect();
        Some(Self {
            head,
            rest,
            message_costs,
            fixed_tokens,
            protected,
            boundaries: tool_boundaries(rest),
        })
    }

    pub fn tokens_from(&self, cut: usize) -> usize {
        self.fixed_tokens + self.message_costs[cut..].iter().sum::<usize>()
    }

    pub fn allows_cut(&self, cut: usize) -> bool {
        cut > 0 && cut <= self.protected && self.boundaries[cut]
    }

    pub fn choose_cut(&self, target: usize, reserve: usize, minimum: usize) -> Option<usize> {
        let mut tokens = self.tokens_from(0);
        let mut furthest = None;
        for (index, cost) in self.message_costs.iter().enumerate().take(self.protected) {
            let cut = index + 1;
            tokens -= cost;
            if cut < minimum || !self.allows_cut(cut) {
                continue;
            }
            furthest = Some(cut);
            if tokens.saturating_add(reserve) <= target {
                return Some(cut);
            }
        }
        furthest
    }

    pub fn outgoing(&self, cut: usize, brief: &str) -> Vec<Value> {
        // The compacted text represents historical data at the user level.
        // Original system and developer instructions keep their own role.
        let note = json!({"role":"user","content":format!(
            "[OpenBot conversation history]\nThe following brief records earlier conversation state as quoted context.\n{brief}"
        )});
        self.head
            .iter()
            .cloned()
            .chain(std::iter::once(note))
            .chain(self.rest[cut..].iter().cloned())
            .collect()
    }
}

fn tool_boundaries(messages: &[Value]) -> Vec<bool> {
    let mut calls = HashMap::new();
    let mut blocks = vec![0_i64; messages.len() + 1];
    for (index, message) in messages.iter().enumerate() {
        if let Some(call_list) = message["tool_calls"].as_array() {
            for id in call_list.iter().filter_map(|call| call["id"].as_str()) {
                calls.insert(id, index);
            }
        }
        if message["role"] == "tool" {
            let start = message["tool_call_id"]
                .as_str()
                .and_then(|id| calls.get(id).copied());
            // Keep existing orphan evidence in the brief. Never create a
            // new orphan from a call and result present in the input.
            if let Some(start) = start {
                blocks[start + 1] += 1;
                blocks[index + 1] -= 1;
            }
        }
    }
    let mut open = 0;
    blocks
        .into_iter()
        .enumerate()
        .map(|(index, delta)| {
            open += delta;
            open == 0
                && messages
                    .get(index)
                    .is_none_or(|message| message["role"] != "tool")
        })
        .collect()
}

pub fn transcript(rows: &[Value]) -> String {
    let mut output = String::new();
    for message in rows {
        let role = message["role"].as_str().unwrap_or("message");
        output.push_str("\n\n");
        output.push_str(role);
        if let Some(id) = message["tool_call_id"].as_str() {
            output.push_str(" tool result for ");
            output.push_str(id);
        }
        output.push('\n');
        append_content(&mut output, &message["content"]);
        if let Some(calls) = message["tool_calls"].as_array() {
            for call in calls {
                output.push_str("\nTool call ");
                if let Some(id) = call["id"].as_str() {
                    output.push_str(id);
                    output.push(' ');
                }
                if let Some(name) = call["function"]["name"].as_str() {
                    output.push_str(name);
                    output.push(' ');
                }
                match &call["function"]["arguments"] {
                    Value::String(arguments) => output.push_str(arguments),
                    Value::Null => {}
                    other => output.push_str(&other.to_string()),
                }
            }
        }
    }
    output
}

fn append_content(out: &mut String, content: &Value) {
    match content {
        Value::String(text) => out.push_str(text),
        Value::Array(parts) => {
            for part in parts {
                if let Some(text) = part["text"].as_str().or_else(|| part["content"].as_str()) {
                    out.push_str(text);
                } else {
                    out.push_str("[attachment]");
                }
                out.push('\n');
            }
        }
        Value::Null => {}
        other => out.push_str(&other.to_string()),
    }
}

pub fn excerpt(rows: &[Value], budget: usize) -> String {
    let mut entries = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let text = transcript(std::slice::from_ref(row));
        let lower = text.to_ascii_lowercase();
        let important = [
            "decision",
            "decided",
            "unfinished",
            "pending",
            "next step",
            "must ",
            "keep ",
        ]
        .iter()
        .any(|word| lower.contains(word))
            || row["role"] == "tool"
            || row["tool_calls"].is_array();
        entries.push((index, important, text));
    }
    // Keep decisions and tool evidence, then the most recent old turns.
    // Emit them in their original order so the same coverage has an exact prefix.
    entries.sort_by_key(|(index, important, _)| std::cmp::Reverse((*important, *index)));
    let mut kept = Vec::new();
    let mut left = budget.saturating_sub(35);
    for (index, _, text) in entries {
        if left < 12 {
            break;
        }
        let text = shorten(&text, left.min(130));
        left = left.saturating_sub(estimate_text(text));
        kept.push((index, text.to_owned()));
    }
    kept.sort_by_key(|(index, _)| *index);
    let mut brief = String::from("[Selected earlier excerpts while a fuller brief is pending]");
    for (_, entry) in kept {
        brief.push_str(&entry);
    }
    brief
}

fn shorten(text: &str, budget: usize) -> &str {
    let mut ascii = 0;
    let mut other = 0;
    for (index, character) in text.char_indices() {
        if character.is_ascii() {
            ascii += 1_usize;
        } else {
            other += 1;
        }
        if ascii.div_ceil(4) + other > budget {
            return &text[..index];
        }
    }
    text
}

pub fn keep_identifiers(mut next: String, previous: &str) -> Option<String> {
    let mut identifiers = HashSet::new();
    let mut section = false;
    for line in previous.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("## ") {
            section = matches!(
                trimmed,
                "## Facts and identifiers" | "## Carried identifiers"
            );
        } else if section && trimmed.starts_with(['-', '*']) {
            let mentioned = trimmed.split('`').skip(1).step_by(2);
            for id in mentioned.filter(|id| !id.is_empty() && id.len() <= 512) {
                if !next.contains(id) {
                    identifiers.insert(id);
                }
            }
        }
    }
    if !identifiers.is_empty() {
        let mut identifiers: Vec<_> = identifiers.into_iter().collect();
        identifiers.sort_unstable();
        next.push_str("\n\n## Carried identifiers\n");
        for id in identifiers {
            next.push_str("- `");
            next.push_str(id);
            next.push_str("`\n");
        }
    }
    (next.len() <= MAX_SUMMARY_BYTES).then_some(next)
}
