#!/usr/bin/env bash
# Run from a fresh checkout on your own Grok Bot Computer.
set -euo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
[[ $# -le 1 ]] || { echo 'Usage: bash quickstart.sh [--check|--install|--public-tunnel]' >&2; exit 2; }
case "${1:---check}" in
  --check|--install|--public-tunnel) action="${1:---check}" ;;
  *) echo 'Usage: bash quickstart.sh [--check|--install|--public-tunnel]' >&2; exit 2 ;;
esac
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 2; }
python3 - "$action" <<'PY'
import json,os,pathlib,shutil,stat,sys
action=sys.argv[1]
root=pathlib.Path(os.environ.get('OPENBOT_SAND_DATA','/home/box/sand-data'))
explicit=os.environ.get('OPENBOT_HOST_MAIN')
hosts=[pathlib.Path(explicit)] if explicit else [pathlib.Path('/opt/sand/sand-host/host-main.cjs'),pathlib.Path('/home/box/sand-host/host-main.cjs')]
missing=[name for name in ('bash','flock','curl','tar') if not shutil.which(name)]
parent=root if root.exists() else root.parent
owner_ok=os.getuid()!=0 and parent.exists() and parent.stat().st_uid==os.getuid()
exposure='unset'; exposure_error=False
try:
 fd=os.open(root/'openbot-expose',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as stream:
  info=os.fstat(stream.fileno())
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
   raise ValueError('Unsafe saved exposure')
  raw=stream.read(4097)
  if len(raw)>4096:raise ValueError('Oversized saved exposure')
  value=raw.decode('utf-8').strip().lower()
  if value not in ('','loopback','cloudflare','cloudflare-quick'):
   raise ValueError('Unknown saved exposure')
  exposure='cloudflare-quick' if value in ('cloudflare','cloudflare-quick') else value or 'unset'
except FileNotFoundError:pass
except (OSError,ValueError,UnicodeError):exposure_error=True
loopback_allowed=not exposure_error and exposure!='cloudflare-quick'
ready=not missing and owner_ok and any(p.is_file() for p in hosts) and not exposure_error
if action=='--install' and not loopback_allowed:ready=False
print(json.dumps({'phase':'preflight','ready':ready,'nativeHostFound':any(p.is_file() for p in hosts),'dataOwnerMatches':owner_ok,'missingCommands':missing,'savedExposure':exposure,'exposureReadable':not exposure_error,'loopbackInstallAllowed':loopback_allowed,'modeChanged':False,'stateChanged':False}))
if exposure_error:
 print('Saved exposure is unreadable, unsafe, or unknown. Preserve it and resolve the configuration before installing.',file=sys.stderr)
 sys.exit(2)
if action=='--install' and not loopback_allowed:
 print('An existing public tunnel is configured. No installation or tunnel change was made. Preserve it; use --public-tunnel only if public access is intended, or explicitly turn off the tunnel before choosing --install.',file=sys.stderr)
 sys.exit(2)
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
# Each installer invocation validates and selects its own compatible runtime.
# Do not let a stale cache hide a working system Node before finalization.
bash "$SOURCE_DIR/install.sh" --bot-finalize
echo 'Panel: http://127.0.0.1:9280 (on the Computer). Any host restart waits for idle. Configure your own account and verify a real turn and tool result.'
