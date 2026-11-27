use std::{
    fs,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

use axum::{
    body::{Body, to_bytes},
    http::Request,
    routing::get,
};
use tower::ServiceExt;

use super::*;
use crate::server;

#[test]
fn go_uses_the_direct_list_and_one_coding_client_session_per_provider() {
    for origin in [
        "https://opencode.ai/inference/go",
        "https://opencode.ai/inference/go/anthropic/v1",
        "https://opencode.ai/zen/go",
    ] {
        let parsed = parse_origin(origin).unwrap();
        assert!(direct_go(&parsed));
        assert_eq!(
            endpoint(parsed, CredentialKind::ApiKey, true)
                .unwrap()
                .as_str(),
            GO_LIST
        );
    }
    for origin in [
        "https://opencode.ai/zen",
        "https://go-proxy.example.test/inference/go",
    ] {
        assert!(!direct_go(&parse_origin(origin).unwrap()));
    }
    let authorized = Authorized {
        kind: CredentialKind::ApiKey,
        token: "fixture-go".into(),
        account: None,
    };
    let first = outbound(&authorized, true, "opencode").unwrap();
    let second = outbound(&authorized, true, "opencode-2").unwrap();
    assert_eq!(first[header::USER_AGENT], "OpenBot/catalog");
    assert_eq!(first[header::AUTHORIZATION], "Bearer fixture-go");
    assert!(first[header::AUTHORIZATION].is_sensitive());
    assert_eq!(first["x-opencode-session"].len(), 32);
    assert_ne!(first["x-opencode-session"], second["x-opencode-session"]);
    assert_eq!(first, outbound(&authorized, true, "opencode").unwrap());
}

#[test]
fn go_model_metadata_preserves_supported_efforts_and_nonchat_categories() {
    let raw = json!({"data":[
        {"id":"model-responses","name":"Responses","type":"chat","recommended_api":"responses",
         "limit":{"context":800000,"output":240000},"modalities":{"input":["text/plain","image/png","not-supported"]},
         "capabilities":{"reasoning":{"supported":true,"toggle":true,"effort":["minimal","low","xhigh","max","unused"]}}},
        {"id":"model-messages","type":"chat","recommended_api":"messages",
         "capabilities":{"reasoning":{"supported":false}}},
        {"id":"embed-model","type":"embedding","recommended_api":"embeddings","reasoning":true},
        {"id":"other-model","type":"new-type"},
        {"id":"model-responses"}, {"id":""}, true
    ]});
    let catalog = models::normalize(&raw, GO_LIST, true, false).unwrap();
    assert_eq!(
        catalog.skipped,
        vec!["duplicate-id", "missing-id", "not-an-object"]
    );
    let list = json!(catalog.models);
    assert_eq!(list[0]["modelType"], "chat");
    assert_eq!(list[0]["apiType"], "responses");
    assert_eq!(list[0]["providerMetadata"], "opencode-go");
    assert_eq!(list[0]["maxOutputTokens"], 240000);
    assert_eq!(list[0]["modalities"], json!(["text", "image"]));
    assert_eq!(
        list[0]["reasoningLevels"],
        json!(["default", "none", "minimal", "low", "xhigh", "max"])
    );
    assert_eq!(list[1]["apiType"], "anthropic");
    assert_eq!(list[1]["reasoningLevels"], json!(["default"]));
    assert_eq!(list[2]["modelType"], "embedding");
    assert_eq!(list[2]["reasoningLevels"], json!([]));
    assert!(list[2].get("apiType").is_none());
    assert_eq!(list[3]["modelType"], "other");
}

#[test]
fn openai_listing_scopes_tokens_and_keeps_only_visible_plan_descriptors() {
    let base = parse_origin("https://api.openai.com/v1").unwrap();
    assert_eq!(
        endpoint(base.clone(), CredentialKind::ChatGptPlan, false)
            .unwrap()
            .as_str(),
        OPENAI_LIST
    );
    assert_eq!(
        endpoint(base, CredentialKind::OpenAiOauth, false)
            .unwrap()
            .as_str(),
        CODEX_LIST
    );
    let raw = json!({"models":[
        {"slug":"hidden-model","visibility":"hide"},{"slug":"gpt-6.1-sol","visibility":"list"},
        {"slug":"gpt-6.2-sol","visibility":"list","context_window":972800,
         "reasoning":{"mandatory":true, "supported_efforts":["low","high"]}}
    ]});
    let list = json!(
        models::normalize(&raw, OPENAI_LIST, false, true)
            .unwrap()
            .models
    );
    assert_eq!(list.as_array().unwrap().len(), 2);
    assert_eq!(list[0]["id"], "gpt-6.1-sol");
    assert_eq!(list[0]["apiType"], "responses");
    assert_eq!(list[0]["contextLength"], 1_050_000);
    assert_eq!(
        list[0]["reasoningLevels"],
        json!(["default", "low", "medium", "high", "xhigh", "max"])
    );
    assert_eq!(list[1]["maxOutputTokens"], 128000);
    assert_eq!(list[1]["contextLength"], 972800);
    assert_eq!(
        list[1]["reasoningLevels"],
        json!(["default", "low", "high"])
    );

    let raw = json!({"data":[{"id":"nvidia/nemotron-3.5-lightning-30b-a3b","max_tokens":65000},{"id":"other-model"}]});
    let list = json!(
        models::normalize(&raw, "https://integrate.api.nvidia.com/v1", false, false)
            .unwrap()
            .models
    );
    assert_eq!(list[0]["apiType"], "chat-completions");
    assert_eq!(list[0]["reasoningLevels"], json!(["default", "none", "on"]));
    assert_eq!(list[0]["maxOutputTokens"], 32768);
    assert_eq!(list[1]["reasoningLevels"], json!([]));
}

#[test]
fn nvidia_embeddings_keep_their_input_modalities_even_when_the_list_omits_them() {
    let raw = json!({"data":[
        {"id":"nvidia/nemotron-3.5-embed-1b","object":"model","supports_thinking":true},
        {"id":"nvidia/nemotron-3.5-embed-8b","object":"model"},
        {"id":"snowflake/arctic-embed-l","object":"model"},
        {"id":"nvidia/nemotron-3.5-lightning-30b-a3b","object":"model"}
    ]});
    let direct = json!(
        models::normalize(&raw, "https://integrate.api.nvidia.com/v1", false, false)
            .unwrap()
            .models
    );
    for index in 0..2 {
        assert_eq!(direct[index]["modelType"], "embedding");
        assert_eq!(direct[index]["modalities"], json!(["text", "image"]));
        assert_eq!(direct[index]["reasoningLevels"], json!([]));
        assert!(direct[index].get("apiType").is_none());
    }
    assert_eq!(direct[2]["modelType"], "embedding");
    assert_eq!(direct[2]["modalities"], json!(["text"]));
    assert_eq!(direct[3]["apiType"], "chat-completions");
    assert!(direct[3].get("modelType").is_none());
    let other = json!(
        models::normalize(&raw, "https://proxy.example.test/v1", false, false)
            .unwrap()
            .models
    );
    assert!(
        other
            .as_array()
            .unwrap()
            .iter()
            .all(|row| row.get("modelType").is_none())
    );
}

#[tokio::test]
async fn discovery_reads_only_the_selected_account_and_does_not_follow_redirects() {
    let requests = Arc::new(AtomicUsize::new(0));
    let calls = requests.clone();
    let service = Router::new()
        .route(
            "/v1/models",
            get(move |headers: HeaderMap| {
                calls.fetch_add(1, Ordering::Relaxed);
                async move {
                    assert_eq!(headers[header::AUTHORIZATION], "Bearer fixture-direct");
                    assert_eq!(headers[header::ACCEPT], "application/json");
                    Json(json!({"data":[{"id":"fixture-model"}]}))
                }
            }),
        )
        .route(
            "/redirect/v1/models",
            get(|| async {
                (
                    StatusCode::TEMPORARY_REDIRECT,
                    [(header::LOCATION, "/v1/models")],
                )
            }),
        )
        .route(
            "/quota/v1/models",
            get(|| async {
                (
                    StatusCode::TOO_MANY_REQUESTS,
                    "provider response with fixture-direct",
                )
            }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move { axum::serve(listener, service).await.unwrap() });
    let root = tempfile::tempdir().unwrap();
    let plan = json!({"kind":"custom","catalog":{"providers":[
        {"id":"direct","origin":base},{"id":"redirect","origin":format!("{base}/redirect")},
        {"id":"quota","origin":format!("{base}/quota")},{"id":"missing","origin":base},
        {"id":"opencode-2","origin":base}
    ],"models":[],"bindings":[]}});
    let secrets = json!({"providers":{"direct":"fixture-direct","redirect":"fixture-other",
        "quota":"fixture-quota", "opencode-2":"fixture-go"}});
    for (file, content) in [("openbot-plan.json", &plan), ("secrets.json", &secrets)] {
        fs::write(root.path().join(file), content.to_string()).unwrap();
    }
    let store = CredentialStore::new(root.path(), false).unwrap();
    let state = State::new(root.path().into(), false).unwrap();
    let discovery = Discovery::new(state.clone(), store).unwrap();
    let host = "127.0.0.1:39280";
    let app = server::router(state, host.into(), [discovery.routes()]);
    for (id, code, kind) in [
        ("direct", 200, ""),
        ("redirect", 502, "unreachable"),
        ("quota", 502, "unreachable"),
        ("missing", 409, "no-secret"),
        ("other", 404, "provider-not-found"),
        ("opencode-2", 400, "not-supported"),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::post(format!("/api/providers/{id}/fetch-models"))
                    .header(header::HOST, host)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status().as_u16(), code);
        let bytes = to_bytes(response.into_body(), 64 * 1024).await.unwrap();
        let body: Value = serde_json::from_slice(&bytes).unwrap();
        assert!(!body.to_string().contains("fixture-direct"));
        if code == 200 {
            assert_eq!(body["models"][0]["id"], "fixture-model");
            assert_eq!(body["providerId"], id);
            assert_eq!(body["skipped"], 0);
            let at = body["fetchedAt"].as_str().unwrap();
            assert!(at.contains('T') && at.ends_with('Z'));
        } else {
            assert_eq!(body["error"]["kind"], kind);
        }
        if id == "redirect" {
            assert_eq!(body["error"]["upstreamStatus"], 307);
        }
        if id == "quota" {
            assert_eq!(body["error"]["upstreamStatus"], 429);
        }
    }
    assert_eq!(requests.load(Ordering::Relaxed), 1);
    task.abort();
}
