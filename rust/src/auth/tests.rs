use std::{
    collections::{HashMap, VecDeque},
    fs,
    io::Write,
    path::Path,
    process::{Command, Stdio},
    sync::{Arc, Mutex},
};

use axum::{
    Router,
    body::{Body, Bytes, to_bytes},
    extract::State as AxumState,
    http::{Method, Request, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tempfile::TempDir;
use tokio::sync::Semaphore;
use tower::ServiceExt;
use url::Url;

use super::{credential::RESOURCE, network::Network, *};
use crate::{
    routing::CredentialKind,
    server::{State, router},
};

const HOST: &str = "127.0.0.1:30444";
const DIRECT: &str = "https://api.openai.com/v1";
const PROVIDER: &str = "openai-2";
const SCOPES: &str =
    "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct";

type Form = HashMap<String, String>;

#[derive(Default)]
struct Replies {
    requests: Vec<Form>,
    payloads: VecDeque<Response>,
    keys: Value,
    gate: Option<Arc<Gate>>,
}

struct Gate {
    entered: Semaphore,
    release: Semaphore,
}

struct Upstream {
    network: Network,
    inner: Arc<Mutex<Replies>>,
    task: tokio::task::JoinHandle<()>,
}

impl Upstream {
    async fn start() -> Self {
        let inner = Arc::new(Mutex::new(Replies::default()));
        let app = Router::new()
            .route("/plan-token", post(mock_token))
            .route("/legacy-token", post(mock_token))
            .route(
                "/keys",
                get(
                    |AxumState(state): AxumState<Arc<Mutex<Replies>>>| async move {
                        Json(state.lock().unwrap().keys.clone())
                    },
                ),
            )
            .with_state(inner.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
        let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let mut network = Network::new().unwrap();
        network.plan_token = base.join("plan-token").unwrap();
        network.codex_token = base.join("legacy-token").unwrap();
        network.jwks = base.join("keys").unwrap();
        Self {
            network,
            inner,
            task,
        }
    }

    fn reply(&self, status: StatusCode, body: Value) {
        self.inner
            .lock()
            .unwrap()
            .payloads
            .push_back((status, Json(body)).into_response());
    }

    fn gate(&self) -> Arc<Gate> {
        let gate = Arc::new(Gate {
            entered: Semaphore::new(0),
            release: Semaphore::new(0),
        });
        self.inner.lock().unwrap().gate = Some(gate.clone());
        gate
    }

    fn requests(&self) -> Vec<Form> {
        self.inner.lock().unwrap().requests.clone()
    }
}

impl Drop for Upstream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn mock_token(AxumState(state): AxumState<Arc<Mutex<Replies>>>, body: Bytes) -> Response {
    let (response, gate) = {
        let mut state = state.lock().unwrap();
        state
            .requests
            .push(url::form_urlencoded::parse(&body).into_owned().collect());
        (state.payloads.pop_front(), state.gate.take())
    };
    if let Some(gate) = gate {
        gate.entered.add_permits(1);
        gate.release.acquire().await.unwrap().forget();
    }
    response.unwrap_or_else(|| StatusCode::INTERNAL_SERVER_ERROR.into_response())
}

fn saved_plan(expiry: u64, access: &str, refresh: &str) -> Value {
    json!({
        "kind":"chatgpt-plan","clientId":"oaiapp_fixture","subject":"fixture-subject",
        "idToken":"fixture-id","accessToken":access,"refreshToken":refresh,
        "expiresAt":expiry,"scopes":SCOPES.split_whitespace().collect::<Vec<_>>(),
        "preservedField":"keep"
    })
}

fn seed() -> TempDir {
    let root = tempfile::tempdir().unwrap();
    write(
        root.path(),
        "openbot-plan.json",
        &json!({
            "kind":"custom","catalog":{
                "providers":[
                    {"id":"openai","origin":DIRECT,"apiType":"responses"},
                    {"id":PROVIDER,"origin":DIRECT,"apiType":"responses"}
                ],
                "models":[{"id":"openai:gpt-fixture","slug":"gpt-fixture","providerId":"openai"}],
                "bindings":[{"conversation":{"kind":"wildcard"},"modelId":"openai:gpt-fixture"}]
            },
            "agents":{"*":{"modelId":"gpt-fixture","providerId":"openai"}},
            "extra":"keep"
        }),
    );
    write(
        root.path(),
        "secrets.json",
        &json!({
            "providers":{"openai":"fixture-first", (PROVIDER):saved_plan(0, "fixture-old", "fixture-renew").to_string(),
                "openai-3":"reserved","opencode-go":"fixture-go"},
            "extra":{"keep":true}
        }),
    );
    fs::write(root.path().join("openbot-mode"), "official\n").unwrap();
    root
}

fn write(root: &Path, file: &str, value: &Value) {
    fs::write(root.join(file), value.to_string()).unwrap();
}

fn credentials(root: &Path) -> Value {
    serde_json::from_slice(&fs::read(root.join("secrets.json")).unwrap()).unwrap()
}

fn record(root: &Path) -> Value {
    serde_json::from_str(credentials(root)["providers"][PROVIDER].as_str().unwrap()).unwrap()
}

fn account_router(root: &Path, upstream: &Upstream) -> Router {
    let path = root.to_path_buf();
    let state = State::new(path, false).unwrap();
    let store = CredentialStore::with_network(root, true, upstream.network.clone()).unwrap();
    let status = crate::panel::ListenerStatus {
        port: 30444,
        serves_ui: true,
        serves_hop: false,
        public_access: None,
    };
    let accounts = Accounts::new(store, HOST, status, None).unwrap();
    router(state, HOST.into(), [accounts.routes()])
}

#[tokio::test]
async fn catalog_edits_keep_provider_accounts_and_model_settings_independent() {
    let upstream = Upstream::start().await;
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("openbot-mode"), "official\n").unwrap();
    let app = account_router(root.path(), &upstream);
    let add = |name, origin, secret| {
        json!({
            "kind":"add-provider", "familyId":"opencode-go", "name":name,
            "origin":origin, "apiType":"responses", "secret":secret
        })
    };
    let invalid = [
        add(
            "Go on retired endpoint",
            "https://n5.example.test/v1",
            "fixture-placeholder",
        ),
        add("Zen", "https://opencode.ai/zen/v1", "fixture-placeholder"),
        json!({"kind":"add-provider","familyId":"alibaba-token-plan","name":"Token Plan",
            "origin":"https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1","secret":"payg-fixture"}),
    ];
    for command in invalid {
        assert_eq!(
            post_json(&app, "/api/save", command).await.0,
            StatusCode::BAD_REQUEST
        );
        assert!(!root.path().join("openbot-plan.json").exists());
        assert!(!root.path().join("secrets.json").exists());
    }
    for (name, origin, secret, id) in [
        (
            "Go One",
            "https://opencode.ai/zen/go/v1",
            "fixture-one",
            "opencode-go",
        ),
        (
            "Go Two",
            "https://opencode.ai/inference/go",
            "fixture-two",
            "opencode-go-2",
        ),
    ] {
        let (status, result) = post_json(&app, "/api/save", add(name, origin, secret)).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(result["createdProviderId"], id);
        assert!(!result.to_string().contains(secret));
        assert_eq!(credentials(root.path())["providers"][id], secret);
    }
    let plan_path = root.path().join("openbot-plan.json");
    let mut stored: Value = serde_json::from_slice(&fs::read(&plan_path).unwrap()).unwrap();
    assert_eq!(stored["hop"]["port"], 30444);
    stored["futureField"] = json!(["keep"]);
    fs::write(&plan_path, stored.to_string()).unwrap();
    for (provider, slug, effort) in [
        ("opencode-go", "demo", "low"),
        ("opencode-go", "fallback", "high"),
        ("opencode-go-2", "demo", "high"),
    ] {
        let (status, result) = post_json(&app, "/api/save",json!({
            "kind":"upsert-model","providerId":provider,"slug":slug,"modelType":"chat",
            "contextTokens": 524288, "maxOutputTokens":32768, "apiType":"responses",
            "reasoningLevels":["default","low","high"], "activeReasoning":effort,
            "catalogLimits":{"sourceUrl":"https://models.dev/api.json", "providerKey":"fixture","contextTokens":524288},
            "modalities":["text","image"],
        })).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(result["activeModelId"], "opencode-go:demo");
        let rows = result["models"].as_array().unwrap();
        let row = rows
            .iter()
            .find(|row| row["id"] == format!("{provider}:{slug}"))
            .unwrap();
        assert_eq!(row["contextTokens"], 524288);
        assert_eq!(row["activeReasoning"], effort);
        assert_eq!(row["catalogLimits"]["providerKey"], "fixture");
        assert_eq!(row["modalities"], json!(["text", "image"]));
    }
    let (_, added) = post_json(&app, "/api/save",json!({
        "kind":"upsert-model","providerId":"opencode-go-2","slug":"embedding","modelType":"embedding"
    })).await;
    assert_eq!(added["activeModelId"], "opencode-go:demo");
    let mut plan: Value = serde_json::from_slice(&fs::read(&plan_path).unwrap()).unwrap();
    plan["agents"]["old-thread"] = json!({"modelId":"demo","providerId":"opencode-go"});
    plan["agents"]["keep-thread"] = json!({"modelId":"demo","providerId":"opencode-go-2"});
    plan["catalog"]["bindings"].as_array_mut().unwrap().extend([
        json!({"conversation":{"kind":"named","id":"old-thread"},"modelId":"opencode-go:demo"}),
        json!({"conversation":{"kind":"named","id":"keep-thread"},"modelId":"opencode-go-2:demo"}),
    ]);
    fs::write(&plan_path, plan.to_string()).unwrap();
    let (status, removed) = post_json(
        &app,
        "/api/save",
        json!({"kind":"remove-model","modelId":"opencode-go:demo"}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(removed["activeModelId"], "opencode-go:fallback");
    let saved: Value = serde_json::from_slice(&fs::read(&plan_path).unwrap()).unwrap();
    assert_eq!(saved["futureField"], json!(["keep"]));
    assert!(saved["agents"].get("old-thread").is_none());
    assert_eq!(
        saved["agents"]["keep-thread"],
        plan["agents"]["keep-thread"]
    );
    assert_eq!(saved["agents"]["*"]["modelId"], "fallback");
    let (status, removed) = post_json(
        &app,
        "/api/save",
        json!({"kind":"remove-provider","providerId":"opencode-go"}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(removed["activeModelId"], "opencode-go-2:demo");
    assert_eq!(
        credentials(root.path())["providers"]["opencode-go"],
        "fixture-one"
    );
    assert_eq!(
        post_json(
            &app,
            "/api/save",
            json!({"kind":"set-secret","providerId":"opencode-go-2","secret":"fixture-rotated"})
        )
        .await
        .0,
        StatusCode::OK
    );
    assert_eq!(
        credentials(root.path())["providers"]["opencode-go-2"],
        "fixture-rotated"
    );
    let mode = root.path().join("openbot-mode");
    let before = fs::read(&plan_path).unwrap();
    let remove = json!({"kind":"remove-provider","providerId":"opencode-go-2"});
    fs::write(&mode, "custom\n").unwrap();
    assert_eq!(
        post_json(&app, "/api/save", remove.clone()).await.0,
        StatusCode::CONFLICT
    );
    assert_eq!(fs::read(&plan_path).unwrap(), before);
    assert_eq!(fs::read_to_string(&mode).unwrap(), "custom\n");
    fs::write(&mode, "official\n").unwrap();
    assert_eq!(post_json(&app, "/api/save", remove).await.0, StatusCode::OK);
    assert!(!plan_path.exists());
    assert_eq!(
        credentials(root.path())["providers"]["opencode-go-2"],
        "fixture-rotated"
    );
    assert_eq!(
        fs::read_to_string(root.path().join("openbot-mode")).unwrap(),
        "official\n"
    );
}

#[tokio::test]
async fn changing_provider_origin_requires_explicit_credentials_and_rejects_a_stale_route() {
    let upstream = Upstream::start().await;
    let root = tempfile::tempdir().unwrap();
    let app = account_router(root.path(), &upstream);
    let old = "https://first.example.test/v1";
    let new = "https://second.example.test/v1";
    assert_eq!(
        post_json(
            &app,
            "/api/save",
            json!({"kind":"add-provider","familyId":"custom",
            "name":"Fixture","origin":old,"secret":"fixture-old"})
        )
        .await
        .0,
        StatusCode::OK
    );
    let edit =
        json!({"kind":"update-provider","providerId":"custom","name":"Changed","origin":new});
    assert_eq!(
        post_json(&app, "/api/save", edit.clone()).await.0,
        StatusCode::BAD_REQUEST
    );
    let mut authorized = edit;
    authorized["secret"] = json!("fixture-new");
    let (status, saved) = post_json(&app, "/api/save", authorized).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(saved["providers"][0]["origin"], new);
    assert!(!saved.to_string().contains("fixture-new"));
    assert!(
        !root
            .path()
            .join(".openbot-rust-provider-move.json")
            .exists()
    );
    let reader = CredentialStore::new(root.path(), false).unwrap();
    let Err(error) = reader.authorize_configured("custom", old).await else {
        panic!("a stale route must never obtain the replacement credential");
    };
    assert_eq!(error.status, StatusCode::CONFLICT);
    assert_eq!(
        reader
            .authorize_configured("custom", new)
            .await
            .unwrap()
            .token,
        "fixture-new"
    );
}

async fn request(app: &Router, method: Method, path: &str, body: Value) -> (StatusCode, String) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method(method)
                .uri(path)
                .header(header::HOST, HOST)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.headers()[header::CACHE_CONTROL], "no-store");
    assert_eq!(response.headers()[header::REFERRER_POLICY], "no-referrer");
    let status = response.status();
    let body = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    (status, String::from_utf8(body.to_vec()).unwrap())
}

async fn post_json(app: &Router, path: &str, body: Value) -> (StatusCode, Value) {
    let (status, text) = request(app, Method::POST, path, body).await;
    (status, serde_json::from_str(&text).unwrap())
}

fn signing_fixture(dir: &TempDir) -> Value {
    let file = dir.path().join("fixture-rsa.pem");
    let result = Command::new("openssl")
        .args([
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
        ])
        .arg(&file)
        .output()
        .expect("OpenSSL is required for the ephemeral sign-in fixture");
    assert!(result.status.success());
    let result = Command::new("openssl")
        .args(["rsa", "-noout", "-modulus", "-in"])
        .arg(&file)
        .output()
        .unwrap();
    assert!(result.status.success());
    let output = String::from_utf8(result.stdout).unwrap();
    let hex = output.trim().strip_prefix("Modulus=").unwrap().as_bytes();
    let modulus: Vec<_> = hex
        .chunks_exact(2)
        .map(|bytes| u8::from_str_radix(std::str::from_utf8(bytes).unwrap(), 16).unwrap())
        .collect();
    json!({"keys":[{
        "kid":"fixture-rsa", "kty":"RSA", "alg":"RS256", "use":"sig",
        "n":URL_SAFE_NO_PAD.encode(modulus), "e":URL_SAFE_NO_PAD.encode([1, 0, 1])
    }]})
}

fn signed_id(dir: &TempDir, audience: &str, nonce: &str, subject: &str) -> String {
    let header = URL_SAFE_NO_PAD.encode(json!({"alg":"RS256","kid":"fixture-rsa"}).to_string());
    let claims = URL_SAFE_NO_PAD.encode(
        json!({
            "iss":"https://auth.openai.com", "aud":audience, "nonce":nonce, "sub":subject,
            "exp":unix_seconds()+600
        })
        .to_string(),
    );
    let input = format!("{header}.{claims}");
    let mut process = Command::new("openssl")
        .args(["dgst", "-sha256", "-sign"])
        .arg(dir.path().join("fixture-rsa.pem"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    process
        .stdin
        .take()
        .unwrap()
        .write_all(input.as_bytes())
        .unwrap();
    let output = process.wait_with_output().unwrap();
    assert!(output.status.success());
    format!("{input}.{}", URL_SAFE_NO_PAD.encode(output.stdout))
}

#[tokio::test]
async fn signed_plan_callback_enforces_identity_and_renews_the_original_provider() {
    let upstream = Upstream::start().await;
    let root = seed();
    let key = tempfile::tempdir().unwrap();
    upstream.inner.lock().unwrap().keys = signing_fixture(&key);
    let app = account_router(root.path(), &upstream);
    let before = fs::read(root.path().join("openbot-plan.json")).unwrap();
    let mut host_id = None;
    for (subject, valid) in [("wrong-account", false), ("fixture-subject", true)] {
        let (status, start) = post_json(
            &app,
            "/api/oauth/chatgpt/start",
            json!({"providerId":PROVIDER}),
        )
        .await;
        assert_eq!(status, StatusCode::OK);
        let url = Url::parse(start["authorizationUrl"].as_str().unwrap()).unwrap();
        assert_eq!(url.host_str(), Some("auth.openai.com"));
        let query: Form = url.query_pairs().into_owned().collect();
        assert_eq!(
            query["redirect_uri"],
            format!("http://{HOST}/auth/callback")
        );
        assert_eq!(query["client_id"], "oaiapp_fixture");
        assert_eq!(query["resource"], RESOURCE);
        assert!(!query.contains_key("agent_name_hint"));
        assert_eq!(
            host_id.get_or_insert(query["ext_agent_host_id"].clone()),
            &query["ext_agent_host_id"]
        );
        let before_exchange = upstream.requests().len();
        let dup = format!(
            "/auth/callback?state={}&state=duplicate&code=opaque",
            query["state"]
        );
        assert_eq!(
            request(&app, Method::GET, &dup, Value::Null).await.0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(upstream.requests().len(), before_exchange);
        let id = signed_id(&key, &query["client_id"], &query["nonce"], subject);
        upstream.reply(
            StatusCode::OK,
            json!({
                "access_token":"fixture-new", "refresh_token":"fixture-next", "id_token":id,
                "expires_in":3600,"scope":SCOPES
            }),
        );
        let path = format!("/auth/callback?state={}&code=opaque", query["state"]);
        let (status, text) = request(&app, Method::GET, &path, Value::Null).await;
        assert_eq!(status == StatusCode::OK, valid);
        assert!(!text.contains("fixture-new"));
        let requests = upstream.requests();
        let exchange = requests.last().unwrap();
        assert_eq!(exchange["grant_type"], "authorization_code");
        assert_eq!(exchange["redirect_uri"], query["redirect_uri"]);
        assert_eq!(
            URL_SAFE_NO_PAD.encode(Sha256::digest(exchange["code_verifier"].as_bytes())),
            query["code_challenge"]
        );
        assert_eq!(
            request(&app, Method::GET, &path, Value::Null).await.0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(upstream.requests().len(), before_exchange + 1);
        let (_, result) = post_json(
            &app,
            "/api/oauth/chatgpt/status",
            json!({"sessionId":start["sessionId"]}),
        )
        .await;
        assert_eq!(record(root.path())["accessToken"], "fixture-old");
        if !valid {
            assert_eq!(result["phase"], "failed");
            continue;
        }
        assert_eq!(result["phase"], "ready");
        let (_, wrong) = post_json(
            &app,
            "/api/save",
            json!({
                "kind":"set-secret","providerId":"openai","credentialId":result["credentialId"]
            }),
        )
        .await;
        assert!(wrong["error"].is_string());
        let save = json!({"kind":"set-secret","providerId":PROVIDER,"credentialId":result["credentialId"]});
        let mut store = credentials(root.path());
        let mut changed = saved_plan(
            unix_seconds() + 1800,
            "fixture-other",
            "fixture-other-renew",
        );
        changed["subject"] = json!("changed-account");
        store["providers"][PROVIDER] = json!(changed.to_string());
        write(root.path(), "secrets.json", &store);
        assert_eq!(
            post_json(&app, "/api/save", save.clone()).await.0,
            StatusCode::CONFLICT
        );
        changed["subject"] = json!("fixture-subject");
        store["providers"][PROVIDER] = json!(changed.to_string());
        write(root.path(), "secrets.json", &store);
        let (status, saved) = post_json(&app, "/api/save", save.clone()).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(saved["activeModelId"], "openai:gpt-fixture");
        assert_eq!(saved["snapshot"]["alignment"]["desired"], "official");
        assert_eq!(saved["logSettings"]["logRetentionDays"], 7);
        assert!(!saved.to_string().contains("fixture-new"));
        assert_eq!(
            post_json(&app, "/api/save", save).await.0,
            StatusCode::BAD_REQUEST
        );
    }
    assert_eq!(record(root.path())["accessToken"], "fixture-new");
    assert_eq!(
        credentials(root.path())["providers"]["opencode-go"],
        "fixture-go"
    );
    assert_eq!(credentials(root.path())["extra"]["keep"], true);
    assert_eq!(
        fs::read(root.path().join("openbot-plan.json")).unwrap(),
        before
    );
    assert_eq!(
        fs::read_to_string(root.path().join("openbot-mode")).unwrap(),
        "official\n"
    );
}

#[tokio::test]
async fn first_plan_registration_saves_under_the_issued_client() {
    let upstream = Upstream::start().await;
    let root = seed();
    let key = tempfile::tempdir().unwrap();
    upstream.inner.lock().unwrap().keys = signing_fixture(&key);
    let app = account_router(root.path(), &upstream);
    let (status, start) = post_json(&app, "/api/oauth/chatgpt/start", json!({})).await;
    assert_eq!(status, StatusCode::OK);
    let url = Url::parse(start["authorizationUrl"].as_str().unwrap()).unwrap();
    let query: Form = url.query_pairs().into_owned().collect();
    assert_eq!(query["client_id"], "dynamic_agent_client");
    assert_eq!(query["agent_name_hint"], "OpenBot");
    upstream.reply(
        StatusCode::OK,
        json!({
            "access_token":"fixture-register", "refresh_token":"fixture-renew-now",
            "id_token":signed_id(&key, "oaiapp_new_fixture", &query["nonce"], "another-account"),
            "expires_in":3600, "scope":SCOPES, "earliest_refresh_at":unix_seconds()+1800
        }),
    );
    let path = format!(
        "/auth/callback?state={}&code=opaque&client_id=oaiapp_new_fixture",
        query["state"]
    );
    assert_eq!(
        request(&app, Method::GET, &path, Value::Null).await.0,
        StatusCode::OK
    );
    assert_eq!(upstream.requests()[0]["client_id"], "oaiapp_new_fixture");
    let (_, status) = post_json(
        &app,
        "/api/oauth/chatgpt/status",
        json!({"sessionId":start["sessionId"]}),
    )
    .await;
    assert_eq!(status["phase"], "ready");
    assert!(credentials(root.path())["providers"]["openai-4"].is_null());
    let (code, result) = post_json(
        &app,
        "/api/save",
        json!({
            "kind":"add-provider","familyId":"openai","name":"New ChatGPT account","origin":DIRECT,
            "apiType":"responses","credentialId":status["credentialId"]
        }),
    )
    .await;
    assert_eq!(code, StatusCode::OK);
    assert_eq!(result["createdProviderId"], "openai-4");
    let credentials = credentials(root.path());
    let saved: Value =
        serde_json::from_str(credentials["providers"]["openai-4"].as_str().unwrap()).unwrap();
    assert_eq!(saved["clientId"], "oaiapp_new_fixture");
    assert_eq!(saved["subject"], "another-account");
    assert_eq!(saved["accessToken"], "fixture-register");
    assert!(saved["earliestRefreshAt"].as_u64().unwrap() > unix_seconds());
    assert_eq!(record(root.path())["accessToken"], "fixture-old");
}

#[tokio::test]
async fn legacy_callback_adds_a_distinct_account_after_explicit_save() {
    let upstream = Upstream::start().await;
    let root = seed();
    let app = account_router(root.path(), &upstream);
    let (_, start) = post_json(&app, "/api/oauth/openai/start", json!({})).await;
    let url = Url::parse(start["authorizationUrl"].as_str().unwrap()).unwrap();
    assert_eq!(url.path(), "/oauth/authorize");
    let query: Form = url.query_pairs().into_owned().collect();
    let complete = |callback: &str| json!({"sessionId":start["sessionId"],"callbackUrl":callback});
    let redirected = format!(
        "http://localhost:1455.example.test/auth/callback?state={}&code=test",
        query["state"]
    );
    assert_eq!(
        post_json(&app, "/api/oauth/openai/complete", complete(&redirected))
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    assert!(upstream.requests().is_empty());
    let callback = format!(
        "{}?state={}&code=opaque",
        query["redirect_uri"], query["state"]
    );
    upstream.reply(StatusCode::OK, json!({
        "access_token":"fixture-legacy","refresh_token":"fixture-legacy-refresh","expires_in":3600
    }));
    let (status, ready) = post_json(&app, "/api/oauth/openai/complete", complete(&callback)).await;
    assert_eq!(status, StatusCode::OK);
    assert!(!ready.to_string().contains("fixture-legacy"));
    assert_eq!(
        post_json(&app, "/api/oauth/openai/complete", complete(&callback))
            .await
            .0,
        StatusCode::BAD_REQUEST
    );
    assert_eq!(upstream.requests().len(), 1);
    let (status, added) = post_json(
        &app,
        "/api/save",
        json!({
            "kind":"add-provider","familyId":"openai","name":"Separate account","origin":DIRECT,
            "apiType":"responses","credentialId":ready["credentialId"]
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(added["createdProviderId"], "openai-4");
    assert_eq!(added["activeModelId"], "openai:gpt-fixture");
    let saved = credentials(root.path());
    let added_credential: Value =
        serde_json::from_str(saved["providers"]["openai-4"].as_str().unwrap()).unwrap();
    assert_eq!(added_credential["kind"], "openai-oauth");
    assert_eq!(added_credential["accessToken"], "fixture-legacy");
    assert_eq!(saved["providers"]["openai-3"], "reserved");
    assert_eq!(record(root.path())["accessToken"], "fixture-old");
    assert_eq!(
        fs::read_to_string(root.path().join("openbot-mode")).unwrap(),
        "official\n"
    );
}

#[tokio::test]
async fn simultaneous_inference_shares_one_renewal_even_after_request_cancellation() {
    let upstream = Upstream::start().await;
    let root = seed();
    let store = CredentialStore::with_network(root.path(), true, upstream.network.clone()).unwrap();
    assert!(CredentialStore::new(root.path(), true).is_err());
    let gate = upstream.gate();
    upstream.reply(
        StatusCode::OK,
        json!({
            "access_token":"fixture-fresh","refresh_token":"fixture-rotated",
            "expires_in":3600,"earliest_refresh_at":unix_seconds()+1800
        }),
    );
    let one = store.clone();
    let first = tokio::spawn(async move { one.authorize(PROVIDER, DIRECT).await });
    gate.entered.acquire().await.unwrap().forget();
    first.abort();
    let waiters: Vec<_> = (0..12)
        .map(|_| {
            let store = store.clone();
            tokio::spawn(async move { store.authorize(PROVIDER, DIRECT).await.unwrap().token })
        })
        .collect();
    let ((), ()) = tokio::join!(store.wait_for_renewals(), async {
        gate.release.add_permits(1);
    });
    assert_eq!(record(root.path())["refreshToken"], "fixture-rotated");
    for waiter in waiters {
        assert_eq!(waiter.await.unwrap(), "fixture-fresh");
    }
    let requests = upstream.requests();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0]["refresh_token"], "fixture-renew");
    assert_eq!(requests[0]["resource"], RESOURCE);
    let renewed = record(root.path());
    assert_eq!(renewed["refreshToken"], "fixture-rotated");
    assert_eq!(renewed["preservedField"], "keep");
    assert_eq!(renewed["scopes"], saved_plan(0, "", "")["scopes"]);
    assert!(renewed["earliestRefreshAt"].as_u64().unwrap() > unix_seconds());
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(root.path().join("secrets.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
}

#[tokio::test]
async fn renewal_preserves_a_newer_save_and_never_follows_an_upstream_redirect() {
    let upstream = Upstream::start().await;
    let root = seed();
    let store = CredentialStore::with_network(root.path(), true, upstream.network.clone()).unwrap();
    let gate = upstream.gate();
    upstream.reply(StatusCode::OK, json!({
        "access_token":"fixture-from-refresh", "refresh_token":"fixture-rotated", "expires_in":3600
    }));
    let reader = store.clone();
    let pending =
        tokio::spawn(async move { reader.authorize(PROVIDER, DIRECT).await.unwrap().token });
    gate.entered.acquire().await.unwrap().forget();
    store
        .save_secret(PROVIDER, "explicit-account-key", None)
        .unwrap();
    gate.release.add_permits(1);
    assert_eq!(pending.await.unwrap(), "explicit-account-key");
    assert_eq!(
        credentials(root.path())["providers"][PROVIDER],
        "explicit-account-key"
    );
    let expired = saved_plan(0, "fixture-expired", "fixture-renew");
    store
        .save_secret(PROVIDER, &expired.to_string(), None)
        .unwrap();
    upstream.inner.lock().unwrap().payloads.push_back(
        (
            StatusCode::TEMPORARY_REDIRECT,
            [(header::LOCATION, upstream.network.codex_token.as_str())],
            "fixture-renew",
        )
            .into_response(),
    );
    let Err(error) = store.authorize(PROVIDER, DIRECT).await else {
        panic!("redirect accepted");
    };
    assert_eq!(error.upstream, Some(307));
    assert!(!error.message.contains("fixture-renew"));
    assert_eq!(upstream.requests().len(), 2);
    assert_eq!(record(root.path()), expired);
}

#[tokio::test]
async fn read_only_and_early_renewal_never_send_the_saved_refresh_token() {
    let upstream = Upstream::start().await;
    let root = seed();
    let readonly =
        CredentialStore::with_network(root.path(), false, upstream.network.clone()).unwrap();
    assert!(readonly.authorize(PROVIDER, DIRECT).await.is_err());
    let writable =
        CredentialStore::with_network(root.path(), true, upstream.network.clone()).unwrap();
    let mut current = saved_plan(unix_seconds() + 30, "fixture-valid", "fixture-renew");
    current["earliestRefreshAt"] = json!(unix_seconds() + 1800);
    writable
        .save_secret(PROVIDER, &current.to_string(), None)
        .unwrap();
    assert_eq!(
        writable.authorize(PROVIDER, DIRECT).await.unwrap().kind,
        CredentialKind::ChatGptPlan
    );
    assert!(
        writable
            .authorize(PROVIDER, "https://proxy.example.test/v1")
            .await
            .is_err()
    );
    current["expiresAt"] = json!(0);
    writable
        .save_secret(PROVIDER, &current.to_string(), None)
        .unwrap();
    let Err(error) = writable.authorize(PROVIDER, DIRECT).await else {
        panic!("expired access used");
    };
    assert!(error.message.contains("renewal window"));
    assert!(upstream.requests().is_empty());
}

#[tokio::test]
async fn saves_default_model_and_effort_without_collapsing_provider_accounts() {
    let fixture = Upstream::start().await;
    let root = seed();
    let path = root.path().join("openbot-plan.json");
    let mut plan: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    plan["catalog"]["providers"].as_array_mut().unwrap().extend([
        json!({"id":"opencode","origin":"https://opencode.ai/inference/go","apiType":"chat-completions"}),
        json!({"id":"opencode-2","origin":"https://opencode.ai/zen/go/v1","apiType":"responses"}),
        json!({"id":"old","origin":"https://n5.example.test/v1","apiType":"chat-completions"}),
    ]);
    plan["catalog"]["models"].as_array_mut().unwrap().extend([
        json!({"id":"opencode:fixture-muse","slug":"fixture-muse","providerId":"opencode",
            "apiType":"chat-completions","reasoningLevels":["default","low","high"],"activeReasoning":"low"}),
        json!({"id":"opencode-2:fixture-muse","slug":"fixture-muse","providerId":"opencode-2",
            "apiType":"responses","reasoningLevels":["default","low","high"],"activeReasoning":"default"}),
        json!({"id":"opencode:fixture-embedding","slug":"fixture-embedding","providerId":"opencode","modelType":"embedding"}),
        json!({"id":"old:fixture-old","slug":"fixture-old","providerId":"old"}),
    ]);
    let pinned = json!({"conversation":{"kind":"exact","id":"fixture-existing-conversation"},"modelId":"openai:gpt-fixture"});
    plan["catalog"]["bindings"]
        .as_array_mut()
        .unwrap()
        .push(pinned.clone());
    plan["agents"]["fixture-existing-conversation"] =
        json!({"modelId":"gpt-fixture","providerId":"openai"});
    write(root.path(), "openbot-plan.json", &plan);
    let before = fs::read(&path).unwrap();
    let previous_keys = fs::read(root.path().join("secrets.json")).unwrap();
    let router = account_router(root.path(), &fixture);
    let activate =
        json!({"kind":"use-model","modelId":"opencode-2:fixture-muse","reasoning":"high"});
    let (status, _) = post_json(&router, "/api/save", activate.clone()).await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(fs::read(&path).unwrap(), before);

    fs::write(root.path().join("openbot-mode"), "custom\n").unwrap();
    let (status, result) = post_json(&router, "/api/save", activate.clone()).await;
    assert_eq!(status, StatusCode::OK, "{}", result);
    assert_eq!(result["snapshot"]["alignment"]["desired"], "custom");
    assert_eq!(result["activeModelId"], "opencode-2:fixture-muse");
    assert_eq!(result["wrapBytesChanged"], false);
    let public = result["models"]
        .as_array()
        .unwrap()
        .iter()
        .find(|model| model["id"] == "opencode-2:fixture-muse")
        .unwrap();
    assert_eq!(public["modalities"], json!(["text"]));
    assert_eq!(public["parameters"], json!([]));
    let saved = fs::read(&path).unwrap();
    let next: Value = serde_json::from_slice(&saved).unwrap();
    assert!(
        next["catalog"]["models"]
            .as_array()
            .unwrap()
            .iter()
            .all(|model| model.get("modalities").is_none())
    );
    assert_eq!(
        next["agents"]["*"],
        json!({"modelId":"fixture-muse","providerId":"opencode-2"})
    );
    assert_eq!(
        next["agents"]["fixture-existing-conversation"],
        plan["agents"]["fixture-existing-conversation"]
    );
    assert!(
        next["catalog"]["bindings"]
            .as_array()
            .unwrap()
            .contains(&pinned)
    );
    let state = State::new(root.path().into(), false).unwrap();
    let selected = state
        .resolve_routes(&["fixture-muse".into()], None, CredentialKind::ApiKey)
        .unwrap();
    let next_route = serde_json::to_value(&selected[0]).unwrap();
    assert_eq!(next_route["providerId"], "opencode-2");
    assert_eq!(next_route["protocol"], "responses");
    assert_eq!(next_route["reasoningEffort"], "high");
    assert_eq!(
        next_route["upstream"],
        "https://opencode.ai/inference/go/openai/v1/responses"
    );

    assert_eq!(
        post_json(&router, "/api/save", activate).await.0,
        StatusCode::OK
    );
    assert_eq!(fs::read(&path).unwrap(), saved);
    for invalid in [
        json!({"kind":"use-model","modelId":"opencode-2:fixture-muse","reasoning":"max"}),
        json!({"kind":"use-model","modelId":"opencode:fixture-embedding"}),
        json!({"kind":"use-model","modelId":"old:fixture-old"}),
    ] {
        assert_eq!(
            post_json(&router, "/api/save", invalid).await.0,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(fs::read(&path).unwrap(), saved);
    }
    let (status, result) = post_json(
        &router,
        "/api/save",
        json!({"kind":"use-model","modelId":"opencode:fixture-muse"}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(result["activeModelId"], "opencode:fixture-muse");
    let switched = state
        .resolve_routes(&["fixture-muse".into()], None, CredentialKind::ApiKey)
        .unwrap();
    let route = serde_json::to_value(&switched[0]).unwrap();
    assert_eq!(route["providerId"], "opencode");
    assert_eq!(route["reasoningEffort"], "low");
    assert_eq!(
        fs::read(root.path().join("secrets.json")).unwrap(),
        previous_keys
    );
}
