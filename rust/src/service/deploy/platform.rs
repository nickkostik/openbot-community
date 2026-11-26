use std::{
    fs, io,
    net::{Ipv4Addr, SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};

use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio::process::Command;

use crate::service::{OWNER, read_owner};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub(super) struct Identity {
    executable: PathBuf,
    command_sha256: String,
}

impl Identity {
    pub(super) fn executable(&self) -> &Path {
        &self.executable
    }

    pub(super) fn expected(argv: &[String]) -> Self {
        let mut hash = Sha256::new();
        for arg in argv {
            hash.update(arg.as_bytes());
            hash.update([0]);
        }
        Self {
            executable: PathBuf::from(&argv[0]),
            command_sha256: format!("{:x}", hash.finalize()),
        }
    }

    fn live(pid: u32) -> io::Result<Self> {
        let proc = PathBuf::from(format!("/proc/{pid}"));
        Ok(Self {
            executable: fs::read_link(proc.join("exe"))?,
            command_sha256: format!("{:x}", Sha256::digest(fs::read(proc.join("cmdline"))?)),
        })
    }
}

#[derive(Serialize)]
pub(super) struct Running {
    pub(super) pid: u32,
    pub(super) identity: Identity,
    port: u16,
    mode: &'static str,
}

pub(super) struct Systemd {
    unit: String,
    data: PathBuf,
    port: u16,
    client: Client,
}

impl Systemd {
    pub(super) fn new(unit: &str, data: &Path, port: u16) -> io::Result<Self> {
        let client = Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(2))
            .build()
            .map_err(io::Error::other)?;
        Ok(Self {
            unit: unit.into(),
            data: data.into(),
            port,
            client,
        })
    }

    async fn main_pid(&self) -> io::Result<u32> {
        let result = systemctl(&[
            "show",
            &self.unit,
            "--property=LoadState",
            "--property=MainPID",
        ])
        .await?;
        parse_unit(&result)
    }

    pub(super) async fn require_unit(&self) -> io::Result<()> {
        self.main_pid().await.map(|_| ())
    }

    pub(super) async fn live(&self) -> io::Result<Running> {
        let pid = self.main_pid().await?;
        let identity = self.owner(pid)?;
        let health = self
            .client
            .get(format!("http://127.0.0.1:{}/healthz", self.port))
            .send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map_err(io::Error::other)?
            .json::<Value>()
            .await
            .map_err(io::Error::other)?;
        if health["ok"] != true || health["service"] != "openbot" {
            return Err(io::Error::other(
                "the owned listener failed the OpenBot health check",
            ));
        }
        if self.main_pid().await? != pid || self.owner(pid)? != identity {
            return Err(io::Error::other(
                "the process changed during the health check",
            ));
        }
        Ok(Running {
            pid,
            identity,
            port: self.port,
            mode: crate::server::read_mode(&self.data)?,
        })
    }

    fn owner(&self, pid: u32) -> io::Result<Identity> {
        if pid == 0
            || read_owner(&self.data.join(OWNER)) != Some(pid)
            || !owns_listener(pid, self.port, true)?
        {
            return Err(io::Error::other(
                "the user unit, published PID, and loopback listener must identify the same process",
            ));
        }
        Identity::live(pid)
    }

    pub(super) async fn require_recoverable_port(&self) -> io::Result<()> {
        let listeners = listening_inodes(&fs::read_to_string("/proc/net/tcp")?, self.port, false);
        let address = SocketAddr::from((Ipv4Addr::LOCALHOST, self.port));
        let occupied = !listeners.is_empty()
            || TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok();
        if occupied && !owns_listener(self.main_pid().await?, self.port, true)? {
            return Err(io::Error::other("a foreign process owns the loopback port"));
        }
        Ok(())
    }

    pub(super) async fn restart(
        &self,
        expected: &Identity,
        replaced_pid: Option<u32>,
    ) -> io::Result<Running> {
        systemctl(&["daemon-reload"]).await?;
        let restart = ["restart", self.unit.as_str()];
        if let Err(error) = systemctl(&restart).await {
            let result = systemctl(&["show", &self.unit, "--property=Result", "--value"]).await?;
            if result.trim() != "start-limit-hit" {
                return Err(error);
            }
            // A deployment and recovery can use several starts in one interval.
            // Reset the limit only for this explicit operation on the same unit.
            self.require_recoverable_port().await?;
            systemctl(&["reset-failed", &self.unit]).await?;
            systemctl(&restart).await?;
        }
        let deadline = Instant::now() + Duration::from_secs(18);
        loop {
            match self.live().await {
                Ok(running) if Some(running.pid) != replaced_pid => {
                    if &running.identity == expected {
                        return Ok(running);
                    }
                    return Err(io::Error::other(
                        "the restarted unit is serving a different command",
                    ));
                }
                Err(error) if Instant::now() >= deadline => return Err(error),
                _ if Instant::now() >= deadline => {
                    return Err(io::Error::other("the unit did not replace its process"));
                }
                _ => tokio::time::sleep(Duration::from_millis(200)).await,
            }
        }
    }
}

async fn systemctl(args: &[&str]) -> io::Result<String> {
    let output = tokio::time::timeout(
        Duration::from_secs(35),
        Command::new("systemctl")
            .args(["--user", "--no-pager", "--no-ask-password"])
            .args(args)
            .stdin(Stdio::null())
            .kill_on_drop(true)
            .output(),
    )
    .await??;
    if !output.status.success() {
        return Err(io::Error::other(format!(
            "systemctl --user {} failed ({})",
            args[0], output.status
        )));
    }
    String::from_utf8(output.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
}

fn parse_unit(output: &str) -> io::Result<u32> {
    if !output.lines().any(|line| line == "LoadState=loaded") {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "the OpenBot user unit is not loaded",
        ));
    }
    output
        .lines()
        .find_map(|line| line.strip_prefix("MainPID="))
        .and_then(|pid| pid.parse().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "no systemd main PID"))
}

fn owns_listener(pid: u32, port: u16, exact: bool) -> io::Result<bool> {
    if pid == 0 {
        return Ok(false);
    }
    let listeners = listening_inodes(&fs::read_to_string("/proc/net/tcp")?, port, exact);
    for descriptor in fs::read_dir(format!("/proc/{pid}/fd"))? {
        let Ok(target) = fs::read_link(descriptor?.path()) else {
            continue;
        };
        if listeners.contains(&target) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn listening_inodes(table: &str, port: u16, exact: bool) -> Vec<PathBuf> {
    let loopback = format!("0100007F:{port:04X}");
    let all_ipv4 = format!("00000000:{port:04X}");
    table
        .lines()
        .skip(1)
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let address = fields.nth(1)?;
            let state = fields.nth(1)?;
            if state != "0A" || (address != loopback && (exact || address != all_ipv4)) {
                return None;
            }
            let inode = fields.nth(5)?.parse::<u64>().ok()?;
            Some(PathBuf::from(format!("socket:[{inode}]")))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compares_the_actual_bound_socket_to_the_process_descriptors() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(owns_listener(std::process::id(), port, true).unwrap());
        assert!(!owns_listener(0, port, true).unwrap());
        drop(listener);
        assert!(!owns_listener(std::process::id(), port, true).unwrap());
        let listener = std::net::TcpListener::bind("0.0.0.0:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(!owns_listener(std::process::id(), port, true).unwrap());
        assert!(owns_listener(std::process::id(), port, false).unwrap());
    }

    #[test]
    fn accepts_loaded_units_and_preserves_the_missing_pid_distinction() {
        assert_eq!(parse_unit("MainPID=123\nLoadState=loaded\n").unwrap(), 123);
        assert_eq!(parse_unit("MainPID=0\nLoadState=loaded\n").unwrap(), 0);
        assert!(parse_unit("MainPID=0\nLoadState=not-found\n").is_err());
    }
}
