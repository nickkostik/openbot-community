# Restore existing Local bots without losing their live branches

This guide is for an explicitly authorized operator repairing existing BOX identities. It covers the `openbot-history-merge-receipt/live-restoration-v1` primitive. It is not a one-command migration, a normal installation step or a claim that the panel can automatically repair every history conflict.

Use this when an existing Local target already has published history, an advanced native SDK context or both, and the older hidden preactivation tail/identifier contracts cannot describe the actual state. Keep the target identity. Do not create another generation to hide the conflict, reset the journal, shorten the real published capture or replace current context with an old checkpoint.

For ordinary setup, use [the installation guide](../README.md). For supported panel transfers, use the existing [Local transfer contract](../skills/openbot-config/reference.md#panel-local-transfer-job). Exact fields, bounds and acceptance rules for this repair are in the [configuration reference](../skills/openbot-config/reference.md#explicit-same-id-live-restoration).

## What must survive

Preserve all original and current message occurrences, payload variants and source ordering. Only top-level ownership normalization and explicitly proved synthetic-ID projections are allowed; quoted IDs, message prose, nested tool arguments/results and historical instructions remain unchanged.

Preserve the current selected SDK context prefix and root fields, every current non-root blob and every complete original branch graph. Preserve the complete current store schema and all nonhistory table/row values, including legacy `blobs`, `automation_completion_inbox` and KV state. A transcript/SDK-only reconstruction cannot stand in for that store; only explicitly permitted transcript/selected-root metadata changes may differ. A matching fixed root slot does not prove equal context. Keep original stores, immutable receipts and raw API captures, including nontext events, in private recoverable archives.

Preserve the byte-exact authored target tree: profile/settings, instructions, tools, routine definitions and history, session files and nested session databases. The per-target proof does not cover shared global sidebar sections, pins or the main selection; capture those separately and use expected-byte checks plus readback for any authorized remapping. Do not merge routine switches by OR-ing them or infer intended enablement from a paused candidate or historical run.

These are bounded evidence contracts. They do not prove that all opaque historical session state has been semantically merged, that future cloud admission is fenced or that a particular mobile/voice client can deliver.

## Prepare privately before any live write

1. Establish one repair writer under literal Custom with explicit Local opt-in. Keep the inherited owned exclusive Custom-dispatch lock and the controller's migration-lock discipline. Require actual native idle and exact existing owned BOX UUID/server bindings. Pause the target routines while preserving their separately captured intended flags; unknown or mixed intent needs review.
2. Capture the full current target publication from sequence one through its actual head, retaining generation and raw base64 bodies. Capture complete current native UI/SDK snapshots coherently, fresh original source captures, all old accepted receipt dependencies and the complete authored target inventory. Keep both divergent branches. An old hash cannot recreate an absent evidence file.
3. Build a new immutable receipt candidate under the current owned journal backup. A pinned entry names its exact existing accepted parent; a proven existing target absent from the current journal uses the dedicated adoption path with `previousReceipt:null`. Null never bypasses a lost accepted parent.
4. Build the candidate UI as the entire actual published prefix followed only by genuinely captured original-source or current-native SQLite-only suffixes. Prove native-only records against the real current-store snapshot and raw-row hashes; do not manufacture a platform capture or self-lineage source for them. Retain every current native and old-baseline occurrence in relative order. If an oversized legacy synthetic alias already has its canonical bounded counterpart published, record the exact injective local/evidence ID projection. Do not rename platform entries or fake an unpublished prefix.
5. Make independent private detached candidate databases. Normalize only those copies to non-WAL SQLite mode, close them and reject sidecars, hard links and unsafe paths. Preserve exact raw input evidence and hashes. Never normalize a live database or an already pinned immutable snapshot.

This source change supplies the detached SDK bridge, validator and guarded journal admission APIs. The operator still owns the separately reviewed live database apply transaction and its durable before/after guards. Do not treat this guide as a generic database replacement script.

## Prove the detached SDK candidate

Run from the reviewed source checkout with the actual compatible Node runtime and exact reviewed native source files:

```bash
node recovery/hooks/same-id-sdk-restoration.mjs \
  --candidate-dir /absolute/private/openbot-history-tail-restoration \
  --host /absolute/private/reviewed-host.cjs \
  --worker /absolute/private/reviewed-agent-store-worker.cjs \
  --allocator-host /absolute/private/reviewed-current-host.cjs \
  --plan-file /absolute/owned/journal-backup/restoration/plan.json
```

These paths are placeholders. The private candidate directory must satisfy the existing detached marker, ownership, single-link database and `openbot-history-tail-*` contracts. The exact plan binds the current snapshot/root, complete published capture, original source graphs, selected route/capacity, parent receipt, literal import records, provenance and identifier changes. See the reference for its schema. Unreviewed native bytes or unsupported semantics refuse; changing the allowed hash to force success is not a repair.

The bridge modifies only the detached candidate and writes `same-id-sdk-restoration-proof.json` there. It does not contact a provider, execute historical actions, apply a live database, accept a receipt, create/register a bot or send a message.

For an existing selected root, it preserves the current prompt prefix and all other root fields, archives all old/current source graphs and appends only independently proved missing supported literal context. Pending tools, uncertain duplicates or unsupported imports refuse. A nonempty root with no additions stays byte-exact; an empty fake delta is not evidence of progress.

For a genuinely empty selected graph, bootstrap requires the entire candidate UI union in order, including any proved current-native-only tail, with at most 200000 records, 4 MiB total literal text, an 8 MiB plan and the actual selected route's capacity/output reserve. Each imported occurrence must match an actual captured API body or current-store raw-row hash. Nonempty-context additions retain the stricter 128-record bound. Historical nontext events are represented only as explicitly inert literal JSON background, with complete original raw records and SDK graphs retained separately. They are not executable restored tools, pending actions or session instructions. Nothing is replayed.

Proof uses actual reviewed native parsing, binary/root roundtrip, full context derivation, reachable closure, next-ID allocation, pagination and complete store schema/nonhistory-state checks. Its preview/unread preservation flags cover the detached SDK step after the candidate UI union has been built. Restoring missing published history may legitimately update the native preview; those flags do not mean the old live preview must stay unchanged. It binds exact input and output hashes and records zero provider/network/tool calls with `liveApplied:false`. A successful proof is a detached candidate result, not a live recovery or delivery result.

## Apply and admit through the canonical controller

Keep the writer lease valid and recheck source heads, route, native idle, owned bindings, authored files and expected live UI/SDK state before live work. Persist a reviewed durable apply intent before the first mutation. Apply only the planned UI changes and SDK additions/root selection under appropriate SQLite transactions; do not replace active database files wholesale or overwrite an advanced root from an old snapshot.

Resume only when observed state is exactly the captured before state or planned after state for that step. A third state means review. Preserve known partial progress, source branches and original evidence; do not reset hashes, receipt phases or acknowledgements to manufacture completion.

After the actual target contains the candidate, use the narrow Python API on the existing canonical `Reminter` instance. The callable signatures are:

- `Reminter.accept_history_merge(aid, receipt, restoration_lock_fd=fd)` for an existing `registered-box` or `active` entry.
- `Reminter.adopt_restored_existing(template, receipt, restoration_lock_fd=fd)` for a proven already copied/registered BOX target missing from the current journal.

Construct the controller only through the reviewed installation's existing loader. The explicit CLI wrappers accept `--dispatch-lock-fd <inherited-fd>` on `accept-history-merge --agent-id <journaled-id> --receipt <owned-receipt-path>` or `adopt-restored-existing --template <owned-template-path> --receipt <owned-receipt-path>`. The caller must actually pass its already-held owned exclusive dispatch-lock descriptor into that process; the flag does not acquire a lease, and an arbitrary integer is not authority. Template JSON must be owned private evidence and contain strict existing identities/server bindings and recorded lineage, not an invented phase. It must not overlap a current journal pair. There is no new web endpoint.

Both paths require actual native idle, current Custom/Local authority, the inherited owned exclusive dispatch lock, exact actual owned BOX binding and all target routines paused. They compare the installed entire SDK graph/metadata, complete store schema and nonhistory table/KV values using a committed-WAL-aware live reader, and recursive authored target tree to the immutable proof. Immutable snapshot reads alone cannot establish live-store equality. Canonical retention archives the complete referenced evidence closure before the journal pin. Adoption records an already proved registered copy; it never calls Create, stages over a populated target or changes visibility/settings/sections. The acceptance paths do not install the live union for you.

## Publish, then prove delivery

Request a fresh canonical publication reload for the installed SDK. Wait for the exact new request's real native-replacement acknowledgement. Never copy an old ACK, restamp an old request/hash or count detached proof as a loaded SDK. Verify complete ordered publication and fresh source/roster authority, then restore only explicitly proved desired routine states and section/pin preferences through their separate guarded workflows.

Send a new test through the intended Grok client. Correlate actual BOX admission, the selected OpenBot route, tool completion where required and the reply delivered back to that client. A hop 200, panel health, BOX roster label, accepted receipt, reload ACK or visible history alone does not satisfy this test. If a client fails, report that remaining failure without claiming the transfer fixed it.

Keep every original branch and private recovery artifact until separately authorized cleanup has its own complete proof. This primitive grants no pruning, mode changes, stock fallback, routine replay, billing guarantee or universal mobile/voice compatibility. It does not make the current panel Start/Resume flow a general same-ID restoration operator.

## Keep the public repository clean

Publish source, synthetic tests and generic instructions only. Actual bot identities/names, sections, conversations, instructions, snapshots, session/SDK roots, native vendor bundles, provider keys, OAuth files, private paths and evidence archives stay outside public Git. Recursive evidence retention is local recovery protection; verify an encrypted durable off-box copy separately before image or destructive work.
