# OpenBot Rust transition

The Rust candidate implements bot settings, model routing, provider inference, OpenAI account handling, and catalog discovery and refresh. It reads the installed catalog and bot preferences, validates writes against the available chat models, and preserves per-model reasoning choices and unknown fields. The listener binds to IPv4 loopback and defaults to comparison mode.

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

Each turn reads the saved mode, pause flags, model selection, effort, and provider credential. The two Go families use distinct session IDs. Anthropic uses its API-key header and direct OpenAI uses Responses. The inference-only listener reads both existing OpenAI OAuth formats and reports expired credentials as `provider_sign_in_required`.

All three protocols yield Chat Completions-compatible JSON or incremental server-sent events. A provider's 503 can retry before the first output. An interrupted stream reports the failure on the current turn and releases the upstream connection. Messages usage reports cache-read and cache-creation tokens separately. [Responses streaming events](https://developers.openai.com/api/reference/responses/streaming-events) · [Messages streaming events](https://platform.claude.com/docs/en/build-with-claude/streaming)

## Rehearse OpenAI accounts

Add `--enable-accounts` in disposable state to enable the account routes and credential writes. Send `POST /api/oauth/chatgpt/start` on the exact loopback origin. Pass `providerId` to reconnect an existing direct OpenAI account. Open the returned authorization URL in a browser that can reach the same loopback port. The `/auth/callback` endpoint verifies the sign-in and `POST /api/oauth/chatgpt/status` returns a one-time credential ID when ready. An explicit `POST /api/save` adds a separate OpenAI account or saves the draft under the provider ID that initiated sign-in. This listener also supports the existing pasted callback flow at `/api/oauth/openai/start` and `/api/oauth/openai/complete`. [OpenAI registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)

Each successful refresh stores the replacement credentials atomically in the existing `secrets.json`. Concurrent turns for one provider share that refresh. The runtime waits for in-flight renewals during normal shutdown. Use synthetic tokens in rehearsals. A refresh can replace the renewable session's token at OpenAI, so running two services on copies of the same live token can break the session. Run the Rust tests with OpenSSL available for the ephemeral signing fixture. [OpenAI accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)

## Compare provider discovery

With `--enable-hop`, `--enable-accounts`, or `--enable-model-refresh`, send `POST /api/providers/{id}/fetch-models` to query a saved provider. The response preserves the listed model order, model types, recommended protocols, and reasoning capabilities. Each OpenCode Go account uses its own credential and catalog session at the direct Go endpoint. Existing OpenAI token formats select their corresponding model endpoints. The request leaves the saved catalog in place. [OpenCode model list API](https://opencode.ai/docs/console/api/models/) · [OpenAI models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)

Add `--enable-model-refresh` in disposable state to run manual and scheduled refreshes. The Models page's catalog Refresh action calls `POST /api/model-catalog/refresh` and polls `GET /api/model-catalog`. The public sources refresh even when `openbot-model-refresh.json` disables scheduled discovery. When scheduling is enabled, the worker checks after 15 seconds and then once per minute. The interval defaults to six hours. The worker records the next due time in `openbot-model-refresh-status.json`, saves the aggregate cache in `openbot-model-catalog.json`, and merges eligible provider models into the latest plan. Existing IDs, bindings, and active and per-bot reasoning choices survive the merge. Use disposable credentials for staging. Live OpenAI renewal still needs authorized account credentials. [Models.dev catalog format](https://github.com/anomalyco/models.dev/blob/dev/README.md) · [OpenRouter model list](https://openrouter.ai/docs/api/api-reference/models/get-models)

## Open the panel in rehearsal

Build the existing panel with `npm run build`, then point `--ui-dir` at the generated `ui` directory. Use the loopback address printed at startup. Open `/#/models/openai` in the browser for the saved OpenAI provider. Add `--enable-accounts` to test the sign-in and account saves with disposable credentials. The panel reads the saved catalog and listener status from Rust. The candidate loads `openbot-model-catalog.json` at startup and uses it for the aggregate count and provider-scoped metadata. Set `OPENBOT_AGENT_DATA` to a copied agent data directory to display the saved bot names and the corresponding log sources.

In disposable Custom state, Models and Dashboard can save an active model and reasoning effort with `--enable-accounts`. The selection writes the wildcard binding and leaves explicit conversation bindings and per-bot overrides intact. The two OpenCode Go accounts keep separate selections and credentials. The stock adapter reads the current plan on the next turn. Official activation returns a conflict until the managed host controls complete the mode change.

With `--write-bot-settings`, the dashboard can pause and resume the gateway and the Bots page can save individual or batch pause settings. The candidate uses the existing pause files. The stock adapter honors a pause on the next turn of an attached session. Terminal hop refusals return their original HTTP status to the adapter without a replay.

## Rehearse request recording

Open `/#/logs` with `--ui-dir` to browse saved records, captured bodies, events, and usage. In disposable Custom state, add `--enable-hop --enable-request-logs` and set `loggingEnabled` in `openbot-logs.json` to record new requests. The writer uses `openbot-requests.jsonl` and its matching body files. It records retry attempts and streamed outcomes under one request. Usage counts the provider once per turn and distinguishes a zero cache count from missing telemetry. Capture uses the saved body policy, redacts credentials, and caps each stored body.

The same opt-in enables log settings saves, cleanup, Strip Bodies, and Clear in the disposable directory. The first retention pass runs after one minute and repeats every 30 minutes. Clear keeps event history. The candidate uses the existing append lock and keeps unknown rows during retention. The current service still handles lifecycle and mode changes and the Official host tap.

## Rehearse compaction

Use `--enable-hop --enable-compaction` with disposable Custom state. For canonical chat history, Rust estimates the input size with the tool schemas and starts trimming at 85 percent of the selected model window. It targets 35 percent when the protected messages fit. The latest user exchange, system and developer instructions, and adjacent tool calls and results keep their structure. Old excerpts are available while the provider writes a brief. Subsequent turns verify the covered history and reuse the saved boundary. Native Responses input and auxiliary calls bypass compaction.

Native summaries and resumable checkpoints use private sidecars under `openbot/compact-state`. The candidate reads a completed legacy brief only when the covered messages match. It preserves the existing legacy summary and checkpoint files. Interrupted native work resumes from its confirmed chunk. Changing the model, effort, or covered history invalidates stale work. The requested direct account receives the summary call when eligible. Context overflow can move the summary to an approved configured fallback.

With request logging enabled, chat records show the estimated input, estimated retained input, target, and summary source. The usage report shows actual provider tokens and reports compaction requests separately. Cache measurements require an eligible repeated prefix. [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) · [DeepSeek context caching](https://api-docs.deepseek.com/guides/kv_cache/)

## Cutover checklist

- [x] Compare the native bot settings API and route resolver with the deployed JavaScript service. Verify writes and restart against a disposable copy.
- [x] Port request serialization and verify tool IDs, ordering, and mixed text and image content across Chat Completions, Responses, and Messages.
- [x] Exercise the native HTTP provider hop against loopback fixtures and direct Go and NVIDIA endpoints. Verify model switches, native effort controls, streaming tool arguments, usage, error reporting, and reconnect in the disposable setup.
- [x] Port credential drafts, refresh, and both OpenAI sign-in paths. Verify signed account renewal under the existing provider ID, independent registration, and refresh persistence in disposable state.
- [x] Port on-demand provider discovery. Compare both Go and both NVIDIA accounts with the current service and verify OpenAI plan and OAuth descriptors with fixtures.
- [x] Serve the existing built panel and state readback through Rust. Open Models in Chromium, exercise sign-in cancellation, and save and reload a disposable account key.
- [x] Read the existing aggregate model catalog through Rust. Verify both global and provider-scoped lookups for the installed model IDs.
- [x] Port manual and scheduled catalog refresh. Compare the full aggregate cache and direct provider merge with the current worker. Test the browser action and restart schedule.
- [x] Port Custom request recording plus log, event, and usage reads. Verify older paired rows, body redaction, and the Requests, Events, and Usage views in Chromium.
- [ ] Complete account authorization with the live desktop browser.
- [ ] Verify generation with a renewed OpenAI credential, confirm dispatch through the stock host adapter, and compare the selected provider and effort in the panel's request log.
- [x] Port log settings, scheduled retention, cleanup, Strip Bodies, and Clear. Rehearse all operations against disposable history.
- [x] Port compaction. Rehearse structured tool results, matching legacy briefs, native checkpoint restart, and an eligible direct OpenCode Go cache hit.
- [x] Port active model and effort saves and gateway and bot pauses. Verify the next turn through one attached stock adapter session in disposable state.
- [ ] Port the installer and the control and lifecycle commands. Verify service ownership, watcher recovery, and the panel's authenticated URLs. Retain the adapter required by the stock Grok Bot host.
- [ ] Restore a protected backup in a disposable installation. Compare provider and model IDs, per-bot settings, mode, and the old and new service checkpoints. Rehearse an upgrade and rollback on the same loopback address.
- [ ] Verify the live bot identities, active history, Official/Temporal mode, and routine settings before and after service cutover. Open the normal panel and complete the supported sign-in flow from the desktop.

See the [Axum routing reference](https://docs.rs/axum/0.8.8/axum/struct.Router.html) and the [Cargo Rust version policy](https://doc.rust-lang.org/cargo/reference/rust-version.html).
