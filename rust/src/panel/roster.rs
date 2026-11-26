use std::{
    collections::{BTreeSet, HashSet},
    ffi::OsStr,
    fs, io,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use serde_json::Value;

use crate::{
    logs::historical_bot_ids,
    server::{BOT_MODELS_MAX_BYTES, read_json},
};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Bot {
    bot_id: String,
    bot_name: String,
    deleted: bool,
    hidden: bool,
    updated_at_ms: Option<u64>,
    created_at_ms: Option<u64>,
}

pub fn paused(root: &Path) -> Vec<String> {
    let saved = read_json(&root.join("openbot-pause-bots.json"), 1024 * 1024)
        .ok()
        .flatten()
        .unwrap_or_default();
    let mut seen = HashSet::new();
    saved["pausedBotIds"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter(|id| !id.trim().is_empty() && seen.insert(*id))
        .map(str::to_owned)
        .collect()
}

pub fn read(root: &Path, agent_data: &Path) -> Vec<Bot> {
    let agents = agent_data.join("agents");
    let mut ids = BTreeSet::new();
    if let Ok(entries) = fs::read_dir(&agents) {
        ids.extend(
            entries
                .flatten()
                .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
                .filter_map(|entry| entry.file_name().into_string().ok()),
        );
    }
    ids.extend(paused(root));
    ids.extend(historical_bot_ids(root));
    if let Ok(Some(saved)) = read_json(&root.join("openbot-bot-models.json"), BOT_MODELS_MAX_BYTES)
        && let Some(assignments) = saved["assignments"].as_object()
    {
        ids.extend(assignments.keys().cloned());
    }
    let mut bots: Vec<_> = ids
        .into_iter()
        .filter(|id| id.len() <= 128 && Path::new(id).file_name() == Some(OsStr::new(id)))
        .map(|id| row(&agents, id))
        .collect();
    bots.sort_by(|left, right| {
        left.deleted
            .cmp(&right.deleted)
            .then_with(|| right.updated_at_ms.cmp(&left.updated_at_ms))
            .then_with(|| right.created_at_ms.cmp(&left.created_at_ms))
            .then_with(|| {
                left.bot_name
                    .to_lowercase()
                    .cmp(&right.bot_name.to_lowercase())
            })
    });
    bots
}

fn row(agents: &Path, id: String) -> Bot {
    let mut bot = Bot {
        bot_name: id.clone(),
        bot_id: id,
        deleted: true,
        hidden: false,
        updated_at_ms: None,
        created_at_ms: None,
    };
    let dir = agents.join(&bot.bot_id);
    let meta = match fs::symlink_metadata(&dir) {
        Ok(meta) if meta.is_dir() => meta,
        Err(error) if error.kind() != io::ErrorKind::NotFound => {
            bot.deleted = false;
            return bot;
        }
        _ => return bot,
    };
    bot.updated_at_ms = milliseconds(meta.modified());
    bot.hidden = profile(&dir.join("settings.json"))
        .is_ok_and(|settings| settings["hiddenFromSidebar"] == true);
    let path = dir.join("profile.json");
    let meta = match fs::symlink_metadata(&path) {
        Ok(meta) if meta.is_file() => meta,
        Err(error) if error.kind() != io::ErrorKind::NotFound => {
            bot.deleted = false;
            return bot;
        }
        _ => return bot,
    };
    bot.created_at_ms = milliseconds(meta.created());
    match read_json(&path, 1024 * 1024) {
        Ok(Some(profile)) => {
            bot.deleted = false;
            if let Some(name) = profile["name"]
                .as_str()
                .map(str::trim)
                .filter(|name| !name.is_empty())
            {
                bot.bot_name = name.into();
            }
        }
        Err(error) if error.kind() != io::ErrorKind::InvalidData => bot.deleted = false,
        _ => {}
    }
    bot
}

fn profile(path: &Path) -> io::Result<Value> {
    if !fs::symlink_metadata(path)?.is_file() {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    Ok(read_json(path, 1024 * 1024)?.unwrap_or_default())
}

fn milliseconds(time: io::Result<SystemTime>) -> Option<u64> {
    time.ok()?
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_millis()
        .try_into()
        .ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_live_hidden_paused_and_archived_agents_by_original_id() {
        let data = tempfile::tempdir().unwrap();
        let profiles = data.path().join("agent-data");
        let root = data.path().join("sand");
        let live = profiles.join("agents/live");
        fs::create_dir_all(&live).unwrap();
        fs::create_dir(&root).unwrap();
        fs::write(live.join("profile.json"), r#"{"name":" Example "}"#).unwrap();
        fs::write(live.join("settings.json"), r#"{"hiddenFromSidebar":true}"#).unwrap();
        fs::write(
            root.join("openbot-pause-bots.json"),
            r#"{"pausedBotIds":["live","archived","live"]}"#,
        )
        .unwrap();
        fs::write(
            root.join("openbot-bot-models.json"),
            r#"{"assignments":{"assigned":"fixture:model","../other":"fixture:model"}}"#,
        )
        .unwrap();
        fs::write(
            root.join("openbot-requests.jsonl"),
            r#"{"id":"log","botId":"logged"}"#,
        )
        .unwrap();
        let rows = read(&root, &profiles);
        assert_eq!(rows[0].bot_name, "Example");
        assert!(rows[0].hidden);
        assert!(rows[0].updated_at_ms.is_some());
        assert!(!rows[0].deleted);
        assert_eq!(paused(&root), ["live", "archived"]);
        assert_eq!(
            rows[1..]
                .iter()
                .map(|bot| bot.bot_id.as_str())
                .collect::<Vec<_>>(),
            ["archived", "assigned", "logged"]
        );
        assert!(rows[1..].iter().all(|bot| bot.deleted));
    }
}
