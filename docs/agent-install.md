# Copy and paste agent instructions

[Home](../README.md) · [Installation](portable-installation.md) · [Models and tools](models-and-tools.md) · [Troubleshooting](troubleshooting.md)

Paste one complete block into an agent with command access to your own Grok Bot Computer. You supply your own provider account and, if needed, GitHub or Tailscale login. The agent should never need your secret pasted into the conversation.

The [main install prompt](../README.md#paste-this-into-your-agent) uses the detached bot installer and creates a temporary Cloudflare control URL. These alternatives make the choices explicit.

## Install without a public tunnel

This path keeps access on the Computer. It also works through an existing authorized SSH connection. It uses the installer's deferred restart controls when the agent is itself running in a bot turn.

```text
Install OpenBot from https://github.com/nickkostik/openbot-community on my Grok Bot Computer, with no public tunnel and no Tailscale setup.

I authorize installation and its guarded idle host restart. Preserve my existing bots, settings, provider accounts, conversations, rooms, routines, and network configuration. Do not enable any routine or remint any bot.

Read this fork's AGENTS.md, docs/portable-installation.md, and docs/portable-recovery.md first. Run as the Computer's sand-data owner against the actual authenticated running host. Do not install on my laptop or patch the desktop app. Use a fresh owned checkout and this exact source repository; use existing authorized GitHub access if it is still private.

If I already have a public tunnel configured, preserve it and explain that conflict before applying this no-public-tunnel choice. Do not tear it down implicitly.

Run quickstart.sh --check first and inspect loopbackInstallAllowed separately from ready. The quickstart --install path refuses an existing public tunnel before installation; do not bypass that refusal by calling install.sh with OPENBOT_TUNNEL=off. Resolve the intended access first.

When you are outside the host's bot turn, run the checked-out install.sh with OPENBOT_SRC pointing to the checkout and OPENBOT_TUNNEL=off.

When you are running inside the host's bot turn, run the same ordinary installer with OPENBOT_DEFER_HOST_BOUNCE=1 as well. After successful installation, invoke that checked-out install.sh --bot-finalize once to start the owned idle finalizer. Do not wait for your own turn to end while keeping it open. Report the pending restart and continue verification on a fresh turn. Do not use --bot-mode here: it creates a public Cloudflare URL even if OPENBOT_TUNNEL=off is set.

Use the same OPENBOT_SAND_DATA and OPENBOT_HOST_MAIN overrides for installation, finalization, and status. Use Node 22.13+ (22.x) or 24+ on PATH for status/finalization, including the installer's cached runtime if needed. Preserve the install source. Do not set a fake version or force a restart.

Give me http://127.0.0.1:9280 to open in the Computer's browser. If I need access from another device, explain the available authenticated SSH forwarding option; do not expose port 9280 to the internet.

Preserve the current mode and model. On a fresh install, let me add an account and choose a custom model in the panel. If BOX execution is absent, report engine compatibility separately and follow the reviewed recovery planning flow. Do not silently select Temporal or stock Grok as a fallback.

Verify a fresh bot reply, a harmless file-read tool result, and the actual effective provider/model after the idle restart. Report installation, local execution, and intended-client delivery separately. Keep credentials and conversation bodies out of the report.
```

The ordinary installer has no detached bot-install receipt. Its exit status and reconcile output describe that invocation; `--bot-status` describes a detached `--bot-mode` run and can contain an older receipt. Use the current pending marker and actual restarted host to verify finalization.

The command pattern for a bot's no-public-tunnel install is:

```bash
OPENBOT_SRC="$openbot_source" OPENBOT_TUNNEL=off OPENBOT_DEFER_HOST_BOUNCE=1 \
  bash "$openbot_source/install.sh"
bash "$openbot_source/install.sh" --bot-finalize
```

Use the `openbot_source` checkout from [terminal installation](../README.md#install-from-a-terminal). Run the second command only after the first succeeds. Custom data/host environment overrides must match on both commands.

## Add Tailscale and SSH recovery

Paste this after OpenBot is installed. It authorizes normal Tailscale setup on your Computer, not a change to your phone or access-policy bypass.

```text
Add optional Tailscale and SSH recovery to my installed OpenBot Computer.

Read docs/portable-installation.md, docs/portable-recovery.md, and the optional persistent Tailscale section in skills/openbot-config/reference.md from the installed fork.

Use the packaged recovery/hooks/tailscale-recovery.py and recovery-bootstrap.py as the sand-data owner. Inspect existing owned network policy, state, binaries, and login before changing them. Preserve model, mode, bot, routine, and existing network settings.

For a first install, get the current stable version from official Tailscale sources and stage it with the helper's --install-version option and vendor checksum verification. Do not use a remembered version as proof of latest. Write a valid schemaVersion 1 opt-in policy with enabled true and autoUpdate true only when no conflicting existing policy exists.

Use kernel networking only if the Computer has the required TUN and network capabilities. Otherwise explain userspace limits. Never configure an exit node in userspace mode. Do not select an exit node unless I have identified the peer; if I have, verify it is advertised, approved, and reachable before selecting it.

Complete normal Tailscale authentication and any SSH identity check through the printed login URL. Ask me only for the browser interaction you cannot complete. Do not print state files, tokens, or credentials, and do not change the tailnet access policy to get around a denial.

Start or recover the independent network watcher through the owned bootstrap. Verify its fresh heartbeat and make a real SSH connection as the allowed Computer user. A started process or connected status alone is not an SSH test. Keep host-key verification enabled.

Explain how to reach the loopback panel through SSH forwarding, what survives a daemon restart, and what requires the Computer's startup integration after an image replacement. Do not promise recovery through an expired login or unavailable exit node.

Report the selected version, networking mode, SSH result, recovery heartbeat, and any remaining prerequisite. Never copy another installation's Tailscale login or service credentials.
```

Tailscale is independent of BOX/Temporal and Custom/Official. It provides access, not a fix for native platform message refusal.

## Select the first compatible local engine

Use this only when the Computer is missing BOX execution and has no existing engine manifest. It explicitly authorizes local-engine preparation and first selection, which the generic installer leaves separate.

```text
Prepare and select the first reviewed compatible BOX engine for my installed OpenBot Computer. I authorize the guarded local-only recovery opt-in and first engine activation when the exact reviewed checks pass. Do not use Temporal as a fallback.

Read docs/portable-recovery.md and the matching configuration reference. Work as the native data owner on the factory /home/box/sand-data root and actual running host. A custom data root is unsupported for this local-engine path; do not pretend it is supported.

Inspect existing engine policy, local-only policy, bot identity/ownership state, and recovery watchers first. Preserve all existing configuration and journals. If any engine manifest already exists, including a disabled file or symlink, use the reviewed upgrade plan rather than overwriting it.

Run the packaged prepare-local-engine.py against the exact host, installed payload, and native dependencies in owned staging. Require state prepared, complete hashes, and passing native offline contracts. Refuse unreviewed source versions or ambiguous patches. Keep vendor-derived bundles private on the Computer.

Run initialize-local-engine.py --plan with the exact completed preparation directory. Review the validated plan, then use --apply only for an absent initial manifest. Establish the documented owned local-only opt-in and recovery integration without enabling routines or changing inference selection. Let the watcher activate only after locks release and the host is idle.

Verify the new running engine, one actual local bot turn, one harmless tool result, and the delivered reply. Treat native identity/dispatch admission as a separate check. If my current bots are Temporal or unbound, report the identity/ownership plan; do not copy or remint the fleet under this engine-selection instruction.

Keep the prior host/source generation and encrypted recovery data. Report preparation, manifest selection, actual activation, native delivery, and each intended client's result separately. Do not label selected or HTTP 200 as fully working.
```

Reviewed engine preparation can refuse an unknown newer vendor release. That protects the existing installation; it is not permission to bypass the recipe or delete the previous generation.

## Move selected Temporal bots to local

Use this separately from installation. It authorizes a complete-copy migration of the current visible Temporal bots, with guarded activation. If you want only particular bots, replace “my current visible Temporal bots” with their names before pasting.

```text
Move my current visible Temporal bots to verified local BOX copies on my OpenBot Computer. I authorize the reviewed complete-copy migration and activation only when its native ownership and live delivery checks pass. Do not use Temporal as a fallback or change my selected provider.

Read the complete fresh BOX remint workflow in skills/openbot-config/reference.md and docs/portable-recovery.md from the installed fork. Verify the compatible local runner is actually active first. Installing a panel or editing harness metadata is not enough.

Build the read-only plan from current canonical identities and existing migration journals. Exclude hidden duplicates, retired copies, probe bots, and already working local owners. Resolve ambiguous names before writing. Verify an encrypted consistent state backup and a safe restoration plan.

Use the packaged remint-box-fleet.py supported plan/stage/register/publish/verify/activate workflow. Preserve existing migration authority. Keep a one-to-one old-to-new identity journal and complete copied conversations, attachments, instructions, settings, model pins, per-model efforts, routine definitions, category/pin mappings, and explicit room membership. Keep my bot names and labels.

Test one owned candidate first. Require actual BOX admission/ownership, a fresh local provider stream, a harmless file-read tool result, copied history, and a delivered reply in the intended client. If registration returns Temporal or the platform refuses dispatch, stop activation, retain the original identity and evidence, and report the failing stage. Do not fake binding or clear drift alerts.

Activate only verified copies. Retire old scheduler ownership before restoring any captured routine state, and verify exactly one lane owns each routine. Do not enable routines that were disabled, replay missed slots, or send broadcasts. Retain originals and rollback data; do not delete histories to clean up duplicates.

Publish the verified current roster and remap categories, pins, peers, and rooms through the guarded supported flow. Tell me to reopen the current bot from the bot list if an already-open thread still points to an old ID. Check each app separately; do not claim standalone mobile is fixed from desktop evidence.

Report the mapping summary, copy verification, ownership/dispatch checks, effective provider/model, tool/reply evidence, preserved settings, and unresolved clients. Keep IDs and private conversations out of public issue reports.
```

This does not override a remote refusal. If native admission is blocked, preserve the plan and originals rather than creating more replacement generations.

## Update this fork

This prompt retains the selected source and handles the active bot turn safely. Bot mode creates or refreshes a temporary public control URL; use the no-public-tunnel flow above if that is unwanted.

```text
Update my installed OpenBot from its saved source repository and ref. I authorize the detached installer, its temporary Cloudflare control URL, and its guarded idle restart. Do not replace this fork with a different upstream.

Read AGENTS.md and the update/recovery guide from that source. Record the current source commit, active route, mode, bot pins and reasoning, routine policy, engine manifest, and network preferences without exposing secrets. Verify an encrypted recoverable state backup before engine or identity work; a source push alone is not a state backup.

Fetch a fresh owned checkout of the saved repository/ref. Stop on source access failure or corrupt source metadata. Use OPENBOT_SRC and the matching host/data overrides to run install.sh --bot-mode. Do not stamp retained old code as the new release.

Read --bot-status --brief after 30 to 60 seconds, at most twice. Report the installed commit, URL/QR, any concrete failure, and whether the host bounce remains pending. Never force the host or rerun the install just because it is waiting for your own turn to end.

Coordinate the native preserve-data Computer update and the matching compatible local engine using docs/updating.md. Capture one target vendor release and verify both runtime tracks, keeping the desired mode. Reconcile an admitted operation receipt after a timeout instead of starting another migration. Require fresh complete native backup evidence; never force an empty-store update. Do not spend Grok quota on an Official canary unless I authorized it. A new vendor version that removes BOX execution needs the reviewed compatibility flow. Latest version metadata does not prove all Temporal features have been ported.

After the idle restart, verify the actual selected mode and preserved settings. In Custom, verify a new local message, the selected provider/model, and a harmless tool result. In Official, keep stock delivery selected and test a fresh stock reply only if I separately authorized the Grok quota; do not switch to Custom just to run a canary. Check the recovery heartbeat and intended clients separately. Do not enable disabled routines, replay old slots, remint identities, clear roster holds, or silently fall back to Temporal, N5, MetaKey, or another paid route.

Report what actually activated and what is still pending or blocked. Keep the previous source and engine available until fresh turn verification passes.
```

## What the agent should report

| Item | Useful evidence |
|---|---|
| Source | Installed commit and intended repository/ref. |
| Panel | Loopback health and, if enabled, a browser check of the actual public URL. |
| Restart | Pending versus completed, plus the actual running host generation. |
| Local execution | Actual BOX turn acceptance and fresh local stream completion. |
| Routing | Effective account/model for that bot, including intentional pins. |
| Tools | A real file-read result and a reply that uses the returned value. |
| Client delivery | Result for each actual app tested, not a gateway probe presented as a phone test. |
| Recovery | Owned watcher heartbeat, optional real SSH, and a separately verified state backup. |
| Blockers | Exact safe error/status and the layer that refused work. |

An agent can report “panel installed; waiting for provider setup” or “engine selected; activation pending.” Those are useful results. Calling either a fully working bot is not.
