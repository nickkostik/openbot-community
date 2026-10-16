# Update OpenBot and both runtime paths

OpenBot source, the native Computer image and the compatible local engine have separate versions and update receipts. Updating one does not prove the others changed or that a message was delivered. This guide applies to the authenticated Grok Bot Computer, not a desktop client modification.

| Path | What it updates | Required evidence |
|---|---|---|
| OpenBot source installer | Supervisor, unified panel/hop, payload and recovery helpers from your repository | Activated source commit, owned service health and any required controlled payload reload. |
| Native preserve-data Computer update | Vendor-managed Computer image and stock native host | Fresh completed backup, admitted operation reaching terminal success, running image/version and restored data. |
| Compatible local-engine preparation | Complete local stock/wrapped pair for that exact captured vendor release | Archive/source hashes, reviewed interfaces, offline contracts and immutable artifacts. |
| Custom activation | Selected local generation loaded through owned recovery | Actual unpredictable local file-tool result and exact delivered reply, with configuration comparison and rollback authority. |
| Official delivery | Vendor stock server/Temporal dispatch using Grok quota | An explicitly authorized native client/API send, server acceptance and the exact delivered reply. |

Official uses the stock native host and vendor-managed server/Temporal service. OpenBot does not deploy or upgrade that cloud service. Custom uses the owned local BOX/OpenBot runtime and the saved provider route; it has no stock/Temporal fallback. Keep the current desired mode, catalog, credentials, global model, per-bot model/effort choices, engine pin, identities, histories, routines, MCP/ACP settings and network policy unless the user explicitly requests a change.

## Choose the authorized scope

A source update can proceed without a native image update or cloud test. Preparing a local pair does not authorize selecting it, changing mode, reminting identities or sending a turn. A Custom canary uses the configured local provider and may consume that provider's quota. An Official canary uses Grok/server quota and requires authorization for that path. A request limited to local work does not authorize a cloud quota test.

If an active Custom pin holds native image updates, plan an authorized temporary Official transition through reconcile and restore the recorded desired mode afterward. Do not clear the pin, reset configuration or bypass a native update guard to avoid that transition. An unreviewed latest vendor release may remain `compatibility-required`; report the pending local path honestly.

## Update the product source

Use your repository and reviewed ref. Public exports replace `nickkostik/openbot-community` with their own source identity:

```bash
gh repo clone nickkostik/openbot-community /tmp/openbot-source
OPENBOT_SRC=/tmp/openbot-source bash /tmp/openbot-source/install.sh --bot-mode
```

For an existing reviewed checkout, use its actual path and commit instead of cloning again. Follow the installer's printed `OPENBOT_BOT_INSTRUCTION` and receipt; poll status at most twice during a Bot turn. Direct installation omits `--bot-mode`. Preserve the saved tunnel setting during an update; `OPENBOT_TUNNEL=off` is an explicit choice for a local-only first installation.

The installer stages source separately, validates syntax/dependencies and retains the previous release before activation. A retained old install after failed downloads is not an upgrade. Source rollback, an open port or a pending restart does not establish delivered chat. Verify the published owned service PID and `/api/state`. Use the supervisor's existing deferred/idle reload for changed code; ordinary catalog/credential edits are read on the next request and do not need a host bounce.

If the saved mode is explicitly Official and an unmarked vendor host no longer supports the optional logging hook, the installer updates the owned controls process while preserving the host, mode, logging preference, catalog and credentials. It reports `officialLogging: "unavailable-host-hook"` in CLI JSON and the detached receipt, with a warning in full/brief status. This exception is explicitly requested by the installer through `--official-controls-fallback`, including its tunnel setup. It never makes an unsupported Custom engine pass or proves stock delivery/log capture; ordinary CLI installs and UI saves remain strict. Use `--keep-service` only for recovery that should retain a healthy listener, not to load a new release.

## Capture one vendor release for both paths

Inspect the actual stock native version and the selected local manifest. The checked follower captures an authenticated vendor release, verifies archive/source hashes and prepares a compatible local pair for that same upstream release. A version label alone is not sufficient: the selected manifest, captured vendor source and prepared pair need matching provenance.

Fresh installations can use the reviewed `prepare-local-engine.py` and first-binding `initialize-local-engine.py` flow described in the [configuration reference](../skills/openbot-config/reference.md#prepare-a-reviewed-box-engine-from-public-vendor-source). An existing pin uses the checked upgrade/follower path. Do not delete an existing policy to force first binding. Keep vendor bundles and generated engine artifacts private, outside the repository export.

In Official, preparation saves an unselected pair: `local-release-ready`, `localEngineSaved: true`, `nativeDeliveryVerified: false`, `activationDeferred: true`. It performs no local canary, changes no selected engine authority and does not pin the stock host. Offline readiness is separate from loading a host and from actual delivery. Removed runners or new interfaces need an exact reviewed source recipe and executable contracts; do not port by version guess, edit hashes or declare full parity.

Evidence belongs to the exact captured source and engine generation. A newly captured release starts fresh proof/report state: it does not inherit an older generation, canary, readiness, error or rollback result; only historical `lastAppliedAtMs` is retained. Previous delivery proof cannot certify a new source, and passed native offline contracts do not establish live activation or delivery. A missing approval-roster API must keep local health busy; do not infer idle or bypass the activation hold.

Reviewed credential interface ports must preserve the latest native credential lifecycle and authorization, refuse missing or stale credential references and never mirror secret values. Interface restoration still requires its own executable contracts and live local delivery proof.

Exact-reviewed local approval ports must retain the complete extension and matching controller, preserve owner/team policies and enforce enabled review settings. Respect intentionally disabled settings; validate the lifecycle without switching configured approvals off. Include the matching exact-reviewed team-admin policy dependency, preserving real admin enforcement responses, cloud-agent restrictions and policy wait, cache invalidation and disposal.

## Complete backup and the native image update

Before an image mutation, retain a verified recoverable backup of current data, configuration, selected engine/baseline pair and network identity. Retain durable off-box copies with owned manifests/pointers. Keep already verified external export archives outside synchronized home so the native updater does not copy the backup into another backup; never move the sole copy or exclude live agent data. Temporary storage is not durable recovery.

`/prepare-upgrade` is a host-bundle reload checkpoint with `owner: "upgrade"`; native `pauseTurnsForRecreate` intentionally refuses that owner. Preserve-data `RecreateSandBox` preparation instead uses authenticated `/api/prepareBoxForRecreate` and requires native `owner: "recreate"`, `quiescent: true` before a fresh snapshot and admission. Resolve leftover host checkpoints through owned native supervisor controls when safe to reload, preserving configuration; never clear native fences or mask `busy`. Reconcile an uncertain admitted operation instead of replaying it. Require a successful terminal image result and separate post-image delivery proof.

Require a genuine fresh completed native flush and a hydrated/durable receipt consistent with current data and ownership. An older canonical `durable: true` receipt does not prove a new flush completed. The neutral status optimization remembers acknowledged summaries; request gzip reduces large SandBoxStore RPC transport cost. A native direct S3 manifest write is a separate path, so gzip alone does not establish backup success. Preserve native writer fencing, CAS/etag, conflict, commit and durability checks.

On native hosts with the reviewed FIFO startup contract, recovery also prepares a missing `/run/sand-box-store-flush` directory for the host user. It uses a fixed root path, directory descriptors and no-symlink checks; existing accessible root/host-owned directories and the installed hook remain intact. Foreign, inaccessible or unsafe paths are refused. Creation arms the existing persisted reload, which waits for idle in either mode. This repairs a startup permission error; it does not establish a completed flush or authorize an image rebuild.

The owned recovery fallback hibernate hook allows 180 seconds for its authenticated native `snapshotBoxStoreNow` request and succeeds only on a fresh parsed `ok: true` result. Recovery upgrades only the byte-exact previous fallback under root-owned regular-file and hash checks, saves the original and atomically replaces it through sudo; other native or foreign hooks stay intact. Hook repair, a longer timeout or cached status does not prove backup or image-update completion and changes no mode, erase or fencing rule.

Use the vendor's authenticated preserve-data image-update controls with `force: false` and `ackTerminal: false`. Record the admitted operation ID and reconcile its existing state through terminal completion. A request timeout, disconnect or client 503 can leave the native operation running. Inspect the operation and on-box receipt before any repeat mutation; never replay solely because the client lost its connection. Do not force erase, accept an empty store, enable an optional commit path to escape fencing, or change ownership to bypass refusal.

After terminal success, verify the running image/native version, update availability, restored data inventory/ownership and recovery health. Recheck the recorded configuration fingerprints. A completed backup is not image completion; a terminal image result is not message-delivery proof. Generic installation does not provision your external bootstrap, tunnels or network credentials: retain separately authorized recovery for Computer replacement and verify reachability independently.

## Activate and verify separately

When Custom activation is authorized, recheck desired mode and manifest/configuration authority, then use the checked local activation and owned idle/checkpoint reload. Observe a real native turn reading an unpredictable local fixture and delivering its exact expected reply. Offline contracts, provider-only HTTP success, host loading and `activated: true` alone are insufficient. Compare settings and roll back only attempt-owned engine authority on failure, preserving user data and the prior compatible generation. Never route a failed Custom canary to stock/Temporal.

When Official verification is authorized, restore verified stock vendor bytes through reconcile/recovery and observe an actual native server/Temporal admission and exact reply. A `MOBILE` API source proves that sampled API path, not physical phone sending, display, microphone or audio playback. Check those clients separately if requested. Finish in the recorded desired mode and report source, image, staged engine, selected engine, actual delivery and remaining compatibility gaps individually.

## Copy/paste instruction for an agent

Fill in the authorization choices before giving this instruction to an agent. Leave cloud verification disabled for a local-only request.

```text
Coordinate my OpenBot update on my authenticated Computer using
nickkostik/openbot-community and my reviewed source ref.

Authorized work:
- Source install/update: YES or NO
- Native preserve-data Computer image update: YES or NO
- Compatible local pair preparation: YES or NO
- Local activation and real file-tool/exact-reply canary: YES or NO
- Temporary Official/Custom transitions required for those steps: YES or NO
- Official server/Temporal send using Grok quota: NO unless I explicitly say YES
- Final desired mode: preserve the recorded mode unless I specify another

Inspect current versions, owned processes and existing operation/on-box
receipts first. Record private configuration fingerprints and retain verified
recoverable/off-box backups. Preserve catalog, accounts, pins, models/efforts,
identities, histories, routines, MCP/ACP settings and network policy.

Update source through the reviewed installer. Capture one vendor release and
prepare the local pair for that exact source with hashes and offline contracts.
Keep preparation unselected in Official. Refuse unreviewed interfaces and
report compatibility-required; do not use blind ports or cloud fallback.

For an authorized native image update, require fresh native backup completion,
use preserve-data with force:false and ackTerminal:false, and observe the
admitted operation to terminal success. Reconcile receipts after timeout/503;
do not repeat a mutation because its client failed. Never bypass ownership,
writer fences, CAS/conflict/durability checks, or force/empty-store protections.

Run only the delivery tests authorized above. Local-only authorization does
not authorize a Grok/Temporal quota test. Verify local activation with a real
unpredictable file tool and exact delivered reply; retain rollback authority.
Restore the final desired mode and compare preserved settings/data. Report
source, native image, prepared/selected local generation and each actual
verified delivery path separately. Do not claim full parity or physical-phone
verification from offline checks or API-only evidence. Keep credentials,
artifacts and proof records private.
```

See the [configuration skill](../skills/openbot-config/SKILL.md), [reference](../skills/openbot-config/reference.md), [portable installation](portable-installation.md) and [portable recovery](portable-recovery.md).
