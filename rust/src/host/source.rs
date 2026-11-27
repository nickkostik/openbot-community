use std::{io, path::Path};

use serde_json::{Value, json};

const MARKER: &str = "/* openbot-stock-wrap */";
const FOREIGN: &str = "/* opengrok-stock-wrap */";
const FACTORY: &str = "createProtoSessionProvider";
const STOCK: &str = "function createProtoSessionProvider(";
const RENAMED: &str = "function createProtoSessionProvider_stock(";
const HOP_SESSION: &str = "createOpenAiHopSession";
const HOP_URL: &str = "resolvedOpenaiBaseUrl";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Shape {
    Stock,
    Marked,
    Foreign,
    Private,
    Gap { session: bool },
    Ambiguous { functions: usize, properties: usize },
}

impl Shape {
    pub(super) fn kind(self) -> &'static str {
        match self {
            Self::Stock => "stock-unmarked",
            Self::Marked => "openbot-marked",
            Self::Foreign => "foreign-opengrok",
            Self::Private => "private-lane",
            Self::Gap { .. } => "gap",
            Self::Ambiguous { .. } => "ambiguous-factory",
        }
    }

    pub(super) fn json(self) -> Value {
        let mut value = json!({"kind":self.kind()});
        match self {
            Self::Marked => value["marker"] = json!(MARKER),
            Self::Gap { session } => {
                let (present, missing) = if session {
                    (HOP_SESSION, HOP_URL)
                } else {
                    (HOP_URL, HOP_SESSION)
                };
                value["present"] = json!(present);
                value["missing"] = json!(missing);
            }
            Self::Ambiguous {
                functions,
                properties,
            } => {
                value["functionDefs"] = json!(functions);
                value["propertyDefs"] = json!(properties);
            }
            _ => {}
        }
        value
    }
}

pub(super) fn inspect(source: &str) -> Shape {
    if source.contains(MARKER) {
        return Shape::Marked;
    }
    if source.contains(FOREIGN) {
        return Shape::Foreign;
    }
    match (source.contains(HOP_SESSION), source.contains(HOP_URL)) {
        (true, true) => return Shape::Private,
        (session, url) if session != url => return Shape::Gap { session },
        _ => {}
    }
    let mut functions = 0;
    let mut properties = 0;
    let mut asynchronous = false;
    for (at, _) in source.match_indices(FACTORY) {
        let before = source[..at].trim_end();
        let after = source[at + FACTORY.len()..].trim_start();
        if after.starts_with('(')
            && let Some(prefix) = before.strip_suffix("function")
            && prefix.chars().next_back().is_none_or(|c| !word(c))
        {
            functions += 1;
            asynchronous |= prefix.trim_end().ends_with("async");
        }
        if let Some(property) = after.strip_prefix(':') {
            let property = property.trim_start();
            let property = property
                .strip_prefix("async")
                .map_or(property, str::trim_start);
            if property
                .strip_prefix("function")
                .is_some_and(|value| value.trim_start().starts_with('('))
            {
                properties += 1;
            }
        }
    }
    if functions == 1
        && properties == 0
        && !asynchronous
        && source.matches(STOCK).count() == 1
        && !source.contains("function createProtoSession(")
    {
        Shape::Stock
    } else {
        Shape::Ambiguous {
            functions,
            properties,
        }
    }
}

fn word(c: char) -> bool {
    c.is_alphanumeric() || matches!(c, '_' | '$')
}

pub(super) fn stock(source: &str) -> io::Result<String> {
    let shape = inspect(source);
    match shape {
        Shape::Marked | Shape::Foreign => {
            let payload = split_header(source, shape == Shape::Foreign).ok_or_else(refused)?;
            let restored = payload.replacen(RENAMED, STOCK, 1);
            if restored == payload || inspect(&restored) != Shape::Stock {
                return Err(refused());
            }
            Ok(restored)
        }
        Shape::Stock => Ok(source.to_owned()),
        _ => Err(refused()),
    }
}

fn split_header(source: &str, foreign: bool) -> Option<&str> {
    let (_, remainder) = source.split_once('\n')?;
    let marker = if foreign { FOREIGN } else { MARKER };
    if !source.starts_with(&format!("{marker}\n")) {
        return None;
    }
    let remainder = if let Some(stamp) = remainder.strip_prefix("/* openbot-payload ") {
        let (hash, tail) = stamp.split_once(" */\n")?;
        if !(8..=64).contains(&hash.len()) || !hash.bytes().all(|ch| ch.is_ascii_hexdigit()) {
            return None;
        }
        tail
    } else {
        remainder
    };
    let namespace = if foreign {
        "__opengrokRuntime"
    } else {
        "__openbotRuntime"
    };
    let declaration = remainder.strip_prefix(&format!("var {namespace} = require("))?;
    let (runtime, remainder) = declaration.split_once('\n')?;
    let runtime = runtime.strip_suffix(");")?;
    let quoted = serde_json::from_str::<String>(runtime).is_ok()
        || runtime
            .strip_prefix('\'')
            .and_then(|item| item.strip_suffix('\''))
            .is_some_and(|item| !item.is_empty() && !item.contains('\''));
    if !quoted {
        return None;
    }
    let remainder = if foreign {
        remainder.strip_prefix("async ").unwrap_or(remainder)
    } else {
        remainder
    };
    let wrapper =
        remainder.strip_prefix(&format!("function {FACTORY}() {{\n  return {namespace}."))?;
    let method = wrapper
        .strip_prefix("attachSession")
        .or_else(|| wrapper.strip_prefix("wrapSession"))?;
    method.strip_prefix("(createProtoSessionProvider_stock, arguments);\n}\n")
}

pub(super) fn mark(stock: &str, runtime: &Path, fingerprint: &str) -> io::Result<String> {
    if inspect(stock) != Shape::Stock {
        return Err(refused());
    }
    let runtime = runtime.to_str().ok_or_else(refused)?;
    let name = serde_json::to_string(runtime).map_err(io::Error::other)?;
    let renamed = stock.replacen(STOCK, RENAMED, 1);
    let marked = format!(
        "{MARKER}\n/* openbot-payload {fingerprint} */\nvar __openbotRuntime = require({name});\n\
        function {FACTORY}() {{\n  return __openbotRuntime.attachSession(createProtoSessionProvider_stock, arguments);\n}}\n{renamed}"
    );
    if self::stock(&marked)? != stock {
        return Err(refused());
    }
    Ok(marked)
}

pub(super) fn marked_with(source: &str, runtime: &Path, fingerprint: &str) -> bool {
    let Ok(runtime) = serde_json::to_string(runtime) else {
        return false;
    };
    source.starts_with(&format!("{MARKER}\n/* openbot-payload {fingerprint} */\nvar __openbotRuntime = require({runtime});\n"))
        && split_header(source, false).is_some()
}

fn refused() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "the host source cannot be safely transformed",
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const HOST: &str = "'use strict';\nfunction createProtoSessionProvider(){return 1;}\nmodule.exports = createProtoSessionProvider;\n";

    #[test]
    fn transforms_and_restores_the_exact_stock_factory() {
        let adapter = Path::new("/a quoted \"path\"/runtime.cjs");
        let marked = mark(HOST, adapter, "012345abcdef").unwrap();
        assert!(marked_with(&marked, adapter, "012345abcdef"));
        assert_eq!(stock(&marked).unwrap(), HOST);
        let legacy = marked.replace("attachSession", "wrapSession");
        assert_eq!(stock(&legacy).unwrap(), HOST);
        let foreign = legacy
            .replace(MARKER, FOREIGN)
            .replace("/* openbot-payload 012345abcdef */\n", "")
            .replace("__openbotRuntime", "__opengrokRuntime");
        assert_eq!(stock(&foreign).unwrap(), HOST);
        assert!(
            stock(&marked.replace("return __openbotRuntime.", "return unrelatedRuntime.")).is_err()
        );
    }

    #[test]
    fn rejects_foreign_or_ambiguous_sources_without_guessing() {
        assert_eq!(inspect(HOST), Shape::Stock);
        for source in [
            format!("{HOST}\nfunction createProtoSessionProvider () {{}}"),
            format!("{HOST}\nconst config={{createProtoSessionProvider: async function () {{}}}};"),
            HOST.replace(
                "function createProtoSessionProvider",
                "async function createProtoSessionProvider",
            ),
            format!("{HOST}\nconst {HOP_SESSION}=true;"),
            format!("{FOREIGN}\n{HOST}"),
        ] {
            assert!(mark(&source, Path::new("/fixture/runtime.cjs"), "012345abcdef").is_err());
        }
    }
}
