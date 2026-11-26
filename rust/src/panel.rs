mod roster;

use std::{
    fs::File,
    io::{self, Read},
    path::{Path, PathBuf},
};

use axum::{
    Json, Router,
    body::{Body, Bytes},
    extract::State,
    http::{HeaderValue, header},
    response::Response,
    routing::get,
};
use serde_json::{Value, json};

use crate::{
    auth::{AuthError, CredentialStore},
    host::{self, Host},
    logs,
};

const MAX_ASSET_BYTES: u64 = 8 * 1024 * 1024;
const ASSETS: [(&str, &str, &str); 4] = [
    ("/", "index.html", "text/html; charset=utf-8"),
    ("/app.js", "app.js", "text/javascript; charset=utf-8"),
    ("/styles.css", "styles.css", "text/css; charset=utf-8"),
    ("/favicon.svg", "favicon.svg", "image/svg+xml"),
];

#[cfg(target_os = "linux")]
pub(crate) fn release_assets(root: &Path) -> io::Result<Vec<(&'static str, Bytes)>> {
    ASSETS
        .into_iter()
        .map(|(_, name, _)| Ok((name, read_asset(root, name)?)))
        .collect()
}

#[derive(Clone)]
struct PanelReadback {
    credentials: CredentialStore,
    status: ListenerStatus,
    agent_data: PathBuf,
    host: Option<Host>,
}

#[derive(Clone, Copy)]
pub struct ListenerStatus {
    pub port: u16,
    pub serves_ui: bool,
    pub serves_hop: bool,
}

pub fn routes(
    assets: PathBuf,
    credentials: CredentialStore,
    status: ListenerStatus,
    host: Option<Host>,
) -> io::Result<Router> {
    let asset_root = assets.canonicalize()?;
    let mut router = Router::new()
        .route("/api/state", get(state))
        .route("/api/snapshot", get(state))
        .route("/api/bots", get(bots))
        .with_state(PanelReadback {
            credentials,
            status,
            host,
            agent_data: std::env::var_os("OPENBOT_AGENT_DATA")
                .filter(|path| !path.is_empty())
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/home/box/agent-data")),
        });
    for (route, name, mime) in ASSETS {
        let bytes = read_asset(&asset_root, name)?;
        router = router.route(
            route,
            get(move || async move {
                let mut response = Response::new(Body::from(bytes));
                response
                    .headers_mut()
                    .insert(header::CONTENT_TYPE, HeaderValue::from_static(mime));
                response.headers_mut().insert(
                    header::X_CONTENT_TYPE_OPTIONS,
                    HeaderValue::from_static("nosniff"),
                );
                response
            }),
        );
    }
    Ok(router)
}

async fn state(State(panel): State<PanelReadback>) -> Result<Json<Value>, AuthError> {
    crate::auth::blocking(move || {
        let mut view = panel.credentials.public_view()?;
        panel
            .status
            .add_to(panel.credentials.root(), &mut view, panel.host.as_ref());
        Ok(Json(view))
    })
    .await
}

async fn bots(State(panel): State<PanelReadback>) -> Result<Json<Vec<roster::Bot>>, AuthError> {
    crate::auth::blocking(move || {
        Ok(Json(roster::read(
            panel.credentials.root(),
            &panel.agent_data,
        )))
    })
    .await
}

impl ListenerStatus {
    pub fn add_to(&self, root: &Path, state: &mut Value, host: Option<&Host>) {
        let mode = crate::server::read_mode(root).unwrap_or("official");
        let listen = |served| {
            if served {
                json!({"kind":"ours", "pid":std::process::id(), "host":"127.0.0.1", "port":self.port})
            } else {
                json!({"kind":"absent"})
            }
        };
        let (wrap, process) = host
            .map(Host::wrap_and_process)
            .unwrap_or_else(|| (json!({"kind":"stock-unmarked"}), json!({"kind":"absent"})));
        let stage = if mode == "custom" && wrap["kind"] == "stock-unmarked" {
            "needs-reinstall"
        } else {
            "ok"
        };
        let alignment = json!({"kind":stage,"desired":mode,"wrap":wrap["kind"]});
        let tunnel = host::tunnel(
            root,
            self.port,
            std::env::var("OPENBOT_PUBLIC_URL").ok().as_deref(),
        );
        state["snapshot"] = json!({
            "wrap":wrap,
            "host":process,
            "tunnel":tunnel,
            "uiListen":listen(self.serves_ui), "hopListen":listen(self.serves_hop),
            "alignment":alignment
        });
        state["logSettings"] = json!(logs::Settings::load(root));
    }
}

fn read_asset(root: &Path, name: &str) -> io::Result<Bytes> {
    let path = root.join(name);
    if !path.symlink_metadata()?.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "OpenBot panel assets must be regular files",
        ));
    }
    let mut data = Vec::new();
    File::open(path)?
        .take(MAX_ASSET_BYTES + 1)
        .read_to_end(&mut data)?;
    if data.len() as u64 > MAX_ASSET_BYTES {
        return Err(io::ErrorKind::FileTooLarge.into());
    }
    Ok(data.into())
}

#[cfg(test)]
mod tests;
