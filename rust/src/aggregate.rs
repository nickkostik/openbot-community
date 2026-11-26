mod plans;
mod public;
mod runtime;

pub use runtime::CatalogService;

use std::{collections::BTreeSet, path::Path};

use serde_json::{Value, json};

use crate::{
    catalog::{Effort, discovery_facts, unversioned_slug},
    routing::{direct_go, parse_origin},
    server::read_json,
};

const MAX_CACHE_BYTES: u64 = 64 * 1024 * 1024;
const SOURCES: [(&str, &str); 2] = [
    ("openrouter", "https://openrouter.ai/api/v1/models"),
    ("models.dev", "https://models.dev/api.json"),
];

struct Inventory {
    data: Option<Value>,
    model_count: usize,
}

impl Inventory {
    fn load(root: &Path) -> Self {
        let data = read_json(&root.join("openbot-model-catalog.json"), MAX_CACHE_BYTES)
            .ok()
            .flatten()
            .filter(|data| data["models"].is_object());
        Self::new(data)
    }

    fn new(mut data: Option<Value>) -> Self {
        if let Some(data) = &mut data {
            for field in ["models", "routes"] {
                if let Some(entries) = data[field].as_object_mut() {
                    entries.retain(|key, row| {
                        let expected = if field == "routes" {
                            key.split_once('\0').map(|(_, id)| id)
                        } else {
                            Some(key.as_str())
                        };
                        let Some(model) =
                            normalize_cached(row).filter(|model| model["id"].as_str() == expected)
                        else {
                            return false;
                        };
                        *row = model;
                        true
                    });
                }
            }
        }
        let model_count = data
            .as_ref()
            .and_then(|data| data["models"].as_object())
            .map_or(0, |models| models.len());
        Self { data, model_count }
    }

    fn snapshot(&self, id: Option<&str>, origin: Option<&str>) -> Value {
        let data = self.data.as_ref().unwrap_or(&Value::Null);
        let sources: Vec<_> = SOURCES
            .into_iter()
            .map(|(name, url)| public::source_row(data, name, url))
            .collect();
        let mut result = json!({
            "status":if self.data.is_some() { "ready" } else { "failed" },
            "lastFetched":data["lastFetched"].as_str(), "totalModels":self.model_count,
            "sources":sources
        });
        if self.data.is_none() {
            result["error"] = json!({"kind":"cache-unavailable","message":"OpenBot has no readable model catalog cache."});
        }
        if let Some(id) = id {
            let model = if let Some(origin) = origin {
                provider_model(data, id, origin)
            } else {
                global_model(data, id)
            };
            result["lookup"] = model.map_or_else(
                || json!({"found":false}),
                |model| json!({"found":true,"model":model}),
            );
        }
        result
    }
}

fn normalize_cached(row: &Value) -> Option<Value> {
    let id = row["id"].as_str()?.trim();
    if id.is_empty() {
        return None;
    }
    let mut levels: BTreeSet<_> = row["reasoningLevels"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter_map(|effort| Effort::parse(&effort.trim().to_ascii_lowercase()))
        .collect();
    if !levels.is_empty() {
        levels.insert(Effort::Default);
    }
    let modalities: Vec<_> = row["modalities"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .collect();
    let number = |value: &Value| -> Option<f64> {
        value
            .as_f64()
            .or_else(|| value.as_str()?.trim().parse().ok())
            .filter(|number| number.is_finite() && *number > 0.0)
    };
    let cap = if row["maxOutputTokens"] == 0 {
        Some(0)
    } else {
        number(&row["maxOutputTokens"])
            .filter(|tokens| *tokens <= 524_288.0)
            .map(|tokens| tokens as u64)
    };
    let price = |keys: [&str; 4]| {
        keys.into_iter()
            .find_map(|key| number(&row["pricing"][key]))
    };
    let input_cost = price(["input", "prompt", "input_cost", "inputCost"]);
    let output_cost = price(["output", "completion", "output_cost", "outputCost"]);
    let pricing = if input_cost.is_some() || output_cost.is_some() {
        let currency = row["pricing"]["currency"]
            .as_str()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("USD")
            .to_ascii_uppercase();
        json!({"input":input_cost,"output":output_cost,"currency":currency})
    } else {
        Value::Null
    };
    let mut result = json!({
        "id":id, "name":row["name"].as_str(),
        "contextLength":number(&row["contextLength"]).map(|tokens| tokens as u64),
        "maxOutputTokens":cap, "modalities":modalities, "reasoning":row["reasoning"]==true,
        "reasoningLevels":levels, "pricing":pricing
    });
    for key in ["providerKey", "sourceUrl"] {
        if row[key].is_string() {
            result[key] = row[key].clone();
        }
    }
    if matches!(
        row["apiType"].as_str(),
        Some("chat-completions" | "responses" | "anthropic")
    ) {
        result["apiType"] = row["apiType"].clone();
    }
    if let Some(modes) = row["outputModalities"].as_array() {
        let mut supported = Vec::new();
        for value in modes.iter().filter_map(Value::as_str) {
            let value = value.trim().to_ascii_lowercase();
            if matches!(value.as_str(), "text" | "image" | "video" | "audio")
                && !supported.contains(&value)
            {
                supported.push(value);
            }
        }
        result["outputModalities"] = json!(supported);
    }
    Some(result)
}

fn provider_model(data: &Value, id: &str, origin: &str) -> Option<Value> {
    let key = origin_key(origin)?;
    let models = &data["routes"];
    let mut model = [id, unversioned_slug(id)]
        .into_iter()
        .find_map(|id| {
            let entry = &models[format!("{key}\0{id}")];
            (entry["id"] == id).then(|| entry.clone())
        })
        .or_else(|| documented_model(origin, id))?;
    if let Some(policy) = discovery_facts(id, origin)
        && policy.context.is_none()
    {
        model["reasoning"] = json!(true);
        model["reasoningLevels"] = json!(policy.levels);
        if let Some(limit) = policy.output {
            model["maxOutputTokens"] = json!(
                model["maxOutputTokens"]
                    .as_u64()
                    .unwrap_or(limit)
                    .min(limit)
            );
        }
    }
    Some(model)
}

fn global_model(data: &Value, id: &str) -> Option<Value> {
    let models = data["models"].as_object()?;
    let requested = bare(id);
    let mut matches: Vec<_> = models
        .iter()
        .filter(|(id, row)| row["id"] == id.as_str() && bare(id) == requested)
        .map(|(_, row)| row)
        .collect();
    matches.sort_by(|a, b| {
        richness(b)
            .cmp(&richness(a))
            .then_with(|| a["id"].as_str().cmp(&b["id"].as_str()))
    });
    let (richest, others) = matches.split_first()?;
    let mut result = (*richest).clone();
    for incoming in others {
        merge_metadata(&mut result, incoming, false);
    }
    if models.get(id).is_some_and(|model| model["id"] == id) {
        result["id"] = json!(id);
    }
    Some(result)
}

fn merge_metadata(model: &mut Value, incoming: &Value, prefer_incoming: bool) {
    for field in ["name", "contextLength", "maxOutputTokens"] {
        if model[field].is_null() || (prefer_incoming && !incoming[field].is_null()) {
            model[field] = incoming[field].clone();
        }
    }
    if model["modalities"].as_array().is_none_or(Vec::is_empty)
        || (prefer_incoming
            && incoming["modalities"]
                .as_array()
                .is_some_and(|list| !list.is_empty()))
    {
        model["modalities"] = incoming["modalities"].clone();
    }
    model["reasoning"] = json!(model["reasoning"] == true || incoming["reasoning"] == true);
    let mut efforts: BTreeSet<_> = [&model["reasoningLevels"], &incoming["reasoningLevels"]]
        .into_iter()
        .filter_map(Value::as_array)
        .flatten()
        .filter_map(Value::as_str)
        .filter_map(Effort::parse)
        .collect();
    if !efforts.is_empty() {
        efforts.insert(Effort::Default);
    }
    model["reasoningLevels"] = json!(efforts);
    if model["pricing"].is_null() {
        model["pricing"] = incoming["pricing"].clone();
    } else if incoming["pricing"].is_object() {
        for component in ["input", "output"] {
            if model["pricing"][component].is_null()
                || (prefer_incoming && !incoming["pricing"][component].is_null())
            {
                model["pricing"][component] = incoming["pricing"][component].clone();
            }
        }
        if prefer_incoming {
            model["pricing"]["currency"] = incoming["pricing"]["currency"].clone();
        }
    }
    if let Some(model) = model.as_object_mut() {
        for field in ["apiType", "providerKey", "sourceUrl", "outputModalities"] {
            model.shift_remove(field);
        }
    }
}

fn origin_key(raw: &str) -> Option<String> {
    let url = parse_origin(raw)?;
    if url.scheme() != "https" {
        return None;
    }
    if direct_go(&url) {
        // The earlier panel used this key for direct Go route metadata.
        return Some("opencode.ai/zen/go".into());
    }
    let host = match url.host_str()? {
        "opencode.ai" if url.port().is_some() => return None,
        "api.kimi.com" => "api.kimi.ai",
        host => host,
    };
    let path = url.path().trim_end_matches('/');
    Some(format!(
        "{host}{}",
        path.strip_suffix("/v1").unwrap_or(path)
    ))
}

fn bare(id: &str) -> &str {
    id.rsplit('/').next().unwrap_or(id)
}

fn richness(row: &Value) -> usize {
    let count = ["name", "contextLength", "maxOutputTokens", "pricing"]
        .into_iter()
        .filter(|key| !row[*key].is_null())
        .count();
    let modalities = row["modalities"]
        .as_array()
        .is_some_and(|value| !value.is_empty());
    let efforts = row["reasoningLevels"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|effort| *effort != "default")
        .count();
    count + usize::from(modalities) + efforts.max(usize::from(row["reasoning"] == true))
}

fn documented_model(origin: &str, id: &str) -> Option<Value> {
    if parse_origin(origin)?.host_str()? != "api.openai.com" {
        return None;
    }
    let slug = match unversioned_slug(id) {
        "gpt-3.5-turbo-0125" | "gpt-3.5-turbo-1106" => "gpt-3.5-turbo",
        "gpt-4-0613" => "gpt-4",
        slug => slug,
    };
    let (context, output, text_only) = match slug {
        "gpt-3.5-turbo" => (16_385, 4_096, true),
        "gpt-3.5-turbo-instruct" => (4_096, 4_096, true),
        "gpt-4" => (8_192, 8_192, true),
        "gpt-5-codex" | "gpt-5.1-codex" | "gpt-5.1-codex-max" | "gpt-5.2-codex" => {
            (400_000, 128_000, false)
        }
        "o4-mini-deep-research" => (200_000, 100_000, false),
        "gpt-5-chat-latest"
        | "gpt-5.1-chat-latest"
        | "gpt-5.2-chat-latest"
        | "gpt-5.3-chat-latest" => (128_000, 16_384, false),
        _ => return None,
    };
    let levels = match slug {
        _ if text_only => json!(["default"]),
        "gpt-5.1-codex-max" | "gpt-5.2-codex" => {
            json!(["default", "low", "medium", "high", "xhigh"])
        }
        _ => json!([]),
    };
    Some(json!({
        "id":id,"name":null,"contextLength":context,"maxOutputTokens":output,
        "modalities":if text_only { json!(["text"]) } else { json!(["text","image"]) },
        "reasoning":!text_only,"reasoningLevels":levels,"pricing":null,
        "providerKey":"openai","sourceUrl":format!("https://developers.openai.com/api/docs/models/{slug}"),
        "outputModalities":["text"]
    }))
}

#[cfg(test)]
mod tests;
