use std::collections::{BTreeSet, HashSet};

use serde::Serialize;
use serde_json::Value;

use crate::catalog::{Effort, Protocol, discovery_facts};

const MAX_OUTPUT_TOKENS: u64 = 131_072;

pub struct Inventory {
    pub models: Vec<Model>,
    pub skipped: Vec<&'static str>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Model {
    id: String,
    name: Option<String>,
    context_length: Option<u64>,
    max_output_tokens: Option<u64>,
    modalities: Vec<String>,
    reasoning_levels: Vec<Effort>,
    #[serde(skip_serializing_if = "Option::is_none")]
    api_type: Option<Protocol>,
    #[serde(skip_serializing_if = "Option::is_none")]
    model_type: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    provider_metadata: Option<&'static str>,
}

pub fn normalize(raw: &Value, origin: &str, go: bool, plan: bool) -> Option<Inventory> {
    let rows = first_array([&raw["data"], &raw["models"]])?;
    let visibility_filter = plan && !raw["data"].is_array();
    let mut used = HashSet::new();
    let mut models = Vec::new();
    let mut skipped = Vec::new();
    for row in rows {
        if visibility_filter && row["visibility"] != "list" {
            continue;
        }
        if !row.is_object() {
            skipped.push("not-an-object");
            continue;
        }
        let Some(id) = field(row, "id", "slug")
            .as_str()
            .map(str::trim)
            .filter(|id| !id.is_empty())
        else {
            skipped.push("missing-id");
            continue;
        };
        if !used.insert(id) {
            skipped.push("duplicate-id");
            continue;
        }
        models.push(model(row, id, origin, go, plan));
    }
    Some(Inventory { models, skipped })
}

fn model(row: &Value, id: &str, origin: &str, go: bool, plan: bool) -> Model {
    let model_type = go
        .then(|| row["type"].as_str().filter(|kind| !kind.trim().is_empty()))
        .flatten()
        .map(|kind| match kind {
            "chat" => "chat",
            "embedding" => "embedding",
            "decision" => "decision",
            "image" => "image",
            _ => "other",
        });
    let chat = model_type == Some("chat");
    let native = if chat { &row["limit"] } else { &Value::Null };
    let architecture = &row["architecture"];
    let mut modalities = if chat {
        modality_list(row["modalities"]["input"].as_array(), true)
    } else {
        Vec::new()
    };
    if modalities.is_empty() {
        modalities = modality_list(
            first_array([
                &row["input_modalities"],
                &architecture["input_modalities"],
                &row["modalities"],
            ]),
            false,
        );
    }
    let reasoning = if model_type.is_some() && !chat {
        Vec::new()
    } else if chat && let Some(levels) = go_reasoning(&row["capabilities"]["reasoning"]) {
        levels
    } else {
        vendor_reasoning(row)
    };
    let api = if plan {
        Some(Protocol::Responses)
    } else if chat {
        match row["recommended_api"].as_str() {
            Some("messages") => Some(Protocol::Anthropic),
            Some("responses") => Some(Protocol::Responses),
            Some("chat-completions") => Some(Protocol::ChatCompletions),
            _ => None,
        }
    } else {
        None
    };
    let mut model = Model {
        id: id.into(),
        name: field(row, "name", "display_name")
            .as_str()
            .filter(|name| !name.trim().is_empty())
            .map(str::to_owned),
        context_length: positive(
            [
                &native["context"],
                &row["context_length"],
                &row["contextLength"],
                &row["context_window"],
                &row["contextWindow"],
                &architecture["context_length"],
            ],
            u64::MAX,
        ),
        max_output_tokens: positive([&native["output"]], u64::MAX).or_else(|| {
            positive(
                [
                    &row["top_provider"]["max_completion_tokens"],
                    &row["max_completion_tokens"],
                    &row["maxOutputTokens"],
                    &row["max_tokens"],
                ],
                MAX_OUTPUT_TOKENS,
            )
        }),
        modalities,
        reasoning_levels: reasoning,
        api_type: api,
        model_type,
        provider_metadata: model_type.map(|_| "opencode-go"),
    };
    if let Some(facts) = discovery_facts(id, origin) {
        if !plan || model.reasoning_levels.len() <= 1 {
            model.reasoning_levels = facts.levels.to_vec();
        }
        model.context_length = model.context_length.or(facts.context);
        if let Some(output) = facts.output {
            model.max_output_tokens = Some(if facts.context.is_some() {
                model.max_output_tokens.unwrap_or(output)
            } else {
                model.max_output_tokens.unwrap_or(output).min(output)
            });
        }
        model.api_type = facts.protocol.or(model.api_type);
    }
    model
}

fn field<'a>(row: &'a Value, primary: &str, alias: &str) -> &'a Value {
    if row[primary].is_null() {
        &row[alias]
    } else {
        &row[primary]
    }
}

pub(crate) fn positive<'a>(
    values: impl IntoIterator<Item = &'a Value>,
    ceiling: u64,
) -> Option<u64> {
    values.into_iter().find_map(|value| {
        let number = value
            .as_f64()
            .or_else(|| value.as_str()?.trim().parse::<f64>().ok())?;
        (number.is_finite() && number >= 1.0 && number <= ceiling as f64).then_some(number as u64)
    })
}

pub(crate) fn first_array<const N: usize>(values: [&Value; N]) -> Option<&Vec<Value>> {
    values.into_iter().find_map(Value::as_array)
}

pub(crate) fn modality_list(items: Option<&Vec<Value>>, mime: bool) -> Vec<String> {
    let mut found = Vec::new();
    for item in items.into_iter().flatten().filter_map(Value::as_str) {
        let token = if mime {
            item.split('/').next().unwrap_or_default()
        } else {
            item
        };
        let token = token.trim().to_ascii_lowercase();
        if matches!(token.as_str(), "text" | "image" | "video" | "audio") && !found.contains(&token)
        {
            found.push(token);
        }
    }
    found
}

fn vendor_effort(value: &Value) -> Option<Effort> {
    let raw = value.as_str()?.trim().to_ascii_lowercase();
    match raw.as_str() {
        "off" | "disabled" => Some(Effort::None),
        "min" => Some(Effort::Low),
        "med" => Some(Effort::Medium),
        "x-high" | "extra-high" | "extra_high" => Some(Effort::XHigh),
        "on" => None,
        _ => Effort::parse(&raw),
    }
}

fn go_reasoning(reasoning: &Value) -> Option<Vec<Effort>> {
    if reasoning["supported"] == false {
        return Some(vec![Effort::Default]);
    }
    if reasoning["supported"] != true {
        return None;
    }
    let mut levels: BTreeSet<_> = reasoning["effort"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(vendor_effort)
        .collect();
    levels.insert(Effort::Default);
    if reasoning["toggle"] == true {
        levels.insert(Effort::None);
    }
    Some(levels.into_iter().collect())
}

fn vendor_reasoning(row: &Value) -> Vec<Effort> {
    let facts = VendorReasoning::read(row);
    if facts.supported(false) {
        facts.allow_list(false, false)
    } else {
        Vec::new()
    }
}

pub(crate) struct VendorReasoning {
    levels: BTreeSet<Effort>,
    declared: bool,
    mandatory: bool,
    toggle: bool,
}

impl VendorReasoning {
    pub(crate) fn read(row: &Value) -> Self {
        let reasoning = &row["reasoning"];
        let mandatory = reasoning["mandatory"] == true;
        let mut levels: BTreeSet<_> = first_array([
            &row["reasoningLevels"],
            &row["reasoning_levels"],
            &row["supported_reasoning_levels"],
            &row["supported_reasoning_efforts"],
        ])
        .into_iter()
        .flatten()
        .filter_map(|value| {
            if value.is_object() {
                vendor_effort(&value["effort"])
            } else {
                vendor_effort(value)
            }
        })
        .collect();
        levels.extend(
            first_array([
                &reasoning["supported_efforts"],
                &reasoning["supportedEfforts"],
            ])
            .into_iter()
            .flatten()
            .filter_map(vendor_effort),
        );
        let mut toggle = false;
        for option in first_array([&row["reasoning_options"], &row["reasoningOptions"]])
            .into_iter()
            .flatten()
        {
            match option["type"].as_str() {
                Some("toggle") => toggle = true,
                Some("effort") => levels.extend(
                    option["values"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(vendor_effort),
                ),
                _ => {}
            }
        }
        Self {
            levels,
            declared: *reasoning == true || reasoning.is_object(),
            mandatory,
            toggle,
        }
    }

    pub(crate) fn supported(&self, hint: bool) -> bool {
        self.declared || self.toggle || !self.levels.is_empty() || hint
    }

    pub(crate) fn allow_list(mut self, hint: bool, route_scoped: bool) -> Vec<Effort> {
        if !self.supported(hint) {
            return vec![Effort::Default];
        }
        let explicit = !self.levels.is_empty();
        let optional = !self.mandatory
            && (!route_scoped || self.toggle || !explicit || self.levels.contains(&Effort::None));
        if !explicit && !self.toggle {
            self.levels.insert(Effort::High);
        }
        self.levels.insert(Effort::Default);
        if optional {
            self.levels.insert(Effort::None);
        } else {
            self.levels.remove(&Effort::None);
        }
        self.levels.into_iter().collect()
    }
}
