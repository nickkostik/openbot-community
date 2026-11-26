use std::fmt;

use clap::ValueEnum;
use serde::Serialize;
use url::Url;

use crate::{
    bot_models::Settings,
    catalog::{Catalog, Effort, Model, Protocol, Provider},
};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, ValueEnum)]
pub enum CredentialKind {
    #[default]
    ApiKey,
    #[value(name = "openai-oauth")]
    OpenAiOauth,
    #[value(name = "chatgpt-plan")]
    ChatGptPlan,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Route {
    provider_id: String,
    model_id: String,
    model: String,
    protocol: Protocol,
    upstream: String,
    reasoning_effort: Effort,
    stale_assignment: bool,
}

#[derive(Debug)]
pub enum RouteError {
    UnknownModel,
    NonChatModel,
    IncompleteProvider,
}

impl fmt::Display for RouteError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::UnknownModel => "unknown model",
            Self::NonChatModel => "model does not support bot chat",
            Self::IncompleteProvider => "model provider is missing or invalid",
        })
    }
}

pub fn resolve(
    catalog: &Catalog,
    settings: &Settings,
    requested: &str,
    bot_id: Option<&str>,
    credential: CredentialKind,
) -> Result<Route, RouteError> {
    let pin = bot_id.and_then(|id| settings.model_for_bot(id));
    let mut stale_assignment = false;
    let selector = match pin {
        Some(pin)
            if catalog
                .models
                .iter()
                .any(|model| model.wire_chat && matches_model(model, pin)) =>
        {
            pin
        }
        Some(pin) => {
            stale_assignment = true;
            if pin == requested {
                catalog
                    .wildcard
                    .as_ref()
                    .map_or(requested, |binding| binding.model.as_str())
            } else {
                requested
            }
        }
        None => requested,
    };
    let model = find_model(catalog, selector).ok_or(RouteError::UnknownModel)?;
    if !model.wire_chat {
        return Err(RouteError::NonChatModel);
    }
    let provider = catalog
        .providers
        .iter()
        .find(|provider| provider.id == model.provider_id)
        .ok_or(RouteError::IncompleteProvider)?;
    let origin = parse_origin(&provider.origin).ok_or(RouteError::IncompleteProvider)?;
    let protocol = protocol_for(model, provider, &origin, credential);
    let effort = bot_id.and_then(|id| settings.effort_for_bot(id, &model.id));
    Ok(Route {
        provider_id: provider.id.clone(),
        model_id: model.id.clone(),
        model: model.slug.clone(),
        protocol,
        upstream: endpoint(origin, protocol, credential),
        reasoning_effort: model.routed_effort(effort),
        stale_assignment,
    })
}

fn matches_model(model: &Model, selector: &str) -> bool {
    model.id == selector || model.slug == selector
}

fn find_model<'a>(catalog: &'a Catalog, selector: &str) -> Option<&'a Model> {
    let active = catalog.wildcard.as_ref();
    if let Some(binding) = active {
        let model = catalog.models.iter().find(|model| {
            model.provider_id == binding.provider && matches_model(model, &binding.model)
        });
        if let Some(model) = model.filter(|model| matches_model(model, selector)) {
            return Some(model);
        }
    }
    catalog
        .models
        .iter()
        .find(|model| model.id == selector)
        .or_else(|| {
            catalog
                .models
                .iter()
                .filter(|model| model.slug == selector)
                .min_by_key(|model| {
                    if active.is_some_and(|binding| binding.provider == model.provider_id) {
                        0
                    } else if model.provider_id.starts_with("opencode") {
                        1
                    } else if model.provider_id == "openai" {
                        2
                    } else {
                        3
                    }
                })
        })
}

fn parse_origin(raw: &str) -> Option<Url> {
    let origin = Url::parse(raw).ok()?;
    if !matches!(origin.scheme(), "http" | "https")
        || origin.host().is_none()
        || !origin.username().is_empty()
        || origin.password().is_some()
        || origin.query().is_some()
        || origin.fragment().is_some()
    {
        return None;
    }
    Some(origin)
}

fn protocol_for(
    model: &Model,
    provider: &Provider,
    origin: &Url,
    credential: CredentialKind,
) -> Protocol {
    let native_openai = origin.scheme() == "https" && origin.host_str() == Some("api.openai.com");
    let slug = model.slug.to_ascii_lowercase();
    let forces_responses = ["gpt-5", "gpt-6"].iter().any(|prefix| {
        slug.strip_prefix(prefix)
            .is_some_and(|suffix| suffix.is_empty() || suffix.starts_with(['.', '-']))
    });
    if credential != CredentialKind::ApiKey || (native_openai && forces_responses) {
        return Protocol::Responses;
    }
    model.protocol.unwrap_or(provider.protocol)
}

fn endpoint(mut origin: Url, protocol: Protocol, credential: CredentialKind) -> String {
    match credential {
        CredentialKind::OpenAiOauth => {
            return "https://chatgpt.com/backend-api/codex/responses".into();
        }
        CredentialKind::ChatGptPlan => return "https://api.openai.com/v1/responses".into(),
        CredentialKind::ApiKey => {}
    }
    let path = origin.path().trim_end_matches('/').to_owned();
    let lower = path.to_ascii_lowercase();
    let scheme = origin.scheme();
    let host = origin.host_str();
    let direct = scheme == "https" && origin.port().is_none();
    let base = if direct
        && host == Some("api.deepseek.com")
        && matches!(
            lower.as_str(),
            "" | "/v1"
                | "/v1/chat/completions"
                | "/v1/responses"
                | "/chat/completions"
                | "/responses"
                | "/anthropic"
                | "/anthropic/v1"
                | "/anthropic/v1/messages"
        ) {
        match protocol {
            Protocol::Responses => {
                origin.set_path("/responses");
                return origin.into();
            }
            Protocol::Anthropic => "/anthropic/v1",
            Protocol::ChatCompletions => "/v1",
        }
    } else if direct
        && host == Some("opencode.ai")
        && matches!(
            path.as_str(),
            "/inference/go"
                | "/inference/go/openai/v1"
                | "/inference/go/anthropic/v1"
                | "/zen/go"
                | "/zen/go/v1"
        )
    {
        match protocol {
            Protocol::Anthropic => "/inference/go/anthropic/v1",
            Protocol::ChatCompletions | Protocol::Responses => "/inference/go/openai/v1",
        }
    } else {
        &path
    };
    let end = match protocol {
        Protocol::ChatCompletions => "/chat/completions",
        Protocol::Responses => "/responses",
        Protocol::Anthropic => "/messages",
    };
    let lower = base.to_ascii_lowercase();
    let resolved = if lower.ends_with(end) {
        base.to_owned()
    } else if lower.ends_with("/v1")
        || (protocol == Protocol::ChatCompletions && lower.ends_with("/v4"))
    {
        format!("{base}{end}")
    } else {
        format!("{base}/v1{end}")
    };
    origin.set_path(&resolved);
    origin.into()
}

#[cfg(test)]
mod tests;
