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

## Install into an existing Linux user service

Use a disposable copy of the OpenBot state and an existing `openbot-*.service` user unit on a dedicated loopback port. Build the panel with `npm ci` and `npm run build`. Build the candidate as above and select the matching Git revision.

~~~sh
native="$PWD/rust/target/release/openbot-runtime"
revision="$(git rev-parse HEAD)"
"$native" --sand-data "$fixture" --port "$port" service --unit "$unit" status
"$native" --sand-data "$fixture" --port "$port" service --unit "$unit" \
  install --ui-dir "$PWD/ui" --revision "$revision"
"$native" --sand-data "$fixture" --port "$port" service --unit "$unit" status
~~~

The installer verifies that systemd's main PID, OpenBot's published PID, and the actual listening socket identify the same process. It pins the binary, the four served panel assets, and any selected adapter and Access configuration by content under `.openbot-rust-releases/`. It selects the release through `90-openbot-native-runtime.conf`. The existing unit and its other drop-ins continue to supply the service policy and environment. The restarted process must own the same listener and answer `/healthz` with the expected command identity. Reinstalling the same healthy bundle returns its running PID.

The deployment receipt in `.openbot-rust-deployment.json` records the previous selection before a restart. An unsuccessful restart restores that selection. If the installer itself stops during deployment, the next `install` or `rollback` first restores the pending baseline. The command retries a rate-limited start once for the selected user unit and the configured port. [systemd user unit lifecycle](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html)

~~~sh
"$native" --sand-data "$fixture" --port "$port" service --unit "$unit" rollback
"$native" --sand-data "$fixture" --port "$port" service --unit "$unit" rollback --original
~~~

The first command restores the preceding release. Use `--original` to restore the service that originally owned the port, even after several native upgrades. Both operations reuse the saved state directory. Keep its existing backup and the original service's code until the recovery rehearsal has passed. The managed runtime reads the selected mode and applies the saved refresh and log-retention policies when it runs. Verify the original protected URLs and the remaining Settings controls before promoting the production unit.

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

In disposable Custom state, Models and Dashboard can save an active model and reasoning effort with `--enable-accounts`. The selection writes the wildcard binding and leaves explicit conversation bindings and per-bot overrides intact. The two OpenCode Go accounts keep separate selections and credentials. The stock adapter reads the current plan on the next turn. Activation from Official requires the managed host controls below.

The native `/api/save` routes also support adding and renaming provider accounts, editing endpoints, replacing credentials, and importing, editing, and removing models. A keyed account needs a credential for a different network origin. Its previous key is withdrawn before the endpoint changes, and the replacement is published after the new endpoint is saved. The private receipt `.openbot-rust-provider-move.json` lets the service finish an interrupted change. Manual and scheduled catalog changes use the same write lock. The hop checks the saved endpoint before taking a credential for a turn.

New OpenCode accounts use the direct Go endpoints. Alibaba Token Plan accounts use their Singapore endpoint and dedicated key. Removing an active model chooses the next eligible chat model from the same provider, then checks other direct providers. Removing a provider preserves its reserved credential ID and unrelated conversation bindings. Catalog edits use the existing mode. Switch to Official before removing the last direct chat model from Custom. [OpenCode Go API](https://opencode.ai/docs/console/api/models/) · [Alibaba Token Plan authentication](https://www.alibabacloud.com/help/en/model-studio/token-plan-faq)

With `--write-bot-settings`, the dashboard can pause and resume the gateway and the Bots page can save individual or batch pause settings. The candidate uses the existing pause files. The stock adapter honors a pause on the next turn of an attached session. Terminal hop refusals return their original HTTP status to the adapter without a replay.

## Rehearse managed host changes

Set `--host-main` or `OPENBOT_HOST_MAIN` to observe the host entrypoint. The snapshot shows its wrap shape and identifies the matching Node process or the detected Grok Bot desktop process. The configured tunnel URL is advisory. Desktop process detection is read-only.

Use a supervised fixture Node host and the installed adapter from the same release to rehearse changes.

~~~sh
"$native" --sand-data "$fixture" --port "$port" --ui-dir "$PWD/ui" \
  --managed-service --enable-hop --enable-accounts --enable-request-logs \
  --host-main "$fixture_host" --host-adapter "$fixture_payload/runtime.cjs"
~~~

Choose a model in the panel or send `POST /api/save` with `kind` set to `use-model`. The service verifies a configured direct chat model and credential, syntax-checks changes with Node, and publishes the plan with the managed loopback endpoint before selecting Custom. Use `--host-node` to select a particular Node executable. Switching to Official preserves the plan and credentials. The host keeps its stock logging adapter while logging is enabled and returns to its original bytes when logging is disabled.

The watcher repairs the supported wrapper after a compatible host update. It records a pending restart in `.openbot-rust-host-reload.json`, waits for the native hop and legacy lease to show no active requests and reach the quiet window, then signals only the matching Node process. The fixture or Computer supervisor starts the replacement. A pending reload from the legacy service takes precedence.

Pass the adapter source and Node Computer entrypoint together to include this controller in a pinned installation. The installed adapter reads the state and port from its release and uses the systemd-owned Rust hop when the service restarts.

~~~sh
"$native" --sand-data "$fixture" --port "$port" service --unit "$unit" \
  install --ui-dir "$PWD/ui" --revision "$revision" \
  --payload-dir "$PWD/payload" --host-main "$fixture_host" --host-node "$node"
~~~

## Verify Cloudflare Access ingress

Use the existing named Cloudflare Tunnel and self-hosted Access applications. Preserve each public Host when forwarding to the loopback listener. Provide the verifier with `--access-config` on the server or the installer. The first `origin` is the address shown in Phone access. An origin needs its exact HTTPS hostname and its Access application’s AUD tag. [Cloudflare origin parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/) · [Cloudflare JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)

~~~json
{
  "teamDomain": "https://example-team.cloudflareaccess.com",
  "origins": [
    {
      "origin": "https://openbot.example.com",
      "audience": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ]
}
~~~

Public panel requests need a valid application assertion from the configured Cloudflare team. Saves also need the site's Origin. The panel validates the signature, issuer, audience, token type, and expiry and refreshes its cached signing keys. Open the loopback panel to finish account sign-in or call inference. `OPENBOT_PUBLIC_URL` is a display setting. The existing connector continues to route both canonical and alias domains, and the chosen service must be verified through a signed-in browser.

## Rehearse request recording

Open `/#/logs` with `--ui-dir` to browse saved records, captured bodies, events, and usage. In disposable Custom state, add `--enable-hop --enable-request-logs` and set `loggingEnabled` in `openbot-logs.json` to record new requests. The writer uses `openbot-requests.jsonl` and its matching body files. It records retry attempts and streamed outcomes under one request. Usage counts the provider once per turn and distinguishes a zero cache count from missing telemetry. Capture uses the saved body policy, redacts credentials, and caps each stored body.

The same opt-in enables log settings saves, cleanup, Strip Bodies, and Clear in the disposable directory. The first retention pass runs after one minute and repeats every 30 minutes. Clear keeps event history. The candidate uses the existing append lock and keeps unknown rows during retention. The verified stock adapter handles Official host logging.

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
- [x] Port provider and model management. Verify an endpoint change and key replacement, imported model settings, active-model fallback, and removal through the existing panel.
- [x] Stage and select the native panel and hop as one existing Linux user service. Verify ownership, a failed restart, an interrupted install, and rollback to the prior and original service.
- [x] Observe the Computer and rehearse Official and Custom switching, lease-aware Node reload, and host drift repair with the stock adapter.
- [x] Include the host adapter and Access verifier in the managed release. Verify the systemd install and rollback and the signed-request gates with disposable fixtures.
- [ ] Port Delivery Follow-up and the Grok Bot Skill controls. Verify the Settings page, then open the original public URLs through the prepared service with a signed-in browser.
- [ ] Restore a protected backup in a disposable installation. Compare provider and model IDs, per-bot settings, mode, and the old and new service checkpoints. Rehearse an upgrade and rollback on the same loopback address.
- [ ] Verify the live bot identities, active history, Official/Temporal mode, and routine settings before and after service cutover. Open the normal panel and complete the supported sign-in flow from the desktop.

See the [Axum routing reference](https://docs.rs/axum/0.8.8/axum/struct.Router.html) and the [Cargo Rust version policy](https://doc.rust-lang.org/cargo/reference/rust-version.html).
