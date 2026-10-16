#!/usr/bin/env python3
"""Keep optional Computer Tailscale access independent of the execution harness."""
import argparse
import fcntl
import hashlib
import io
import ipaddress
import json
import os
import pathlib
import platform
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request

MAX_BINARY = 100 * 1024 * 1024
VERSION = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+$')
KERNEL_TUN = 'openbot-ts0'
HOST_NETWORKS = [ipaddress.ip_network(cidr) for cidr in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    '198.18.0.0/15', 'fc00::/7', 'fe80::/10')]
WATCH_INTERVAL = 15


def directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Tailscale directory must belong to the current user')
    path.chmod(0o700)
    return path


def owned(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise RuntimeError('Untrusted Tailscale file')
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def atomic(path, data, mode=0o600):
    if path.exists() or path.is_symlink():
        owned(path, MAX_BINARY)
    fd, name = tempfile.mkstemp(prefix='.tailscale-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno()); os.fchmod(out.fileno(), mode)
        os.replace(name, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)


def fetch(url, limit):
    with urllib.request.urlopen(url, timeout=25) as response:
        if not response.geturl().startswith('https://dl.tailscale.com/stable/'):
            raise RuntimeError('Unexpected Tailscale download origin')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('Tailscale download exceeds its limit')
    return data


def architecture():
    name = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if name is None:
        raise RuntimeError('Unsupported Tailscale architecture')
    return name


def latest_version(arch, download=fetch):
    index = download('https://dl.tailscale.com/stable/', 1024 * 1024).decode('utf8')
    versions = re.findall(r'tailscale_([0-9]+\.[0-9]+\.[0-9]+)_' + arch + r'\.tgz', index)
    if not versions:
        raise RuntimeError('Stable Tailscale package listing changed')
    return max(versions, key=lambda value: tuple(map(int, value.split('.'))))


def install(root, version, arch, download=fetch):
    if not VERSION.fullmatch(version) or arch not in ('amd64', 'arm64'):
        raise RuntimeError('Invalid Tailscale package selector')
    url = 'https://dl.tailscale.com/stable/tailscale_' + version + '_' + arch + '.tgz'
    expected = download(url + '.sha256', 4096).decode('ascii').split()[0]
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise RuntimeError('Invalid Tailscale package checksum')
    package = download(url, 70 * 1024 * 1024)
    if hashlib.sha256(package).hexdigest() != expected:
        raise RuntimeError('Tailscale package checksum differs')
    versions = directory(root / 'versions')
    folder = directory(versions / version)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(package), mode='r:gz') as archive:
        for name in ('tailscale', 'tailscaled'):
            entries = [entry for entry in archive if entry.name == 'tailscale_' + version + '_' + arch + '/' + name]
            if len(entries) != 1 or not entries[0].isfile() or not 0 < entries[0].size <= MAX_BINARY:
                raise RuntimeError('Invalid Tailscale executable entry')
            raw = archive.extractfile(entries[0]).read(MAX_BINARY + 1)
            if len(raw) != entries[0].size:
                raise RuntimeError('Truncated Tailscale executable')
            files[name] = raw
    # Validate both executables before publishing either one or the manifest.
    for name, raw in files.items():
        atomic(folder / name, raw, 0o700)
    manifest = {'schemaVersion': 1, 'version': version, 'arch': arch,
                'packageSha256': expected, 'checkedAtMs': int(time.time() * 1000),
                'binaries': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    atomic(root / 'current.json', (json.dumps(manifest) + '\n').encode())
    return manifest


def executable(root, manifest, name):
    if not isinstance(manifest, dict) or not isinstance(manifest.get('binaries'), dict):
        raise RuntimeError('Invalid persistent Tailscale manifest')
    version = manifest.get('version', '')
    if not isinstance(version, str) or not VERSION.fullmatch(version) or manifest.get('schemaVersion') != 1:
        raise RuntimeError('Invalid persistent Tailscale manifest')
    versions = directory(root / 'versions')
    folder = directory(versions / version)
    path = folder / name
    digest = hashlib.sha256(owned(path, MAX_BINARY)).hexdigest()
    if digest != manifest.get('binaries', {}).get(name):
        raise RuntimeError('Persistent Tailscale binary changed')
    return path


def process_identity(pid):
    proc = pathlib.Path('/proc', str(pid))
    try:
        return {'exe': str((proc / 'exe').resolve(strict=True)),
                'argv': (proc / 'cmdline').read_bytes().decode().split('\0'), 'uid': proc.stat().st_uid}
    except PermissionError:
        # File capabilities make Linux mark tailscaled non-dumpable. Read only
        # this PID's identity with sudo rather than weakening /proc protection
        # or making the daemon/root-owned node keys belong to root.
        script = """import json,pathlib,sys
p=pathlib.Path('/proc',str(int(sys.argv[1])))
try: print(json.dumps({'exe':str((p/'exe').resolve(strict=True)),
 'argv':(p/'cmdline').read_bytes().decode().split(chr(0)),'uid':p.stat().st_uid}))
except FileNotFoundError: print('null')
"""
        result = subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/python3', '-c', script, str(pid)],
                                capture_output=True, text=True, timeout=8, check=True)
        identity = json.loads(result.stdout)
        if identity is None:
            raise FileNotFoundError('Tailscale process exited')
        if not isinstance(identity, dict) or not isinstance(identity.get('argv'), list):
            raise RuntimeError('Invalid privileged process identity')
        return identity


def live_pid(root):
    try:
        pid = int(owned(root / 'daemon.pid', 64).decode().strip())
        if pid <= 1:
            return None
        identity = process_identity(pid)
        actual = pathlib.Path(identity['exe'])
        expected = '--state=' + str(root / 'state/tailscaled.state')
        if actual.parent.parent != root / 'versions' or actual.name != 'tailscaled' or expected not in identity['argv'] or identity['uid'] != os.getuid():
            raise RuntimeError('Tailscale PID belongs to another process')
        return pid
    except (FileNotFoundError, ProcessLookupError):
        return None


def network_settings(settings):
    if not isinstance(settings, dict) or settings.get('schemaVersion') != 1 or not isinstance(settings.get('enabled'), bool):
        raise RuntimeError('Invalid Tailscale recovery settings')
    mode = settings.get('networking', 'userspace')
    if mode not in ('userspace', 'kernel'):
        raise RuntimeError('Invalid Tailscale networking mode')
    for name in ('autoUpdate', 'exitNodeAllowLANAccess'):
        if name in settings and not isinstance(settings[name], bool):
            raise RuntimeError('Invalid Tailscale recovery settings')
    exit_ip = settings.get('exitNodeIP')
    if exit_ip is not None:
        if not isinstance(exit_ip, str):
            raise RuntimeError('Invalid Tailscale exit node address')
        try:
            address = ipaddress.ip_address(exit_ip)
        except (ValueError, TypeError):
            raise RuntimeError('Invalid Tailscale exit node address') from None
        if address not in ipaddress.ip_network('100.64.0.0/10') and address not in ipaddress.ip_network('fd7a:115c:a1e0::/48'):
            raise RuntimeError('Exit node must use a Tailscale address')
        if mode != 'kernel':
            raise RuntimeError('An OS exit node requires kernel networking')
    host_routes = settings.get('hostTransportCIDRs', [])
    if not isinstance(host_routes, list) or len(host_routes) > 16:
        raise RuntimeError('Invalid host transport routes')
    for value in host_routes:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except (ValueError, TypeError):
            raise RuntimeError('Invalid host transport route') from None
        if not isinstance(value, str) or not any(network.version == allowed.version and network.subnet_of(allowed) for allowed in HOST_NETWORKS):
            raise RuntimeError('Host transport routes must be local infrastructure')
    return settings


def daemon_networking(pid):
    argv = process_identity(pid)['argv']
    if '--tun=userspace-networking' in argv:
        return 'userspace'
    if '--tun=' + KERNEL_TUN in argv:
        return 'kernel'
    raise RuntimeError('Unexpected Tailscale tunnel configuration')


def ensure_kernel_capabilities(daemon):
    if not pathlib.Path('/dev/net/tun').exists():
        raise RuntimeError('Kernel Tailscale requires /dev/net/tun')
    status = pathlib.Path('/proc/self/status').read_text()
    if re.search(r'^NoNewPrivs:\s*1$', status, re.MULTILINE):
        raise RuntimeError('Kernel Tailscale capabilities are disabled by the host')
    # Keep the daemon and its persistent node keys owned by the Computer user.
    # Grant only networking capabilities to the checksum-verified executable.
    probe = subprocess.run(['/usr/sbin/getcap', str(daemon)], capture_output=True, text=True, timeout=8, check=True)
    expected = str(daemon) + ' cap_net_admin,cap_net_raw=ep'
    if probe.stdout.strip() != expected:
        command = ['/usr/sbin/setcap', 'cap_net_admin,cap_net_raw+ep', str(daemon)]
        if os.getuid() != 0:
            command = ['/usr/bin/sudo', '-n'] + command
        subprocess.run(command, capture_output=True, text=True, timeout=8, check=True)
        probe = subprocess.run(['/usr/sbin/getcap', str(daemon)], capture_output=True, text=True, timeout=8, check=True)
        if probe.stdout.strip() != expected:
            raise RuntimeError('Kernel Tailscale capabilities did not persist')


def cli_json(base, args):
    response = subprocess.run(base + args, capture_output=True, text=True, timeout=8, check=True)
    result = json.loads(response.stdout)
    if not isinstance(result, dict):
        raise RuntimeError('Invalid Tailscale CLI response')
    return result


def ensure_exit_node(base, status, settings):
    desired = settings.get('exitNodeIP')
    if not desired or status.get('BackendState') != 'Running':
        return status
    peers = [peer for peer in (status.get('Peer') or {}).values() if desired in peer.get('TailscaleIPs', [])]
    if len(peers) != 1 or not peers[0].get('ExitNodeOption') or not peers[0].get('ID'):
        raise RuntimeError('Configured Tailscale exit node is not approved')
    peer = peers[0]
    prefs = cli_json(base, ['debug', 'prefs'])
    allow_lan = settings.get('exitNodeAllowLANAccess', True)
    if prefs.get('ExitNodeID') != peer['ID'] or prefs.get('ExitNodeAllowLANAccess') != allow_lan:
        subprocess.run(base + ['set', '--exit-node=' + desired,
            '--exit-node-allow-lan-access=' + str(allow_lan).lower()],
            capture_output=True, text=True, timeout=8, check=True)
        status = cli_json(base, ['status', '--json'])
    return status


def selected_exit_ips(status):
    selected = status.get('ExitNodeStatus') or {}
    addresses = selected.get('TailscaleIPs') or []
    if not addresses and selected.get('ID'):
        addresses = [address for peer in (status.get('Peer') or {}).values()
                     if peer.get('ID') == selected['ID'] for address in peer.get('TailscaleIPs', [])]
    # ExitNodeStatus reports network prefixes; Peer/Self report bare addresses.
    return [str(ipaddress.ip_interface(address).ip) for address in addresses]


def ensure_host_transport(settings):
    for index, cidr in enumerate(settings.get('hostTransportCIDRs', [])):
        network = ipaddress.ip_network(cidr)
        family = '-4' if network.version == 4 else '-6'
        priority = 5100 + index
        rules = json.loads(subprocess.check_output(['/usr/sbin/ip', '-j', family, 'rule'], text=True, timeout=8))
        existing = [rule for rule in rules if rule.get('priority') == priority]
        if existing:
            rule = existing[0]
            destination = rule.get('dst', '')
            if '/' not in destination and destination:
                destination += '/' + str(rule.get('dstlen', 32 if network.version == 4 else 128))
            if len(existing) != 1 or destination != cidr or rule.get('table') != 'main':
                raise RuntimeError('Host transport routing priority is already occupied')
            continue
        command = ['/usr/sbin/ip', family, 'rule', 'add', 'priority', str(priority), 'to', cidr, 'lookup', 'main']
        if os.getuid() != 0:
            command = ['/usr/bin/sudo', '-n'] + command
        subprocess.run(command, capture_output=True, text=True, timeout=8, check=True)


def ensure_network(sand_data):
    sand_data = pathlib.Path(sand_data)
    flag = sand_data / 'openbot-tailscale.json'
    if not flag.exists() and not flag.is_symlink():
        return {'state': 'disabled'}
    settings = network_settings(json.loads(owned(flag, 4096)))
    if not settings['enabled']:
        return {'state': 'disabled'}
    root = directory(sand_data / 'tailscale')
    lock = root / 'recovery.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted Tailscale recovery lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = json.loads(owned(root / 'current.json', 4096))
        if not isinstance(manifest, dict) or not isinstance(manifest.get('checkedAtMs'), int):
            raise RuntimeError('Invalid persistent Tailscale manifest')
        pid = live_pid(root)
        # Updating while disconnected cannot strand a live SSH session. New
        # packages remain staged until the next daemon recovery otherwise.
        update_error = None
        if settings.get('autoUpdate') is True and int(time.time() * 1000) - manifest.get('checkedAtMs', 0) > 21600000:
            try:
                version = latest_version(architecture())
                if version != manifest['version']:
                    manifest = install(root, version, architecture())
                else:
                    manifest['checkedAtMs'] = int(time.time() * 1000)
                    atomic(root / 'current.json', (json.dumps(manifest) + '\n').encode())
            except (OSError, ValueError, RuntimeError, tarfile.TarError) as error:
                # A package server outage must not block the trusted daemon.
                update_error = type(error).__name__
        state = directory(root / 'state')
        if (state / 'tailscaled.state').exists() or (state / 'tailscaled.state').is_symlink():
            owned(state / 'tailscaled.state', 4 * 1024 * 1024)
            (state / 'tailscaled.state').chmod(0o600)
        cli = executable(root, manifest, 'tailscale')
        if pid is None:
            daemon = executable(root, manifest, 'tailscaled')
            networking = settings.get('networking', 'userspace')
            if networking == 'kernel':
                ensure_kernel_capabilities(daemon)
                executable(root, manifest, 'tailscaled')
                ensure_host_transport(settings)
            log = os.open(root / 'daemon.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(log)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise RuntimeError('Untrusted Tailscale daemon log')
                os.fchmod(log, 0o600)
                child = subprocess.Popen([str(daemon), '--tun=' + (KERNEL_TUN if networking == 'kernel' else 'userspace-networking'),
                    '--state=' + str(state / 'tailscaled.state'), '--statedir=' + str(state),
                    '--socket=' + str(state / 'tailscaled.sock'), '--port=0'],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            finally:
                os.close(log)
            atomic(root / 'daemon.pid', str(child.pid).encode())
            return {'state': 'started', 'pid': child.pid, 'version': manifest['version'],
                    'networking': networking, 'updateErrorClass': update_error}
        networking = daemon_networking(pid)
        base = [str(cli), '--socket=' + str(state / 'tailscaled.sock')]
        status = cli_json(base, ['status', '--json'])
        pending_restart = networking != settings.get('networking', 'userspace')
        if networking == 'kernel' and not pending_restart:
            ensure_host_transport(settings)
            status = ensure_exit_node(base, status, settings)
        selected = status.get('ExitNodeStatus') or {}
        return {'state': status.get('BackendState', 'unknown'), 'pid': pid,
                'version': status.get('Version'), 'stagedVersion': manifest['version'],
                'updateErrorClass': update_error,
                'networking': networking, 'configurationPendingRestart': pending_restart,
                'desiredExitNodeIP': settings.get('exitNodeIP'),
                'selectedExitNodeIPs': selected_exit_ips(status),
                'exitNodeOnline': selected.get('Online'),
                'authRequired': status.get('BackendState') == 'NeedsLogin',
                'selfIPs': (status.get('Self') or {}).get('TailscaleIPs', [])}
    finally:
        os.close(fd)


def watcher_root(sand_data):
    """Opt in using only network policy, without an execution-harness gate."""
    sand_data = pathlib.Path(sand_data)
    info = sand_data.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Untrusted Tailscale data directory')
    policy = sand_data / 'openbot-tailscale.json'
    if not policy.exists() and not policy.is_symlink():
        return None
    settings = network_settings(json.loads(owned(policy, 4096)))
    return directory(sand_data / 'tailscale') if settings['enabled'] else None


def watcher_lock(root):
    fd = os.open(root / 'recovery-watch.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted Tailscale watcher lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


def ensure_network_watcher(sand_data):
    root = watcher_root(sand_data)
    if root is None:
        return {'state': 'disabled'}
    try:
        lock = watcher_lock(root)
    except BlockingIOError:
        return {'state': 'running'}
    os.close(lock)
    # The child takes the same lifetime lock. Concurrent bootstrap calls may
    # spawn candidates, but only one candidate can own the recovery loop.
    helper = pathlib.Path(__file__).absolute()
    info = helper.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Untrusted Tailscale watcher helper directory')
    owned(helper, 128 * 1024)
    log = os.open(root / 'recovery-watch.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(log)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted Tailscale watcher log')
        os.fchmod(log, 0o600)
        child = subprocess.Popen(['/usr/bin/python3', str(helper), '--sand-data', str(sand_data), '--watch'],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    finally:
        os.close(log)
    return {'state': 'started', 'pid': child.pid}


def watch_network(sand_data):
    root = watcher_root(sand_data)
    if root is None:
        return {'state': 'disabled'}
    try:
        lock = watcher_lock(root)
    except BlockingIOError:
        return {'state': 'already-running'}
    try:
        atomic(root / 'recovery-watch.pid', (str(os.getpid()) + '\n').encode())
        previous = None
        while True:
            try:
                result = ensure_network(sand_data)
            except BlockingIOError:
                result = {'state': 'recovery-in-progress'}
            except Exception as error:
                # Never log tokens, login URLs, command output or raw errors.
                result = {'state': 'failed', 'errorClass': type(error).__name__}
            state = {**result, 'watcherPid': os.getpid(), 'checkedAtMs': int(time.time() * 1000)}
            atomic(root / 'recovery-watch-state.json', (json.dumps(state) + '\n').encode())
            label = (result.get('state'), result.get('pid'), result.get('errorClass'))
            if label != previous:
                print(json.dumps(state), flush=True)
                previous = label
            if result.get('state') == 'disabled':
                return result
            time.sleep(WATCH_INTERVAL)
    finally:
        os.close(lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sand-data', type=pathlib.Path, default=pathlib.Path('/home/box/sand-data'))
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--install-version')
    actions.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    try:
        if args.install_version:
            result = install(directory(args.sand_data / 'tailscale'), args.install_version, architecture())
            print(json.dumps({'installedVersion': result['version'], 'checksumVerified': True}))
        elif args.watch:
            print(json.dumps(watch_network(args.sand_data)))
        else:
            print(json.dumps(ensure_network(args.sand_data)))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'failed', 'errorClass': type(error).__name__}))
        raise SystemExit(1)
