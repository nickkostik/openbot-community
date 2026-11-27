# Optional Linux desktop repair

Use this only when an affected **Linux Grok Bot desktop** shows stale bots or
`Something went wrong` / `Invalid time value` when opening a recovered chat.
Installing OpenBot on the shared Computer does not repair a separate desktop.
The ordinary OpenBot installer does not run these scripts.

The composite roster and conversation repair supports Grok Bot **0.75.0 and
0.75.3**. The current renderer guard below supports the reviewed **1.3.0**
layout. Unknown builds, partial patches, and mixed release anchors are refused.

## Repair a conversation on 0.75.0 or 0.75.3

1. Save any draft and let pending sends finish. Quit the desktop normally.
2. From a fresh checkout of this public repository, run the preflight:

   ```bash
   python3 recovery/desktop-sidebar-authority/patch-temporal-sidebar-preferences.py --check
   ```

3. Review the output. If it reports a supported change, apply it on that desktop:

   ```bash
   sudo env \
     GROKBOT_ASAR_PATCH_BACKUPS="$HOME/.local/share/openbot/desktop-sidebar-backups" \
     GROKBOT_CODE_CACHE_DIR="$HOME/.config/Grok Bot/v8-code-cache" \
     python3 recovery/desktop-sidebar-authority/patch-temporal-sidebar-preferences.py
   ```

4. Reopen the app, open the failing conversation and its older messages, then send
   one short test from its normal composer. Check the reply arrives, the draft is
   empty and there is no failed-send toast. Repeat for each affected desktop.

Set `GROKBOT_ASAR` for a different installation path, and set the code-cache
directory for the actual profile. Never point this at a Mac archive. No server
agent, bot identity, transcript, stored timestamp, instruction, routine or model
setting is edited by this archive repair.

The renderer guard treats dates outside JavaScript's supported range as unknown
instead of calling a date formatter that throws. Content and message IDs stay
intact. The roster repair respects a complete authoritative roster, protected
pending rows and normal Hide/Unhide controls. It retains previews and activity
metadata; an incomplete roster does not authorize pruning.

The composite repair also reads and saves Temporal sidebar sections through the
existing account settings service. It preserves section IDs and member order
and does not require the old Computer to be awake. It propagates save failures.
Neither part performs a history merge or changes the selected execution engine.

Installation backs up the original archive, keeps packed-file lengths and ASAR
offsets unchanged, refreshes changed asset SHA256 integrity and replaces the
archive atomically. Because equal-length main-process code can reuse stale V8
bytecode, the composite installer refuses a running app and backs up only its
`main-app-*.v8` caches before invalidating them. Other caches are retained.

## Optional update watcher

For the current renderer, use `patch-current-date.py` with
`ensure-date-guard.sh`. The repair bounds message timestamps before date
formatting. In compatible layouts, owned Temporal chats sort by recorded
conversation activity within each section. Chats with a missing or invalid
activity date use their creation date. Shared chats and other harnesses use the
stock ordering rules.

Copy both scripts and `patch-sidebar-authority.py` into
`~/.local/share/openbot/desktop-sidebar-recovery/`. Make
`ensure-date-guard.sh` executable. Install
`systemd/grokbot-date-guard.{service,timer}` as user units and enable the timer
after reviewing the paths and saving any open drafts. Its minute checks use the
owner's existing sudo permission. The first compatible repair stores an
archive backup before reloading the desktop through its existing launcher. An
interrupted reload is retried on the next check. A current failure appears in
`~/.local/state/openbot/date-guard.alert` and clears after a successful check.
Open the affected chat and scroll into older history after applying the guard.

### Composite repair for 0.75.0 and 0.75.3

Only configure this if you already manage this desktop with a working
`grokbot-desktop.service` and have authorized unattended compatible repair.
Inspect the examples in `systemd/`; retain your own service's GUI flags.

Copy the four product scripts to
`~/.local/share/openbot/desktop-sidebar-recovery/`. Install the timer/service
examples into your user systemd configuration and the desktop drop-in beneath
that service. Inspect the copied paths before enabling anything.

The watcher uses the owner's existing sudo permission; it does not install a
sudo policy. It preflights before stopping an app, preserves whether the app was
running, backs up a supported update and restarts only when needed. Unsupported
updates remain intact and produce `sidebar-patches-watch.alert` beside the
scripts. Keep that alert visible to your monitoring agent. Do not replace a
rejected layout with an unreviewed search-and-replace patch.

## Agent instructions

```text
Investigate the failing Linux Grok Bot desktop conversation. Read this guide
and AGENTS.md from this checkout. Inspect the actual app version and renderer
error. A working panel or another desktop is not proof this client works.

Use only the exact supported-layout repair after saving drafts, waiting for
pending sends and quitting the app normally. Preserve all bot IDs, sections,
history, instructions, tools, routines and mode. Do not change server routing,
delete persistence, recreate bots, enable routines or replay old work.

Run the preflight and keep the original backup. If the layout is unsupported,
stop before changing the archive and report the version and error. After a
supported repair, reopen every affected conversation, inspect older messages,
and verify a real composer send receives a reply. Report checks separately from
anything untested. Never put account exports, error dumps, archives or keys in
the public repository.
```

`repair-client-cache.py` is a separate, advanced cache reconciliation tool. It
defaults to a dry run and requires private, fresh owner roster/settings exports.
Do not run it to solve a date-rendering error. Keep its input exports and
backups outside Git and review UUID membership before any explicit application.

## Tests

```bash
python3 -m unittest discover -s recovery/desktop-sidebar-authority -p 'test_*.py'
```

The tests exercise both date readers and message rows, unchanged archive offsets
and integrity, mixed-build refusal, complete/incomplete rosters, normal
visibility controls, pending rows, server preference failures, V8 cache
preservation and watcher stop/restore behavior. Fixtures are synthetic. No
account evidence or installed vendor archive is distributed with this tool.
