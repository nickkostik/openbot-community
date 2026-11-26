mod models;

use std::{collections::HashSet, fs, path::Path};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::{
    AuthError, CredentialStore, PLAN_MAX_BYTES, STORE_MAX_BYTES, Value, json, raw_secret,
    read_bytes, read_json, read_mode, save_reply, stage, store_error,
};
use crate::{
    auth::credential::{Credential, openai_origin, text},
    catalog::{Catalog, Model, Protocol, Provider},
    routing::{is_direct_provider, parse_origin},
};

const PLAN: &str = "openbot-plan.json";
const MOVE: &str = ".openbot-rust-provider-move.json";
const TOKEN_PLAN: &str = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1";

#[derive(Deserialize, Serialize)]
struct ProviderMove {
    before_plan: String,
    next_plan: Value,
    provider: String,
    before_key: String,
    next_key: String,
}

impl CredentialStore {
    pub(in crate::auth) fn edit_catalog(
        &self,
        command: &Value,
        secret: Option<&str>,
        port: u16,
    ) -> Result<Value, AuthError> {
        let _write = self.write_lock()?;
        let before =
            read_bytes(&self.root().join(PLAN), PLAN_MAX_BYTES).map_err(|_| store_error())?;
        let mut plan = self.plan()?;
        let old_plan = plan.clone();
        if before.is_none() {
            plan["hop"]["port"] = json!(port);
            plan["hopBaseUrl"] = json!(format!("http://127.0.0.1:{port}/v1"));
        }
        let mut secrets = self.load()?;
        let mode = read_mode(self.root()).map_err(|_| store_error())?;
        let mut created = None;
        let mut delete_empty_plan = false;
        let mut replacement = None;
        let mut crossing = false;
        match command["kind"].as_str() {
            Some("add-provider") => {
                let family = provider_id(field(command, "familyId")?)?;
                let id = new_provider_id(&plan, &secrets, family)?;
                let row = provider_row(command, &id, None)?;
                let secret = secret.ok_or_else(|| AuthError::input("A credential is required."))?;
                replacement = Some((
                    id.clone(),
                    provider_credential(
                        &id,
                        secret,
                        row["origin"].as_str().ok_or_else(store_error)?,
                    )?,
                ));
                providers(&mut plan)?.push(row);
                created = Some(id);
            }
            Some("update-provider" | "upsert-provider" | "custom") => {
                let upsert = command["kind"] != "update-provider";
                if upsert && mode != "custom" {
                    return Err(mode_conflict());
                }
                let generated;
                let id = if upsert {
                    generated = slugify(field(command, "name")?);
                    provider_id(&generated)?
                } else {
                    provider_id(field(command, "providerId")?)?
                };
                let rows = providers(&mut plan)?;
                let index = rows.iter().position(|row| row["id"] == id);
                if index.is_none() && (!upsert || raw_secret(&secrets, id).is_some()) {
                    return Err(AuthError::input(
                        "Select an existing provider or add a new account.",
                    ));
                }
                let existing = index.map(|index| &rows[index]);
                let row = provider_row(command, id, existing)?;
                let origin = row["origin"].as_str().ok_or_else(store_error)?;
                let existing_key = raw_secret(&secrets, id);
                crossing = existing_key.is_some()
                    && existing.is_some_and(|row| {
                        let old = row["origin"].as_str().and_then(parse_origin);
                        old.map(|old| old.origin())
                            != parse_origin(origin).map(|next| next.origin())
                    });
                if crossing && secret.is_none() {
                    return Err(AuthError::input("Enter a credential for the new endpoint."));
                }
                if upsert && secret.is_none() && existing_key.is_none() {
                    return Err(AuthError::input("A credential is required."));
                }
                replacement = secret
                    .map(|secret| Ok((id.to_owned(), provider_credential(id, secret, origin)?)))
                    .transpose()?;
                if let Some(index) = index {
                    rows[index] = row;
                } else {
                    rows.push(row);
                }
                if upsert {
                    let slug = command["modelSlug"]
                        .as_str()
                        .ok_or_else(|| AuthError::input("A modelSlug field is required."))?
                        .trim();
                    if !slug.is_empty() {
                        models::upsert(&mut plan, id, slug, command, true)?;
                    }
                }
            }
            Some("upsert-model") => models::upsert(
                &mut plan,
                provider_id(field(command, "providerId")?)?,
                field(command, "slug")?,
                command,
                false,
            )?,
            Some("remove-model") => {
                let id = field(command, "modelId")?;
                models::remove(&mut plan, id, false, mode)?;
            }
            Some("remove-provider") => {
                let id = provider_id(field(command, "providerId")?)?;
                let rows = providers(&mut plan)?;
                let index = rows
                    .iter()
                    .position(|row| row["id"] == id)
                    .ok_or_else(|| AuthError::input("Select an existing provider."))?;
                rows.remove(index);
                delete_empty_plan = rows.is_empty();
                models::remove(&mut plan, id, true, mode)?;
                if delete_empty_plan && mode == "custom" {
                    return Err(mode_conflict());
                }
            }
            Some("official" | "set-expose") => return Err(mode_conflict()),
            _ => return Err(AuthError::input("Unknown OpenBot save action.")),
        }
        let plan_changed = plan != old_plan;
        let staged_plan = (plan_changed && !delete_empty_plan)
            .then(|| stage(self.root(), &plan, PLAN_MAX_BYTES))
            .transpose()?;
        let plan_is_current = || -> Result<bool, AuthError> {
            Ok(
                read_bytes(&self.root().join(PLAN), PLAN_MAX_BYTES).map_err(|_| store_error())?
                    == before,
            )
        };
        if !plan_is_current()? {
            return Err(AuthError::conflict(
                "The catalog changed during the save. Try again.",
            ));
        }
        if let Some((id, secret)) = replacement {
            let old_key = raw_secret(&secrets, &id);
            if crossing {
                let pending = ProviderMove {
                    before_plan: digest(before.as_deref().ok_or_else(store_error)?),
                    next_plan: plan.clone(),
                    provider: id.clone(),
                    before_key: digest(old_key.ok_or_else(store_error)?.as_bytes()),
                    next_key: secret.clone(),
                };
                let value = serde_json::to_value(&pending).map_err(|_| store_error())?;
                self.commit(stage(self.root(), &value, MOVE_MAX_BYTES)?, MOVE)?;
                self.recover_provider_move()?;
                secrets = self.load()?;
                return Ok(save_reply(&plan, &secrets, created));
            }
            if old_key != Some(&secret) {
                secrets["providers"][&id] = json!(secret);
                self.store(&secrets)?;
            }
        }
        if delete_empty_plan {
            fs::remove_file(self.root().join(PLAN)).map_err(|_| store_error())?;
            sync(self.root())?;
            plan["catalog"] = json!({"providers":[],"models":[],"bindings":[]});
        } else if let Some(staged) = staged_plan {
            self.commit(staged, PLAN)?;
        }
        Ok(save_reply(&plan, &secrets, created))
    }

    // Publish a credential move as absent-key -> new route -> new key. The
    // receipt permits completion from each point, including after process loss.
    pub(super) fn recover_provider_move(&self) -> Result<(), AuthError> {
        let Some(value) =
            read_json(&self.root().join(MOVE), MOVE_MAX_BYTES).map_err(|_| store_error())?
        else {
            return Ok(());
        };
        let pending: ProviderMove = serde_json::from_value(value).map_err(|_| store_error())?;
        let raw = read_bytes(&self.root().join(PLAN), PLAN_MAX_BYTES)
            .map_err(|_| store_error())?
            .ok_or_else(store_error)?;
        let changing_plan = digest(&raw) == pending.before_plan;
        if !changing_plan
            && serde_json::from_slice::<Value>(&raw).map_err(|_| store_error())?
                != pending.next_plan
        {
            return Err(AuthError::conflict(
                "The provider move conflicts with a newer catalog.",
            ));
        }
        let mut secrets = self.load()?;
        let current = raw_secret(&secrets, &pending.provider);
        if current.is_some_and(|key| {
            digest(key.as_bytes()) != pending.before_key && key != pending.next_key
        }) {
            return Err(AuthError::conflict(
                "The provider move conflicts with a newer credential.",
            ));
        }
        if changing_plan {
            let staged = stage(self.root(), &pending.next_plan, PLAN_MAX_BYTES)?;
            if current.is_some() {
                secrets["providers"]
                    .as_object_mut()
                    .ok_or_else(store_error)?
                    .shift_remove(&pending.provider);
                self.store(&secrets)?;
            }
            self.commit(staged, PLAN)?;
        }
        if raw_secret(&secrets, &pending.provider) != Some(&pending.next_key) {
            secrets["providers"][&pending.provider] = json!(pending.next_key);
            self.store(&secrets)?;
        }
        fs::remove_file(self.root().join(MOVE)).map_err(|_| store_error())?;
        sync(self.root())
    }
}

const MOVE_MAX_BYTES: u64 = PLAN_MAX_BYTES + STORE_MAX_BYTES;

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn sync(root: &Path) -> Result<(), AuthError> {
    #[cfg(unix)]
    fs::File::open(root)
        .and_then(|file| file.sync_all())
        .map_err(|_| store_error())?;
    let _ = root;
    Ok(())
}

fn mode_conflict() -> AuthError {
    AuthError::conflict("This change needs the managed host mode or tunnel controls.")
}

fn providers(plan: &mut Value) -> Result<&mut Vec<Value>, AuthError> {
    plan["catalog"]["providers"]
        .as_array_mut()
        .ok_or_else(store_error)
}

fn provider_id(id: &str) -> Result<&str, AuthError> {
    if id.len() > 64
        || !id.as_bytes().first().is_some_and(u8::is_ascii_alphanumeric)
        || !id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
    {
        return Err(AuthError::input("The provider ID is invalid."));
    }
    Ok(id)
}

fn field<'a>(body: &'a Value, name: &str) -> Result<&'a str, AuthError> {
    text(&body[name]).ok_or_else(|| AuthError::input(format!("{name} is required.")))
}

fn slugify(name: &str) -> String {
    name.split(|character: char| !character.is_ascii_alphanumeric())
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join("-")
        .to_ascii_lowercase()
}

fn new_provider_id(plan: &Value, store: &Value, family: &str) -> Result<String, AuthError> {
    let rows = plan["catalog"]["providers"]
        .as_array()
        .ok_or_else(store_error)?;
    let occupied: HashSet<_> = rows.iter().filter_map(|row| row["id"].as_str()).collect();
    std::iter::once(family.to_owned())
        .chain((2..=10000).map(|number| {
            let suffix = format!("-{number}");
            format!("{}{suffix}", &family[..family.len().min(64 - suffix.len())])
        }))
        .find(|id| !occupied.contains(id.as_str()) && raw_secret(store, id).is_none())
        .ok_or_else(|| AuthError::unavailable("OpenBot has too many IDs for this provider."))
}

fn provider_row(command: &Value, id: &str, existing: Option<&Value>) -> Result<Value, AuthError> {
    let name = field(command, "name")?;
    if name.chars().count() > 128 {
        return Err(AuthError::input(
            "An account name can have up to 128 characters.",
        ));
    }
    let origin = field(command, "origin")?.trim_end_matches('/');
    let protocol = match command.get("apiType").filter(|value| !value.is_null()) {
        Some(value) => Protocol::parse(value)
            .ok_or_else(|| AuthError::input("Select a supported API protocol."))?,
        None => existing
            .and_then(|row| Protocol::parse(&row["apiType"]))
            .unwrap_or_default(),
    };
    let provider = Provider {
        id: id.into(),
        name: Some(name.into()),
        origin: origin.into(),
        protocol,
    };
    if !is_direct_provider(&provider) {
        return Err(AuthError::input(
            "Use a direct provider endpoint. OpenCode accounts require OpenCode Go.",
        ));
    }
    if is_family(id, "openai") && !openai_origin(origin) {
        return Err(AuthError::input(
            "OpenAI accounts require a direct OpenAI endpoint.",
        ));
    }
    if is_family(id, "alibaba-token-plan") && origin != TOKEN_PLAN {
        return Err(AuthError::input(
            "Use the Singapore Alibaba Token Plan base URL for this account.",
        ));
    }
    let mut row = existing.cloned().unwrap_or_else(|| {
        json!({
            "id":id, "maxTokensDefault":65536, "mapFile":"provider-maps.cjs"
        })
    });
    row["name"] = json!(name);
    row["origin"] = json!(origin);
    row["apiType"] = json!(protocol);
    Ok(row)
}

fn is_family(id: &str, family: &str) -> bool {
    let id = id.to_ascii_lowercase();
    id == family
        || id
            .strip_prefix(family)
            .is_some_and(|suffix| suffix.starts_with('-'))
}

pub(super) fn provider_credential(id: &str, raw: &str, origin: &str) -> Result<String, AuthError> {
    if raw.trim() == "oauth" {
        return Err(AuthError::input(
            "Complete sign-in and save the resulting credential.",
        ));
    }
    let saved = Credential::import(raw, origin)?.into_saved();
    if is_family(id, "alibaba-token-plan") && (origin != TOKEN_PLAN || !saved.starts_with("sk-sp-"))
    {
        return Err(AuthError::input(
            "An Alibaba Token Plan account requires its dedicated token-plan endpoint and sk-sp- key.",
        ));
    }
    Ok(saved)
}

pub(super) fn set_wildcard(plan: &mut Value, model: &Model) -> Result<bool, AuthError> {
    let binding = json!({"conversation":{"kind":"wildcard"},"modelId":model.id});
    let bindings = plan["catalog"]["bindings"]
        .as_array_mut()
        .ok_or_else(store_error)?;
    let mut active = bindings
        .iter()
        .filter(|row| row["conversation"]["kind"] == "wildcard");
    let mut changed = active.next() != Some(&binding) || active.next().is_some();
    if changed {
        bindings.retain(|row| row["conversation"]["kind"] != "wildcard");
        bindings.push(binding);
    }
    if plan["agents"].is_null() {
        plan["agents"] = json!({});
    }
    let agents = plan["agents"].as_object_mut().ok_or_else(store_error)?;
    let agent = json!({"modelId":model.slug,"providerId":model.provider_id});
    if agents.get("*") != Some(&agent) {
        agents.insert("*".into(), agent);
        changed = true;
    }
    Ok(changed)
}

fn direct_chat(catalog: &Catalog, model: &Model) -> bool {
    model.wire_chat
        && catalog
            .providers
            .iter()
            .any(|provider| provider.id == model.provider_id && is_direct_provider(provider))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovers_the_same_provider_move_after_each_publication_step() {
        let old_plan = json!({
            "kind":"custom","catalog":{"providers":[{"id":"fixture","origin":"https://old.example.test/v1"}],
            "models":[],"bindings":[]}
        });
        let mut next_plan = old_plan.clone();
        next_plan["catalog"]["providers"][0]["origin"] = json!("https://new.example.test/v1");
        let old_bytes = serde_json::to_vec(&old_plan).unwrap();
        let pending = ProviderMove {
            before_plan: digest(&old_bytes),
            next_plan: next_plan.clone(),
            provider: "fixture".into(),
            before_key: digest(b"fixture-old"),
            next_key: "fixture-next".into(),
        };
        for (plan, key) in [
            (&old_plan, Some("fixture-old")),
            (&old_plan, None),
            (&next_plan, None),
            (&next_plan, Some("fixture-next")),
        ] {
            let directory = tempfile::tempdir().unwrap();
            let root = directory.path();
            let mut secrets = json!({"providers":{"unrelated":"keep"},"extra":[true]});
            if let Some(key) = key {
                secrets["providers"]["fixture"] = json!(key);
            }
            fs::write(root.join(PLAN), serde_json::to_vec(plan).unwrap()).unwrap();
            fs::write(root.join("secrets.json"), secrets.to_string()).unwrap();
            fs::write(root.join(MOVE), serde_json::to_vec(&pending).unwrap()).unwrap();
            let store = CredentialStore::new(root, true).unwrap();
            assert_eq!(store.plan().unwrap(), next_plan);
            assert_eq!(
                store.load().unwrap()["providers"]["fixture"],
                "fixture-next"
            );
            assert_eq!(store.load().unwrap()["providers"]["unrelated"], "keep");
            assert_eq!(store.load().unwrap()["extra"], json!([true]));
            assert!(!root.join(MOVE).exists());
        }
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path();
        let independently_rotated = json!({"providers":{"fixture":"separate-save"}});
        fs::write(root.join(PLAN), &old_bytes).unwrap();
        fs::write(root.join("secrets.json"), independently_rotated.to_string()).unwrap();
        fs::write(root.join(MOVE), serde_json::to_vec(&pending).unwrap()).unwrap();
        assert!(CredentialStore::new(root, true).is_err());
        assert_eq!(fs::read(root.join(PLAN)).unwrap(), old_bytes);
        assert_eq!(
            read_json(&root.join("secrets.json"), STORE_MAX_BYTES).unwrap(),
            Some(independently_rotated)
        );
        assert!(root.join(MOVE).exists());
    }
}
