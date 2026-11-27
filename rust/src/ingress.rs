use std::{
    collections::BTreeMap,
    io,
    path::Path,
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use axum::http::{HeaderMap, Method, StatusCode, header};
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::Mutex;
use url::Url;

use crate::jwt::Token;

const ASSERTION: &str = "cf-access-jwt-assertion";
const MAX_CONFIG: u64 = 16 * 1024;
const MAX_KEYS: usize = 256 * 1024;
const KEY_TTL: Duration = Duration::from_secs(300);
const RETRY_KEYS: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub(crate) struct Policy {
    authority: String,
    access: Option<Arc<Access>>,
}

impl From<&str> for Policy {
    fn from(authority: &str) -> Self {
        Self {
            authority: authority.into(),
            access: None,
        }
    }
}

impl From<String> for Policy {
    fn from(authority: String) -> Self {
        Self {
            authority,
            access: None,
        }
    }
}

impl Policy {
    pub(crate) fn new(authority: String, config: Option<&Path>) -> io::Result<Self> {
        let access = config
            .map(|path| {
                let (_, settings) = read_config(path)?;
                Access::new(settings).map(Arc::new)
            })
            .transpose()?;
        Ok(Self { authority, access })
    }

    pub(crate) fn public_url(&self) -> Option<&str> {
        Some(&self.access.as_ref()?.settings.primary)
    }

    pub(crate) async fn authorize(
        &self,
        headers: &HeaderMap,
        method: &Method,
        path: &str,
    ) -> Result<(), Denial> {
        let host = single(headers, header::HOST)?.ok_or(Denial::Origin)?;
        let origin = single(headers, header::ORIGIN)?;
        if host == self.authority {
            if origin.is_some_and(|origin| origin != format!("http://{host}"))
                || [
                    ASSERTION,
                    "cf-ray",
                    "cf-connecting-ip",
                    "forwarded",
                    "x-forwarded-host",
                    "x-forwarded-proto",
                    "x-forwarded-for",
                ]
                .iter()
                .any(|name| headers.contains_key(*name))
            {
                return Err(Denial::Origin);
            }
            return Ok(());
        }
        let access = self.access.as_ref().ok_or(Denial::Origin)?;
        let site = access.settings.sites.get(host).ok_or(Denial::Origin)?;
        if origin.is_some_and(|origin| origin != site.origin)
            || (origin.is_none() && !matches!(*method, Method::GET | Method::HEAD))
        {
            return Err(Denial::Origin);
        }
        if !matches!(
            path,
            "/" | "/app.js" | "/styles.css" | "/favicon.svg" | "/healthz"
        ) && (!path.starts_with("/api/") || path.starts_with("/api/oauth/"))
        {
            return Err(Denial::LocalOnly);
        }
        let token = single(headers, header::HeaderName::from_static(ASSERTION))?
            .and_then(Token::parse)
            .ok_or(Denial::Token)?;
        access.verify(&token, &site.audience).await
    }
}

fn single(headers: &HeaderMap, name: header::HeaderName) -> Result<Option<&str>, Denial> {
    let all = headers.get_all(name);
    let mut values = all.iter();
    match (values.next(), values.next()) {
        (None, None) => Ok(None),
        (Some(value), None) => value.to_str().map(Some).map_err(|_| Denial::Origin),
        _ => Err(Denial::Origin),
    }
}

#[derive(Debug)]
pub(crate) enum Denial {
    Origin,
    LocalOnly,
    Token,
    Keys,
}

impl Denial {
    pub(crate) fn response(&self) -> (StatusCode, &'static str) {
        match self {
            Self::Origin => (
                StatusCode::FORBIDDEN,
                "OpenBot requires a configured panel origin.",
            ),
            Self::LocalOnly => (
                StatusCode::FORBIDDEN,
                "Open this feature from the OpenBot loopback panel.",
            ),
            Self::Token => (
                StatusCode::FORBIDDEN,
                "OpenBot requires a valid Cloudflare Access session.",
            ),
            Self::Keys => (
                StatusCode::SERVICE_UNAVAILABLE,
                "Cloudflare Access signing keys are temporarily unavailable.",
            ),
        }
    }
}

struct Access {
    settings: Config,
    url: Url,
    client: reqwest::Client,
    keys: Mutex<Keys>,
}

#[derive(Default)]
struct Keys {
    value: Value,
    updated: Option<Instant>,
    attempted: Option<Instant>,
}

impl Access {
    fn new(settings: Config) -> io::Result<Self> {
        let url = Url::parse(&format!("{}/cdn-cgi/access/certs", settings.issuer))
            .map_err(io::Error::other)?;
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(Duration::from_secs(4))
            .build()
            .map_err(io::Error::other)?;
        Ok(Self {
            settings,
            url,
            client,
            keys: Mutex::default(),
        })
    }

    async fn download_keys(&self) -> Option<Value> {
        let mut result = self.client.get(self.url.clone()).send().await.ok()?;
        if !result.status().is_success() {
            return None;
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = result.chunk().await.ok()? {
            if bytes.len() + chunk.len() > MAX_KEYS {
                return None;
            }
            bytes.extend_from_slice(&chunk);
        }
        let keys: Value = serde_json::from_slice(&bytes).ok()?;
        let rows = keys["keys"].as_array()?;
        (1..=32).contains(&rows.len()).then_some(keys)
    }

    async fn verify(&self, token: &Token<'_>, audience: &str) -> Result<(), Denial> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let claims = &token.claims;
        if claims["iss"] != self.settings.issuer
            || claims["type"] != "app"
            || !claims["aud"].as_array().is_some_and(|items| {
                items.iter().all(Value::is_string) && items.iter().any(|value| value == audience)
            })
            || claims["exp"].as_u64().is_none_or(|expiry| expiry <= now)
            || ["iat", "nbf"].iter().any(|name| {
                claims
                    .get(*name)
                    .is_some_and(|value| value.as_u64().is_none_or(|start| start > now))
            })
        {
            return Err(Denial::Token);
        }
        let mut keys = self.keys.lock().await;
        let fresh = |keys: &Keys| keys.updated.is_some_and(|time| time.elapsed() < KEY_TTL);
        let known = keys.value["keys"]
            .as_array()
            .is_some_and(|rows| rows.iter().any(|key| key["kid"] == token.kid));
        if (!fresh(&keys) || !known)
            && keys
                .attempted
                .is_none_or(|time| time.elapsed() >= RETRY_KEYS)
        {
            keys.attempted = Some(Instant::now());
            if let Some(updated) = self.download_keys().await {
                keys.value = updated;
                keys.updated = Some(Instant::now());
            }
        }
        if !fresh(&keys) {
            return Err(Denial::Keys);
        }
        if !token.verify(&keys.value) {
            return Err(Denial::Token);
        }
        Ok(())
    }
}

struct Config {
    issuer: String,
    primary: String,
    sites: BTreeMap<String, Site>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct FileConfig {
    team_domain: String,
    origins: Vec<Site>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Site {
    origin: String,
    audience: String,
}

fn read_config(path: &Path) -> io::Result<(Vec<u8>, Config)> {
    if !path.symlink_metadata()?.is_file() {
        return Err(invalid_config());
    }
    let bytes = crate::server::read_bytes(path, MAX_CONFIG)?.ok_or(io::ErrorKind::NotFound)?;
    let config = Config::parse(&bytes)?;
    Ok((bytes, config))
}

#[cfg(target_os = "linux")]
pub(crate) fn release_config(path: &Path) -> io::Result<Vec<u8>> {
    read_config(path).map(|(bytes, _)| bytes)
}

impl Config {
    fn parse(bytes: &[u8]) -> io::Result<Self> {
        let source: FileConfig = serde_json::from_slice(bytes).map_err(|_| invalid_config())?;
        let team = domain(&source.team_domain).ok_or_else(invalid_config)?;
        if team
            .strip_suffix(".cloudflareaccess.com")
            .is_none_or(|prefix| prefix.contains('.'))
            || !(1..=16).contains(&source.origins.len())
        {
            return Err(invalid_config());
        }
        let mut sites = BTreeMap::new();
        let primary = source.origins[0].origin.clone();
        for site in source.origins {
            let host = domain(&site.origin).ok_or_else(invalid_config)?;
            if site.audience.len() != 64
                || !site.audience.bytes().all(|byte| byte.is_ascii_hexdigit())
                || sites.insert(host, site).is_some()
            {
                return Err(invalid_config());
            }
        }
        Ok(Self {
            issuer: format!("https://{team}"),
            primary,
            sites,
        })
    }
}

fn domain(value: &str) -> Option<String> {
    let host = value.strip_prefix("https://")?;
    if !host.contains('.')
        || host.len() > 253
        || !host.split('.').all(|label| {
            !label.is_empty()
                && label.len() <= 63
                && !label.starts_with('-')
                && !label.ends_with('-')
                && label
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        })
    {
        return None;
    }
    match Url::parse(value).ok()?.host()? {
        url::Host::Domain(canonical) if canonical == host => Some(host.into()),
        _ => None,
    }
}

fn invalid_config() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "invalid Cloudflare Access panel configuration",
    )
}

#[cfg(test)]
mod tests;
