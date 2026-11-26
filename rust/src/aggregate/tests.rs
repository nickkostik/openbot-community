use std::fs;

use axum::{
    body::{Body, to_bytes},
    http::{Request, header},
};
use tower::ServiceExt;

use super::*;

const HOST: &str = "127.0.0.1:39080";
const GO: &str = "https://opencode.ai/inference/go";

fn saved() -> Value {
    let go_key = "opencode.ai/zen/go\0fixture-model";
    let kimi_key = "api.kimi.ai/coding\0fixture-model";
    json!({
        "lastFetched":"2026-11-26T07:44:00Z",
        "models":{
          "vendor/fixture-model":{"id":"vendor/fixture-model","name":"Rich metadata","contextLength":800000,
              "maxOutputTokens":943718,"modalities":["text"],"reasoning":true,"reasoningLevels":["high","low"],
              "pricing":{"input":2.0,"currency":"usd"}},
          "fixture-model":{"id":"fixture-model","maxOutputTokens":"393216","reasoning":true,
              "reasoningLevels":["default","max"],"pricing":{"output":5.0}},
          "bad":{"id":"different-id"}
        },
        "routes":{
            (go_key):{"id":"fixture-model","providerKey":"opencode-go","apiType":"responses","reasoning":true,
                "reasoningLevels":["xhigh","minimal","default"],"outputModalities":["Text","text","unknown"]},
            (kimi_key):{"id":"fixture-model","providerKey":"kimi-for-coding","maxOutputTokens":0}
        },
        "sources":[{"name":"models.dev","modelCount":2,"lastFetched":"2026-11-26T07:44:00Z"}]
    })
}

#[test]
fn saved_cache_recovers_route_metadata_without_inheriting_other_families() {
    let inventory = Inventory::new(Some(saved()));
    let go = inventory.snapshot(Some("fixture-model-2026-11-26"), Some(GO));
    assert_eq!(go["status"], "ready");
    assert_eq!(go["totalModels"], 2);
    assert_eq!(go["lookup"]["model"]["outputModalities"], json!(["text"]));
    assert_eq!(
        go["lookup"]["model"]["reasoningLevels"],
        json!(["default", "minimal", "xhigh"])
    );
    assert_eq!(go["lookup"]["model"]["apiType"], "responses");
    let kimi = inventory.snapshot(
        Some("fixture-model"),
        Some("https://api.kimi.com/coding/v1"),
    );
    assert_eq!(kimi["lookup"]["model"]["maxOutputTokens"], 0);
    assert_eq!(kimi["lookup"]["model"]["reasoningLevels"], json!([]));
    let proxy = inventory.snapshot(
        Some("fixture-model"),
        Some("https://proxy.example.test/inference/go"),
    );
    assert_eq!(proxy["lookup"]["found"], false);
}

#[test]
fn bare_lookup_merges_populated_fields_and_recovers_documented_openai_models() {
    let mut cache = saved();
    cache["models"]["vendor/prefixed-only"] =
        json!({"id":"vendor/prefixed-only","contextLength":128000});
    let inventory = Inventory::new(Some(cache));
    let stats = inventory.snapshot(Some("fixture-model"), None);
    let model = &stats["lookup"]["model"];
    assert_eq!(model["id"], "fixture-model");
    assert_eq!(model["name"], "Rich metadata");
    assert_eq!(model["maxOutputTokens"], 393216);
    assert_eq!(
        model["reasoningLevels"],
        json!(["default", "low", "high", "max"])
    );
    assert_eq!(
        model["pricing"],
        json!({"input":2.0,"output":5.0,"currency":"USD"})
    );
    let alias = inventory.snapshot(Some("prefixed-only"), None);
    assert_eq!(alias["lookup"]["model"]["id"], "vendor/prefixed-only");
    assert_eq!(alias["lookup"]["model"]["contextLength"], 128000);
    let empty = Inventory::new(None);
    let known = empty.snapshot(
        Some("gpt-5.2-codex-2026-11-26"),
        Some("https://api.openai.com/v1"),
    );
    let instruct = empty.snapshot(
        Some("gpt-3.5-turbo-instruct"),
        Some("https://api.openai.com/v1"),
    );
    assert_eq!(instruct["lookup"]["model"]["contextLength"], 4096);
    assert_eq!(known["status"], "failed");
    assert_eq!(known["lookup"]["model"]["contextLength"], 400000);
    assert_eq!(
        known["lookup"]["model"]["reasoningLevels"],
        json!(["default", "low", "medium", "high", "xhigh"])
    );
    assert_eq!(
        empty.snapshot(Some("gpt-5.2-codex"), Some(GO))["lookup"]["found"],
        false
    );
}

#[tokio::test]
async fn model_catalog_endpoint_scopes_lookup_to_the_saved_provider() {
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("openbot-model-catalog.json"),
        saved().to_string(),
    )
    .unwrap();
    fs::write(
        root.path().join("openbot-plan.json"),
        json!({"kind":"custom","catalog":{
            "providers":[{"id":"opencode-2","origin":GO}],"models":[],"bindings":[]
        }})
        .to_string(),
    )
    .unwrap();
    let state = State::new(root.path().to_owned(), false).unwrap();
    let app = crate::server::router(state.clone(), HOST.into(), [routes(state)]);
    for (id, status) in [
        ("opencode-2", StatusCode::OK),
        ("unknown", StatusCode::NOT_FOUND),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::get(format!(
                    "/api/model-catalog?modelId=fixture-model&providerId={id}"
                ))
                .header(header::HOST, HOST)
                .body(Body::empty())
                .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), status);
        let body: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 4096).await.unwrap()).unwrap();
        if status == StatusCode::OK {
            assert_eq!(body["lookup"]["model"]["providerKey"], "opencode-go");
            assert_eq!(body["sources"][1]["modelCount"], 2);
        } else {
            assert_eq!(body["error"]["kind"], "provider-not-found");
        }
    }
}
