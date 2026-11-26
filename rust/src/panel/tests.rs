use std::fs;

use axum::{
    body::to_bytes,
    http::{Request, StatusCode},
};
use tower::ServiceExt;

use super::*;
use crate::server::{self, State};

const HOST: &str = "127.0.0.1:42880";
const STATUS: ListenerStatus = ListenerStatus {
    port: 42880,
    serves_ui: true,
    serves_hop: false,
};

fn app(root: &Path) -> Router {
    let state = State::new(root.to_path_buf(), false).unwrap();
    let credential = CredentialStore::new(root, false).unwrap();
    let panel = routes(root.join("ui"), credential, STATUS, None).unwrap();
    server::router(state, HOST.into(), [panel])
}

fn fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("ui")).unwrap();
    for (_, filename, _) in ASSETS {
        fs::write(
            root.path().join("ui").join(filename),
            format!("fixture {filename}"),
        )
        .unwrap();
    }
    fs::write(root.path().join("openbot-mode"), "official\n").unwrap();
    fs::write(
        root.path().join("secrets.json"),
        r#"{"providers":{"openai":"fixture-secret"}}"#,
    )
    .unwrap();
    fs::write(
        root.path().join("openbot-logs.json"),
        r#"{"loggingEnabled":true,"logRetentionDays":"70"}"#,
    )
    .unwrap();
    fs::write(root.path().join("openbot-plan.json"), json!({"kind":"custom","catalog":{
        "providers":[{"id":"openai","name":"OpenAI fixture","origin":"https://api.openai.com/v1","apiType":"responses"}],
        "models":[{"id":"openai:saved-model","slug":"saved-model","providerId":"openai"}],
        "bindings":[{"conversation":{"kind":"wildcard"},"modelId":"openai:saved-model"}]
    }}).to_string()).unwrap();
    root
}

async fn get(app: &Router, route: &str) -> Response {
    app.clone()
        .oneshot(
            Request::get(route)
                .header(header::HOST, HOST)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap()
}

#[tokio::test]
async fn panel_serves_only_the_built_assets_and_never_credential_files() {
    let state = fixture();
    let app = app(state.path());
    for (route, filename, mime) in ASSETS {
        let response = get(&app, route).await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()[header::CONTENT_TYPE], mime);
        assert_eq!(
            response.headers()[header::X_CONTENT_TYPE_OPTIONS],
            "nosniff"
        );
        assert_eq!(
            to_bytes(response.into_body(), 50).await.unwrap(),
            format!("fixture {filename}")
        );
    }
    assert_eq!(get(&app, "/healthz").await.status(), StatusCode::OK);
    for path in [
        "/secrets.json",
        "/ui/app.js",
        "/../secrets.json",
        "/%2e%2e/secrets.json",
    ] {
        assert_eq!(get(&app, path).await.status(), StatusCode::NOT_FOUND);
    }
    #[cfg(unix)]
    {
        let file = state.path().join("ui/app.js");
        fs::remove_file(&file).unwrap();
        std::os::unix::fs::symlink(state.path().join("secrets.json"), file).unwrap();
        let credential = CredentialStore::new(state.path(), false).unwrap();
        assert!(routes(state.path().join("ui"), credential, STATUS, None).is_err());
    }
}

#[tokio::test]
async fn panel_reports_saved_state_with_no_credentials_and_accurate_listener_status() {
    let root = fixture();
    let app = app(root.path());
    for path in ["/api/state", "/api/snapshot"] {
        let response = get(&app, path).await;
        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), 4096).await.unwrap();
        assert!(
            !std::str::from_utf8(&body)
                .unwrap()
                .contains("fixture-secret")
        );
        let state: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(state["snapshot"]["alignment"]["desired"], "official");
        assert_eq!(state["snapshot"]["uiListen"]["port"], STATUS.port);
        assert_eq!(state["snapshot"]["hopListen"]["kind"], "absent");
        assert_eq!(state["keyedProviders"], json!(["openai"]));
        assert_eq!(state["activeModelId"], "openai:saved-model");
        assert_eq!(state["logSettings"]["logRetentionDays"], 70);
        assert_eq!(state["logSettings"]["loggingEnabled"], true);
    }
}
