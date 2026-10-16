# Local engine and recovery

[Home](../README.md) · [Agent instructions](../docs/agent-install.md) · [Installation](../docs/portable-installation.md) · [Troubleshooting](../docs/troubleshooting.md)

OpenBot runs on the user's own Grok Bot Computer. The authenticated gateway, native host, bot identities and MCP/ACP credentials belong to that installation. No vendor host bundle, account credential, Tailscale state or fleet history is included in this project.

## Compatibility boundary

Recent vendor hosts can remove native BOX execution. A feature flag or UI model selection cannot recreate that runner. Local recovery requires a compatible BOX baseline or an explicitly ported, tested engine generation. The retained engine uses exact hashes, owned files and an explicit policy; ambiguous anchors or removed execution are refused. The release follower stages/ports known compatible changes, checks them and waits for idle activation. It does not claim every new vendor release is automatically compatible.

Do not turn a server Temporal row into a working local bot merely by editing a profile. Server ownership, dispatch, copied history and transcript publication require separate verification. Keep IDs and room/category bindings intact. Never remint a fleet as part of a generic installer or replay missed routine slots automatically.

## Preparing a reviewed engine pair

From the complete installed fork's hook set, `prepare-local-engine.py` can construct an owned staging pair using verified public vendor baseline `861ef07` / `0.67.0-pre.9` and a host with an exact reviewed BOX compatibility recipe. Run it on the Computer after installing this fork, Node 22.13+ (22.x) or 24+, the required parser packages and native host dependencies:

```bash
python3 /home/box/sand-data/openbot/recovery/hooks/prepare-local-engine.py \
  --package-root /home/box/sand-data/openbot \
  --host-main /opt/sand/sand-host/host-main.cjs
```

Pass the actual selected native host path; this helper defaults to the `/opt/sand` path rather than discovering the legacy host. `--node` defaults to `/exec-daemon/node`. Stage defaults to `/home/box/sand-data/openbot-engine-preparation`; `--stage-root /tmp/owned-name` selects another caller-owned nonsymlinked tree under an owned parent or trusted shared `/tmp`. Optional `--upstream-version` selects an explicit reviewed vendor version; otherwise the helper reads latest and refuses an unreviewed source.

The helper applies canonical recovery transforms before BOX linking, wraps/stamps the latest and compatibility baseline pairs with the same installed payload, and runs native offline checks on the final source. A successful result has `state: "prepared"` and names `result.directory`, containing the complete `preparation.json` and four read-only 0400 source artifacts: `stock.cjs`, `wrapped.cjs`, `baseline.cjs` and `baseline-wrapped.cjs`. The report includes `baselineWrappedSha256`. Keep vendor-derived source local and private. Source drift, unsafe staging, changed payload, wrapping failure or failed/incomplete contracts refuse success. [Command, ownership rules and report fields](../skills/openbot-config/reference.md#prepare-a-reviewed-box-engine-from-public-vendor-source).

`activated`, `liveHostChanged` and `nativeDeliveryVerified` remain false during preparation. It does not select the pin, reload the host, remint identities, enable routines or switch models/mode; it does not substitute Temporal. First selection below and loading through the owned recovery integration remain separate. Offline hashes/contracts do not prove a fresh complete deployment, routine delivery or standalone mobile admission.

## Selecting the first local engine

With no existing engine policy, `initialize-local-engine.py` reconstructs and validates a completed preparation against the current installed payload/native dependencies before selecting it. An omitted action plans; `--plan` and `--apply` are mutually exclusive. Use the installed source and replace the placeholder with the exact absolute preparation `directory`:

```bash
openbot_prepared_directory="/absolute/path/from-completed-preparation-directory"
python3 /home/box/sand-data/openbot/recovery/hooks/initialize-local-engine.py --plan \
  --prepared-directory "$openbot_prepared_directory" \
  --root /home/box/sand-data \
  --package-root /home/box/sand-data/openbot \
  --host-main /opt/sand/sand-host/host-main.cjs \
  --node /exec-daemon/node
```

After reviewing that validated plan:

```bash
python3 /home/box/sand-data/openbot/recovery/hooks/initialize-local-engine.py --apply \
  --prepared-directory "$openbot_prepared_directory" \
  --root /home/box/sand-data \
  --package-root /home/box/sand-data/openbot \
  --host-main /opt/sand/sand-host/host-main.cjs \
  --node /exec-daemon/node
```

Every existing `openbot-local-engine.json` path, including a disabled file or dangling symlink, is refused; preserve it and use the reviewed upgrade flow instead. Planning may stage a fresh reconstruction but publishes no policy. Apply exclusively creates the initial complete private manifest and immutable latest/baseline generations, with exact compatibility-base hashes for later reviewed ports. `state: "selected"` / `activated: true` describes manifest selection; `hostReloadRequested` and `nativeDeliveryVerified` remain false. [Validation, ownership and result fields](../skills/openbot-config/reference.md#select-the-first-local-engine-manifest).

The command does not start the watcher, enable local-only policy, change bot IDs/models/mode/routine settings or directly reload the host. A preexisting opted-in owned recovery watcher can load the selected engine after the command releases its lock and the host passes idle checks; apply therefore does not guarantee an unchanged live host. Complete the separate integration, native identity/turn/tool and intended-client checks below. Initial selection/offline proof does not establish full fresh deployment or repair standalone mobile admission.

## Owned recovery integration

The optional Computer integration uses `recovery/hooks/computer-recovery.py` and `recovery-bootstrap.py`. It reads the installation's explicit local-only policy and owned configuration files. It can restart owned services, validate/stage patches and defer a host reload; it must not take over a foreign port or delete bot state. The Tailscale helper follows its own opt-in policy and requires normal login and SSH authorization.

Invoke the bootstrap from the installed source as the sand-data owner:

```bash
python3 /home/box/sand-data/openbot/recovery/hooks/recovery-bootstrap.py \
  --sand-data /home/box/sand-data \
  --host-main /opt/sand/sand-host/host-main.cjs
```

Pass the actual native host path. Omitted `--host-main` / `--host` uses `OPENBOT_HOST_MAIN`, then an unambiguous same-owner native Node process, then an existing known `/opt/sand` or legacy `/home/box` host. Invalid paths, syntax probes and ambiguous host selection cannot redirect recovery. Helpers come from the running package, then `sand-data/openbot/recovery/hooks/`; separately copied `openbot-hooks/` is a legacy fallback. Package/file ownership and nonsymlink checks apply before execution.

Local-engine recovery still requires the factory `/home/box/sand-data` root because its native injectors embed that path; another `--sand-data` reports `unsupported-path` rather than repairing a different installation. Opted-in network recovery remains independent and can use another root. A held lifetime lock alone reports `starting`; `running` requires a matching owned PID and helper/host command. A recognized wrong-host watcher reports `conflict` without being killed or duplicated. Legacy default `/opt/sand` watchers keep their lock/PID. A `started` result is not a delivered-turn or SSH test.

The external recovery relay/connector is optional and needs the owner's own deployment and credentials. Installing this repository does not install another user's PVE service, Cloudflare tunnel or exit node.

Panel recovery uses `install --keep-service`. With an explicitly saved Official mode, this recovers the owned control service without editing the native host, logging preferences, catalog, credentials or mode. A new upstream host whose optional logging factory is unsupported can therefore still serve the controls. An existing owned listener is retained; a foreign listener or changed selection is refused. This does not prove logging instrumentation or bot delivery: normal logging installs/saves still require a compatible checked host wrap. Custom recovery keeps its existing wrap checks.

### Independent Tailscale recovery

With an enabled `openbot-tailscale.json` and installed, checksum-verified Tailscale binaries/state, the owned bootstrap starts a separate network watcher on the Computer. It retries every 15 seconds regardless of BOX/Temporal or Official/Custom mode. A failed first network probe does not prevent that watcher from starting. Network opt-out stops its retry loop; it does not erase the login or stop an existing daemon.

The OpenBot panel also starts or recovers this independent watcher at startup and during its 15-second recovery checks. It uses the helper from the installed fork's `recovery/hooks/`, with the separately deployed `openbot-hooks/` helper as a fallback; copying hooks out of the installed source is not required for network recovery. Its network startup attempt has a separate 60-second backoff and writes child output to `openbot-network-recovery-watch.log`. The Python lifetime lock, fresh heartbeat and real SSH remain the proof of recovery. A network startup failure does not prevent opted-in local-engine recovery. The panel must be running; a stopped panel or replaced pod still needs the owner's startup integration below. `OPENBOT_COMPUTER_RECOVERY=0` opts out of the panel's recovery checks.

The same loop can run directly from this fork's installed source:

```bash
python3 /home/box/sand-data/openbot/recovery/hooks/tailscale-recovery.py \
  --sand-data /home/box/sand-data --watch
```

It holds `tailscale/recovery-watch.lock`, publishes its PID and a private `tailscale/recovery-watch-state.json` heartbeat, and retries transient failures with only an error class in logs. A bootstrap `started` result means a candidate was spawned; verify the lifetime lock, fresh heartbeat and real SSH before calling it healthy. Existing login, saved exit node, LAN access and host-transport exceptions are retained. It does not reset preferences, acquire an identity or interrupt a live daemon to load a staged version.

This loop recovers a daemon inside a running Computer. A pod/image replacement kills its processes too, so the owner's startup/transport integration must invoke the bootstrap again. It cannot guarantee recovery through an expired login, denied SSH policy or an unavailable exit node. Verify real SSH after an owned daemon restart and compare protected bot/model/routine settings.

## Verification required for each installation

Official recovery retains its last confirmed stock conversation selection while
that counterpart remains eligible. A later migration of the preserved BOX copy
does not itself request a different Official identity. The owned
`openbot-official-dispatch-state.json` receipt must report
`phase: "stock-roster-selected"` and `mode: "official"`; both legacy sparse
`new -> old` mappings and current complete mappings are supported. Every selected
counterpart is still checked against the journal, platform ownership, kind,
harness and native server binding. Missing or stale receipts retain the normal
first-handoff policy; malformed confirmed mappings refuse before settings writes.
An ineligible remembered counterpart uses the existing verified stock fallback.
Selection stability is not a transcript/SDK merge or permission to prune copies.

Transcript identity normalization changes only the record's top-level owner
`agentId`. Quoted IDs in messages, rich text and historical tool payloads remain
part of the original evidence. Different message bodies remain distinct branches;
unclassified or partial journal lines are retained. This prevents new copy-time
rewrites and does not repair an already rewritten history or merge SDK contexts.

Verify the actual context as well as its metadata. A native `latestRootBlobId`
may identify a fixed slot shared by different copies; compare the real root in
`conversation-blobs.db`, its referenced blobs, and turn/message coverage. Matching
pointer values or transcript counts alone do not prove complete context. Retain
divergent roots until their recovery and the intended client's delivery are proven.

- Open the panel and check its active model against the next real bot turn.
- Confirm the turn attaches locally and completes its stream; HTTP 200 alone is insufficient.
- Verify a tool call returns its result, including a live voice call before hangup.
- Check local and platform routine ownership, receipts and due-slot fairness before enabling schedules; avoid duplicate dispatch.
- If using Tailscale, test real SSH and daemon restart recovery with the intended routing and access policy.
- Test each client separately. Standalone Grok Bot iOS/Android admission remains unresolved for this fork's BOX path; desktop, integrated Grok and direct gateway evidence does not prove mobile delivery.

Provider subscription credentials are adapter-specific. OpenAI OAuth uses the authenticated Responses adapter; an API key uses its configured origin/protocol. OpenCode Go accounts use the direct Go endpoint in independent families. Existing per-bot model/reasoning choices, compaction/cache policy and MCP/ACP configuration must survive updates.

See [portable installation](../docs/portable-installation.md), the configuration skill and reference for concrete disk schemas and supervisor commands. Keep the prior engine/source generation and an encrypted backup until a fresh turn and tool result verify the new generation.
