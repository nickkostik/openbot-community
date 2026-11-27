use std::fs;

use axum::{
    body::{Body, to_bytes},
    http::{Method, Request},
};
use tower::ServiceExt;

use super::*;

const HOST: &str = "127.0.0.1:9287";

fn fixture() -> tempfile::TempDir {
    let dir = tempfile::tempdir().unwrap();
    let plan = json!({
        "kind": "custom",
        "catalog": {
            "providers": [
                { "id": "opencode", "origin": "https://opencode.ai/inference/go" },
                { "id": "opencode-2", "origin": "https://opencode.ai/inference/go" },
                { "id": "deepseek", "origin": "https://api.deepseek.com/anthropic/v1" },
                { "id": "openai", "origin": "https://api.openai.com/v1" },
                { "id": "nvidia", "origin": "https://integrate.api.nvidia.com/v1" },
            ],
            "models": [
                {"id": "opencode:example", "providerId": "opencode", "slug": "example"},
                {"id": "opencode-2:example", "providerId": "opencode-2", "slug": "example",
                    "reasoningLevels": "high, minimal LOW"},
                {"id": "opencode:search", "providerId": "opencode", "slug": "search",
                    "modelType": "embedding"},
                {"id": "deepseek:flash", "providerId": "deepseek", "slug": "deepseek-v4-flash"},
                {"id": "nvidia:bolt", "providerId": "nvidia", "slug": "nvidia/nemotron-3.5-bolt-e4b"},
                {"id": "openai:sol", "providerId": "openai", "slug": "gpt-6.2-sol-2026-11-26"},
                {"id": "skip"},
            ],
            "bindings": []
        }
    });
    fs::write(dir.path().join("openbot-plan.json"), plan.to_string()).unwrap();
    fs::write(dir.path().join("openbot-mode"), "official\n").unwrap();
    fs::write(
        dir.path().join("openbot-bot-models.json"),
        json!({
            "assignments": {"bot-a": "opencode:example", "__proto__": "opencode-2:example"},
            "reasoning": {"bot-a": {"opencode:example": "high", "deepseek:flash": "xhigh",
                                  "nvidia:bolt": "max", "invalid": "made-up"}},
            "futureField": {"preserve": true}
        })
        .to_string(),
    )
    .unwrap();
    dir
}

async fn call(router: &Router, method: Method, value: Value) -> (StatusCode, Value) {
    let response = router
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri("/api/bot-models")
                .header(header::HOST, HOST)
                .header(header::ORIGIN, format!("http://{HOST}"))
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(value.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn shadow_reads_both_go_accounts_and_provider_specific_efforts() {
    let dir = fixture();
    let app = router(
        State::new(dir.path().into(), false).unwrap(),
        HOST.into(),
        [],
    );
    let (status, actual) = call(&app, Method::GET, Value::Null).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        actual,
        json!({
            "assignments": {"bot-a": "opencode:example"},
            "reasoning": {"bot-a": {
                "opencode:example": "high", "deepseek:flash": "high", "nvidia:bolt": "on"
            }},
            "available": ["opencode:example", "opencode-2:example", "deepseek:flash", "nvidia:bolt", "openai:sol"],
        })
    );
    assert_eq!(
        call(
            &app,
            Method::PUT,
            json!({"botId": "bot-a", "modelId": "opencode-2:example"})
        )
        .await
        .0,
        StatusCode::METHOD_NOT_ALLOWED,
    );
    assert!(!dir.path().join(".openbot-rust-bot-settings.lock").exists());
}

#[tokio::test]
async fn batch_switch_validates_all_models_and_keeps_effort_per_route() {
    let dir = fixture();
    let file = dir.path().join("openbot-bot-models.json");
    let app = router(
        State::new(dir.path().into(), true).unwrap(),
        HOST.into(),
        [],
    );
    let before = fs::read(&file).unwrap();
    for invalid in [
        json!({"botId":"bot-a","modelId":"opencode:search"}),
        json!({"botId":"bot-a","modelId":"opencode-2:example","reasoningEffort":"max"}),
        json!({"botIds":["bot-a","bot-b"],"modelIds":["opencode-2:example","openai:sol"],"reasoningEffort":"none"}),
        json!({"botId":"bot-a","reasoningModelId":"openai:sol","reasoningEffort":"none"}),
    ] {
        assert_eq!(
            call(&app, Method::PUT, invalid).await.0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(fs::read(&file).unwrap(), before);
    }

    let (status, result) = call(&app, Method::PUT, json!({
        "botIds": ["bot-a", "bot-b"], "modelId": "opencode-2:example", "reasoningEffort": "minimal",
    })).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(result["assignments"]["bot-a"], "opencode-2:example");
    assert_eq!(result["reasoning"]["bot-a"]["opencode:example"], "high");
    assert_eq!(
        result["reasoning"]["bot-a"]["opencode-2:example"],
        "minimal"
    );
    assert_eq!(
        result["reasoning"]["bot-b"]["opencode-2:example"],
        "minimal"
    );
    let on_disk = read_json(&file, BOT_MODELS_MAX_BYTES).unwrap().unwrap();
    assert_eq!(on_disk["futureField"]["preserve"], true);
    assert_eq!(on_disk["reasoning"]["bot-a"]["nvidia:bolt"], "max");

    let (_, current) = call(&app, Method::PUT, json!({
        "botId": "bot-a", "modelId": null, "reasoningModelId":"opencode-2:example", "reasoningEffort": null
    })).await;
    assert!(current["assignments"]["bot-a"].is_null());
    assert!(current["reasoning"]["bot-a"]["opencode-2:example"].is_null());
    assert_eq!(current["reasoning"]["bot-a"]["opencode:example"], "high");
    assert_eq!(
        fs::read_to_string(dir.path().join("openbot-mode")).unwrap(),
        "official\n"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(file).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}

#[tokio::test]
async fn simultaneous_writes_survive_restart() {
    let dir = fixture();
    let root = dir.path().to_owned();
    let app = router(State::new(root.clone(), true).unwrap(), HOST.into(), []);
    assert!(State::new(root.clone(), true).is_err());
    let (first, second) = tokio::join!(
        call(
            &app,
            Method::PUT,
            json!({"botId":"bot-b", "modelId":"opencode:example"})
        ),
        call(
            &app,
            Method::PUT,
            json!({"botId":"bot-c", "modelId":"opencode-2:example"})
        )
    );
    assert_eq!(first.0, StatusCode::OK);
    assert_eq!(second.0, StatusCode::OK);
    drop(app);

    let reopened = router(State::new(root, true).unwrap(), HOST.into(), []);
    let (status, view) = call(&reopened, Method::GET, Value::Null).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(view["assignments"]["bot-b"], "opencode:example");
    assert_eq!(view["assignments"]["bot-c"], "opencode-2:example");
}

#[tokio::test]
async fn corrupt_state_can_be_read_but_a_save_cannot_replace_it() {
    let dir = fixture();
    let file = dir.path().join("openbot-bot-models.json");
    fs::write(&file, b"{ invalid").unwrap();
    let app = router(
        State::new(dir.path().into(), true).unwrap(),
        HOST.into(),
        [],
    );
    let (status, view) = call(&app, Method::GET, Value::Null).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(view["assignments"], json!({}));
    let (status, _) = call(
        &app,
        Method::PUT,
        json!({"botId":"bot-a","modelId":"opencode-2:example"}),
    )
    .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(fs::read(&file).unwrap(), b"{ invalid");

    fs::remove_file(&file).unwrap();
    assert_eq!(
        call(
            &app,
            Method::PUT,
            json!({"botId":"bot-a","modelId":"opencode-2:example"})
        )
        .await
        .0,
        StatusCode::OK
    );
}

#[tokio::test]
async fn rejects_cross_origin_and_non_loopback_host() {
    let dir = fixture();
    let app = router(
        State::new(dir.path().into(), false).unwrap(),
        HOST.into(),
        [],
    );
    for (host, origin) in [
        ("untrusted.test", None),
        (HOST, Some("https://untrusted.test")),
    ] {
        let mut request = Request::builder()
            .uri("/api/bot-models")
            .header(header::HOST, host);
        if let Some(origin) = origin {
            request = request.header(header::ORIGIN, origin);
        }
        let response = app
            .clone()
            .oneshot(request.body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    }
}
