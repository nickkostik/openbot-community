use std::fmt;

use clap::ValueEnum;
use serde::Serialize;
use serde_json::Value;
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
    pub protocol: Protocol,
    pub upstream: String,
    reasoning_effort: Effort,
    stale_assignment: bool,
}

pub struct Selection<'a> {
    pub model: &'a Model,
    pub provider: &'a Provider,
    pub effort_override: Option<Effort>,
    origin: Url,
    stale_assignment: bool,
}

impl Selection<'_> {
    pub fn effort(&self) -> Effort {
        self.effort_override
            .unwrap_or_else(|| self.model.default_effort())
    }

    pub fn route(&self, credential: CredentialKind) -> Route {
        let protocol = protocol_for(self.model, self.provider, &self.origin, credential);
        Route {
            provider_id: self.provider.id.clone(),
            model_id: self.model.id.clone(),
            model: self.model.slug.clone(),
            protocol,
            upstream: endpoint(self.origin.clone(), protocol, credential),
            reasoning_effort: self.effort(),
            stale_assignment: self.stale_assignment,
        }
    }
}

impl Route {
    pub fn encode_request(
        &self,
        mut body: Value,
        credential: CredentialKind,
    ) -> Result<Value, &'static str> {
        let values = body
            .as_object_mut()
            .ok_or("chat request must be an object")?;
        values.insert("model".into(), Value::String(self.model.clone()));
        let deepseek = Url::parse(&self.upstream)
            .ok()
            .is_some_and(|url| url.host_str() == Some("api.deepseek.com"));
        crate::wire::encode(
            self.protocol,
            body,
            deepseek,
            credential == CredentialKind::OpenAiOauth,
        )
    }
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
    Ok(select(catalog, settings, requested, bot_id)?.route(credential))
}

pub fn select<'a>(
    catalog: &'a Catalog,
    settings: &Settings,
    requested: &str,
    bot_id: Option<&str>,
) -> Result<Selection<'a>, RouteError> {
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
        .find(|provider| provider.id == model.provider_id && is_direct_provider(provider))
        .ok_or(RouteError::IncompleteProvider)?;
    let origin = parse_origin(&provider.origin).ok_or(RouteError::IncompleteProvider)?;
    Ok(Selection {
        model,
        provider,
        origin,
        effort_override: bot_id
            .and_then(|id| settings.effort_for_bot(id, &model.id))
            .and_then(|effort| model.effective_override(effort)),
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

pub fn parse_origin(raw: &str) -> Option<Url> {
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

pub(crate) fn retired_provider(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    let mut previous = "";
    for word in lower.split(|character: char| !character.is_ascii_alphanumeric()) {
        if matches!(word, "n5" | "metakey" | "modelhub") || previous == "meta" && word == "key" {
            return true;
        }
        previous = word;
    }
    false
}

pub(crate) fn is_direct_provider(provider: &Provider) -> bool {
    if [&provider.id, &provider.origin]
        .into_iter()
        .any(|field| retired_provider(field))
        || provider.name.as_deref().is_some_and(retired_provider)
    {
        return false;
    }
    let Some(origin) = parse_origin(&provider.origin) else {
        return false;
    };
    if provider.id.to_ascii_lowercase().starts_with("opencode")
        || origin.host_str() == Some("opencode.ai")
    {
        return direct_go(&origin);
    }
    origin.scheme() == "https"
        || origin.host().is_some_and(|host| match host {
            url::Host::Ipv4(ip) => ip.is_loopback(),
            url::Host::Ipv6(ip) => ip.is_loopback(),
            url::Host::Domain(domain) => domain == "localhost",
        })
}

pub fn direct_go(origin: &Url) -> bool {
    origin.scheme() == "https"
        && origin.port().is_none()
        && origin.host_str() == Some("opencode.ai")
        && matches!(
            origin.path().trim_end_matches('/'),
            "/inference/go"
                | "/inference/go/openai/v1"
                | "/inference/go/anthropic/v1"
                | "/zen/go"
                | "/zen/go/v1"
        )
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
    } else if direct_go(&origin) {
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
