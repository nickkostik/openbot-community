use serde::{Deserialize, Serialize};
use serde_json::Value;
use url::Url;

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Effort {
    Default,
    None,
    On,
    Minimal,
    Low,
    Medium,
    High,
    XHigh,
    Max,
}

use Effort::{Default, High, Low, Max, Medium, Minimal, None as Off, On, XHigh};

const EFFORTS: [Effort; 9] = [Default, Off, On, Minimal, Low, Medium, High, XHigh, Max];
const DEFAULT_LEVELS: [Effort; 5] = [Default, Off, Low, Medium, High];
const OPENAI_LEVELS: [Effort; 7] = [Default, Off, Low, Medium, High, XHigh, Max];
const OPENAI_REQUIRED_LEVELS: [Effort; 6] = [Default, Low, Medium, High, XHigh, Max];

impl Effort {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "default" => Some(Default),
            "none" => Some(Off),
            "on" => Some(On),
            "minimal" => Some(Minimal),
            "low" => Some(Low),
            "medium" => Some(Medium),
            "high" => Some(High),
            "xhigh" => Some(XHigh),
            "max" => Some(Max),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct Policy {
    kind: PolicyKind,
    levels: &'static [Effort],
    aliases: &'static [(Effort, Effort)],
}

#[derive(Clone, Copy, Debug)]
pub enum PolicyKind {
    DeepSeek,
    KimiCode,
    NvidiaToggle,
    NvidiaSuper,
}

impl Policy {
    fn for_route(slug: &str, origin: &str) -> Option<Self> {
        let origin = Url::parse(origin).ok()?;
        if origin.scheme() != "https"
            || origin.port().is_some()
            || !origin.username().is_empty()
            || origin.password().is_some()
            || origin.query().is_some()
            || origin.fragment().is_some()
        {
            return None;
        }

        match (
            origin.host_str()?,
            origin.path().trim_end_matches('/'),
            slug,
        ) {
            (
                "api.deepseek.com",
                ""
                | "/v1"
                | "/v1/chat/completions"
                | "/v1/responses"
                | "/chat/completions"
                | "/responses"
                | "/anthropic"
                | "/anthropic/v1"
                | "/anthropic/v1/messages",
                "deepseek-flash"
                | "deepseek-v4-pro"
                | "deepseek-v4-flash"
                | "deepseek-v4-flash-vision-exp",
            ) => Some(Self {
                kind: PolicyKind::DeepSeek,
                levels: &[Default, Off, Low, High, Max],
                aliases: &[(Minimal, Low), (Medium, High), (XHigh, High)],
            }),
            (
                "api.kimi.ai" | "api.kimi.com",
                "/coding" | "/coding/v1",
                "k3.1-pro" | "k3.1-pro-highspeed",
            ) => Some(Self {
                kind: PolicyKind::KimiCode,
                levels: &[Default, Low, High, Max],
                aliases: &[(Medium, High), (XHigh, Max)],
            }),
            (
                "integrate.api.nvidia.com",
                "" | "/v1",
                "nvidia/nemotron-3.5-lightning-30b-a3b" | "nvidia/nemotron-3.5-bolt-e4b",
            ) => Some(Self {
                kind: PolicyKind::NvidiaToggle,
                levels: &[Default, Off, On],
                aliases: &[
                    (Minimal, On),
                    (Low, On),
                    (Medium, On),
                    (High, On),
                    (XHigh, On),
                    (Max, On),
                ],
            }),
            ("integrate.api.nvidia.com", "" | "/v1", "nvidia/nemotron-3.5-super-vl-120b-a12b") => {
                Some(Self {
                    kind: PolicyKind::NvidiaSuper,
                    levels: &[Default, Off, Low, High],
                    aliases: &[
                        (Minimal, Low),
                        (Medium, Low),
                        (On, High),
                        (XHigh, High),
                        (Max, High),
                    ],
                })
            }
            _ => None,
        }
    }
}

fn openai_levels(slug: &str) -> Option<&'static [Effort]> {
    match unversioned_slug(slug) {
        "gpt-6.2-astra" | "gpt-6.2-sol" | "gpt-6.1-sol" | "gpt-6-astra" => {
            Some(&OPENAI_REQUIRED_LEVELS)
        }
        "gpt-6.1-terra" | "gpt-6-sol" | "gpt-6-luna" => Some(&OPENAI_LEVELS),
        _ => None,
    }
}

pub fn unversioned_slug(slug: &str) -> &str {
    match slug
        .as_bytes()
        .split_at_checked(slug.len().saturating_sub(11))
    {
        Some((head, [b'-', y1, y2, y3, y4, b'-', m1, m2, b'-', d1, d2]))
            if [y1, y2, y3, y4, m1, m2, d1, d2]
                .iter()
                .all(|b| b.is_ascii_digit()) =>
        {
            std::str::from_utf8(head).unwrap_or(slug)
        }
        _ => slug,
    }
}

pub struct DiscoveryFacts {
    pub levels: &'static [Effort],
    pub context: Option<u64>,
    pub output: Option<u64>,
    pub protocol: Option<Protocol>,
}

pub fn discovery_facts(slug: &str, origin: &str) -> Option<DiscoveryFacts> {
    if let Some(policy) = Policy::for_route(slug, origin) {
        let nvidia = matches!(
            policy.kind,
            PolicyKind::NvidiaToggle | PolicyKind::NvidiaSuper
        );
        return Some(DiscoveryFacts {
            levels: policy.levels,
            context: None,
            output: (slug == "nvidia/nemotron-3.5-lightning-30b-a3b").then_some(32768),
            protocol: nvidia.then_some(Protocol::ChatCompletions),
        });
    }
    if Url::parse(origin)
        .is_ok_and(|url| url.scheme() == "https" && url.host_str() == Some("api.openai.com"))
    {
        return Some(DiscoveryFacts {
            levels: openai_levels(slug)?,
            context: Some(1_050_000),
            output: matches!(
                unversioned_slug(slug),
                "gpt-6.2-astra" | "gpt-6.2-sol" | "gpt-6.1-terra"
            )
            .then_some(128000),
            protocol: None,
        });
    }
    None
}

fn parsed_levels(value: &Value) -> Vec<Effort> {
    let normalize = |value: &str| Effort::parse(&value.trim().to_ascii_lowercase());
    let mut levels: Vec<_> = match value {
        Value::Array(values) => values
            .iter()
            .filter_map(Value::as_str)
            .filter_map(normalize)
            .collect(),
        Value::String(text) => text
            .split(|c: char| c == ',' || c.is_whitespace())
            .filter_map(normalize)
            .collect(),
        _ => Vec::new(),
    };
    if levels.is_empty() {
        return DEFAULT_LEVELS.to_vec();
    }
    levels.push(Default);
    EFFORTS
        .into_iter()
        .filter(|level| levels.contains(level))
        .collect()
}

#[derive(Debug)]
pub struct Model {
    pub id: String,
    pub provider_id: String,
    pub slug: String,
    pub chat: bool,
    pub wire_chat: bool,
    pub protocol: Option<Protocol>,
    pub levels: Vec<Effort>,
    pub max_output_tokens: u64,
    pub fast: bool,
    saved_effort: Effort,
    policy: Option<Policy>,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Protocol {
    #[default]
    ChatCompletions,
    Responses,
    Anthropic,
}

impl Protocol {
    fn parse(value: &Value) -> Option<Self> {
        match value.as_str()? {
            "chat-completions" => Some(Self::ChatCompletions),
            "responses" => Some(Self::Responses),
            "anthropic" => Some(Self::Anthropic),
            _ => None,
        }
    }
}

#[derive(Debug)]
pub struct Provider {
    pub id: String,
    pub name: Option<String>,
    pub origin: String,
    pub protocol: Protocol,
}

impl Provider {
    pub(crate) fn from_saved(row: &Value) -> Option<Self> {
        Some(Self {
            id: row["id"].as_str()?.into(),
            name: row["name"].as_str().map(str::to_owned),
            origin: row["origin"].as_str().unwrap_or_default().into(),
            protocol: Protocol::parse(&row["apiType"]).unwrap_or_default(),
        })
    }
}

#[derive(Debug)]
pub struct Binding {
    pub model: String,
    pub provider: String,
}

impl Binding {
    fn from_saved(row: &Value) -> Option<Self> {
        Some(Self {
            model: row["modelId"].as_str()?.into(),
            provider: row["providerId"].as_str().unwrap_or_default().into(),
        })
    }
}

impl Model {
    fn from_saved(row: &Value, providers: &[Provider]) -> Option<Self> {
        let id = row["id"].as_str()?;
        let provider_id = row["providerId"].as_str()?;
        let slug = row["slug"].as_str()?;
        let origin = providers
            .iter()
            .find(|provider| provider.id == provider_id)
            .map(|provider| provider.origin.as_str())
            .unwrap_or_default();
        let policy = Policy::for_route(slug, origin);
        let levels = policy
            .map(|policy| policy.levels)
            .or_else(|| {
                (provider_id == "openai")
                    .then(|| openai_levels(slug))
                    .flatten()
            })
            .map_or_else(|| parsed_levels(&row["reasoningLevels"]), <[_]>::to_vec);
        let mut saved_effort = row["activeReasoning"]
            .as_str()
            .and_then(Effort::parse)
            .unwrap_or(Default);
        if saved_effort == Off
            && !row["reasoningLevels"]
                .as_array()
                .is_some_and(|levels| levels.iter().any(|level| level == "default"))
        {
            saved_effort = Default;
        }
        let protocol = match &row["apiType"] {
            Value::Null => Protocol::parse(&row["api_type"]),
            configured => Protocol::parse(configured),
        };
        Some(Self {
            id: id.into(),
            provider_id: provider_id.into(),
            slug: slug.into(),
            chat: !matches!(
                row["modelType"].as_str(),
                Some("embedding" | "decision" | "image" | "other")
            ),
            wire_chat: row.get("modelType").is_none_or(|kind| kind == "chat"),
            protocol,
            levels,
            max_output_tokens: configured_tokens(&row["maxOutputTokens"]),
            fast: row["parameters"]
                .as_array()
                .and_then(|rows| rows.iter().find(|row| row["id"] == "fast"))
                .is_some_and(|row| {
                    row["value"] == true
                        || row["value"]
                            .as_str()
                            .is_some_and(|s| s.eq_ignore_ascii_case("true"))
                }),
            saved_effort,
            policy,
        })
    }

    pub fn display_effort(&self, effort: Effort) -> Effort {
        self.policy
            .and_then(|policy| policy.aliases.iter().find(|(old, _)| *old == effort))
            .map_or(effort, |(_, current)| *current)
    }

    pub fn effective_override(&self, choice: Effort) -> Option<Effort> {
        let choice = self.display_effort(choice);
        self.levels.contains(&choice).then_some(choice)
    }

    pub fn default_effort(&self) -> Effort {
        self.display_effort(self.saved_effort)
    }

    pub fn policy(&self) -> Option<PolicyKind> {
        self.policy.map(|policy| policy.kind)
    }
}

fn configured_tokens(value: &Value) -> u64 {
    value
        .as_f64()
        .or_else(|| value.as_str()?.trim().parse().ok())
        .filter(|tokens| tokens.is_finite() && *tokens >= 1.0)
        .map_or(65_536, |tokens| tokens.min(131_072.0) as u64)
}

#[derive(Debug, Default)]
pub struct Catalog {
    pub models: Vec<Model>,
    pub providers: Vec<Provider>,
    pub wildcard: Option<Binding>,
}

impl Catalog {
    pub fn from_plan(plan: &Value) -> Self {
        let data = &plan["catalog"];
        if plan["kind"] != "custom" || !data["bindings"].is_array() {
            return Self::default();
        }
        let (Some(providers), Some(models)) =
            (data["providers"].as_array(), data["models"].as_array())
        else {
            return Self::default();
        };
        let providers: Vec<_> = providers.iter().filter_map(Provider::from_saved).collect();
        Self {
            models: models
                .iter()
                .filter_map(|row| Model::from_saved(row, &providers))
                .collect(),
            providers,
            wildcard: Binding::from_saved(&plan["agents"]["*"]),
        }
    }

    pub fn chat_model(&self, id: &str) -> Option<&Model> {
        self.models
            .iter()
            .find(|model| model.chat && model.id == id)
    }
}
