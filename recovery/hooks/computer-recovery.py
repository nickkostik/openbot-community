#!/usr/bin/env python3
"""Recover the installed local harness and named tunnel after computer updates.

This is an optional, explicit local-mode installation. It never mints identities,
changes the selected provider, rewrites transcripts, or reports server dispatch
as repaired merely because the local host has been patched.
"""
import argparse
import contextlib
import datetime
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import signal
import stat
import subprocess
import tempfile
import time
import urllib.request
import uuid

SD = pathlib.Path('/home/box/sand-data')
HOST = pathlib.Path('/opt/sand/sand-host/host-main.cjs')
HERE = pathlib.Path(__file__).resolve().parent
PATCHES = ('native-routine-trigger-inject.py',
           'local-mode-inject.py', 'gate-inject.py', 'room-mirror-guard-inject.py',
           'local-routines-inject.py', 'transcript-text-inject.py', 'subagent-state-inject.py',
           'local-engine-inject.py', 'exec-timing-inject.py', 'window-readiness-inject.py',
           'store-status-inject.py', 'store-request-compression-inject.py', 'store-byte-upload-inject.py', 'store-chrome-snapshot-inject.py',
           'local-publication-inject.py')
PACK_DIR = pathlib.Path('/tmp/sand-box-store-pack')
HIBERNATE_HOOK = pathlib.Path('/var/run/anyrun-hibernate-hook')
FLUSH_CHANNEL_DIR = pathlib.Path('/run/sand-box-store-flush')
FLUSH_CHANNEL_CONTRACT = (
    'SAND_BOX_STORE_FLUSH_FIFO_DIR = "/run/sand-box-store-flush";',
    'var BoxStoreHibernateHookService = class {',
    'this.fifoDir = deps.fifoDir ?? SAND_BOX_STORE_FLUSH_FIFO_DIR;',
    'if (provisioned && (0, import_node_fs26.existsSync)(this.hookPath)) {',
)
FLUSH_CHANNEL_PREPARE = '''import json,os,stat,sys
def prepare_channel(parent, uid, gid, *, parent_uid=0):
    name = 'sand-box-store-flush'
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open(parent, flags)
    try:
        parent_info = os.fstat(parent_fd)
        if parent_info.st_uid != parent_uid or parent_info.st_mode & 0o022:
            raise RuntimeError('Untrusted native runtime directory')
        created = False
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        channel_fd = os.open(name, flags, dir_fd=parent_fd)
        try:
            info = os.fstat(channel_fd)
            if info.st_uid not in (0, uid):
                raise RuntimeError('Foreign native flush channel')
            if created:
                try:
                    os.fchown(channel_fd, uid, gid)
                    os.fchmod(channel_fd, 0o700)
                    os.fsync(channel_fd)
                except BaseException:
                    held = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    if (held.st_dev, held.st_ino) == (info.st_dev, info.st_ino):
                        os.rmdir(name, dir_fd=parent_fd)
                    raise
            return {'state': 'created' if created else 'existing'}
        finally:
            os.close(channel_fd)
    finally:
        os.close(parent_fd)
if __name__ == '__main__':
    uid,gid = map(int, sys.argv[1:])
    if uid < 0 or gid < 0:
        raise RuntimeError('Invalid runtime owner')
    print(json.dumps(prepare_channel('/run', uid, gid)))
'''
HIBERNATE_SCRIPT = '''#!/usr/bin/python3
# openbot-computer-recovery: flush through the authenticated native gateway
import json, pathlib, sys, urllib.request
try:
    token = json.loads(pathlib.Path('/home/box/sand-data/gateway.json').read_text())['token']
    request = urllib.request.Request('http://127.0.0.1:1340/api/snapshotBoxStoreNow',
        data=b'{"includeIdleOnly":true}',
        headers={'content-type':'application/json','authorization':'Bearer '+token})
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    sys.exit(0 if result.get('ok') is True else 1)
except Exception:
    sys.exit(1)
'''
PREVIOUS_HIBERNATE_SCRIPT = HIBERNATE_SCRIPT.replace('timeout=180', 'timeout=55')
HOOK_REPLACE = '''import hashlib,os,pathlib,stat,sys
stage,target=map(pathlib.Path,sys.argv[1:3]);digest=sys.argv[3]
for path in (stage,target):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0:
        raise RuntimeError('Refusing a foreign shutdown hook')
fd=os.open(target,os.O_RDONLY|os.O_NOFOLLOW)
try:
    if hashlib.sha256(os.read(fd,4096)).hexdigest()!=digest:
        raise RuntimeError('Shutdown hook changed before replacement')
finally:os.close(fd)
os.replace(stage,target)
'''


def atomic_bytes(path, data, expected=None, mode=0o600):
    path = pathlib.Path(path)
    fd, name = tempfile.mkstemp(prefix='.openbot-recovery-', suffix=path.suffix, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        if expected is not None and path.read_bytes() != expected:
            raise RuntimeError('Concurrent file change; no replacement applied')
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


@contextlib.contextmanager
def owned_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Recovery lock must be an owned regular file')
        yield fd
    finally:
        os.close(fd)


def load_patch(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.patch


def local_engine():
    spec = importlib.util.spec_from_file_location('local_engine', HERE / 'local-engine.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patched(source):
    for name in PATCHES:
        source = load_patch(name)(source)
    return source


def repair_bundle(path, backup, check, before_replace=lambda: None):
    """No live write until every transformation and syntax check has succeeded."""
    before = path.read_bytes()
    engine = local_engine()
    current = before.decode('utf8')
    selected = engine.select_source(SD, path, current)
    # Owned injectors may postdate the pinned engine. Compare against their
    # complete canonical output, so a payload refresh cannot undo those
    # patches or cause recovery and reconcile to fight over the stamp.
    updated = engine.preserve_payload_stamp(patched(selected), current).encode('utf8')
    if updated == before:
        return False
    check(updated)
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    saved = backup / (path.name + '.' + hashlib.sha256(before).hexdigest()[:16] + '.gz')
    if not saved.exists():
        atomic_bytes(saved, gzip.compress(before))
    before_replace()
    atomic_bytes(path, updated, expected=before, mode=path.stat().st_mode & 0o777)
    return True


def repair_local_profiles(before_replace=lambda: None):
    """Repair every existing UUID profile in place; never create or rebind an ID."""
    root = SD / 'agents'
    result = {'profileCount': 0, 'boxProfiles': 0, 'changedProfileIds': [], 'errors': []}
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError('Local agent root is missing or symlinked')
    for directory in sorted(root.iterdir()):
        try:
            aid = str(uuid.UUID(directory.name))
        except ValueError:
            continue
        if aid != directory.name:
            continue
        path = directory / 'profile.json'
        try:
            if directory.is_symlink():
                raise RuntimeError('Symlinked agent directory')
            if not directory.is_dir():
                continue
            info = path.lstat()
        except FileNotFoundError:
            # Deleted/missing profiles stay absent; do not resurrect tombstones.
            continue
        except (OSError, RuntimeError) as error:
            result['errors'].append({'agentId': aid, 'errorType': type(error).__name__})
            continue
        result['profileCount'] += 1
        try:
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError('Profile is not an owned regular file')
            before = path.read_bytes()
            profile = json.loads(before)
            if not isinstance(profile, dict) or profile.get('harness') not in (None, 'temporal', 'box'):
                raise RuntimeError('Invalid profile harness')
            if any(key in profile and profile[key] != aid for key in ('id', 'agentId', 'legacyAgentId')):
                raise RuntimeError('Profile identity differs from directory')
            if profile.get('harness') != 'box':
                backup = SD / 'recovery-snapshots/local-fleet-profiles'
                backup.mkdir(parents=True, exist_ok=True, mode=0o700)
                saved = backup / (aid + '.' + hashlib.sha256(before).hexdigest()[:16] + '.json')
                if not saved.exists():
                    atomic_bytes(saved, before)
                updated = (json.dumps({**profile, 'harness': 'box'}, ensure_ascii=False, indent=2) + '\n').encode()
                before_replace()
                atomic_bytes(path, updated, expected=before, mode=stat.S_IMODE(info.st_mode))
                result['changedProfileIds'].append(aid)
            result['boxProfiles'] += 1
        except (OSError, ValueError, RuntimeError) as error:
            result['errors'].append({'agentId': aid, 'errorType': type(error).__name__})
    return result


def process_args(proc=pathlib.Path('/proc')):
    for p in proc.glob('[0-9]*/cmdline'):
        try:
            yield int(p.parent.name), p.read_bytes().split(b'\0')
        except OSError:
            continue


def host_pids():
    return [pid for pid, args in process_args()
            if args[0] == b'/exec-daemon/node' and os.fsencode(HOST) in args and b'guard' not in args]


def gateway_status():
    token = json.loads((SD / 'gateway.json').read_text())['token']
    request = urllib.request.Request('http://127.0.0.1:1340/api/getHostStatus', data=b'{}',
                                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.load(response)


def ensure_native_flush_channel():
    """Pre-provision the native FIFO directory without replacing its hook."""
    try:
        info = HOST.lstat()
    except FileNotFoundError:
        return {'state': 'unsupported-host'}
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Refusing an unowned native flush contract')
    try:
        channel = FLUSH_CHANNEL_DIR.lstat()
    except FileNotFoundError:
        channel = None
    if channel is not None:
        if not stat.S_ISDIR(channel.st_mode) or channel.st_uid not in (0, os.getuid()):
            raise RuntimeError('Refusing a foreign or symlinked native flush channel')
        if not os.access(FLUSH_CHANNEL_DIR, os.R_OK | os.W_OK | os.X_OK):
            raise RuntimeError('Existing native flush channel is not accessible')
        return {'state': 'existing'}
    # Existing owned directories need no mutation or repeated full-bundle read.
    fd = os.open(HOST, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r', encoding='utf8') as handle:
        native = os.fstat(handle.fileno())
        if not stat.S_ISREG(native.st_mode) or native.st_uid != os.getuid() or native.st_size > 48 * 1024 * 1024:
            raise RuntimeError('Refusing an unowned or oversized native flush contract')
        source = handle.read(48 * 1024 * 1024 + 1)
    if any(source.count(anchor) != 1 for anchor in FLUSH_CHANNEL_CONTRACT):
        return {'state': 'unsupported-host'}
    result = subprocess.run(['sudo', '-n', '/usr/bin/python3', '-c', FLUSH_CHANNEL_PREPARE,
                             str(os.getuid()), str(os.getgid())],
                            check=True, capture_output=True, text=True, timeout=5)
    prepared = json.loads(result.stdout)
    if prepared not in ({'state': 'created'}, {'state': 'existing'}):
        raise RuntimeError('Unexpected native flush preparation result')
    # Another owner may have created the path while sudo started. Inspect its
    # accessibility as the host user, never rewrite an existing directory.
    channel = FLUSH_CHANNEL_DIR.lstat()
    if not stat.S_ISDIR(channel.st_mode) or channel.st_uid not in (0, os.getuid()) or not os.access(
            FLUSH_CHANNEL_DIR, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError('Prepared native flush channel is not accessible')
    return prepared


def ensure_backup_permissions():
    """Repair named runtime artifacts, never sweep ownership of user data."""
    try:
        entry = PACK_DIR.lstat()
    except FileNotFoundError:
        PACK_DIR.mkdir(mode=0o700)
        entry = PACK_DIR.lstat()
    if not stat.S_ISDIR(entry.st_mode) or entry.st_uid not in (0, os.getuid()):
        raise RuntimeError('Refusing a foreign or symlinked backup packing directory')
    if entry.st_uid != os.getuid():
        subprocess.run(['sudo', '-n', 'chown', '--no-dereference',
                        str(os.getuid()) + ':' + str(os.getgid()), str(PACK_DIR)],
                       check=True, capture_output=True, timeout=5)
    PACK_DIR.chmod(0o700)
    # The stock hook attempts root-owned /run operations as box on this image.
    # Preserve a native/foreign hook. Upgrade only our exact previous fallback.
    try:
        hook = HIBERNATE_HOOK.lstat()
    except FileNotFoundError:
        hook = None
    if hook is not None:
        if not stat.S_ISREG(hook.st_mode) or hook.st_uid != 0:
            raise RuntimeError('Refusing to replace a foreign shutdown hook')
        previous = HIBERNATE_HOOK.read_bytes()
        if previous != PREVIOUS_HIBERNATE_SCRIPT.encode():
            return {'packDirectory': 'writable', 'hibernateHook': 'existing',
                    'nativeFlushChannel': ensure_native_flush_channel()}
        saved = SD / 'recovery-snapshots/backup-hooks' / (hashlib.sha256(previous).hexdigest() + '.py')
        saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not saved.exists():
            atomic_bytes(saved, previous)
    fd, name = tempfile.mkstemp(prefix='.openbot-backup-hook-', dir=SD)
    stage = HIBERNATE_HOOK.parent / ('.openbot-backup-hook-' + uuid.uuid4().hex)
    try:
        with os.fdopen(fd, 'w') as source:
            source.write(HIBERNATE_SCRIPT)
        subprocess.run(['sudo', '-n', 'install', '-o', '0', '-g', '0', '-m', '755', name, str(stage)],
                       check=True, capture_output=True, timeout=5)
        if hook is None:
            # Exclusive link: an intervening platform hook is never overwritten.
            result = subprocess.run(['sudo', '-n', 'ln', str(stage), str(HIBERNATE_HOOK)],
                                    capture_output=True, timeout=5)
            if result.returncode and not HIBERNATE_HOOK.exists():
                raise RuntimeError('Shutdown hook installation failed')
        else:
            subprocess.run(['sudo', '-n', '/usr/bin/python3', '-c', HOOK_REPLACE,
                            str(stage), str(HIBERNATE_HOOK), hashlib.sha256(previous).hexdigest()],
                           check=True, capture_output=True, timeout=5)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)
        subprocess.run(['sudo', '-n', 'rm', '-f', str(stage)], capture_output=True, timeout=5)
    return {'packDirectory': 'writable', 'hibernateHook': 'installed' if hook is None else 'updated',
            'nativeFlushChannel': ensure_native_flush_channel()}


def ensure_named_tunnel():
    config = SD / 'cloudflared/openbot-ui.yml'
    binary = SD / 'bin/cloudflared'
    if not config.is_file() or not binary.is_file():
        return {'state': 'not-configured'}
    data = json.loads(config.read_text())  # JSON is valid YAML for cloudflared.
    creds = pathlib.Path(data.get('credentials-file', ''))
    if creds.parent != config.parent or not creds.is_file():
        raise RuntimeError('Named tunnel credentials do not match the saved installation')
    if not isinstance(data.get('tunnel'), str) or not data['tunnel']:
        raise RuntimeError('Missing saved named tunnel identity')
    owned = [pid for pid, args in process_args()
             if args[0] == os.fsencode(binary) and os.fsencode(config) in args]
    if owned:
        return {'state': 'running', 'pid': owned[0]}
    log = config.parent / 'openbot-ui.log'
    if log.exists() and log.stat().st_size > 4 * 1024 * 1024:
        os.replace(log, log.with_suffix('.log.1'))
    with log.open('ab', buffering=0) as output:
        child = subprocess.Popen([str(binary), 'tunnel', '--config', str(config), '--no-autoupdate', 'run'],
                                 stdout=output, stderr=output, start_new_session=True)
    atomic_bytes(config.parent / 'openbot-ui.pid', (str(child.pid) + '\n').encode())
    return {'state': 'started', 'pid': child.pid}


def owned_ui_listener(pid, ui, proc=pathlib.Path('/proc')):
    """Prove the published UI process owns the exact loopback LISTEN socket."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    directory = proc / str(pid)

    def identity():
        try:
            if directory.stat().st_uid != os.getuid():
                return None
            args = (directory / 'cmdline').read_bytes().split(b'\0')
            if os.fsencode(ui) not in args:
                return None
            # The process name may contain spaces or parentheses. Field 22
            # (start time) identifies a process even when its PID is reused.
            raw = (directory / 'stat').read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            if len(fields) < 20 or fields[0] == 'Z':
                return None
            return fields[19], args
        except FileNotFoundError:
            return None

    before = identity()
    if before is None:
        return False
    inodes = set()
    for row in (proc / 'net/tcp').read_text().splitlines()[1:]:
        fields = row.split()
        if len(fields) >= 10 and fields[1] == '0100007F:2440' and fields[3] == '0A':
            inodes.add('socket:[' + fields[9] + ']')
    try:
        for descriptor in (directory / 'fd').iterdir():
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue  # An unrelated descriptor closed during inspection.
            if target in inodes:
                return identity() == before
    except FileNotFoundError:
        return False
    return False


def ensure_openbot_service():
    """Recover the unified panel/hop through reconcile, never a hop-only bind."""
    # Packaged installs keep the helper beside their own source tree. Copied
    # native hooks retain the existing factory repository fallback.
    repos = [SD / 'openbot']
    if HERE.name == 'hooks' and HERE.parent.name == 'recovery':
        repos.insert(0, HERE.parent.parent)
    repo = next((root for root in repos
                 if (root / 'src/cli.ts').is_file() and (root / 'src/ui/server.ts').is_file()),
                SD / 'openbot')
    cli = repo / 'src/cli.ts'
    ui = repo / 'src/ui/server.ts'
    node = SD / 'node22/bin/node'
    if not all(p.is_file() for p in (cli, ui, node)):
        return {'state': 'not-installed'}
    try:
        pid = int((SD / 'openbot-ui.pid').read_text().strip())
    except (OSError, ValueError):
        pid = None
    # A slow HTTP response is not evidence that the owned listener died.
    # Reinstalling it kills every in-flight turn and its compactor, turning
    # transient event-loop pressure into a continuous restart loop.
    if pid is not None and owned_ui_listener(pid, ui):
        try:
            with urllib.request.urlopen('http://127.0.0.1:9280/api/pause', timeout=2) as response:
                ready = response.status == 200 and isinstance(json.load(response).get('paused'), bool)
        except (OSError, ValueError):
            ready = False
        # Recheck the process and socket after the probe, including PID reuse.
        if owned_ui_listener(pid, ui):
            return {'state': 'running' if ready else 'degraded', 'pid': pid,
                    **({} if ready else {'reason': 'health-probe-unavailable'})}
    result = subprocess.run([str(node), '--experimental-strip-types', str(cli), 'install',
                             '--host-main', str(HOST), '--sand-data', str(SD),
                             '--defer-host-bounce', '--keep-service', '--json'], cwd=repo,
                            capture_output=True, timeout=40)
    try:
        outcome = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError('Unified OpenBot reconcile did not return a valid result')
    if result.returncode or outcome.get('ok') is not True:
        raise RuntimeError('Unified OpenBot reconcile refused service recovery')
    return {'state': 'reconciled', 'hostReloadDeferred': outcome.get('hostBounce') == 'deferred'}


def ensure_browser_egress():
    if not (SD / 'openbot-browser-direct.enable').is_file():
        return {'state': 'not-selected'}
    spec = importlib.util.spec_from_file_location('browser_egress', HERE / 'browser-egress.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ensure_direct_browser_egress()


def ensure_bot_context():
    """Refresh only the nonsecret model-readable identity projection."""
    spec = importlib.util.spec_from_file_location('bot_context', HERE / 'publish-bot-context.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SD = SD
    return module.publish()


def ensure_retired_windows():
    """Release retired desktop leases under memory or window-capacity pressure."""
    spec = importlib.util.spec_from_file_location('retired_windows', HERE / 'reap-retired-windows.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SD = SD
    return module.reap(limit=2, pressure_only=True)


def ensure_window_readiness():
    spec = importlib.util.spec_from_file_location('window_readiness', HERE / 'window-readiness-inject.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SD = SD
    return module.ensure_native_script()


def check_server_roster():
    spec = importlib.util.spec_from_file_location('server_roster', HERE / 'server-roster-check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check(SD, atomic_bytes)


def restore_official_stock(check, before_replace=lambda: None):
    """Leave native updates alone; remove only our composed BOX runtime.

    A local generation's stock.cjs still contains the BOX ports. Official must
    restore the exact vendor artifact instead of merely peeling its hop wrap.
    """
    engine = local_engine()
    marker = '/* openbot-latest-box-compatibility-v1 */'
    targets = {}
    for path in (SD / 'host-main.cjs.pre-openbot', HOST):
        if path.exists():
            raw = engine.owned_bytes(path, engine.MAX_BYTES)
            if marker.encode() in raw:
                targets[path] = raw
    if not targets:
        return []
    pin = engine.policy(SD)
    authority = pin
    # A failed local canary may roll the BOX pin back. That must not downgrade
    # Official: the follower's authenticated vendor capture is independent.
    receipt = SD / 'openbot-upstream-sync-state.json'
    if receipt.exists():
        captured = json.loads(engine.owned_bytes(receipt, 262144))
        if captured.get('upstreamVersion') and captured.get('upstreamSourceSha256'):
            authority = captured
    version = authority.get('upstreamVersion') if authority else None
    digest = authority.get('upstreamSourceSha256') if authority else None
    if not isinstance(version, str) or not re.fullmatch('[0-9a-f]{7,40}', version) or (
            not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
        raise RuntimeError('No verified vendor authority for Official restoration')
    folder = SD / 'openbot-upstream/releases' / version
    for parent in (folder.parent.parent, folder.parent, folder):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted saved vendor directory')
    metadata = json.loads(engine.owned_bytes(folder / 'vendor.json', 16384))
    stock = engine.owned_bytes(folder / 'host-main.cjs', engine.MAX_BYTES)
    if metadata.get('sourceSha256') != digest or hashlib.sha256(stock).hexdigest() != digest:
        raise RuntimeError('Vendor source hash differs; Official restoration refused')
    if marker.encode() in stock or b'/* openbot-stock-wrap */' in stock:
        raise RuntimeError('Official vendor artifact contains local execution ports')
    check(stock)
    backup = SD / 'recovery-snapshots/computer-update-bundles'
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    changed = []
    for path, before in targets.items():
        saved = backup / (path.name + '.' + hashlib.sha256(before).hexdigest()[:16] + '.gz')
        if not saved.exists():
            atomic_bytes(saved, gzip.compress(before))
        if path == HOST:
            before_replace()
        if engine.owned_bytes(SD / 'openbot-mode', 64).decode().strip() != 'official':
            raise RuntimeError('Mode changed before Official restoration')
        atomic_bytes(path, stock, expected=before, mode=stat.S_IMODE(path.stat().st_mode))
        changed.append(path.name)
    if HOST in targets:
        version_path = HOST.parent / 'version'
        before = engine.owned_bytes(version_path, 128) if version_path.exists() else None
        if engine.owned_bytes(SD / 'openbot-mode', 64).decode().strip() != 'official':
            raise RuntimeError('Mode changed before Official version restoration')
        atomic_bytes(version_path, (version + '\n').encode(), expected=before,
                     mode=stat.S_IMODE(version_path.stat().st_mode) if before is not None else 0o600)
    return changed


def repair_official_store_status(check, before_replace=lambda: None):
    """Repair native backup metadata/transport; never choose a BOX engine."""
    engine = local_engine()
    before = engine.owned_bytes(HOST, engine.MAX_BYTES)
    source = load_patch('store-status-inject.py')(before.decode())
    source = load_patch('store-request-compression-inject.py')(source)
    source = load_patch('store-byte-upload-inject.py')(source)
    updated = load_patch('store-chrome-snapshot-inject.py')(source).encode()
    if updated == before:
        return False
    check(updated)
    if engine.owned_bytes(SD / 'openbot-mode', 64).decode().strip() != 'official':
        raise RuntimeError('Mode changed before native backup metadata repair')
    backup = SD / 'recovery-snapshots/computer-update-bundles'
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    saved = backup / (HOST.name + '.' + hashlib.sha256(before).hexdigest()[:16] + '.gz')
    if not saved.exists():
        atomic_bytes(saved, gzip.compress(before))
    before_replace()
    atomic_bytes(HOST, updated, expected=before, mode=stat.S_IMODE(HOST.stat().st_mode))
    return True


def ensure_upstream_sync():
    spec = importlib.util.spec_from_file_location('local_upstream_sync', HERE / 'local-upstream-sync.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.maybe_start(SD)


def ensure_custom_dispatch():
    spec = importlib.util.spec_from_file_location('custom_dispatch', HERE / 'custom-dispatch-recovery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.maybe_start(SD)


def publication_reload():
    spec = importlib.util.spec_from_file_location('publication_reload', HERE / 'publication-reload.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.consume(SD, HOST, host_pids, gateway_status)


def tick():
    mode = (SD / 'openbot-mode').read_text().strip() if (SD / 'openbot-mode').exists() else ''
    if not (SD / 'openbot-local-only.enable').exists() or mode not in ('custom', 'official'):
        return {'state': 'disabled'}
    state_path = SD / 'computer-recovery-state.json'
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    # Tunnel recovery is independent of a platform patch anchor changing.
    state['tunnel'] = ensure_named_tunnel()
    try:
        state['openbotService'] = ensure_openbot_service()
    except Exception as error:
        state['openbotService'] = {'state': 'failed', 'errorType': type(error).__name__}
    try:
        state['browserEgress'] = ensure_browser_egress()
    except Exception as error:
        state['browserEgress'] = {'state': 'failed', 'errorType': type(error).__name__}
    try:
        state['botContext'] = ensure_bot_context()
    except Exception as error:
        # A missing peer or refusing a private/symlinked file must never stop
        # the native host, modify identities, or interrupt service recovery.
        state['botContext'] = {'state': 'failed', 'errorType': type(error).__name__}
    try:
        state['retiredWindows'] = ensure_retired_windows()
    except Exception as error:
        state['retiredWindows'] = {'state': 'failed', 'errorType': type(error).__name__}
    try:
        state['windowReadiness'] = ensure_window_readiness()
    except Exception as error:
        state['windowReadiness'] = {'state': 'failed', 'errorType': type(error).__name__}
    try:
        state['upstreamSync'] = ensure_upstream_sync()
    except Exception as error:
        state['upstreamSync'] = {'state': 'failed', 'errorType': type(error).__name__}
    try:
        state['backupRepair'] = ensure_backup_permissions()
        if state['backupRepair'].get('nativeFlushChannel', {}).get('state') == 'created':
            state['pendingOfficialStockReload' if mode == 'official' else 'pendingHostReload'] = True
            atomic_bytes(state_path, (json.dumps(state, indent=2) + '\n').encode())
    except Exception as error:
        # Permissions must not prevent delivery/tunnel recovery when sudo is absent.
        state['backupRepair'] = {'state': 'failed', 'errorType': type(error).__name__}
    def check(source):
        result = subprocess.run(['/exec-daemon/node', '--check'], input=source, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Native syntax validation failed; no bundle replaced')
    # Native Official updates are untouched. A previous composed local runtime
    # needs its actual vendor counterpart before stock delivery can resume.
    if mode == 'official':
        def arm_stock_reload():
            state['pendingOfficialStockReload'] = True
            atomic_bytes(state_path, (json.dumps(state, indent=2) + '\n').encode())
        changed = restore_official_stock(check, arm_stock_reload)
        try:
            status_changed = repair_official_store_status(check, arm_stock_reload)
            state['storeStatusRepair'] = {'state': 'applied' if status_changed else 'current'}
            if status_changed:
                changed.append(HOST.name)
        except RuntimeError as error:
            # Unknown upstream status contracts must not prevent stock delivery.
            state['storeStatusRepair'] = {'state': 'compatibility-required', 'errorType': type(error).__name__}
        if state.get('pendingOfficialStockReload'):
            pids = host_pids()
            if len(pids) == 1 and gateway_status().get('isBusy') is False:
                if host_pids() != pids or (SD / 'openbot-mode').read_text().strip() != 'official':
                    raise RuntimeError('Native owner or mode changed before Official reload')
                os.kill(pids[0], signal.SIGTERM)
                state['pendingOfficialStockReload'] = False
                state['pendingHostReload'] = False
                state['lastReloadedPid'] = pids[0]
        state.update({'atUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      'state': 'official-stock', 'changedBundles': changed,
                      'localEngine': local_engine().summary(SD), 'serverDispatchVerified': False})
        atomic_bytes(state_path, (json.dumps(state, indent=2) + '\n').encode())
        return state
    changes = []
    def arm_reload():
        state['pendingHostReload'] = True
        atomic_bytes(state_path, (json.dumps(state, indent=2) + '\n').encode())
    for path in (SD / 'host-main.cjs.pre-openbot', HOST):
        if path.exists() and repair_bundle(path, SD / 'recovery-snapshots/computer-update-bundles', check,
                                        arm_reload if path == HOST else lambda: None):
            changes.append(path.name)
            if path == HOST:
                state['pendingHostReload'] = True
    # Profile reads are guarded in the loaded native bundle. Rewriting an
    # on-disk harness does not require killing the host every time the server
    # mirror changes that file; only an unloaded bundle/publication does.
    state['profiles'] = repair_local_profiles()
    try:
        state['serverRoster'] = check_server_roster()
    except Exception as error:
        state['serverRoster'] = {'state': 'unknown', 'errorType': type(error).__name__, 'nativeSendVerified': False}
    try:
        state['customDispatch'] = ensure_custom_dispatch()
    except Exception as error:
        state['customDispatch'] = {'phase': 'failed', 'errorType': type(error).__name__, 'nativeAppDeliveryVerified': False}
    state['publicationReload'] = publication_reload()
    if state['publicationReload']['phase'] == 'signal-issued':
        state['pendingHostReload'] = False
        state['lastReloadedPid'] = state['publicationReload']['pid']
    if state.get('pendingHostReload') and state['publicationReload']['phase'] in ('absent', 'acknowledged'):
        pids = host_pids()
        if len(pids) == 1 and gateway_status().get('isBusy') is False:
            # Prove ownership again immediately before signalling a recycled PID.
            if host_pids() != pids or (SD / 'openbot-mode').read_text().strip() != 'custom':
                raise RuntimeError('Native owner or mode changed before Custom reload')
            os.kill(pids[0], signal.SIGTERM)
            state['pendingHostReload'] = False
            state['lastReloadedPid'] = pids[0]
    state.update({'atUtc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'state': 'server-roster-drift' if state['serverRoster'].get('state') == 'drift' else 'local-patched', 'changedBundles': changes,
                  'localEngine': local_engine().summary(SD), 'serverDispatchVerified': False})
    atomic_bytes(state_path, (json.dumps(state, indent=2) + '\n').encode())
    return state


def absolute_host(value):
    path = pathlib.Path(value)
    if not path.is_absolute() or '..' in path.parts or any(c in value for c in ('\0', '\n', '\r')):
        raise argparse.ArgumentTypeError('Native host must be an absolute path without traversal')
    return path


def main(argv=None):
    global HOST
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', type=absolute_host, default=HOST)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args(argv)
    HOST = args.host
    SD.mkdir(parents=True, exist_ok=True)
    # One watcher lives at a time, but the mutation lock is per tick so the
    # release follower can verify a tentative engine between ordinary checks.
    with owned_lock(SD / 'computer-recovery-watch.lock') as watcher:
        try:
            fcntl.flock(watcher, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if args.watch:
            atomic_bytes(SD / 'computer-recovery.pid', (str(os.getpid()) + '\n').encode())
        while True:
            try:
                with owned_lock(SD / 'computer-recovery.lock') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        state = {'state': 'upgrade-in-progress'}
                    else:
                        state = tick()
                if not args.watch or state.get('changedBundles') or state.get('profiles', {}).get('changedProfileIds') or state.get('profiles', {}).get('errors') or state.get('tunnel', {}).get('state') == 'started':
                    print(json.dumps(state), flush=True)
            except Exception as error:
                # Never include authenticated headers, credentials, or native source in logs.
                print(json.dumps({'state': 'repair-failed', 'errorType': type(error).__name__,
                                  'message': str(error) if isinstance(error, RuntimeError) else 'Recovery check failed'}), flush=True)
                if not args.watch:
                    raise SystemExit(1)
            if not args.watch:
                return
            time.sleep(15)


if __name__ == '__main__':
    main()
