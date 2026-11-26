use std::{io, time::Duration};

use serde_json::{Map, Value, json};
use url::Url;

use super::{Inventory, SOURCES, merge_metadata, normalize_cached, origin_key};
use crate::discovery::models::{VendorReasoning, first_array, modality_list, positive};

const MAX_PUBLIC_BYTES: usize = 32 * 1024 * 1024;

pub(super) struct PublicCatalog {
    client: reqwest::Client,
    urls: [Url; 2],
}

pub(super) struct Feed {
    models: Map<String, Value>,
    routes: Option<Map<String, Value>>,
}

pub(super) type Results = [Result<Feed, String>; 2];

impl PublicCatalog {
    pub(super) fn new() -> io::Result<Self> {
        let first = Url::parse(SOURCES[0].1).map_err(io::Error::other)?;
        let second = Url::parse(SOURCES[1].1).map_err(io::Error::other)?;
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(Duration::from_secs(10))
            .user_agent("OpenBot-model-catalog/1.0")
            .build()
            .map_err(io::Error::other)?;
        Ok(Self {
            client,
            urls: [first, second],
        })
    }

    pub(super) async fn fetch(&self) -> Results {
        let (router, dev) = tokio::join!(self.fetch_source(0), self.fetch_source(1));
        [router, dev]
    }

    async fn fetch_source(&self, index: usize) -> Result<Feed, String> {
        let (name, _) = SOURCES[index];
        let mut response = self
            .client
            .get(self.urls[index].clone())
            .header(reqwest::header::ACCEPT, "application/json")
            .send()
            .await
            .map_err(|_| format!("{name} is unreachable."))?;
        if !response.status().is_success() {
            return Err(format!(
                "{name} returned HTTP {}.",
                response.status().as_u16()
            ));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| format!("{name} response could not be read."))?
        {
            if bytes.len() + chunk.len() > MAX_PUBLIC_BYTES {
                return Err(format!("{name} returned an oversized catalog."));
            }
            bytes.extend_from_slice(&chunk);
        }
        tokio::task::spawn_blocking(move || {
            let data = serde_json::from_slice(&bytes)
                .map_err(|_| format!("{name} returned a non-JSON body."))?;
            let feed = if index == 0 {
                router_feed(&data)
            } else {
                dev_feed(&data)
            };
            (!feed.models.is_empty())
                .then_some(feed)
                .ok_or_else(|| format!("{name} returned no model records."))
        })
        .await
        .map_err(|_| format!("{name} data could not be processed."))?
    }

    #[cfg(test)]
    pub(super) fn with_urls(urls: [Url; 2]) -> Self {
        Self {
            urls,
            ..Self::new().unwrap()
        }
    }
}

pub(super) fn combine(current: &Inventory, results: Results, stamp: &str) -> Option<Inventory> {
    if results.iter().all(Result::is_err) {
        return None;
    }
    let mut data = current.data.clone().unwrap_or_else(|| {
        json!({
            "models":{}, "routes":{}, "sources":[]
        })
    });
    let mut sources = Vec::with_capacity(SOURCES.len());
    for (index, result) in results.into_iter().enumerate() {
        let (name, url) = SOURCES[index];
        match result {
            Ok(feed) => {
                let count = feed.models.len();
                let models = data["models"].as_object_mut()?;
                for (id, row) in feed.models {
                    insert(models, id, row, index == 1);
                }
                if let Some(routes) = feed.routes {
                    data["routes"] = Value::Object(routes);
                }
                sources.push(json!({"name":name,"url":url,"modelCount":count,"lastFetched":stamp}));
            }
            Err(_) => sources.push(source_row(&data, name, url)),
        }
    }
    data["lastFetched"] = json!(stamp);
    data["totalModels"] = json!(data["models"].as_object()?.len());
    data["sources"] = json!(sources);
    Some(Inventory::new(Some(data)))
}

pub(super) fn source_row(data: &Value, name: &str, url: &str) -> Value {
    let cached = data["sources"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|entry| entry["name"] == name)
        .unwrap_or(&Value::Null);
    json!({
        "name":name, "url":url, "modelCount":cached["modelCount"].as_u64().unwrap_or(0),
        "lastFetched":cached["lastFetched"].as_str()
    })
}

fn router_feed(data: &Value) -> Feed {
    let mut models = Map::new();
    for row in data["data"].as_array().into_iter().flatten() {
        if let Some(model) = normalized(row, false, false) {
            let id = model["id"].as_str().unwrap_or_default().to_owned();
            insert(&mut models, id, model, false);
        }
    }
    Feed {
        models,
        routes: None,
    }
}

fn dev_feed(data: &Value) -> Feed {
    let mut models = Map::new();
    let mut routes = Map::new();
    for (key, provider) in data.as_object().into_iter().flatten() {
        let api = provider["api"]
            .as_str()
            .or_else(|| (key == "openai").then_some("https://api.openai.com/v1"));
        let origin = api.and_then(origin_key);
        for raw in provider["models"]
            .as_object()
            .into_iter()
            .flat_map(|rows| rows.values())
        {
            let Some(model) = normalized(raw, true, false) else {
                continue;
            };
            let id = model["id"].as_str().unwrap_or_default().to_owned();
            insert(&mut models, id.clone(), model, false);
            if let Some(origin) = &origin
                && let Some(mut route) = normalized(raw, true, true)
            {
                let sdk = raw["provider"]["npm"]
                    .as_str()
                    .or_else(|| provider["npm"].as_str());
                if let Some(protocol) = match sdk {
                    Some("@ai-sdk/openai") => Some("responses"),
                    Some("@ai-sdk/anthropic") => Some("anthropic"),
                    Some("@ai-sdk/openai-compatible") => Some("chat-completions"),
                    _ => None,
                } {
                    route["apiType"] = json!(protocol);
                }
                route["providerKey"] = json!(key);
                route["sourceUrl"] = json!(SOURCES[1].1);
                route["outputModalities"] =
                    json!(modality_list(raw["modalities"]["output"].as_array(), false));
                routes.insert(format!("{origin}\0{id}"), route);
            }
        }
    }
    Feed {
        models,
        routes: Some(routes),
    }
}

fn normalized(row: &Value, models_dev: bool, route_scoped: bool) -> Option<Value> {
    let id = row["id"].as_str()?.trim();
    let architecture = &row["architecture"];
    let (context, output, modes, pricing) = if models_dev {
        let limits = &row["limit"];
        (
            positive(
                [
                    &limits["context"],
                    &row["context_length"],
                    &row["contextLength"],
                ],
                u64::MAX,
            ),
            if route_scoped && limits["output"] == 0 {
                Some(0)
            } else {
                positive(
                    [
                        &limits["output"],
                        &row["max_output_tokens"],
                        &row["maxOutputTokens"],
                    ],
                    524_288,
                )
            },
            first_array([
                &row["modalities"]["input"],
                &row["input_modalities"],
                &row["modalities"],
            ]),
            row.get("cost")
                .filter(|value| !value.is_null())
                .unwrap_or(&row["pricing"]),
        )
    } else {
        (
            positive(
                [
                    &row["context_length"],
                    &row["contextLength"],
                    &architecture["context_length"],
                ],
                u64::MAX,
            ),
            positive(
                [
                    &row["top_provider"]["max_completion_tokens"],
                    &row["max_completion_tokens"],
                    &row["maxOutputTokens"],
                ],
                524_288,
            ),
            first_array([
                &row["input_modalities"],
                &architecture["input_modalities"],
                &row["modalities"],
            ]),
            &row["pricing"],
        )
    };
    let hint = reasoning_hint(row, id);
    let reasoning = VendorReasoning::read(row);
    let supported = reasoning.supported(hint);
    let name = row["name"].as_str().filter(|name| !name.trim().is_empty());
    normalize_cached(&json!({
        "id":id, "name":name, "contextLength":context, "maxOutputTokens":output,
        "modalities":modality_list(modes, false), "reasoning":supported,
        "reasoningLevels":reasoning.allow_list(hint, route_scoped), "pricing":pricing
    }))
}

fn reasoning_hint(row: &Value, id: &str) -> bool {
    let described = first_array([&row["supported_parameters"], &row["supportedParameters"]])
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .any(|parameter| {
            matches!(
                parameter.to_ascii_lowercase().as_str(),
                "reasoning" | "reasoning_effort" | "thinking" | "thinking_effort"
            )
        });
    let name = row["name"]
        .as_str()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let id = id.to_ascii_lowercase();
    described
        || [
            "reasoner",
            "reasoning",
            "deepseek-r",
            ":thinking",
            "qwq",
            "-r1",
        ]
        .into_iter()
        .any(|hint| id.contains(hint) || name.contains(hint))
}

fn insert(map: &mut Map<String, Value>, id: String, row: Value, prefer_incoming: bool) {
    match map.get_mut(&id) {
        Some(existing) => merge_metadata(existing, &row, prefer_incoming),
        None => {
            map.insert(id, row);
        }
    }
}

#[cfg(test)]
mod tests;
