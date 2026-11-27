mod adapter;

use std::{
    fs::{self, File, OpenOptions},
    io::{self, Write},
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
};

use axum::body::Bytes;
use clap::Args;
use sha2::{Digest, Sha256};

use super::{Selection, drift, platform::Identity};

const BINARY: &str = "openbot-runtime";
const ACCESS: &str = "access.json";

#[derive(Args)]
pub(crate) struct Install {
    /// Built panel assets for this revision.
    #[arg(long)]
    ui_dir: PathBuf,
    /// Full Git revision for the release.
    #[arg(long)]
    revision: String,
    /// Project payload directory containing the stock Computer adapter.
    #[arg(long, requires = "host_main")]
    payload_dir: Option<PathBuf>,
    /// Standalone Node Computer entrypoint managed by this release.
    #[arg(long, requires = "payload_dir")]
    host_main: Option<PathBuf>,
    /// Absolute Node executable path for the staged source check.
    #[arg(long, requires = "host_main")]
    host_node: Option<PathBuf>,
    /// Optional configuration for Cloudflare Access at public panel origins.
    #[arg(long)]
    access_config: Option<PathBuf>,
}

pub(super) fn stage(
    data: &Path,
    options: &Install,
    port: u16,
    source: &Path,
) -> io::Result<Selection> {
    let revision = options.revision.as_str();
    if !matches!(revision.len(), 40 | 64) || !revision.bytes().all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "a full Git revision is required",
        ));
    }
    let payload = options
        .payload_dir
        .as_ref()
        .map(|directory| adapter::Payload::stage(directory, revision, port))
        .transpose()?;
    let files = Files {
        executable: fs::read(source)?,
        assets: crate::panel::release_assets(&options.ui_dir)?,
        revision,
        payload,
        access: options
            .access_config
            .as_deref()
            .map(crate::ingress::release_config)
            .transpose()?,
    };
    let releases = data.join(".openbot-rust-releases");
    fs::create_dir_all(&releases)?;
    if !releases.symlink_metadata()?.file_type().is_dir() {
        return Err(drift("the release store must be a directory"));
    }
    let release = releases.join(files.digest());
    let argv = command(&release, data, port, options)?;
    let quoted = argv
        .iter()
        .map(|arg| quote(arg))
        .collect::<io::Result<Vec<_>>>()?
        .join(" ");
    let drop_in = format!(
        "# Managed by openbot-runtime service\n[Service]\nWorkingDirectory=/\n\
         Environment=OPENBOT_COMMIT={revision}\nExecStart=\nExecStart=:{quoted}\n"
    );
    files.install(&releases, &release)?;
    Ok(Selection {
        identity: Identity::expected(&argv),
        drop_in: Some(drop_in),
    })
}

struct Files<'a> {
    executable: Vec<u8>,
    assets: Vec<(&'static str, Bytes)>,
    revision: &'a str,
    payload: Option<adapter::Payload>,
    access: Option<Vec<u8>>,
}

pub(super) fn validate_installed(executable: &Path) -> io::Result<()> {
    let release = executable.parent().ok_or(io::ErrorKind::InvalidInput)?;
    let revision = fs::read_to_string(release.join("revision"))?;
    let files = Files {
        executable: fs::read(executable)?,
        assets: crate::panel::release_assets(&release.join("ui"))?,
        revision: &revision,
        payload: adapter::Payload::read_installed(release)?,
        access: read_access(release)?,
    };
    if !release.symlink_metadata()?.file_type().is_dir()
        || !release.join("ui").symlink_metadata()?.file_type().is_dir()
        || !executable.symlink_metadata()?.file_type().is_file()
        || release.file_name().and_then(|name| name.to_str()) != Some(&files.digest())
    {
        return Err(drift("the installed runtime no longer matches its bundle"));
    }
    Ok(())
}

impl Files<'_> {
    fn digest(&self) -> String {
        let mut hash = Sha256::new();
        hash.update(Sha256::digest(self.revision));
        hash.update(Sha256::digest(&self.executable));
        for (name, bytes) in &self.assets {
            hash.update(Sha256::digest(name));
            hash.update(Sha256::digest(bytes));
        }
        if let Some(payload) = &self.payload {
            payload.hash(&mut hash);
        }
        if let Some(access) = &self.access {
            hash.update(Sha256::digest(ACCESS));
            hash.update(Sha256::digest(access));
        }
        format!("{:x}", hash.finalize())
    }

    fn install(&self, releases: &Path, release: &Path) -> io::Result<()> {
        match release.symlink_metadata() {
            Ok(entry) => {
                if !entry.file_type().is_dir() {
                    return Err(drift("the staged runtime is not a directory"));
                }
                return self.verify(release);
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
        let staging = tempfile::tempdir_in(releases)?;
        let path = staging.path();
        fs::create_dir(path.join("ui"))?;
        write_new(path.join(BINARY), &self.executable, 0o700)?;
        write_new(path.join("revision"), self.revision.as_bytes(), 0o600)?;
        for (name, bytes) in &self.assets {
            write_new(path.join("ui").join(name), bytes, 0o600)?;
        }
        if let Some(payload) = &self.payload {
            payload.install(path)?;
        }
        if let Some(access) = &self.access {
            write_new(path.join(ACCESS), access, 0o600)?;
        }
        File::open(path.join("ui"))?.sync_all()?;
        File::open(path)?.sync_all()?;
        fs::rename(path, release)?;
        File::open(releases)?.sync_all()
    }

    fn verify(&self, release: &Path) -> io::Result<()> {
        if !release.join("ui").symlink_metadata()?.file_type().is_dir()
            || !matches_bytes(&release.join(BINARY), &self.executable)?
            || !matches_bytes(&release.join("revision"), self.revision.as_bytes())?
        {
            return Err(drift("the existing staged release has changed"));
        }
        for (name, bytes) in &self.assets {
            if !matches_bytes(&release.join("ui").join(name), bytes)? {
                return Err(drift("the existing staged panel has changed"));
            }
        }
        if adapter::Payload::read_installed(release)? != self.payload {
            return Err(drift("the existing staged host adapter has changed"));
        }
        if read_access(release)? != self.access {
            return Err(drift(
                "the existing staged panel access configuration has changed",
            ));
        }
        Ok(())
    }
}

fn read_access(release: &Path) -> io::Result<Option<Vec<u8>>> {
    let path = release.join(ACCESS);
    match path.symlink_metadata() {
        Ok(_) => crate::ingress::release_config(&path).map(Some),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn write_new(path: PathBuf, data: &[u8], mode: u32) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(path)?;
    file.write_all(data)?;
    file.sync_all()
}

fn matches_bytes(path: &Path, expected: &[u8]) -> io::Result<bool> {
    Ok(path.symlink_metadata()?.file_type().is_file()
        && crate::server::read_bytes(path, expected.len() as u64)?.as_deref() == Some(expected))
}

fn command(release: &Path, data: &Path, port: u16, options: &Install) -> io::Result<Vec<String>> {
    let path = |path: &Path| {
        path.to_str()
            .map(str::to_owned)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path must be UTF-8"))
    };
    let mut argv = vec![
        path(&release.join(BINARY))?,
        "--sand-data".into(),
        path(data)?,
        "--port".into(),
        port.to_string(),
        "--ui-dir".into(),
        path(&release.join("ui"))?,
    ];
    argv.extend(
        [
            "--managed-service",
            "--write-bot-settings",
            "--enable-hop",
            "--enable-accounts",
            "--enable-model-refresh",
            "--enable-request-logs",
            "--enable-compaction",
        ]
        .map(str::to_owned),
    );
    if let Some(host) = options.host_main.as_deref() {
        if !host.is_absolute() || !host.symlink_metadata()?.file_type().is_file() {
            return Err(drift("select an absolute regular Computer entrypoint"));
        }
        argv.extend([
            "--host-main".into(),
            path(host)?,
            "--host-adapter".into(),
            path(&release.join("payload/runtime.cjs"))?,
        ]);
        if let Some(node) = options.host_node.as_deref() {
            use std::os::unix::fs::PermissionsExt;
            if !node.is_absolute()
                || !node.is_file()
                || node.metadata()?.permissions().mode() & 0o111 == 0
            {
                return Err(drift("select an absolute Node executable"));
            }
            argv.extend(["--host-node".into(), path(node)?]);
        }
    }
    if options.access_config.is_some() {
        argv.extend(["--access-config".into(), path(&release.join(ACCESS))?]);
    }
    Ok(argv)
}

fn quote(value: &str) -> io::Result<String> {
    if value.chars().any(char::is_control) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "a unit argument contains a control character",
        ));
    }
    Ok(format!(
        "\"{}\"",
        value
            .replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('%', "%%")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pins_the_binary_and_exact_panel_assets_without_overwriting_a_release() {
        let directory = tempfile::tempdir().unwrap();
        let data = directory.path();
        let ui = data.join("original ui");
        fs::create_dir(&ui).unwrap();
        for name in ["index.html", "app.js", "styles.css", "favicon.svg"] {
            fs::write(ui.join(name), name).unwrap();
        }
        fs::write(ui.join(".env"), "must not copy").unwrap();
        let executable = data.join(BINARY);
        fs::write(&executable, "fixture executable").unwrap();
        let options = Install {
            ui_dir: ui.clone(),
            revision: "a".repeat(40),
            payload_dir: None,
            host_main: None,
            host_node: None,
            access_config: None,
        };
        let first = stage(data, &options, 9280, &executable).unwrap();
        assert_eq!(stage(data, &options, 9280, &executable).unwrap(), first);
        let staged = fs::read_dir(data.join(".openbot-rust-releases"))
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        assert!(!staged.join("ui/.env").exists());
        validate_installed(&staged.join(BINARY)).unwrap();
        fs::write(staged.join("ui/app.js"), "tampered").unwrap();
        assert!(validate_installed(&staged.join(BINARY)).is_err());
        assert!(stage(data, &options, 9280, &executable).is_err());
        fs::write(ui.join("app.js"), "change").unwrap();
        assert_ne!(stage(data, &options, 9280, &executable).unwrap(), first);
    }

    #[test]
    fn quotes_unit_arguments_without_expanding_specifiers() {
        assert_eq!(
            quote("/test/$a%x \"v\"").unwrap(),
            "\"/test/$a%%x \\\"v\\\"\""
        );
        assert!(quote("/one\nExecStart=/unrelated").is_err());
    }
}
