use std::{fs, io, path::Path};

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
pub(super) struct Target {
    pub(super) pid: u32,
    start: u64,
}

#[cfg(target_os = "linux")]
pub(super) fn find(path: &Path) -> Vec<Target> {
    let mut targets: Vec<_> = pids().filter_map(|pid| identity(pid, path)).collect();
    targets.sort_unstable_by_key(|target| target.pid);
    targets
}

#[cfg(target_os = "linux")]
fn pids() -> impl Iterator<Item = u32> {
    fs::read_dir("/proc")
        .into_iter()
        .flatten()
        .filter_map(|entry| entry.ok()?.file_name().to_str()?.parse().ok())
}

#[cfg(target_os = "linux")]
pub(super) fn desktop() -> Option<u32> {
    use std::os::unix::fs::MetadataExt;

    let uid = rustix::process::geteuid().as_raw();
    pids()
        .filter(|&pid| {
            let root = std::path::PathBuf::from(format!("/proc/{pid}"));
            let Ok(executable) = fs::read_link(root.join("exe")) else {
                return false;
            };
            if !matches!(
                executable.to_str(),
                Some(
                    "/opt/Grok Bot/grok-bot"
                        | "/opt/grok-bot"
                        | "/usr/bin/grok-bot"
                        | "/usr/local/bin/grok-bot"
                )
            ) || !fs::metadata(&root).is_ok_and(|meta| meta.uid() == uid)
            {
                return false;
            }
            let parent = fs::read_to_string(root.join("stat")).ok().and_then(|stat| {
                let mut fields = stat.rsplit_once(')')?.1.split_whitespace();
                (fields.next()? != "Z").then_some(())?;
                fields.next()?.parse::<u32>().ok()
            });
            let child = parent
                .and_then(|pid| fs::read_link(format!("/proc/{pid}/exe")).ok())
                .is_some_and(|path| path == executable);
            let command = crate::server::read_bytes(&root.join("cmdline"), 64 * 1024);
            parent.is_some()
                && !child
                && command.is_ok_and(|bytes| {
                    bytes.is_some_and(|bytes| {
                        !bytes.is_empty() && !bytes.windows(7).any(|bytes| bytes == b"--type=")
                    })
                })
        })
        .min()
}

#[cfg(not(target_os = "linux"))]
pub(super) fn desktop() -> Option<u32> {
    None
}

#[cfg(not(target_os = "linux"))]
pub(super) fn find(_: &Path) -> Vec<Target> {
    Vec::new()
}

#[cfg(target_os = "linux")]
pub(super) fn identity(pid: u32, path: &Path) -> Option<Target> {
    let root = std::path::PathBuf::from(format!("/proc/{pid}"));
    let cmd = crate::server::read_bytes(&root.join("cmdline"), 64 * 1024).ok()??;
    if entrypoint(&cmd) != Some(path.as_os_str().as_encoded_bytes()) {
        return None;
    }
    if fs::read_link(root.join("exe")).ok()?.file_name()? != "node" {
        return None;
    }
    let stat = fs::read_to_string(root.join("stat")).ok()?;
    let state = stat.rsplit_once(')')?.1.trim_start();
    if state.starts_with('Z') {
        return None;
    }
    let start = state.split_whitespace().nth(19)?.parse().ok()?;
    Some(Target { pid, start })
}

#[cfg(target_os = "linux")]
fn entrypoint(cmd: &[u8]) -> Option<&[u8]> {
    let mut args = cmd.split(|byte| *byte == 0).filter(|arg| !arg.is_empty());
    if args.next()?.rsplit(|byte| *byte == b'/').next()? != b"node" {
        return None;
    }
    while let Some(arg) = args.next() {
        if arg == b"--" {
            return args.next();
        }
        let option = arg.split(|byte| *byte == b'=').next()?;
        if matches!(
            option,
            b"-e" | b"-p" | b"-c" | b"--eval" | b"--print" | b"--check"
        ) || [b"-e", b"-p", b"-c"]
            .into_iter()
            .any(|prefix| arg.starts_with(prefix))
        {
            return None;
        }
        if matches!(
            arg,
            b"-r"
                | b"--require"
                | b"--import"
                | b"--loader"
                | b"--experimental-loader"
                | b"--conditions"
        ) {
            args.next()?;
        } else if !arg.starts_with(b"-") {
            return Some(arg);
        }
    }
    None
}

#[cfg(not(target_os = "linux"))]
pub(super) fn identity(_: u32, _: &Path) -> Option<Target> {
    None
}

#[cfg(target_os = "linux")]
pub(super) fn terminate(target: Target, path: &Path) -> io::Result<()> {
    use rustix::process::{Pid, PidfdFlags, Signal, pidfd_open, pidfd_send_signal};
    let pid = i32::try_from(target.pid)
        .ok()
        .and_then(Pid::from_raw)
        .ok_or(io::ErrorKind::InvalidInput)?;
    let fd = match pidfd_open(pid, PidfdFlags::empty()) {
        Ok(fd) => fd,
        Err(rustix::io::Errno::SRCH) => return Ok(()),
        Err(error) => return Err(error.into()),
    };
    if identity(target.pid, path) != Some(target) {
        return Ok(());
    }
    match pidfd_send_signal(fd, Signal::TERM) {
        Ok(()) | Err(rustix::io::Errno::SRCH) => Ok(()),
        Err(error) => Err(error.into()),
    }
}

#[cfg(not(target_os = "linux"))]
pub(super) fn terminate(_: Target, _: &Path) -> io::Result<()> {
    Err(io::ErrorKind::Unsupported.into())
}

#[cfg(target_os = "linux")]
pub(super) fn cloudflared(pid: u32) -> bool {
    fs::read_link(format!("/proc/{pid}/exe"))
        .is_ok_and(|exe| exe.file_name().is_some_and(|name| name == "cloudflared"))
}

#[cfg(not(target_os = "linux"))]
pub(super) fn cloudflared(_: u32) -> bool {
    false
}

#[cfg(all(test, target_os = "linux"))]
#[test]
fn only_a_real_node_entrypoint_can_own_the_host() {
    for argv in [
        b"/usr/bin/node\0--require\0/other.cjs\0/home/host main.cjs\0--arg\0".as_slice(),
        b"node\0--\0/home/host main.cjs\0",
    ] {
        assert_eq!(entrypoint(argv), Some(b"/home/host main.cjs".as_slice()));
    }
    for argv in [
        b"node\0--check\0/home/host main.cjs\0".as_slice(),
        b"node\0-eexample\0/home/host main.cjs\0",
        b"/bin/sh\0-c\0node /home/host main.cjs\0",
    ] {
        assert_eq!(entrypoint(argv), None);
    }
    assert_eq!(
        entrypoint(b"node\0/cli.mjs\0--host-main\0/home/host main.cjs\0"),
        Some(b"/cli.mjs".as_slice())
    );
}
