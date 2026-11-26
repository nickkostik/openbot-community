# OpenBot Rust transition

The Rust candidate implements `/api/bot-models`, model routing, and the provider hop. It reads the installed catalog and bot preferences, validates writes against the available chat models, and preserves per-model reasoning choices and unknown fields. The listener binds to IPv4 loopback and defaults to comparison mode.

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

Add `--request path/to/fixture.json` to encode a canonical request for one selected model. The fixture uses the normalized Chat Completions shape after provider reasoning controls. The output shows the serialized Chat Completions, Responses, or Messages body. The Messages encoder preserves text and images in order and groups parallel tool results. [Messages image formats](https://platform.claude.com/docs/en/build-with-claude/vision) · [Responses request schema](https://developers.openai.com/api/reference/resources/responses/methods/create)

## Rehearse inference

Start the candidate with `--enable-hop` and point `--sand-data` at a disposable Custom state directory. The candidate binds an available loopback port, serves `GET /healthz`, and accepts canonical Chat Completions requests at `POST /v1/chat/completions`. Add `--write-bot-settings` to rehearse settings writes in that directory.

Each turn reads the saved mode, pause flags, model selection, effort, and provider credential. The two Go families use distinct session IDs. Anthropic uses its API-key header, direct OpenAI uses Responses, and the current saved OpenAI OAuth formats can authenticate while their access tokens are valid. Expired OpenAI credentials produce `provider_sign_in_required`.

All three protocols yield Chat Completions-compatible JSON or incremental server-sent events. A provider's 503 can retry before the first output. An interrupted stream reports the failure on the current turn and releases the upstream connection. Messages usage reports cache-read and cache-creation tokens separately. [Responses streaming events](https://developers.openai.com/api/reference/responses/streaming-events) · [Messages streaming events](https://platform.claude.com/docs/en/build-with-claude/streaming)

## Cutover checklist

- [x] Compare the native bot settings API and route resolver with the deployed JavaScript service. Verify writes and restart against a disposable copy.
- [x] Port request serialization and verify tool IDs, ordering, and mixed text and image content across Chat Completions, Responses, and Messages.
- [x] Exercise the native HTTP provider hop against loopback fixtures and direct Go and NVIDIA endpoints. Verify model switches, native effort controls, streaming tool arguments, usage, error reporting, and reconnect in the disposable setup.
- [ ] Port the catalog, credential drafts and refresh, and both OpenAI sign-in paths. Exercise the desktop callback and save the resulting account under its existing provider ID in a disposable installation.
- [ ] Verify generation with a renewed OpenAI credential, confirm dispatch through the stock host adapter, and compare the selected provider and effort in the panel's request log.
- [ ] Port log settings and retention, usage, compaction, and provider-specific cache identifiers. Replay representative long histories with structured tool results and compare content and cache counters.
- [ ] Port the installer and the control and lifecycle commands. Verify service ownership, watcher recovery, and the panel's authenticated URLs. Retain the adapter required by the stock Grok Bot host.
- [ ] Restore a protected backup in a disposable installation. Compare provider and model IDs, per-bot settings, mode, and the old and new service checkpoints. Rehearse an upgrade and rollback on the same loopback address.
- [ ] Verify the live bot identities, active history, Official/Temporal mode, and routine settings before and after service cutover. Open the normal panel and complete the supported sign-in flow from the desktop.

See the [Axum routing reference](https://docs.rs/axum/0.8.8/axum/struct.Router.html) and the [Cargo Rust version policy](https://doc.rust-lang.org/cargo/reference/rust-version.html).
