use std::{
    fs::{self, File, Metadata},
    io::{self, Write},
    path::{Component, Path, PathBuf},
};

use axum::{
    Json, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde_json::{Value, json};

use crate::{lease::Lease, server::read_bytes};

const SLUG: &str = "openbot-config";
const FILES: [(&str, &[u8]); 2] = [
    (
        "reference.md",
        include_bytes!("../../skills/openbot-config/reference.md"),
    ),
    (
        "SKILL.md",
        include_bytes!("../../skills/openbot-config/SKILL.md"),
    ),
];
const MAX_SKILL_FILE: u64 = 4 * 1024 * 1024;
const BLOCKED: &str =
    "Cannot write Grok Bot user skills. Check folder permissions on the workflows directory.";

#[derive(Clone)]
pub(crate) struct Skills {
    data: PathBuf,
    workflows: PathBuf,
    revision: String,
    writable: bool,
}

impl Skills {
    pub(crate) fn new(data: &Path, writable: bool, revision: String) -> io::Result<Self> {
        let from_env = |name| {
            std::env::var(name)
                .ok()
                .map(|value| value.trim().to_owned())
                .filter(|value| !value.is_empty())
                .map(PathBuf::from)
        };
        let workflows = from_env("OPENBOT_WORKFLOWS").unwrap_or_else(|| {
            from_env("OPENBOT_AGENT_DATA")
                .unwrap_or_else(|| PathBuf::from("/home/box/agent-data"))
                .join("workflows")
        });
        Ok(Self {
            data: data.into(),
            workflows: std::path::absolute(workflows)?,
            revision,
            writable,
        })
    }

    pub(crate) fn routes(self) -> Router {
        Router::new()
            .route("/api/grok-skills", get(status))
            .route("/api/grok-skills/install", post(install))
            .layer(DefaultBodyLimit::max(4096))
            .with_state(self)
    }

    fn report(&self) -> Value {
        let folder = self.workflows.join(SLUG);
        let available = checked_directory(&folder).and_then(|ancestor| {
            for (name, _) in FILES {
                regular_file(&folder.join(name))?;
            }
            Ok(ancestor)
        });
        let status = match available {
            Ok(_) if current(&folder) => "current",
            Ok(ancestor) if self.writable && can_write(&ancestor) => {
                if folder.join("SKILL.md").is_file() {
                    "stale"
                } else {
                    "missing"
                }
            }
            _ => "blocked",
        };
        json!({"dest":self.workflows,"source":"local","ref":self.revision,
               "skills":[{"slug":SLUG,"name":SLUG,"state":status,"destPath":folder}]})
    }

    fn apply(&self) -> io::Result<()> {
        if !self.writable {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        let _lease = Lease::acquire(&self.data.join(".openbot-grok-skill.lock"))?;
        let folder = self.workflows.join(SLUG);
        let ancestor = checked_directory(&folder)?;
        if !can_write(&ancestor) {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
        if ancestor != folder {
            fs::create_dir_all(&self.workflows)?;
            checked_directory(&self.workflows)?;
            let staged = tempfile::Builder::new()
                .prefix(".openbot-skill-")
                .tempdir_in(&self.workflows)?;
            for (name, bytes) in FILES {
                let mut file = File::create_new(staged.path().join(name))?;
                file.write_all(bytes)?;
                file.sync_all()?;
            }
            File::open(staged.path())?.sync_all()?;
            fs::set_permissions(staged.path(), self.workflows.metadata()?.permissions())?;
            install_folder(staged.path(), &folder)?;
            return File::open(&self.workflows)?.sync_all();
        }
        update_files(&folder)
    }
}

async fn status(State(skills): State<Skills>) -> Response {
    job(move || Json(skills.report()).into_response()).await
}

async fn install(State(skills): State<Skills>, body: Bytes) -> Response {
    job(move || {
        let slug = body_slug(&body);
        let result = match slug {
            Err(message) => Err((StatusCode::BAD_REQUEST, message)),
            Ok(Some(value)) if value != SLUG => Err((StatusCode::BAD_REQUEST, "unknown skill")),
            Ok(_) => skills.apply().map_err(|error| match error.kind() {
                io::ErrorKind::PermissionDenied => (StatusCode::FORBIDDEN, BLOCKED),
                io::ErrorKind::WouldBlock | io::ErrorKind::AlreadyExists => (
                    StatusCode::CONFLICT,
                    "The Grok Bot skill changed during installation. Try again.",
                ),
                _ => (
                    StatusCode::SERVICE_UNAVAILABLE,
                    "Could not save the Grok Bot skill.",
                ),
            }),
        };
        let mut report = skills.report();
        report["ok"] = json!(result.is_ok());
        if let Err((status, error)) = result {
            report["error"] = json!(error);
            return (status, Json(report)).into_response();
        }
        Json(report).into_response()
    })
    .await
}

async fn job(work: impl FnOnce() -> Response + Send + 'static) -> Response {
    tokio::task::spawn_blocking(work).await.unwrap_or_else(|_| {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"Could not read the Grok Bot skill."})),
        )
            .into_response()
    })
}

fn body_slug(bytes: &[u8]) -> Result<Option<String>, &'static str> {
    if bytes.iter().all(u8::is_ascii_whitespace) {
        return Ok(None);
    }
    let body: Value = serde_json::from_slice(bytes).map_err(|_| "invalid json")?;
    match body {
        Value::Null => Ok(None),
        Value::Object(body) => match body.get("slug") {
            None | Some(Value::Null) => Ok(None),
            Some(Value::String(slug)) if slug.is_empty() => Ok(None),
            Some(Value::String(slug))
                if slug.len() <= 64
                    && slug
                        .bytes()
                        .next()
                        .is_some_and(|a| a.is_ascii_alphanumeric())
                    && slug
                        .bytes()
                        .all(|a| a.is_ascii_alphanumeric() || matches!(a, b'.' | b'_' | b'-')) =>
            {
                Ok(Some(slug.clone()))
            }
            _ => Err("invalid slug"),
        },
        _ => Err("invalid json"),
    }
}

fn checked_directory(path: &Path) -> io::Result<PathBuf> {
    if !path.is_absolute()
        || path.components().any(|part| match part {
            Component::ParentDir => true,
            Component::Normal(name) => name == "plugins" || name == "managed-skills",
            _ => false,
        })
    {
        return Err(io::ErrorKind::PermissionDenied.into());
    }
    let mut last = None;
    for directory in path.ancestors() {
        match directory.symlink_metadata() {
            Ok(info) if info.is_dir() => {
                last.get_or_insert(directory);
            }
            Ok(_) => return Err(io::ErrorKind::PermissionDenied.into()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    last.map(Path::to_path_buf)
        .ok_or(io::ErrorKind::NotFound.into())
}

#[cfg(target_os = "linux")]
fn can_write(folder: &Path) -> bool {
    use rustix::fs::{Access, AtFlags, CWD, accessat};
    accessat(
        CWD,
        folder,
        Access::WRITE_OK | Access::EXEC_OK,
        AtFlags::EACCESS,
    )
    .is_ok()
}

#[cfg(not(target_os = "linux"))]
fn can_write(folder: &Path) -> bool {
    folder
        .metadata()
        .is_ok_and(|stat| !stat.permissions().readonly())
}

fn regular_file(path: &Path) -> io::Result<Option<Metadata>> {
    match path.symlink_metadata() {
        Ok(info) if info.is_file() => Ok(Some(info)),
        Ok(_) => Err(io::ErrorKind::PermissionDenied.into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn current(folder: &Path) -> bool {
    FILES.iter().all(|(name, bytes)| {
        read_bytes(&folder.join(name), bytes.len() as u64)
            .is_ok_and(|body| body.as_deref() == Some(bytes))
    })
}

#[cfg(target_os = "linux")]
fn install_folder(staged: &Path, folder: &Path) -> io::Result<()> {
    use rustix::fs::{CWD, RenameFlags, renameat_with};
    renameat_with(CWD, staged, CWD, folder, RenameFlags::NOREPLACE).map_err(Into::into)
}

#[cfg(not(target_os = "linux"))]
fn install_folder(staged: &Path, folder: &Path) -> io::Result<()> {
    if folder.symlink_metadata().is_ok() {
        return Err(io::ErrorKind::AlreadyExists.into());
    }
    fs::rename(staged, folder)
}

struct Edit {
    path: PathBuf,
    original: Option<Vec<u8>>,
    next: tempfile::NamedTempFile,
    undo: Option<tempfile::NamedTempFile>,
}

fn update_files(folder: &Path) -> io::Result<()> {
    let mut edits = Vec::new();
    for (name, bytes) in FILES {
        let path = folder.join(name);
        let stat = regular_file(&path)?;
        let original = read_bytes(&path, MAX_SKILL_FILE)?;
        if original.as_deref() == Some(bytes) {
            continue;
        }
        let stage = |bytes: &[u8]| -> io::Result<_> {
            let mut temp = tempfile::NamedTempFile::new_in(folder)?;
            if let Some(stat) = &stat {
                temp.as_file().set_permissions(stat.permissions())?;
            }
            temp.write_all(bytes)?;
            temp.as_file().sync_all()?;
            Ok(temp)
        };
        let next = stage(bytes)?;
        let undo = original.as_deref().map(stage).transpose()?;
        edits.push(Edit {
            path,
            original,
            next,
            undo,
        });
    }
    let mut applied: Vec<(PathBuf, Option<tempfile::NamedTempFile>)> = Vec::new();
    for Edit {
        path,
        original,
        next,
        undo,
    } in edits
    {
        let result = (|| -> io::Result<()> {
            checked_directory(folder)?;
            regular_file(&path)?;
            if read_bytes(&path, MAX_SKILL_FILE)? != original {
                return Err(io::ErrorKind::WouldBlock.into());
            }
            next.persist(&path)?;
            Ok(())
        })();
        if let Err(error) = result {
            for (path, undo) in applied.into_iter().rev() {
                if let Some(file) = undo {
                    file.persist(&path)?;
                } else {
                    fs::remove_file(path)?;
                }
            }
            return Err(error);
        }
        applied.push((path, undo));
    }
    File::open(folder)?.sync_all()
}

#[cfg(test)]
mod tests;
