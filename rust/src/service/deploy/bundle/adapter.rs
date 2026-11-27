use std::{
    collections::BTreeSet,
    fs::{self, File},
    io,
    path::{Path, PathBuf},
};

use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};

use super::{drift, write_new};

const MODULES: [&str; 7] = [
    "bot-models.cjs",
    "image-read.cjs",
    "openai-messages.cjs",
    "openai-stream.cjs",
    "request-log.cjs",
    "runtime.cjs",
    "version.cjs",
];
const PACKAGES: [&str; 2] = ["vendor/jpeg-js", "vendor/pngjs"];
const MAX_ASSET_BYTES: u64 = 512 * 1024;
const MAX_ASSETS: usize = 100;
const STAMP: &str = "version.json";

#[derive(PartialEq, Eq)]
pub(super) struct Payload(Vec<(String, Vec<u8>)>);

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Stamp {
    commit: String,
    runtime: String,
    port: u16,
}

impl Payload {
    pub(super) fn stage(source: &Path, revision: &str, port: u16) -> io::Result<Self> {
        let mut stamp = serde_json::to_vec(&json!({
            "commit": revision,
            "runtime": "rust",
            "port": port,
        }))?;
        stamp.push(b'\n');
        Self::read(source, stamp)
    }

    pub(super) fn read_installed(release: &Path) -> io::Result<Option<Self>> {
        let path = release.join("payload");
        match path.symlink_metadata() {
            Ok(_) => {
                regular_directory(&path)?;
                Self::read(&path, read_file(&path.join(STAMP), 1024)?).map(Some)
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error),
        }
    }

    fn read(root: &Path, stamp: Vec<u8>) -> io::Result<Self> {
        let metadata: Stamp = serde_json::from_slice(&stamp)?;
        if metadata.runtime != "rust"
            || metadata.port == 0
            || !matches!(metadata.commit.len(), 40 | 64)
            || !metadata.commit.bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            return Err(drift("the installed host adapter stamp is invalid"));
        }
        regular_directory(root)?;
        regular_directory(&root.join("vendor"))?;
        let mut names: BTreeSet<String> = MODULES.into_iter().map(str::to_owned).collect();
        for package in PACKAGES {
            names.insert(format!("{package}/package.json"));
            names.insert(format!("{package}/LICENSE"));
            for directory in [package.to_owned(), format!("{package}/lib")] {
                let path = root.join(&directory);
                regular_directory(&path)?;
                for (index, entry) in fs::read_dir(path)?.enumerate() {
                    if index >= MAX_ASSETS {
                        return Err(drift("the host adapter image package has too many files"));
                    }
                    let name = entry?.file_name();
                    if let Some(name) = name.to_str().filter(|name| name.ends_with(".js")) {
                        names.insert(format!("{directory}/{name}"));
                    }
                }
            }
        }
        if names.len() >= MAX_ASSETS
            || !names.contains("vendor/jpeg-js/index.js")
            || !names.contains("vendor/pngjs/lib/png.js")
        {
            return Err(drift("the host adapter image modules are incomplete"));
        }
        let mut files = names
            .into_iter()
            .map(|name| {
                let body = read_file(&root.join(&name), MAX_ASSET_BYTES)?;
                Ok((name, body))
            })
            .collect::<io::Result<Vec<_>>>()?;
        files.push((STAMP.into(), stamp));
        Ok(Self(files))
    }

    pub(super) fn hash(&self, hash: &mut Sha256) {
        for (name, bytes) in &self.0 {
            hash.update(Sha256::digest(format!("payload/{name}")));
            hash.update(Sha256::digest(bytes));
        }
    }

    pub(super) fn install(&self, release: &Path) -> io::Result<()> {
        let root = release.join("payload");
        let mut directories = BTreeSet::<PathBuf>::new();
        for (name, bytes) in &self.0 {
            let path = root.join(name);
            let parent = path.parent().ok_or(io::ErrorKind::InvalidInput)?;
            if !directories.contains(parent) {
                fs::create_dir_all(parent)?;
                directories.extend(
                    parent
                        .ancestors()
                        .take_while(|directory| *directory != release)
                        .map(Path::to_path_buf),
                );
            }
            write_new(path, bytes, 0o600)?;
        }
        for directory in directories.iter().rev() {
            File::open(directory)?.sync_all()?;
        }
        Ok(())
    }
}

fn regular_directory(path: &Path) -> io::Result<()> {
    if !path.symlink_metadata()?.file_type().is_dir() {
        return Err(drift("host adapter assets must be in regular directories"));
    }
    Ok(())
}

fn read_file(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    if !path.symlink_metadata()?.file_type().is_file() {
        return Err(drift("host adapter assets must be regular files"));
    }
    crate::server::read_bytes(path, limit)?.ok_or_else(|| io::ErrorKind::NotFound.into())
}
