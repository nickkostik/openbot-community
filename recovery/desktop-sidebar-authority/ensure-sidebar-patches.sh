#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
asar="${GROKBOT_ASAR:-/opt/Grok Bot/resources/app.asar}"
patcher="${GROKBOT_SIDEBAR_PATCHER:-$script_dir/patch-temporal-sidebar-preferences.py}"
log="${GROKBOT_SIDEBAR_LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/openbot/sidebar-patches-watch.log}"
backups="${GROKBOT_ASAR_PATCH_BACKUPS:-$HOME/.local/share/openbot/desktop-sidebar-backups}"
code_cache="${GROKBOT_CODE_CACHE_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/Grok Bot/v8-code-cache}"
alert="${GROKBOT_SIDEBAR_ALERT:-$script_dir/sidebar-patches-watch.alert}"
runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
lock="${runtime_dir}/grokbot-sidebar-patches.watch.lock"
service="${GROKBOT_DESKTOP_SERVICE:-grokbot-desktop.service}"

mkdir -p "$(dirname "$log")" "$(dirname "$alert")"
exec 9>"$lock"
flock -n 9 || exit 0

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

before="$(sha256sum "$asar" | awk '{print $1}')"
if ! sudo -n /usr/bin/env "GROKBOT_ASAR=$asar" /usr/bin/python3 "$patcher" --check >"${log}.check" 2>&1; then
  message="$(timestamp) preflight failure: $(tail -n 1 "${log}.check" 2>/dev/null || true)"
  printf '%s\n' "$message" | tee -a "$log" >"$alert"
  exit 0
fi
changed="$(/usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["changed"])' <"${log}.check")"
if [[ "$changed" == "False" ]]; then
  rm -f "$alert"
  exit 0
fi

was_active=false
if systemctl --user is-active --quiet "$service"; then
  was_active=true
  systemctl --user stop "$service"
fi
# A menu-launched instance may own the singleton while the unit is inactive.
# Quit before patching or invalidating V8 cache; old code must not repersist it.
quit_count="$(/usr/bin/python3 - "$asar" <<'PY'
import os,signal,sys,time
from pathlib import Path
binary=(Path(sys.argv[1]).parent.parent/'grok-bot').resolve()
owned=[]
for entry in Path('/proc').glob('[0-9]*'):
    try:
        if entry.stat().st_uid==os.getuid() and (entry/'exe').resolve()==binary and b'--type=' not in (entry/'cmdline').read_bytes(): owned.append(entry)
    except OSError: pass
for entry in owned:
    try: os.kill(int(entry.name),signal.SIGTERM)
    except ProcessLookupError: pass
for _ in range(100):
    if not any(entry.exists() for entry in owned): break
    time.sleep(0.1)
else: raise SystemExit('normal quit timed out; original archive preserved')
print(len(owned))
PY
)"
if [[ "$quit_count" != "0" ]]; then was_active=true; fi
if ! sudo -n /usr/bin/env "GROKBOT_ASAR=$asar" "GROKBOT_ASAR_PATCH_BACKUPS=$backups" "GROKBOT_CODE_CACHE_DIR=$code_cache" /usr/bin/python3 "$patcher" >"${log}.last" 2>&1; then
  message="$(timestamp) patch failure: $(tail -n 1 "${log}.last" 2>/dev/null || true)"
  printf '%s\n' "$message" | tee -a "$log" >"$alert"
  if [[ "$was_active" == true ]]; then systemctl --user start "$service"; fi
  exit 0
fi
after="$(sha256sum "$asar" | awk '{print $1}')"

if [[ "$before" != "$after" ]]; then
  printf '%s bundle changed %s -> %s; restarting %s\n' \
    "$(timestamp)" "$before" "$after" "$service" | tee -a "$log"
  rm -f "$alert"
else
  rm -f "$alert"
fi
# Another compatible installer may have finished while this watcher was
# stopping the app. Restore the previous running state even in that case.
if [[ "$was_active" == true ]]; then systemctl --user start "$service"; fi
