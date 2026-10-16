# Troubleshooting and keeping it working

[Home](../README.md) · [Agent install](agent-install.md) · [Installation](portable-installation.md) · [Models and tools](models-and-tools.md)

Find the layer that failed before changing settings. A provider error, platform delivery refusal, stale bot pin, and broken tunnel need different fixes.

## Start with one fresh turn

Pick one existing bot. Record the time, current bot ID privately, selected global route, that bot's pin, and effective effort. Send one new read-only test after any pending host restart finishes. Use a unique marker so an old reply cannot pass the test.

Correlate the client send, local acceptance, provider request, completed stream, tool result, and delivered reply. If a stage is absent, investigate that stage. A successful hop probe does not prove that the client sent anything to the box.

| Check | Proves | Does not prove |
|---|---|---|
| Panel loads | The control service and access path answer. | Native dispatch, a working provider, or client delivery. |
| `/healthz` returns `ok: true` | That service answers its health route. | A finished inference stream. |
| Model save succeeds | The configuration accepted the selection. | An explicit bot pin was removed or a running turn changed. |
| Gateway accepts a send | Work was accepted at that boundary. | The model completed or the user got the reply. |
| Provider HTTP 200 | Headers/response began successfully. | The stream finished or a tool result was consumed. |
| File-read result and marker reply arrive | The tested bot completed that tool/reply path. | Every client or every integration works. |
| Desktop reply arrives | That desktop delivery path works. | Standalone iOS or Android admission. |
| Voice tool finishes and speech plays before hangup | That real call completed its tool continuation. | Other voice clients or transports behave the same way. |

## The panel is down

First check whether the Computer is awake. A hibernated Computer cannot run its panel, tunnel, or on-box recovery watchers. Wake it through the normal Grok Bot Computer flow, or an explicitly configured external recovery service. Wake recovery must preserve the saved Official/Custom selection. A public 502 alone does not tell you which service failed.

From the **Computer**, make a bounded loopback check:

```bash
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:9280/healthz
```

The health path is `/healthz`. A `/health` 404 is not a failed `/healthz` check.

If loopback works, check the actual public URL in a browser and inspect tunnel ownership/logs. A stale tunnel origin or rotated quick URL is separate from a dead service. Do not start a second OpenBot server on another port to hide the problem.

If loopback fails, check installer/reconcile results and the owned service's logs. Use the supervisor or opted-in recovery integration. A PID file alone is not ownership proof; a recycled PID or unrelated listener must not be adopted or killed.

For script-error popups, capture the first real console error and its source. A cross-origin `Script error.` or wallet-injected `window.ethereum` error is not enough to blame OpenBot, but it is not proof of harmless noise either. Compare a clean browser session with the same panel, and keep real application errors visible. Do not post private console payloads.

An explicit Official selection takes precedence over a retained custom model catalog. Current guard checks skip custom repair and hop patrol in Official; a repair already observing the box must refuse if the saved mode changes. Check `openbot-audit.jsonl` for the writer when a mode changes, rather than inferring intent from the plan file.

## The selected model does not change

Check these in order:

1. The save completed and the current panel state shows the intended global model.
2. The bot is set to Default, or its intentional pin selects the route you expect.
3. The bot/model reasoning setting is supported by that route.
4. The host restart is finished and you sent a new message.
5. The request trace names the actual account/model and completes with a delivered reply.

Keep separate accounts separate. Do not remove all overrides, reset the plan, edit request log model names, or switch to a different provider to make a test look green. The log's recorded slug can differ from the canonical account-prefixed ID; account grouping and the shared trace matter.

## Delivery says “This Bot is unavailable”

Inspect the semantic delivery response, even if HTTP is 200. `GROK_BOT_USER_MESSAGE_DELIVERY_REFUSED` / `send/agent-unavailable` is a refusal, not accepted work.

If no matching local acceptance appears, the failure happened before the BOX runner. Changing the local model or repainting `harness: box` does not replace the platform's response. Check native identity ownership, actual binding/acceptance evidence, the current client path, and the [compatibility boundary](portable-recovery.md#compatibility-boundary).

The unchanged standalone Grok Bot iOS/Android app remains unresolved for this fork's BOX path. Desktop, integrated Grok web/app, and native gateway tests are separate evidence. The inactive optional proxy code is not an installed mobile fix. There is no supported claim that box-side code overrides a remote pre-dispatch refusal.

Do not remint a fleet as a quick repair. If an explicitly authorized migration is required, retain its one-to-one journal, copied transcripts, category/pin mappings, room memberships, routine ownership, and original identities until delivery is verified. Reopen the current bot from the bot list when an already-open thread points to a retired ID; keep that distinct from refusal of the verified current ID. [Guarded migration prompt](agent-install.md#move-selected-temporal-bots-to-local) and [identity/migration reference](../skills/openbot-config/reference.md#complete-fresh-box-remint-workflow).

## Provider and stream errors

| Symptom | First thing to check |
|---|---|
| 400, unsupported protocol | The exact model's Chat Completions/Responses/Anthropic setting at that account's origin. |
| 400, tools with reasoning unsupported | Use the route's supported protocol and effort. Do not disable all reasoning or fabricate missing tool history. |
| 401 | That account's key/login expiry, allowed origin, and refresh result. Renew only that account. |
| 429 or “no credits” | The selected account's actual quota and whether it is API-key or subscription authentication. |
| 502, reset, aborted, disconnected | Correlate abort source, host reload, client cancellation, and upstream stream completion. The status is a symptom, not a root cause. |
| 503 | Determine whether it came from a pause/ownership gate, the hop, or the provider. Inspect its safe error code and trace. |
| Empty 200 stream | Check terminal completion and native delivered reply; successful headers are insufficient. |
| `foreign-hop` | Identify the owned service and public origin mapping. OpenBot refuses unrelated port owners. |

For OpenCode Go, verify the direct `/zen/go/v1` origin, the Go key, per-model protocol, and the identified-client headers. For OpenAI OAuth, use the authenticated Responses adapter. Never send OAuth tokens to a generic relay or substitute API credits for a subscription account silently.

Retries can create more provider work. Do not replay uncertain sends or outbound actions simply because a receipt is missing. Fix the cause and use one isolated read-only canary.

## Routines are enabled but do nothing

Enabled definitions are only one part of the clock. The local scheduler also needs a running compatible adapter, the explicit local-only policy, valid scheduler configuration, an allowed inference mode, unpaused ownership, and available capacity.

The clock supports **both Official and Custom**, but Official inference still uses stock Grok. The configured cap is 1 through 12, with a default of 2 when omitted. Changing that cap is a deliberate load decision, not part of installation.

Check policy, fresh heartbeat, eligible/due/queued counts, queue deadlines, pending claims, durable receipts, and native runs together. `isRunning` includes tool-wait time; it is not a count of models currently generating. Zero open receipts does not exclude an in-memory pending claim or a receiptless start.

A routine can miss a slot because it was never observed within the grace window, expired in the queue, had a held owner, or lost available capacity. A stale newest receipt does not by itself identify the cause. Require slot and native run evidence.

**Keep roster drift holds intact.** Clearing a hold while the platform still owns a Temporal routine can let both schedulers dispatch the same work, including outbound actions. Resolve ownership through the verified journal/binding flow; never clear an alert merely to increase eligible counts. Do not replay historical missed slots.

For full policy and receipt semantics, read the [native local routine clock](../skills/openbot-config/reference.md#native-local-routine-clock).

## Twelve bots and lag

The scheduler supports up to 12 concurrent bot owners; that is a limit, not a zero-lag promise. Ordinary chats, tool waits, pending claims, and scheduled work share capacity. Account rate limits and connection pools can bottleneck before CPU does.

Measure an isolated batch of harmless reads at the intended concurrency. Record reply latency, time to first token, complete-stream success, panel/health latency during the batch, CPU/memory pressure, and provider errors. Leave model assignments and routine settings unchanged. Check the provider's actual limits before increasing workload.

Use real turn completions when judging the result. A fast health check or synthetic backend does not measure a twelve-bot live workload. Stagger heavy routine slots when their timing allows it; investigate long tool waits and orphaned claims before raising the cap.

## Voice tools finish only after hangup

Keep the call open and trace the tool task through acceptance, completion, continuation delivery, and audible playback. A text result visible after hangup does not pass this test.

A synthetic voice socket that receives a manually injected nudge is useful transport evidence, but not proof that the installed app consumes the real event. Report which leg completed and which did not. Installed-app completion before hangup remains unverified here; do not claim a diagnostic socket fixed every client.

## Update without losing your setup

Use the [coordinated update guide](updating.md) and [update prompt](agent-install.md#update-this-fork). The native Official host and matching compatible local engine need their own completion evidence. There are three separate update tracks:

| Track | What must be checked |
|---|---|
| OpenBot source | Saved repository/ref, validated staged source, installed commit, and activated service. |
| Vendor app/Computer | Supported updater, actual running bundle/version, and engine compatibility. |
| Model catalog | Provider discovery status, published metadata, and preserved explicit settings. |

Source updates retain an earlier release and keep runtime data in sand-data. Downloads or activation can fail; a retained old release must not be stamped as new. `OPENBOT_HOST_BOUNCE=pending` means the host has not finished reloading.

Keep the bot's installing turn short so the idle finalizer can run. Do not force a restart during live work or start a native host without its authenticated environment. If activation fails, inspect the recorded rollback result and retained source directories before retrying.

For an engine update, use the reviewed pair/manifest flow. Do not overwrite the live bundle from a random backup or remove the policy to bypass a refusal. The upstream follower stages compatible ports and waits for idle activation; unknown changes can require review. Latest metadata does not prove full parity.

## Back up the things you would hate to lose

A GitHub source push backs up code. It does not back up the Computer's credentials, conversations, or state.

| Backup | Preserve |
|---|---|
| Source | Exact OpenBot commit, dependencies/lockfile, custom hooks, docs, and license. |
| Private runtime | Provider/OAuth credentials, plan, mode, pins/efforts, network policy/state, engine manifests, identity journals, bot databases/attachments, routine definitions/claims, and compaction checkpoints. |
| Native managed store | Its actual successful snapshot and durability status, checked separately. |

Use a consistent SQLite backup or an idle/quiesced snapshot; copying only a live `store.db` while ignoring its WAL is not a complete conversation backup. Include private compaction sidecars when keeping resumable state. Store credentials and histories only in encrypted private recovery, with the decryption key kept separately.

Decrypt and validate an archive before calling it a backup. Restore to an isolated owned staging directory and check its manifest, database integrity, hashes, and selected configuration. Do not start cloned bots or routines during the restore test. Keep access to the prior engine/source until fresh local delivery is proven.

The native backup-status compatibility patch caches a small summary only after a validated canonical read or an acknowledged native write. Version 2 counts the existing manifest without building another complete map, and accepts a renamed local catch variable while retaining the exact acknowledgement contract. It upgrades only the complete known version 1 patch; unfamiliar or partial contracts remain unsupported. This reduces repeated status-poll work. The separate byte-upload helper addresses direct S3 byte-body copies and socket buffering; neither helper proves a fresh full backup completed.

A timed-out native store-status request means durability is unknown. A flush hook or empty snapshot is not a completed vendor backup. Do not disable that gate, fake a durable status, delete backup provenance, or recursively change the entire Computer's ownership to force an update.

## Share a useful issue report

Include OpenBot commit, vendor app/host version, client type/version, route family/protocol without secrets, exact safe error, timestamps with timezone, and which stage failed. Add a small reproduction using a harmless read and the checks that actually completed.

Exclude OAuth files, keys, gateway/tunnel/Tailscale state, raw database copies, private transcripts, and request bodies. [Public release and sharing checklist](public-release.md).
