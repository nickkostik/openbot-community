pub(crate) mod config;
mod history;
mod reminders;

use std::{
    collections::HashMap,
    path::Path,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use axum::http::HeaderMap;
use serde_json::{Map, Value, json};

use self::{
    config::{Config, ConfigFile, Mode},
    history::{History, add_ack, add_nudge},
};

const MAX_EPOCHS: usize = 8192;
const EPOCH_RETENTION: Duration = Duration::from_secs(3600);
type Key = [u8; 32];
type Epochs = Arc<Mutex<HashMap<Key, Epoch>>>;

#[derive(Clone)]
pub(crate) struct Delivery {
    pub(crate) file: ConfigFile,
    epochs: Epochs,
}

struct Epoch {
    updated: Instant,
    reminder_sent: bool,
    silent: bool,
    final_no_tool: bool,
    followups: u8,
    followup_pending: bool,
    redrives: u8,
}

impl Epoch {
    fn new(now: Instant) -> Self {
        Self {
            updated: now,
            reminder_sent: false,
            silent: false,
            final_no_tool: false,
            followups: 0,
            followup_pending: false,
            redrives: 0,
        }
    }
}

impl Delivery {
    pub(crate) fn new(root: &Path) -> Self {
        Self {
            file: ConfigFile::new(root),
            epochs: Default::default(),
        }
    }

    pub(crate) fn turn(
        &self,
        config: Config,
        body: &Map<String, Value>,
        headers: &HeaderMap,
        bot: Option<&str>,
        conversation: Option<&str>,
    ) -> Option<Turn> {
        if config.mode == Mode::Off {
            return None;
        }
        let history = History::from_request(body, headers, bot, conversation);
        let opening = body.get("opening") == Some(&Value::Bool(true));
        let mut meta = json!({
            "injectionMode":config.mode.name(), "injectionWouldApply":false,
            "identityGateResult":if history.is_ok() { "pass" } else { "fail" },
            "l2Eligible":false, "l2Attempted":false, "l2AdditionalRuns":0,
            "l2AddedLatencyMs":0, "heldTerminalBytes":0, "finalNoTool":false,
            "hostDeliveryEventMode":"unavailable",
        });
        match &history {
            Ok(history) => {
                meta["debtShape"] = json!(history.debt.name());
                meta["debtState"] = json!(if history.debt.owed() {
                    "owed"
                } else {
                    "not-owed"
                });
                meta["latestRealUserMessageSequence"] = json!(history.boundary);
                meta["touchClassification"] = json!(if history.after_touch.is_some() {
                    "initiated-fallback"
                } else {
                    "none"
                });
                meta["toolCallsAfterLastTouch"] = json!(history.after_touch.unwrap_or_default());
                if let Some(key) = history.key {
                    meta["injectionFingerprint"] = json!(crate::auth::hex(&key[..12]));
                }
            }
            Err(reason) => {
                meta["l2Outcome"] = json!("not-eligible");
                meta["skipReason"] = json!(reason);
            }
        }
        let history = history.ok();
        if let Some(key) = history.as_ref().and_then(|history| history.key)
            && config.mode == Mode::Enforce
            && let Ok(mut epochs) = self.epochs.lock()
        {
            let now = Instant::now();
            if epochs.len() == MAX_EPOCHS && !epochs.contains_key(&key) {
                epochs.retain(|_, epoch| {
                    epoch.followup_pending || now.duration_since(epoch.updated) < EPOCH_RETENTION
                });
            }
            if let Some(epoch) = epochs.get_mut(&key) {
                if !epoch.followup_pending && now.duration_since(epoch.updated) >= EPOCH_RETENTION {
                    *epoch = Epoch::new(now);
                }
            } else if epochs.len() < MAX_EPOCHS {
                epochs.insert(key, Epoch::new(now));
            }
        }
        Some(Turn {
            config,
            history,
            opening,
            epochs: self.epochs.clone(),
            meta,
            budget_skip: None,
        })
    }
}

pub(crate) struct Turn {
    config: Config,
    history: Option<History>,
    opening: bool,
    epochs: Epochs,
    meta: Value,
    budget_skip: Option<&'static str>,
}

pub(crate) struct FollowupBody {
    pub messages: Vec<Value>,
    pub cap: u64,
}

#[derive(Clone, Copy)]
pub(crate) struct Terminal {
    pub silent: bool,
    pub single_choice: bool,
    pub tool_calls: usize,
    pub delivery_call: bool,
}

pub(crate) struct Retry {
    epochs: Epochs,
    key: Key,
    pub timeout: Duration,
}

impl Drop for Retry {
    fn drop(&mut self) {
        if let Ok(mut epochs) = self.epochs.lock()
            && let Some(epoch) = epochs.get_mut(&self.key)
        {
            epoch.followup_pending = false;
        }
    }
}

impl Turn {
    pub(crate) fn metadata(&self) -> &Value {
        &self.meta
    }

    pub(crate) fn decision_budget(&self) -> Duration {
        self.config.l2.decision
    }

    pub(crate) fn before(&mut self, body: &mut Map<String, Value>) {
        let Some(history) = &self.history else { return };
        let Some(rows) = body.get_mut("messages").and_then(Value::as_array_mut) else {
            return;
        };
        let mut epochs = self.epochs.lock().ok();
        let epoch = epochs
            .as_mut()
            .and_then(|epochs| epochs.get_mut(&history.key?));
        let elapsed = history.after_touch.unwrap_or(history.tools);
        let ack = self.config.l1;
        let reminder = if ack.enabled {
            if self.opening {
                Some(("l1.reply-first", reminders::OPENING, true))
            } else if epoch.as_ref().is_none_or(|epoch| !epoch.reminder_sent) {
                if elapsed > ack.silence {
                    Some(("l1.silence", reminders::SILENCE, false))
                } else if elapsed > ack.result && history.text_sends > 0 {
                    Some(("l1.early-result", reminders::RESULT, false))
                } else if history.text_sends == 0 && history.tools > ack.start {
                    Some(("l1.start-ack", reminders::ACK, false))
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };
        if let Some((family, message, opening)) = reminder {
            let enforce = self.config.mode == Mode::Enforce;
            if !enforce || add_ack(rows, opening, message) {
                self.meta["injectionFamilies"] = json!([family]);
                self.meta["injectionWouldApply"] = json!(true);
                if enforce && let Some(epoch) = epoch {
                    epoch.reminder_sent = true;
                    epoch.updated = Instant::now();
                }
                return;
            }
        }
        if let Some(epoch) = epoch
            && self.config.mode == Mode::Enforce
            && self.config.l3.enabled
            && epoch.silent
            && !epoch.final_no_tool
            && !epoch.followup_pending
            && epoch.updated.elapsed() <= self.config.l3.ttl
            && epoch.redrives < self.config.l3.max_runs
            && let Some((family, nudge)) = history.debt.nudge()
        {
            let count = rows.len();
            add_nudge(rows, nudge);
            if rows.len() != count {
                epoch.redrives += 1;
                epoch.updated = Instant::now();
                self.meta["injectionFamilies"] = json!([format!("l3.{family}")]);
                self.meta["injectionWouldApply"] = json!(true);
                self.meta["l3Redrive"] = json!(true);
            }
        }
    }

    pub(crate) fn candidate(
        &mut self,
        body: &Map<String, Value>,
        output_cap: u64,
        rates: Option<(f64, f64)>,
    ) -> Option<FollowupBody> {
        let history = self.history.as_ref()?;
        let l2 = self.config.l2;
        if !l2.enabled || history.key.is_none() || !history.debt.owed() {
            return None;
        }
        let (_, nudge) = history.debt.nudge()?;
        let cap = output_cap.min(l2.completion_cap);
        let rows = body.get("messages").and_then(Value::as_array)?;
        let estimate = rows
            .iter()
            .map(|row| tokens(row, 0))
            .sum::<usize>()
            .saturating_add(body.get("tools").map_or(0, |tool| tokens(tool, 0)))
            .saturating_add(nudge.len().div_ceil(4) + 5);
        self.meta["l2PromptTokensEstimated"] = json!(estimate);
        self.meta["l2CompletionTokenCap"] = json!(cap);
        let cost = rates.map(|(input, output)| estimate as f64 * input + cap as f64 * output);
        self.meta["l2CostReservedUsd"] = cost.map_or(json!("unknown"), |usd| json!(usd));
        self.budget_skip = if estimate > l2.prompt_cap {
            Some("budget_prompt")
        } else if cost.map_or(l2.cost_cap == 0.0, |usd| usd > l2.cost_cap) {
            Some("budget_cost")
        } else {
            None
        };
        if self.config.mode != Mode::Enforce || self.budget_skip.is_some() {
            return None;
        }
        let mut messages = rows.clone();
        add_nudge(&mut messages, nudge);
        Some(FollowupBody { messages, cap })
    }

    pub(crate) fn skip_packet(&mut self, reason: &'static str) {
        self.budget_skip = Some(reason);
    }

    pub(crate) fn terminal(
        &mut self,
        first: Terminal,
        packet_ready: bool,
        received_at: Instant,
    ) -> Option<Retry> {
        let history = self.history.as_ref()?;
        let config = self.config;
        self.meta["currentResponseToolCallCount"] = json!(first.tool_calls);
        if !first.silent || !first.single_choice {
            if config.mode == Mode::Enforce {
                self.set_silent(false, false);
            }
            if config.l2.enabled {
                self.skip(
                    if first.tool_calls > 0 {
                        "response_has_tool_calls"
                    } else if !first.single_choice {
                        "multiple_or_missing_choices"
                    } else {
                        "terminal_not_silent"
                    },
                    "classification-skipped",
                );
            }
            return None;
        }
        self.meta["terminalFinishReason"] = json!("stop");
        if config.mode == Mode::Enforce {
            self.set_silent(true, false);
        }
        if !config.l2.enabled {
            return None;
        }
        let Some(key) = history.key else {
            self.skip("missing_epoch", "not-eligible");
            return None;
        };
        let Some((family, _)) = history.debt.nudge() else {
            self.skip("debt_not_owed", "not-eligible");
            return None;
        };
        let shape = history.debt.name();
        if let Some(reason) = self.budget_skip {
            self.skip(reason, "not-eligible");
            return None;
        }
        if config.mode == Mode::DryRun {
            if config.l2.max_runs > 0 {
                self.add_family(&format!("l2.{family}"));
            }
            return None;
        }
        self.meta["l2NudgeShape"] = json!(shape);
        self.meta["l2Eligible"] = json!(true);
        if !packet_ready {
            return None;
        }
        let mut epochs = match self.epochs.lock() {
            Ok(epochs) => epochs,
            Err(_) => return None,
        };
        let skip = match epochs.get_mut(&key) {
            None => Some("epoch_capacity"),
            Some(epoch) if epoch.followup_pending => Some("lease_busy"),
            Some(epoch) if epoch.final_no_tool || epoch.followups >= config.l2.max_runs => {
                self.meta["finalNoTool"] = json!(true);
                epoch.final_no_tool = true;
                Some("l2_attempt_exhausted")
            }
            Some(_) if received_at.elapsed() >= config.l2.decision => {
                Some("terminal_decision_timeout")
            }
            Some(epoch) => {
                epoch.followups += 1;
                epoch.followup_pending = true;
                epoch.updated = Instant::now();
                None
            }
        };
        drop(epochs);
        if let Some(reason) = skip {
            self.skip(reason, "classification-skipped");
            return None;
        }
        self.add_family(&format!("l2.{family}"));
        self.meta["l2Attempted"] = json!(true);
        self.meta["l2AdditionalRuns"] = json!(1);
        Some(Retry {
            epochs: self.epochs.clone(),
            key,
            timeout: config.l2.timeout,
        })
    }

    pub(crate) fn settled(&mut self, second: Option<(Terminal, &Value)>, started: Instant) {
        self.meta["l2AddedLatencyMs"] = json!(started.elapsed().as_millis() as u64);
        match second {
            Some((terminal, usage)) => {
                self.set_silent(terminal.silent, terminal.silent);
                self.meta["l2Outcome"] = json!(if terminal.silent {
                    "valid-no-tool"
                } else if terminal.delivery_call {
                    "second-success"
                } else {
                    "valid-terminal"
                });
                self.meta["finalNoTool"] = json!(terminal.silent);
                self.meta["heldReleasedReason"] = json!("l2-second-terminal");
                self.meta["terminalDecision"] = json!("l2-triggered");
                if let Some(prompt) = usage["prompt_tokens"].as_u64() {
                    self.meta["l2PromptTokensActual"] = json!(prompt);
                }
                if let Some(completion) = usage["completion_tokens"].as_u64() {
                    self.meta["l2CompletionTokensActual"] = json!(completion);
                }
            }
            None => {
                self.set_silent(true, false);
                self.meta["l2Outcome"] = json!("fallback-original-terminal");
                self.meta["heldReleasedReason"] = json!("l2_fallback_original_terminal");
                self.meta["terminalDecision"] = json!("l2-fallback-original-terminal");
            }
        }
    }

    fn set_silent(&self, silent: bool, final_no_tool: bool) {
        if let Some(key) = self.history.as_ref().and_then(|history| history.key)
            && let Ok(mut epochs) = self.epochs.lock()
            && let Some(epoch) = epochs.get_mut(&key)
        {
            epoch.silent = silent;
            epoch.final_no_tool |= final_no_tool;
            epoch.updated = Instant::now();
        }
    }

    fn add_family(&mut self, family: &str) {
        if !self.meta["injectionFamilies"].is_array() {
            self.meta["injectionFamilies"] = json!([]);
        }
        if let Some(families) = self.meta["injectionFamilies"].as_array_mut() {
            families.push(json!(family));
        }
        self.meta["injectionWouldApply"] = json!(true);
    }

    fn skip(&mut self, reason: &'static str, outcome: &'static str) {
        self.meta["skipReason"] = json!(reason);
        self.meta["l2Outcome"] = json!(outcome);
        self.meta["heldReleasedReason"] = json!(reason);
    }
}

fn tokens(value: &Value, depth: u8) -> usize {
    match value {
        Value::String(text) => text.len().div_ceil(4),
        _ if depth >= 16 => 0,
        Value::Array(items) => items.iter().map(|item| tokens(item, depth + 1)).sum(),
        Value::Object(row) if row.contains_key("image_url") || row.contains_key("input_image") => {
            1600
        }
        Value::Object(row) => row.values().map(|value| tokens(value, depth + 1)).sum(),
        _ => 0,
    }
}
