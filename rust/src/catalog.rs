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
    levels: &'static [Effort],
    aliases: &'static [(Effort, Effort)],
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
                levels: &[Default, Off, Low, High, Max],
                aliases: &[(Minimal, Low), (Medium, High), (XHigh, High)],
            }),
            (
                "api.kimi.ai" | "api.kimi.com",
                "/coding" | "/coding/v1",
                "k3.1-pro" | "k3.1-pro-highspeed",
            ) => Some(Self {
                levels: &[Default, Low, High, Max],
                aliases: &[(Medium, High), (XHigh, Max)],
            }),
            (
                "integrate.api.nvidia.com",
                "" | "/v1",
                "nvidia/nemotron-3.5-lightning-30b-a3b" | "nvidia/nemotron-3.5-bolt-e4b",
            ) => Some(Self {
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
    let name = match slug
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
    };
    match name {
        "gpt-6.2-astra" | "gpt-6.2-sol" | "gpt-6.1-sol" | "gpt-6-astra" => {
            Some(&OPENAI_REQUIRED_LEVELS)
        }
        "gpt-6.1-terra" | "gpt-6-sol" | "gpt-6-luna" => Some(&OPENAI_LEVELS),
        _ => None,
    }
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
    pub chat: bool,
    pub levels: Vec<Effort>,
    policy: Option<Policy>,
}

impl Model {
    fn from_saved(row: &Value, providers: &[Value]) -> Option<Self> {
        let id = row["id"].as_str()?;
        let provider_id = row["providerId"].as_str()?;
        let slug = row["slug"].as_str()?;
        let origin = providers
            .iter()
            .find(|provider| provider["id"].as_str() == Some(provider_id))
            .and_then(|provider| provider["origin"].as_str())
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
        Some(Self {
            id: id.into(),
            chat: !matches!(
                row["modelType"].as_str(),
                Some("embedding" | "decision" | "image" | "other")
            ),
            levels,
            policy,
        })
    }

    pub fn display_effort(&self, effort: Effort) -> Effort {
        self.policy
            .and_then(|policy| policy.aliases.iter().find(|(old, _)| *old == effort))
            .map_or(effort, |(_, current)| *current)
    }
}

#[derive(Debug, Default)]
pub struct Catalog {
    pub models: Vec<Model>,
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
        Self {
            models: models
                .iter()
                .filter_map(|row| Model::from_saved(row, providers))
                .collect(),
        }
    }

    pub fn chat_model(&self, id: &str) -> Option<&Model> {
        self.models
            .iter()
            .find(|model| model.chat && model.id == id)
    }
}
