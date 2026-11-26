use axum::{
    body::{Body, to_bytes},
    http::Request,
};
use tower::ServiceExt;

use super::*;

const HOST: &str = "127.0.0.1:47829";

fn fixture(root: &Path, writable: bool) -> (Router, PathBuf) {
    let state = crate::server::State::new(root.to_owned(), false).unwrap();
    let workflows = root.join("agent-data/workflows");
    let service = Skills {
        data: root.into(),
        workflows: workflows.clone(),
        revision: "test-commit".into(),
        writable,
    };
    (
        crate::server::router(state, HOST.into(), [service.routes()]),
        workflows,
    )
}

async fn request(app: &Router, body: Option<&str>) -> (StatusCode, Value) {
    let (uri, method) = if body.is_some() {
        ("/api/grok-skills/install", "POST")
    } else {
        ("/api/grok-skills", "GET")
    };
    let reply = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(uri)
                .method(method)
                .header("host", HOST)
                .body(Body::from(body.unwrap_or_default().to_owned()))
                .unwrap(),
        )
        .await
        .unwrap();
    let result = reply.status();
    let bytes = to_bytes(reply.into_body(), 8192).await.unwrap();
    (result, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn skill_is_installed_and_updated_from_the_build_preserving_user_files() {
    let root = tempfile::tempdir().unwrap();
    let (app, workflows) = fixture(root.path(), true);
    let (status, report) = request(&app, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(report["source"], "local");
    assert_eq!(report["ref"], "test-commit");
    assert_eq!(report["skills"][0]["state"], "missing");
    assert!(!workflows.exists());
    for bad in ["{", r#"{"slug":"../plugins"}"#, r#"{"slug":"unknown"}"#] {
        assert_eq!(request(&app, Some(bad)).await.0, StatusCode::BAD_REQUEST);
        assert!(!workflows.exists());
    }
    let (status, updated) = request(&app, Some(r#"{"slug":"openbot-config"}"#)).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(updated["ok"], true);
    assert_eq!(updated["skills"][0]["state"], "current");
    let folder = workflows.join(SLUG);
    for (name, bytes) in FILES {
        assert_eq!(fs::read(folder.join(name)).unwrap(), bytes);
    }
    fs::write(folder.join("personal.md"), "saved work").unwrap();
    fs::write(folder.join("reference.md"), "old revision").unwrap();
    assert_eq!(request(&app, None).await.1["skills"][0]["state"], "stale");
    let (status, updated) = request(&app, Some("{}")).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(updated["skills"][0]["state"], "current");
    assert_eq!(fs::read(folder.join("personal.md")).unwrap(), b"saved work");
    assert_eq!(fs::read(folder.join("reference.md")).unwrap(), FILES[0].1);
    assert_eq!(
        request(&app, Some("")).await.1["skills"][0]["state"],
        "current"
    );
}

#[cfg(unix)]
#[tokio::test]
async fn install_refuses_protected_or_linked_destinations_and_readonly_state() {
    let root = tempfile::tempdir().unwrap();
    let (app, workflows) = fixture(root.path(), false);
    assert_eq!(request(&app, Some("{}")).await.0, StatusCode::FORBIDDEN);
    assert!(!workflows.exists());
    let (app, workflows) = fixture(root.path(), true);
    fs::create_dir_all(workflows.parent().unwrap()).unwrap();
    let managed = root.path().join("managed-skills");
    fs::create_dir(&managed).unwrap();
    std::os::unix::fs::symlink(&managed, &workflows).unwrap();
    assert_eq!(request(&app, Some("{}")).await.0, StatusCode::FORBIDDEN);
    assert!(!managed.join(SLUG).exists());
    fs::remove_file(&workflows).unwrap();
    let folder = workflows.join(SLUG);
    fs::create_dir_all(&folder).unwrap();
    let external = managed.join("SKILL.md");
    fs::write(&external, "protected").unwrap();
    std::os::unix::fs::symlink(&external, folder.join("SKILL.md")).unwrap();
    assert_eq!(request(&app, Some("{}")).await.0, StatusCode::FORBIDDEN);
    assert_eq!(fs::read_to_string(external).unwrap(), "protected");
    assert!(!folder.join("reference.md").exists());
}
