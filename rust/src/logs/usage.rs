use serde_json::{Map, Value, json};

pub fn observe(row: &mut Map<String, Value>, usage: &Value) {
    if !usage.is_object() {
        return;
    }
    for (field, paths) in [
        (
            "promptTokens",
            &["/prompt_tokens", "/input_tokens", "/promptTokens"][..],
        ),
        (
            "completionTokens",
            &["/completion_tokens", "/output_tokens", "/completionTokens"],
        ),
        ("totalTokens", &["/total_tokens", "/totalTokens"]),
        (
            "cachedTokens",
            &[
                "/prompt_tokens_details/cached_tokens",
                "/input_tokens_details/cached_tokens",
                "/cache_read_input_tokens",
                "/cached_tokens",
                "/prompt_cache_hit_tokens",
                "/cachedTokens",
            ],
        ),
        (
            "reasoningTokens",
            &[
                "/completion_tokens_details/reasoning_tokens",
                "/output_tokens_details/reasoning_tokens",
                "/reasoning_tokens",
                "/reasoning_output_tokens",
                "/reasoningTokens",
            ],
        ),
        (
            "textTokens",
            &["/completion_tokens_details/text_tokens", "/text_tokens"],
        ),
        (
            "imageTokens",
            &["/prompt_tokens_details/image_tokens", "/image_tokens"],
        ),
        (
            "audioTokens",
            &["/prompt_tokens_details/audio_tokens", "/audio_tokens"],
        ),
    ] {
        if let Some(value) = paths.iter().find_map(|path| usage.pointer(path)?.as_u64()) {
            row.insert(field.into(), json!(value));
        }
    }
}

pub fn count(row: &Value, field: &str) -> u64 {
    row[field].as_u64().unwrap_or(0)
}

#[derive(Default)]
pub struct Totals {
    pub(crate) requests: usize,
    pub(crate) ok: usize,
    pub(crate) prompt_tokens: u64,
    pub(crate) completion_tokens: u64,
    pub(crate) total_tokens: u64,
    pub(crate) cached_tokens: u64,
    pub(crate) reasoning_tokens: u64,
    known_input: u64,
    latency_sum: u64,
    latency_count: u64,
    first_sum: u64,
    first_count: u64,
    timed_output: u64,
    stream_duration: u64,
}

impl Totals {
    pub fn add(&mut self, row: &Value) {
        self.requests += 1;
        let ok = row["ok"] == true;
        self.ok += usize::from(ok);
        let input = count(row, "promptTokens");
        let output = count(row, "completionTokens");
        self.prompt_tokens = self.prompt_tokens.saturating_add(input);
        self.completion_tokens = self.completion_tokens.saturating_add(output);
        self.total_tokens = self.total_tokens.saturating_add(count(row, "totalTokens"));
        self.reasoning_tokens = self
            .reasoning_tokens
            .saturating_add(count(row, "reasoningTokens"));
        if let Some(cached) = row["cachedTokens"].as_u64() {
            self.cached_tokens = self.cached_tokens.saturating_add(cached.min(input));
            self.known_input = self.known_input.saturating_add(input);
        }
        let latency = row["latencyMs"].as_u64();
        let first = row["firstTokenMs"].as_u64();
        if let Some(latency) = latency {
            self.latency_sum = self.latency_sum.saturating_add(latency);
            self.latency_count += 1;
        }
        if let Some(first) = first {
            self.first_sum = self.first_sum.saturating_add(first);
            self.first_count += 1;
        }
        if ok
            && row["stream"] == true
            && output > 0
            && let Some(elapsed) = latency
                .zip(first)
                .and_then(|(end, start)| end.checked_sub(start))
            && elapsed >= 50
        {
            self.timed_output = self.timed_output.saturating_add(output);
            self.stream_duration = self.stream_duration.saturating_add(elapsed);
        }
    }

    pub fn view(&self, key: &str) -> Value {
        json!({
            "key":key,"requests":self.requests,"ok":self.ok,"fail":self.requests-self.ok,
            "promptTokens":self.prompt_tokens,"completionTokens":self.completion_tokens,
            "totalTokens":self.total_tokens,"cachedTokens":self.cached_tokens,
            "reasoningTokens":self.reasoning_tokens,"cacheKnownInputTokens":self.known_input,
            "cacheUnknownInputTokens":self.prompt_tokens-self.known_input,
            "cacheCoverage":ratio(self.known_input, self.prompt_tokens).unwrap_or(0.0),
            "cacheHitRate":ratio(self.cached_tokens, self.known_input),
            "avgLatencyMs":ratio(self.latency_sum, self.latency_count).unwrap_or(0.0),
            "avgFirstTokenMs":ratio(self.first_sum, self.first_count),
            "avgTps":ratio(self.timed_output, self.stream_duration).map(|tps| tps*1000.0),
        })
    }
}

fn ratio(numerator: u64, denominator: u64) -> Option<f64> {
    (denominator > 0).then(|| numerator as f64 / denominator as f64)
}
