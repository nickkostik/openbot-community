mod process;
mod source;

use std::{
    fs::{self, File, Metadata},
    io::{self, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use axum::{body::Body, response::Response};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::{
    lease::Lease,
    logs,
    server::{read_bytes, read_json, read_mode},
};
use source::Shape;

const HOST_LIMIT: u64 = 64 * 1024 * 1024;
const RELOAD: &str = ".openbot-rust-host-reload.json";
const MODE: &str = "openbot-mode";
const GRACE_MS: u64 = 120_000;
const STOP_QUIET_MS: u64 = 90_000;
const BUSY_QUIET_MS: u64 = 300_000;

#[derive(Clone)]
pub(crate) struct Host(Arc<HostInner>);

struct HostInner {
    root: PathBuf,
    path: PathBuf,
    managed: Option<Managed>,
    observed: Mutex<Option<(Stamp, HostFile)>>,
    active: Mutex<Activity>,
}

struct Managed {
    _lease: Lease,
    adapter: PathBuf,
    node: PathBuf,
    writes: Mutex<()>,
}

#[derive(Default)]
struct Activity {
    count: usize,
    updated_ms: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct Stamp {
    len: u64,
    modified: Option<SystemTime>,
    #[cfg(unix)]
    inode: (u64, u64, i64, i64),
}

#[derive(Clone)]
struct HostFile {
    digest: String,
    shape: Shape,
    first_lines: String,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Reload {
    armed_at_ms: u64,
    expected_digest: String,
    targets: Vec<process::Target>,
}

pub(crate) struct Transition {
    pub(crate) changed: bool,
    pub(crate) deferred: bool,
}

impl Host {
    pub(crate) fn new(
        root: &Path,
        path: PathBuf,
        adapter: Option<PathBuf>,
        node: PathBuf,
    ) -> io::Result<Self> {
        if !path.is_absolute() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "the host entrypoint must be absolute",
            ));
        }
        let managed = adapter
            .map(|adapter| -> io::Result<_> {
                if !cfg!(target_os = "linux") {
                    return Err(io::ErrorKind::Unsupported.into());
                }
                let adapter = adapter.canonicalize()?;
                if adapter.file_name().is_none_or(|name| name != "runtime.cjs")
                    || !adapter.is_file()
                {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "select the installed runtime.cjs",
                    ));
                }
                let _lease = Lease::acquire(&root.join(".openbot-rust-host.lock"))?;
                Ok(Managed {
                    _lease,
                    adapter,
                    node,
                    writes: Mutex::new(()),
                })
            })
            .transpose()?;
        Ok(Self(Arc::new(HostInner {
            root: root.into(),
            path,
            managed,
            observed: Mutex::default(),
            active: Mutex::default(),
        })))
    }

    pub(crate) fn managed(&self) -> bool {
        self.0.managed.is_some()
    }

    pub(crate) fn wrap_and_process(&self) -> (Value, Value) {
        let wrap = self.observe().ok().flatten().map_or_else(
            || json!({"kind":"stock-unmarked"}),
            |file| file.shape.json(),
        );
        let process = if let Some(target) = process::find(&self.0.path).first() {
            json!({"kind":"running-owned","pid":target.pid,"runtime":"node"})
        } else if let Some(pid) = process::desktop() {
            json!({"kind":"running-owned","pid":pid,"runtime":"desktop"})
        } else {
            json!({"kind":"absent"})
        };
        (wrap, process)
    }

    fn observe(&self) -> io::Result<Option<HostFile>> {
        let mut cache = self
            .0
            .observed
            .lock()
            .map_err(|_| io::Error::other("host observation lock poisoned"))?;
        let Some(stamp) = stamp_at(&self.0.path)? else {
            *cache = None;
            return Ok(None);
        };
        if let Some((previous, file)) = &*cache
            && previous == &stamp
        {
            return Ok(Some(file.clone()));
        }
        let source = self.read_source()?;
        let file = HostFile {
            digest: digest(source.as_bytes()),
            shape: source::inspect(&source),
            first_lines: source.chars().take(8192).collect(),
        };
        *cache = Some((stamp, file.clone()));
        Ok(Some(file))
    }

    fn read_source(&self) -> io::Result<String> {
        read_bytes(&self.0.path, HOST_LIMIT)?
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::NotFound,
                    "the configured host entrypoint is missing",
                )
            })
            .and_then(|bytes| String::from_utf8(bytes).map_err(io::Error::other))
    }

    pub(crate) fn reconcile(
        &self,
        mode: &str,
        commit: impl FnOnce() -> io::Result<()>,
    ) -> io::Result<Transition> {
        let managed = self.0.managed.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::Unsupported,
                "the host is configured for observation only",
            )
        })?;
        let _writes = managed
            .writes
            .lock()
            .map_err(|_| io::Error::other("host write lock poisoned"))?;
        self.reconcile_locked(managed, mode, None, commit)
    }

    fn reconcile_locked(
        &self,
        managed: &Managed,
        mode: &str,
        expected_mode: Option<&str>,
        commit: impl FnOnce() -> io::Result<()>,
    ) -> io::Result<Transition> {
        if self.0.root.join("openbot-pending-bounce.json").exists() {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "a previous host reload is pending",
            ));
        }
        let file = self.observe()?.ok_or(io::ErrorKind::NotFound)?;
        let logging = logs::Settings::load(&self.0.root).logging_enabled();
        let wants_adapter = mode == "custom" || logging;
        let fingerprint = adapter_fingerprint(&managed.adapter, &self.0.path)?;
        let needs_edit = if wants_adapter {
            !source::marked_with(&file.first_lines, &managed.adapter, &fingerprint)
        } else {
            matches!(file.shape, Shape::Marked | Shape::Foreign)
        };

        let prepared = if needs_edit {
            let raw = self.read_source()?;
            let stock = source::stock(&raw)?;
            let next = if wants_adapter {
                source::mark(&stock, &managed.adapter, &fingerprint)?
            } else {
                stock.clone()
            };
            Some((raw, stock, next))
        } else {
            None
        };
        let mut checked = None;
        if let Some((_, _, next)) = &prepared {
            let staged = stage_host(&self.0.path, next.as_bytes())?;
            syntax_check(&managed.node, staged.path())?;
            checked = Some(staged);
        }
        let mode_path = self.0.root.join(MODE);
        let saved_mode = read_mode(&self.0.root)?;
        if expected_mode.is_some_and(|expected| saved_mode != expected) {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "the selected mode changed",
            ));
        }
        if mode == "official" || saved_mode == "official" {
            set_mode(&mode_path, "official")?;
        }
        if let Some((raw, stock, next)) = prepared {
            if self.read_source()? != raw {
                return Err(io::Error::new(
                    io::ErrorKind::WouldBlock,
                    "the host changed while the update was prepared",
                ));
            }
            backup(&self.0.root, stock.as_bytes())?;
            let targets = process::find(&self.0.path);
            if !targets.is_empty() {
                self.arm(digest(next.as_bytes()), targets)?;
            }
            checked
                .ok_or(io::ErrorKind::InvalidData)?
                .persist(&self.0.path)?;
            sync_parent(&self.0.path)?;
        }
        commit()?;
        if mode == "custom" {
            set_mode(&mode_path, mode)?;
        }
        Ok(Transition {
            changed: needs_edit,
            deferred: self.0.root.join(RELOAD).exists(),
        })
    }

    fn arm(&self, expected_digest: String, targets: Vec<process::Target>) -> io::Result<()> {
        let reload = Reload {
            armed_at_ms: now_ms(),
            expected_digest,
            targets,
        };
        let mut bytes = serde_json::to_vec_pretty(&reload)?;
        bytes.push(b'\n');
        replace(&self.0.root.join(RELOAD), &bytes)
    }

    fn tick(&self) -> io::Result<()> {
        let Some(managed) = &self.0.managed else {
            return Ok(());
        };
        let _writes = managed
            .writes
            .lock()
            .map_err(|_| io::Error::other("host write lock poisoned"))?;
        let mode = read_mode(&self.0.root)?;
        self.reconcile_locked(managed, mode, Some(mode), || Ok(()))?;
        self.apply_reload()
    }

    fn apply_reload(&self) -> io::Result<()> {
        let path = self.0.root.join(RELOAD);
        let Some(raw) = read_bytes(&path, 16 * 1024)? else {
            return Ok(());
        };
        let reload: Reload = serde_json::from_slice(&raw)?;
        let intact = self
            .observe()?
            .is_some_and(|file| file.digest == reload.expected_digest);
        let targets: Vec<_> = reload
            .targets
            .into_iter()
            .filter(|target| process::identity(target.pid, &self.0.path) == Some(*target))
            .collect();
        if !intact || targets.is_empty() {
            return remove_receipt(&path, &raw);
        }
        let activity = self
            .0
            .active
            .lock()
            .map_err(|_| io::Error::other("host turn lock poisoned"))?;
        let lease = read_json(&self.0.root.join("openbot-turn-lease.json"), 4096)
            .ok()
            .flatten();
        if !is_idle(now_ms(), reload.armed_at_ms, &activity, lease.as_ref()) {
            return Ok(());
        }
        if read_bytes(&path, 16 * 1024)?.as_deref() != Some(&raw) {
            return Ok(());
        }
        for target in targets {
            process::terminate(target, &self.0.path)?;
        }
        remove_receipt(&path, &raw)
    }

    pub(crate) fn watch(&self) -> Option<tokio::task::JoinHandle<()>> {
        self.managed().then(|| {
            let host = self.clone();
            tokio::spawn(async move {
                let mut last = None;
                loop {
                    let check = host.clone();
                    let failure = tokio::task::spawn_blocking(move || check.tick())
                        .await
                        .map_err(io::Error::other)
                        .and_then(|value| value)
                        .err()
                        .map(|error| error.to_string());
                    if failure != last
                        && let Some(error) = &failure
                    {
                        eprintln!("OpenBot host check: {error}");
                    }
                    last = failure;
                    tokio::time::sleep(Duration::from_secs(5)).await;
                }
            })
        })
    }

    pub(crate) fn begin_turn(&self) -> Option<Turn> {
        let mut activity = self.0.active.lock().ok()?;
        activity.count += 1;
        activity.updated_ms = now_ms();
        Some(Turn(self.clone()))
    }
}

pub(crate) struct Turn(Host);

impl Turn {
    pub(crate) fn wrap_response(self, response: Response) -> Response {
        response.map(|body| {
            Body::from_stream(body.into_data_stream().map(move |frame| {
                let _turn = &self;
                frame
            }))
        })
    }
}

impl Drop for Turn {
    fn drop(&mut self) {
        if let Ok(mut activity) = self.0.0.active.lock() {
            activity.count = activity.count.saturating_sub(1);
            activity.updated_ms = now_ms();
        }
    }
}

pub(crate) fn tunnel(root: &Path, port: u16, public: Option<&str>) -> Value {
    let internal = format!("http://127.0.0.1:{port}");
    if let Some(url) = public.and_then(https_url) {
        return json!({"kind":"cloudflare-managed","url":url,"internal":internal});
    }
    let pid = read_bytes(&root.join("openbot-tunnel.pid"), 64)
        .ok()
        .flatten()
        .and_then(|bytes| std::str::from_utf8(&bytes).ok()?.trim().parse().ok());
    if let Some(pid) = pid.filter(|pid| process::cloudflared(*pid))
        && let Ok(Some(cache)) = read_json(&root.join("openbot-tunnel.json"), 4096)
        && cache["kind"] == "cloudflare-quick"
        && let Some(url) = cache["url"].as_str().and_then(https_url)
    {
        return json!({"kind":"cloudflare-quick","url":url,"internal":internal,"pid":pid});
    }
    json!({"kind":"off"})
}

fn https_url(value: &str) -> Option<String> {
    let mut url = crate::routing::parse_origin(value.trim())?;
    if url.scheme() != "https" {
        return None;
    }
    url.set_fragment(None);
    Some(url.as_str().trim_end_matches('/').to_owned())
}

fn is_idle(now: u64, armed: u64, activity: &Activity, lease: Option<&Value>) -> bool {
    if now.saturating_sub(armed) < GRACE_MS || activity.count > 0 {
        return false;
    }
    let lease_time = |value: &Value| {
        ["updatedAt", "lastStartAt", "lastEndAt"]
            .iter()
            .filter_map(|field| value[*field].as_u64())
            .max()
            .unwrap_or(0)
    };
    if lease.is_some_and(|lease| lease["active"].as_u64().is_some_and(|active| active > 0)) {
        return false;
    }
    let quiet = if lease.is_some_and(|lease| lease["lastFinishReason"] == "stop") {
        STOP_QUIET_MS
    } else {
        BUSY_QUIET_MS
    };
    now.saturating_sub(
        armed
            .max(activity.updated_ms)
            .max(lease.map_or(0, lease_time)),
    ) >= quiet
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn digest(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn stamp_at(path: &Path) -> io::Result<Option<Stamp>> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_file() => Ok(Some(file_stamp(&meta))),
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "the host entrypoint must be a regular file",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn file_stamp(meta: &Metadata) -> Stamp {
    #[cfg(unix)]
    use std::os::unix::fs::MetadataExt;
    Stamp {
        len: meta.len(),
        modified: meta.modified().ok(),
        #[cfg(unix)]
        inode: (meta.dev(), meta.ino(), meta.ctime(), meta.ctime_nsec()),
    }
}

fn adapter_fingerprint(adapter: &Path, host: &Path) -> io::Result<String> {
    let directory = adapter.parent().ok_or(io::ErrorKind::InvalidInput)?;
    let host_parent = host.parent().ok_or(io::ErrorKind::InvalidInput)?;
    let local_host = (host_parent.canonicalize()? == directory)
        .then_some(host.file_name())
        .flatten();
    let mut names: Vec<_> = fs::read_dir(directory)?
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .filter(|entry| Some(entry.file_name().as_os_str()) != local_host)
        .filter_map(|entry| entry.file_name().into_string().ok())
        .filter(|name| name.ends_with(".cjs") && !name.starts_with('.'))
        .collect();
    names.sort_unstable();
    names.push("version.json".to_owned());
    let mut hash = Sha256::new();
    for name in names {
        hash.update(name.as_bytes());
        hash.update([0]);
        if let Some(body) = read_bytes(&directory.join(name), HOST_LIMIT)? {
            hash.update(body);
        }
        hash.update([0]);
    }
    Ok(format!("{:x}", hash.finalize())[..16].to_owned())
}

fn stage_host(path: &Path, bytes: &[u8]) -> io::Result<tempfile::NamedTempFile> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.file_type().is_file() {
        return Err(io::ErrorKind::InvalidData.into());
    }
    let mut temp = tempfile::Builder::new()
        .prefix(".openbot-host-")
        .suffix(".cjs")
        .tempfile_in(path.parent().ok_or(io::ErrorKind::InvalidInput)?)?;
    #[cfg(target_os = "linux")]
    {
        use rustix::process::{Gid, getegid, geteuid};
        use std::os::unix::fs::MetadataExt;
        if meta.uid() != geteuid().as_raw() {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "the host source has a different owner",
            ));
        }
        if meta.gid() != getegid().as_raw() {
            rustix::fs::fchown(temp.as_file(), None, Some(Gid::from_raw(meta.gid())))?;
        }
    }
    temp.as_file().set_permissions(meta.permissions())?;
    temp.write_all(bytes)?;
    temp.as_file().sync_all()?;
    Ok(temp)
}

fn syntax_check(node: &Path, file: &Path) -> io::Result<()> {
    let mut child = Command::new(node)
        .arg("--check")
        .arg(file)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        if let Some(result) = child.try_wait()? {
            return if result.success() {
                Ok(())
            } else {
                Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "the updated host did not pass Node syntax validation",
                ))
            };
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(io::ErrorKind::TimedOut.into());
        }
        std::thread::sleep(Duration::from_millis(30));
    }
}

fn set_mode(path: &Path, mode: &str) -> io::Result<()> {
    let next = format!("{mode}\n");
    if read_bytes(path, 128)?.as_deref() != Some(next.as_bytes()) {
        replace(path, next.as_bytes())?;
    }
    Ok(())
}

fn backup(root: &Path, bytes: &[u8]) -> io::Result<()> {
    let path = root.join("host-main.cjs.pre-openbot");
    if path.exists() {
        return Ok(());
    }
    let mut temp = tempfile::NamedTempFile::new_in(root)?;
    temp.write_all(bytes)?;
    temp.as_file().sync_all()?;
    match temp.persist_noclobber(&path) {
        Ok(_) => sync_parent(&path),
        Err(error) if error.error.kind() == io::ErrorKind::AlreadyExists => Ok(()),
        Err(error) => Err(error.error),
    }
}

fn replace(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut temp =
        tempfile::NamedTempFile::new_in(path.parent().ok_or(io::ErrorKind::InvalidInput)?)?;
    temp.write_all(bytes)?;
    temp.as_file().sync_all()?;
    temp.persist(path)?;
    sync_parent(path)
}

fn remove_receipt(path: &Path, previous: &[u8]) -> io::Result<()> {
    if read_bytes(path, 16 * 1024)?.as_deref() == Some(previous) {
        fs::remove_file(path)?;
        sync_parent(path)?;
    }
    Ok(())
}

fn sync_parent(path: &Path) -> io::Result<()> {
    File::open(path.parent().ok_or(io::ErrorKind::InvalidInput)?)?.sync_all()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reload_respects_both_native_activity_and_the_host_lease() {
        let now = 600_000;
        let idle = Activity::default();
        let lease = json!({"active":0,"updatedAt":50_000,"lastFinishReason":"stop"});
        assert!(is_idle(now, 400_000, &idle, Some(&lease)));
        assert!(!is_idle(now, 490_000, &idle, Some(&lease)));
        assert!(!is_idle(now, 400_000, &idle, None));
        assert!(is_idle(now, 200_000, &idle, None));
        let in_flight = Activity {
            count: 1,
            updated_ms: 0,
        };
        assert!(!is_idle(now, 0, &in_flight, Some(&lease)));
        assert!(!is_idle(
            now,
            0,
            &idle,
            Some(&json!({"active":1,"updatedAt":1}))
        ));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn switches_to_custom_only_after_a_validated_wrapper_and_restores_stock() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("host-main.cjs");
        let adapter = root.path().join("runtime.cjs");
        let stock = b"function createProtoSessionProvider(){return {}; }\n";
        fs::write(&path, stock).unwrap();
        fs::write(&adapter, "module.exports={}").unwrap();
        let controller =
            Host::new(root.path(), path.clone(), Some(adapter), "/bin/true".into()).unwrap();
        let selected = controller
            .reconcile("custom", || {
                assert_eq!(read_mode(root.path())?, "official");
                assert_eq!(controller.wrap_and_process().0["kind"], "openbot-marked");
                Ok(())
            })
            .unwrap();
        assert!(selected.changed);
        assert!(!selected.deferred);
        assert_eq!(read_mode(root.path()).unwrap(), "custom");
        assert!(!controller.reconcile("custom", || Ok(())).unwrap().changed);
        fs::write(&path, stock).unwrap();
        controller.tick().unwrap();
        assert_eq!(controller.wrap_and_process().0["kind"], "openbot-marked");
        fs::write(
            root.path().join("openbot-logs.json"),
            r#"{"loggingEnabled":true}"#,
        )
        .unwrap();
        assert!(!controller.reconcile("official", || Ok(())).unwrap().changed);
        assert_eq!(read_mode(root.path()).unwrap(), "official");
        assert_eq!(controller.wrap_and_process().0["kind"], "openbot-marked");
        fs::write(root.path().join("openbot-logs.json"), "{}").unwrap();
        controller.tick().unwrap();
        assert_eq!(fs::read(&path).unwrap(), stock);
        assert_eq!(
            fs::read(root.path().join("host-main.cjs.pre-openbot")).unwrap(),
            stock
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn syntax_failure_never_publishes_custom_or_creates_a_reload() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("host-main.cjs");
        let adapter = root.path().join("runtime.cjs");
        let stock = b"function createProtoSessionProvider(){return {}; }\n";
        fs::write(&path, stock).unwrap();
        fs::write(&adapter, "module.exports={}").unwrap();
        let controller = Host::new(
            root.path(),
            path.clone(),
            Some(adapter),
            "/bin/false".into(),
        )
        .unwrap();
        assert!(
            controller
                .reconcile("custom", || panic!("catalog callback must be skipped"))
                .is_err()
        );
        assert_eq!(fs::read(&path).unwrap(), stock);
        assert_eq!(read_mode(root.path()).unwrap(), "official");
        assert!(!root.path().join(RELOAD).exists());
    }
}
