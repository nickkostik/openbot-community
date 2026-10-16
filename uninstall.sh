#!/usr/bin/env bash
# OpenBot uninstaller. Run this on the Grok Bot Computer, not on a Mac.
set -euo pipefail

HOST="${OPENBOT_HOST_MAIN:-}"
if [[ -z "$HOST" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    HOST="$(python3 - <<'PYHOST'
import pathlib, sys
candidates = ['/opt/sand/sand-host/host-main.cjs', '/home/box/sand-host/host-main.cjs']
running = set()
try:
    for directory in pathlib.Path('/proc').iterdir():
        if not directory.name.isdigit():
            continue
        try:
            args = directory.joinpath('cmdline').read_bytes().decode(errors='replace').split('\x00')[:-1]
            if args and pathlib.Path(args[0]).name == 'node' and args[-1] in candidates:
                running.add(args[-1])
        except OSError:
            pass
except OSError:
    pass
if len(running) > 1:
    print('OpenBot: multiple native hosts are running; set OPENBOT_HOST_MAIN explicitly.', file=sys.stderr)
    sys.exit(1)
print(next(iter(running)) if running else next((p for p in candidates if pathlib.Path(p).is_file()), candidates[-1]))
PYHOST
)"
  elif [[ -f /opt/sand/sand-host/host-main.cjs ]]; then
    HOST=/opt/sand/sand-host/host-main.cjs
  else
    HOST=/home/box/sand-host/host-main.cjs
  fi
fi
DATA="${OPENBOT_SAND_DATA-/home/box/sand-data}"
RESULT_FILE="${OPENBOT_BOT_RESULT:-$DATA/openbot-uninstall-result.json}"
LOG_FILE="${OPENBOT_BOT_LOG:-$DATA/openbot-uninstall.log}"
PID_FILE="${OPENBOT_BOT_PID:-$DATA/openbot-uninstall.pid}"
YES=0
PURGE_SECRETS=0
WORKER=0
WORKER_ENTRY=0
WORKER_FAILURE_RECORDED=0

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

sanity_check() {
  local normalized
  [[ -n "$DATA" ]] || { echo 'OpenBot: refusing to operate on an empty OPENBOT_SAND_DATA (expected a /sand-data path).' >&2; exit 2; }
  [[ "$DATA" == /* ]] || { echo "OpenBot: refusing to operate on unsafe OPENBOT_SAND_DATA=$DATA (expected an absolute /sand-data path)." >&2; exit 2; }
  normalized="$(realpath -m -- "$DATA" 2>/dev/null)" || { echo "OpenBot: refusing to operate on unsafe OPENBOT_SAND_DATA=$DATA (expected a /sand-data path)." >&2; exit 2; }
  [[ "$normalized" == /* && "$normalized" != / ]] || { echo "OpenBot: refusing to operate on unsafe OPENBOT_SAND_DATA=$DATA (expected a /sand-data path)." >&2; exit 2; }
  case "$normalized" in
    */sand-data|*/sand-data/*) DATA="$normalized" ;;
    *) echo "OpenBot: refusing to operate on unsafe OPENBOT_SAND_DATA=$DATA (expected a /sand-data path)." >&2; exit 2 ;;
  esac
}

json_write() {
  local status="$1" stage="$2" summary="$3" error="${4:-}" finished="${5:-}"
  mkdir -p "$(dirname "$RESULT_FILE")"
  # Python is supplied by the Computer and survives deletion of node22.
  python3 - "$RESULT_FILE" "$status" "$stage" "$summary" "$error" "$finished" <<'PYRESULT'
import datetime, json, os, sys
file, status, stage, summary, error, finished = sys.argv[1:]
try:
    with open(file, encoding='utf8') as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        result = {}
except (OSError, ValueError):
    result = {}
timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
result['status'] = status
if not result.get('startedAt'):
    result['startedAt'] = timestamp
if finished:
    result['finishedAt'] = finished
else:
    result.pop('finishedAt', None)
result['progress'] = {'stage': stage, 'summary': summary, 'updatedAt': timestamp}
if error:
    result['error'] = error
else:
    result.pop('error', None)
# Readers continue to see the prior complete document until publication.
temporary = file + '.' + str(os.getpid()) + '.tmp'
try:
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf8') as handle:
        handle.write(json.dumps(result, indent=2) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, file)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PYRESULT
}

require_status_runtime() {
  command -v python3 >/dev/null 2>&1 || {
    echo 'OpenBot: Python 3 is required to record uninstall progress; no uninstall was started.' >&2
    return 2
  }
}

pid_from_file() {
  local file="$1" pid=""
  [[ -f "$file" ]] || return 1
  pid="$(tr -d '[:space:]' <"$file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$pid"
}

alive() { kill -0 "$1" 2>/dev/null; }
argv_of() {
  if [[ -r "/proc/$1/cmdline" ]]; then tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null || true; else ps -p "$1" -o args= 2>/dev/null || true; fi
}

stop_pid_if_argv() {
  local pid="$1" needle="$2" args
  alive "$pid" || return 1
  args="$(argv_of "$pid")"
  [[ "$args" == *"$needle"* ]] || return 2
  kill "$pid" 2>/dev/null || true
  for _ in {1..50}; do alive "$pid" || break; sleep 0.1; done
  return 0
}

stop_guard() {
  local file="$DATA/openbot-guard.pid" pid
  pid="$(pid_from_file "$file" || true)"
  if [[ -n "$pid" ]] && alive "$pid"; then
    if [[ "$(argv_of "$pid")" == *"src/cli.ts guard --daemon"* || "$(argv_of "$pid")" == *"guard-daemon"* ]]; then
      kill "$pid" 2>/dev/null || true
      echo 'Guard daemon stopped.'
    else
      echo "Guard pidfile $pid does not match the guard daemon; left process untouched."
    fi
  else
    echo 'Guard daemon was not running.'
  fi
  [[ -e "$file" ]] && rm -f "$file"
  return 0
}

stop_tunnel() {
  local file="$DATA/openbot-tunnel.pid" pid
  pid="$(pid_from_file "$file" || true)"
  if [[ -n "$pid" ]] && alive "$pid"; then
    local args="$(argv_of "$pid")"
    if [[ "$args" == *cloudflared* && "$args" == *--url* && "$args" == *127.0.0.1:9280* ]]; then kill "$pid" 2>/dev/null || true; echo 'Cloudflare Tunnel stopped.'; else echo "Tunnel pidfile $pid does not match the owned cloudflared command; left process untouched."; fi
  else echo 'Cloudflare Tunnel was not running.'; fi
  [[ -e "$file" ]] && rm -f "$file"
  return 0
}

stop_hop() {
  local file="$DATA/openbot-hop.pid" pid result
  pid="$(pid_from_file "$file" || true)"
  if [[ -n "$pid" ]] && alive "$pid"; then
    result=0; stop_pid_if_argv "$pid" hop-server || result=$?
    if [[ "$result" -eq 0 ]]; then echo 'Standalone hop stopped.'; elif [[ "$result" -eq 2 ]]; then echo "Hop pidfile $pid does not match hop-server; left process untouched."; fi
  else echo 'Standalone hop was not running.'; fi
  [[ -e "$file" ]] && rm -f "$file"
  return 0
}

port_pids() {
  if command -v lsof >/dev/null 2>&1; then lsof -nP -t -iTCP:9280 -sTCP:LISTEN 2>/dev/null || true;
  elif command -v ss >/dev/null 2>&1; then ss -ltnp 2>/dev/null | awk '$4 ~ /:9280$/ { match($0,/pid=[0-9]+/); if (RSTART) print substr($0,RSTART+4,RLENGTH-4) }' | sort -u;
  fi
}

stop_ui() {
  local file="$DATA/openbot-ui.pid" pid args found=0
  pid="$(pid_from_file "$file" || true)"
  if [[ -n "$pid" ]] && alive "$pid"; then
    args="$(argv_of "$pid")"
    if [[ "$args" == *src/ui/server.ts* ]]; then kill "$pid" 2>/dev/null || true; found=1; echo 'OpenBot UI server stopped.'; else echo "UI pidfile $pid does not match OpenBot UI; left process untouched."; fi
  fi
  if [[ "$found" -eq 0 ]]; then
    while read -r pid; do
      [[ -n "$pid" ]] || continue
      args="$(argv_of "$pid")"
      if [[ "$args" == *src/ui/server.ts* ]]; then kill "$pid" 2>/dev/null || true; found=1; echo 'OpenBot UI server stopped by argv fallback.'; fi
    done < <(port_pids)
  fi
  if [[ "$found" -eq 0 ]] && [[ -n "$(port_pids)" ]]; then echo '9280 is occupied by a foreign process; it was not killed.'; fi
  [[ -e "$file" ]] && rm -f "$file"
  return 0
}

restore_host() {
  local preferred="$DATA/host-main.cjs.pre-openbot" legacy="${HOST}.pre-openbot" backup="" candidate first_line invalid_found=0
  local invalid_message="$(printf '%b' '\345\244\207\344\273\275\346\227\240\346\225\210\357\274\214\344\277\235\347\225\231\345\275\223\345\211\215\345\256\277\344\270\273\346\226\207\344\273\266')"
  HOST_RESTORE_INVALID=0
  HOST_RESTORE_SUMMARY=''
  for candidate in "$preferred" "$legacy"; do
    [[ -e "$candidate" ]] || continue
    if [[ -f "$candidate" && -s "$candidate" && -r "$candidate" ]]; then
      first_line="$(sed -n '1p' "$candidate" 2>/dev/null || true)"
      if [[ -n "${first_line//[[:space:]]/}" ]]; then
        backup="$candidate"
        break
      fi
    fi
    invalid_found=1
  done
  if [[ -n "$backup" ]]; then
    if mv -f -- "$backup" "$HOST"; then
      HOST_RESTORE_SUMMARY="Restored the stock host from $backup."
      echo "$HOST_RESTORE_SUMMARY"
    else
      HOST_RESTORE_INVALID=1
      HOST_RESTORE_SUMMARY="Host backup invalid; current host file preserved ($invalid_message)"
      echo "$invalid_message"
    fi
  elif [[ "$invalid_found" -eq 1 ]]; then
    HOST_RESTORE_INVALID=1
    HOST_RESTORE_SUMMARY="Host backup invalid; current host file preserved ($invalid_message)"
    echo "$invalid_message"
  elif [[ -f "$HOST" ]]; then
    echo 'No OpenBot host backup found; host was left unchanged.'
  else
    echo 'No OpenBot host backup or host file found.'
  fi
  [[ "$HOST_RESTORE_INVALID" -eq 0 ]]
}

remove_data() {
  local item
  for item in "$DATA/openbot" "$DATA/node22"; do [[ -e "$item" ]] && rm -rf "$item"; done
  for item in "$DATA"/openbot-*; do
    [[ -e "$item" ]] || continue
    [[ "$item" == "$RESULT_FILE" || "$item" == "$LOG_FILE" ]] && continue
    rm -rf "$item"
  done
  if [[ "$PURGE_SECRETS" -eq 1 && -e "$DATA/secrets.json" ]]; then rm -f "$DATA/secrets.json"; echo 'Deleted provider secrets (--purge-secrets).'; else echo 'Provider secrets retained at secrets.json (default; use --purge-secrets to delete).'; fi
}

verify() {
  local port="$(port_pids)"
  [[ -z "$port" ]] && echo 'Verification: 9280 is not listening.' || echo "Verification: 9280 still has listener pid(s) $port; foreign listeners were not killed."
  if [[ -f "$DATA/host-main.cjs.pre-openbot" || -f "${HOST}.pre-openbot" ]]; then echo 'Verification: stock host backup still exists; host restore needs attention.'; elif [[ -f "$HOST" ]]; then echo 'Verification: host-main.cjs is present.'; else echo 'Verification: host-main.cjs is missing.'; fi
}

uninstall_main() {
  local mode
  if [[ -f "$DATA/openbot-mode" ]]; then
    mode="$(tr -d '[:space:]' <"$DATA/openbot-mode")"
  else
    mode=''
  fi
  [[ "$mode" == custom ]] && echo 'OpenBot is currently in custom mode.' || echo "OpenBot is currently in official mode or has no mode file; residual files will still be removed."
  [[ "$YES" -eq 1 ]] || {
    printf '%s\n' \
      'The following will be removed: OpenBot program/runtime, OpenBot state/log/pid files, tunnel files, and the OpenBot host wrap (stock backup restored when available).' \
      "Provider secrets will be retained by default at $DATA/secrets.json." \
      'Do not touch /home/box/agent-data.' \
      >&2
    if [[ ! -t 0 ]] || ! read -r -p 'Continue with OpenBot uninstall? [y/N] ' answer; then
      printf '%s\n' 'Non-interactive shell detected: re-run with --yes to confirm, or run from a terminal.' >&2
      return 2
    fi
    [[ "$answer" == y || "$answer" == Y || "$answer" == yes || "$answer" == YES ]] || { echo 'OpenBot uninstall cancelled.'; return 0; }
  }
  [[ "$WORKER" -eq 1 ]] && json_write running guard 'Stopping OpenBot workers.'
  stop_guard
  [[ "$WORKER" -eq 1 ]] && json_write running tunnel 'Turning off the Cloudflare Tunnel.'
  stop_tunnel
  [[ "$WORKER" -eq 1 ]] && json_write running hop 'Stopping the standalone hop.'
  stop_hop
  [[ "$WORKER" -eq 1 ]] && json_write running ui 'Stopping the OpenBot UI.'
  stop_ui
  [[ "$WORKER" -eq 1 ]] && json_write running host 'Restoring the stock Grok Bot host.'
  restore_host || true
  echo 'The Grok Bot host was not restarted by uninstall; restart Grok Bot on the Computer if it is still running.'
  [[ "$WORKER" -eq 1 ]] && json_write running files 'Removing OpenBot files.'
  remove_data
  verify
  if [[ "$HOST_RESTORE_INVALID" -eq 1 ]]; then
    if [[ "$WORKER" -eq 1 ]]; then
      if json_write failed verify "$HOST_RESTORE_SUMMARY" 'The host backup failed validation; the current host file was preserved.' "$(now)"; then
        WORKER_FAILURE_RECORDED=1
      fi
    fi
    return 1
  fi
  if [[ "$WORKER" -eq 1 ]]; then
    json_write success verify "Uninstall complete. Restart Grok Bot on the Computer if needed.${HOST_RESTORE_SUMMARY:+ $HOST_RESTORE_SUMMARY}" '' "$(now)"
  fi
  return 0
}

bot_status() {
  if [[ ! -f "$RESULT_FILE" ]]; then
    printf '%s\n' '===== OPENBOT BOT RESULT =====' 'OPENBOT_STATUS=not-started' "OPENBOT_RESULT_FILE=$RESULT_FILE" 'OPENBOT_BOT_INSTRUCTION=No detached OpenBot uninstall is recorded. Run uninstall.sh --bot-mode.' '===== END OPENBOT BOT RESULT ====='
  else
    require_status_runtime || return $?
    python3 - "$RESULT_FILE" <<'PYSTATUS'
import json, sys
file = sys.argv[1]
try:
    with open(file, encoding='utf8') as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError('Invalid result document')
except (OSError, ValueError):
    value = {'status': 'failed', 'error': 'Invalid result JSON'}
def line(key, value):
    print(key + '=' + str(value).replace('\r', '').replace('\n', '\\n'))
print('===== OPENBOT BOT RESULT =====')
status = value.get('status') or 'failed'
line('OPENBOT_STATUS', status)
line('OPENBOT_RESULT_FILE', file)
progress = value.get('progress')
if isinstance(progress, dict):
    line('OPENBOT_PROGRESS_STAGE', progress.get('stage', ''))
    line('OPENBOT_PROGRESS_SUMMARY', progress.get('summary', ''))
if value.get('error'):
    line('OPENBOT_ERROR', value['error'])
if status == 'running':
    instruction = 'Relay the current uninstall stage; poll uninstall.sh --bot-status again in 30-60 seconds.'
elif status == 'success':
    instruction = 'SendToUser: OpenBot uninstall completed. Tell the user to restart Grok Bot on the Computer if it is still running; provider secrets were retained unless --purge-secrets was used.'
else:
    instruction = 'Tell the user the OpenBot uninstall failed and include OPENBOT_ERROR.'
line('OPENBOT_BOT_INSTRUCTION', instruction)
print('===== END OPENBOT BOT RESULT =====')
PYSTATUS
  fi
}

sanity_check
while [[ $# -gt 0 ]]; do case "$1" in --yes) YES=1 ;; --purge-secrets) PURGE_SECRETS=1 ;; --bot-mode) WORKER=1 ;; --bot-mode-worker) WORKER=1; WORKER_ENTRY=1; YES=1 ;; --bot-status) bot_status; exit $? ;; *) echo "OpenBot: unknown option $1" >&2; exit 2 ;; esac; shift; done
if [[ "$WORKER" -eq 1 ]]; then require_status_runtime; fi
if [[ "$WORKER" -eq 1 && "${1:-}" != '--bot-mode-worker' ]]; then :; fi
if [[ "$WORKER_ENTRY" -eq 0 && -f "$PID_FILE" ]]; then
  old="$(pid_from_file "$PID_FILE" || true)"
  if [[ -n "$old" ]] && alive "$old" && [[ "$(argv_of "$old")" == *uninstall-worker* ]]; then printf '%s\n' '===== OPENBOT BOT RESULT =====' 'OPENBOT_STATUS=started' "OPENBOT_RESULT_FILE=$RESULT_FILE" 'OPENBOT_BOT_INSTRUCTION=SendToUser now: uninstall is already running; poll --bot-status in 30-60 seconds.' '===== END OPENBOT BOT RESULT ====='; exit 0; fi
fi
if [[ "$WORKER" -eq 1 && "$WORKER_ENTRY" -eq 0 ]]; then
  mkdir -p "$DATA"
  purge_arg=()
  [[ "$PURGE_SECRETS" -eq 1 ]] && purge_arg+=(--purge-secrets)
  json_write running starting 'Uninstall worker started.'
  (setsid bash -euo pipefail -c 'exec -a openbot-uninstall-worker bash "$1" "${@:2}"' bash "$0" --bot-mode-worker "${purge_arg[@]}" </dev/null >>"$LOG_FILE" 2>&1) &
  echo "$!" >"$PID_FILE"
  printf '%s\n' '===== OPENBOT BOT RESULT =====' 'OPENBOT_STATUS=started' "OPENBOT_RESULT_FILE=$RESULT_FILE" "OPENBOT_LOG_FILE=$LOG_FILE" 'OPENBOT_BOT_INSTRUCTION=SendToUser now: uninstall started; poll --bot-status in 30-60 seconds.' '===== END OPENBOT BOT RESULT ====='
  exit 0
fi
if [[ "$WORKER_ENTRY" -eq 1 ]]; then
  set +e
  uninstall_main
  code=$?
  set -e
  if [[ "$code" -ne 0 && "$WORKER_FAILURE_RECORDED" -ne 1 ]]; then
    json_write failed failed 'Uninstall failed.' 'The detached uninstall worker exited before completing.' "$(now)" || true
  fi
  rm -f "$PID_FILE"
  exit "$code"
fi
uninstall_main
rm -f "$PID_FILE"
