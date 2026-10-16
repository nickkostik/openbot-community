# Portable Computer installation

[Home](../README.md) · [Paste-ready agent instructions](agent-install.md) · [Models and tools](models-and-tools.md) · [Troubleshooting](troubleshooting.md)

Install on your own Grok Bot **Computer**, with its authenticated host already running. Public repositories support an ordinary Git clone without a GitHub login. For a private fork, use an authorized GitHub login or copy a source checkout to the Computer. An anonymous `curl` of a private repository cannot install it.

## Before running the installer

Work as the normal Computer user who owns sand-data. Keep an encrypted consistent state backup before changing an existing engine or identity set. Check that Bash, Python 3, `flock`, and a source download/copy are available. The installer can cache Node 22.13+ (22.x) or 24+ locally; it does not replace system Node. Use a terminal with command execution, not a desktop-app patch.

Read [client compatibility](../README.md#which-clients-work) first. If your only client is the unchanged standalone Grok Bot mobile app, this fork cannot currently promise local delivery there. A host without BOX execution needs the separate reviewed engine flow below.

## Install on the Computer

The installer, CLI and panel recognize both `/opt/sand/sand-host/host-main.cjs` and the legacy `/home/box/sand-host/host-main.cjs`. They prefer the actual running native host. If both are running, specify `OPENBOT_HOST_MAIN` (or the CLI's `--host-main`) rather than selecting an unused copy. `OPENBOT_SAND_DATA` and `--sand-data` select the data directory. With the default data directory, the stock backup lives beside the selected host as `host-main.cjs.pre-openbot`; a custom data directory retains its backup there. The CLI and panel resolve the host at startup.

Use a fresh owned checkout. For a private source:

```bash
openbot_source="$(mktemp -d /tmp/openbot-source.XXXXXX)"
gh repo clone nickkostik/openbot-community "$openbot_source"
bash "$openbot_source/quickstart.sh" --check
bash "$openbot_source/quickstart.sh" --install
```

For a public source, use `git clone https://github.com/nickkostik/openbot-community.git "$openbot_source"` for the clone step. Stop on a clone failure; do not install whatever happens to be left in an old directory.

Open `http://127.0.0.1:9280` in the Computer's browser. The installer preserves the selected mode, providers, keys, bot settings and routine definitions. On a fresh install it starts the panel in Official mode; add a provider and select a custom model to route through OpenBot. It does not remint bots or enable a routine.

On an existing Official installation, a newer vendor host may no longer expose the optional request-logging hook. The installer can still update the control panel and explicitly reports `officialLogging: "unavailable-host-hook"`; detached status includes the same warning. Stock delivery and your saved logging preference remain unchanged. This reports a controls update, not working traffic recording or a completed chat test. Custom still requires its checked local engine and wrapper.

`bash quickstart.sh --check` is read-only. It reports `savedExposure` and `loopbackInstallAllowed` alongside Computer prerequisites. A ready Computer can still have `loopbackInstallAllowed: false` because public access is already configured. `quickstart.sh --install` refuses that conflict before invoking the installer, so it cannot silently disable an existing tunnel. Preserve the policy and resolve which access the owner wants; `--public-tunnel` explicitly selects the detached public installer. Unsafe or unknown saved exposure refuses every quickstart action without printing the saved contents. This guard belongs to quickstart: a direct installer invocation with `OPENBOT_TUNNEL=off` explicitly changes the saved exposure.

For a bot-triggered install/update, run the same checked-out script with `--bot-mode`. It runs detached, records progress, obtains a Cloudflare URL and QR, and defers a changed host's restart until the active turn finishes. Inspect `--bot-status --brief` after 30–60 seconds, at most twice, then report the current stage or failure. A retained old release is not an upgrade, and a pending restart is not activation proof.

**Bot mode creates a temporary public tunnel even with `OPENBOT_TUNNEL=off`.** Use the [no-public-tunnel agent flow](agent-install.md#install-without-a-public-tunnel) if that is unwanted. Public control URLs grant privileged access to whoever can reach them.

If the native vendor host lacks BOX execution, installing the panel or selecting a model does not recreate it. After installing this fork and its dependencies, [prepare a reviewed engine pair](portable-recovery.md#preparing-a-reviewed-engine-pair) in owned staging, then review the [first binding plan](portable-recovery.md#selecting-the-first-local-engine) when no engine policy exists. Explicit `--apply` selects that initial policy; an already opted-in owned recovery watcher can load it after lock release and idle checks. Integration, native identity admission and actual turn/tool verification remain separate.

## Without Tailscale

For an ordinary terminal install, `OPENBOT_TUNNEL=off` uses the Computer's browser only and requires no VPN. Cloudflare is a separate optional transport: use the panel's tunnel control or `openbot tunnel on` for a temporary public URL. Treat a public control URL as privileged access. Tailscale is not a prerequisite for models, tools or the local scheduler.

If an authorized SSH path already exists, keep the panel on loopback and forward it. Run this on your client computer, replacing the destination with your own Computer's allowed SSH user and host:

```bash
ssh -N -L 127.0.0.1:19280:127.0.0.1:9280 box@YOUR_COMPUTER_SSH_HOST
```

Open `http://127.0.0.1:19280` on that client while SSH remains connected. Keep host-key verification enabled. This needs an existing allowed SSH path; OpenBot installation does not create one.

Quick Cloudflare URLs change and have no uptime guarantee or SSE support. They are convenient panel access, not a production inference-stream transport. A stable named tunnel with access control requires your own separate Cloudflare configuration. [Cloudflare Quick Tunnel limits](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

If `openbot` is not on PATH, invoke the installed `src/cli.ts` with a Node 22.13+ (22.x) or 24+ executable and `--experimental-strip-types`, or use `npm start -- tunnel status` from the installed source with that runtime. The CLI command is not a second hop server.

## With Tailscale

The optional owned recovery helper reads `sand-data/openbot-tailscale.json`. Authentication is a normal Tailscale login, not a key bundled with OpenBot. An example policy (choose your own exit node or omit `exitNodeIP`):

```json
{
  "schemaVersion": 1,
  "enabled": true,
  "autoUpdate": true,
  "networking": "kernel",
  "exitNodeAllowLANAccess": true
}
```

First stage the current stable package with the owned helper's `--install-version`; normal recovery requires its checked manifest. Then install the policy as the Computer user, enable the owned bootstrap, and complete its printed login/SSH checks. The [paste-ready Tailscale prompt](agent-install.md#add-tailscale-and-ssh-recovery) walks an agent through this order. Kernel mode requires the Computer's TUN and network capabilities; use the documented userspace mode for a proxy-only environment. Userspace plus an exit node is refused.

Enabling Tailscale does not grant SSH access outside the tailnet's policy. An identity-check URL needs normal browser completion; a connected badge is not proof of SSH access. Do not copy another installation's Tailscale state or gateway credentials. [Tailscale SSH access and check mode](https://tailscale.com/docs/features/tailscale-ssh). [Recovery and compatibility](portable-recovery.md) describes what the helper can prove after daemon or image replacement.

## Prove the first turn

1. Open the panel in the Computer's browser and confirm its health. If the engine is missing, finish the explicit compatible-engine selection and owned recovery integration first.
2. Add your own provider account in the panel, fetch/import its models, and select the custom global route. Do not post credentials to the installing agent's chat. [Account setup](models-and-tools.md#add-your-accounts).
3. Choose one existing local bot. Check its pin and effort; use Default only when you want global inheritance. Wait for any pending idle host restart to complete.
4. Send a fresh harmless read task with a unique marker. For example, create an owned temporary file with a random nonce and ask the bot to read it and return that nonce. Do not put the answer in the prompt. Clean up the file afterward.
5. Correlate the new accepted turn, the effective account/model, complete upstream stream, returned tool result, and delivered reply. Enable metadata recording if needed; keep bodies private.
6. Check your actual desktop/web/mobile client separately. A gateway probe cannot stand in for the standalone phone app. Do not enable new or paused routines until ownership and schedule tests are complete; preserve existing schedule settings during installation.

If one stage fails, preserve settings and use the [troubleshooting table](troubleshooting.md#start-with-one-fresh-turn). Report that stage rather than rerunning the installer or silently falling back to metered Temporal. Even a verified BOX turn uses the selected provider's quota; log totals alone do not establish account-wide billing.

## Updating your fork

The selected repository/ref is retained in `sand-data/openbot-install-source.json`, outside the replaced source tree. A checkout identifies its own GitHub remote (or package repository); `OPENBOT_REPOSITORY=OWNER/REPO` and `OPENBOT_REF=BRANCH` explicitly choose another source. The default source belongs to this fork.

An authenticated `gh api` fetch supports private repositories. Public repositories use bounded codeload/archive downloads, pinned to the same resolved commit. For an offline or copied checkout, pass `OPENBOT_SRC` again. Optional `OPENBOT_TARBALL` and `OPENBOT_ARCHIVE_TARBALL` URLs must be HTTP(S), without credentials, query or fragment. They are remembered for later archive updates; use `OPENBOT_COMMIT` only when you know that archive's actual commit.

Both shell and bot installs prepare a separate sibling directory, validate source and required parser dependencies, and retain the previous release before switching. A failed cutover restores it. If the supervisor refuses activation, the previous source directory is restored. Runtime data lives outside the source directory and stays in place. If every download fails, an existing install is retained **without stamping it as the requested new version**. Corrupt saved source metadata is refused.

## Uninstalling

Keep the reviewed source checkout outside the installed OpenBot tree so its uninstaller remains available for status after removal:

```bash
bash /tmp/openbot-source/uninstall.sh --bot-mode
bash /tmp/openbot-source/uninstall.sh --bot-status
```

Bot mode requires the Computer's Python 3 and refuses before removing anything if it is missing. Receipt writes and status reads use Python 3, so removing the sole cached `sand-data/node22` runtime cannot strand the final result at `running` because Node is gone. Use the same data/result-path overrides for both commands and follow the printed `OPENBOT_BOT_INSTRUCTION`; a failure includes its concrete `OPENBOT_ERROR`. A completed host-backup validation failure preserves the current host file and its specific error in the atomically published result. Provider secrets remain by default unless `--purge-secrets` is supplied. Restart Grok Bot on the Computer if needed, as the uninstaller instructs.

## Provider accounts

The setup page supports NVIDIA, direct OpenCode **Go**, OpenAI, OpenRouter, Alibaba Coding Plan, Z.AI Coding Plan and custom protocols. Every added account receives an independent provider family; matching names do not overwrite another account. Choose API key, OpenAI sign-in, or a supported credential JSON import/drop. Imported logins are staged until Add account succeeds. Adding an account preserves the active model and bot overrides; fetch/select its models separately.

## Exporting a clean project

Use the [public release checklist](public-release.md) before changing visibility. A source export is separate from an encrypted private runtime backup.

After reviewing and committing changes:

```bash
npm run export:shareable -- --destination /tmp/openbot-shareable --repository YOUR_ACCOUNT/YOUR_REPOSITORY
```

The export contains product code, UI, hooks, tests, docs and licenses from one exact commit. It excludes Git history, the recovery incident journal, fleet configuration/evidence/backups, runtime state, credentials and vendor host bundles. It updates the installer and package links for the destination repository. The destination must be new. Initialize it as a new private repository and review before changing its visibility.

Exported hooks alone do not activate a compatible BOX engine or make an unsupported native mobile sender work. [Reviewed engine preparation](portable-recovery.md#preparing-a-reviewed-engine-pair) can stage a verified pair using the owner's installed native dependencies; controlled activation and identity/live-turn verification remain separate. There is no automatic paid Temporal fallback. Verify an actual bot turn and tool result after installing; a panel HTTP 200 proves only the panel.
