# OpenBot configuration reference

Use with [SKILL.md](SKILL.md) and [the coordinated update guide](../../docs/updating.md). Source identity is `nickkostik/openbot-community`, selected by the reviewed exporter. These are generic product contracts, not a deployed fleet, incident record or delivery claim. Use your own account privately; never commit credentials, private SDK/session state, vendor bundles, generated engine artifacts or conversations.

## Installation

Run the reviewed fork on the authenticated Computer, with the native host already installed:

```bash
gh repo clone nickkostik/openbot-community /tmp/openbot-source
OPENBOT_SRC=/tmp/openbot-source bash /tmp/openbot-source/install.sh --bot-mode
```

For a reviewed existing checkout, use its actual path. Bot mode detaches and emits `OPENBOT_BOT_INSTRUCTION`; follow that instruction and poll status at most twice in a Bot turn. Direct install omits `--bot-mode`. Set `OPENBOT_TUNNEL=off` only when local panel exposure is intended. Uninstall through the installed fork's `uninstall.sh`, or a retained reviewed checkout for status after removal, not another project's script.

Installer source precedence is explicit `OPENBOT_REPOSITORY`/`OPENBOT_REF`, checkout GitHub remote/package metadata, saved settings, then the exported product default. A different repository defaults to `main` unless a ref is explicit. A local checkout supplies its actual HEAD/package stamp. Failed downloads retaining existing code keep its old stamp and metadata; do not report an upgrade.

`openbot-install-source.json` lives outside the replaced source tree:

```json
{
  "schemaVersion": 1,
  "repository": "nickkostik/openbot-community",
  "ref": "main",
  "commit": "<resolved source commit>",
  "sourceType": "local"
}
```

Metadata is bounded owned regular nonsymlink JSON, maximum 16 KiB, atomically published 0600 after activation. `sourceType` is `local`, `github` or `tarball`. Optional `tarball`/`archiveTarball` contain safe HTTP(S) URLs without credentials/query/fragment. Explicit source overrides are independently validated; corrupt saved settings are refused.

Installation takes `openbot-install.lock`, stages source beside the destination, excludes runtime/private artifacts, validates Node syntax and required parser dependencies, and retains `openbot-previous`. Concurrent install exits 75. The installer requires the supported version and executable capabilities described below; its checksum-verified fallback is staged and tested before cutover, preserving system Node and prior private runtime on invalid staging. Missing required `acorn`/`eslint-scope` refuses activation. Preserve existing mode/accounts/models, bot reasoning, histories/instructions/routines, tool/ACP/MCP settings and private session state. Source rollback, installer success and panel health are separate from a real native send/tool-return/reply. The default installer neither remints identities nor deletes data/replays routines; a native image operation, mode identity handoff and live cross-mode context merge need their own explicit process and verification.

### Supported Node runtime

The source installer requires a stable Node 22.13+ release within 22.x, or Node 24+ (`^22.13.0 || >=24`), not a major-only 22+ test. Earlier 22.x and all 23.x releases are unsupported. A matching version must also execute `--experimental-strip-types` and expose built-in `fetch` and `WebSocket`; a version string alone is insufficient.

Installation validates the system runtime first without replacing it. If unsuitable, it uses an already valid private cached runtime at `<data>/node22/bin/node`, or downloads/checksum-verifies and stages a supported runtime before source activation. Staged execution must pass the same version/capability checks before replacing the private cache; failed staging preserves the prior runtime and refuses activation. The cache directory name does not authorize an earlier 22.x runtime. Required parser/dependency checks and source rollback still apply. These checks validate installer prerequisites, not native engine/client delivery or a completed runtime upgrade; inspect the activated source and intended native operation separately.

Detached files are `openbot-install-result.json`, `openbot-install.log`, `openbot-install.pid` and corresponding uninstall files. Result `status` is running/success/failed, with timestamps, progress/timings and optional source/commit/error/rollback/restart metadata. A successful installer controls fallback also retains `officialLogging: "unavailable-host-hook"`; status and Bot instructions must disclose optional Official logging unavailable rather than describe a fully verified install. Python-backed uninstall/status receipts survive removal of cached Node. Read the actual receipt after disconnect rather than repeating a mutation; capability disclosure and source success are not native chat validation.

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

Default CLI installs and UI saves retain checked logging-wrap requirements when logging is enabled. The source installer now explicitly supplies the separately guarded `--official-controls-fallback` exception below; it is not a universal bypass or a Custom repair. `--keep-service` continues to preserve a healthy listener and may therefore keep older service code loaded; deliberate source upgrades need the normal reload path or that narrow installer fallback, not service-only recovery.

The existing `computer-recovery.py` service fallback passes `--keep-service` with the selected host/data paths and deferred-bounce setting, preserving the Official selection rather than repeatedly failing optional logging instrumentation. Read the owned service PID and `/api/state` separately from logging compatibility and an intended-client stock send/reply. An available panel, retained logging preference or successful service-only receipt does not establish a loaded native tap, fresh captured traffic, repaired roster/message delivery, full mode/context reconciliation or billing. Mode checks describe observed authority, not an atomic transaction against all external writers.

### Installer fallback for unavailable Official logging

The source installer invokes CLI `install --official-controls-fallback` when reconciling its staged release and `tunnel on --official-controls-fallback --json` during its existing explicitly authorized public setup. The latter prevents a second strict logging reconciliation from undoing the controls-only outcome. CLI install and mutating tunnel paths capture exact saved mode bytes once for both desired state and `expectedMode`; the flag enables internal `officialControlsFallback: true`. It adds no UI save kind/result shape, does not disable saved logging or switch mode, and grants no public-access permission by itself. A direct reviewed install diagnostic equivalent is:

```bash
node --experimental-strip-types /home/box/sand-data/openbot/src/cli.ts install \
  --official-controls-fallback --json
```

For the separately authorized installer public setup, the corresponding existing tunnel action is `node --experimental-strip-types /home/box/sand-data/openbot/src/cli.ts tunnel on --official-controls-fallback --json`. Explicit `--official-controls-fallback` is recognized on mutating tunnel commands; default tunnel commands and UI saves still require their ordinary checked wrappers. Do not append it as a fallback after quickstart refuses saved exposure or treat it as authorization to change a private/public access choice. API/capability fields below are unchanged.

The exception applies only when the desired and explicitly saved mode are Official, saved request logging is enabled, the host is unmarked and requires no source peel/rewrite, and census cannot find a unique usable stock logging hook (`private-lane`, `gap` or `ambiguous-factory`). It does not accept an existing marked composition or change an unknown/default mode into Official to qualify. When those conditions are absent, ordinary checked reconcile behavior remains, including Custom wrapping, supported Official tap installation or existing refusal. Default CLI invocation without this flag and UI saves remain strict.

Unlike `--keep-service` recovery, this installer path reloads an existing owned control UI to load the staged release, or starts it if absent, using normal PID/listen checks. It rejects foreign listeners and rechecks the saved raw mode after asynchronous observation/startup before Official finalization. Native host bytes, mode, logging preferences, catalog, credentials and existing host backups stay untouched by this fallback; no wrapper is fabricated, host bounce scheduled or inference path replaced. Official's normal stale-Custom-guard cleanup/self-PID protection and requested exposure reconciliation remain distinct. Do not infer an atomic transaction against external writers or claim that another saved public access policy was implicitly authorized.

Successful CLI JSON includes these capability fields (the full result also has its normal snapshot):

```json
{
  "ok": true,
  "wrapBytesChanged": false,
  "hostBounce": "none",
  "officialLogging": "unavailable-host-hook"
}
```

`officialLogging` means controls were updated while optional stock request logging could not be installed on this host. The logging preference remains saved; this is not a claim that a tap is loaded or traffic captured. The detached install result preserves the same field. CLI text, `--bot-status` and `--bot-status --brief` must relay the capability warning; printed `OPENBOT_OFFICIAL_LOGGING=unavailable-host-hook` and `OPENBOT_CAPABILITY_WARNING` describe that limitation, and the Bot completion instruction carries it alongside success. A missing field or `not-reported` is not positive logging proof.

Report source/controls success with optional logging unavailable, leaving stock delivery policy unchanged. It does not establish a completed native inference/tool turn, client delivery, billing, history/SDK/routine merge or mode handoff. Inspect a compatible loaded tap and actual intended-client stream/reply separately if those outcomes are required. Do not change providers, credentials or logging preferences simply to hide the disclosure.

### Explicit Official roster selection

`recovery/hooks/official-dispatch-recovery.py` is a manual roster-selection helper for a separately authorized recovery. It requires literal Official already selected and an existing all-active current remint journal; it does not switch inference mode, mint an identity or run automatically through the supervisor or a save. Inspect its read-only live-authority plan first. Only an explicit authorized invocation without `--plan` applies profile/visibility/preferences changes:

```bash
python3 /home/box/sand-data/openbot-hooks/official-dispatch-recovery.py --plan
# Authorized roster mutation, preserving the already-selected Official mode:
python3 /home/box/sand-data/openbot-hooks/official-dispatch-recovery.py
```

The plan reads the current journal/platform roster and selected target profile without applying visibility/profile/settings changes. Both source and destination server rows must retain their recorded UUID/legacy UUID, saved numeric server ID and owner authority. Duplicate platform UUID rows, overlapping journal pairs, changed bindings or missing/unowned counterparts refuse. The chosen target must be actual Temporal with a matching local profile binding and exactly the journal's recorded kind; changed kind refuses with `Stock counterpart kind changed`. Without an applicable confirmed counterpart, the current destination is preferred only when its Temporal row has that kind; otherwise the original counterpart is considered and must pass the same final target-kind check. A destination converted to a plain AGENT cannot replace a typed ROOM by name, nor can a changed original type bypass validation. This selection is not proof that room delivery or histories are fully reconciled.

Later runs read the owned nonsymlink `openbot-official-dispatch-state.json` as selection authority only when `phase: "stock-roster-selected"` and `mode: "official"`. Its `identityMapping` must be an object with canonical identity keys and values. Legacy sparse `new -> old` receipts and full both-counterpart mappings are supported. A remembered target must be the current journal entry’s `oldId` or `newId`; if the receipt also maps that old counterpart, its target must agree with the remembered new-counterpart mapping. Inconsistent confirmed mappings, malformed mappings and symlink/unowned receipts refuse before settings writes. A different generation’s identity mapping, an incomplete/non-Official receipt or no confirmed receipt supplies no selection authority.

A remembered target is retained only while its current row is Temporal and has the recorded kind, then it must pass every existing ownership, legacy-identity, server-binding and local-profile check. A later upstream migration of the preserved BOX copy therefore does not silently switch the confirmed Official conversation. If the remembered row is absent or no longer Temporal/of the recorded kind, the existing stock fallback selection applies with all guards intact; changed ownership/bindings still refuse. Without an applicable confirmed receipt, the existing first-handoff selection remains. This changes no mode or default Custom/Official dispatch protocol, merges no histories/SDK roots, authorizes no pruning and proves no native-app delivery.

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

Success returns `{ok,wrapBytesChanged,snapshot,providers,models,keyedProviders,activeModelId,logSettings}`, with `createdProviderId` for additive onboarding. Saves use reconcile and serialized state authority. Refusals include `host-missing`, foreign listener/wrap, `census-refused`, `syntax-check-failed`, `listen-failed`, `mode-changed`, `source-changed` and `local-engine-refused` with a reason. Refusal is not permission to adopt, force ownership or reset configuration. An invalid selected engine can refuse before any mode or plan write; preserve its policy and artifacts for review instead of deleting the pin or hand-writing Custom.

Other APIs include GET `/api/state` or `/api/snapshot`, GET `/api/bots`, logs/settings, pause controls, provider discovery and skills/OAuth APIs documented here. Hop `/v1/chat/completions` is inference, not configuration. A successful HTTP response is not native end-to-end delivery.

## Pauses

Use GET/PUT `/api/pause`, not direct file edits. Stored `openbot-pause.json` is `{paused:boolean,at:ISO-time-or-null,note:string-or-null}`; note is trimmed to 500 characters. PUT takes `{paused:boolean,note?:string}`. Missing/corrupt ordinary gateway state is fail-open. Paused hop returns 503 `paused`; it remains alive for health patrol. Pure stock Official without a tap is outside that chain.

`openbot-pause-bots.json` is `{"pausedBotIds":["<bot-id>"]}`. GET/PUT `/api/pause-bots` uses `{botId,paused}` or full `{pausedBotIds}` replacement. Missing/corrupt ordinary state is empty. Native/hop checks apply on the next turn; no bounce. Hidden/deleted inventory entries stay distinct for historical log resolution, without changing native visibility or deleting data.

### Transcript ownership versus quoted history

`fleet-state-merge.py.remap_transcript_owner` normalizes only the top-level transcript record's string `agentId` through the journal identity mapping. Message prose, `richText`, nested agent identifiers, tool arguments/results and quoted IDs remain historical payloads, not configuration references. Distinct quoted IDs remain distinct payload variants even when owners normalize to one identity; exact normalized duplicates may collapse, while differing same-entry-ID variants retain deterministic aliases and provenance. Never use global string replacement to make history appear equal. Newly generated synthetic branch aliases retain their full deterministic digest within the native 128-byte entry-identifier bound, measured as UTF-8 bytes. The original-ID-plus-digest form is retained only when it fits and is unoccupied; otherwise `compact_branch_identifier` hashes the full legacy alias into a bounded `openbot-branch-<full-sha256>` identity, retrying deterministic counter seeds on collisions. It never truncates a digest or drops a payload variant. This prevents new oversized aliases; existing published identifiers are not automatically rewritten.

For future authorized copies, `migrate-local-bots.py.copy_agent` excludes `transcript_entries` from global SQL TEXT replacement and uses the owner-only union. Agent-local and external `.jsonl` journals use `remap_transcript_jsonl`, which changes only a mapped top-level owner and retains unclassified/partial lines. Non-transcript configuration identity remapping is unchanged. These safeguards prevent future quoted-content rewriting; they do not repair already changed histories, prove existing copies equivalent, splice SDK roots or establish a completed live history merge. Retain originals and reviewed branch evidence before any separately authorized repair.

### Native context blob coverage

A matching `latestRootBlobId` in metadata may identify a reusable fixed slot across copied databases, not equal SDK context. Inspect the actual root payload in each branch's `conversation-blobs.db` and its reachable blob closure, including referenced prompt/state blobs, turn identities and execution-step coverage. Equal metadata IDs, direct-reference lists or visible user-turn IDs alone cannot establish complete context coverage; prompt references and steps can still diverge. Snapshot/hash inspection is evidence gathering, not a generic live-root repair. Even when every reachable reference resolves in each branch, complete closures may contain divergent prompts/state/steps; zero unresolved references proves closure completeness for that snapshot, not context equivalence or a merged live root. Validate through read-only coherent branch snapshots and retain both divergent roots.

Keep divergent roots and their coherent branch databases/session journals separately. Do not automatically graft/splice opaque roots, overwrite one branch, advance cursors or replay pending tool/routine completions. Actual compatible closure and turn/state coverage must be verified before a separately reviewed live reconciliation; an archival transcript union, root-slot match or private snapshot never authorizes pruning, mode transition or a delivery-success claim.

### Supplemental peer payload proof

For explicitly mapped separately recovered peers, `Reminter.refresh_peer_roster` requires matching role/binding authority, retained source entry IDs and complete source payload coverage in destination local history. Transcript comparison uses `fleet-state-merge.remap_transcript_owner`: only the top-level record owner changes through lineage mapping. Quoted UUIDs, nested tool data, prose and rich text remain literal evidence; rewriting those fields cannot manufacture source coverage. This is the same owner-only comparison used by Custom counterpart reselection; configuration/role normalization remains separate.

The destination's entire current local transcript must equal the corresponding ordered published platform prefix as decoded payloads, not just entry IDs. Pagination reuses the returned generation on later requests and refuses a changed generation, a repeated sequence number with different payload, or nonadvancing pagination. Additional destination entries and a changed original source order remain separately governed/reported; ID sets alone cannot establish publication/body equality. A failed preflight leaves the supplemental peer-roster write unapplied and requires explicit reconciliation rather than dropping a variant.

Each successful supplemental `existingCopyProofs` receipt now includes `sourcePayloadsRetained: true` and `destinationPayloadsPublished: true`, alongside `sourceIdsRetained`, `destinationPublicationVerified` and the separately reported `sourceOrderPreserved`. These booleans describe the checked payload evidence for that operation, not SDK/context merging, native/client send acceptance, billing, mode selection or permission to delete branches. Historical ID-only receipts do not acquire the stronger proof retroactively. Source/tests/dry runs do not establish deployment of a changed native engine or installed templates.

## Routine policy

### Native local routine clock

Optional `openbot-local-routines.json`:

```json
{"enabled":true,"maxConcurrent":2,"graceMs":120000,"queueTtlMs":3600000}
```

`enabled` must be true; cap is integer 1–12, default 2. Grace is integer 0–300000, default 120000; queue TTL satisfies grace <= TTL <= 3600000, default 3600000. Invalid policy holds dispatch. Local-only opt-in and literal recognized mode are prerequisites, not permission to duplicate stock ownership.

Native BOX ownership, not on a session box, readiness, per-routine enablement, transfer/retirement holds and shared native-running/pending/durable claims all gate execution. Temporal-owned work is excluded. The native automation parser/runner owns execution; no independent cron, platform user-turn substitute or manual-endpoint imitation. One consumed slot must never be replayed after uncertain delivery.

Global/bot pauses hold the clock, and malformed/unreadable pause authority conservatively holds it even though ordinary hop pause is fail-open. Keep `openbot-routine-holds.json.heldAgentIds`, native enablement and receipts intact. Both-mode fixtures or policy eligibility do not prove Official delivery. Loading changed scheduler code requires a controlled idle-owned host reload; a cap edit alone is not proof of the loaded module or actual concurrency.

### Selected routine authority for a paused candidate

The [return-routine-authority.py](../../recovery/hooks/return-routine-authority.py) consumer and [return-routine-codec.mjs](../../recovery/hooks/return-routine-codec.mjs) bridge let an existing current-journal hidden registered BOX candidate consume separately reviewed selected-owner routine evidence. The ordinary Start/Resume coordinator and existing single-writer/migration authority remain in control; API/web actions are unchanged. This is a routine-authority consumer, not an automatic fresh-generation builder, general scheduler merger, cloud-idle test or native-delivery claim.

A journal entry may pin `returnRoutineAuthority: {path,sha256}` to immutable private schema `openbot-return-routine-authority/v1` before pausing source schedules. Let `L` mean the recorded old Local source, `N` the existing candidate and `O` the actual selected owner; these are symbols, not installation IDs. The consumer validates exact current journal/owned UUID and numeric server bindings, source captures, raw hashes, definitions and native proof rather than accepting a boolean intent flag. Retain all original references and evidence; do not hand-edit the pin, desired states or captures to pass restoration.

| Field | Consumer authority |
|---|---|
| `sourceId`, `newId` | Exact recorded Local `L` and candidate `N` identities in the current journal. |
| `ownerId`, `ownerServerId`, `selection` | Actual selected `O`, its exact numeric server binding and captured selection authority; names or a stale Local profile do not choose the owner. |
| `localDefinitions` | Original Local `L` routine definitions retained as evidence. |
| `cloudRecords`, `capturedCloud` | Full original selected-owner cloud records and the pinned raw capture reference; preserve original authored and historical variants. |
| `matched`, `states` | Checked routine correspondence and exact desired selected-owner flags captured before pause. False stays false; true is not inferred by OR-ing branch flags. |
| `nativeProof`, `nativePlan` | Immutable pinned references to the reviewed native codec's materialization plan and executable proof. |
| Materialized configuration/run paths, `timeZoneSettings` | Checked native candidate files, complete retained run history and pinned time-zone settings reference; not arbitrary output paths or inferred scheduling defaults. |
| `quiescenceEvidence` | When supplied/required, exact captured pause evidence under the strict contract below; never a free-form quiescent flag. |

The exact-source-pinned native codec preserves authored prompt, schedule, `triggerPresentation`, cron/window and time-zone semantics, while all materialized candidate routines start paused. Complete raw historical run variants, including annotations, audit metadata and stale `running` records, remain pinned, readable immutable archives as inert evidence. The active native `FileAutomationStore` may lawfully retain its 20-row recent window after its first runtime write; this is not a promise of uncapped active-store or UI history. Each private proof row binds `nativeVisibleProjection`, exact `nativeVisibleProjectionJson` and its SHA256 plus projection count and limit 20. The consumer reconstructs that lawful window, refuses empty or arbitrarily truncated old subsets as substitutes, and strictly validates new observations; full archived variants remain authority and cannot be discarded or replayed. Historical running status proves neither live work nor idle, and actual pending source notices, pending runs or queued work still refuse consumption/materialization. Do not normalize away archive variants or reset projection authority to pass these checks.

Optional `quiescenceEvidence` must carry its required pinned `capture`, `pausedReadback` and `quiesceJournal` references when used. If `L` differs from selected `O` and any Local switch was true before pause, also require both `localCapture` and `localPausedReadback`; one without the other cannot prove the separate Local source paused. Validation checks owned UUID/server-ID authority, exact original source capture/raw digests and definitions, and literal current all-false paused states. Never manufacture evidence from arbitrary false flags, stale snapshots or a copied acknowledgement. Keep the selected original desired states distinct from temporary paused readback.

Only after this new authority validates does its selected intent supersede legacy `serverRoutineAuthority`; legacy references remain retained. Restoration may tolerate a missing old `L` path only for the new materialized native-proof recipe with all its captured authority intact. Existing legacy restoration guards still apply otherwise. Do not broadly suppress missing-source checks or let stale Local all-false state overwrite the selected owner's captured switches. The consumer does not infer desired enablement from a union, a historical run record or the candidate's deliberately paused starting state.

Before any scheduler pause during activation, the canonical path pins the exact restoration states and durable pause intent. Interrupted activation/restoration follows those receipts and preserves later recorded progress rather than recapturing false flags after the source has been paused or blindly re-enabling everything. Current selected true/false choices stay authoritative under the existing ownership/readback guards; native scheduler execution remains native. These proofs concern routine configuration/history retention and pause/restore intent only. They establish no fresh identity handoff, cloud-idle state, full history/SDK merge, actual routine execution, intended-client delivery, billing or permission to prune original evidence.

### Native routine writer preservation

The pure [native-routine-trigger-preservation.py](../../recovery/hooks/native-routine-trigger-preservation.py) transform accepts only its reviewed complete source digests and exact native serializer contract. It retains the full serialized authored cron trigger, including timing/windows and `triggerPresentation`, alongside the native legacy `schedule`. Updating `isEnabled` or `lastRunAt` must not erase that trigger. The complete reviewed patched source is a checked fixed point; unknown versions, changed boundaries, partial markers or unexpected output hashes refuse before producing a candidate. Do not supply an arbitrary digest to bless an unreviewed source.

Stage the candidate through canonical local-engine preparation, syntax/compatibility checks and wrapping, then use the existing owned idle activation path. The pure transform itself reads no agent files, writes no host, reloads no process and changes no mode, provider, routine enablement or dispatch. Do not introduce an after-toggle file-repair loop or patch the live host outside that pipeline. A detached source/native fixture proves only its checked preservation contract; report loaded-runtime activation and actual routine execution or delivered replies separately.

Native active `runs.json` is bounded to 20 typed visible history records and is rewritten by ordinary native begin/finish operations. It is not a lossless archive for every imported raw run. Preserve complete raw source and prior run variants, audit annotations and orphaned historical running rows in immutable hashed private archives before normalization; retain their provenance rather than injecting unsupported fields into the active native schema. Normal native history truncation does not authorize deleting those archives.

Historical run state, including a past row marked running, does not prove current work is queued or a bot is busy/idle. Check current pending completion inbox, scheduler receipts/claims and native live turn/working status through their separate authorities before transfer or activation. Never replay old actions, acknowledge a pending completion or clear a live hold merely to make history fit the bounded native view. These preservation rules do not enable paused routines or establish delivery, billing, full transfer or prune safety.

## Panel Local transfer job

The dashboard's Local transfer card reads `GET /api/local-transfer` and offers explicit start/resume through `POST /api/local-transfer`. This is a separate endpoint, not a new `/api/save` kind. The only accepted POST bodies are exactly `{"action":"start"}` and `{"action":"resume"}`. Additional fields, arbitrary commands, environment values and paths are not accepted. An action requires current-user-owned literal Custom mode and the owned `openbot-local-only.enable` flag in the selected data root. The action never flips mode or creates Local opt-in; preserve both choices and all existing identity/history authority.

The API publishes a bounded whitelist, excluding agent IDs, paths, credentials, transcripts, free-form exceptions and private error bodies:

| Field | Contract |
|---|---|
| `enabled` | Current owned mode is exactly trimmed `custom` and the Local opt-in flag is present; not proof of BOX ownership or delivery. |
| `available` | A checked owned transfer helper is available; not proof that work is running. |
| `lockHeld` | Singleton `flock` is contended; this alone establishes neither a transfer worker nor successful progress. |
| `workerRunning` | Lock is held and recorded `workerPid` resolves to an owned `/proc` process with the exact transfer script/root command line. Maintenance keepers and unknown holders report false. |
| `phase` | One of `idle`, `disabled`, `starting`, `checking-bindings`, `copying`, `registering`, `preserving-late-history`, `waiting-for-publication`, `waiting-for-rate-limit`, `waiting-for-control-plane`, `activating`, `bound-box`, `review-needed`, `failed`, `cancelled-mode-change`. |
| `jobId` | Optional stable opaque job UUID retained across retries/resume, not a bot identity or migration generation. The sole fresh-ID exception is an explicit requested retry whose prior bounded-tail marker is exactly the never-applied preparation-failure shape documented below. |
| `counts` | Fresh counts from the owned `openbot-box-remint.json` for `planned`, `copied`, `registered-box`, `active`; current phase counts, not cached progress or cumulative completed totals. |
| `agentCount`, `verifiedAgents`, `updatedAtMs` | Optional nonnegative progress counts/update time. `verifiedAgents` is zero unless the current exact completion proof matches; not native-client delivery evidence. |
| `publicationVerified` | True only when all current entries are active, saved `publicationProof` matches the exact canonical journal SHA256 and verification-artifact SHA256, and verification results match every current source/destination pair with `verified-box` statuses. No private identifiers or artifact contents are exposed. |
| `rosterComplete` | Current zero-untracked scope receipt matches the exact canonical journal SHA256, with `rosterCheckedAtMs` less than 120 seconds old and not in the future. Publication proof alone does not satisfy it. |
| `untrackedSourceAgents`, `untrackedVisibleAgents`, `unknownSourceAgents`, `hiddenArchiveAgents` | Optional nonnegative aggregate counts: sources outside covered lineage, their proven-visible subset, uncertain routing/ownership/visibility or invalid-binding sources, and proven hidden archives excluded from scope. Unknown-source review is not limited to Temporal rows. No bot IDs or private bodies are exposed. |
| `rosterCheckedAtMs` | Optional Unix-millisecond time of the last whole-owner-roster scope check; freshness is required for completion. |
| `refusalPhase`, `diagnosticCode` | Optional bounded phase plus safe category: `history-conflict`, `identity-conflict` or `preflight-refused`. Preserve evidence and inspect privately rather than exposing raw errors. |
| `diagnosticAssistant`, `diagnosticReason`, `diagnosticAdviceCode` | Adapter receipt status `running`, `available`, `failed` or `disabled`; `unavailable` only when no adapter is installed. Reason is product-owned fixed text. Optional advice code is allowlisted and shown only for available advice; no returned prose/execution authority is exposed. |
| `nativeAppDeliveryVerified`, `billingVerified` | Always false in this public transfer status. |

The service selects its reviewed source helper or installed `openbot-hooks/custom-dispatch-recovery.py`; callers cannot supply an executable. The helper also provides the following commands from the authenticated Computer:

```bash
python3 /home/box/sand-data/openbot-hooks/custom-dispatch-recovery.py \
  --root /home/box/sand-data --status
# Explicitly authorized start or retry of the same guarded transfer:
python3 /home/box/sand-data/openbot-hooks/custom-dispatch-recovery.py \
  --root /home/box/sand-data --request
```

`--status` inspects progress, fresh journal counts and process/lock ownership without starting copy/network work. `--request` requests a detached worker for either start or resume; use the existing job's receipt after a disconnect instead of assuming a second job is needed. The worker inherits the held single-writer lock across spawn, retaining the same persisted `jobId` across attempts. Concurrent requests observe the held lock instead of creating parallel writers. A maintenance keeper or unknown holder yields `lockHeld: true`, `workerRunning: false`; the UI displays transfer locked without claiming running/success. Preserve the lock and inspect its owner privately rather than killing an unknown process or deleting lock authority to retry.

Every snapshot derives counts from the current owned remint journal. A saved `bound-box` phase survives only with both `publicationVerified: true` and a current `rosterComplete: true` scope receipt. Publication proof requires all current entries active, saved `publicationProof` matching the exact canonical journal SHA256 and verification-artifact SHA256, and artifact results matching every current `oldId`/`newId` pair with `verified-box` statuses. These identifiers and artifacts remain private. Without that exact publication proof, public `verifiedAgents` is zero; stale completion derives `copying` when entries are planned, `registering` when entries are copied, or `waiting-for-publication` when entries are registered or all active without matching proof. Even when publication proof passes, missing/expired roster scope changes saved `bound-box` to `checking-bindings`; copied-cohort publication does not certify whole-roster coverage. Cached phase, matching counts, an earlier generation or a held lock cannot manufacture either proof. Public native-app delivery and billing flags remain false even when both checks pass.

The private `openbot-custom-dispatch-state.json` tracks job progress; `openbot-box-remint.json` remains migration/copy/activation authority. Private `openbot-custom-dispatch-error.json` and `openbot-custom-dispatch.log` retain diagnostic details on the Computer. Never copy their contents into public status or expose raw subprocess errors. Do not edit journal phases, zero conflict counters, delete an error to manufacture recovery, remint another generation or bypass ownership/history/publication guards to make progress appear complete.

Transient control-plane failures can retain bounded retry/backoff. Nontransient failures enter `review-needed`, stopping automatic supervisor retries. Explicit resume requests another attempt under all current guards; it does not resolve conflicts, accept a history-merge receipt, override copying rules or alter the retained source evidence. If mode/Local authority is withdrawn, the workflow stops; a start/resume button is not permission to restore that authority silently. Complete separately authorized reconciliation when required, then resume the same job. The narrowly defined bounded cloud-tail path can prepare and accept its own checked incremental receipt before activation; this is not a general conflict override or permission for arbitrary automatic SDK/history synthesis.

When the optional tool-free adapter is installed, the coordinator may request its bounded diagnostic for a persisted review-needed refusal. `diagnosticAssistant` then follows the actual private receipt: `running`, `available`, `failed` or `disabled`. Only a missing adapter reports `unavailable`; disabled/unsupported routes and failed requests must not be presented as a completed review. Available advice is a product-owned static string selected by an allowlisted `diagnosticAdviceCode`, never model-returned prose or an executable command. See [the advisor contract](#optional-tool-free-transfer-advisor) for privacy, selected-route checks, persisted call limits and opt-out. Do not fabricate agent work or launch unbounded inference/spend. A transferred/registered/active roster or `bound-box` is not a native send/tool-return/delivered-reply, billing, live SDK/context merge or prune-safety proof. Keep originals, private backups, routines and session branches under their existing contracts and verify requested native-client outcomes independently.

### Whole owner-roster transfer scope

The coordinator inspects the complete current owner roster at job start, before activation and again before completion, including Temporal rows and uncertain routing/ownership. Scope excludes all proven current and historical aliases in the journal's full lineage, rather than only the current source/destination pair. Other users' explicitly unowned rows do not authorize copying. Actual owned canonical bound BOX rows are skipped as already local and never reminted. A proven hidden archive is excluded only when owned local profile/settings evidence proves `hiddenFromSidebar: true`, actual ownership is confirmed, and profile UUID/legacy UUID/numeric server binding matches that row exactly. A name, locally forced harness label or missing local record cannot establish hidden-archive authority; unknown routing/harness or a BOX row with uncertain ownership or invalid binding requires conservative review.

Remaining sources are counted in `untrackedSourceAgents`; known owned visible Temporal rows also count in `untrackedVisibleAgents`. Cloud-only or uncertain routing, ownership or visibility, including unknown harnesses and BOX rows without confirmed ownership/canonical binding, count in `unknownSourceAgents` and require review. These unknown counts are not limited to Temporal rows. Proven excluded archives count separately in `hiddenArchiveAgents`. Untracked or uncertain scope stops deterministic transfer at `review-needed` with safe `diagnosticCode: "history-conflict"`, retaining current entries and both source branches. Only fixed aggregate counts reach the API/dashboard; do not expose IDs, roster bodies, profiles or raw errors. These counts identify required review, not lost data or a completed import.

The coordinator never resets the current generation or automatically appends an outside source to make coverage appear complete. Initial planning may cover discovered local source candidates, but unknown/cloud-only outsiders still block that initial scope. There is no implemented full cloud-history/SDK-context import capability behind start/resume: do not silently substitute a local-only copy, ignore an unknown source, hide it, rewrite profile visibility, prune history or claim all cloud/context state was imported. Preserve the existing journal, publication evidence, histories and private sessions. Complete separately reviewed source discovery/import and authority reconciliation before any separately authorized addition; explicit resume reruns current scope and all existing copy/publication/activation guards without supplying that missing capability.

A successful scope check privately records zero untracked/visible/unknown counts, `rosterCheckedAtMs` and `rosterScopeJournalSha256` for the exact canonical current journal. Public `rosterComplete` is true only while that receipt still matches the journal and its age satisfies `0 <= age < 120000` milliseconds. Proven hidden archives may remain counted without invalidating scope. Status polling does not refresh an old receipt merely because the journaled copies passed publication; expired, missing, future-dated or changed-journal authority cannot retain completion. Saved `bound-box` with valid publication but no current scope reports `checking-bindings`; the card distinguishes the published cohort from an unchecked whole roster. A fresh live roster check through guarded resume may renew scope, not a manual timestamp/hash edit. Even both proofs establish only their checked scope: native-client admission, tool/reply delivery, billing, full live SDK/context import and pruning remain separate and unverified.

## Reuse existing BOX counterparts in Custom

The existing opted-in Custom dispatch coordinator can reuse a completed current journal's actual verified BOX destinations after an authorized return to Custom. It compares owner-visible server authority first. When that current authority is `bound-box`, already-paired original IDs are excluded from fresh source detection and the coordinator calls internal `Reminter.reselect_active()`. This prevents a visible original left by Official roster selection from creating another copy generation merely to restore the same target. There is no new CLI subcommand or live-merge override. Official remains owner authority: selecting Official does not authorize a Custom re-selection or automatic mode flip.

A nonempty all-active current journal is required. If visibility, preferences and recorded finalization already select the destinations, the method can return `state: "already-selected"` without mutations; that fast return is not a fresh publication/delivery test. Before an actual re-selection, every destination must pass current actual owned BOX binding, exact journal target server ID, native roster, SQLite/profile identity and full ordered local-history publication verification. A forced local profile label, healthy hop, a saved Custom token or an all-Temporal server roster cannot satisfy that authority. This does not migrate a Temporal identity or prove that the current platform will admit a new BOX turn.

Every original source must still be owned, retain exact journal source UUID/legacy UUID/server binding and kind, and match its saved local profile server ID. Source/destination profile roles are compared after lineage normalization, excluding only routing harness/server-ID fields. Before the first scheduler or visibility mutation, a fresh full platform transcript export and current local source history must each be covered by the destination's local history as complete normalized payloads. Matching entry IDs alone do not qualify. Changed instructions/roles, uncopied records or same-ID divergent payloads refuse with an explicit reconciliation requirement, preserving both branches instead of hiding, overwriting or automatically reminting through the conflict. All pairs must pass this preflight before proceeding; the helper lock and preflight are not a global transaction against external writers. These transcript comparisons now call `fleet-state-merge.remap_transcript_owner`, normalizing only top-level `agentId`; quoted UUIDs/rich text and nested tool data remain unchanged and cannot be rewritten to make coverage appear complete. Source role/config normalization stays separate.

The method privately retains platform preferences and source evidence under `recovery-snapshots/box-reselection-<timestamp>-<nonce>/`. It then confirms old cloud schedules paused, backs up/disables enabled local source schedules and reveals the existing target while hiding the original. Custom/local-only authority is rechecked before each visibility operation and before final preference work. Target database/files, model and per-model effort choices and current target routine switches are not copied or reset from the old source; recovered events and pending completions are not replayed. Finalization restores section/pin/main references from current preferences using recorded aliases while preserving labels, native collapse/notification choices and unrelated entries. Keep source data, private snapshots and original context/session roots.

A mutation result reports `state: "reselected"`, count, private backup path, `liveHistoryMerged: false`, `sdkContextsMerged: false` and `pruneAllowed: false`. Managed `openbot-custom-dispatch-state.json` keeps `nativeAppDeliveryVerified: false`, `billingVerified: false`, `liveHistoryMerged: false` and `sdkContextsMerged: false`; mode or selection success does not upgrade these into delivery/merge proof. The coordinator's existing captured routine-restoration progress and validated peer refresh remain distinct from actual execution. Inspect current authority and intended-client send/local attach/reply separately, and verify billing independently where claimed.

This is bounded identity/preference reuse when the destination already contains the current required history/role payloads. It is not an SDK/context union, routine-definition merge, Temporal-to-BOX server migration, new native admission guarantee, deletion/prune workflow or proof that every mobile/tool/session path works. If all actual destinations are Temporal or source branches differ, stop at the reported guard and complete separate authorized reconciliation rather than labelling the fleet local or clearing held evidence. A fresh-copy workflow remains a different explicit recovery with its own proof requirements.

### Manifest-only BOX publication authority refresh

The reviewed `custom-dispatch-recovery.py --refresh-publication-bindings` command refreshes only `openbot-publication-bindings.json` from a fresh authenticated owner roster, without running the handoff coordinator. Use the installed reviewed helper on the authenticated Computer with literal Custom and its owned local-only opt-in already selected:

```bash
python3 /home/box/sand-data/openbot-hooks/custom-dispatch-recovery.py \
  --refresh-publication-bindings
```

It uses the coordinator's existing dispatch lock/ownership contract and refuses a held/unsafe lock. No remint journal is required or created. Rows must be a well-formed list; only actual `harness: "box"` rows with `viewerIsOwner: true` qualify. Each included UUID/legacy UUID and numeric server binding must pass the real BOX-row validator, and duplicate owned BOX authority refuses the entire refresh. Do not infer authority from locally forced profile labels, names, a previous generation or hop health. Recheck Custom/local-only authority before the atomic manifest commit; a mode change, unsafe manifest path or invalid roster leaves the manifest unrefreshed.

The private manifest retains schema `{schemaVersion:1,checkedAtMs,boxAgents:{UUID:numericServerIdString}}`; preserve its privacy and do not edit it to bless a Temporal identity. CLI JSON reports `phase: "publication-bindings-refreshed"`, `boxAgentCount`, `checkedAtMs` and `nativeAppDeliveryVerified: false`. The aggregate receipt is not a publication or delivery receipt. This operation does not mint/copy/activate identities, edit profiles/routines/history/mode, send a canary or prove publisher execution, client delivery or billing. Native `exportGrokBotWorkingState` canonical backfill remains a separate operation with its own authority/evidence; refreshing this manifest neither performs nor certifies it.

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

### Durable publication reload

An authorized `remint-box-fleet.py publish` rechecks the registered copies' owned BOX bindings and requests the native startup publication sweep through [publication-reload.py](../../recovery/hooks/publication-reload.py). It no longer sets `computer-recovery-state.json.pendingHostReload` for publication. That state file remains recovery telemetry and may still contain pending reloads for other repairs; do not edit it to create, acknowledge or repair a publication request.

The separate durable authority is `<data>/openbot-publication-reload.json` with sibling `openbot-publication-reload.json.lock`. Both must be current-user-owned regular nonsymlink files with no group/other permissions; the marker is bounded to 64 KiB. The writer and consumer hold the same exclusive lock. Marker writes use a private temporary file, file fsync, atomic replacement and directory fsync so telemetry rewrites cannot lose the request. Keep these files and the remint journal private; never copy another installation's request.

| Field | Contract |
|---|---|
| `schema` | Exactly `1`. |
| `requestId` | Fresh canonical UUID for this request, independent of bot/conversation IDs. |
| `mode` | Exactly `custom`. |
| `phase` | Persisted `pending`, `signal-issued` or `acknowledged`. Waiting observations do not replace this phase. |
| `target` | Native process identity `{pid,startTicks}`; PID is a positive integer and `startTicks` is the `/proc/<pid>/stat` start identity string. PID alone cannot authorize a reload. |
| `hostSha256` | SHA256 of the exact selected owned native host source when requested. |
| `requestedAtMs` | Request creation time in Unix milliseconds. |
| `signalIssuedAtMs` | Latest durably recorded signal-intent time in Unix milliseconds; also starts the ten-second retry grace. It does not prove SIGTERM reached the host. |
| `signalAttempts` | Count of durably recorded signal intents, incremented before the initial attempt and each retry; not a successful-signal count. |
| `acknowledgedAtMs`, `replacement` | Added on acknowledgement with the replacement native `{pid,startTicks}` identity. Acknowledgement does not prove publication or client delivery. |

Request creation requires owned local-only opt-in, literal trimmed Custom and one native host. It captures the host source and process start identity, then rechecks mode and process authority before writing. The durable marker is published before `publish` saves `publicationReloadRequestedAtMs` and `publicationReloadRequestId` in `openbot-box-remint.json` and returns its request receipt. Compare the same request ID across marker, journal, publish result and watcher observation. If interrupted between marker publication and journal/result completion, inspect the retained marker before repeating the operation; a successful journal save is not required for the marker to survive.

The Custom computer watcher consumes the request under its lock. It requires owned local-only opt-in and Custom mode, the exact requested host hash and source mtime quiet for at least two seconds. Missing, multiple or unidentifiable native hosts remain waiting. For the recorded native PID/start identity, the authenticated gateway must report literal `isBusy: false`; busy or unknown is not idle. After that potentially slow call it rechecks mode/local-only authority, source hash and quiet age, and exact native process identity before signalling.

Before the initial SIGTERM and each retry, the watcher durably writes `phase: "signal-issued"`, updates `signalIssuedAtMs` and increments `signalAttempts` under the same `requestId`. While the same PID/start identity remains, ticks within ten seconds of the latest intent report `waiting-native-replacement` without signalling. After that grace, the watcher repeats the exact expected source hash, two-second source quiet age, Custom/local-only, unique process and gateway-idle checks, including every post-gateway authority recheck, before retrying SIGTERM against that same target. Busy or unknown gateway state waits for idle. There is no SIGKILL escalation. A failed signal call returns that request to `pending` and surfaces the failure; a persisted intent alone is not a successful signal receipt.

Watcher `publicationReload` observations can report `absent`, a persisted phase, or `waiting-custom`, `waiting-request-refresh`, `waiting-stable-source`, `waiting-native-host`, `waiting-native-replacement`, `waiting-idle` or `waiting-authority`. In Official, the computer watcher does not consume or clear this Custom request. A changed host hash leaves it waiting for a request refresh; finish the checked source change and use an explicitly authorized `publish` to capture fresh source/process authority. The legacy `pendingHostReload` branch runs only when the durable observation is `absent` or `acknowledged`; an active durable request, including one waiting for source refresh, prevents that telemetry branch from bypassing these guards. Do not hand-edit the hash, target, mode, phase or request ID, remove the lock, or poll by repeatedly invoking mutating `publish`.

The durable intent closes the telemetry-loss problem but still permits a crash between writing `signal-issued` and calling SIGTERM. Automatic recovery uses the same ten-second grace and fresh authority checks before retrying against the unchanged target identity. Retries are limited by this interval and ownership/idle/source conditions, not a fixed attempt-count ceiling. Once a unique owned native identity differs from `target`, the watcher rechecks Custom/native authority and acknowledges that request without signalling the replacement; an already replaced process can also acknowledge on the first consumer tick. A same PID with different `startTicks` is a replacement, so recycled PIDs do not authorize another bounce. No acknowledgement is written while the same target remains. Preserve the marker through Official selection, watcher restart and telemetry updates rather than resetting it or creating repeated requests to manufacture progress.

`publish` reports `supervisedReloadRequested: true`, `publicationReloadRequestId`, `publicationVerified: false` and `nativeAppDeliveryVerified: false`. An `acknowledged` reload means a replacement native process was observed under the checked authority. It does not prove that the startup sweep completed, that copied transcript payloads reached the platform, that the UI displays them, or that chat sends/replies work. Run the separate full ordered paginated publication `verify` before activation, preserve incomplete/divergent history and test actual intended-client delivery separately. No acknowledgement authorizes activation, pruning, inferred billing or a live SDK/context merge.

### Native transcript publication observer and retries

The checked [local-publication-inject.py](../../recovery/hooks/local-publication-inject.py) composition adds [transcript-publication-observer.cjs](../../recovery/hooks/transcript-publication-observer.cjs) beside the actual native `TranscriptEntryPublisher`. It observes the native report callback without replacing the original telemetry, captures safe failure metadata in the actual failed catch and records actual native commit attempts. Exact complete injection anchors and the existing authoritative-roster policy must match; unknown or incomplete compositions refuse. Loading this changed source uses the existing checked owned idle reload. A later individual publication failure uses the per-agent publisher retry path and does not request a host bounce.

Each retry calls that same publisher's `flushAgent` with the canonical agent UUID. Native lanes, database reads, generations, commit implementation and ordinary scheduling remain authoritative. The observer does not manufacture transcript records, alter payloads or sequence/generation authority, insert user turns, replay inference/tool/routine actions, or change credentials, mode or identity. Its eligibility is rechecked before every retry: local-only opt-in and literal Custom must still hold, and the authoritative owned manifest must still bind that agent to actual BOX ownership. When an observer flush waits behind another native operation, `drainAgent` rechecks that authority when its queued work reaches the native lane, preventing a retry after a mode or ownership change during the wait. Locally forced profile labels do not supply publication authority. Official mode, a stopping observer or lost/invalid ownership policy prevents another retry.

Retries use nominal delays of 2, 4, 8, 16, 30 and 30 seconds, each with up to twenty percent jitter and an absolute thirty-second cap. At most six retry requests are allowed per agent per native process; this is a retry-request budget, not a deadline or an upper bound on ordinary native commits. At most 256 agent states are tracked; additional agents retain ordinary native behavior without observer retries, and tracked states are not evicted to reset a budget. A successful ordinary native report cancels an obsolete pending retry. A native `TranscriptLedgerCorruptError` exhausts observer retries immediately; preserve the ledger and use separately reviewed recovery rather than rewriting it, resetting authority or restarting the host to manufacture another retry budget.

Private status lives at `<data>/openbot-transcript-publication-status.json`, under the actual native sand-data root. The directory must be owned and real; an existing target must be an owned regular nonsymlink file with no group/other permissions. Writes use an exclusively created 0600 temporary file and atomic rename. Unsafe targets or failed writes produce only a fixed warning; status is best-effort telemetry, not durable retry authority or permission to repair paths by broadening ownership. Retry budgets and timers belong to the live process, not this JSON; do not edit the file to control work.

| Field | Telemetry contract |
|---|---|
| `schemaVersion`, `observedAtMs`, `maxRetries` | `1`, Unix-millisecond observation time, and `6`. |
| `publicationVerified`, `nativeAppDeliveryVerified` | Always false. |
| `agents` | Object keyed by canonical agent UUID; private installation identity, never a public fleet listing. |
| Agent `phase` | `observed`, `retry-wait`, `retry-queued`, `native-drained`, `native-commit-ok`, `exhausted` or `policy-paused`. |
| `retryRequests`, `failures`, `inFlight` | Observer retry requests, observed failures, and whether an observer `flushAgent` call is active. They do not count model calls or prove successful publication. |
| `nativeCommitAttempts` | Optional count of actual native commit callback invocations observed. A retry can drain without another commit. |
| `lastAttemptedSeq`, `generation` | Optional safe nonnegative native sequence maximum and native generation from an actual commit attempt; not an independently verified remote cursor. |
| `lastFailureAtMs`, `lastRetryAtMs`, `lastNativeCommitAtMs` | Optional Unix-millisecond times for the observed failure, retry request and successful native commit report. |
| `lastCommittedRows` | Optional nonnegative row count from the latest native success report, not total published history. |
| `retryNotBeforeMs` | Optional scheduled retry time; policy, native work and process lifetime still govern whether it runs. |
| `errorClass`, `errorCode` | Optional allowlisted diagnostic tags or `other`, never an exception message or body. |
| `connectCode` | Optional canonical Connect enum name derived only from a numeric code on an error/cause whose actual `name` is `ConnectError`, or `other`. Arbitrary numeric application error codes are not Connect codes. |
| `httpStatus` | Optional actual integer HTTP status from the error/cause chain, only 100–599. Never inferred from exception text or a Connect enum. |

The class allowlist is `Error`, `TypeError`, `RangeError`, `ConnectError`, `AbortError`, `TimeoutError`, `DeadlineExceededError`, `TranscriptLedgerCorruptError`, `TranscriptEntryCommitRejectedError`, `SystemError` and `DOMException`. Error codes are limited to `ECONNRESET`, `ECONNREFUSED`, `ETIMEDOUT`, `ENETUNREACH`, `EHOSTUNREACH`, `EAI_AGAIN`, `ENOTFOUND`, `EPIPE`, `EBUSY`, `ENOENT`, `EACCES`, `EPERM`, `ENOSPC`, `SQLITE_BUSY`, `SQLITE_CORRUPT`, `SQLITE_READONLY` and `SQLITE_FULL`; unfamiliar tags become `other`. Numeric codes map to canonical Connect enum names only when that error/cause's actual `name` is `ConnectError`; numeric codes on other application errors remain `other`. The observer examines at most four error/cause levels and stores no error message, response body, prompt, transcript text, credentials or URL. Keep agent identifiers and all status files private even though diagnostics are restricted.

`native-drained` means the native `flushAgent` call returned true; `native-commit-ok` means a native success report was observed. Neither proves that every copied payload is remotely published in order, that the UI acknowledged/displayed it, or that an intended-client reply arrived. `exhausted` records a bounded retry outcome, not permission to drop history or bypass a corrupt-ledger guard. The telemetry file adds no save kind or persistent policy and grants no activation, prune, billing or SDK/context-merge authority. Continue to use separate full ordered paginated publication verification and actual intended-client send/tool-return/delivered-reply evidence.

### Reviewed terminal return and history merge receipts

This is an explicit recovery after a completed journal’s Local copy was later migrated to Temporal while the original stock conversation was selected again. It is not an automatic save/model update, a first migration or permission to discard either branch. With separately authorized Custom/local-only recovery, initialize a new generation using:

```bash
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py init \
  --next-generation --return-from-terminal
```

Both flags apply only to `init`, require an existing entirely active journal and retain previous generations and private snapshots. Let `O` name the original stock conversation, `L` its recorded terminal copy and `N` the new destination; these symbols are not actual IDs. Planning retains the proven `O → L → N` chain, checks owner/legacy UUID/server-ID/profile bindings and requires the observed source rows to be Temporal with the recorded kinds. The original selected visible stock conversation remains `selectionSourceId` with its exact `selectionSourceServerId`; the new entry’s `oldId` is `L`, so stage copies only the terminal `L` data instead of overlapping copies from both ancestors. Entries set `requiresHistoryMerge: true` and remain copied until explicit review. Preserve originals and separately reconcile any changed instructions, routine definitions/settings, tools and private sessions; selecting a terminal source does not prove all source state is already equivalent.

`stage` still records original `copyProof.historyIdConflicts`, including nonzero conflicts. Do not zero that counter, edit journal phases or delete conflicting messages to pass registration. Prepare coherent immutable transcript/SDK baselines and source snapshots privately through a separately reviewed merge, then explicitly accept its evidence while the entry is copied:

```bash
python3 /home/box/sand-data/openbot-hooks/remint-box-fleet.py accept-history-merge \
  --agent-id '<destination-id-from-journal>' \
  --receipt '/absolute/journal-backup/reviewed/receipt.json'
```

This command requires Custom/local-only authority and the migration lock. It validates and pins `historyMergeReceipt: {path,sha256}` without changing original conflict counts. A different base receipt cannot replace a pin; supported advancements are separately reviewed incremental or unpublished-identifier projection receipts naming that exact pinned parent under their contracts below. Subsequent registration/verification recursively revalidates immutable ancestor evidence rather than trusting an accepted flag; only the current effective baseline is matched against the live transcript. A successful result reports baseline/source counts, pinned digest and `pruneAllowed: false`; it does not claim native app or billing verification. The command accepts reviewed evidence; it does not construct a context merge or run native tools.

#### Exact immutable evidence contract

`history-merge-receipt.py` requires schema `openbot-history-merge-receipt/v1` and exactly these top-level fields: `schema,oldId,newId,selectionSourceId,backup,baseline,sources,projections,sourceOrders,orderResolutions,sdkProof`. IDs and `backup` must match the current journal entry and its proven lineage. Every file reference is exactly `{path,sha256}` with lowercase SHA256. Paths must be absolute within that journal backup, contain no parent traversal or symlink components and identify bounded current-user-owned regular files; intermediate backup directories must be current-user-owned. Every referenced byte digest is checked. The per-file bound is 128 MiB and the transcript/SDK-record bound is 200000; evidence outside those limits refuses. Keep real IDs, snapshots, receipts, native reports and context blobs outside public source and logs.

| Field | Checked contract |
|---|---|
| `baseline` | Exactly `{store,blobs}` file references to the merged SQLite store and blob snapshot. Store integrity, unique nonempty transcript IDs, target metadata owner and SDK root pointer are checked. |
| `sources` | 1–512 unique source/kind rows, exactly `{agentId,kind,path,sha256,generation,sdkRootSha256,blobSnapshot}`. Both selected original and terminal source IDs must be represented within the proven lineage. `kind` is `sqlite` or `platform`; generation is null, a bounded string or integer. |
| SQLite source | Immutable store plus mandatory referenced `blobSnapshot`; metadata root slot must contain the exact claimed source SDK root digest. A null source root requires an empty source blob store. |
| Platform source | Privately captured base64 transcript bodies, optionally wrapped as exactly `{generation,entries}` matching the captured generation. `blobSnapshot` must be null; a visible transcript capture is not hidden cloud SDK state. |
| `projections` | Exact `{sourceId,sourceKind,sourceEntryId,targetEntryId}` rows covering every captured source event once, with no unknown/duplicate projections. Only top-level transcript ownership and the projected entry ID may differ; full remaining payloads must match. Distinct entries within one source cannot collapse to one target. |
| `sourceOrders` | Exact `{sourceId,sourceKind,entryIds}` rows retaining each full source sequence as provenance. Source chat-event order must remain in the baseline except the narrow reviewed correction below. Unclassified event kinds refuse. |
| `orderResolutions` | At most 32 exact `{sourceId,sourceKind,beforeId,afterId,beforePayloadSha256,afterPayloadSha256,reason}` rows; unused or unknown resolutions refuse. |
| `sdkProof` | Reference to exact `openbot-sdk-merge-proof/v1` JSON with fields `schema,engineSha256,rootSlot,rootSha256,blobs,sourceRoots,nativeReport`. `blobs` must equal every actual baseline blob ID/digest, `sourceRoots` must match captured SQLite roots, archived source-root bytes must remain present and baseline metadata/root bytes must agree. `nativeReport` is a pinned JSON file reference; engine digest syntax/provenance and report retention are not execution of a native reader by this helper. |

The only supported chat-order correction is `reason: "cross-turn-adjacent-arrival"`: an adjacent previous-turn sent reply (`t<number>s<number>`) and distinct next-turn user record (`t<number>u`) with a positive timestamp reversal of at most 1000 ms, bound to both exact normalized payload hashes. It is not permission to sort arbitrary conversations, alter timestamps or reorder genuine same-turn messages. Voice/spend/status records retain source-order provenance even when their arrival ordering differs from chat ordering. Other contradictions require separate review.

The live destination may add fresh turns after the immutable baseline: all baseline events must still occur with exact normalized payloads in the same ordered subsequence. Equal row counts, an ID set, a matching fixed root slot or a complete but divergent SDK blob graph do not qualify. The helper binds byte/provenance evidence; separately review actual native root decoding, full prompt/turn/tool/task coverage, current destination SDK state and changed instructions/routines/session authority. Do not treat an arbitrary report file or digest as proof that these semantic checks ran. Preserve source roots and pending tool/routine evidence; never replay recovered actions to manufacture success.

Activation derives preferences and retirement only from complete proven chains ending at the selected destinations. It transfers every ancestor reference to the new target while preserving original sections/order, pins, main selection and existing collapse/notification choices; partial activation does not retire unrelated prior generations. Original and intermediate duplicate server schedules are paused, their enabled local routine definitions are privately backed up and disabled, and the target’s captured routine-restoration progress remains authoritative. Do not re-enable previously disabled outbound routines, replay missed slots or overwrite later user switches. Peer-roster consolidation permits many ancestors only for the proven lineage and equal metadata; conflicting or unrelated duplicates refuse.

A future explicitly requested Official roster repair uses the original `selectionSourceId` and exact server binding, not the intermediate terminal copy. Before changing visibility/preferences it requires the merged destination’s current transcript, including later new messages, to appear in that stock counterpart as full normalized payloads in ordered subsequence. Otherwise it refuses with `Merged local history must reach its stock counterpart before hiding`; mode save alone is not a completed handoff. This guard neither publishes the missing messages nor proves full stock SDK/instruction/routine equivalence. Preserve Local data and complete separately authorized reconciliation rather than hiding the richer branch. Actual intended-client Local or Official send/tool-return/reply and billing remain independent tests; no receipt authorizes pruning.

### Reviewed incremental history receipts

When a source receives new messages after its reviewed immutable merge, do not edit the old baseline, receipt, pinned hash or source captures to make verification pass. Prepare a separate reviewed extension with schema `openbot-history-merge-receipt/incremental-v1`. It has exactly the original v1 top-level fields plus `previousReceipt`, an exact `{path,sha256}` reference to the currently pinned receipt. The same identity, lineage and journal backup must remain authoritative. An incremental receipt cannot initialize an unpinned entry or skip the current pin; resubmitting the same already accepted receipt remains idempotent.

If the immediate parent is an `identifier-projection-v1` receipt, the new incremental receipt does not copy that revision-only `identifierProof` field into its top-level schema. The identifier proof remains exclusively in the exact immutable parent reached through `previousReceipt`, where recursive validation still checks it. The new receipt retains only the incremental schema’s allowed fields; this is strict schema composition, not loss of evidence or a relaxation of the validator.

Validation recursively checks every parent with the same owned-path, no-symlink, digest, file-size and record bounds as the base schema. At most sixteen `previousReceipt` links are accepted; a direct self-reference or a chain beyond that bound refuses. The complete prior evidence remains required and immutable: a new receipt is not authority to replace an old file in place or discard ancestors after advancing the journal pin.

| Evidence | Incremental rule |
|---|---|
| Identity and baseline | `oldId,newId,selectionSourceId,backup` are unchanged. The new baseline contains strictly more transcript records, with every old decoded record and its order as an exact prefix; it cannot edit, remove, deduplicate or reorder any previous baseline event. |
| Sources | The exact `(agentId,kind)` set remains. Each source preserves `generation,sdkRootSha256,blobSnapshot` and its entire previous decoded-record prefix. Original SQLite source evidence remains byte-for-byte referenced by the same row; only a separate platform capture may extend its previous records under unchanged generation. A changed generation, new source identity or rewritten old message requires a different separately reviewed recovery, not this append contract. |
| Projections and ordering | `projections` and `orderResolutions` retain their previous list prefixes; every prior `sourceOrders` row retains the same source identity and complete previous entry-ID prefix. The usual full payload coverage, no-collapse and exact reviewed ordering checks still apply to the complete new baseline. |
| SDK proof | `engineSha256,rootSlot,sourceRoots` stay unchanged. Every previous non-root blob ID and digest remains in the new graph. The former active root's exact digest must remain archived among the new graph's values when the fixed root slot advances. The full new blob graph, metadata/root digest and referenced native report must still pass the base proof contract. |
| Live transcript | The full new baseline must occur in the live destination as the same normalized payloads in ordered subsequence. This permits separately admitted later turns without allowing loss or alteration of baseline messages; it does not validate a live SDK root merely from a matching slot or transcript. |

Use the same explicit `accept-history-merge --agent-id '<destination-id-from-journal>' --receipt '/absolute/journal-backup/reviewed/incremental-receipt.json'` command after preparing and installing the reviewed baseline. A copied entry follows its existing staging checks. For a `registered-box` entry, only an incremental or identifier-projection candidate is accepted, with native profile `harness: "box"`, literal `settings.hiddenFromSidebar: true` and every local routine definition explicitly `enabled: false`. Active/visible copies cannot use this preactivation path. Keep actual owned BOX server/native bindings independently verified; a local profile label is not dispatch authority. The registered-copy acceptor also reads fresh authenticated owner roster authority: exactly one actual owned BOX row must match the destination UUID and its numeric server ID must equal both the journal and profile bindings. It then sends the legitimate idempotent `SetGrokBotAgentClientState` request with `hiddenFromSidebar: true`. On successful acceptance the saved entry records `historyMergeServerHiddenAcknowledgedAtMs`. This is an acknowledged hide request, not verified server-hidden readback: no server-hidden read API is available. Do not relabel that acknowledgement as client visibility or delivery proof.

The operator, rather than the validator, owns the safe installation transaction. Under the authorized Custom/local-only recovery and appropriate locks, capture coherent current UI and SDK databases plus profile/settings authority, and freshly capture every source cloud head with its generation and complete payload/order. Before writing, compare-and-swap those expected destination bytes/metadata and fresh source heads; if any changed, retain both branches and review the new tail instead of overwriting a concurrent send. Preserve the prior accepted artifacts, profile/settings, instructions, routine switches and original sections throughout.

Prepare the extension off the live path. Use the actual compatible native codec/readers to verify the detached UI projection and SDK graph, append any reviewed imported context as literal historical context, and retain all prior roots/blobs and pending tool/routine evidence. Do not execute imported tool calls, regenerate past assistant replies or replay user actions to manufacture a passing state. Only after the authority checks remain current should the operator install the detached baseline on the hidden paused copy and call the canonical acceptor, then separately verify full ordered publication before activation. The acceptor does not perform these CAS/install/native-reader steps or automatically synthesize a safe history/context merge. The separately reviewed operator should persist its durable apply intent before any UI append, then record UI-append completion, SDK installation and canonical acceptance. After interruption, resume only when each observed UI/SDK/profile/settings state is exactly the captured old state or the operator’s planned installed state for that step; an arbitrary third state refuses without writes. Do not clear the intent or regenerate expected hashes to bless unknown changes. This is a recovery transaction requirement, not a new generic CLI or automatic merge facility.

Before activation changes the journal, routine switches or sidebar, the canonical activator runs `history-merge-receipt.source_freshness` for every selected nonactive copy with a pinned receipt. It first revalidates the complete receipt, then compares every captured platform source against a fresh authenticated owner `ListGrokBotTranscriptEntries` request with `limit: 1`: generation, greatest sequence and SHA256 of the raw decoded body must match the captured head, including the empty-head case. Original SQLite sources must retain transcript count, greatest sequence, canonical decoded-head payload hash and actual SDK root digest. Owner/no-symlink checks still protect live source paths. Observations are shared only within that activation call, never persisted as reusable freshness authority.

Any mismatch refuses activation before source hide/pause or target routine/sidebar writes, retaining both branches for an explicitly reviewed incremental staging and acceptance. This is a source-head freshness gate under the native append-only transcript/head contract, not a full concurrent-writer transaction or proof that uncaptured interior history cannot change. It does not supply server-hidden readback, synthesize a merge, replay actions or prove publication/client delivery. There is no new CLI; the check is part of the existing activation path. The panel coordinator may attempt the single bounded literal-tail preservation below for this exact freshness refusal; unsupported changes still require separate review.

An accepted incremental receipt proves only its checked immutable byte/provenance and live-transcript contract. It does not merge changed instructions, routines, tool permissions or session authority automatically; it does not prove SDK semantic coverage merely from the retained report. Actual loaded context, current binding, publication, intended-client message/tool-return/delivered reply and billing require their own evidence. The operation never grants pruning, mode changes, source deletion or replay permission.

### Bounded panel cloud-tail preservation

The existing deterministic Local-transfer coordinator can handle one narrow source of interrupted handoff: new literal cloud messages appended after an already reviewed merge while its destination is still a hidden paused registered BOX copy. This is part of the existing dashboard/API start/resume workflow, not a general autonomous history merger or a new standalone recovery command. Its [history-tail-repair.py](../../recovery/hooks/history-tail-repair.py) coordinator and [history-tail-codec.mjs](../../recovery/hooks/history-tail-codec.mjs) bridge are product code; private captures, plans, receipts, native vendor sources, SDK blobs and provider credentials stay outside public Git history.

This automatic path is restricted to new hidden registered staging copies before their first activation. It does not solve the common Official-to-Custom return where a Local counterpart is already active and its Official source has received newer messages. That situation remains explicit review pending a separately reviewed fresh hidden generation workflow that preserves both live branches. Never change an active copy back to `registered-box`, hide it to qualify, overwrite its advanced SDK or reset a generation/receipt to force this bounded path.

Before activation, the coordinator checks the entire known source set through canonical freshness checks. Only the exact changed-source-head refusal can enter `preserving-late-history`. Every other ownership, role, receipt, roster or verification refusal retains its existing review path. The helper requires the actual inherited owned `openbot-custom-dispatch.lock` file descriptor, validates its inode/device/owner and retains the exclusive writer lock; manually starting an unlocked helper is not equivalent. Literal Custom and owned Local opt-in remain authority before work and mutation steps. Selecting Official or withdrawing that authority stops further work rather than silently flipping modes.

| Boundary | Required behavior |
|---|---|
| Per-job budget | One bounded attempt per stable transfer `jobId`, at most 128 imported records and 4 MiB of imported literal UTF-8 text across all destinations. These are import bounds, not provider-token or full-request-byte limits. Never reset job/evidence files to gain another attempt. |
| Cloud source | Existing reviewed platform captures must have the supported generation/entries wrapper. Fresh owner reads retain the same generation and exact old boundary record, with a contiguous increasing sequence suffix. Changed boundary, fork, conflicting repeated sequence, stalled paging, changed generation or oversized suffix refuses. The prior immutable source capture remains; a new capture extends it. |
| Destination | `registered-box` only, local hidden flag literally true and every copied routine explicitly disabled. Fresh actual owned BOX authority must match the journal and profile numeric server IDs. Profile/settings/routine hashes and effective selected model/capacity are captured and compared again before apply. A visible or active copy, changed SDK graph, destination messages after its baseline, changed settings or ambiguous route refuses. |
| Context | An existing reviewed typed SDK root and exact reviewed engine, native SDK worker and allocator contracts are required. Empty-room roots, unknown source formats, nonliteral imported events, pending tool calls, changed source SDK state or a tail that cannot fit the selected model's context after reserving its configured output budget require review. There is no summarizer, provider fallback, inference or account/key change in this path. |

Supported tails use a strict reviewed field allowlist for completed native text events, deliberately narrower than the full native transcript schema. The admitted core is a nonempty ID, kind and finite nonnegative native timestamp no greater than JavaScript’s maximum safe integer; native fractional timestamp values are preserved rather than rounded. A `message` requires a user/assistant role, string content and explicit literal `isStreaming: false`; absence of that completion marker does not qualify. A text `send-message` must retain only its reviewed text message shape. Known native correlation metadata is admitted only with checked types: bounded nonempty control-free `requestId,clientNonce,batchId` strings and a validated `sentWhileOfflineAtMs` timestamp on messages, or checked `requestId` on sends. Any additional metadata needs an explicit reviewed native contract before admission; raw string content alone never makes an arbitrary event safe. Unknown root/nested fields, failed/cancelled status, tool/image/attachment/parts envelopes, recipients, peer/DM/rich-message semantics or cross-bot destination metadata require preservation review. Retaining such fields only in raw UI history would be insufficient if SDK conversion silently dropped their semantics; the helper must refuse instead of claiming a lossless context transfer.

For admitted events, the full original event payload remains in the UI/source evidence, with only owner normalization and a deterministic bounded entry-ID alias where needed to avoid a collision. The SDK receives the exact reviewed literal text and role through actual native conversion, preceded by a notice that it is background history rather than pending requests or instructions. Imported text is never executed as an old action, regenerated as an assistant reply or dispatched to a tool/provider. Unsupported semantic fields stay in their preserved original branches pending explicit review; no success receipt may substitute plain text for their meaning.

Preparation creates private independent coherent UI/SDK snapshots under the current journal backup and normalizes only detached candidates to non-WAL SQLite mode. Original source databases, existing receipt artifacts and live SDK remain untouched during preparation. The complete hidden destination transcript must equal the previous immutable baseline, and the complete existing SDK graph must match its pinned proof. A new detached UI store strictly appends the tail. Native conversion proves exact text/role equality, preservation of all previous prompt messages and other root fields, root binary roundtrip, actual state-handle context readback, complete reachable closure and no unresolved references. Pending tools refuse; network and tool/process entry points are denied. Native transcript parsing/pagination at 37 and 500 entries and next-ID allocation are checked before any live apply. The bridge independently checks the exact plan file byte SHA256 and records it as `sourcePlanFileSHA`; its separately recorded `sourcePlanSHA` is the hash of JavaScript-normalized JSON. These have different representations and must not be treated as interchangeable authority. A matching normalized object hash cannot replace the exact plan-file digest check.

The bridge uses the selected route only to fit estimated context plus the configured output reserve and update provisional SDK capacity metadata. It does not call that model or change billed usage counters. It archives the previous exact active SDK root under its digest and retains every previous non-root blob before adding literal context and selecting the new root. Only the permitted native activity metadata changes in the detached UI; other unread/settings fields remain. The new SDK proof and `openbot-history-merge-receipt/incremental-v1` receipt retain the exact prior receipt reference, every original source, projection/order prefix, prior root and complete blob history. Canonical receipt validation runs against an independent detached mirror before acceptance is possible.

A private per-job `bounded-tail-job-<jobId>.json` records preparing/prepared/completed/review-needed state under the journal backup. Each candidate has immutable `intent.json` containing the previous/new receipt references, expected profile/settings/routine guards, exact server binding, route, source heads and before/planned UI and SDK snapshots. Interrupted preparation requires review; it is not rebuilt blindly. A prepared intent may resume its same plan, and a completed attempt cannot import another tail for that job. Keep these files private and retain them through disconnects/restarts.

An explicit panel start/resume request may allocate a fresh job ID only when persisted transfer progress is `review-needed` and the previous owned bounded-tail marker is exactly `{"phase":"review-needed","jobId":"<same-prior-job-id>"}`, with no additional fields. That shape records preparation failure before any apply; the old marker and evidence remain immutable and retained. An automatic watcher tick cannot allocate this retry job. Missing, malformed, unsafe or any other marker shape supplies no fresh-ID authority. Prepared, partially applied, accepted, completed, publication and reload-bound jobs keep the original job ID and exact recorded crash-resume path; never overwrite their markers or rotate IDs to reset an applied attempt. This exception creates no migration generation and changes no mode, settings, sections, source history or pending actions.

Before applying, the helper rechecks the pinned receipt, hidden paused owned BOX binding, settings/route guards, local source heads and freshly read cloud heads. It sends the legitimate idempotent server-hide request and records an acknowledgement, not server-hidden readback. Durable apply progress is recorded before the first database mutation. UI append and SDK-root installation use separate `BEGIN IMMEDIATE` transactions under the existing migration lock; they are a resumable sequence, not one atomic transaction spanning both databases. Every observed UI row/KV state and complete SDK graph must equal the exact captured before state or exact planned after state. Arbitrary third states refuse. UI changes are only the planned transcript append and permitted native unread/activity metadata; SDK changes add planned blobs and replace only the fixed active root while archiving its old bytes. Do not overwrite live files from a detached snapshot wholesale.

After the UI and SDK transactions, fresh cloud heads and Local authority are checked again before the canonical `accept-history-merge` path pins the prepared incremental receipt. Durable progress distinguishes `prepared`, `ui-appended`, `sdk-appended` and `accepted`; an interruption resumes only the already recorded plan and exact states, never regenerated expected hashes. If a source advanced again, preserve partial progress and both branches for review rather than ignoring it. An already accepted matching receipt is validated idempotently. These checks are not an atomic transaction against every external sender; final freshness remains required.

Successful preservation returns to `waiting-for-publication`, requests canonical native republication and records safe aggregate repair counts in private transfer progress. It cannot activate from detached proof or an accepted receipt alone. The next transfer pass must reverify full ordered published payloads, current source freshness and complete owner-roster scope before the normal guarded activation preserves original sections/pins/main pointer and captured routine switches. Unsupported data, changed authority, overflow, missing native pins or another late tail lead to review; they never authorize lost messages, source deletion, tool replay or a Temporal fallback. AI advisor output remains diagnostic and cannot approve or perform this repair.

SDK cache activation has a separate per-job gate. After canonical receipt acceptance, the coordinator requests the existing owned idle publication reload and writes that request’s exact UUID as `reloadRequestId` in the durable completed `bounded-tail-job-<jobId>.json` marker. A subsequent pass must first resume any prepared partial UI/SDK transaction; it cannot run publication verification against a half-applied candidate. For a completed job, verification and activation wait until `<data>/openbot-publication-reload.json` has the same `requestId` and literal `phase: "acknowledged"`. Published UI transcript rows alone cannot prove that the native process discarded an older cached SDK root.

If a crash occurs after the receipt pin but before the reload request is issued or recorded, resume queues a canonical reload and binds its new ID to the completed job. A missing or displaced global reload request similarly causes a fresh canonical request to be recorded; an acknowledgement belonging to another request does not satisfy this job. Do not hand-edit request IDs, phases or the job marker to bypass the wait. The existing source/process/idle/mode guards still control signalling, and an acknowledged matching replacement still needs full ordered publication, fresh source/roster checks and an actual intended-client send/tool-return/reply before success can be claimed.

Detached native fixture success proves the checked candidate contract only. It does not establish that these helpers are installed or loaded on a particular Computer, that a live automatic apply ran, that publication completed or that an actual Grok client delivered a reply. Verify deployment, mode/settings/section preservation, publication and intended-client send/tool-return/reply separately before reporting success. No provider keys, conversations or private native bundles belong in public installation examples. Passing standalone native and full detached preparation checks under the strict completion contract does not upgrade this to a deployed or live-applied result.

### Reviewed unpublished identifier projection

The platform's native transcript entry identifier is bounded to 128 UTF-8 bytes. An older synthetic same-ID conflict alias could exceed that bound and stall publication even while its message body was valid. Fixing the generator prevents future oversized aliases; an existing immutable merge needs the separate explicit schema `openbot-history-merge-receipt/identifier-projection-v1`, never an in-place evidence edit or a transcript-generation reset.

This receipt has all base-v1 top-level fields plus exact `previousReceipt` and `identifierProof` file references. `previousReceipt` must name the current pinned `{path,sha256}`; an unpinned entry cannot begin with a projection receipt. The same ownership, path, digest, depth and size bounds apply to the entire retained chain. Every ancestor's baseline, sources, SDK evidence and provenance still validate immutably, but only the current effective baseline's IDs/payloads are matched against the live transcript. Requiring obsolete ancestor aliases to remain live would defeat an explicitly verified identifier correction; omitting their immutable validation would lose evidence. Incremental receipts keep their strict append-only contract and cannot be used as a rename override.

| Evidence | Identifier-projection contract |
|---|---|
| Preserved authority | `oldId,newId,selectionSourceId,backup,sources,sourceOrders,orderResolutions,sdkProof` equal the previous receipt exactly. The SDK blob snapshot reference in `baseline.blobs` is also unchanged. |
| Baseline | Same record count and sequence values in the same order. Every unlisted `(seq,id,entry)` row remains byte-exact. A listed row changes only its top-level entry ID and matching SQL ID; every other decoded message field remains exactly equal. No event is added, removed or replayed. |
| Allowed changes | A listed old ID must exceed 128 UTF-8 bytes and match the known synthetic `<original>~openbot-branch-<64-hex-digest>` form, optionally with legacy trailing `~` collision characters. It must lie strictly after the complete captured published prefix. The replacement must equal the canonical full-digest bounded allocator's result against the retained occupied-ID set, including deterministic collision fallback. Native/original IDs and already published IDs cannot be reprojected. |
| Source coverage | Source identities, generation, payload variants and order stay unchanged. Only `targetEntryId` values in the previous projection list are remapped through the exact listed old-to-new alias map; source entry IDs and quoted/nested identifiers remain historical evidence. |
| SDK | The existing SDK baseline and proof references stay unchanged. A separately pinned version-reviewed native reference audit must prove zero structural references to the corrected aliases and byte-identical complete blob graph; literal quoted aliases remain unchanged. |

`identifierProof` references exact `openbot-unpublished-identifier-projection/v1` JSON with fields `schema,previousReceipt,publishedPrefix,changes,nativeSdkProof`. Its parent reference must equal the receipt's parent. `changes` contains nonempty unique sequence rows exactly `{seq,oldId,newId}`, all present in the baseline. `publishedPrefix` is a pinned file reference to exact `openbot-platform-transcript-prefix/v1` with `schema,agentId,generation,capturedAtMs,entries`. Capture the complete actual platform prefix through authenticated owner reads, preserving generation and each raw base64 body. Every captured `seq,entryId,body` must match the previous immutable baseline from sequence one through the prefix, byte-for-byte. A partial or invented capture cannot establish that an alias is unpublished; the operator must capture and compare the current real prefix before writes. The validator binds the captured evidence rather than performing that owner capture itself.

The read-only [history-identifier-sdk-check.mjs](../../recovery/hooks/history-identifier-sdk-check.mjs) requires the exact reviewed native worker source digest, owned bounded nonsymlink coherent blob snapshot and private synthetic-ID input/output files. It uses that worker's actual native root/turn/user codecs and reachable-graph reader without booting its main runner; network and tool/process side effects are denied. It verifies root binary roundtrip, complete reachable closure, absence of structural alias references and unchanged hashes of every SDK blob. Alias appearances in literal `text,content,summary,reasoning` fields are retained byte-exact; an active unknown binary location cannot be classified as harmless text. A missing root is valid only for a truly empty blob graph. Unsupported worker bytes or ambiguous references refuse rather than editing SDK state.

Run this audit only against an independent coherent diagnostic clone, never the live SDK database, a hard link to it or a previously pinned baseline. Before invoking the checker, normalize only that clone to `PRAGMA journal_mode=DELETE`, close it and confirm no WAL/SHM/journal sidecars remain. Read-only SQLite can otherwise create WAL metadata despite opening without write authority. Verify that every blob ID and byte digest in the normalized clone still equals the immutable original graph; database-header normalization is not permission to alter SDK payloads or replace the receipt’s original SDK/blob references. The checker refuses a WAL-mode header, checks the diagnostic database hash before/after and refuses created sidecars; it never normalizes the header itself. Its SQLite scans retain each `StatementSync` through iterator completion so Node 22 garbage collection cannot finalize an in-progress scan. These implementation checks preserve audit reliability and do not establish live application or delivered replies.

The pinned native proof uses schema `openbot-identifier-sdk-reference-proof/v1` and must match the reviewed worker digest, unchanged SDK root digest and exact sorted corrected-old-ID set; `structuralReferences` must be empty, `graphUnchanged: true` and `sourceUpdated: false`. A populated root needs `rootRoundtrip: true`; an absent root needs `rootAbsent: true`. Retain the actual proof, literal/archive blob hashes and all original SDK blobs privately. These checks concern identifier references and graph preservation, not full context/instruction equivalence or client delivery.

Use the existing explicit `accept-history-merge` command only after the separately reviewed detached correction and current authority/CAS checks. Registered copies must still be hidden, paused and actual owned BOX with exact journal/profile/server bindings; hide acknowledgement retains its separate limitations above. Never overwrite an active copy, rename a published prefix, substitute a new SDK graph, reset generation/ledger/cursors, change instructions/routines/settings, run recovered actions or prune original evidence to bypass this contract. After acceptance, verify complete ordered native publication and actual intended-client message/tool-return/delivered reply separately. The new receipt grants no delivery, billing, mode-change or deletion proof and introduces no new CLI command.

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

### Optional tool-free transfer advisor

The panel's deterministic Local-transfer coordinator remains the only transfer writer. When it persists a review-needed refusal, it may start the separate optional [transfer-diagnostics.py](../../recovery/hooks/transfer-diagnostics.py) advisor. This is a bounded inference classification, not a tool-enabled repair agent. It cannot mint/register/activate bots, reconcile histories or SDK roots, edit sections/routines/settings, reload a host, execute returned commands, select another route or declare verification complete. The transfer continues or waits according to its own existing guards regardless of advisor availability. Its finite phase allowlist includes the existing `preserving-late-history` refusal phase, allowing the same bounded review without AI access to history or additional execution authority; unknown phases still refuse, and route, effort, call-budget and tool restrictions remain unchanged.

Private files under the selected data root are `openbot-transfer-diagnostic.json` and its separate `openbot-transfer-diagnostic.lock`. The advisor reads the bounded owned `openbot-custom-dispatch-state.json`; it writes only its own atomic diagnostic receipt under its exclusive lock. Never edit the coordinator's phase, journal, receipt or holds to make the advisor run. These files are installation state and stay outside public source/backups intended for other users.

```bash
# Status only; no inference request:
python3 /home/box/sand-data/openbot/recovery/hooks/transfer-diagnostics.py \
  --state /home/box/sand-data/openbot-custom-dispatch-state.json
# Explicit bounded diagnostic invocation, also used by the coordinator:
python3 /home/box/sand-data/openbot/recovery/hooks/transfer-diagnostics.py \
  --state /home/box/sand-data/openbot-custom-dispatch-state.json --run
```

Only strict allowlisted phases/refusal codes, bounded aggregate counts and boolean merge/publication observations form the model's diagnostic facts. No bot names/IDs, histories, instructions, summaries, sessions, credentials, raw errors, arbitrary strings or tool arguments leave through this diagnostic. Its opaque local job-scoped request identity is not a bot/conversation identity. Accept only the exact single-object JSON advice-code schema, either raw or inside one whole optional `json` code fence; the sole key must be one allowed `adviceCode`. Surrounding prose, multiple blocks, duplicate/extra keys, tool/function requests, malformed/oversized answers and unrecognized codes refuse. Display only product-owned static allowlisted advice, never returned prose or commands. Length-truncated output reports inert `advice-truncated`; bounded numeric usage can also identify a reasoning-starved answer without retaining response text. A failed/truncated classification cannot become a decision or trigger extra unbounded calls.

The selected global model must have one unambiguous saved route on an approved direct origin: OpenCode Go `https://opencode.ai/zen/go/v1`, OpenAI API `https://api.openai.com/v1`, or NVIDIA `https://integrate.api.nvidia.com/v1`. Other origins, including unsupported OAuth/backend origins, report unavailable; neither a provider family label nor a same-slug route authorizes substitution. The helper verifies the owned loopback hop process/listener and calls that existing hop, which retains provider credential/protocol handling. It does not read provider secrets, contact a provider directly, pool accounts, add a provider or fall back. Custom and current route/state authority are rechecked around the request; changes discard the result.

For this auxiliary classification only, derive the lowest explicitly catalog-supported `reasoning_effort` in order `none · low · medium · high · xhigh · max`. Do not inherit a costly bot/global effort into the small diagnostic budget or invent `none` when the model does not advertise it; a catalog declaring `low` as its minimum requests `low`. A missing/duplicate selected model, malformed/duplicate effort metadata or no explicit supported level refuses instead of guessing, changing models/providers or falling back. This request-scoped effort leaves all bot, global model and compactor settings untouched. The 256-token maximum, 12-second network deadline, 15-second parent bound and persisted fingerprint/backoff remain unchanged.

Before network work, persist the fingerprint of the local job, selected route, request-scoped effort and sanitized refusal facts in the job-owned `seenFingerprints` ledger. Retain the legacy single `fingerprint` when upgrading an older receipt. The ledger holds at most 16 unique SHA256 fingerprints without eviction: an `A → B → A` refusal change must not repeat the first call, even after restart or timeout. Each job permits at most 16 attempted diagnostics, including failed/incomplete attempts already reserved before network work; exhaustion persists disabled `diagnostic-budget-exhausted` and makes no further call. Malformed ledger authority fails closed as `invalid-advice-ledger`; preflight failures preserve prior receipts rather than discarding their attempted-call evidence. The atomic diagnostic sidecar has a hard 4096-byte write bound.

A new fingerprint remains subject to the persisted 15-minute backoff, including when a different job is selected. A new job may start its own fresh 16-entry ledger only after that existing backoff; a new job ID is not permission to skip the wait. Never delete receipts/locks, evict hashes or manufacture a job to bypass these limits. Network work retains its 12-second deadline, bounded response size and 15-second parent subprocess limit. Create `openbot-transfer-diagnostics.disable` in the data root to opt out; it disables future calls without changing transfer authority or saved routing.

The panel must distinguish unavailable/disabled, failed, pending/running and available advice honestly. Missing advice is not successful diagnosis; a static suggestion is not a proven cause. `nativeAppDeliveryVerified` remains false. Neither advice, an inference HTTP 200 nor a completed transfer worker proves ordered full publication, loaded context, actual Grok client acceptance/tool return/delivered reply, billing or prune safety. Those remain separate deterministic checks and live tests.

### Routing diagnostic egress

`diagnose-routing.py.open_no_redirect` preserves detected operator `http`/`https`/`all` proxy choices and no-proxy exclusions. If any operator proxy is present, it skips the Computer fallback even when that fallback value is invalid. Only when none is configured may the provided `SAND_EGRESS_TUNNEL_PROXY_ADDR` supply the HTTPS diagnostic proxy; no address retains the existing direct behavior. This is a per-process diagnostic transport choice, not a saved networking, exit-node or mode change.

The provided fallback accepts only a plain HTTP loopback listener at `127.0.0.1`, `::1` or `localhost` with an explicit port from 1 to 65535, optionally supplied without the HTTP scheme. Credentials, remote/lookalike hosts, non-HTTP schemes, non-root paths, query/fragment, backslashes and whitespace/control characters refuse with `invalid-egress-proxy`. Existing operator policy is not rewritten or replaced. The helper keeps HTTPS certificate verification and rejects redirects instead of following credentials to another endpoint. A successful diagnostic network request still does not establish BOX admission, native publication, intended-client send/reply or billing.

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

### Checked panel local-engine selection

The existing normal Custom save/reconcile path can select an already enabled trusted `openbot-local-engine.json` pin before native wrap census. This breaks the dependency in which an updated marker-free vendor host no longer has a usable BOX factory, census rejects a Custom save, and Custom-only recovery never starts because mode remains Official. It adds no save kind, automatic first binding or permission to invent a local runner. Use the existing `use-model` or other intentional Custom save through the owned panel/API; do not hand-write `openbot-mode` to bypass census. Missing policy or an explicitly disabled policy retains the ordinary checked census path.

Selection requires schema 1, boolean enabled and both exact lowercase source hashes. The optional generation chooses the existing fixed pair or its strictly validated 32-hex immutable generation. Each selected directory is current-user-owned and nonsymlinked. Source files are current-user-owned no-follow regular files, bounded to 48 MiB, fully UTF-8 decoded and checked for changed size/mtime/inode/device during the read. The complete bytes must match the pin. Both stock and wrapped sources must retain the reviewed native `sendPrompt` body structure and scheduled/turn runner anchors; a marker or version string alone does not satisfy the check.

The stock half must have no OpenBot wrapper marker, and the wrapped half exactly one. Rebuilding the canonical wrapper from that stock with this installation’s runtime path and current payload fingerprint must exactly equal the selected wrapped bytes after only the defined payload-stamp refresh. An arbitrary marked bundle or a mismatched runtime path is refused. Reconcile syntax-checks the complete selected source before publishing it, then compares the original raw mode, active host bytes and every read policy/artifact/snapshot against their captured values. A changed mode returns `mode-changed`; changed host/artifact authority returns `source-changed` or `local-engine-refused`. Invalid structure, ownership, hash, policy or original vendor authority returns `local-engine-refused`; syntax failure retains `syntax-check-failed`. These failures do not grant a bypass, downgrade or Temporal fallback. They are pre-write checks, not a filesystem transaction against every possible external writer.

After the enabled exact-hash pin pair passes these checks, a later Custom save can preserve the already wrapped current native BOX runner instead of reinstalling that older pin. The current wrapper must reconstruct exactly for this installation’s runtime path around its current underlying source, with the reviewed native BOX runner structure intact. Only the defined installed payload stamp may refresh; legitimate newer post-wrap repairs remain in the selected source. A wrapper referring to a different runtime or a partial marked source that lost its native runner refuses rather than being silently adopted or overwritten. The retained pin remains the checked recovery source when stock has become unsupported or BOX execution is missing. No new policy, manifest field or save kind is introduced.

This prevents an ordinary `set-secret`, `use-model` or other Custom configuration save from resetting a repaired current engine to the retained older pin and making recovery reapply the same newer repairs and reload again. It does not disable necessary payload/source reloads or make arbitrary marked source trusted. Complete pin validation, syntax and eager dependency preflight, original vendor preservation and raw mode/host/catalog/payload/artifact comparisons still govern publication. Actual loaded-runtime compatibility, native ownership, history/context preservation and intended-client tool/reply delivery remain separate evidence; retaining current bytes is not itself a canary or full-parity result. Retaining the current engine also keeps recovery’s existing known backup, while the separate exact vendor snapshot remains Official authority; byte-identical vendor snapshot/record files are not rewritten.

The panel/API can expose optional `localEngine` on state and save responses:

```json
{"localEngine":{"state":"ready","nativeDeliveryVerified":false}}
```

`state` is `ready`, `unavailable` or `not-pinned`. It is a read-only hint, not selected mode, a loaded process or a transfer receipt. The service can cache this inspection for five seconds, invalidating it on observed host/mode/plan/policy/payload/artifact metadata or dependency-checker changes. A direct save never trusts the cache: it repeats exact artifact checks, current payload/catalog/mode/source comparisons and the dependency/syntax preflight before writes. Re-enable Custom uses the saved valid `activeModelId`, preserving the user’s chosen route rather than taking the first catalog entry. Intentional per-bot assignments remain in force. The panel can permit an otherwise unknown native census only for a ready pin; foreign listeners and foreign wraps remain refused.

Before selection, eager top-level static non-relative native `require` dependencies are parsed and resolved from the actual native host path without executing or booting vendor code. Missing required imports refuse through `local-engine-refused` before host/mode/plan publication. Resolution stays live even when the parsed import list is cached. Deferred callbacks, class/function bodies and optional imports inside `try` are outside this minimum boot preflight; it does not establish full native module/API compatibility or successful chat execution. The readiness response always keeps `nativeDeliveryVerified: false`, including `ready`.

The native vendor source is preserved independently before selecting the composed local engine. Private files in the selected data root are:

| File | Contract |
|---|---|
| `openbot-official-host.cjs` | Exact original unwrapped vendor bytes, saved 0600; never the composed local engine’s `stock.cjs`. |
| `openbot-official-host.json` | Owned bounded regular nonsymlink JSON, maximum 16 KiB: `{schemaVersion:1,sha256,localNormalizedSha256}`. Both hashes are lowercase SHA256; the latter identifies the selected local source with its payload stamp normalized. |

These files contain vendor runtime source/provenance and stay outside public source and examples. Existing composed installations without the independent snapshot can recover vendor authority only from their owned bounded upstream-sync state or engine policy’s exact `upstreamVersion` and `upstreamSourceSha256`, plus matching owned `openbot-upstream/releases/<version>/vendor.json` and `host-main.cjs`. The capture’s metadata and bytes must match that exact source hash and contain neither the OpenBot wrapper nor local compatibility marker. Missing or invalid capture refuses rather than treating local `stock.cjs` as vendor stock. All authority read during selection is included in the pre-publication comparison.

A later explicit Official reconcile restores this independent vendor only when the active source is a recognized composed local engine: the compatibility marker or exact saved normalized local-source hash identifies it. A fresh ordinary marker-free vendor update remains authoritative; it is not replaced with an older saved vendor. Restore keeps the saved provider plan, credentials, local artifacts and logging preference. When the restored vendor cannot supply an optional logging tap, successful controls can report `officialLogging: "unavailable-host-hook"`; this is capability disclosure, not captured stock traffic. Host loading and restart still follow the existing supervisor ownership/deferred-idle rules.

No engine selection edits bot identities, sections, history databases, SDK/session roots, instructions, routines or their switches. A successful save can establish selected mode and validated on-disk engine bytes; it does not establish current BOX ownership, loaded-runtime identity, a live history/context union, native app admission, actual tool-return/reply delivery, metering or prune safety. Preserve every source branch and private recovery evidence, inspect the loaded runtime separately and complete an actual intended-client local message/tool/delivered-reply test before claiming the handoff works.

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

### Reviewed vendor download origin

`local-upstream-sync.py.vendor_origin` derives the download base only from one of two reviewed trusted-stock-source layouts; there is no user-supplied origin field or JavaScript evaluation. The legacy layout uses unique literal `SAND_HOST_BUNDLE_S3_BUCKET`, `SAND_HOST_BUNDLE_S3_REGION` and `SAND_HOST_BUNDLE_S3_PREFIX` assignments. The shared public-daemon layout uses unique literal `PUBLIC_VM_DAEMON_BUCKET`, `PUBLIC_VM_DAEMON_BUCKET_REGION` and the same prefix, together with the exact HTTPS `PUBLIC_VM_DAEMON_BUCKET_ORIGIN` template, exact `SAND_HOST_BUNDLE_PUBLIC_ORIGIN = PUBLIC_VM_DAEMON_BUCKET_ORIGIN` alias and exact `SAND_HOST_BUNDLE_PUBLIC_BASE_URL` origin-plus-prefix template. Only whitespace around assignments may vary; literal bucket/region/prefix values remain restricted to letters, digits, underscore and hyphen.

Duplicate assignments, dynamic expressions, mixed or partial chains, HTTP/lookalike origins and altered interpolation targets refuse capture. The parser does not execute source or accept arbitrary policy URLs to make discovery succeed. An unknown origin defers release work for review; preserve the current runtime/mode/settings rather than stamping an advertised version, switching mode, bypassing hashes or activating an unverified source. Supported discovery still requires the existing bounded download, archive/source digest, extraction/ownership, preparation and activation checks. A parsed origin or newly staged release is not loaded-engine, native delivery or full-parity proof. This compatibility addition introduces no save kind, configuration schema or automatic live activation.

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

### Native canonical backup status

The neutral [store-status-inject.py](../../recovery/hooks/store-status-inject.py) v2 patch remembers only numeric metadata from validated canonical reads or completed native writes, deduplicates cold reads and preserves newer acknowledged receipts. Failed reads clear their in-flight promise for retry; failed reads/writes and live unacknowledged edits never seed success. A successful empty reset reports `durable: false`. Cached durable status is not a fresh read or completed full flush.

The summary walks the existing native Map directly or lazily iterates only own object entries/keys, without full `Object.entries`, replacement Map, value-array or Map-to-object copies in that bookkeeping path. Native parsing/storage and existing classifiers remain; this reduces status-poll allocation overhead, not all native RAM, manifest parsing cost or inference latency. Do not describe it as a complete memory fix or a measured memory ceiling.

Only local catch-binding identifier renumbering is tolerated at the exact acknowledged-write anchor. Every read/write/hydration/acknowledgement fragment must remain complete and unique. A complete known `/* openbot-canonical-store-status-v1 */` composition upgrades to `/* openbot-canonical-store-status-v2 */`; complete v2 is a checked fixed point. Unknown, partial, duplicate or mixed compositions refuse rather than advertising repair. Unfamiliar contracts report compatibility-required and preserve native delivery.

SandBoxStore-only large serialized requests still use the separate native Connect gzip helper; stock clients stay unchanged. Payloads, authentication/interceptors, writer fencing, etag/CAS, conflicts, full-flush/durability and update acceptance gates are unchanged. Direct S3 uploads/manifest writes are outside status/RPC gzip; their source-level byte-body copying/socket buffering is now addressed by the [separate byte-upload helper](#neutral-native-direct-store-byte-uploads), without proving complete transport or a fresh accepted flush. These patches change no mode/settings, add no save kind or persistent status schema, and prove neither fresh backup completion nor inference/chat delivery.

Backup preparation handles only `/tmp/sand-box-store-pack`, `/var/run/anyrun-hibernate-hook` and the fixed `/run/sand-box-store-flush` channel, with symlink/foreign-owner refusal and no recursive data ownership sweep. The flush directory has the separate preserve-existing and checked-creation contract below. A missing shutdown hook is installed as root-owned 0755 with an exclusive link. Only the byte-exact previous owned fallback may be upgraded: save its original bytes, require a root-owned regular target, recheck its SHA256 and atomically replace through noninteractive sudo. Other native or foreign root-owned hook contents are preserved. The credential-free fallback reads the gateway token only at execution, requests authenticated native `snapshotBoxStoreNow` with `includeIdleOnly: true` and a 180-second timeout, and exits successfully only for parsed literal `ok: true`. Preparation failures are recorded separately in `backupRepair`; successful hook status is `existing`, `installed` or `updated`. A repaired hook/channel or cached receipt does not establish a fresh snapshot or image completion. This repair changes no inference mode and bypasses no writer fence, erase or durability check.

Custom repair validates the complete owned transformation pipeline, keeps backups, compares actual expected bytes and reloads only through owned idle/checkpoint authority. Do not force profiles, migrate identities, enable routines or remint bots without specific authorization. Retain all current data and rollback authority.

### Neutral native direct-store byte uploads

[store-byte-upload-inject.py](../../recovery/hooks/store-byte-upload-inject.py) adds the checked `/* openbot-store-byte-upload-v1 */` helper only to `SandBoxStoreServiceObjectStore.putContentAddressed` and `putMutable` byte-PUT bodies. Ordinary `Uint8Array` and Buffer inputs become immutable Blobs assembled from 65536-byte (64 KiB) views of exactly the supplied byte range. A single large Blob still produced an oversized client socket queue in the tested runtime; explicit bounded parts matter. Uncommon non-byte inputs and SharedArrayBuffer-backed views retain the previous `Buffer.from` coercion. File uploads, multipart uploads and store RPCs stay on their distinct native paths.

Native fetch continues to provide body length and replay behavior. The checked change preserves bytes, native hashing/size logic, presigned headers, redirects, abort signals, conditional retries, ETag/CAS, writer fencing and conflict handling. It adds no manual content header, relaxed commit/flush acceptance or alternate credentials. The two class/method regions and original bodies must be unique and ordered; missing/changed/ambiguous anchors, partial markers or duplicate helpers refuse. A complete known composition is a checked fixed point.

Custom includes the byte-upload helper after canonical status v2 and native RPC gzip, followed by [browser-session coalescing](#coalesced-native-browser-session-backups), in its checked repair pipeline. Official composes those same four neutral transformations before full-bundle syntax validation, backup, mode/expected-byte-checked replacement and the existing owned idle reload. Unknown contracts leave the new composition unapplied rather than publishing a partial repair. Existing `storeStatusRepair` applied/current/compatibility-required reports cover this composition; no new mode, settings, identity, history, save kind or persistent status schema is introduced.

A 32 MiB synthetic stalled TLS fixture on actual Node 22.14 observed maximum client socket queue fall from 32 MiB to 64 KiB and temporary ArrayBuffer usage from approximately 128 MiB to 69 MiB. Byte/header/length equality, replay and abort behavior also have executable fixture checks. These are bounded fixture observations, not a universal memory ceiling, network-speed promise, installation proof or completed live backup. The immutable upload copy and caller's manifest remain allocated. Direct S3 byte-body buffering is addressed in source; full native flush/transport completion, writer acceptance, durable state and image-update success still require fresh native evidence. Historical failures outside RPC gzip are not thereby proven resolved, and inference/chat delivery remains separate.

### Coalesced native browser-session backups

[store-chrome-snapshot-inject.py](../../recovery/hooks/store-chrome-snapshot-inject.py) changes only the reviewed `ChromeSessionWatcher` session-change callback inside `DefaultBoxStoreSyncService`, plus a `/* openbot-store-chrome-snapshot-v1 */` helper. A WeakMap keyed by the actual native sync owner keeps one active drain task and one dirty flag. Further browser events while capture is active mark the latest state dirty instead of enqueueing one independent manifest rewrite per event. A trailing capture scans the latest state; changes arriving during that capture may require another pass, so continuous activity is not reduced to exactly two lifetime captures. Separate sync owners do not coalesce or block each other.

Each capture still calls native `snapshotNow({ chromeSessionOnly: true, waitForInFlight: true })`. Between captures the helper yields through `setImmediate`, allowing an already waiting manual full flush to acquire the native cycle under its existing concurrency rules. The manual/full snapshot path, database capture, writer fencing, ETag/CAS, conflict handling and durability/update gates remain native. No full snapshot is replaced by browser-only work, and no fairness deadline or completed flush is fabricated.

Absent/stopped sync owners start no work; an owner becoming stopped prevents trailing replay after its active capture settles. Rejections retain the native `chrome-session snapshot rejected` log and do not create an error-only retry spin; later changes can request capture again. The native service class, callback location, helper/marker counts and complete fixed-point composition must match exactly. Unknown, partial, duplicated or out-of-class callback contracts refuse before publishing a new patch. The callback matcher accepts exactly the reviewed current `error64` form or its consistent lexical `error42` variant used by retained engines; all binding uses must match within the original callback. This is not generalized identifier matching: unreviewed names, mismatched references or mixed callback forms refuse. Both accepted originals produce identical coalescer bytes and remain subject to complete fixed-point checks. Compatibility/source dry runs are not activation, mode-switch, fresh-backup or native-delivery proof; inspect the actually loaded runtime and intended operation separately.

Custom includes this helper after status v2, store RPC gzip and direct-store byte uploads. Official stages those same four neutral transformations together before complete syntax validation, backup, mode/expected-byte checks and the existing owned idle reload. Existing `storeStatusRepair` results describe the combined repair; no new mode, settings, identity, history, save kind or persistent queue schema is added.

Executable synthetic checks cover event storms, latest trailing state, changes during a trailing capture, waiting manual-flush ordering, independent owners, stopped owners and error logging. They do not prove deployment, diagnose a live flush queue as the root cause, establish a backup deadline or verify full backup/durable completion. Successful background cycles can coexist with a timed-out full-flush caller; inspect the native operation and fresh full-flush acceptance separately rather than treating either observation as conclusive.

### Native FIFO flush channel

The bounded backup preparation also handles the native fixed `/run/sand-box-store-flush` directory, addressing startup `EACCES` when the native nonroot host cannot create it. It does not install a replacement runner or overwrite an existing root shutdown hook. The selected native host must first be a regular file owned by the helper UID. An existing real root- or host-owned directory must be readable, writable and searchable by that host UID; retain its owner, mode and contents without rereading the full bundle. Symlink, non-directory, foreign-owned or inaccessible existing paths refuse instead of being chowned or replaced.

Only creation of a missing directory reads the selected host source and requires all four native contract anchors exactly once: the fixed FIFO-directory constant, `BoxStoreHibernateHookService` class, native `deps.fifoDir` fallback and provisioned-plus-hook-exists branch. Missing/unknown/ambiguous contracts return `unsupported-host` without invoking sudo or provisioning. This is a source-checked native pre-provision contract, not authority to create arbitrary runtime directories.

Creation uses bounded `sudo -n /usr/bin/python3` with the fixed `/run` parent and leaf name. It opens parent and child through no-follow directory file descriptors, requires the parent to be root-owned without group/other write permission, and sets only the newly created child to the runtime UID/GID and 0700. Existing root/host-owned directories, including one appearing during the creation race, retain their metadata and contents and must pass a host-user accessibility recheck. Failure cleanup is limited to its own still-matching empty newly created directory; it never sweeps data ownership or removes an existing channel.

`computer-recovery-state.json.backupRepair.nativeFlushChannel` reports `{"state":"created"}`, `{"state":"existing"}` or `{"state":"unsupported-host"}`. A refusal/permission/sudo error is caught as failed backup preparation; inspect it separately from panel/tunnel/native delivery. `created` persists the existing mode-specific pending reload (`pendingOfficialStockReload` in Official, `pendingHostReload` in Custom) so the native service can initialize its FIFO channel at the next owned idle restart. Busy/unknown state remains pending; unchanged `existing` does not request a new restart. The normal mode/process ownership checks still govern restart.

Provisioning changes no inference mode, provider, settings, identity or history. A directory, armed reload or initialized hook is not a completed backup, fresh accepted manifest, full-flush/durability proof or image-update success. Native writer fencing, etag/CAS, conflicts, erase protections and backup/update acceptance remain authoritative; verify those outcomes separately.

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
