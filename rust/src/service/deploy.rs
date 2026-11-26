mod bundle;
mod platform;

use std::{
    fs::{self, File},
    io,
    path::{Path, PathBuf},
};

use clap::Subcommand;
use serde::{Deserialize, Serialize};

use super::replace;
use crate::lease::Lease;
use platform::{Identity, Running, Systemd};

const DROP_IN: &str = "90-openbot-native-runtime.conf";
const JOURNAL: &str = ".openbot-rust-deployment.json";

#[derive(Subcommand)]
pub(crate) enum Action {
    /// Prove which process owns the configured OpenBot listener.
    Status,
    /// Stage this binary and its built panel, then restart the existing unit.
    Install {
        #[arg(long)]
        ui_dir: PathBuf,
        #[arg(long)]
        revision: String,
    },
    /// Restore the preceding verified service, or recover an interrupted install.
    Rollback {
        /// Restore the service that was running before the first native install.
        #[arg(long)]
        original: bool,
    },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
struct Selection {
    identity: Identity,
    drop_in: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Change {
    before: Selection,
    after: Selection,
}

#[derive(Default, Deserialize, Serialize)]
struct Journal {
    unit: String,
    port: u16,
    original: Option<Selection>,
    previous: Option<Change>,
    pending: Option<Change>,
}

pub(crate) async fn run(data: &Path, port: u16, unit: &str, action: &Action) -> io::Result<()> {
    if port == 0 || !unit_name(unit) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "specify a fixed port and an existing openbot user service",
        ));
    }
    let data = data.canonicalize()?;
    let platform = Systemd::new(unit, &data, port)?;
    if matches!(action, Action::Status) {
        let running = platform.live().await?;
        println!("{}", serde_json::to_string_pretty(&running)?);
        return Ok(());
    }

    let directory = systemd_user_config()?.join(format!("{unit}.d"));
    platform.require_unit().await?;
    fs::create_dir_all(&directory)?;
    let _unit = Lease::acquire(&directory.join(".openbot-rust-deploy.lock"))?;
    let _data = Lease::acquire(&data.join(".openbot-rust-deploy.lock"))?;
    let mut deploy = Deployment {
        drop_in: directory.join(DROP_IN),
        journal_path: data.join(JOURNAL),
        journal: Journal::default(),
        platform,
    };
    deploy.journal = deploy.read_journal(unit, port)?;
    if let Some(restored) = deploy.recover().await?
        && matches!(action, Action::Rollback { original: false })
    {
        println!("{}", serde_json::to_string_pretty(&restored)?);
        return Ok(());
    }

    let running = deploy.platform.live().await?;
    let before = Selection {
        identity: running.identity,
        drop_in: deploy.read_drop_in()?,
    };
    if let Some(history) = &deploy.journal.previous {
        if before != history.after {
            return Err(drift("running service differs from the last deployment"));
        }
    } else if before.drop_in.is_some() {
        return Err(drift("the managed drop-in has no deployment receipt"));
    }

    let after = match action {
        Action::Install { ui_dir, revision } => {
            bundle::stage(&data, ui_dir, revision, port, &std::env::current_exe()?)?
        }
        Action::Rollback { original } => if *original {
            deploy.journal.original.as_ref()
        } else {
            deploy
                .journal
                .previous
                .as_ref()
                .map(|change| &change.before)
        }
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "no saved deployment"))?
        .clone(),
        Action::Status => unreachable!(),
    };
    if before == after {
        println!(
            "{}",
            serde_json::to_string_pretty(&deploy.platform.live().await?)?
        );
        return Ok(());
    }
    deploy.change(Change { before, after }, running.pid).await
}

struct Deployment {
    drop_in: PathBuf,
    journal_path: PathBuf,
    journal: Journal,
    platform: Systemd,
}

impl Deployment {
    fn read_journal(&self, unit: &str, port: u16) -> io::Result<Journal> {
        let Some(bytes) = crate::server::read_bytes(&self.journal_path, 128 * 1024)? else {
            return Ok(Journal {
                unit: unit.into(),
                port,
                ..Journal::default()
            });
        };
        let journal: Journal = serde_json::from_slice(&bytes)?;
        if journal.unit != unit || journal.port != port {
            return Err(drift(
                "the deployment receipt belongs to another unit or port",
            ));
        }
        Ok(journal)
    }

    fn save_journal(&self) -> io::Result<()> {
        let mut bytes = serde_json::to_vec_pretty(&self.journal)?;
        bytes.push(b'\n');
        replace(&self.journal_path, &bytes)
    }

    fn read_drop_in(&self) -> io::Result<Option<String>> {
        crate::server::read_bytes(&self.drop_in, 16 * 1024)?
            .map(|bytes| {
                String::from_utf8(bytes)
                    .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
            })
            .transpose()
    }

    fn select(&self, chosen: &Selection) -> io::Result<()> {
        if chosen.drop_in.is_some() {
            bundle::validate_installed(chosen.identity.executable())?;
        }
        if self.read_drop_in()? == chosen.drop_in {
            return Ok(());
        }
        if let Some(bytes) = &chosen.drop_in {
            replace(&self.drop_in, bytes.as_bytes())
        } else {
            fs::remove_file(&self.drop_in)?;
            File::open(self.drop_in.parent().ok_or(io::ErrorKind::InvalidInput)?)?.sync_all()
        }
    }

    async fn recover(&mut self) -> io::Result<Option<Running>> {
        let Some(change) = &self.journal.pending else {
            return Ok(None);
        };
        let actual = self.read_drop_in()?;
        if actual != change.before.drop_in && actual != change.after.drop_in {
            return Err(drift(
                "the drop-in was edited during an incomplete deployment",
            ));
        }
        self.platform.require_recoverable_port().await?;
        self.select(&change.before)?;
        let running = self.platform.restart(&change.before.identity, None).await?;
        self.journal.pending = None;
        self.save_journal()?;
        Ok(Some(running))
    }

    async fn change(&mut self, change: Change, replaced_pid: u32) -> io::Result<()> {
        self.journal
            .original
            .get_or_insert_with(|| change.before.clone());
        self.journal.pending = Some(change.clone());
        self.save_journal()?;
        let result = match self.select(&change.after) {
            Ok(()) => {
                self.platform
                    .restart(&change.after.identity, Some(replaced_pid))
                    .await
            }
            Err(error) => Err(error),
        };
        match result {
            Ok(running) => {
                self.journal.previous = Some(change);
                self.journal.pending = None;
                self.save_journal()?;
                println!("{}", serde_json::to_string_pretty(&running)?);
                Ok(())
            }
            Err(error) => match self.recover().await {
                Ok(_) => Err(io::Error::other(format!(
                    "{error}; previous service restored"
                ))),
                Err(recovery) => Err(io::Error::other(format!(
                    "{error}; recovery pending: {recovery}"
                ))),
            },
        }
    }
}

fn unit_name(value: &str) -> bool {
    let Some(name) = value.strip_suffix(".service") else {
        return false;
    };
    name == "openbot"
        || name.strip_prefix("openbot-").is_some_and(|suffix| {
            !suffix.is_empty()
                && suffix
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        })
}

fn systemd_user_config() -> io::Result<PathBuf> {
    let directory = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config")))
        .filter(|path| path.is_absolute())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no user configuration path"))?;
    Ok(directory.join("systemd/user"))
}

fn drift(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_concrete_openbot_unit_names() {
        assert!(unit_name("openbot.service"));
        assert!(unit_name("openbot-870.service"));
        for name in [
            "../openbot.service",
            "openbot-.service",
            "openbot@870.service",
            "openbot-870.socket",
            "unrelated.service",
        ] {
            assert!(!unit_name(name), "{name}");
        }
    }
}
