---
name: openbot-config
description: Configure and recover OpenBot on the authenticated Grok Bot Computer. Use for installation, independent provider accounts, models/reasoning, Official stock server delivery versus Custom local routing, logs, routine policy, context compaction, and checked source/image/local-engine updates. Preserve user state; use reconcile for mode, wrap and tunnel changes.
---

# OpenBot config on the Computer

Use this skill on the authenticated Grok Bot Computer. OpenBot supervises the selected native host, normally `/opt/sand/sand-host/host-main.cjs` or legacy `/home/box/sand-host/host-main.cjs`. It does not patch the Mac client. Read [reference.md](reference.md) for exact disk/API contracts and [the coordinated update guide](../../docs/updating.md) before updating either runtime.

Official uses stock server/Temporal delivery and Grok quota. Temporal is vendor managed; OpenBot does not deploy that cloud service. Custom uses the owned local BOX/OpenBot runtime and its saved provider route. A failed Custom request never falls back to Official/Temporal. `openbot-mode` is authority; a retained catalog does not imply Custom. Official preserves the plan, credentials and saved local engine.

One owned loopback process on `127.0.0.1:9280` serves the panel, `/api/*` and Custom `POST /v1/chat/completions`. Mutations that change mode/wrap/tunnel use `DesiredState` and reconcile. Never adopt a foreign listener, start a second hop or launch a tokenless native host.

## Inspect before changing

Read the selected host/data paths, literal mode, current catalog/default, bot pins/efforts, pauses, routines, engine/follower policies and installed source receipt. Inspect owned process identity and any pending operation/reload receipt. Keep credentials and conversation data private; show secret shapes only with `<stored locally>`.

Preserve existing accounts/IDs, model assignments, histories, routine switches, MCP/ACP settings, selected engine and network identity unless the user requested that change. Do not remint identities or copy server-owned bots to BOX merely to repair routing. Profile/roster health is not actual send or delivery proof.

Use JSON for a configuration-only edit when mode is Custom, the selected host has `/* openbot-stock-wrap */`, `/api/state` succeeds and the published `openbot-ui.pid` identifies the owned live service. Plan/secrets/maps/log/injection/bot-model edits are read on the next request. A request already in flight keeps its route. Otherwise reconcile; do not bounce the host for ordinary catalog edits.

## Install or update source

Use a fresh reviewed checkout of the public distribution or your own fork. Keep it separate from live data, credentials, private backups and any old recovery repository history; the exporter binds this recipe to the selected repository:

```bash
gh repo clone nickkostik/openbot-community /tmp/openbot-source
OPENBOT_SRC=/tmp/openbot-source bash /tmp/openbot-source/install.sh --bot-mode
```

For an existing checkout, use its actual path/commit. Follow the printed `OPENBOT_BOT_INSTRUCTION`; poll status at most twice in a Bot turn. Direct installation omits `--bot-mode`. First install starts in Official; an update retains existing desired state. Use `OPENBOT_TUNNEL=off` only when local-only exposure is intended. The repository may require an authenticated GitHub login.

Quickstart begins with read-only `bash quickstart.sh --check` (also the no-argument default). Read `savedExposure`, `exposureReadable` and `loopbackInstallAllowed` independently of `ready`: prerequisites can be ready while a saved public tunnel forbids loopback install. `--install` refuses existing `cloudflare`/`cloudflare-quick` before invoking install or changing tunnel policy. Unknown/unreadable/unsafe saved exposure blocks every quickstart action without echoing its contents. Preserve the policy and use the user's explicit intended access; `--public-tunnel` is separate public-access authorization, never an automatic fallback. Preflight success is not a native installation or chat test. [Exposure preflight contract](reference.md#quickstart-exposure-preflight).

Source installation stages and validates code/dependencies, keeps a previous release and reports actual activation/rollback. Reused source is retention, not an update. An installer `success` or live panel means source/service setup only: verify the loaded native engine and a real intended-client send/tool-return/delivered reply separately. Installation does not remint identities, delete existing state, enable paused routines or replay work by default. A native image update, a Custom/Official identity handoff and cross-mode live history/context/session reconciliation are separate operations; none is implied complete. [Installation and privacy boundaries](reference.md#installation).

The installer explicitly invokes `install --official-controls-fallback` and uses `tunnel on --official-controls-fallback --json` for its existing authorized public setup step. Both capture saved raw mode once for desired state and `expectedMode`. Only explicit saved Official with logging enabled and an unchanged unmarked host lacking a unique usable hook can update/start controls without host edits or bounce. `officialLogging: "unavailable-host-hook"` means optional request logging is unavailable; relay it with installer/status success. This differs from `--keep-service`, which retains a healthy listener. Custom/default install, default tunnel and UI save checks stay strict; changed-mode/foreign-listener refusals remain. The flag does not authorize public access or replace exposure preflight. Controls availability proves neither logging nor inference/chat delivery. [Installer fallback and result fields](reference.md#installer-fallback-for-unavailable-official-logging).

## Mode and tunnel

Use `openbot official` or `POST /api/save` with `{"kind":"official"}` for Official. Keep the catalog/secrets. Explicit trimmed Official is a successful guard no-op, independent of provider quota or saved catalog; its tick skips Custom patrol/deferred bounce. Official reconcile stops the Custom guard with self-PID protection.

For separately authorized Official roster repair after a completed remint, inspect `official-dispatch-recovery.py --plan` before an explicit no-flag run. This manual helper requires Official already selected; it does not switch mode or run automatically. Both counterpart IDs converge to one validated owned stock target, unselected counterparts are hidden and sidebar/pin/main preferences are preserved without duplicate self-pairs. Both UUID/server bindings and ownership are checked; the selected target kind must match its journal kind, so a converted plain agent cannot replace a room. Retain every original: this selects a roster, not a live history/SDK/routine merge, delivery proof or permission to prune. [CLI, selection guards and receipts](reference.md#explicit-official-roster-selection).

Select Custom through a saved model and reconcile; do not infer Custom from an unmarked host or claim a completed identity handoff from save success. A stock host with desired Custom needs checked runtime recovery. Existing live-context/session branches and some native-client mode transitions still need separate verified reconciliation; never resolve them by deleting a branch or silently substituting stock delivery. Guarded repair of missing/corrupt mode requires a valid catalog and raw mode checks before writes/after observation; a changed explicit Official choice wins.

When an opted-in Custom recovery already has a completed journal and real verified BOX counterparts, it can reselect those same identities instead of minting another generation from paired originals. All targets must pass actual owner/BOX binding, native roster and full copied-publication checks; current source roles and local plus fresh platform payloads must already be covered before any scheduler/visibility write. Changed roles, uncopied or same-ID divergent history refuse rather than hiding a branch. Selection retires original schedules, preserves target routine/model/effort state and restores recorded preferences under repeated Custom checks. It neither merges live SDK/history state nor proves native admission, delivery, billing or prune safety. An all-Temporal roster is still not local. [Bounded counterpart reuse](reference.md#reuse-existing-box-counterparts-in-custom).

Use `set-expose` / `openbot tunnel on|off|status` for the saved exposure policy. Preserve an existing named connector and its private credentials. Tunnel PID, watcher heartbeat and public health are separate from chat delivery. [Modes and service ownership](reference.md#modes-and-service-ownership).

## Add accounts without replacing another account

Prefer the panel's Add account wizard or `add-provider`, with `familyId`, display `name`, origin and privately supplied credential. It allocates an unused account ID, saves zero models and returns `createdProviderId`, preserving mode/default/models/bot choices. Duplicate display names are allowed; IDs are authority. Orphan secret IDs stay reserved.

Then fetch models for that returned ID, import the requested rows with `upsert-model` and explicitly select one only if authorized. Use `update-provider`/`set-secret` for an existing ID. Legacy `upsert-provider`/`custom` derives an ID from the name and changes mode/default; it is not independent account onboarding.

OpenAI sign-in creates a temporary opaque credential draft; save it through `add-provider` without overwriting another login. Import exactly one selected credential-file account, not its unrelated wrapper. OAuth credentials go only to supported direct OpenAI origins. Keep subscription and API-key accounts separate; exhausted quota does not authorize a paid fallback, relay or account replacement. [Accounts and credentials](reference.md#accounts-and-credentials).

## Models, reasoning and limits

Canonical model IDs are `providerId:slug`; plan wildcard compatibility uses the slug plus provider ID. Preserve same-slug accounts, explicit model protocols, parameters and published metadata. A bot pin wins over the global route; a supported bot/exact-model effort wins over the model's configured effort. Clearing a pin preserves saved effort choices.

Reasoning order is `default · none · low · medium · high · xhigh · max`. Use only the routed model's supported levels; `default` differs from clearing a choice. Default output budget is 65536 with operational ceiling 131072; published output/context metadata is separate and does not authorize raising caps. Do not default to 8192 or force GLM `fast: true`.

Use provider-scoped model discovery/catalog refresh. Do not borrow another origin's protocol/limits or merge accounts. Automatic refresh preserves mode/default, bot choices and existing routes; selected efforts hold an incompatible proposed allow-list. [Model schema and bot overrides](reference.md#models-and-bot-overrides). When authorized to maximize context, set only each existing model row’s `contextTokens` to its verified route/provider published maximum, including million-token windows; keep unknown limits unverified and unchanged. Keep working-window guidance and output budgets separate. In Official, edit the saved plan atomically; `upsert-model`/`use-model` activate Custom. Preserve all other settings; the hop hot-reads the JSON without a host restart. [Context-only procedure](reference.md#maximize-verified-model-context).

## Preserve identities during authorized recovery

Fresh installation does not plan a fleet migration. For a separately authorized legacy migration, the planner captures all actual owned BOX profiles in private `protectedProfiles` keyed by canonical UUID and verification requires each unchanged. It has no built-in working-bot identity. Older `workingProfile` authority must resolve from its saved canonical ID or unique saved server ID; ambiguity fails. Preserve originals, complete history, instructions/routines/tool settings and private session roots. Snapshot/ID checks are not a live context merge or permission to prune duplicates. [Migration authority](reference.md#portable-migration-identity-protection).

## Compaction, cache and wire behavior

Custom compaction normally summarizes through the selected canonical model, with only the product's approved overflow routes and each route's own credential/protocol. Do not switch account for authentication/quota/transport errors. Marked zero-tool auxiliary requests have separate cache/session scope and do not alter main-chat compaction state.

Private summaries/checkpoints require exact covered-message hashes and routing/effort identity; a guarded exact suffix may survive native oldest-message eviction. Never edit hashes/cursors or publish cached text. Retain complete latest tool groups, original assistant reasoning and unresolved evidence. Stable local prefixes are not provider cache/billing proof. [Compaction and cache](reference.md#compaction-and-cache).

## Pauses, routines, ACP and MCP

Use `GET`/`PUT /api/pause` and `/api/pause-bots` rather than hand-editing gateway pause. Ordinary missing/corrupt pause is fail-open; the scheduler conservatively holds malformed pause authority. Official without a logging tap is pure stock, outside OpenBot's gateway pause.

The optional local clock uses the native automation path, native ownership/readiness and shared durable claims. Both-mode policy eligibility does not authorize duplicate platform/local dispatch. Preserve routine enablement/holds/receipts; do not replay missed work, add cron or imitate scheduling with manual user turns. Default concurrency is 2, supported integer range 1–12. [Routine policy](reference.md#routine-policy).

ACP model/thinking choices must be advertised session options and confirmed after selection; preserve saved login/global defaults and original resume cwd. Apply reviewed bridge repairs only to explicitly selected owned sources, with syntax/hash/backup guards and an idle managed reload. MCP discovery/authentication and actual tool execution are separate checks. Never grant extra tools or bypass permission/team policy to make a canary pass. [ACP and MCP](reference.md#acp-and-mcp).

## Update both runtimes and recover

Follow [docs/updating.md](../../docs/updating.md): source installer, native preserve-data image update and compatible local engine are separate paths. Require a recoverable backup and a genuine fresh native flush, not old durable status. Keep already verified off-box export archives outside synchronized home without moving a sole copy or excluding live data. Preserve writer fences/CAS/durability and force/empty-store protections. The owned fallback snapshot request allows 180 seconds and requires fresh native `ok: true`; upgrade only the byte-exact previous fallback under ownership/hash checks, preserving native or foreign hooks.

Capture the same exact vendor release for stock Official and the prepared local pair. Hash/contract-checked staging in Official remains unselected with `nativeDeliveryVerified: false`, `activationDeferred: true`. Unreviewed runners/interfaces report `compatibility-required`; no blind ports or parity claim. Custom activation requires actual unpredictable local file-tool plus exact delivered-reply proof and attempt-owned rollback. Official quota tests need authorization beyond local-only work. Evidence is specific to the captured source/generation; a new capture resets proof/report state, retaining only historical `lastAppliedAtMs`, and cannot inherit an older canary. An absent approval-roster API keeps local health busy rather than implying idle.

After timeout, disconnect or client 503, inspect the existing admitted operation and on-box receipt before repeating a mutation. Restore the recorded desired mode after authorized temporary transitions and compare settings/data. Do not promise physical phone/voice or full parity from API/offline tests.

For a fresh local engine, use reviewed public-source preparation and first binding; do not delete an existing pin to force initialization. Save vendor/engine artifacts privately with their exact manifest and compatible baseline. [Local engine preparation and authority](reference.md#local-engine-preparation-and-authority).

Recovery preserves ordinary native Official updates and does not force BOX profiles/roster. Only verified restoration of a previously composed host or a checked neutral native backup optimization may replace/reload the Official host at idle. Custom repair applies the full owned checked pipeline. Unknown contracts leave native delivery intact and report compatibility-required. [Recovery and network](reference.md#recovery-and-network).

For panel-only recovery with literal Official already saved, use reviewed CLI `install --keep-service --defer-host-bounce --json`. Its guarded `serviceOnly` path can start absent controls despite an unsupported optional logging factory while retaining a healthy owned listener, without rewriting host bytes, logging choice, mode, catalog or credentials or restarting the native host. Foreign listeners/observed mode changes refuse; Custom follows normal checks. Computer recovery uses this repair flag. Default CLI and UI saves still require enabled logging compatibility; the installer's separate narrow fallback can load new controls while explicitly reporting logging unavailable. A reachable panel does not prove a tap, inference or native-client delivery. [Recovery semantics](reference.md#official-controls-without-logging-rewrap) and [installer exception](reference.md#installer-fallback-for-unavailable-official-logging).

Optional Tailscale recovery is independently opted in, retains authentication/state and verifies packages/ownership. A started process or heartbeat is not SSH/exit-route proof. Computer replacement still needs your separately authorized external bootstrap/credentials; generic installation provisions no personal external service. Preserve saved network/tunnel choices and verify actual intended reachability separately.

## Logs and verification

Use private bounded request logs and provider Usage to inspect the actual route and provider-reported tokens/cache fields. Correlate hop and harness request IDs; do not count a harness observation as a second provider call. Logging/cached metadata does not establish Grok metering, charges, quota or native delivery. Official taps record only stock traffic that actually reaches them.

Keep bodies off unless authorized, never expose credentials or transcript/summary contents, and use bounded retention. Distinguish configured/staged/selected/loaded/tested/delivered outcomes in the report. [Logs and diagnostics](reference.md#logs-and-diagnostics).
