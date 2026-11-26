#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
asar="/opt/Grok Bot/resources/app.asar"
backups="$HOME/.local/share/openbot/desktop-sidebar-backups"
state="$HOME/.local/state/openbot"
mkdir -p "$state"
exec 9>"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/grokbot-date-guard.lock"
flock -n 9 || exit 0
baseline="$state/date-guard.reload-baseline"
target_file="$state/date-guard.reload-target"
alert="$state/date-guard.alert"

save_state() {
  local file=$1 value=$2 temp
  temp="$(mktemp "$state/.date-guard.XXXXXX")" || return
  if ! printf '%s\n' "$value" >"$temp" || ! mv -f -- "$temp" "$file"; then
    rm -f -- "$temp"
    return 1
  fi
}

fail() {
  local message=$1
  save_state "$alert" "$(date -u +%FT%TZ) $message" || printf 'Could not save the date guard alert\n' >&2
  printf '%s\n' "$message" >&2
  exit 1
}

# Only the current user's exact Electron main processes count.
native_desktop() {
  /usr/bin/python3 - "$1" <<'PY'
import os,signal,sys,time
from pathlib import Path
binary='/opt/Grok Bot/grok-bot'; owned=[]
for p in Path('/proc').glob('[0-9]*'):
    try:
        if p.stat().st_uid != os.getuid(): continue
        exe=os.readlink(p/'exe').removesuffix(' (deleted)')
        if exe == binary and b'--type=' not in (p/'cmdline').read_bytes(): owned.append(p)
    except OSError: continue
if sys.argv[1] == 'status':
    print('yes' if owned else 'no')
    raise SystemExit(0)
for p in owned:
    try: os.kill(int(p.name),signal.SIGTERM)
    except ProcessLookupError: pass
for _ in range(100):
    if not any(p.exists() for p in owned): break
    time.sleep(.1)
else: raise SystemExit('Desktop did not exit; refusing a second instance')
PY
}

reload_desktop() {
  local target running
  if [[ -s "$target_file" ]]; then
    target="$(<"$target_file")"
  elif systemctl --user is-active --quiet grokbot-desktop.service; then
    target=grokbot-desktop.service
  elif systemctl --user is-active --quiet grokbot-date-recovery-launch.service; then
    target=grokbot-date-recovery-launch.service
  else
    running="$(native_desktop status)" || return
    [[ "$running" == yes ]] || return 0
    target=desktop-menu
  fi

  # Remember the launcher before stopping the app. A failed start can leave it
  # stopped and no longer discoverable on the next timer run.
  save_state "$target_file" "$target" || return
  case "$target" in
    grokbot-desktop.service|grokbot-date-recovery-launch.service)
      systemctl --user restart "$target" && systemctl --user is-active --quiet "$target"
      ;;
    desktop-menu)
      native_desktop stop && gtk-launch grok-bot
      ;;
    *) return 1 ;;
  esac
}

before="$(sha256sum "$asar" | cut -d' ' -f1)" || fail 'Date guard could not read the application archive'
# Record the original archive before the patcher can replace it. This also
# covers a watcher interrupted between applying the guard and reloading.
if [[ ! -s "$baseline" ]]; then
  save_state "$baseline" "$before" || fail 'Date guard could not record the pending archive'
fi
node="$(command -v node || true)"
if [[ "$node" == */mise/shims/node ]] && command -v mise >/dev/null 2>&1; then
  node="$(mise which node 2>/dev/null || true)"
fi
if [[ -z "$node" && -x "$HOME/.local/tools/node-v22.23.4-linux-x64/bin/node" ]]; then
  node="$HOME/.local/tools/node-v22.23.4-linux-x64/bin/node"
fi
if ! sudo -n /usr/bin/env "GROKBOT_ASAR_PATCH_BACKUPS=$backups" "GROKBOT_NODE_BIN=$node" /usr/bin/python3 "$root/patch-current-date.py" >"$state/date-guard.last" 2>&1; then
  fail "Date guard check failed: $(tail -n 1 "$state/date-guard.last")"
fi
after="$(sha256sum "$asar" | cut -d' ' -f1)" || fail 'Date guard could not verify the application archive'
if [[ "$(<"$baseline")" != "$after" || -s "$target_file" ]]; then
  reload_desktop || fail 'Date guard is installed and the desktop reload is pending'
fi
rm -f -- "$target_file" "$baseline" "$alert"
