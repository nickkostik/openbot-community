use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::{Map, Value};

use crate::catalog::{Catalog, Effort};

type Assignments = BTreeMap<String, String>;
type Reasoning = BTreeMap<String, BTreeMap<String, Effort>>;

#[derive(Debug, Default)]
pub struct Settings {
    assignments: Assignments,
    reasoning: Reasoning,
    extra: Map<String, Value>,
}

#[derive(Debug, Serialize)]
pub struct View {
    assignments: Assignments,
    reasoning: Reasoning,
    available: Vec<String>,
}

fn safe_id(value: &str) -> bool {
    !value.trim().is_empty() && !matches!(value, "__proto__" | "constructor" | "prototype")
}

fn text_id(value: &Value) -> Option<&str> {
    value.as_str().filter(|value| safe_id(value))
}

fn entries(value: &Value) -> impl Iterator<Item = (&String, &Value)> {
    value.as_object().into_iter().flatten()
}

impl Settings {
    pub fn from_saved(mut value: Map<String, Value>) -> Self {
        let assignments = entries(&value.remove("assignments").unwrap_or_default())
            .filter_map(|(bot, model)| {
                let model = model.as_str().filter(|model| !model.trim().is_empty())?;
                safe_id(bot).then(|| (bot.clone(), model.into()))
            })
            .collect();
        let reasoning = entries(&value.remove("reasoning").unwrap_or_default())
            .filter(|(bot, models)| safe_id(bot) && models.is_object())
            .map(|(bot, models)| {
                let choices = entries(models)
                    .filter_map(|(model, choice)| {
                        let effort = Effort::parse(choice.as_str()?)?;
                        safe_id(model).then(|| (model.clone(), effort))
                    })
                    .collect();
                (bot.clone(), choices)
            })
            .collect();
        Self {
            assignments,
            reasoning,
            extra: value,
        }
    }

    pub fn view(&self, catalog: &Catalog) -> View {
        let mut reasoning = self.reasoning.clone();
        for choices in reasoning.values_mut() {
            for (id, effort) in choices {
                if let Some(model) = catalog.chat_model(id) {
                    *effort = model.display_effort(*effort);
                }
            }
        }
        View {
            assignments: self.assignments.clone(),
            reasoning,
            available: catalog
                .models
                .iter()
                .filter(|model| model.chat)
                .map(|model| model.id.clone())
                .collect(),
        }
    }

    pub fn saved(&self) -> Result<Map<String, Value>, serde_json::Error> {
        let mut record = self.extra.clone();
        record.insert(
            "assignments".into(),
            serde_json::to_value(&self.assignments)?,
        );
        record.insert("reasoning".into(), serde_json::to_value(&self.reasoning)?);
        Ok(record)
    }

    pub fn update(&mut self, change: &Value, catalog: &Catalog) -> Result<(), String> {
        let change = change
            .as_object()
            .ok_or("expected botId/botIds and model or reasoning settings")?;
        if let Some(assignments) = change.get("assignments") {
            if change.contains_key("reasoningEffort") {
                return Err("save assignments and reasoning separately".into());
            }
            let replacement = assignments
                .as_object()
                .ok_or("assignments must be an object")?;
            let mut next = Assignments::new();
            for (bot, model) in replacement {
                let available = model
                    .as_str()
                    .filter(|model| catalog.chat_model(model).is_some());
                match available {
                    Some(model) if safe_id(bot) => next.insert(bot.clone(), model.into()),
                    _ => return Err(unknown_model(model)),
                };
            }
            self.assignments = next;
            return Ok(());
        }

        let ids = collect_ids(
            change.get("botIds"),
            change.get("botId"),
            "botId or nonempty botIds is required",
        )?;
        let assignment = change.get("modelId");
        let effort = change.get("reasoningEffort");
        if assignment.is_none() && effort.is_none() {
            return Err("modelId or reasoningEffort is required".into());
        }
        let model = match assignment {
            Some(Value::Null) | None => None,
            Some(Value::String(model)) if model.is_empty() => None,
            Some(value) => Some(
                value
                    .as_str()
                    .filter(|model| catalog.chat_model(model).is_some())
                    .ok_or_else(|| unknown_model(value))?,
            ),
        };

        if let Some(effort) = effort {
            let models_error = "reasoningModelId or modelIds must name available models";
            let selected = collect_ids(
                change.get("modelIds"),
                change
                    .get("reasoningModelId")
                    .filter(|value| !value.is_null())
                    .or(assignment),
                models_error,
            )?;
            let routes = selected
                .iter()
                .map(|id| catalog.chat_model(id).ok_or(models_error))
                .collect::<Result<Vec<_>, _>>()?;
            let effort = match effort {
                Value::Null => None,
                other => Some(
                    other
                        .as_str()
                        .and_then(Effort::parse)
                        .ok_or("unsupported reasoning effort")?,
                ),
            };
            if let Some(level) = effort {
                for route in &routes {
                    if !route.levels.contains(&level) {
                        let label = change["reasoningEffort"].as_str().unwrap_or_default();
                        return Err(format!(
                            "{} does not support reasoning effort {label}",
                            route.id
                        ));
                    }
                }
            }

            for id in &ids {
                let choices = self.reasoning.entry((*id).into()).or_default();
                for route in &routes {
                    if let Some(level) = effort {
                        choices.insert(route.id.clone(), level);
                    } else {
                        choices.remove(&route.id);
                    }
                }
                if choices.is_empty() {
                    self.reasoning.remove(*id);
                }
            }
        }
        if assignment.is_some() {
            for id in ids {
                if let Some(model) = model {
                    self.assignments.insert(id.into(), model.into());
                } else {
                    self.assignments.remove(id);
                }
            }
        }
        Ok(())
    }
}

fn collect_ids<'a>(
    list: Option<&'a Value>,
    single: Option<&'a Value>,
    error: &'static str,
) -> Result<Vec<&'a str>, &'static str> {
    let ids: Vec<_> = if let Some(list) = list.and_then(Value::as_array) {
        list.iter().map(text_id).collect()
    } else {
        vec![single.and_then(text_id)]
    };
    if ids.is_empty() {
        return Err(error);
    }
    ids.into_iter().collect::<Option<_>>().ok_or(error)
}

fn unknown_model(value: &Value) -> String {
    if let Some(model) = value.as_str() {
        format!("unknown modelId: {model}")
    } else {
        "unknown modelId".into()
    }
}
