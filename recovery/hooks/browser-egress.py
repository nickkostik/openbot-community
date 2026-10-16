#!/usr/bin/env python3
"""Keep an explicitly selected box internet route out of a broken desktop relay."""
import hashlib
import json
import os
import pathlib
import signal
import stat
import subprocess

SD = pathlib.Path('/home/box/sand-data')
LAUNCHER = pathlib.Path('/usr/local/bin/start-egress-tunnel')
BINARY = '/usr/local/bin/sand-egress-tunnel'
SUPERVISOR_ARGV = ['bash', '/usr/local/bin/supervise-egress-tunnel', '8790', str(LAUNCHER)]
STOCK_LAUNCHER = '#!/usr/bin/env bash\nset -euo pipefail\nexec /usr/local/bin/sand-egress-tunnel server "$@"\n'
DIRECT_RULES = [{'hosts': ['*'], 'action': 'direct'}]
DIRECT_LAUNCHER = '''#!/usr/bin/env bash
set -euo pipefail
# openbot-browser-egress-v1: explicit box route; retain the native proxy and authentication
if [ -f /home/box/sand-data/openbot-browser-direct.enable ] &&
   [ -f /home/box/sand-data/openbot-local-only.enable ] &&
   [ "$(cat /home/box/sand-data/openbot-mode 2>/dev/null)" = custom ]; then
    export SAND_EGRESS_TUNNEL_RELAY_RULES='[{"hosts":["*"],"action":"direct"}]'
fi
exec /usr/local/bin/sand-egress-tunnel server "$@"
'''
LEGACY_NONE_LAUNCHER = DIRECT_LAUNCHER.replace(
    "SAND_EGRESS_TUNNEL_RELAY_RULES='[{\"hosts\":[\"*\"],\"action\":\"direct\"}]'",
    'SAND_EGRESS_TUNNEL_RELAY_RULES=none')


def direct_selected():
    try:
        return ((SD / 'openbot-browser-direct.enable').is_file()
                and (SD / 'openbot-local-only.enable').is_file()
                and (SD / 'openbot-mode').read_text().strip() == 'custom')
    except OSError:
        return False


def install_launcher(before):
    """Privileged replacement accepts only the two known scripts at this fixed path."""
    program = '''import hashlib, os, pathlib, stat, sys, tempfile
path = pathlib.Path('/usr/local/bin/start-egress-tunnel')
stock = STOCK
updated = UPDATED
legacy = LEGACY
info = path.lstat()
if os.geteuid() != 0 or not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, 1000):
    raise RuntimeError('Native egress launcher ownership changed')
current = path.read_text()
if current not in (stock, legacy, updated) or hashlib.sha256(current.encode()).hexdigest() != sys.argv[1]:
    raise RuntimeError('Native egress launcher changed concurrently')
fd, name = tempfile.mkstemp(prefix='.openbot-egress-', dir=path.parent)
try:
    with os.fdopen(fd, 'w') as handle:
        handle.write(updated)
        handle.flush()
        os.fsync(handle.fileno())
        os.fchmod(handle.fileno(), 0o755)
    if path.read_text() != current or path.lstat().st_ino != info.st_ino:
        raise RuntimeError('Native egress launcher changed before replacement')
    os.replace(name, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    pathlib.Path(name).unlink(missing_ok=True)
'''.replace('stock = STOCK', 'stock = ' + repr(STOCK_LAUNCHER)).replace(
        'updated = UPDATED', 'updated = ' + repr(DIRECT_LAUNCHER)).replace(
        'legacy = LEGACY', 'legacy = ' + repr(LEGACY_NONE_LAUNCHER))
    subprocess.run(['sudo', '-n', '/usr/bin/python3', '-c', program,
                    hashlib.sha256(before.encode()).hexdigest()],
                   check=True, capture_output=True, timeout=5)


def ensure_launcher():
    info = LAUNCHER.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid()):
        raise RuntimeError('Refusing a foreign or symlinked native egress launcher')
    before = LAUNCHER.read_text()
    if before == DIRECT_LAUNCHER:
        return False
    if before not in (STOCK_LAUNCHER, LEGACY_NONE_LAUNCHER):
        raise RuntimeError('Unknown native egress launcher; no network patch applied')
    checked = subprocess.run(['bash', '-n'], input=DIRECT_LAUNCHER, text=True,
                             capture_output=True, timeout=5)
    if checked.returncode:
        raise RuntimeError('Egress launcher syntax validation failed')
    backup = SD / 'recovery-snapshots/browser-egress'
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    saved = backup / (hashlib.sha256(before.encode()).hexdigest() + '.sh')
    try:
        with saved.open('x') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(before)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if saved.read_text() != before:
            raise RuntimeError('Native egress launcher backup differs')
    install_launcher(before)
    return True


def process_snapshot(directory):
    """Never return the bearer credential from the daemon's environment."""
    info = directory.stat()
    if info.st_uid != os.getuid():
        raise RuntimeError('Egress process ownership differs')
    identity = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
    argv = [x.decode() for x in (directory / 'cmdline').read_bytes().split(b'\0') if x]
    rules = None
    for entry in (directory / 'environ').read_bytes().split(b'\0'):
        if entry.startswith(b'SAND_EGRESS_TUNNEL_RELAY_RULES='):
            try:
                value = json.loads(entry.split(b'=', 1)[1])
                rules = 'direct' if value == DIRECT_RULES else 'other'
            except (ValueError, UnicodeError):
                rules = 'other'
    if (directory / 'stat').read_text().rsplit(')', 1)[1].split()[19] != identity[19]:
        raise RuntimeError('Egress process changed during inspection')
    return {'pid': int(directory.name), 'exe': os.readlink(directory / 'exe'),
            'argv': argv, 'ppid': int(identity[1]), 'startTime': identity[19], 'rules': rules}


def owned_tunnel(proc=pathlib.Path('/proc')):
    children = []
    for directory in proc.glob('[0-9]*'):
        try:
            # /proc for root services can be unreadable to the box user.
            # Inspect only this user's processes; never adopt foreign children.
            if directory.stat().st_uid != os.getuid():
                continue
            if os.readlink(directory / 'exe') != BINARY:
                continue
            child = process_snapshot(directory)
            if child['argv'] != [BINARY, 'server']:
                continue
            parent = process_snapshot(proc / str(child['ppid']))
            if parent['argv'] != SUPERVISOR_ARGV:
                raise RuntimeError('Native egress process is not supervised by the expected launcher')
            children.append(child)
        except FileNotFoundError:
            continue
    if len(children) > 1:
        raise RuntimeError('Multiple native egress processes; no restart applied')
    return children[0] if children else None


def ensure_direct_browser_egress():
    if not direct_selected():
        return {'state': 'not-selected'}
    changed = ensure_launcher()
    child = owned_tunnel()
    if child is None:
        return {'state': 'waiting-for-supervisor', 'launcherChanged': changed}
    if child['rules'] == 'direct':
        return {'state': 'direct', 'pid': child['pid'], 'launcherChanged': changed,
                'browserNavigationVerified': False}
    if owned_tunnel() != child or not direct_selected():
        raise RuntimeError('Native egress process or route selection changed before restart')
    os.kill(child['pid'], signal.SIGTERM)
    return {'state': 'restart-requested', 'previousPid': child['pid'], 'launcherChanged': changed,
            'browserNavigationVerified': False}


if __name__ == '__main__':
    print(json.dumps(ensure_direct_browser_egress()))
