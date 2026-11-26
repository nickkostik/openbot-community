use std::{
    fs::{File, OpenOptions},
    io,
    path::Path,
};

pub struct Lease(File);

impl Lease {
    pub fn acquire(path: &Path) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        file.try_lock()?;
        Ok(Self(file))
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        // A child between fork and exec can briefly retain an open descriptor.
        let _ = self.0.unlock();
    }
}

#[cfg(all(test, unix))]
#[test]
fn dropping_a_lease_releases_the_lock_with_a_duplicate_descriptor_open() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("writer.lock");
    let lease = Lease::acquire(&path).unwrap();
    let inherited = lease.0.try_clone().unwrap();
    assert!(Lease::acquire(&path).is_err());
    drop(lease);
    let replacement = Lease::acquire(&path).unwrap();
    drop(inherited);
    assert!(Lease::acquire(&path).is_err());
    drop(replacement);
}
