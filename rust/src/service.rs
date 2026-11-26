use std::{
    fs::{self, File},
    io::{self, Write},
    path::{Path, PathBuf},
};

use crate::lease::Lease;

#[cfg(target_os = "linux")]
pub(crate) mod deploy;

const OWNER: &str = "openbot-ui.pid";

pub(crate) struct PublishedOwner {
    path: PathBuf,
    pid: u32,
    _lease: Lease,
}

impl PublishedOwner {
    // The caller already holds the bound loopback socket and has prepared the service.
    pub(crate) fn publish(root: &Path) -> io::Result<Self> {
        let lease = Lease::acquire(&root.join(".openbot-rust-service.lock"))?;
        let path = root.join(OWNER);
        let pid = std::process::id();
        replace(&path, format!("{pid}\n").as_bytes())?;
        Ok(Self {
            path,
            pid,
            _lease: lease,
        })
    }
}

impl Drop for PublishedOwner {
    fn drop(&mut self) {
        if read_owner(&self.path) == Some(self.pid) {
            let _ = fs::remove_file(&self.path);
        }
    }
}

fn read_owner(path: &Path) -> Option<u32> {
    let bytes = crate::server::read_bytes(path, 32).ok()??;
    std::str::from_utf8(&bytes).ok()?.trim().parse().ok()
}

fn replace(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = path.parent().ok_or(io::ErrorKind::InvalidInput)?;
    let mut temp = tempfile::NamedTempFile::new_in(parent)?;
    temp.write_all(bytes)?;
    temp.as_file().sync_all()?;
    temp.persist(path)?;
    File::open(parent)?.sync_all()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn publishes_exactly_one_owner_and_never_removes_a_new_receipt() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join(OWNER);
        fs::write(&path, "1\n").unwrap();
        let owner = PublishedOwner::publish(root.path()).unwrap();
        assert_eq!(read_owner(&path), Some(std::process::id()));
        assert!(PublishedOwner::publish(root.path()).is_err());
        fs::write(&path, format!("{}\n", std::process::id() + 1)).unwrap();
        drop(owner);
        assert!(path.is_file());
        let owner = PublishedOwner::publish(root.path()).unwrap();
        drop(owner);
        assert!(!path.exists());
    }
}
