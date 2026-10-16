#!/usr/bin/env bash
# Run from a fresh checkout on your own Grok Bot Computer.
set -euo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
case "${1:---check}" in
  --check|--install|--public-tunnel) action="${1:---check}" ;;
  *) echo 'Usage: bash quickstart.sh [--check|--install|--public-tunnel]' >&2; exit 2 ;;
esac
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 2; }
python3 - <<'PY'
import json,os,pathlib,shutil,sys
root=pathlib.Path(os.environ.get('OPENBOT_SAND_DATA','/home/box/sand-data'))
explicit=os.environ.get('OPENBOT_HOST_MAIN')
hosts=[pathlib.Path(explicit)] if explicit else [pathlib.Path('/opt/sand/sand-host/host-main.cjs'),pathlib.Path('/home/box/sand-host/host-main.cjs')]
missing=[name for name in ('bash','flock','curl','tar') if not shutil.which(name)]
parent=root if root.exists() else root.parent
owner_ok=os.getuid()!=0 and parent.exists() and parent.stat().st_uid==os.getuid()
ready=not missing and owner_ok and any(p.is_file() for p in hosts)
print(json.dumps({'ready':ready,'nativeHostFound':any(p.is_file() for p in hosts),'dataOwnerMatches':owner_ok,'missingCommands':missing,'modeChanged':False,'stateChanged':False}))
if not ready:
 print('Run on the authenticated Grok Bot Computer as its normal data owner, with the required commands installed.',file=sys.stderr)
 sys.exit(2)
PY
[[ "$action" != --check ]] || exit 0
export OPENBOT_SRC="$SOURCE_DIR"
if [[ "$action" == --public-tunnel ]]; then
  echo 'Starting the detached installer with an optional public control URL. Keep the URL private.'
  exec bash "$SOURCE_DIR/install.sh" --bot-mode
fi
# Existing source, mode, model, accounts, and native state remain installer authority.
export OPENBOT_TUNNEL=off OPENBOT_DEFER_HOST_BOUNCE=1
bash "$SOURCE_DIR/install.sh"
TASK_DATA="${OPENBOT_SAND_DATA:-/home/box/sand-data}"
if [[ -x "$TASK_DATA/node22/bin/node" ]]; then export PATH="$TASK_DATA/node22/bin:$PATH"; fi
bash "$SOURCE_DIR/install.sh" --bot-finalize
echo 'Panel: http://127.0.0.1:9280 (on the Computer). Any host restart waits for idle. Configure your own account and verify a real turn and tool result.'
