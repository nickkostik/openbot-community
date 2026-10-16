# OpenBot

[English](README.md) · [中文](README.zh-CN.md)

Pick the model you actually want. Keep your bots, their tools, and their settings.

OpenBot runs on your **Grok Bot Computer** and routes supported local bot turns to your own provider accounts. This fork keeps per-bot models and reasoning, independent accounts, compaction, cache reporting, local routines, and recovery tools together.

**The goal is simple: install it, send a real message, get a real tool result. Then call it working.**

## Paste this into your agent

Use a coding agent that can execute commands on your authenticated Grok Bot Computer. The installer is for that Computer, not your laptop or phone.

This prompt authorizes a temporary public control URL. Anyone with that URL can operate the panel. For Computer-only access, use the [no-public-tunnel prompt](docs/agent-install.md#install-without-a-public-tunnel).

```text
Install OpenBot from https://github.com/nickkostik/openbot-community on my Grok Bot Computer.

I authorize the installer, its temporary Cloudflare control URL, and its guarded idle restart. Keep my existing bots and settings.

Read AGENTS.md, docs/agent-install.md, docs/portable-installation.md, and docs/portable-recovery.md from that exact fork before changing anything.

Run on the authenticated Computer as the sand-data owner. Discover the actual running host and data directory. If there are multiple hosts, resolve that before installing. Do not patch my desktop app.

Clone a fresh owned checkout from that public repository. Stop on a download failure; do not substitute another OpenBot repo.

Run the checked-out install.sh with OPENBOT_SRC set to that checkout and --bot-mode. Read --bot-status --brief after 30 to 60 seconds, at most twice. Report a failure or unfinished stage instead of endlessly polling or reinstalling.

Send me the resulting panel URL and QR through the normal reply tool. Keep that URL private. A pending host bounce means the restart still needs to finish after your turn; do not force it.

Preserve providers, keys, model pins, reasoning choices, conversations, rooms, categories, routines, network preferences, and the saved update source. Adding an account must not change my active model. Do not enable routines, replay missed slots, remint bots, clear drift holds, or fall back to Temporal.

If the vendor host no longer has a BOX runner, explain the missing engine and the reviewed recovery plan. Installing the panel alone is not local execution. Preserve any existing engine policy; first engine selection is a separate explicit opt-in.

Let me add my credentials in the panel. Never ask me to paste keys, OAuth files, or tokens into chat. OpenCode Go must use its direct Go endpoint, with each account in its own family. No N5 or MetaKey.

After a custom route is selected and the idle restart completes, verify one new message and one harmless file-read tool call through an existing bot. Correlate the effective provider/model, completed stream, tool result, and delivered reply. Test each client separately.

Report the installed commit, panel status, engine status, effective route, restart status, tests that passed, and anything still blocked. No success claim based only on HTTP 200, a picker value, or harness metadata. Do not claim standalone mobile or voice works without testing that actual client.
```

The public source needs no GitHub login. Provider accounts and any optional Tailscale login belong to your own installation.

[More paste-ready prompts](docs/agent-install.md) cover Computer-only installation, optional Tailscale, first local-engine selection, complete-copy migration, and updates.

## What you get

| Feature | What it does |
|---|---|
| Your provider accounts | NVIDIA, direct OpenCode Go, OpenAI, OpenRouter, Alibaba Coding Plan, Z.AI Coding Plan, and custom endpoints. Each added account has its own family. |
| Global model and bot overrides | Pick a default, pin a different model for a bot, and save reasoning per bot and model. Batch controls are included. |
| Compaction | Use the bot's selected model first, keep resumable progress, and use approved overflow fallbacks when appropriate. |
| Cache and usage reporting | Show provider cache counts, unknown coverage, request purpose, and measured stream timing without counting the harness view twice. |
| Local routines | A guarded native clock with queued slots, receipts, ownership checks, and a configurable cap up to 12 bots. |
| Updates and recovery | Stage source before activation, retain a previous release, wait for idle restarts, and recover opted-in owned services. |
| Existing integrations | Keep the Computer's native MCP tools and ACP agent connections; verify their actual results after installation. |

## Local means where the turn runs

**Custom / Official** chooses the inference route. **BOX / Temporal** chooses the execution engine. They are separate settings.

| State | Where work goes |
|---|---|
| BOX + Custom | A compatible local runner uses OpenBot and the selected provider account. |
| Official | The stock host and platform delivery use Grok quota. A retained local generation is not selected as the live Official host. |
| Temporal | The platform executes the turn. A box-side model picker cannot intercept it. |

Provider calls still consume that provider's quota or credits. OpenBot is not a promise of free usage, and its logs are not the billing ledger.

Recent upstream hosts removed BOX execution. On those versions, a local installation needs an explicitly reviewed compatible engine. This repository includes preparation and recovery tooling, not another user's engine bundle or credentials. Start with the [compatibility boundary](docs/portable-recovery.md#compatibility-boundary).

## Which clients work?

Compatibility notes checked on **October 11, 2026**. Verify your own installation after each update.

| Path | Current evidence |
|---|---|
| Panel and local hop | Tested. A working panel does not prove bot dispatch. |
| Native gateway to local bot | Real turns and tool round trips have completed through supported routes. |
| Grok Bot desktop and integrated Grok web/app | Working in the reference installation, based on client reports and local turn evidence. Your client still needs its own send test. |
| Unchanged standalone Grok Bot iOS/Android app | **Unresolved for this BOX path.** The platform can refuse delivery before the box receives it. |
| Tool completion during an installed-app voice call | **Unverified.** Diagnostic voice transport tests do not prove live app completion before hangup. |
| Every feature from the latest Temporal engine | **Incomplete.** Compatible updates require reviewed ports; latest version metadata is not full feature parity. |

The installer does not change phone certificates or network settings and does not activate the optional proxy code. If standalone mobile is your only client, read this table before installing.

## Install from a terminal

Run on the Computer as its normal data owner. You need Bash, Python 3, `flock`, and access to the native authenticated host. Node 22.13+ (22.x) or 24+ is required; the installer can cache Node without replacing system Node.

Clone the public project:

```bash
openbot_source="$(mktemp -d /tmp/openbot-source.XXXXXX)"
git clone https://github.com/nickkostik/openbot-community.git "$openbot_source"
bash "$openbot_source/quickstart.sh" --check
bash "$openbot_source/quickstart.sh" --install
```

Stop if cloning fails. The installer saves this source repository and keeps it on later updates.

If public access is already configured, `--check` reports it and `--install` stops before changing that tunnel. Preserve the existing setup and resolve which access you want; `--public-tunnel` is a separate explicit choice.

Open **http://127.0.0.1:9280 in the Computer's browser**. A fresh install starts in Official mode. Add your own account, fetch its models, select a custom route, and complete the [first real turn checks](docs/portable-installation.md#prove-the-first-turn). Existing installs keep their saved mode and settings.

An Official update can keep the panel available when a new vendor host removes the optional logging hook. The installer explicitly warns that request logging is unavailable and leaves stock delivery and saved settings unchanged. [Update behavior and verification](docs/updating.md#update-the-product-source).

Quickstart defers any required host restart until the host is idle. A direct ordinary `install.sh` call can interrupt active turns unless deferred restart is requested. Use the agent prompt when installing from inside a bot turn.

## Set it up your way

1. **Connect an account.** Choose the provider preset and enter your credentials in the panel. Adding it creates an independent family.
2. **Fetch models.** Import the routes offered by that account. Check protocol, limits, and supported reasoning for that route.
3. **Choose the global model.** Bots set to Default inherit it; explicit bot pins keep their own model.
4. **Choose reasoning.** On Bots, save effort for one bot/model pair or a selection of bots and models. Default and Off have different meanings.
5. **Send a new message.** Check the actual provider call and delivered reply. An already running turn does not change models halfway through.

[Models, reasoning, compaction, cache, and tools](docs/models-and-tools.md) explains the details.

## The guide

| Need | Read this |
|---|---|
| Let an agent install it | [Paste-ready instructions](docs/agent-install.md) |
| Install with or without Tailscale | [Computer installation](docs/portable-installation.md) |
| Retain a compatible local runner | [Local engine and recovery](docs/portable-recovery.md) |
| Connect accounts and tune bots | [Models and tools](docs/models-and-tools.md) |
| Update the Official host and matching local engine | [Coordinated update guide](docs/updating.md) |
| Diagnose delivery, models, routines, or updates | [Troubleshooting and maintenance](docs/troubleshooting.md) |
| Share your own clean fork | [Public release checklist](docs/public-release.md) |
| Work on the project | [Agent instructions](AGENTS.md) and [configuration reference](skills/openbot-config/reference.md) |

## Update both runtimes

Use the [coordinated update guide](docs/updating.md). Updating OpenBot source, the native Computer image, and the compatible local engine are separate steps in one checked procedure. Keep the selected mode and saved bot settings. A staged local generation is ready for testing; it becomes verified only after a real local tool call and delivered reply. Unknown upstream changes require a reviewed port. The cloud Temporal service is maintained by the vendor.

## Uninstall

Run on the Computer:

```bash
bash /home/box/sand-data/openbot/uninstall.sh
```

The interactive uninstaller asks for confirmation and keeps provider secrets by default. `--purge-secrets` explicitly deletes them. Keep an external source checkout and encrypted state backup until you have verified restoration.

## License

MIT. See [LICENSE](LICENSE). OpenBot is an independent project and is not affiliated with xAI, Cursor, or the model providers.
