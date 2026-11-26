use serde_json::{Value, json, to_value};

use super::*;

fn fixture() -> Catalog {
    Catalog::from_plan(&json!({
        "kind":"custom",
        "agents":{"*":{"providerId":"opencode-2","modelId":"demo"}},
        "catalog":{
            "providers":[
                {"id":"opencode","origin":"https://opencode.ai/inference/go","apiType":"chat-completions"},
                {"id":"opencode-2","origin":"https://opencode.ai/zen/go/v1","apiType":"chat-completions"},
                {"id":"deepseek","origin":"https://api.deepseek.com/anthropic/v1","apiType":"anthropic"},
                {"id":"openai","origin":"https://api.openai.com/v1","apiType":"chat-completions"},
                {"id":"zai","origin":"https://api.z.ai/api/coding/paas/v4","apiType":"chat-completions"},
                {"id":"invalid","origin":"https://key:test@example.invalid/v1","apiType":"chat-completions"}
            ],
            "models":[
                {"id":"opencode:demo","providerId":"opencode","slug":"demo","activeReasoning":"none","reasoningLevels":["none","high"]},
                {"id":"opencode-2:demo","providerId":"opencode-2","slug":"demo","apiType":"anthropic","reasoningLevels":["default","minimal","high"]},
                {"id":"opencode:search","providerId":"opencode","slug":"search","modelType":"embedding"},
                {"id":"deepseek:flash","providerId":"deepseek","slug":"deepseek-v4-flash"},
                {"id":"deepseek:flash-responses","providerId":"deepseek","slug":"deepseek-v4-flash","apiType":"responses"},
                {"id":"openai:sol","providerId":"openai","slug":"gpt-6.1-sol"},
                {"id":"zai:stable","providerId":"zai","slug":"glm"},
                {"id":"invalid:demo","providerId":"invalid","slug":"invalid"}
            ],
            "bindings":[]
        }
    }))
}

fn settings() -> Settings {
    Settings::from_saved(
        json!({
            "assignments": {"stale":"opencode:search","bot-example":"opencode-2:demo"},
            "reasoning": {"bot-example":{"opencode:demo":"high","opencode-2:demo":"minimal"},
                          "deepseek":{"deepseek:flash":"xhigh"}}
        })
        .as_object()
        .unwrap()
        .clone(),
    )
}

fn plan(model: &str, bot: Option<&str>, kind: CredentialKind) -> Value {
    to_value(resolve(&fixture(), &settings(), model, bot, kind).unwrap()).unwrap()
}

#[test]
fn exact_ids_keep_go_accounts_separate_and_prefer_the_active_family_for_a_slug() {
    let first = plan("opencode:demo", None, CredentialKind::ApiKey);
    let second = plan("opencode-2:demo", None, CredentialKind::ApiKey);
    assert_eq!(
        first["upstream"],
        "https://opencode.ai/inference/go/openai/v1/chat/completions"
    );
    assert_eq!(first["reasoningEffort"], "default");
    assert_eq!(
        second["upstream"],
        "https://opencode.ai/inference/go/anthropic/v1/messages"
    );
    assert_eq!(second["providerId"], "opencode-2");
    assert_eq!(plan("demo", None, CredentialKind::ApiKey), second);
}

#[test]
fn bot_pin_precedes_slug_and_reasoning_remains_scoped_to_the_effective_model() {
    let selected = plan("opencode:demo", Some("Bot-Example"), CredentialKind::ApiKey);
    assert_eq!(selected["modelId"], "opencode-2:demo");
    assert_eq!(selected["reasoningEffort"], "minimal");
    let stale = plan("opencode:search", Some("stale"), CredentialKind::ApiKey);
    assert_eq!(stale["modelId"], "opencode-2:demo");
    assert_eq!(stale["staleAssignment"], true);
    let deepseek = plan("deepseek:flash", Some("deepseek"), CredentialKind::ApiKey);
    assert_eq!(deepseek["reasoningEffort"], "high");
    assert_eq!(
        deepseek["upstream"],
        "https://api.deepseek.com/anthropic/v1/messages"
    );
}

#[test]
fn protocol_specific_endpoints_cover_responses_messages_and_chat_completions() {
    for (selector, kind, expected, protocol) in [
        (
            "deepseek:flash-responses",
            CredentialKind::ApiKey,
            "https://api.deepseek.com/responses",
            "responses",
        ),
        (
            "openai:sol",
            CredentialKind::ApiKey,
            "https://api.openai.com/v1/responses",
            "responses",
        ),
        (
            "openai:sol",
            CredentialKind::OpenAiOauth,
            "https://chatgpt.com/backend-api/codex/responses",
            "responses",
        ),
        (
            "openai:sol",
            CredentialKind::ChatGptPlan,
            "https://api.openai.com/v1/responses",
            "responses",
        ),
        (
            "zai:stable",
            CredentialKind::ApiKey,
            "https://api.z.ai/api/coding/paas/v4/chat/completions",
            "chat-completions",
        ),
    ] {
        let selected = plan(selector, None, kind);
        assert_eq!(selected["upstream"], expected);
        assert_eq!(selected["protocol"], protocol);
    }
}

#[test]
fn nonchat_models_and_credential_bearing_origins_are_refused() {
    let catalog = fixture();
    let settings = settings();
    assert!(matches!(
        resolve(
            &catalog,
            &settings,
            "opencode:search",
            None,
            CredentialKind::ApiKey
        ),
        Err(RouteError::NonChatModel)
    ));
    assert!(matches!(
        resolve(
            &catalog,
            &settings,
            "invalid:demo",
            None,
            CredentialKind::ApiKey
        ),
        Err(RouteError::IncompleteProvider)
    ));
}
