use std::{io, time::Duration};

use serde_json::Value;
use url::Url;

use super::{
    AuthError,
    credential::{self, CODEX_CLIENT},
    unix_seconds,
};
use crate::routing::CredentialKind;

const ISSUER: &str = "https://auth.openai.com";
const MAX_OPENAI_REPLY: usize = 1024 * 1024;

#[derive(Clone)]
pub struct Network {
    client: reqwest::Client,
    pub plan_authorize: Url,
    pub codex_authorize: Url,
    pub plan_token: Url,
    pub codex_token: Url,
    pub jwks: Url,
}

impl Network {
    pub fn new() -> io::Result<Self> {
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(Duration::from_secs(15))
            .build()
            .map_err(io::Error::other)?;
        let endpoint = |path| Url::parse(&format!("{ISSUER}{path}")).map_err(io::Error::other);
        Ok(Self {
            client,
            plan_authorize: endpoint("/api/accounts/authorize")?,
            codex_authorize: endpoint("/oauth/authorize")?,
            plan_token: endpoint("/api/accounts/oauth/token")?,
            codex_token: endpoint("/oauth/token")?,
            jwks: endpoint("/.well-known/jwks.json")?,
        })
    }

    pub async fn exchange(
        &self,
        kind: CredentialKind,
        fields: &[(&str, &str)],
    ) -> Result<Value, AuthError> {
        let endpoint = if kind == CredentialKind::ChatGptPlan {
            &self.plan_token
        } else {
            &self.codex_token
        };
        self.json(self.client.post(endpoint.clone()).form(fields))
            .await
    }

    pub async fn refresh(&self, oauth: &credential::OAuth) -> Result<Value, AuthError> {
        let refresh = credential::text(&oauth.record["refreshToken"]).ok_or_else(|| {
            AuthError::unavailable("The saved OpenAI sign-in has no refresh token. Sign in again.")
        })?;
        let mut form = vec![("grant_type", "refresh_token"), ("refresh_token", refresh)];
        if oauth.kind == CredentialKind::ChatGptPlan {
            form.push((
                "client_id",
                credential::text(&oauth.record["clientId"]).unwrap_or_default(),
            ));
            form.push(("resource", credential::RESOURCE));
        } else {
            form.push(("client_id", CODEX_CLIENT));
        }
        self.exchange(oauth.kind, &form).await
    }

    async fn json(&self, request: reqwest::RequestBuilder) -> Result<Value, AuthError> {
        let mut response = request
            .header(reqwest::header::ACCEPT, "application/json")
            .send()
            .await
            .map_err(|_| AuthError::unavailable("OpenBot could not reach OpenAI sign-in."))?;
        if !response.status().is_success() {
            let status = response.status().as_u16();
            let mut error = AuthError::unavailable(format!(
                "OpenAI rejected the credential request (HTTP {status})."
            ));
            error.upstream = Some(status);
            return Err(error);
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| {
            AuthError::unavailable("OpenAI sign-in did not finish sending its response.")
        })? {
            if body.len() + chunk.len() > MAX_OPENAI_REPLY {
                return Err(AuthError::unavailable(
                    "OpenAI sign-in returned an oversized response.",
                ));
            }
            body.extend_from_slice(&chunk);
        }
        serde_json::from_slice::<Value>(&body)
            .ok()
            .filter(Value::is_object)
            .ok_or_else(|| AuthError::unavailable("OpenAI sign-in returned an invalid response."))
    }

    pub async fn verified_subject(
        &self,
        token: &str,
        client: &str,
        nonce: &str,
    ) -> Result<String, AuthError> {
        let invalid =
            || AuthError::input("OpenAI's ChatGPT account identity could not be verified.");
        let token = crate::jwt::Token::parse(token).ok_or_else(invalid)?;
        let claims_data = &token.claims;
        let subject = credential::text(&claims_data["sub"]).ok_or_else(invalid)?;
        let now = unix_seconds();
        let authorized_party = claims_data.get("azp");
        let audience = &claims_data["aud"];
        let audience_matches = audience == client
            || audience.as_array().is_some_and(|items| {
                items.iter().all(Value::is_string)
                    && items.iter().any(|item| item == client)
                    && (items.len() == 1 || authorized_party.is_some_and(|value| value == client))
            });
        if claims_data["iss"] != ISSUER
            || claims_data["nonce"] != nonce
            || credential::seconds(&claims_data["exp"]).is_none_or(|expiry| expiry <= now)
            || claims_data
                .get("nbf")
                .is_some_and(|value| credential::seconds(value).is_none_or(|seconds| seconds > now))
            || !audience_matches
            || authorized_party.is_some_and(|value| value != client)
        {
            return Err(invalid());
        }
        let body = self
            .json(self.client.get(self.jwks.clone()))
            .await
            .map_err(|_| invalid())?;
        if !token.verify(&body) {
            return Err(invalid());
        }
        Ok(subject.into())
    }
}
