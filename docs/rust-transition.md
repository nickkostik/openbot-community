# OpenBot Rust transition

The first port implements the existing `/api/bot-models` API and model route resolution in Rust. It reads the installed catalog and bot preferences, validates writes against the available chat models, and preserves per-model reasoning choices and unknown fields. The listener binds to IPv4 loopback. It starts in comparison mode, and the explicit write option is for a disposable state directory.

## Build and compare

~~~sh
cargo +1.95.0 test --locked --manifest-path rust/Cargo.toml
cargo +1.95.0 build --release --locked --manifest-path rust/Cargo.toml
python3 rust/compare-read-only.py \
  --binary rust/target/release/openbot-runtime \
  --sand-data /home/box/sand-data
~~~

The comparison checks the model picker order, saved pins, effective reasoning choices, and input file fingerprints. Use `--baseline` if the existing OpenBot listens on a different loopback port. For a write rehearsal, copy `openbot-plan.json` and `openbot-bot-models.json` into a temporary directory and launch the binary against that directory with `--write-bot-settings`.

Preview the upstream for one or more saved catalog IDs with `resolve`.

~~~sh
rust/target/release/openbot-runtime --sand-data /home/box/sand-data \
  resolve --model openai:gpt-6.1-sol --credential chatgpt-plan
~~~

The command prints the selected provider ID, protocol, endpoint, and reasoning choice from the saved catalog and preferences. Credential kind is a simulation input. Add `--bot` with a bot ID to preview its saved overrides.

## Cutover checklist

- [x] Compare the native bot settings API and route resolver with the deployed JavaScript service. Verify writes and restart against a disposable copy.
- [ ] Port the catalog, credential drafts and refresh, and both OpenAI sign-in paths. Exercise the desktop callback and save the resulting account under its existing provider ID in a disposable installation.
- [ ] Connect the resolver to native provider headers, request and response conversion, streamed tool calls and images, reconnect, and upstream error handling. Compare the exact payloads across Chat Completions, Responses, and Messages. Exercise both direct OpenCode Go accounts independently.
- [ ] Port log settings and retention, usage, compaction, and provider-specific cache identifiers. Replay representative long histories with structured tool results and compare content and cache counters.
- [ ] Port the installer and the control and lifecycle commands. Verify service ownership, watcher recovery, and the panel's authenticated URLs. Retain the adapter required by the stock Grok Bot host.
- [ ] Restore a protected backup in a disposable installation. Compare provider and model IDs, per-bot settings, mode, and the old and new service checkpoints. Rehearse an upgrade and rollback on the same loopback address.
- [ ] Verify the live bot identities, active history, Official/Temporal mode, and routine settings before and after service cutover. Open the normal panel and complete the supported sign-in flow from the desktop.

See the [Axum routing reference](https://docs.rs/axum/0.8.8/axum/struct.Router.html) and the [Cargo Rust version policy](https://doc.rust-lang.org/cargo/reference/rust-version.html).
