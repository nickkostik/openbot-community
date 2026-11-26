use std::{
    fs,
    io::{self, Write},
    path::{Path, PathBuf},
};

use ring::digest::{SHA1_FOR_LEGACY_USE_ONLY, digest as ring_digest};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::server::{read_bytes, read_json};

use super::history::{MAX_SUMMARY_BYTES, MAX_TRANSCRIPT_BYTES, Receipt, digest};

const MAX_STATE_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, rename_all = "camelCase")]
pub struct Saved {
    pub head_hash: String,
    pub last_window: usize,
    pub trim: Option<Receipt>,
    pub fallback: String,
    pub brief: Option<Brief>,
    pub pending: Option<Pending>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Brief {
    pub receipt: Receipt,
    pub text: String,
    pub source: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Pending {
    pub id: String,
    pub receipt: Receipt,
    pub routing_key: String,
    pub source_hash: String,
    pub cursor: usize,
    pub text: String,
    pub failures: u32,
    pub next_attempt_ms: u64,
}

pub struct Store {
    dir: PathBuf,
}

impl Store {
    pub fn new(root: &Path) -> Self {
        Self {
            dir: root.join("openbot/compact-state"),
        }
    }

    fn state_path(&self, key: &str) -> PathBuf {
        self.dir.join(format!("{key}.rust.json"))
    }

    pub fn load(&self, key: &str) -> io::Result<Option<Saved>> {
        private_bytes(&self.state_path(key), MAX_STATE_BYTES)?
            .map(|data| serde_json::from_slice(&data).map_err(io::Error::from))
            .transpose()
    }

    pub fn save(&self, key: &str, state: &Saved) -> io::Result<()> {
        let data = serde_json::to_vec(state)?;
        if data.len() as u64 > MAX_STATE_BYTES {
            return Err(io::ErrorKind::FileTooLarge.into());
        }
        self.write(&self.state_path(key), &data)
    }

    fn transcript_path(&self, key: &str, id: &str) -> Option<PathBuf> {
        (id.len() == 36
            && id
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() || byte == b'-'))
        .then(|| self.dir.join(format!("{key}.{id}.rust.transcript")))
    }

    pub fn save_source(&self, key: &str, id: &str, source: &str) -> io::Result<()> {
        if source.len() > MAX_TRANSCRIPT_BYTES {
            return Err(io::ErrorKind::FileTooLarge.into());
        }
        let path = self
            .transcript_path(key, id)
            .ok_or(io::ErrorKind::InvalidInput)?;
        self.write(&path, source.as_bytes())
    }

    pub fn source(&self, key: &str, job: &Pending) -> io::Result<String> {
        let path = self
            .transcript_path(key, &job.id)
            .ok_or(io::ErrorKind::InvalidInput)?;
        let bytes =
            private_bytes(&path, MAX_TRANSCRIPT_BYTES as u64)?.ok_or(io::ErrorKind::NotFound)?;
        let source = String::from_utf8(bytes).map_err(|_| io::ErrorKind::InvalidData)?;
        if digest(&[&source]) != job.source_hash
            || !source.is_char_boundary(job.cursor)
            || job.cursor >= source.len()
        {
            return Err(io::ErrorKind::InvalidData.into());
        }
        Ok(source)
    }

    pub fn discard_source(&self, key: &str, id: &str) {
        if let Some(path) = self.transcript_path(key, id) {
            let _ = fs::remove_file(path);
        }
    }

    pub fn legacy(&self, conversation: Option<&str>) -> Option<Saved> {
        let identity = conversation?.trim();
        if identity.is_empty() {
            return None;
        }
        let legacy_hash = ring_digest(
            &SHA1_FOR_LEGACY_USE_ONLY,
            format!("conversation\0{identity}").as_bytes(),
        );
        let hash: String = legacy_hash.as_ref()[..8]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let path = self.dir.join(format!("{hash}.json"));
        if !fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.is_file()) {
            return None;
        }
        let raw = read_json(&path, MAX_STATE_BYTES).ok().flatten()?;
        let trim = legacy_receipt(
            &raw,
            "compactedPrefixCount",
            "compactedPrefixHash",
            "compactedPrefixHashes",
        );
        let brief = legacy_receipt(
            &raw,
            "summaryForDropped",
            "summaryPrefixHash",
            "summaryPrefixHashes",
        )
        .zip(
            raw["summary"]
                .as_str()
                .filter(|text| !text.trim().is_empty() && text.len() <= MAX_SUMMARY_BYTES),
        )
        .and_then(|(receipt, text)| {
            let source = raw["summarySource"].as_str().unwrap_or_default();
            (!source.is_empty() && !super::is_kimi_k3(source) && !super::retired(source)).then(
                || Brief {
                    receipt,
                    text: text.into(),
                    source: source.into(),
                },
            )
        });
        Some(Saved {
            last_window: raw["lastContextTokens"]
                .as_u64()
                .unwrap_or(0)
                .try_into()
                .unwrap_or(0),
            trim,
            brief,
            ..Saved::default()
        })
    }

    fn write(&self, path: &Path, bytes: &[u8]) -> io::Result<()> {
        fs::create_dir_all(&self.dir)?;
        let mut file = tempfile::NamedTempFile::new_in(&self.dir)?;
        file.write_all(bytes)?;
        file.as_file().sync_all()?;
        file.persist(path).map_err(|error| error.error)?;
        #[cfg(unix)]
        fs::File::open(&self.dir)?.sync_all()?;
        Ok(())
    }
}

fn private_bytes(path: &Path, max: u64) -> io::Result<Option<Vec<u8>>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => read_bytes(path, max),
        Ok(_) => Err(io::ErrorKind::InvalidInput.into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn legacy_receipt(raw: &Value, count: &str, hash: &str, rows: &str) -> Option<Receipt> {
    Some(Receipt {
        count: raw[count].as_u64()?.try_into().ok()?,
        hash: raw[hash].as_str()?.into(),
        messages: raw[rows]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect(),
    })
}
