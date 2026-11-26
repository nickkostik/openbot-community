use std::path::Path;

use serde_json::Value;

use crate::server::read_json;

const REDACTED: &str = "[redacted]";

pub struct Redactor(Vec<String>);

impl Redactor {
    pub fn load(root: &Path, active: Option<String>) -> Self {
        let mut secrets: Vec<_> = active.into_iter().collect();
        if let Ok(Some(store)) = read_json(&root.join("secrets.json"), 4 * 1024 * 1024)
            && let Some(providers) = store["providers"].as_object()
        {
            for value in providers.values().filter_map(Value::as_str) {
                secrets.push(value.to_owned());
                if let Ok(Value::Object(fields)) = serde_json::from_str::<Value>(value) {
                    fields
                        .iter()
                        .filter(|(key, _)| sensitive(key))
                        .filter_map(|(_, value)| value.as_str())
                        .for_each(|value| secrets.push(value.to_owned()));
                }
            }
        }
        secrets.retain(|secret| !secret.is_empty());
        secrets.sort_unstable_by(|a, b| b.len().cmp(&a.len()).then_with(|| a.cmp(b)));
        secrets.dedup();
        Self(secrets)
    }

    pub fn value(&self, value: &mut Value) {
        match value {
            Value::String(text) => *text = self.text(text),
            Value::Array(items) => items.iter_mut().for_each(|item| self.value(item)),
            Value::Object(fields) => {
                let source = std::mem::take(fields);
                for (key, mut value) in source {
                    if sensitive(&key) {
                        value = Value::String(REDACTED.into());
                    } else {
                        self.value(&mut value);
                    }
                    fields.insert(self.text(&key), value);
                }
            }
            _ => {}
        }
    }

    fn text(&self, text: &str) -> String {
        let mut out = text.to_owned();
        for secret in &self.0 {
            if out.contains(secret) {
                out = out.replace(secret, REDACTED);
            }
        }
        // Also remove credential-like values typed by the client or echoed by
        // a provider, including keys that were never saved on this machine.
        let mut sanitized = String::with_capacity(out.len());
        let mut bearer = false;
        for segment in out.split_inclusive(char::is_whitespace) {
            let word = segment.trim_end();
            let whitespace = &segment[word.len()..];
            let token = word
                .split(|character: char| {
                    !(character.is_ascii_alphanumeric() || matches!(character, '_' | '-'))
                })
                .any(|field| {
                    ["sk-", "ocg_"].iter().any(|prefix| {
                        field
                            .strip_prefix(prefix)
                            .is_some_and(|rest| rest.len() >= 5)
                    })
                });
            if !word.is_empty() {
                sanitized.push_str(if token || bearer { REDACTED } else { word });
                bearer = word
                    .trim_matches(|character: char| !character.is_ascii_alphanumeric())
                    .eq_ignore_ascii_case("bearer");
            }
            sanitized.push_str(whitespace);
        }
        sanitized
    }
}

fn sensitive(key: &str) -> bool {
    let key: String = key
        .bytes()
        .filter(|byte| byte.is_ascii_alphanumeric())
        .map(|byte| byte.to_ascii_lowercase() as char)
        .collect();
    matches!(
        key.as_str(),
        "authorization"
            | "proxyauthorization"
            | "apikey"
            | "xapikey"
            | "accesstoken"
            | "refreshtoken"
            | "idtoken"
            | "clientsecret"
            | "password"
            | "cookie"
            | "setcookie"
            | "credential"
            | "token"
    )
}
