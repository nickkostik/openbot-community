use std::{
    io::Write,
    process::{Command, Stdio},
    sync::atomic::{AtomicUsize, Ordering},
};

use axum::{Json, Router, body::Body, http::Request, routing::get};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use serde_json::json;
use tower::ServiceExt;

use super::*;

const LOCAL: &str = "127.0.0.1:41729";
const PUBLIC: &str = "panel.example.net";
const ALIAS: &str = "panel.example.org";
const ISSUER: &str = "https://openbot-fixture.cloudflareaccess.com";
const AUD: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ALIAS_AUD: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

fn config() -> Value {
    json!({"teamDomain":ISSUER,"origins":[
        {"origin":format!("https://{PUBLIC}"),"audience":AUD},
        {"origin":format!("https://{ALIAS}"),"audience":ALIAS_AUD},
    ]})
}

#[test]
fn accepts_exact_configured_https_hosts_and_rejects_ambiguous_configuration() {
    let parsed = Config::parse(&config().to_string().into_bytes()).unwrap();
    assert_eq!(parsed.primary, format!("https://{PUBLIC}"));
    assert_eq!(parsed.sites[ALIAS].audience, ALIAS_AUD);
    for replacement in [
        "https://alias.example.org/other",
        "https://alias.example.org:443",
        "https://alias.example.org@elsewhere.example.org",
        "https://127.0.0.1",
        "http://alias.example.org",
        "https://panel.example.net",
    ] {
        let mut changed = config();
        changed["origins"][1]["origin"] = json!(replacement);
        assert!(Config::parse(&changed.to_string().into_bytes()).is_err());
    }
    let mut changed = config();
    changed["teamDomain"] = json!("https://cloudflareaccess.com.example.org");
    assert!(Config::parse(&changed.to_string().into_bytes()).is_err());
}

struct FixtureKey {
    directory: tempfile::TempDir,
    kid: String,
    public: Value,
}

impl FixtureKey {
    fn new(kid: &str) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let output = Command::new("openssl")
            .current_dir(directory.path())
            .args([
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
                "-out",
                "key.pem",
            ])
            .output()
            .unwrap();
        assert!(output.status.success());
        let modulus = Command::new("openssl")
            .current_dir(directory.path())
            .args(["rsa", "-in", "key.pem", "-noout", "-modulus"])
            .output()
            .unwrap();
        assert!(modulus.status.success());
        let hex = String::from_utf8(modulus.stdout).unwrap();
        let digits = hex.trim().strip_prefix("Modulus=").unwrap();
        let bytes = (0..digits.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&digits[i..i + 2], 16).unwrap())
            .collect::<Vec<_>>();
        let public = json!({"kid":kid,"kty":"RSA","alg":"RS256","use":"sig",
                           "n":URL_SAFE_NO_PAD.encode(bytes),"e":"AQAB"});
        Self {
            directory,
            kid: kid.into(),
            public,
        }
    }

    fn sign(&self, claims: &Value) -> String {
        let header = json!({"kid":self.kid,"alg":"RS256","typ":"JWT"});
        let message = format!(
            "{}.{}",
            URL_SAFE_NO_PAD.encode(header.to_string()),
            URL_SAFE_NO_PAD.encode(claims.to_string())
        );
        let mut signer = Command::new("openssl")
            .current_dir(self.directory.path())
            .args(["dgst", "-sha256", "-sign", "key.pem"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        signer
            .stdin
            .take()
            .unwrap()
            .write_all(message.as_bytes())
            .unwrap();
        let output = signer.wait_with_output().unwrap();
        assert!(output.status.success());
        format!("{message}.{}", URL_SAFE_NO_PAD.encode(output.stdout))
    }
}

async fn call(app: &Router, method: Method, path: &str, headers: &[(&str, &str)]) -> StatusCode {
    let mut request = Request::builder()
        .method(method)
        .uri(path)
        .header(header::CONTENT_TYPE, "application/json");
    for (key, value) in headers {
        request = request.header(*key, *value);
    }
    let reply = app
        .clone()
        .oneshot(request.body(Body::from(r#"{"paused":true}"#)).unwrap())
        .await
        .unwrap();
    assert_eq!(reply.headers()[header::CACHE_CONTROL], "no-store");
    reply.status()
}

#[tokio::test]
async fn public_panel_verifies_edge_identity_and_keeps_inference_and_oauth_local() {
    let key = FixtureKey::new("fixture-rsa-one");
    let rotation = FixtureKey::new("fixture-rsa-two");
    let published = Arc::new(std::sync::Mutex::new(json!({"keys":[key.public]})));
    let calls = Arc::new(AtomicUsize::new(0));
    let certificates = Router::new().route(
        "/certs",
        get({
            let calls = calls.clone();
            let published = published.clone();
            move || {
                let calls = calls.clone();
                let published = published.clone();
                async move {
                    calls.fetch_add(1, Ordering::Relaxed);
                    Json(published.lock().unwrap().clone())
                }
            }
        }),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let certificate_server =
        tokio::spawn(async move { axum::serve(listener, certificates).await.unwrap() });
    let mut access = Access::new(Config::parse(config().to_string().as_bytes()).unwrap()).unwrap();
    access.url = Url::parse(&format!("http://{addr}/certs")).unwrap();
    let access = Arc::new(access);
    let policy = Policy {
        authority: LOCAL.into(),
        access: Some(access.clone()),
    };
    let dir = tempfile::tempdir().unwrap();
    let state = crate::server::State::new(dir.path().to_path_buf(), true).unwrap();
    let app = crate::server::router(state, policy, []);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let claims = json!({"iss":ISSUER,"type":"app","aud":[AUD],"exp":now+900,"iat":now});
    let token = key.sign(&claims);
    let mut alias_claims = claims.clone();
    alias_claims["aud"] = json!([ALIAS_AUD]);
    let alias_token = key.sign(&alias_claims);
    let mut expired = claims.clone();
    expired["exp"] = json!(now - 1);
    let expired = key.sign(&expired);
    let mut wrong_type = claims.clone();
    wrong_type["type"] = json!("org");
    let wrong_type = key.sign(&wrong_type);
    let (message, signature) = token.rsplit_once('.').unwrap();
    let mut forged = URL_SAFE_NO_PAD.decode(signature).unwrap();
    forged[0] ^= 1;
    let forged = format!("{message}.{}", URL_SAFE_NO_PAD.encode(forged));
    let cookie = format!("CF_Authorization={token}");
    for headers in [
        vec![("host", PUBLIC)],
        vec![("host", PUBLIC), ("cookie", &cookie)],
        vec![("host", ALIAS), (ASSERTION, &token)],
        vec![("host", "elsewhere.example.org"), (ASSERTION, &token)],
        vec![("host", PUBLIC), (ASSERTION, &expired)],
        vec![("host", PUBLIC), (ASSERTION, &wrong_type)],
        vec![("host", LOCAL), ("x-forwarded-host", PUBLIC)],
        vec![("host", LOCAL), ("cf-ray", "edge-fixture")],
        vec![("host", PUBLIC), (ASSERTION, &token), (ASSERTION, &token)],
    ] {
        assert_eq!(
            call(&app, Method::GET, "/api/pause", &headers).await,
            StatusCode::FORBIDDEN
        );
    }
    assert_eq!(calls.load(Ordering::Relaxed), 0);
    assert_eq!(
        call(&app, Method::GET, "/api/pause", &[("host", LOCAL)]).await,
        StatusCode::OK
    );
    let authorized = [("host", PUBLIC), (ASSERTION, token.as_str())];
    assert_eq!(
        call(&app, Method::GET, "/api/pause", &authorized).await,
        StatusCode::OK
    );
    assert_eq!(
        call(
            &app,
            Method::GET,
            "/api/pause",
            &[("host", ALIAS), (ASSERTION, &alias_token)]
        )
        .await,
        StatusCode::OK
    );
    assert_eq!(
        call(
            &app,
            Method::GET,
            "/api/pause",
            &[("host", PUBLIC), (ASSERTION, &forged)]
        )
        .await,
        StatusCode::FORBIDDEN
    );
    assert_eq!(calls.load(Ordering::Relaxed), 1);
    assert_eq!(
        call(&app, Method::PUT, "/api/pause", &authorized).await,
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        call(
            &app,
            Method::PUT,
            "/api/pause",
            &[
                ("host", PUBLIC),
                (ASSERTION, &token),
                ("origin", "https://elsewhere.example.org")
            ]
        )
        .await,
        StatusCode::FORBIDDEN
    );
    assert!(!dir.path().join("openbot-pause.json").exists());
    let public_origin = format!("https://{PUBLIC}");
    let same_origin = [
        ("host", PUBLIC),
        (ASSERTION, &token),
        ("origin", &public_origin),
    ];
    assert_eq!(
        call(&app, Method::PUT, "/api/pause", &same_origin).await,
        StatusCode::OK
    );
    assert!(dir.path().join("openbot-pause.json").is_file());
    for path in [
        "/v1/chat/completions",
        "/api/oauth/chatgpt/start",
        "/auth/callback",
    ] {
        assert_eq!(
            call(&app, Method::POST, path, &same_origin).await,
            StatusCode::FORBIDDEN
        );
    }
    assert_eq!(calls.load(Ordering::Relaxed), 1);
    *published.lock().unwrap() = json!({"keys":[rotation.public]});
    access.keys.lock().await.attempted = None;
    let rotated = rotation.sign(&claims);
    assert_eq!(
        call(
            &app,
            Method::GET,
            "/api/pause",
            &[("host", PUBLIC), (ASSERTION, &rotated)]
        )
        .await,
        StatusCode::OK
    );
    assert_eq!(calls.load(Ordering::Relaxed), 2);
    assert_eq!(
        call(&app, Method::GET, "/api/pause", &authorized).await,
        StatusCode::FORBIDDEN
    );
    certificate_server.abort();
    let _ = certificate_server.await;
    {
        let mut keys = access.keys.lock().await;
        keys.updated = Some(Instant::now() - KEY_TTL);
        keys.attempted = None;
    }
    assert_eq!(
        call(
            &app,
            Method::GET,
            "/api/pause",
            &[("host", PUBLIC), (ASSERTION, &rotated)]
        )
        .await,
        StatusCode::SERVICE_UNAVAILABLE
    );
    assert_eq!(
        call(&app, Method::GET, "/api/pause", &[("host", LOCAL)]).await,
        StatusCode::OK
    );
}
