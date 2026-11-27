use std::{
    fs,
    sync::atomic::{AtomicUsize, Ordering},
};

use axum::{
    body::{Body, to_bytes},
    extract::Path as AxumPath,
    http::{HeaderMap, Method, Request, header},
};
use tokio::sync::Semaphore;
use tower::ServiceExt;
use url::Url;

use super::*;
use crate::catalog::Provider;

const HOST: &str = "127.0.0.1:18080";

struct Upstream {
    entered: Semaphore,
    ready: Semaphore,
    lists: AtomicUsize,
    active: AtomicUsize,
    peak: AtomicUsize,
    public: AtomicUsize,
}

impl Default for Upstream {
    fn default() -> Self {
        Self {
            entered: Semaphore::new(0),
            ready: Semaphore::new(0),
            lists: AtomicUsize::default(),
            active: AtomicUsize::default(),
            peak: AtomicUsize::default(),
            public: AtomicUsize::default(),
        }
    }
}

async fn list(
    AxumState(upstream): AxumState<Arc<Upstream>>,
    AxumPath(account): AxumPath<String>,
    headers: HeaderMap,
) -> Json<Value> {
    assert!(matches!(account.as_str(), "one" | "two" | "three"));
    assert_eq!(
        headers[header::AUTHORIZATION],
        format!("Bearer fixture-{account}")
    );
    upstream.lists.fetch_add(1, Ordering::SeqCst);
    let active = upstream.active.fetch_add(1, Ordering::SeqCst) + 1;
    upstream.peak.fetch_max(active, Ordering::SeqCst);
    upstream.entered.add_permits(1);
    upstream.ready.acquire().await.unwrap().forget();
    upstream.active.fetch_sub(1, Ordering::SeqCst);
    Json(json!({"data":[{"id":"new-model","name":format!("{account} discovery") }]}))
}

async fn public(
    AxumState(upstream): AxumState<Arc<Upstream>>,
    AxumPath(feed): AxumPath<String>,
    headers: HeaderMap,
) -> Json<Value> {
    assert!(!headers.contains_key(header::AUTHORIZATION));
    upstream.public.fetch_add(1, Ordering::SeqCst);
    Json(if feed == "router" {
        json!({"data":[{"id":"model-from-router","name":"Public router"}]})
    } else {
        json!({"opencode-go":{"api":"https://opencode.ai/inference/go","models":{
            "model-from-go":{"id":"model-from-go","limit":{"context":200000}}
        }}})
    })
}

fn write(root: &Path, name: &str, value: &Value) {
    fs::write(root.join(name), value.to_string()).unwrap();
}

fn read(root: &Path, name: &str) -> Value {
    serde_json::from_slice(&fs::read(root.join(name)).unwrap()).unwrap()
}

async fn call(app: &Router, method: Method, uri: &str) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .header(header::HOST, HOST)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    (status, serde_json::from_slice(&body).unwrap())
}

#[tokio::test]
async fn manual_and_scheduled_refresh_share_one_run_and_merge_newer_settings() {
    let counts = Arc::new(Upstream::default());
    let upstream = Router::new()
        .route("/public/{feed}", get(public))
        .route("/{account}/v1/models", get(list))
        .with_state(counts.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let upstream = tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
    let root = tempfile::tempdir().unwrap();
    let path = root.path();
    let providers: Vec<_> = ["one", "two", "three", "n5-retired"]
        .into_iter()
        .map(|account| json!({"id":account,"origin":format!("{base}/{account}")}))
        .collect();
    let mut plan = json!({
        "kind":"custom","catalog":{
            "providers":providers,
            "models":[{"id":"custom-kept","slug":"kept","providerId":"one","activeReasoning":"default",
                "reasoningLevels":["default","high","max"],"parameters":[]}],
            "bindings":[{"conversation":{"kind":"wildcard"},"modelId":"custom-kept"}]
        },
        "agents":{"*":{"modelId":"kept","providerId":"one"}},
        "extra":"preserved"
    });
    write(path, "openbot-plan.json", &plan);
    write(
        path,
        "secrets.json",
        &json!({"providers":{"one":"fixture-one","two":"fixture-two",
        "three":"fixture-three","n5-retired":"fixture-retired"}}),
    );
    write(path, SETTINGS_FILE, &json!({"intervalHours":0.5}));
    fs::write(path.join("openbot-mode"), "official\n").unwrap();
    let state = State::with_catalog_writer(path.to_path_buf(), false, true).unwrap();
    assert!(State::new(path.to_path_buf(), true).is_err());
    let credentials = CredentialStore::new(path, true).unwrap();
    let discovery = Discovery::new(state.clone(), credentials.clone()).unwrap();
    let urls = [
        Url::parse(&format!("{base}/public/router")).unwrap(),
        Url::parse(&format!("{base}/public/dev")).unwrap(),
    ];
    let make = || {
        CatalogService::with_job(
            state.clone(),
            Some(Job {
                discovery: discovery.clone(),
                credentials: credentials.clone(),
                public: PublicCatalog::with_urls(urls.clone()),
            }),
        )
    };
    let service = make();
    let app = crate::server::router(state.clone(), HOST.into(), [service.routes()]);
    let (status, first) = call(&app, Method::POST, "/api/model-catalog/refresh").await;
    assert_eq!(status, StatusCode::ACCEPTED);
    let joined = service.check_schedule().await.unwrap();
    assert!(joined.is_none() || json!(joined) == first["startedAt"]);
    let (_, second) = call(&app, Method::POST, "/api/model-catalog/refresh").await;
    assert_eq!(second["startedAt"], first["startedAt"]);
    tokio::time::timeout(Duration::from_secs(4), counts.entered.acquire_many(2))
        .await
        .unwrap()
        .unwrap()
        .forget();
    let (_, running) = call(&app, Method::GET, "/api/model-catalog").await;
    assert_eq!(running["status"], "loading");
    assert_eq!(running["totalModels"], 2);
    assert_eq!(running["autoRefresh"]["intervalHours"], 1.0);
    assert_eq!(counts.lists.load(Ordering::SeqCst), 2);
    let stale = Provider::from_saved(&plan["catalog"]["providers"][1]).unwrap();
    plan["catalog"]["providers"][1]["origin"] = json!(format!("{base}/changed"));
    plan["catalog"]["models"][0]["activeReasoning"] = json!("max");
    write(path, "openbot-plan.json", &plan);
    let err = discovery.list(&stale).await.unwrap_err();
    assert_eq!(err.kind(), "provider-changed");
    counts.ready.add_permits(3);
    tokio::time::timeout(Duration::from_secs(4), service.wait_idle())
        .await
        .unwrap();
    let (_, ready) = call(&app, Method::GET, "/api/model-catalog").await;
    assert_eq!(ready["status"], "ready");
    assert_eq!(ready["autoRefresh"]["state"], "completed");
    assert_eq!(ready["autoRefresh"]["added"], 2);
    assert_eq!(
        ready["autoRefresh"]["providers"][3]["errorKind"],
        "not-supported"
    );
    assert_eq!(counts.peak.load(Ordering::SeqCst), 2);
    assert_eq!(counts.lists.load(Ordering::SeqCst), 3);
    assert_eq!(counts.public.load(Ordering::SeqCst), 2);
    let saved = read(path, "openbot-plan.json");
    assert_eq!(saved["catalog"]["models"][0]["activeReasoning"], "max");
    assert_eq!(saved["catalog"]["bindings"], plan["catalog"]["bindings"]);
    assert_eq!(saved["catalog"]["models"][1]["id"], "one:new-model");
    assert_eq!(saved["catalog"]["models"][2]["id"], "three:new-model");
    assert_eq!(read(path, CACHE_FILE)["totalModels"], 2);
    assert_eq!(read(path, STATUS_FILE)["state"], "completed");
    assert_eq!(
        fs::read_to_string(path.join("openbot-mode")).unwrap(),
        "official\n"
    );

    let restored = make();
    assert!(restored.check_schedule().await.unwrap().is_none());
    write(path, SETTINGS_FILE, &json!({"enabled":false}));
    assert!(restored.check_schedule().await.unwrap().is_none());
    let reopened = crate::server::router(state, HOST.into(), [restored.routes()]);
    let (code, _) = call(&reopened, Method::POST, "/api/model-catalog/refresh").await;
    assert_eq!(code, StatusCode::ACCEPTED);
    tokio::time::timeout(Duration::from_secs(4), restored.wait_idle())
        .await
        .unwrap();
    assert_eq!(counts.lists.load(Ordering::SeqCst), 3);
    assert_eq!(counts.public.load(Ordering::SeqCst), 4);
    assert_eq!(read(path, STATUS_FILE)["state"], "disabled");
    upstream.abort();
}

#[test]
fn interrupted_refresh_is_due_after_restart() {
    let root = tempfile::tempdir().unwrap();
    write(
        root.path(),
        STATUS_FILE,
        &json!({
            "state":"running","nextAttemptAt":"2099-11-26T11:00:00Z"
        }),
    );
    let state = State::new(root.path().to_path_buf(), false).unwrap();
    let service = CatalogService::new(state, None).unwrap();
    assert!(is_due(
        &service.read().unwrap().status,
        OffsetDateTime::now_utc()
    ));
}
