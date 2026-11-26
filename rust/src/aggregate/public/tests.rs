use super::*;
use crate::aggregate::provider_model;

const GO: &str = "https://opencode.ai/inference/go";

#[test]
fn fresh_sources_merge_fields_and_preserve_route_effort_and_protocol() {
    let router = router_feed(&json!({"data":[
        {"id":"example/contributor","name":"Router","reasoning":{"supported_efforts":["low","high"]},"pricing":{"prompt":"0.002"}},
        {"id":"contributor","name":"Router alias","context_length":400000,"pricing":{"prompt":"0.004"}}
    ]}));
    let dev = dev_feed(&json!({"opencode-go":{
        "api":"https://opencode.ai/inference/go/openai/v1","npm":"@ai-sdk/openai-compatible",
        "models":{
            "contributor":{
                "id":"contributor","name":"GO Contributor","limit":{"context":810000,"output":393216},
                "reasoning":true,"reasoning_options":[{"type":"effort","values":["minimal","low","high","xhigh"]}],
                "provider":{"npm":"@ai-sdk/openai"},
                "modalities":{"input":["text","image"],"output":["text"]},"cost":{"output":0.012}
            },
            "embedding":{"id":"embedding","limit":{"context":25000,"output":0}}
        }
    }}));
    let inventory = combine(
        &Inventory::new(None),
        [Ok(router), Ok(dev)],
        "2026-11-26T09:00:00Z",
    )
    .unwrap();
    let raw = inventory.data.unwrap();
    assert_eq!(raw["sources"][0]["modelCount"], 2);
    assert_eq!(raw["sources"][1]["modelCount"], 2);
    assert_eq!(raw["totalModels"], 3);
    assert_eq!(raw["models"]["contributor"]["name"], "GO Contributor");
    assert_eq!(raw["models"]["contributor"]["contextLength"], 810000);
    assert_eq!(
        raw["models"]["contributor"]["pricing"],
        json!({"input":0.004,"output":0.012,"currency":"USD"})
    );
    let route = provider_model(&raw, "contributor", GO).unwrap();
    assert_eq!(route["apiType"], "responses");
    assert_eq!(route["maxOutputTokens"], 393216);
    assert_eq!(
        route["reasoningLevels"],
        json!(["default", "minimal", "low", "high", "xhigh"])
    );
    assert!(
        provider_model(
            &raw,
            "contributor",
            "https://opencode.ai/inference/openai/v1"
        )
        .is_none()
    );
    assert_eq!(
        provider_model(&raw, "embedding", GO).unwrap()["maxOutputTokens"],
        0
    );
}

#[test]
fn partial_outages_keep_previous_source_routes_and_both_failures_keep_the_cache() {
    let dev = dev_feed(
        &json!({"opencode-go":{"api":GO,"models":{"kept":{"id":"kept","limit":{"context":84200}}}}}),
    );
    let baseline = combine(
        &Inventory::new(None),
        [Err("offline".into()), Ok(dev)],
        "2026-11-26T09:00:00Z",
    )
    .unwrap();
    let router = router_feed(&json!({"data":[{"id":"external/visible","context_length":128000}]}));
    let next = combine(
        &baseline,
        [Ok(router), Err("offline".into())],
        "2026-11-26T10:00:00Z",
    )
    .unwrap();
    let data = next.data.as_ref().unwrap();
    assert_eq!(data["sources"][0]["lastFetched"], "2026-11-26T10:00:00Z");
    assert_eq!(data["sources"][1]["lastFetched"], "2026-11-26T09:00:00Z");
    assert!(provider_model(data, "kept", GO).is_some());
    assert!(combine(&next, [Err("1".into()), Err("2".into())], "now").is_none());
}
