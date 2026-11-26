use std::{fs, time::Duration};

use axum::{
    body::{Body, to_bytes},
    http::Request,
};
use serde_json::json;
use tower::ServiceExt;

use super::*;

async fn request(router: &Router, path: &str) -> (StatusCode, Value) {
    let response = router
        .clone()
        .oneshot(Request::get(path).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), 8 * 1024 * 1024)
        .await
        .unwrap();
    (status, serde_json::from_slice(&body).unwrap())
}

#[tokio::test]
async fn legacy_rows_keep_their_log_details_but_never_double_count_usage() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join(REQUESTS), [
        json!({"id":"provider","startedAt":"2026-11-24T22:00:00.100Z","channel":"hop","ok":true,"status":200,
            "model":"fixture","providerId":"direct","purpose":"chat","requestId":"correlation",
            "promptTokens":100,"completionTokens":10,"cachedTokens":92,"totalTokens":110,
            "botId":"bot-one","chatType":"dm","hasRequest":true,"hasResponse":false}),
        json!({"id":"harness","startedAt":"2026-11-24T22:00:00.200Z","channel":"custom-host","ok":true,"status":200,
            "model":"fixture","providerId":"direct","requestId":"correlation","botId":"bot-one","botName":"Example Bot",
            "promptTokens":100,"completionTokens":10,"cachedTokens":92,"totalTokens":110}),
        json!({"id":"stock","startedAt":"2026-11-24T22:01:00.100Z","channel":"official","ok":false,"status":503,
            "model":"stock","providerId":"official","botId":"bot-two","chatType":"routine",
            "promptTokens":50,"totalTokens":50,"hasResponse":true}),
    ].into_iter().map(|row| row.to_string()).collect::<Vec<_>>().join("\n") + "\ninvalid-line\n").unwrap();
    fs::create_dir(dir.path().join(BODIES)).unwrap();
    fs::write(
        dir.path().join(BODIES).join("provider.json"),
        json!({"request":{"example":"old request"},"requestFull":"full redacted text"}).to_string(),
    )
    .unwrap();
    fs::write(dir.path().join(EVENTS), json!({"id":"evt-1","type":"test.event","severity":"WARN","at":"2026-11-24T22:01:02.000Z","requestId":"correlation"}).to_string()).unwrap();
    let app = Logs::new(dir.path(), false).routes();
    let (_, page) = request(
        &app,
        "/api/logs?channel=custom&botId=bot-one&page=2&pageSize=1",
    )
    .await;
    assert_eq!(page["total"], 2);
    assert_eq!(page["items"][0]["id"], "provider");
    let (_, find) = request(
        &app,
        "/api/logs?q=CORRELATION&from=2026-11-24T16:00:00-06:00",
    )
    .await;
    assert_eq!(find["items"][0]["id"], "harness");
    assert_eq!(find["total"], 2);
    let (_, detail) = request(&app, "/api/logs/provider").await;
    assert_eq!(detail["request"]["example"], "old request");
    assert_eq!(detail["requestFull"], "full redacted text");
    let (_, missing) = request(&app, "/api/logs/stock").await;
    assert_eq!(missing["bodyMissing"], true);
    assert_eq!(
        request(&app, "/api/logs/%2Fetc%2Fpasswd").await.0,
        StatusCode::NOT_FOUND
    );

    let (_, facets) = request(&app, "/api/logs/facets").await;
    assert_eq!(facets["bots"][1]["botName"], "Example Bot");
    assert_eq!(
        facets["model"]["values"][0],
        json!({"value":"fixture","count":2})
    );
    let (_, stats) = request(&app, "/api/logs/stats").await;
    assert_eq!(stats["totalTokens"], 160);
    assert_eq!(stats["records"], 3);
    assert_eq!(stats["errors"], 1);
    assert_eq!(stats["excludedHarnessRecords"], 1);
    assert_eq!(stats["bodyFiles"], 1);
    let (_, usage) = request(&app, "/api/logs/usage").await;
    assert_eq!(usage["summary"]["totalTokens"], 160);
    assert_eq!(usage["summary"]["cacheKnownInputTokens"], 100);
    assert_eq!(usage["summary"]["cacheUnknownInputTokens"], 50);
    assert_eq!(usage["summary"]["cacheHitRate"], 0.92);
    assert_eq!(usage["summary"]["requests"], 2);
    let (_, filtered) = request(
        &app,
        "/api/logs/usage?provider=direct&model=direct%3Afixture",
    )
    .await;
    assert_eq!(filtered["summary"]["requests"], 1);
    let (_, events) = request(&app, "/api/logs/events?severity=warn&requestId=correlation").await;
    assert_eq!(events["items"][0]["id"], "evt-1");
}

#[tokio::test]
async fn request_writes_wait_for_legacy_prune_and_preserve_concurrent_rows() {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    fs::write(
        root.join("openbot-logs.json"),
        json!({"loggingEnabled":true,"logBodiesOnError":false}).to_string(),
    )
    .unwrap();
    let guard = root.join("openbot-requests.lock");
    fs::write(&guard, "legacy-process\n").unwrap();
    fs::write(root.join(REQUESTS), "partial row without line ending").unwrap();
    let logs = Logs::new(root, true);
    for _ in 0..16 {
        logs.begin().await.unwrap().finish(StatusCode::OK, None);
    }
    tokio::time::sleep(Duration::from_millis(45)).await;
    assert_eq!(
        fs::read_to_string(root.join(REQUESTS)).unwrap(),
        "partial row without line ending"
    );
    fs::remove_file(guard).unwrap();
    tokio::time::timeout(Duration::from_secs(3), logs.wait_for_writes())
        .await
        .unwrap();
    let result = read::Rows::load(root, REQUESTS).unwrap();
    assert_eq!(result.values.len(), 16);
    let distinct: std::collections::BTreeSet<_> = result
        .values
        .iter()
        .map(|row| row["id"].as_str().unwrap())
        .collect();
    assert_eq!(distinct.len(), 16);

    let next = logs.begin().await.unwrap();
    fs::write(root.join("openbot-logs.json"), "{}").unwrap();
    next.finish(StatusCode::OK, None);
    logs.wait_for_writes().await;
    assert_eq!(read::Rows::load(root, REQUESTS).unwrap().values.len(), 16);
    assert!(Logs::new(root, false).begin().await.is_none());
}

#[cfg(unix)]
#[tokio::test]
async fn ignores_symlinked_body_records() {
    use std::os::unix::fs::symlink;

    let dir = tempfile::tempdir().unwrap();
    let root = dir.path();
    let vault = root.join("test-outside-body-storage.json");
    fs::write(&vault, r#"{"request":"synthetic secret"}"#).unwrap();
    fs::write(root.join(REQUESTS), r#"{"id":"row","hasRequest":true}"#).unwrap();
    fs::create_dir(root.join(BODIES)).unwrap();
    symlink(&vault, root.join(BODIES).join("row.json")).unwrap();
    let app = Logs::new(root, false).routes();
    let (_, row) = request(&app, "/api/logs/row").await;
    assert_eq!(row["bodyMissing"], true);
    assert!(row.get("request").is_none());
}
