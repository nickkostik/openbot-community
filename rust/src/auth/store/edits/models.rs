use std::collections::HashSet;

use super::{
    AuthError, Catalog, Model, Protocol, Value, direct_chat, json, mode_conflict, set_wildcard,
    store_error,
};
use crate::{
    catalog::{Effort, discovery_facts},
    routing::parse_origin,
};

const CONTEXT_DEFAULT: u64 = 128_000;
const OUTPUT_DEFAULT: u64 = 65_536;
const OUTPUT_CEILING: u64 = 131_072;
const MODALITIES: [&str; 4] = ["text", "image", "audio", "video"];

pub(super) fn upsert(
    plan: &mut Value,
    provider_id: &str,
    slug: &str,
    command: &Value,
    activate: bool,
) -> Result<(), AuthError> {
    if slug.is_empty() || slug.chars().any(|ch| ch.is_whitespace() || ch.is_control()) {
        return Err(AuthError::input("Enter a model identifier without spaces."));
    }
    let catalog = Catalog::from_plan(plan);
    let provider = catalog
        .providers
        .iter()
        .find(|row| row.id == provider_id)
        .ok_or_else(|| AuthError::input("Select an existing provider."))?;
    let id = format!("{provider_id}:{slug}");
    let rows = plan["catalog"]["models"]
        .as_array_mut()
        .ok_or_else(store_error)?;
    let index = rows.iter().position(|row| row["id"] == id);
    let mut row = index.map(|index| rows[index].clone()).unwrap_or_else(|| {
        json!({
            "id":id, "providerId":provider_id, "slug":slug, "modalities":["text"], "parameters":[]
        })
    });
    let facts = discovery_facts(slug, &provider.origin);
    let default_context = facts
        .as_ref()
        .and_then(|facts| facts.context)
        .unwrap_or(CONTEXT_DEFAULT);
    let context = tokens(selected(command, &row, "contextTokens"), 10_000_000);
    let output = tokens(selected(command, &row, "maxOutputTokens"), OUTPUT_CEILING);
    let cap = facts
        .as_ref()
        .and_then(|facts| facts.output)
        .unwrap_or(OUTPUT_CEILING)
        .min(OUTPUT_CEILING);
    row["contextTokens"] = json!(context.unwrap_or(default_context));
    row["maxOutputTokens"] = json!(output.unwrap_or(OUTPUT_DEFAULT).min(cap));
    if let Some(value) = command.get("modalities").filter(|value| !value.is_null()) {
        let types = modalities(value);
        row["modalities"] = if types.is_empty() {
            json!(["text"])
        } else {
            json!(types)
        };
    }
    if let Some(value) = command.get("modelType").filter(|value| !value.is_null()) {
        match value.as_str() {
            Some("chat" | "embedding" | "decision" | "image" | "other") => {
                row["modelType"] = value.clone();
            }
            _ => return Err(AuthError::input("Select a supported model type.")),
        }
    }
    if let Some(value) = command.get("apiType").filter(|value| !value.is_null()) {
        row["apiType"] = json!(
            Protocol::parse(value)
                .ok_or_else(|| AuthError::input("Select a supported API protocol."))?
        );
    }
    if let Some(value) = command
        .get("catalogLimits")
        .filter(|value| !value.is_null())
    {
        row["catalogLimits"] = catalog_limits(value)?;
    }
    if let Some(value) = command
        .get("reasoningLevels")
        .filter(|value| !value.is_null())
    {
        if !value.is_array() && !value.is_string() {
            return Err(AuthError::input("Use a list of reasoning efforts."));
        }
        row["reasoningLevels"] = value.clone();
    }
    let model = Model::from_saved(&row, &catalog.providers).ok_or_else(store_error)?;
    model.apply_embedding_metadata(&mut row);
    let levels = facts
        .as_ref()
        .map_or(model.levels.as_slice(), |facts| facts.levels);
    let effort = match command
        .get("activeReasoning")
        .filter(|choice| !choice.is_null())
    {
        Some(choice) => choice
            .as_str()
            .and_then(Effort::parse)
            .map(|choice| model.display_effort(choice))
            .filter(|choice| levels.contains(choice))
            .ok_or_else(|| AuthError::input("The model does not support that reasoning effort."))?,
        None => {
            // Resolve the previous selection using the original row, including
            // the legacy Off/default distinction, before changing its levels.
            let previous =
                index.and_then(|index| Model::from_saved(&rows[index], &catalog.providers));
            previous
                .map(|old| model.display_effort(old.default_effort()))
                .filter(|choice| levels.contains(choice))
                .unwrap_or(Effort::Default)
        }
    };
    row["reasoningLevels"] = json!(levels);
    row["activeReasoning"] = json!(effort);
    if !row["parameters"].is_array() {
        row["parameters"] = json!([]);
    }
    if activate && !direct_chat(&catalog, &model) {
        return Err(AuthError::input(
            "Select a direct model that supports bot chat.",
        ));
    }
    if let Some(index) = index {
        rows[index] = row;
    } else {
        rows.push(row);
    }
    if model.wire_chat {
        if (activate || !has_wildcard(plan)) && direct_chat(&catalog, &model) {
            set_wildcard(plan, &model)?;
        }
    } else {
        trim_bindings(plan, [&model])?;
        if !has_wildcard(plan) {
            let remaining = Catalog::from_plan(plan);
            if let Some(next) = fallback(&remaining, Some(provider_id)) {
                set_wildcard(plan, next)?;
            }
        }
    }
    Ok(())
}

pub(super) fn remove(
    plan: &mut Value,
    id: &str,
    by_provider: bool,
    mode: &str,
) -> Result<(), AuthError> {
    let catalog = Catalog::from_plan(plan);
    let matches = |model: &Model| {
        if by_provider {
            model.provider_id == id
        } else {
            model.id == id
        }
    };
    let removed: Vec<_> = catalog
        .models
        .iter()
        .filter(|model| matches(model))
        .collect();
    if !by_provider && removed.is_empty() {
        return Err(AuthError::input("Select an existing model."));
    }
    let rows = plan["catalog"]["models"]
        .as_array_mut()
        .ok_or_else(store_error)?;
    let key = if by_provider { "providerId" } else { "id" };
    rows.retain(|row| row[key] != id);
    let lost_wildcard = trim_bindings(plan, removed.iter().copied())?;
    let left = Catalog::from_plan(plan);
    let same_provider = (lost_wildcard && !by_provider).then(|| removed[0].provider_id.as_str());
    let next = fallback(&left, same_provider);
    if by_provider && next.is_none() && mode == "custom" {
        return Err(mode_conflict());
    }
    if (lost_wildcard || by_provider && !has_wildcard(plan))
        && let Some(next) = next
    {
        set_wildcard(plan, next)?;
    }
    Ok(())
}

fn has_wildcard(plan: &Value) -> bool {
    plan["catalog"]["bindings"].as_array().is_some_and(|rows| {
        rows.iter()
            .any(|row| row["conversation"]["kind"] == "wildcard")
    })
}

fn fallback<'a>(catalog: &'a Catalog, prefer: Option<&str>) -> Option<&'a Model> {
    catalog
        .models
        .iter()
        .filter(|model| direct_chat(catalog, model))
        .min_by_key(|model| usize::from(prefer != Some(&model.provider_id)))
}

fn trim_bindings<'a>(
    plan: &mut Value,
    removed: impl IntoIterator<Item = &'a Model>,
) -> Result<bool, AuthError> {
    let removed: Vec<_> = removed.into_iter().collect();
    let ids: HashSet<_> = removed.iter().map(|model| model.id.as_str()).collect();
    let routes: HashSet<_> = removed
        .iter()
        .map(|model| (model.provider_id.as_str(), model.slug.as_str()))
        .collect();
    let mut keys = HashSet::new();
    let bindings = plan["catalog"]["bindings"]
        .as_array_mut()
        .ok_or_else(store_error)?;
    bindings.retain(|row| {
        if row["modelId"].as_str().is_none_or(|id| !ids.contains(id)) {
            return true;
        }
        let conversation = &row["conversation"];
        if conversation["kind"] == "wildcard" {
            keys.insert("*".to_owned());
        } else if let Some(id) = conversation["id"].as_str() {
            keys.insert(id.to_owned());
        }
        false
    });
    if let Some(agents) = plan["agents"].as_object_mut() {
        agents.retain(|key, row| {
            let matched = row["providerId"]
                .as_str()
                .zip(row["modelId"].as_str())
                .is_some_and(|route| routes.contains(&route));
            !keys.contains(key) && !matched
        });
    }
    Ok(keys.contains("*"))
}

fn selected<'a>(command: &'a Value, stored: &'a Value, key: &str) -> &'a Value {
    command
        .get(key)
        .filter(|value| !value.is_null())
        .unwrap_or(&stored[key])
}

fn tokens(value: &Value, ceiling: u64) -> Option<u64> {
    value
        .as_f64()
        .or_else(|| value.as_str()?.trim().parse().ok())
        .filter(|n| n.is_finite() && *n >= 1.0 && *n <= ceiling as f64)
        .map(|n| n as u64)
}

fn modalities(value: &Value) -> Vec<String> {
    let ordered = match value {
        Value::String(text) => text
            .split(|ch: char| ch.is_whitespace() || ch == ',')
            .collect::<Vec<_>>(),
        Value::Array(rows) => rows.iter().filter_map(Value::as_str).collect(),
        _ => Vec::new(),
    };
    let mut seen = HashSet::new();
    ordered
        .into_iter()
        .filter_map(|item| {
            let item = item.trim().to_ascii_lowercase();
            (MODALITIES.contains(&item.as_str()) && seen.insert(item.clone())).then_some(item)
        })
        .collect()
}

fn catalog_limits(value: &Value) -> Result<Value, AuthError> {
    let source = value["sourceUrl"]
        .as_str()
        .filter(|source| parse_origin(source).is_some_and(|url| url.scheme() == "https"));
    let Some((source, key)) = source.zip(value["providerKey"].as_str()) else {
        return Err(AuthError::input(
            "Catalog limits require a public source and provider key.",
        ));
    };
    Ok(json!({
        "sourceUrl":source, "providerKey":key,
        "contextTokens":value["contextTokens"].as_u64().filter(|n| (1..=100_000_000).contains(n)),
        "maxOutputTokens":value["maxOutputTokens"].as_u64().filter(|n| *n<=524_288),
        "outputModalities":modalities(&value["outputModalities"])
    }))
}
