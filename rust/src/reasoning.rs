use serde_json::{Map, Value, json};
use url::Url;

use crate::{
    catalog::{Effort, PolicyKind, Protocol},
    routing::Selection,
};

type Body = Map<String, Value>;

fn remove(body: &mut Body, keys: &[&str]) {
    for key in keys {
        body.shift_remove(*key);
    }
}

fn nested(body: &mut Body, key: &str, update: impl FnOnce(&mut Body)) {
    let mut object = match body.shift_remove(key) {
        Some(Value::Object(object)) => object,
        _ => Map::new(),
    };
    update(&mut object);
    if !object.is_empty() {
        body.insert(key.into(), Value::Object(object));
    }
}

fn incoming(body: &Body, key: &str) -> Option<Effort> {
    body.get(key)?.as_str().and_then(Effort::parse)
}

fn apply_cap(body: &mut Body, selection: &Selection<'_>, protocol: Protocol) {
    let mut cap = selection.model.max_output_tokens;
    if matches!(selection.model.policy(), Some(PolicyKind::NvidiaToggle))
        && selection.model.slug == "nvidia/nemotron-3.5-lightning-30b-a3b"
    {
        cap = cap.min(32_768);
    }
    let key = if protocol == Protocol::Responses {
        "max_output_tokens"
    } else if protocol == Protocol::ChatCompletions && body.contains_key("max_completion_tokens") {
        "max_completion_tokens"
    } else {
        "max_tokens"
    };
    let requested = body
        .get(key)
        .or_else(|| body.get("max_completion_tokens"))
        .or_else(|| body.get("max_tokens"))
        .and_then(|value| {
            value
                .as_f64()
                .or_else(|| value.as_str()?.trim().parse().ok())
        })
        .filter(|tokens| tokens.is_finite() && *tokens >= 1.0)
        .map_or(cap, |tokens| tokens.min(cap as f64) as u64);
    remove(
        body,
        &["max_tokens", "max_completion_tokens", "max_output_tokens"],
    );
    body.insert(key.into(), json!(requested));
}

pub fn apply(body: &mut Body, selection: &Selection<'_>, protocol: Protocol) {
    let effort = selection.effort();
    let slug = selection.model.slug.to_ascii_lowercase();
    let origin = selection.provider.origin.to_ascii_lowercase();
    let direct_zai = matches!(
        origin.trim_end_matches('/'),
        "https://api.z.ai/api/coding/paas/v4" | "https://api.z.ai/api/paas/v4"
    ) && matches!(slug.as_str(), "glm-5.3" | "glm-5.3-flash");

    apply_cap(body, selection, protocol);
    if selection.effort_override.is_some() {
        let clear_thinking = body
            .get("thinking")
            .and_then(|value| value["clear_thinking"].as_bool());
        remove(
            body,
            &[
                "reasoning_effort",
                "reasoning",
                "thinking",
                "enable_thinking",
            ],
        );
        if direct_zai && let Some(clear) = clear_thinking {
            body.insert("thinking".into(), json!({"clear_thinking": clear}));
        }
        if body.contains_key("output_config") {
            nested(body, "output_config", |object| remove(object, &["effort"]));
        }
        if matches!(
            selection.model.policy(),
            Some(PolicyKind::NvidiaToggle | PolicyKind::NvidiaSuper)
        ) && body.contains_key("chat_template_kwargs")
        {
            nested(body, "chat_template_kwargs", |object| {
                remove(object, &["enable_thinking", "reasoning_effort"]);
            });
        }
    }

    match selection.model.policy() {
        Some(PolicyKind::DeepSeek) => deepseek(body, selection, protocol, effort),
        Some(PolicyKind::KimiCode) => kimi(body, selection, effort),
        Some(PolicyKind::NvidiaToggle) => nvidia(body, selection, effort, true),
        Some(PolicyKind::NvidiaSuper) => nvidia(body, selection, effort, false),
        None if direct_zai => zai(body, effort),
        None if origin.trim_end_matches('/')
            == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
            && matches!(
                slug.as_str(),
                "qwen4-max"
                    | "qwen3.8-max"
                    | "qwen3.8-max-0902"
                    | "qwen3.8-flash"
                    | "qwen3.8-2.4t-a95b"
                    | "qwen3.8-27b"
            ) =>
        {
            if !matches!(effort, Effort::Default | Effort::On) {
                remove(body, &["thinking_budget", "enable_thinking", "thinking"]);
                body.insert("reasoning_effort".into(), json!(effort));
            }
        }
        None => generic(body, selection, &slug, &origin, effort),
    }
}

fn effective(selection: &Selection<'_>, value: Option<Effort>) -> Option<Effort> {
    value
        .and_then(|value| selection.model.effective_override(value))
        .filter(|value| *value != Effort::Default)
}

fn deepseek(body: &mut Body, selection: &Selection<'_>, protocol: Protocol, saved: Effort) {
    let thinking = body
        .get("thinking")
        .and_then(|value| value["type"].as_str());
    let enabled = thinking == Some("enabled");
    let native = match protocol {
        Protocol::Responses => body.get("reasoning"),
        Protocol::Anthropic => body.get("output_config"),
        Protocol::ChatCompletions => None,
    }
    .and_then(|value| value["effort"].as_str())
    .and_then(Effort::parse);
    let effort = effective(selection, Some(saved)).or_else(|| {
        if thinking == Some("disabled") {
            Some(Effort::None)
        } else {
            effective(selection, native)
                .or_else(|| effective(selection, incoming(body, "reasoning_effort")))
                .filter(|effort| !enabled || *effort != Effort::None)
        }
    });
    remove(
        body,
        &[
            "thinking",
            "thinking_budget",
            "enable_thinking",
            "reasoning",
            "reasoning_effort",
        ],
    );
    if body
        .get("output_config")
        .is_some_and(|value| value.get("effort").is_some())
    {
        nested(body, "output_config", |object| remove(object, &["effort"]));
    }
    if protocol == Protocol::Responses {
        if let Some(effort) = effort {
            body.insert("reasoning_effort".into(), json!(effort));
        }
    } else if effort == Some(Effort::None) {
        body.insert("thinking".into(), json!({"type": "disabled"}));
    } else if effort.is_some() || enabled {
        body.insert("thinking".into(), json!({"type": "enabled"}));
        if let Some(effort) = effort {
            body.insert("reasoning_effort".into(), json!(effort));
        }
    }
}

fn kimi(body: &mut Body, selection: &Selection<'_>, saved: Effort) {
    let effort = effective(selection, Some(saved))
        .or_else(|| effective(selection, incoming(body, "reasoning_effort")));
    if let Some(effort) = effort {
        body.insert("reasoning_effort".into(), json!(effort));
    } else {
        body.shift_remove("reasoning_effort");
    }
    if body
        .get("thinking")
        .is_some_and(|value| value["type"] == "disabled")
    {
        body.shift_remove("thinking");
    }
    if body.get("enable_thinking") == Some(&Value::Bool(false)) {
        body.shift_remove("enable_thinking");
    }
    if body.get("output_config").is_some_and(Value::is_object) {
        nested(body, "output_config", |object| {
            if let Some(effort) = effective(selection, incoming(object, "effort")) {
                object.insert("effort".into(), json!(effort));
            } else {
                object.shift_remove("effort");
            }
        });
    }
}

fn nvidia(body: &mut Body, selection: &Selection<'_>, saved: Effort, toggle: bool) {
    let effort = effective(selection, Some(saved))
        .or_else(|| effective(selection, incoming(body, "reasoning_effort")));
    remove(
        body,
        &[
            "thinking",
            "enable_thinking",
            "reasoning",
            "reasoning_effort",
        ],
    );
    if let Some(effort) = effort {
        nested(body, "chat_template_kwargs", |options| {
            options.shift_remove("reasoning_effort");
            if toggle {
                options.insert("enable_thinking".into(), json!(effort == Effort::On));
            } else {
                options.shift_remove("enable_thinking");
            }
        });
        if !toggle {
            body.insert("reasoning_effort".into(), json!(effort));
        }
    }
}

fn zai(body: &mut Body, saved: Effort) {
    let allowed = |effort: &Effort| matches!(effort, Effort::Low | Effort::High | Effort::Max);
    let effort = Some(saved)
        .filter(allowed)
        .or_else(|| incoming(body, "reasoning_effort").filter(allowed));
    if let Some(effort) = effort {
        body.insert("reasoning_effort".into(), json!(effort));
    } else {
        body.shift_remove("reasoning_effort");
    }
    if body.contains_key("thinking") || effort.is_some() {
        nested(body, "thinking", |object| {
            object.insert("type".into(), json!("enabled"));
        });
    }
}

fn generic(body: &mut Body, selection: &Selection<'_>, slug: &str, origin: &str, effort: Effort) {
    let host = Url::parse(origin)
        .ok()
        .and_then(|url| url.host_str().map(str::to_owned));
    let fast = selection.effort_override.is_none() && selection.model.fast;
    if slug.starts_with("grok-") || slug.starts_with("grok.") || host.as_deref() == Some("api.x.ai")
    {
        let effort = if fast {
            Effort::Low
        } else if effort == Effort::Max {
            Effort::XHigh
        } else {
            effort
        };
        if matches!(
            effort,
            Effort::Low | Effort::Medium | Effort::High | Effort::XHigh
        ) {
            body.insert("reasoning_effort".into(), json!(effort));
        }
    } else if slug
        .strip_prefix("glm")
        .and_then(|tail| tail.as_bytes().first())
        .is_some_and(|byte| byte.is_ascii_digit() || matches!(byte, b'-' | b'.'))
        || host
            .as_deref()
            .is_some_and(|host| host == "bigmodel.cn" || host.ends_with(".bigmodel.cn"))
    {
        if fast || effort == Effort::None {
            body.insert("thinking".into(), json!({"type": "disabled"}));
        } else if matches!(
            effort,
            Effort::Low | Effort::Medium | Effort::High | Effort::XHigh | Effort::Max
        ) {
            body.entry("thinking")
                .or_insert_with(|| json!({"type": "enabled"}));
            insert_effort(
                body,
                if effort == Effort::XHigh {
                    Effort::Max
                } else {
                    effort
                },
            );
        }
    } else if effort == Effort::None {
        if host
            .as_deref()
            .is_some_and(|host| matches!(host, "api.openai.com" | "chatgpt.com"))
            || slug.split(['/', ':']).any(openai_model)
        {
            body.insert("reasoning_effort".into(), json!(Effort::None));
        } else {
            body.insert("thinking".into(), json!({"type": "disabled"}));
        }
    } else if !matches!(effort, Effort::Default | Effort::On) {
        insert_effort(body, effort);
    }
}

fn insert_effort(body: &mut Body, effort: Effort) {
    if body.get("reasoning_effort").is_none_or(Value::is_null) {
        body.insert("reasoning_effort".into(), json!(effort));
    }
}

fn openai_model(name: &str) -> bool {
    name.starts_with("gpt-")
        || name.starts_with("gpt.")
        || name
            .as_bytes()
            .get(..2)
            .is_some_and(|head| head[0] == b'o' && matches!(head[1], b'1'..=b'9'))
            && name
                .as_bytes()
                .get(2)
                .is_none_or(|byte| matches!(byte, b'-' | b'.' | b' '))
}
