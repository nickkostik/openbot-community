use std::fs;

use super::*;

const GO: &str = "https://opencode.ai/inference/go";

fn setup() -> (Value, Inventory) {
    let plan = json!({
        "kind":"custom","agents":{"*":{"modelId":"kept","providerId":"opencode"}},
        "catalog":{
            "providers":[{"id":"opencode","origin":GO,"name":"Go 1"},{"id":"opencode2","origin":GO,"name":"Go 2"}],
            "models":[{"id":"custom-keep","slug":"kept","providerId":"opencode","contextTokens":258000,"maxOutputTokens":32000,
                "activeReasoning":"high","reasoningLevels":["default","high","max"],"modalities":["text"],
                "parameters":[{"id":"temperature","value":"0.5"}]},
                {"id":"other","slug":"embedding","providerId":"opencode","contextTokens":128000,"maxOutputTokens":65536}],
            "bindings":[{"conversation":{"kind":"wildcard"},"modelId":"custom-keep"}]},
        "extra":{"keep":true}
    });
    let cache = Inventory::new(Some(json!({"models":{},"routes":{
        "opencode.ai/zen/go\0kept":{
            "id":"kept","contextLength":1024000,"maxOutputTokens":128000,
            "reasoning":true,"reasoningLevels":["default","high"],"modalities":["text","image"],
            "providerKey":"opencode-go","sourceUrl":"https://models.dev/api.json","apiType":"responses"
        }
    }})));
    (plan, cache)
}

fn listing(row: &Value, models: Result<Vec<Value>, &'static str>) -> Listing {
    Listing {
        target: Target {
            provider: Provider::from_saved(row).unwrap(),
            saved: row.clone(),
        },
        models,
    }
}

#[test]
fn merge_preserves_referenced_models_and_keeps_account_specific_capabilities() {
    let (mut plan, inventory) = setup();
    let before = plan.clone();
    let providers = plan["catalog"]["providers"].as_array().unwrap();
    let lists = [
        listing(
            &providers[0],
            Ok(vec![
                json!({"id":"kept","providerMetadata":"opencode-go","modelType":"embedding","reasoningLevels":[]}),
                json!({"id":"embedding","providerMetadata":"opencode-go","modelType":"embedding"}),
                json!({"id":"new","providerMetadata":"opencode-go","modelType":"chat","apiType":"responses",
                "reasoningLevels":["default","minimal"],"contextLength":300000,"maxOutputTokens":200000}),
            ]),
        ),
        listing(
            &providers[1],
            Ok(vec![
                json!({"id":"new","providerMetadata":"opencode-go","modelType":"chat","apiType":"anthropic",
                "reasoningLevels":["default","none","low"],"contextLength":350000}),
            ]),
        ),
    ];
    let choices = json!({"reasoning":{"fixture-bot":{"custom-keep":"max"}}});
    let changes = merge(&mut plan, &inventory, &lists, &choices, false).unwrap();
    assert_eq!(changes.added, 2);
    assert_eq!(changes.updated, 2);
    assert_eq!(changes.reasoning_held, 1);
    for key in ["agents", "extra"] {
        assert_eq!(plan[key], before[key]);
    }
    assert_eq!(plan["catalog"]["bindings"], before["catalog"]["bindings"]);
    assert_eq!(plan["catalog"]["providers"], before["catalog"]["providers"]);
    let saved = &plan["catalog"]["models"];
    assert_eq!(saved[0]["id"], "custom-keep");
    assert!(saved[0]["modelType"].is_null());
    assert_eq!(saved[0]["activeReasoning"], "high");
    assert_eq!(
        saved[0]["reasoningLevels"],
        before["catalog"]["models"][0]["reasoningLevels"]
    );
    assert_eq!(saved[0]["contextTokens"], 258000);
    assert_eq!(saved[0]["catalogLimits"]["contextTokens"], 1024000);
    assert_eq!(saved[1]["modelType"], "embedding");
    assert_eq!(saved[2]["maxOutputTokens"], 65536);
    assert_eq!(saved[2]["apiType"], "responses");
    assert_eq!(saved[3]["id"], "opencode2:new");
    assert_eq!(saved[3]["apiType"], "anthropic");
    assert_eq!(
        saved[3]["reasoningLevels"],
        json!(["default", "none", "low"])
    );
    let unchanged = plan.clone();
    let repeated = merge(&mut plan, &inventory, &lists, &choices, false).unwrap();
    assert_eq!(repeated.added + repeated.updated, 0);
    assert_eq!(plan, unchanged);
}

#[test]
fn provider_changes_and_retired_routes_do_not_apply_stale_results() {
    let (mut plan, inventory) = setup();
    let go = listing(
        &plan["catalog"]["providers"][0],
        Ok(vec![json!({"id":"stale"})]),
    );
    plan["catalog"]["providers"][0]["name"] = json!("Changed while running");
    let zen = listing(
        &json!({"id":"opencode2","origin":"https://opencode.ai/inference/v1"}),
        Ok(vec![]),
    );
    let retired = listing(
        &json!({"id":"other","name":"Meta Key bridge","origin":"https://example.test/v1"}),
        Ok(vec![]),
    );
    assert!(!allowed(&zen.target));
    assert!(!allowed(&retired.target));
    let before = plan.clone();
    let changes = merge(&mut plan, &inventory, &[go], &Value::Null, true).unwrap();
    assert_eq!(changes.added + changes.updated, 0);
    assert_eq!(plan, before);
}

#[test]
fn plan_update_detects_an_external_write_and_respects_the_newer_bytes() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("openbot-plan.json");
    let (plan, _) = setup();
    fs::write(&path, plan.to_string()).unwrap();
    let store = CredentialStore::new(root.path(), true).unwrap();
    let mut newer = plan.clone();
    newer["extra"] = json!({"savedDuringCommit":true});
    let result = store.refresh_plan(|value| {
        value["extra"] = json!({"staleRefresh":true});
        fs::write(&path, newer.to_string()).unwrap();
        Ok((true, ()))
    });
    assert_eq!(result.unwrap_err().status, axum::http::StatusCode::CONFLICT);
    assert_eq!(fs::read_to_string(path).unwrap(), newer.to_string());
}
