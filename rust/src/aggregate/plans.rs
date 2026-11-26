use std::collections::{BTreeSet, HashMap, HashSet};

use futures_util::{StreamExt, stream};
use serde_json::{Value, json};

use super::{Inventory, provider_model};
use crate::{
    auth::{AuthError, CredentialStore},
    catalog::{Catalog, Effort, Model, Provider, discovery_facts},
    discovery::Discovery,
    routing::{direct_go, parse_origin},
    server::{BOT_MODELS_MAX_BYTES, PLAN_MAX_BYTES, State, read_json},
};

const DEFAULT_CONTEXT: u64 = 128_000;
const OUTPUT_BUDGET: u64 = 65_536;

pub(super) struct Target {
    provider: Provider,
    saved: Value,
}

pub(super) struct Listing {
    target: Target,
    models: Result<Vec<Value>, &'static str>,
}

#[derive(Default)]
pub(super) struct Changes {
    pub added: usize,
    pub updated: usize,
    pub reasoning_held: usize,
}

#[derive(Default)]
struct Protected<'a> {
    selected: HashSet<&'a str>,
    reasoning: HashMap<&'a str, BTreeSet<Effort>>,
    unreadable: bool,
}

pub(super) fn targets(state: &State) -> Result<Vec<Target>, AuthError> {
    let plan = read_json(&state.root().join("openbot-plan.json"), PLAN_MAX_BYTES)
        .map_err(|_| invalid_plan())?;
    let Some(plan) = plan else {
        return Ok(Vec::new());
    };
    validate_plan(&plan)?;
    Ok(plan["catalog"]["providers"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| {
            Provider::from_saved(row).map(|provider| Target {
                provider,
                saved: row.clone(),
            })
        })
        .collect())
}

pub(super) async fn collect(discovery: &Discovery, targets: Vec<Target>) -> Vec<Listing> {
    stream::iter(targets.into_iter().map(|target| async {
        let models = if allowed(&target) {
            discovery
                .list(&target.provider)
                .await
                .map_err(|error| error.kind())
                .and_then(|value| value["models"].as_array().cloned().ok_or("parse-error"))
        } else {
            Err("not-supported")
        };
        Listing { target, models }
    }))
    .buffered(2)
    .collect()
    .await
}

impl Listing {
    pub(super) fn status(&self) -> Value {
        let mut status = json!({
            "id":self.target.provider.id,
            "fetched":self.models.as_ref().map_or(0, Vec::len)
        });
        if let Err(kind) = self.models {
            status["errorKind"] = json!(kind);
        }
        status
    }
}

pub(super) fn update(
    state: &State,
    credentials: &CredentialStore,
    inventory: &Inventory,
    lists: &[Listing],
) -> Result<Changes, AuthError> {
    if lists.is_empty() {
        return Ok(Changes::default());
    }
    let _settings = state.lock_bot_settings().map_err(|_| invalid_plan())?;
    let protected = read_json(
        &state.root().join("openbot-bot-models.json"),
        BOT_MODELS_MAX_BYTES,
    );
    let unavailable = protected.is_err()
        || protected
            .as_ref()
            .is_ok_and(|value| value.as_ref().is_some_and(|value| !value.is_object()));
    let choices = protected.ok().flatten().unwrap_or_default();
    credentials
        .refresh_plan(|plan| {
            let changed = merge(plan, inventory, lists, &choices, unavailable)?;
            Ok((changed.added != 0 || changed.updated != 0, changed))
        })
        .map(Option::unwrap_or_default)
}

fn merge(
    plan: &mut Value,
    inventory: &Inventory,
    lists: &[Listing],
    choices: &Value,
    unavailable: bool,
) -> Result<Changes, AuthError> {
    validate_plan(plan)?;
    let catalog = Catalog::from_plan(plan);
    let saved = &plan["catalog"];
    let protected = Protected::new(saved, choices, unavailable);
    let providers: HashMap<_, _> = saved["providers"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|row| Some((row["id"].as_str()?, row)))
        .collect();
    let models: HashMap<_, _> = catalog
        .models
        .iter()
        .map(|model| (model.id.as_str(), model))
        .collect();
    let mut rows = saved["models"]
        .as_array()
        .cloned()
        .ok_or_else(invalid_plan)?;
    let mut ids: HashSet<_> = rows
        .iter()
        .filter_map(|row| row["id"].as_str().map(str::to_owned))
        .collect();
    let mut routes: HashSet<_> = catalog
        .models
        .iter()
        .map(|model| (model.provider_id.clone(), model.slug.clone()))
        .collect();
    let mut counts = Changes::default();
    let data = inventory.data.as_ref().unwrap_or(&Value::Null);
    for list in lists.iter().filter(|list| allowed(&list.target)) {
        let provider = &list.target.provider;
        if providers.get(provider.id.as_str()).copied() != Some(&list.target.saved) {
            continue;
        }
        let direct: HashMap<_, _> = list
            .models
            .as_ref()
            .ok()
            .into_iter()
            .flatten()
            .filter(|model| model["providerMetadata"] == "opencode-go")
            .filter_map(|model| Some((model["id"].as_str()?, model)))
            .collect();
        for row in &mut rows {
            let Some(current) = row["id"].as_str().and_then(|id| models.get(id)).copied() else {
                continue;
            };
            if current.provider_id != provider.id {
                continue;
            }
            let metadata = provider_model(data, &current.slug, &provider.origin);
            let direct = direct.get(current.slug.as_str()).copied();
            if let Some(updated) = refresh_model(
                row,
                current,
                metadata.as_ref(),
                direct,
                &protected,
                &mut counts,
            ) && *row != updated
            {
                *row = updated;
                counts.updated += 1;
            }
        }
        for fetched in list.models.as_ref().ok().into_iter().flatten() {
            let Some(slug) = fetched["id"]
                .as_str()
                .filter(|id| !id.is_empty() && !id.chars().any(char::is_whitespace))
            else {
                continue;
            };
            let id = format!("{}:{slug}", provider.id);
            let route = (provider.id.clone(), slug.into());
            if ids.contains(&id) || routes.contains(&route) {
                continue;
            }
            let metadata = provider_model(data, slug, &provider.origin);
            rows.push(import_model(
                &id,
                provider,
                slug,
                fetched,
                metadata.as_ref(),
            ));
            ids.insert(id);
            routes.insert(route);
            counts.added += 1;
        }
    }
    if counts.added != 0 || counts.updated != 0 {
        plan["catalog"]["models"] = json!(rows);
    }
    Ok(counts)
}

impl<'a> Protected<'a> {
    fn new(saved: &'a Value, choices: &'a Value, unreadable: bool) -> Self {
        let selected = saved["bindings"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|row| row["modelId"].as_str())
            .chain(
                choices["assignments"]
                    .as_object()
                    .into_iter()
                    .flat_map(|rows| rows.values())
                    .filter_map(Value::as_str),
            )
            .collect();
        let mut reasoning: HashMap<&str, BTreeSet<Effort>> = HashMap::new();
        for choices in choices["reasoning"]
            .as_object()
            .into_iter()
            .flat_map(|rows| rows.values())
        {
            for (model, effort) in choices.as_object().into_iter().flatten() {
                if let Some(effort) = effort.as_str().and_then(Effort::parse) {
                    reasoning.entry(model).or_default().insert(effort);
                }
            }
        }
        Self {
            selected,
            reasoning,
            unreadable,
        }
    }

    fn keeps(&self, current: &Model, levels: &Value) -> bool {
        let offered: BTreeSet<Effort> = levels
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .filter_map(Effort::parse)
            .collect();
        self.unreadable
            || !offered.contains(&current.default_effort())
            || self
                .reasoning
                .get(current.id.as_str())
                .is_some_and(|saved| {
                    saved
                        .iter()
                        .any(|effort| !offered.contains(&current.display_effort(*effort)))
                })
    }
}

fn refresh_model(
    original: &Value,
    model: &Model,
    public: Option<&Value>,
    direct: Option<&Value>,
    protected: &Protected<'_>,
    changes: &mut Changes,
) -> Option<Value> {
    let direct_levels = direct.and_then(|row| nonempty(&row["reasoningLevels"]));
    let model_type = direct
        .and_then(|row| row["modelType"].as_str())
        .filter(|kind| {
            if *kind == "chat" {
                original.get("modelType").is_some()
            } else {
                !model.chat
                    || (!protected.unreadable && !protected.selected.contains(model.id.as_str()))
            }
        });
    if public.is_none()
        && direct_levels.is_none()
        && model_type.is_none()
        && !model.has_documented_embedding_inputs()
    {
        return None;
    }
    let mut updated = original.clone();
    if let Some(public) = public {
        let context = number(&original["contextTokens"]).unwrap_or(DEFAULT_CONTEXT);
        let default_context = context == DEFAULT_CONTEXT
            || number(&original["catalogLimits"]["contextTokens"]) == Some(context);
        updated["contextTokens"] = json!(if default_context {
            number(&public["contextLength"]).unwrap_or(context)
        } else {
            context
        });
        let cap = number(&public["maxOutputTokens"]).map_or(model.max_output_tokens, |cap| {
            model.max_output_tokens.min(cap)
        });
        updated["maxOutputTokens"] = json!(cap);
        if let Some(modalities) = nonempty(&public["modalities"]) {
            updated["modalities"] = modalities.clone();
        } else if !original["modalities"].is_array() {
            updated["modalities"] = json!(["text"]);
        }
        if model.protocol.is_none()
            && let Some(protocol) = public["apiType"].as_str()
        {
            updated["apiType"] = json!(protocol);
        }
        if let Some(limits) = catalog_limits(public) {
            updated["catalogLimits"] = limits;
        }
    }
    if let Some(kind) = model_type {
        updated["modelType"] = json!(kind);
    }
    let levels = direct_levels
        .cloned()
        .or_else(|| public.map(|row| import_levels(&json!(model.levels), Some(row), false)));
    if let Some(levels) = levels {
        if protected.keeps(model, &levels) {
            // Preserve the valid saved ordering and choice when a source narrows its list.
            changes.reasoning_held += 1;
        } else {
            updated["reasoningLevels"] = levels;
        }
    }
    model.apply_embedding_metadata(&mut updated);
    Some(updated)
}

fn import_model(
    id: &str,
    provider: &Provider,
    slug: &str,
    fetched: &Value,
    public: Option<&Value>,
) -> Value {
    let go = fetched["providerMetadata"] == "opencode-go";
    let public = public.unwrap_or(&Value::Null);
    let documented = (provider.id == "openai")
        .then(|| discovery_facts(slug, &provider.origin))
        .flatten();
    let context = number(&fetched["contextLength"])
        .or_else(|| number(&public["contextLength"]))
        .or_else(|| documented.as_ref().and_then(|facts| facts.context))
        .unwrap_or(DEFAULT_CONTEXT);
    let output = number(&fetched["maxOutputTokens"])
        .or_else(|| number(&public["maxOutputTokens"]))
        .map_or(OUTPUT_BUDGET, |n| n.min(OUTPUT_BUDGET));
    let modes = if go || fetched["modelType"] == "embedding" {
        nonempty(&fetched["modalities"])
    } else {
        None
    }
    .or_else(|| nonempty(&public["modalities"]))
    .or_else(|| nonempty(&fetched["modalities"]));
    let levels = documented
        .map(|facts| json!(facts.levels))
        .unwrap_or_else(|| {
            import_levels(
                &fetched["reasoningLevels"],
                public.is_object().then_some(public),
                go,
            )
        });
    let protocol = fetched["apiType"]
        .as_str()
        .or_else(|| public["apiType"].as_str());
    let mut model = json!({
        "id":id, "providerId":provider.id, "slug":slug,
        "contextTokens":context, "maxOutputTokens":output,
        "reasoningLevels":levels, "activeReasoning":"default",
        "modalities":modes.cloned().unwrap_or_else(|| json!(["text"])), "parameters":[],
        "apiType":protocol.map_or_else(|| json!(provider.protocol), |protocol| json!(protocol))
    });
    if let Some(kind) = fetched["modelType"].as_str() {
        model["modelType"] = json!(kind);
    }
    if let Some(limits) = catalog_limits(public) {
        model["catalogLimits"] = limits;
    }
    model
}

fn import_levels(fetched: &Value, public: Option<&Value>, go: bool) -> Value {
    let direct = nonempty(fetched);
    if go && let Some(levels) = direct {
        return levels.clone();
    }
    if let Some(levels) = public
        .and_then(|row| nonempty(&row["reasoningLevels"]))
        .or(direct)
    {
        return levels.clone();
    }
    match public.and_then(|row| row["reasoning"].as_bool()) {
        Some(true) => json!(["default", "none", "high"]),
        Some(false) => json!(["default"]),
        None => json!(["default", "none", "low", "medium", "high"]),
    }
}

fn catalog_limits(row: &Value) -> Option<Value> {
    let url = row["sourceUrl"].as_str()?;
    let provider = row["providerKey"].as_str()?;
    if parse_origin(url)?.scheme() != "https" {
        return None;
    }
    Some(json!({
        "contextTokens":number(&row["contextLength"]).filter(|tokens| *tokens <= 100_000_000),
        "maxOutputTokens":row["maxOutputTokens"].as_u64().filter(|tokens| *tokens <= 524_288),
        "sourceUrl":url, "providerKey":provider,
        "outputModalities":row["outputModalities"].as_array().map_or_else(|| json!(["text"]), |modes| json!(modes))
    }))
}

fn allowed(target: &Target) -> bool {
    let provider = &target.provider;
    let fields = format!(
        "{} {} {}",
        provider.id,
        target.saved["name"].as_str().unwrap_or_default(),
        provider.origin
    )
    .to_ascii_lowercase();
    let mut previous = "";
    for word in fields.split(|ch: char| !ch.is_ascii_alphanumeric() && ch != '_') {
        if matches!(word, "n5" | "metakey") || (previous == "meta" && word == "key") {
            return false;
        }
        previous = word;
    }
    !provider.id.to_ascii_lowercase().starts_with("opencode")
        || parse_origin(&provider.origin).is_some_and(|origin| direct_go(&origin))
}

fn nonempty(value: &Value) -> Option<&Value> {
    value
        .as_array()
        .is_some_and(|items| !items.is_empty())
        .then_some(value)
}

fn number(value: &Value) -> Option<u64> {
    value
        .as_u64()
        .or_else(|| value.as_str()?.trim().parse().ok())
        .filter(|value| *value > 0)
}

fn validate_plan(plan: &Value) -> Result<(), AuthError> {
    if plan["kind"] == "custom"
        && ["providers", "models", "bindings"]
            .iter()
            .all(|field| plan["catalog"][*field].is_array())
    {
        Ok(())
    } else {
        Err(invalid_plan())
    }
}

fn invalid_plan() -> AuthError {
    AuthError::unavailable("OpenBot could not read or update its saved model catalog.")
}

#[cfg(test)]
mod tests;
