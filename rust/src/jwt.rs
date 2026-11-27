use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use ring::signature::{RSA_PKCS1_2048_8192_SHA256, RsaPublicKeyComponents};
use serde_json::Value;

pub(crate) struct Token<'a> {
    message: &'a str,
    signature: Vec<u8>,
    pub(crate) kid: String,
    pub(crate) claims: Value,
}

impl<'a> Token<'a> {
    pub(crate) fn parse(token: &'a str) -> Option<Self> {
        if token.len() > 32_768 {
            return None;
        }
        let (message, signature) = token.rsplit_once('.')?;
        let (header, claims) = message.split_once('.')?;
        let header = decode(header)?;
        if header["alg"] != "RS256" || header.get("crit").is_some() {
            return None;
        }
        let kid = header["kid"].as_str()?.trim();
        if kid.is_empty() || kid.len() > 256 {
            return None;
        }
        let signature = URL_SAFE_NO_PAD.decode(signature).ok()?;
        if !(256..=1024).contains(&signature.len()) {
            return None;
        }
        Some(Self {
            message,
            signature,
            kid: kid.into(),
            claims: decode(claims)?,
        })
    }

    pub(crate) fn verify(&self, keys: &Value) -> bool {
        let mut matching = keys["keys"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|item| {
                item["kid"] == self.kid
                    && item["kty"] == "RSA"
                    && item.get("use").is_none_or(|value| value == "sig")
                    && item.get("alg").is_none_or(|value| value == "RS256")
            });
        let (Some(key), None) = (matching.next(), matching.next()) else {
            return false;
        };
        let modulus = key["n"]
            .as_str()
            .and_then(|value| URL_SAFE_NO_PAD.decode(value).ok());
        let exponent = key["e"]
            .as_str()
            .and_then(|value| URL_SAFE_NO_PAD.decode(value).ok());
        let (Some(n), Some(e)) = (modulus, exponent) else {
            return false;
        };
        RsaPublicKeyComponents { n, e }
            .verify(
                &RSA_PKCS1_2048_8192_SHA256,
                self.message.as_bytes(),
                &self.signature,
            )
            .is_ok()
    }
}

fn decode(value: &str) -> Option<Value> {
    serde_json::from_slice(&URL_SAFE_NO_PAD.decode(value).ok()?).ok()
}
