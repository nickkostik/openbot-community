# OpenBot configuration reference

Use with [SKILL.md](SKILL.md) and [the coordinated update guide](../../docs/updating.md). Source identity is `YOUR_ACCOUNT/YOUR_REPOSITORY`, selected by the reviewed exporter. These are generic product contracts, not a deployed fleet, incident record or delivery claim. Use your own account privately; never commit credentials, private SDK/session state, vendor bundles, generated engine artifacts or conversations.

## Installation

Run the reviewed fork on the authenticated Computer, with the native host already installed:

```bash
gh repo clone YOUR_ACCOUNT/YOUR_REPOSITORY /tmp/openbot-source
OPENBOT_SRC=/tmp/openbot-source bash /tmp/openbot-source/install.sh --bot-mode
```

For a reviewed existing checkout, use its actual path. Bot mode detaches and emits `OPENBOT_BOT_INSTRUCTION`; follow that instruction and poll status at most twice in a Bot turn. Direct install omits `--bot-mode`. Set `OPENBOT_TUNNEL=off` only when local panel exposure is intended. Uninstall through the installed fork's `uninstall.sh`, or a retained reviewed checkout for status after removal, not another project's script.

Installer source precedence is explicit `OPENBOT_REPOSITORY`/`OPENBOT_REF`, checkout GitHub remote/package metadata, saved settings, then the exported product default. A different repository defaults to `main` unless a ref is explicit. A local checkout supplies its actual HEAD/package stamp. Failed downloads retaining existing code keep its old stamp and metadata; do not report an upgrade.

`openbot-install-source.json` lives outside the replaced source tree:

```json
{
  "schemaVersion": 1,
  "repository": "YOUR_ACCOUNT/YOUR_REPOSITORY",
  "ref": "main",
  "commit": "<resolved source commit>",
  "sourceType": "local"
}
```

Metadata is bounded owned regular nonsymlink JSON, maximum 16 KiB, atomically published 0600 after activation. `sourceType` is `local`, `github` or `tarball`. Optional `tarball`/`archiveTarball` contain safe HTTP(S) URLs without credentials/query/fragment. Explicit source overrides are independently validated; corrupt saved settings are refused.

Installation takes `openbot-install.lock`, stages source beside the destination, excludes runtime/private artifacts, validates Node syntax and required parser dependencies, and retains `openbot-previous`. Concurrent install exits 75. Node must actually be 22+; downloaded fallback is SHA256 checked and executed before cutover. Missing required `acorn`/`eslint-scope` refuses activation. Preserve existing mode/accounts/models, bot reasoning, histories/instructions/routines, tool/ACP/MCP settings and private session state. Source rollback, installer success and panel health are separate from a real native send/tool-return/reply. The default installer neither remints identities nor deletes data/replays routines; a native image operation, mode identity handoff and live cross-mode context merge need their own explicit process and verification.

Detached files are `openbot-install-result.json`, `openbot-install.log`, `openbot-install.pid` and corresponding `openbot-uninstall-*` files. Result `status` is `running`, `success` or `failed`, with `startedAt`, terminal `finishedAt`, progress `{stage,summary,updatedAt}`, timings and optional source/commit/error/rollback/restart fields. Python-backed uninstall/status receipts survive removal of the cached Node runtime. Read the actual receipt after a client disconnect; do not restart a mutation blindly.

### Quickstart exposure preflight

From the reviewed source checkout, `bash quickstart.sh --check` (or no argument) checks prerequisites and reads saved exposure without invoking the installer or changing mode/network policy. Run as the authenticated Computer's normal non-root data owner; the script requires its documented commands and a recognized native host. It respects `OPENBOT_SAND_DATA` and explicit `OPENBOT_HOST_MAIN` for preflight selection. It accepts one action: `--check`, `--install` or `--public-tunnel`.

```bash
bash /tmp/openbot-source/quickstart.sh --check
```

Preflight JSON has `phase: "preflight"`, prerequisite `ready`/host/owner/command observations and the separate exposure fields:

| Field | Meaning |
|---|---|
| `savedExposure` | Recognized `unset`, `loopback` or normalized `cloudflare-quick`; stored legacy `cloudflare` reports `cloudflare-quick`. Missing/empty saved policy reports `unset`. |
| `exposureReadable` | Whether saved policy is absent or safely readable/recognized. False is refusal, not permission to infer loopback from a displayed `unset`. |
| `loopbackInstallAllowed` | Safe recognized policy does not already select a public tunnel; evaluate this separately from prerequisites. |
| `modeChanged`, `stateChanged` | False describes this preflight only, not later installer effects or successful native runtime verification. |

`--check` can report `ready: true` alongside `loopbackInstallAllowed: false` for a safely read saved public tunnel. This is a successful inspection, not authorization to run the loopback installer. `--install` instead refuses an existing `cloudflare`/`cloudflare-quick` policy before any installer or tunnel change, preserving its bytes and existing access intent. Do not remove the policy or retry using another action just to pass the check. Resolve intended access from the user's existing explicit instructions; clarify only when that choice is missing. An authorized change away from an existing public route uses the normal exposure controls before a later loopback install, not an implicit policy reset.

The final saved `openbot-expose` file must be current-user-owned, regular and nonsymlink, at most 4096 bytes, valid UTF-8 and one of the recognized trimmed tokens (case-normalized for this check). Unreadable, unowned, symlinked, oversized or unknown content makes every quickstart action refuse before install/tunnel work. The error never echoes raw saved contents. Preserve the original and resolve its authority/format through the reviewed configuration workflow; do not chmod/rewrite/delete it to manufacture readiness.

Only separately authorized `--public-tunnel` selects the optional detached public-access install path; it is not fallback for a refused loopback install or evidence that an endpoint is publicly reachable. That path still uses the installer's saved/explicit exposure authority and normal status instructions. `--install` is the loopback path and deliberately supplies `OPENBOT_TUNNEL=off` only after its exposure guard permits it. Neither a ready preflight nor automated fixture checks prove a real native installation, retained access, mode/identity handoff, tool return, delivered reply or billing. Verify those requested outcomes separately and preserve any admitted operation receipt after disconnection.

### Fresh-source privacy boundary

Use a fresh product export and new Git history, not a copy of a deployed Computer or a public push of a private recovery repository. Public export reads a committed revision, includes only allowlisted product documentation and selected product source, replaces private configuration guides with these generic templates, omits deployment-specific fleet instructions/incidents and runs a fail-closed public-content audit. Audit rejection aborts the export; do not disable it or replace private identities with guessed labels to continue. Public fixture UUIDs must be synthetic. Inspect the export receipt and own source destination before publication; source export alone does not provision or verify an installation.

Keep real bot names/IDs, topology, credentials, logs, conversations, history/compaction sidecars, vendor/engine bundles and SDK/session roots outside public source. Users supply their own authorized native host and provider credentials after installation. Tailscale and public tunnels are independent optional transports, requiring the new owner's policy/login rather than another installation's saved identity or relay. Neither setup proves native app admission, completed mode transitions or full feature parity.

### Paths and overrides

Default data root is `/home/box/sand-data`; installed source defaults to `<data>/openbot`. Keep runtime data separate from source/staging. Known hosts are `/opt/sand/sand-host/host-main.cjs` and legacy `/home/box/sand-host/host-main.cjs`. Startup discovery prefers one recognized running location, otherwise available `/opt/sand`; simultaneous running known hosts require an explicit choice. An argv occurrence alone is not process ownership.

| Setting | Purpose |
|---|---|
| `OPENBOT_SAND_DATA`, CLI `--sand-data` | Data root; CLI wins. |
| `OPENBOT_HOST_MAIN`, CLI `--host-main` | Selected native host; CLI wins. Explicit paths bypass discovery. |
| `OPENBOT_SRC`, `OPENBOT_DEST` | Reviewed local source and installed destination. |
| `OPENBOT_REPOSITORY`, `OPENBOT_REF` | Source repository/ref. |
| `OPENBOT_TARBALL`, `OPENBOT_ARCHIVE_TARBALL`, `OPENBOT_COMMIT` | Safe alternate archives and their actual known source stamp. |
| `OPENBOT_PLAN`, `OPENBOT_MODE`, `OPENBOT_SECRETS` | Plan, mode file and secret store. |
| `OPENBOT_BOT_MODELS`, `OPENBOT_LOGS`, `OPENBOT_PAUSE` | Bot overrides, logging settings and gateway pause. |
| `OPENBOT_MAPS`, `OPENBOT_INJECTION` | Live maps module and injection policy. |
| `OPENBOT_AGENT_DATA`, `OPENBOT_WORKFLOWS` | Agent data / user-skill destination; workflows wins. |
| `OPENBOT_COMPUTER_RECOVERY=0` | Disables starting the panel's computer watcher timer, not existing daemons. |
| `OPENBOT_API_KEY` | Private CLI install credential through environment; never command-line secret flags. |
| `OPENBOT_TUNNEL` | Explicit install exposure choice `cloudflare` or `off`. |

With default data root, known host backups are adjacent `<host>.pre-openbot`; custom roots retain `<root>/host-main.cjs.pre-openbot`. Uninstall can use a valid existing data-root backup first. Verify provenance; do not assume a backup from another location applies. Python hooks retain their documented explicit targets and do not inherit every CLI override.

`GET /api/grok-skills` reads installed project `skills/` first. `POST /api/grok-skills/install` accepts `{}` or `{slug:"openbot-config"}` and copies source-relative files into `/home/box/agent-data/workflows/<slug>/`, preserving unrelated destination files. It skips symlinks and refuses blocked destinations. Only absent local skills permit validated repository/ref fallback; private GitHub installer login is not automatically reused by that fetch.

## Modes and service ownership

`openbot-mode` contains trimmed `official` or `custom`. Official selects stock server/Temporal/Grok quota, preserves catalog/secrets/engine artifacts and stops the Custom daemon, even with `keepGuard:true`, while retaining self-PID protection. Logging can retain a stock tap, not a hop. Custom selects the saved local route; no stock fallback is permitted.

`openbot-expose` stores `loopback` or `cloudflare-quick`; quickstart also recognizes legacy `cloudflare` as the latter. Installer `OPENBOT_TUNNEL=off|cloudflare` is a separate input, not authority to replace saved policy silently. Mode/wrap/tunnel changes use `DesiredState` and reconcile; binding objects contain model/conversation identity, never secrets. Explicit Official remains the guard's successful no-op with no Custom patrol/deferred-bounce fallback. Missing/corrupt mode with valid catalog can repair Custom, but raw `expectedMode` is checked before writes/after observation; changed explicit Official wins. [Read-only quickstart policy guards](#quickstart-exposure-preflight).

The unified owned service handles UI, APIs and hop at `127.0.0.1:9280`. Only the child that successfully binds publishes `openbot-ui.pid`; a foreign listener is refused. Reconcile requires both an answering port and a live published PID. Inspect `openbot-ui.log`; never hand-write a PID or adopt a process merely because it holds the port.

For configuration-only hot reload, all must hold: literal Custom, `openbot-stock-wrap` on the selected host, successful `/api/state`, and a live owned UI PID. Plan/secrets/maps/log/injection/bot settings reload per request/turn. A stock/unmarked host with desired Custom needs reconcile. Source/module changes use a controlled service/payload reload; environment root changes require reload too.

Bot-mode upgrades use `openbot-pending-bounce.json`, `openbot-turn-lease.json` and `openbot-finalize.pid`. Deferred reload waits at least 120 seconds after arming, never during an in-flight request, and observes 90 seconds quiet after a completed answer or 300 seconds otherwise. Only recorded, still-owned native host PIDs started before arming may be signalled. Ordinary JSON edits need no bounce.

CLI includes `openbot status`, `openbot official`, `openbot tunnel on|off|status` and `openbot finalize-host`. Source install/repair uses `install`; explicit new `--origin`/`--model` requires private `OPENBOT_API_KEY`. `--dry-run` proves wrapping; `--census-only` is observation. `install --keep-service` is owned service repair, not a source upgrade: only explicitly saved Official selects the guarded [controls-only path](#official-controls-without-logging-rewrap), while Custom retains normal checked wrap/reload behavior and deferred-idle rules.

### Official controls without logging rewrap

For an already selected literal Official mode, the reviewed installed CLI can recover the owned panel independently of optional native logging compatibility:

```bash
node --experimental-strip-types /home/box/sand-data/openbot/src/cli.ts install \
  --keep-service --defer-host-bounce --json
```

This is an existing `install` option, not a new save kind, source upgrade or mode switch. With Official explicitly saved, `--keep-service` passes internal `serviceOnly: true` and the observed raw `expectedMode` to reconcile. It validates the selected host/process/service authority, rejects a foreign listener and rejects observed mode changes before controls recovery. The service-only branch itself rejects Custom or any mode other than literal Official; a CLI repair whose desired mode is Custom instead uses the ordinary checked Custom pipeline. Never hand-edit mode to make this shortcut apply.

Official controls-only recovery retains an existing owned unified listener or starts it when absent using the normal published-PID/listen checks. Unsupported or ambiguous optional logging-factory anchors do not prevent this branch from serving the panel. Native host bytes, saved logging settings, catalog, credentials and mode remain unchanged; there is no host wrap/reinstrumentation, native restart or inferred payload activation. A successful receipt has `wrapBytesChanged: false` and `hostBounce: "none"`. Existing Official finalization still stops a stale Custom guard with its self-PID protection and reconciles the saved exposure policy. This is not permission to adopt an unknown process, bypass a missing/foreign host, disable logging or erase settings.

Normal source installs and UI saves retain the checked logging-wrap requirements when logging is enabled. Custom still needs its actual checked wrapper/provider path; `--keep-service` is not a universal escape from those checks. Preserving a listener also means it may still be running older service code, so use the normal deliberate update/reload path for source upgrades.

The existing `computer-recovery.py` service fallback passes `--keep-service` with the selected host/data paths and deferred-bounce setting, preserving the Official selection rather than repeatedly failing optional logging instrumentation. Read the owned service PID and `/api/state` separately from logging compatibility and an intended-client stock send/reply. An available panel, retained logging preference or successful service-only receipt does not establish a loaded native tap, fresh captured traffic, repaired roster/message delivery, full mode/context reconciliation or billing. Mode checks describe observed authority, not an atomic transaction against all external writers.

### Explicit Official roster selection

`recovery/hooks/official-dispatch-recovery.py` is a manual roster-selection helper for a separately authorized recovery. It requires literal Official already selected and an existing all-active current remint journal; it does not switch inference mode, mint an identity or run automatically through the supervisor or a save. Inspect its read-only live-authority plan first. Only an explicit authorized invocation without `--plan` applies profile/visibility/preferences changes:

```bash
python3 /home/box/sand-data/openbot-hooks/official-dispatch-recovery.py --plan
# Authorized roster mutation, preserving the already-selected Official mode:
python3 /home/box/sand-data/openbot-hooks/official-dispatch-recovery.py
```

The plan reads the current journal/platform roster and selected target profile without applying visibility/profile/settings changes. Both source and destination server rows must retain their recorded UUID/legacy UUID, saved numeric server ID and owner authority. Duplicate platform UUID rows, overlapping journal pairs, changed bindings or missing/unowned counterparts refuse. The chosen target must be actual Temporal with a matching local profile binding and exactly the journal's recorded kind; changed kind refuses with `Stock counterpart kind changed`. The current destination is preferred only when its Temporal row has that kind; otherwise the original counterpart is considered and must pass the same final target-kind check. A destination converted to a plain AGENT cannot replace a typed ROOM by name, nor can a changed original type bypass validation. This selection is not proof that room delivery or histories are fully reconciled.

`identityMapping` includes both counterpart IDs, each pointing at the selected stock target; it is not only a new-to-old inverse map. References to either counterpart in sidebar sections, pins or the main pointer therefore converge regardless of which identity was already visible. Temporary preference transfer pairs the distinct counterpart IDs at their existing position through visibility handoff. Identity mappings to themselves are excluded from reverse pairing, so an already-selected target does not produce a duplicate self-pair. Exactly the chosen counterpart is revealed and either source or destination that was not selected is hidden; no source data is deleted. Section labels/order, pins, native collapse state, notifications and main pointer are retained and checked with the existing readback guards.

Application keeps the already-selected mode and rechecks Official before platform/native operations and selected profile commits. It privately retains routing/profile backups and writes `openbot-official-dispatch-state.json` after checked selection. These checks are not a global transaction against external writers; inspect partial progress if mode changes during the operation rather than claiming automatic rollback. Preserve all BOX databases, context/session roots, instructions/routine settings and model/reasoning overrides. This is neither a live SDK/history/routine union nor a way to bypass transcript writer fences, replay pending completions, delete duplicates or authorize future Custom reminting.

The plan reports `phase: "planned"`, `mode: "official"`, target count, private identity mapping, `liveHistoryMerged: false` and `pruneAllowed: false`. A completed application reports `phase: "stock-roster-selected"` plus private backup metadata and `nativeAppDeliveryVerified: false`; live-history/prune flags remain false. Keep mappings/backups private and verify an actual intended-client send/reply separately. Roster convergence is not phone delivery, billing proof, full cross-mode context reconciliation or permission to prune retained counterparts.

## Accounts and credentials

Use additive `add-provider`, not a name-derived legacy activation, for a new independent account:

```json
{
  "kind": "add-provider",
  "familyId": "custom",
  "name": "My provider account",
  "origin": "https://api.provider.example/v1",
  "apiType": "chat-completions",
  "secret": "<stored locally>"
}
```

Send credentials privately to the owned loopback API. `familyId` matches `/^[a-z0-9][a-z0-9._-]{0,63}$/i`; `name` is trimmed 1–128 characters and need not be unique. Origin is HTTP(S) without credentials/query/fragment. Protocol is `chat-completions`, `responses` or `anthropic`. Secret/import content is bounded to 256 KiB.

Allocation reserves catalog and orphan secret IDs, using the base then available numbered suffixes. Success returns `createdProviderId`; use it for model fetch/import/edit. Adds a zero-model account and preserves mode/default/pins/efforts/models/other secrets. `update-provider` and `set-secret` address an existing ID. Legacy `upsert-provider`/`custom` slugifies the display name and can change mode/default.

OpenAI sign-in: `POST /api/oauth/openai/start` with `{}` returns `{sessionId,authorizationUrl,expiresIn:1800}`. Complete privately with `{sessionId,callbackUrl}` at `/api/oauth/openai/complete`; it returns only `{ok:true,credentialId,expiresIn:1800}`. Matching state/PKCE/code is required. A blank localhost callback page can be expected when browser and Computer differ. Drafts are in memory for 30 minutes; restart/expiry requires new login.

Save that opaque draft using `add-provider`, `familyId:"openai"`, supported direct OpenAI origin and `credentialId`, with `secret` absent. It persists only the new allocated account and consumes the draft after successful save; refusal retains the unexpired draft. Never put an `oauth` sentinel into `secret` or expose callback/token contents.

Credential-file import sends exactly one chosen supported entry, not unrelated accounts. OAuth needs access+refresh tokens and stays on HTTPS `api.openai.com` or `chatgpt.com/backend-api/codex`; do not send it to relays. Each account has independent refresh, account header, discovery and cache scope. API-key quota is separate from subscription quota; neither account creation nor login proves quota/delivery or authorizes a paid fallback.

`secrets.json` is private and separate from the plan:

```json
{"providers":{"your-account":"<stored locally>"}}
```

Known presets use their direct origins and correct credential family. Go accounts require `https://opencode.ai/zen/go/v1`; do not replace them with Zen PAYG. Native OpenAI/OAuth uses Responses. Preserve existing IDs/protocols; preset names are not migration authority. Automatic refresh excludes blocked intermediary families/origins under the existing product guards.

## Models and bot overrides

Illustrative minimal plan; choose actual provider-supported models/limits privately:

```json
{
  "kind": "custom",
  "hop": {"host":"127.0.0.1","port":9280},
  "hopBaseUrl": "http://127.0.0.1:9280/v1",
  "agents": {"*":{"modelId":"your-model","providerId":"your-account"}},
  "catalog": {
    "providers": [{"id":"your-account","name":"My account","origin":"https://api.provider.example/v1","apiType":"chat-completions","maxTokensDefault":65536,"mapFile":"provider-maps.cjs"}],
    "models": [{"id":"your-account:your-model","providerId":"your-account","slug":"your-model","apiType":"chat-completions","contextTokens":128000,"maxOutputTokens":65536,"reasoningLevels":["default","none","low","medium","high"],"activeReasoning":"default","modalities":["text"],"parameters":[]}],
    "bindings": [{"conversation":{"kind":"wildcard"},"modelId":"your-account:your-model"}]
  }
}
```

`model.id` and bindings use canonical `providerId:slug`; wildcard `agents["*"].modelId` is the slug with its provider ID. Keep compatibility fields in sync. Model protocol overrides provider default, except mandatory native OpenAI/OAuth Responses. Preserve parameters and explicit protocols through edits. Invalid/missing plan yields hop 503, not authority to erase state.

Reasoning order is `default, none, low, medium, high, xhigh, max`. Keep `default` and apply only supported levels. Operational output defaults to 65536, ceiling 131072; generic context defaults to 128000. Provider/model facts can differ. Do not default to 8192 or GLM fast mode. `parameters` are `{id:string,value:string}[]`; supported explicit bot effort clears conflicting inbound/legacy fast reasoning only for the request, not saved plan.

Optional `catalogLimits` retains published facts separately:

```json
{
  "catalogLimits": {
    "contextTokens": 128000,
    "maxOutputTokens": 65536,
    "sourceUrl": "https://models.dev/api.json",
    "providerKey": "example-provider",
    "outputModalities": ["text"]
  }
}
```

Published context is a positive integer up to 100000000; output is 0–524288 or null. HTTPS provenance without credentials/query/fragment and a string provider key are required. Output modalities are text/image/video/audio. Metadata never raises operational output automatically or proves the configured account accepts that route/limit.

Bot settings at `openbot-bot-models.json`:

```json
{
  "assignments": {"<bot-id>":"your-account:your-model"},
  "reasoning": {"<bot-id>":{"your-account:your-model":"high"}}
}
```

A bot pin wins over global/conversation routing; supported exact bot/model effort wins over model effort. Missing/corrupt settings inherit; internal model requests skip bot overrides. `default` explicitly selects provider default, while null API effort clears the choice. Clearing model pins preserves effort choices; switching accounts never transfers a same-slug effort. Preserve unrelated root metadata.

`GET /api/bot-models` returns `{assignments,reasoning,available}`. `PUT` accepts selected `botId`/`botIds` pin edits, exact `reasoningModelId`/`modelIds` effort edits or separate full `{assignments}` replacement. It validates the entire selection. A deleted pin target falls back globally with a stale event, not a silent account remap.

Provider discovery: `POST /api/providers/:id/fetch-models` fetches without importing/selecting. Scoped metadata: `GET /api/model-catalog?modelId=<slug>&providerId=<existing-id>`; unknown provider returns 404, no match returns `lookup.found:false`, without cross-origin fallback. Managed cache uses origin/model scope; do not hand-edit it.

Automatic refresh policy `openbot-model-refresh.json`:

```json
{"enabled":false,"intervalHours":6}
```

Only boolean false opts out; absent/malformed policy enables six-hour discovery. Interval clamps to 1–168 hours. Service checks due time after 15 seconds then every 60 seconds; runs share in-flight work and preserve next due time across restart. Manual `/api/model-catalog/refresh` returns 202 acceptance; inspect GET `autoRefresh` completion.

Refresh uses at most two direct provider calls concurrently and only existing credentials/accounts. It reads latest authority after awaits, preserves removed/changed accounts, default/bot settings/protocols/parameters and never deletes routes omitted by discovery. Explicit context stays; derived context may refresh. Saved output caps never rise and can fall to a smaller positive published limit. A new effort list excluding a selected bot/model effort is held. Plan merge is serialized and compare-checked; no reconcile or bounce.

### Maximize verified model context

For an authorized largest-window configuration, verify each saved route’s positive published `catalogLimits.contextTokens` against its current provider scope and provenance. Use that route’s exact maximum, including million-token values; honor genuinely narrower provider limits. Missing, null, stale or unmatched limits remain unverified and unchanged until verified; never inflate them or borrow another provider’s same-slug value. A Codex recommended working window is operating guidance, distinct from the published route maximum and the separate output-token limit. [Official GPT-6.1 Sol documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol) publishes a 1,050,000-token context window; that fact alone does not prove a configured account accepts a full-window request.

For a context-only edit while Official, do not call `POST /api/save` with `upsert-model` or `use-model`: those operations activate Custom. Back up the private saved plan, check expected original bytes and current mode, then atomically update only the affected existing model rows’ `contextTokens` in the resolved `openbot-plan.json`. Preserve mode, catalog IDs/providers, defaults, bot pins, reasoning, operational output budgets and compaction settings; do not rewrite mode or invoke reconcile. Validate/read back the plan and `GET /api/state`, comparing other settings before/after. The per-request hop reads the new JSON without a native host restart or mode save. Saved configuration and panel API readback establish configuration, not full-context provider acceptance or a native million-token turn.

## Save and HTTP contracts

Loopback `POST /api/save`, `Content-Type: application/json`:

| `kind` | Fields / behavior |
|---|---|
| `official` | Select stock delivery; retain catalog/secrets/local engine. |
| `add-provider` | `familyId,name,origin`, optional `apiType`; `secret` or OpenAI `credentialId`. Independent zero-model account. |
| `upsert-provider`, `custom` | `name,origin,modelSlug,secret`, optional limits. Legacy name-derived activation. |
| `upsert-model` | Existing `providerId,slug`, optional protocol/limits/reasoning/modalities/metadata. No separate update-model kind. |
| `use-model` | Canonical `modelId`, optional `reasoning`; changes wildcard, intentional bot pins still win. |
| `update-provider` | `providerId,name,origin`, optional `secret,apiType`; retains ID. |
| `set-secret` | Existing `providerId,secret`; preserve account identity. |
| `remove-provider`, `remove-model` | Explicit `providerId`/`modelId`; last account/model removal can choose Official and remove the plan. |
| `set-expose` | `expose:"cloudflare"` or `"off"`. |

Success returns `{ok,wrapBytesChanged,snapshot,providers,models,keyedProviders,activeModelId,logSettings}`, with `createdProviderId` for additive onboarding. Saves use reconcile and serialized state authority. Refusals include `host-missing`, foreign listener/wrap, `census-refused`, `syntax-check-failed`, `listen-failed`, `mode-changed`. Refusal is not permission to adopt, force ownership or reset configuration.

Other APIs include GET `/api/state` or `/api/snapshot`, GET `/api/bots`, logs/settings, pause controls, provider discovery and skills/OAuth APIs documented here. Hop `/v1/chat/completions` is inference, not configuration. A successful HTTP response is not native end-to-end delivery.

## Pauses

Use GET/PUT `/api/pause`, not direct file edits. Stored `openbot-pause.json` is `{paused:boolean,at:ISO-time-or-null,note:string-or-null}`; note is trimmed to 500 characters. PUT takes `{paused:boolean,note?:string}`. Missing/corrupt ordinary gateway state is fail-open. Paused hop returns 503 `paused`; it remains alive for health patrol. Pure stock Official without a tap is outside that chain.

`openbot-pause-bots.json` is `{"pausedBotIds":["<bot-id>"]}`. GET/PUT `/api/pause-bots` uses `{botId,paused}` or full `{pausedBotIds}` replacement. Missing/corrupt ordinary state is empty. Native/hop checks apply on the next turn; no bounce. Hidden/deleted inventory entries stay distinct for historical log resolution, without changing native visibility or deleting data.

## Routine policy

### Native local routine clock

Optional `openbot-local-routines.json`:

```json
{"enabled":true,"maxConcurrent":2,"graceMs":120000,"queueTtlMs":3600000}
```

`enabled` must be true; cap is integer 1–12, default 2. Grace is integer 0–300000, default 120000; queue TTL satisfies grace <= TTL <= 3600000, default 3600000. Invalid policy holds dispatch. Local-only opt-in and literal recognized mode are prerequisites, not permission to duplicate stock ownership.

Native BOX ownership, not on a session box, readiness, per-routine enablement, transfer/retirement holds and shared native-running/pending/durable claims all gate execution. Temporal-owned work is excluded. The native automation parser/runner owns execution; no independent cron, platform user-turn substitute or manual-endpoint imitation. One consumed slot must never be replayed after uncertain delivery.

Global/bot pauses hold the clock, and malformed/unreadable pause authority conservatively holds it even though ordinary hop pause is fail-open. Keep `openbot-routine-holds.json.heldAgentIds`, native enablement and receipts intact. Both-mode fixtures or policy eligibility do not prove Official delivery. Loading changed scheduler code requires a controlled idle-owned host reload; a cap edit alone is not proof of the loaded module or actual concurrency.

## Reuse existing BOX counterparts in Custom

The existing opted-in Custom dispatch coordinator can reuse a completed current journal's actual verified BOX destinations after an authorized return to Custom. It compares owner-visible server authority first. When that current authority is `bound-box`, already-paired original IDs are excluded from fresh source detection and the coordinator calls internal `Reminter.reselect_active()`. This prevents a visible original left by Official roster selection from creating another copy generation merely to restore the same target. There is no new CLI subcommand or live-merge override. Official remains owner authority: selecting Official does not authorize a Custom re-selection or automatic mode flip.

A nonempty all-active current journal is required. If visibility, preferences and recorded finalization already select the destinations, the method can return `state: "already-selected"` without mutations; that fast return is not a fresh publication/delivery test. Before an actual re-selection, every destination must pass current actual owned BOX binding, exact journal target server ID, native roster, SQLite/profile identity and full ordered local-history publication verification. A forced local profile label, healthy hop, a saved Custom token or an all-Temporal server roster cannot satisfy that authority. This does not migrate a Temporal identity or prove that the current platform will admit a new BOX turn.

Every original source must still be owned, retain exact journal source UUID/legacy UUID/server binding and kind, and match its saved local profile server ID. Source/destination profile roles are compared after lineage normalization, excluding only routing harness/server-ID fields. Before the first scheduler or visibility mutation, a fresh full platform transcript export and current local source history must each be covered by the destination's local history as complete normalized payloads. Matching entry IDs alone do not qualify. Changed instructions/roles, uncopied records or same-ID divergent payloads refuse with an explicit reconciliation requirement, preserving both branches instead of hiding, overwriting or automatically reminting through the conflict. All pairs must pass this preflight before proceeding; the helper lock and preflight are not a global transaction against external writers.

The method privately retains platform preferences and source evidence under `recovery-snapshots/box-reselection-<timestamp>-<nonce>/`. It then confirms old cloud schedules paused, backs up/disables enabled local source schedules and reveals the existing target while hiding the original. Custom/local-only authority is rechecked before each visibility operation and before final preference work. Target database/files, model and per-model effort choices and current target routine switches are not copied or reset from the old source; recovered events and pending completions are not replayed. Finalization restores section/pin/main references from current preferences using recorded aliases while preserving labels, native collapse/notification choices and unrelated entries. Keep source data, private snapshots and original context/session roots.

A mutation result reports `state: "reselected"`, count, private backup path, `liveHistoryMerged: false`, `sdkContextsMerged: false` and `pruneAllowed: false`. Managed `openbot-custom-dispatch-state.json` keeps `nativeAppDeliveryVerified: false`, `billingVerified: false`, `liveHistoryMerged: false` and `sdkContextsMerged: false`; mode or selection success does not upgrade these into delivery/merge proof. The coordinator's existing captured routine-restoration progress and validated peer refresh remain distinct from actual execution. Inspect current authority and intended-client send/local attach/reply separately, and verify billing independently where claimed.

This is bounded identity/preference reuse when the destination already contains the current required history/role payloads. It is not an SDK/context union, routine-definition merge, Temporal-to-BOX server migration, new native admission guarantee, deletion/prune workflow or proof that every mobile/tool/session path works. If all actual destinations are Temporal or source branches differ, stop at the reported guard and complete separate authorized reconciliation rather than labelling the fleet local or clearing held evidence. A fresh-copy workflow remains a different explicit recovery with its own proof requirements.

## Complete fresh BOX remint workflow

Identity copies/remints are a separate explicit user-authorized recovery, not a source/model update or an automatic response to drift. The installed `remint-box-fleet.py` retains migration authority and writes its own private `openbot-box-remint.json`; do not import somebody else's fleet journal or roster.

`plan` and `verify` are read-only diagnostics; live SQLite reads can coordinate WAL state. Mutations require literal Custom and owned local-only opt-in. Plan eligibility, journal creation, complete copy, BOX registration, publication, ordered paginated history verification and activation are distinct phases. Keep original profiles/data and private backups until verified handoff; copied routines start paused.

```bash
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py plan
# Only after an explicit complete-copy authorization:
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py init
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py stage --agent-id '<journaled-source-id>'
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py register --agent-id '<journaled-source-id>'
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py publish
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py verify --agent-id '<journaled-source-id>'
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py activate --agent-id '<journaled-source-id>'
```

Journal entries retain their own source/destination UUIDs, server binding and staged/active proof; never import another fleet's IDs. Verify SQLite/metadata identity, actual BOX platform/native binding and copied ordered full published payloads, not IDs alone. Keep divergent same-ID history and session evidence under review instead of overwriting it or forcing registration. Publication requests an owned idle reload; save/list/publication receipts do not prove intended-client send/reply, a mode identity transition, cross-mode live context merge or billing. Refused Temporal binding is not fallback authority.

Activation verifies destinations before retiring sources, preserves sidebar/main identity settings and captures current source routine switches. Retired server schedules must be explicitly disabled and read back before cutover, not merely hidden locally. `activationAutomationStates,routineRestoreProgress,routinesRestored` prevent replaying enablement over later user choices. For an authorized interrupted active handoff, `restore-routines --agent-id '<journaled-id>'` completes only unfinished captured switches under current binding/ownership checks; it does not clear global holds or replay missed runs.

Existing journals are retained. Only an explicitly requested next generation can roll an entirely active journal forward while keeping prior mapping/backups and rejecting cycles/conflicts. No user/fleet IDs, fixed roster, copy artifacts or credentials belong in product exports.

### Portable migration identity protection

The legacy `migrate-local-bots.py` planner has no built-in working-bot UUID and no automatic role/name exception. For an explicitly authorized migration it snapshots every actual owned BOX row whose agent/legacy UUIDs match into the private journal's `protectedProfiles`, keyed by canonical UUID. Each value is the full captured local profile; verification requires that same profile unchanged. Changed/missing profiles or invalid authority fail rather than choosing a special bot from another installation. The journal/backup contains real deployment data and must remain private.

A legacy journal's `workingProfile` may resolve only through the saved canonical `id` or a unique saved `serverId` match in the current installation. Missing/ambiguous resolution fails; there is no installation-default UUID fallback. Existing staged-copy integrity, metadata and unique BOX server-binding checks still apply. A read-only verify never calls plan, remints, registers, edits profile state or silently repairs authority; SQLite live reads can still coordinate WAL sidecars.

These profile invariants protect existing state, not proof of a live context/session merge or native delivery. Keep source branches, session roots, pending tool/routine completion evidence and original instructions/settings. Do not prune duplicates from an archive/ID match, graft opaque SDK state or replay recovered actions to manufacture a passing test. Fresh install and model configuration never authorize such a migration; cross-mode live state consolidation and each client mode transition remain explicit verification work.

## Compaction and cache

Custom `payload/context-truncate.cjs` normally chooses the selected canonical model with its effective supported reasoning. Kimi K3 is excluded; its selected-primary exception uses an approved configured cheap fallback. Missing/invalid targets or forbidden MetaKey/ModelHub/N5 routes are refused. Current approved overflow families are configured DeepSeek v4.1 Flash, GPT-6.1 Sol and Muse 1.3 Contributor routes, with account scope, credential/protocol and context preserved. Never add an account or rewrite an origin to satisfy compaction.

Only recognized context/input-size overflow can advance a dispatched chunk to an approved fallback. Auth, quota, generic errors and transport failures do not switch accounts. Sequential chunks are at most 65536 estimated transcript tokens, fitted to context and generation allowance; each request has an absolute 180-second deadline and each active run a ten-minute budget. Incomplete results retain accepted chunks and bounded exponential cooldown rather than fabricating a summary.

Trigger above 85% estimated input or above 35% after a window shrink; trim aims toward 35%, keeping leading instructions and the complete latest message/tool group. Estimates include assistant reasoning, tool calls and schemas; they are not provider tokenizer/billing evidence. A protected group can exceed the target. Never manufacture missing tool results.

State is `<data>/openbot/compact-state/<chat-key>.json`, with private immutable transcript sidecars for resumable work. Useful fields include `lastContextTokens,lastEstimate,lastDropped,lastTrigger,compactedPrefixCount,compactedPrefixHash,summaryForDropped,summaryPrefixHash,summarySource,summaryContextTokens,pendingDroppedCount`, optional per-row hashes and private summaries.

A v2 `summaryJob` records `version:2,fingerprint,routingKey,coveredCount,coveredHash,coveredHashes,transcriptHash,cursor,text,chunks,failures,nextAttemptAtMs,lastError`. Exact content/route/effort authority and a bounded owned nonsymlink hash-checked sidecar are required. A guarded oldest-message eviction rebase needs at least four original rows, three retained rows, two distinct hashes and exactly one exact suffix match. Edits/reordering/ambiguous coverage reject reuse. Never hand-edit cursor/hashes/text or publish cached data.

Native zero-tool auxiliary requests carry a strict internal marker, skip main-chat compaction/cancellation and get separate cache/session namespaces within their owned scope. Markers are stripped before provider dispatch. Stable provider/conversation cache keys and local summary reuse do not prove provider cache hits, charges, Grok metering or delivery; inspect provider-reported usage independently.

Original assistant reasoning stays separate `reasoning_content`, including tool-call/reasoning-only rows. Host image parts become proper image content. Byte dedup/history quotas/current-turn caps and the 3.4 MiB wire budget account for messages, tools and envelope. Protect the full latest turn/tool group; if protected input cannot fit, local 413 refuses before dispatch rather than silently truncating current evidence.

## Logs and diagnostics

`openbot-logs.json`:

```json
{"loggingEnabled":false,"logBodies":false,"logBodiesOnError":true,"logRetentionDays":7,"maxBodyCaptureBytes":65536,"maxRecords":2000}
```

Retention is 1–365 days; capture 1024–1048576 bytes; records 1–10000. Prefer PUT `/api/logs/settings` for pruning and Official tap reconcile; GET settings/logs/by-ID and POST `/api/logs/clear` are separate controls. Body logging requires authorized private handling. Never print credentials, full transcripts or summary sidecars.

Hop and custom-host records share request identity for correlation, but harness observation is not a second provider request. Usage uses actual provider/model and reported input/output/cache fields; cached metadata and UI health are not billing/quota/phone proof. Read-only roster diagnostics/profile repairs do not establish native identity admission or delivery.

## Injection

Optional `openbot-injection.json` controls only named Custom-hop hardening after structural conversion. Missing/invalid policy fails closed to off; Official tap is untouched. Modes are `off`, `dry-run`, `enforce`. Dry-run computes diagnostics without extra runs/mutation; enforce remains explicitly bounded and opt-in.

```json
{
  "mode":"off",
  "layers":{
    "l1":{"enabled":true,"startOfTurnAckThreshold":1,"watchingSilenceThreshold":6,"earlyResultThreshold":0},
    "l2":{"enabled":true,"maxAdditionalRuns":1,"terminalDecisionTimeoutMs":250,"timeoutMs":120000,"maxAdditionalPromptTokens":131072,"maxAdditionalCompletionTokens":2048,"maxAdditionalCostUsd":0.10},
    "l3":{"enabled":true,"maxRedrivesPerEpoch":1,"ttlMs":300000}
  }
}
```

The hop reads policy per request. Settings GET/PUT `/api/settings/delivery` accepts mode and optional boolean layer enables; layers with off are rejected, while off preserves saved layer objects. Environment `OPENBOT_INJECTION_MODE` and layer overrides can pin effective settings. No policy edit selects a model, changes credentials or authorizes extra inference beyond its explicit bounds.

## ACP and MCP

Keep native MCP configuration, tool grants and authentication under their existing owner's control. Inventory/discovery and successful auth do not prove a real tool works. Verify the requested tool and its permission path; do not create namespaces, dummy services or override team policy to satisfy a canary. Engine interfaces use exact reviewed source recipes and actual executable contracts; unknown changed anchors report compatibility-required.

OpenCode ACP starts with `acp`, without forced CLI model/variant flags. Select supplied model then thinking through advertised session config categories `model` and `thought_level`, confirm returned `currentValue`, and preserve native saved login/global defaults. Resume with original cwd and the same supported session identity. Unsupported options and reported JSON errors/timeouts/cancellations are failures, not successful delivery.

External ACP bridges are configured and maintained in their own projects, separately from OpenBot save. Use each user's own reviewed source paths, accounts, model catalog and credentials; retain that bridge's backup, syntax/compatibility checks, authorization and managed idle reload procedure. Installation-specific source patchers and hardcoded account overlays are not shipped in the public export. Do not fetch private deployment helpers, apply another installation's paths/account policy or assume installing OpenBot changes an external bridge.

## Local-engine preparation and authority

### Explicit tested local-engine pin

Engine policy `openbot-local-engine.json` is private bounded owned regular nonsymlink JSON, maximum 16 KiB:

| Field | Contract |
|---|---|
| `schemaVersion`, `enabled` | Exactly 1 and boolean; active pin requires true. |
| `stockSha256`, `wrappedSha256` | Exact lowercase SHA256 of matching saved artifacts. |
| `generationId` | Optional 32 lowercase hex selecting immutable `openbot-local-engine/generations/<id>/`; absent uses the legacy fixed pair. |
| `clientVersion`, `hostVersion` | Version metadata, not compatibility or delivery proof. |
| `upstreamVersion`, `upstreamClientVersion`, `upstreamSourceSha256`, `upstreamSyncStrategy` | Checked captured provenance. |
| `boxCompatibilityBase` | Managed `{stockSha256,wrappedSha256,generationId?}` preserving the verified baseline and bounded prior-policy history. |

Directories/files must be owned and nonsymlinked; source is regular, at most 48 MiB, exact-hash checked and has required native send/scheduled runner/wrap structure. Keep immutable stock/wrapped files and referenced baseline/prior generations private and encrypted in recovery. No exported vendor source or hash edits to bless unknown interfaces.

### Prepare a reviewed BOX engine from public vendor source

For a fresh installation, prepare a reviewed baseline and same-release pair using the complete installed package:

```bash
python3 /home/box/sand-data/openbot/recovery/hooks/prepare-local-engine.py \
  --package-root /home/box/sand-data/openbot \
  --host-main /opt/sand/sand-host/host-main.cjs
```

Select the actual host and compatible Node explicitly when defaults differ. Preparation checks reviewed vendor/archive/source identities, transformations and actual native offline dependencies, saves immutable latest/baseline stock/wrapped pairs and `preparation.json`. Report is `state:"prepared"`, with `activated:false,liveHostChanged:false,nativeDeliveryVerified:false`; it changes no mode, pin, routines or identities. Unreviewed latest source refuses instead of choosing an older release blindly.

### Select the first local-engine manifest

For first binding only when no engine-policy path exists:

```bash
python3 /home/box/sand-data/openbot/recovery/hooks/initialize-local-engine.py --plan \
  --prepared-directory /absolute/path/from-completed-preparation \
  --root /home/box/sand-data \
  --package-root /home/box/sand-data/openbot \
  --host-main /opt/sand/sand-host/host-main.cjs
```

Planning reconstructs the exact artifacts against current payload/dependencies and can create staging/locks, but selects no policy. Explicit authorized `--apply` repeats checks and exclusively publishes immutable baseline/latest generations and manifest. Any existing policy, including disabled/malformed/symlinked authority, refuses first binding; preserve it and use the checked upgrade path. `activated:true` here means selected manifest only; `hostReloadRequested:false,nativeDeliveryVerified:false`. An already opted-in watcher can later load it after idle checks. Do not confuse selection with delivery.

### Upgrade the trusted pinned engine pair

Existing pins use `refresh-local-engine.py --plan` to validate the complete pipeline and explicitly authorized `--apply` to select a checked generation; it does not directly signal the host. The full owned recovery/follower activation path still requires controlled loading, actual local file-tool/exact-delivered-reply canary, configuration comparison and attempt-owned rollback. Do not manually copy a bundle over the native host.

`/prepare-upgrade` is a host-bundle reload checkpoint with `owner: "upgrade"`; native `pauseTurnsForRecreate` intentionally refuses that owner. Preserve-data `RecreateSandBox` preparation instead uses authenticated `/api/prepareBoxForRecreate` and requires native `owner: "recreate"`, `quiescent: true` before a fresh snapshot and admission. Resolve leftover host checkpoints through owned native supervisor controls when safe to reload, preserving configuration; never clear native fences or mask `busy`. Reconcile an uncertain admitted operation instead of replaying it. Require a successful terminal image result and separate post-image delivery proof.

### Opt-in upstream release follower

Follower policy `openbot-upstream-sync.json`:

```json
{"schemaVersion":1,"enabled":true,"pollSeconds":1800,"strategy":"instructions-and-compatible-engine"}
```

Owned bounded nonsymlink policy; poll integer 60–86400, default 1800; only the stated strategy is accepted. Enabled tested pin/artifacts and complete helper/parser set are prerequisites. Vendor source is captured from verified bundle provenance with archive/source SHA checks; downloads/extraction/ownership/locks are bounded. Network assessment occurs separately; activation respects idle/memory/checkpoint authority.

### Same release local engine preparation in Official

Official keeps native stock updates and stages a complete same-release local pair without selection/send. `openbot-upstream-local-ready.json` has schema 1, phase `local-release-ready`, complete proposed manifest, `savedAtMs,basePolicySha256`; `nativeDeliveryVerified:false,activationDeferred:true`. Follower state additionally reports generation/version/localEngineSaved/configurationUnchanged. Readiness or a partial instruction backport is not full same-release parity.

Evidence belongs to the exact captured source and engine generation. A newly captured release starts fresh proof/report state: it does not inherit an older generation, canary, readiness, error or rollback result; only historical `lastAppliedAtMs` is retained. Previous delivery proof cannot certify a new source, and passed native offline contracts do not establish live activation or delivery.

`localHealthContract: "optional-approval-roster"` is an optional exact-source reviewed recipe for a vendor that removed `allRunningAgentsAwaitApproval` and `agentIdsWithPendingApprovals`. It restores local busy/upgrade accounting and checks whether the native approval-roster method is a function. An absent method conservatively keeps local health busy; it never implies idle or authorizes an upgrade. This port does not enable the unsupported `autoReviewSelector` or `needsAuthLiveState` gates; their reviewed defaults remain false. Unknown recipes or changed anchors require compatibility review.

Restore removed credential interfaces only through exact-source reviewed `sharedApiPorts` and `sharedClassPorts` recipes with complete checked export/member sets. A reviewed `startCredentialProvider` port may restore `accessForAgentWindow`, `status` and `agentToolLease`; a reviewed `CredentialCoordinator` port may restore `refreshedAtMs`, `getDirectory`, `refresh` and `createAccess`. Keep the latest native credential lifecycle and authorization authoritative, refuse missing or stale credential references and never mirror secret values. Changed or ambiguous contracts require review; restoring an interface does not establish live local delivery.

An exact-reviewed `retainedExtensions` entry for `autoReviewExtension` retains the complete local approval extension and matching `AutoReviewService` controller when upstream has reduced these interfaces to server-card cleanup. Preserve owner/team policies and configured review: disabled settings remain off, while enabled settings enforce configured review. Executable offline contracts must construct the actual selected extension, bind a runner, check disabled/enabled settings, confirm an empty awaiting roster and dispose correctly. These lifecycle checks do not establish final live delivery or authorize disabling approvals.

`retainedExtensions` accepts only exact-reviewed `inferenceExtension`, `autoReviewExtension`, `teamAdminPolicyExtension` and `sessionExtension` names; other names refuse. Reviewed local approval restoration must include its `teamAdminPolicyExtension` dependency with the matching `createTeamAdminPolicyService` factory. Preserve the real `isAutoReviewEnforced` response, cloud-agent restrictions and `waitForPolicy`, cache invalidation and disposal semantics. Offline contracts must instantiate the actual selected policy factory, exercise policy wait, true/false enforcement and cloud restrictions, then pass that service into the real review runner binding. These policy contracts preserve configured enforcement and do not establish final live delivery.

An exact-reviewed `sessionExtension` port must retain local form start/end/remap and virtual-card APIs with the complete matching `UserFormService` module (`src/host/extensions/session/user-form-service.ts`) and retained transcript `UserFormResponses`. Keep consented values bound to their actual holds and request IDs. Native offline contracts must use the actual selected form service to check no-hold and duplicate-hold refusal, unknown-field refusal without filling, a wrong request ID leaving the hold intact, matching settlement and forget behavior. These lifecycle contracts do not establish delivered-turn proof.

`mcpListingReader: "live-native-catalog"` is an exact-reviewed recipe that exposes `McpHostService.api.peekCursorScmBuiltinListed(provider)` through the existing `hostMcp.peekCursorScmBuiltinListed(provider)` reader. Keep its receiver, current live catalog, ownership and modern MCP permission checks intact; do not synthesize or cache a listing result. Native executable contracts must exercise false-to-true live listing changes through the actual selected `McpHostService`; these interface checks are separate from delivered-turn proof.

Redacted private turn diagnostics may include optional `missingMember` only when a strict JavaScript member-call `TypeError` signature identifies it; otherwise omit that field. Never record receiver text, free-form errors, prompts, tool arguments or URLs. A diagnostic receipt is not delivery proof.

Custom pins the selected local bundle/image path only under local-only enablement and uses exact reviewed compatible source or BOX interface ports. Unknown runners/interfaces/anchors fail compatibility; no cloud fallback. Activation must observe a real unpredictable file and exact delivered reply before accepting the engine. Each generation/runtime needs its own proof.

## Recovery and network

The panel starts the common recovery timer; network policy is inspected independently of mode/local-only routing. The computer watcher requires owned factory `/home/box/sand-data`, local-only opt-in and literal Custom/Official. Custom roots may support independently opted-in network recovery, not automatic retargeting of fixed computer hooks. A started PID/held lock is not a verified running owned watcher or native delivery.

Official recovers panel/tunnel/backup and follows upstream without forcing BOX profiles/roster or pinning normal stock updates. Verified restoration touches only the known composition-marker targets and exact owned authenticated captured vendor source; invalid capture refuses instead of falling back to an older pin. Active composed-host restoration mode/CAS checks may synchronize native version. Ordinary newer marker-free host/version remain untouched. Changed verified restoration or neutral backup optimization may request one owned idle reload after fresh mode/ownership checks.

Neutral canonical status remembers only validated reads/completed native writes, deduplicates cold reads, never seeds failures as success and preserves newer receipts. Successful empty reset reports durable false. SandBoxStore-only large serialized requests use native Connect gzip; stock clients remain unchanged. These repairs preserve payloads/auth/interceptors/etag/writer/conflict/durability and do not prove a fresh backup. Direct S3 manifest writes are a distinct path. Unknown contracts report compatibility-required, preserving native delivery.

Backup preparation is confined to `/tmp/sand-box-store-pack` and `/var/run/anyrun-hibernate-hook`, with symlink/foreign-owner refusal and no recursive data ownership sweep. A missing hook is installed as root-owned 0755 with an exclusive link. Only the byte-exact previous owned fallback may be upgraded: save its original bytes, require a root-owned regular target, recheck its SHA256 and atomically replace through noninteractive sudo. Other native or foreign hook contents are preserved. The credential-free fallback reads the gateway token only at execution, requests authenticated native `snapshotBoxStoreNow` with `includeIdleOnly: true` and a 180-second timeout, and exits successfully only for parsed literal `ok: true`. Preparation failures are recorded separately in `backupRepair`; successful hook status is `existing`, `installed` or `updated`. A repaired hook or cached receipt does not establish a fresh snapshot or image completion. This repair changes no inference mode and bypasses no writer fence, erase or durability check.

Custom repair validates the complete owned transformation pipeline, keeps backups, compares actual expected bytes and reloads only through owned idle/checkpoint authority. Do not force profiles, migrate identities, enable routines or remint bots without specific authorization. Retain all current data and rollback authority.

### Optional persistent Tailscale recovery

Optional `openbot-tailscale.json`, owned regular nonsymlink maximum 4096 bytes:

```json
{"schemaVersion":1,"enabled":true,"autoUpdate":false,"networking":"userspace"}
```

| Field | Contract |
|---|---|
| `schemaVersion`, `enabled` | Exactly 1, required boolean. Missing/false ends future recovery without clearing live login/preferences. |
| `autoUpdate` | Optional boolean; verified package staging only, no live restart. |
| `networking` | `userspace` default or `kernel`; changed live mode reports pending restart, preserving the daemon. |
| `exitNodeIP` | Optional validated Tailscale address of an already approved user-selected exit peer; kernel required. Omission does not clear preferences. |
| `exitNodeAllowLANAccess` | Optional boolean, default true. |
| `hostTransportCIDRs` | At most 16 strict allowed local-infrastructure CIDRs; destination main-table exceptions, no public/default routes or conflicting rules. Omission does not delete existing rules. |

First stage an explicitly selected verified stable Tailscale package with installed `tailscale-recovery.py --install-version <version>`; it does not enable/login/start/restart the daemon. Then opt in and invoke the existing `recovery-bootstrap.py`. The network loop checks every 15 seconds, has its own lifetime singleton lock and private PID/heartbeat/log, and retains existing authenticated state. A live package update stages for next recovery only.

Kernel launch validates checked binaries, TUN and narrow networking capabilities while retaining native-owner UID. Never adopt a foreign daemon or broaden ownership/permissions. Existing identity/state, configured exit peer, host transport rules and tunnel credentials stay private and recoverable. Runtime/peer-online/heartbeat is not proof of actual SSH or intended route; verify the authorized reachability separately.

Generic installation does not install an external host/bootstrap, tunnel credentials or wake policy. Your separately authorized off-box transport is required for Computer replacement or hibernation recovery; a live-Computer watcher cannot bootstrap a stopped panel/pod alone. Keep external credentials outside product source. See [portable recovery](../../docs/portable-recovery.md) and [coordinated updates](../../docs/updating.md).
