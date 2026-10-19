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

`fleet-state-merge.py.remap_transcript_owner` normalizes only the top-level transcript record's string `agentId` through the journal identity mapping. Message prose, `richText`, nested agent identifiers, tool arguments/results and quoted IDs remain historical payloads, not configuration references. Distinct quoted IDs remain distinct payload variants even when owners normalize to one identity; exact normalized duplicates may collapse, while differing same-entry-ID variants retain deterministic aliases and provenance. Never use global string replacement to make history appear equal.

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

## Reuse existing BOX counterparts in Custom

The existing opted-in Custom dispatch coordinator can reuse a completed current journal's actual verified BOX destinations after an authorized return to Custom. It compares owner-visible server authority first. When that current authority is `bound-box`, already-paired original IDs are excluded from fresh source detection and the coordinator calls internal `Reminter.reselect_active()`. This prevents a visible original left by Official roster selection from creating another copy generation merely to restore the same target. There is no new CLI subcommand or live-merge override. Official remains owner authority: selecting Official does not authorize a Custom re-selection or automatic mode flip.

A nonempty all-active current journal is required. If visibility, preferences and recorded finalization already select the destinations, the method can return `state: "already-selected"` without mutations; that fast return is not a fresh publication/delivery test. Before an actual re-selection, every destination must pass current actual owned BOX binding, exact journal target server ID, native roster, SQLite/profile identity and full ordered local-history publication verification. A forced local profile label, healthy hop, a saved Custom token or an all-Temporal server roster cannot satisfy that authority. This does not migrate a Temporal identity or prove that the current platform will admit a new BOX turn.

Every original source must still be owned, retain exact journal source UUID/legacy UUID/server binding and kind, and match its saved local profile server ID. Source/destination profile roles are compared after lineage normalization, excluding only routing harness/server-ID fields. Before the first scheduler or visibility mutation, a fresh full platform transcript export and current local source history must each be covered by the destination's local history as complete normalized payloads. Matching entry IDs alone do not qualify. Changed instructions/roles, uncopied records or same-ID divergent payloads refuse with an explicit reconciliation requirement, preserving both branches instead of hiding, overwriting or automatically reminting through the conflict. All pairs must pass this preflight before proceeding; the helper lock and preflight are not a global transaction against external writers. These transcript comparisons now call `fleet-state-merge.remap_transcript_owner`, normalizing only top-level `agentId`; quoted UUIDs/rich text and nested tool data remain unchanged and cannot be rewritten to make coverage appear complete. Source role/config normalization stays separate.

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

This command requires Custom/local-only authority and the migration lock. It validates and pins `historyMergeReceipt: {path,sha256}` without changing original conflict counts. A different receipt cannot replace a pin. Subsequent registration/verification revalidates pinned evidence rather than trusting an accepted flag. A successful result reports baseline/source counts, pinned digest and `pruneAllowed: false`; it does not claim native app or billing verification. The command accepts reviewed evidence; it does not construct a context merge or run native tools.

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
