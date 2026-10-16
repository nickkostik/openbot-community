#!/usr/bin/env python3
"""Restart the owned recovery watcher after a managed computer image replacement."""
import argparse
import fcntl
import json
import importlib.util
import os
import pathlib
import re
import stat
import subprocess

FACTORY_SAND_DATA = pathlib.Path('/home/box/sand-data')
DEFAULT_HOST = pathlib.Path('/opt/sand/sand-host/host-main.cjs')
KNOWN_HOSTS = (DEFAULT_HOST, pathlib.Path('/home/box/sand-host/host-main.cjs'))
HERE = pathlib.Path(__file__).absolute().parent
PROC = pathlib.Path('/proc')


def owned_file(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise RuntimeError('Untrusted recovery bootstrap file')
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def owned_directory(path, label):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Untrusted ' + label + ' directory')


def helper_paths(root, name):
    """Prefer the running/installed package; keep copied hooks as a fallback."""
    packages = []
    if HERE.name == 'hooks' and HERE.parent.name == 'recovery':
        packages.append(HERE.parent.parent)
    if root / 'openbot' not in packages:
        packages.append(root / 'openbot')
    helpers = []
    for package in packages:
        helper = package / 'recovery/hooks' / name
        if not helper.exists() and not helper.is_symlink():
            continue
        # Check each package directory before following its children. Never
        # silently downgrade a present but untrusted preferred installation.
        for directory in (package, package / 'recovery', helper.parent,
                          package / 'src', package / 'src/ui'):
            owned_directory(directory, 'recovery helper')
        owned_file(package / 'src/cli.ts', 4 * 1024 * 1024)
        owned_file(package / 'src/ui/server.ts', 4 * 1024 * 1024)
        owned_file(helper, 128 * 1024)
        helpers.append(helper)
    copied = root / 'openbot-hooks' / name
    if copied.exists() or copied.is_symlink():
        owned_directory(copied.parent, 'recovery helper')
        owned_file(copied, 128 * 1024)
        helpers.append(copied)
    if not helpers:
        raise FileNotFoundError('Recovery helper is not installed')
    return helpers


def process_args(pid):
    """Read only a same-owner live process, never trust a recycled pidfile."""
    directory = PROC / str(pid)
    try:
        owned_directory(directory, 'recovery process')
        raw = owned_file(directory / 'cmdline', 16 * 1024)
        return [os.fsdecode(arg) for arg in raw.rstrip(b'\0').split(b'\0')]
    except (OSError, RuntimeError):
        return []


def selected_host(value=None):
    value = value if value is not None else os.environ.get('OPENBOT_HOST_MAIN')
    if value is not None:
        value = os.fspath(value)
        path = pathlib.Path(value)
        if not path.is_absolute() or '..' in path.parts or any(c in value for c in ('\0', '\n', '\r')):
            raise ValueError('Native host must be an absolute path without traversal')
        return path
    hosts = set()
    for directory in PROC.glob('[0-9]*'):
        args = process_args(directory.name)
        if len(args) >= 2 and args[0] in ('/exec-daemon/node', '/usr/bin/node', '/usr/local/bin/node'):
            # Only the script invocation qualifies, with the native launcher's
            # harmless warning/source-map switches. A syntax probe, eval or a
            # different script carrying this path must not select our host.
            options = args[1:-1]
            direct = all(option in ('--enable-source-maps', '--no-warnings') or
                         re.fullmatch(r'--disable-warning=[A-Za-z][A-Za-z0-9_]*', option)
                         for option in options)
            if direct and args[-1] in tuple(str(path) for path in KNOWN_HOSTS):
                hosts.add(pathlib.Path(args[-1]))
    if len(hosts) > 1:
        raise RuntimeError('Ambiguous native hosts; select --host-main explicitly')
    if hosts:
        return hosts.pop()
    return next((path for path in KNOWN_HOSTS if path.is_file()), DEFAULT_HOST)


def watcher_process(root, helpers, host):
    pid_file = root / 'computer-recovery.pid'
    try:
        text = owned_file(pid_file, 64).decode().strip()
    except FileNotFoundError:
        return None
    if not text.isdecimal() or int(text) <= 0:
        return None
    args = process_args(int(text))
    if len(args) < 2 or args[0] != '/usr/bin/python3' or args[1] not in tuple(str(path) for path in helpers):
        return None
    configured = args[2:] == ['--host', str(host), '--watch']
    legacy = host == DEFAULT_HOST and args[2:] == ['--watch']
    return 'matching' if configured or legacy else 'conflict'


def ensure_watcher(root, host=None):
    root = pathlib.Path(root)
    owned_directory(root, 'recovery bootstrap')
    flag = root / 'openbot-local-only.enable'
    if not flag.exists() and not flag.is_symlink():
        return {'state': 'disabled'}
    owned_file(flag, 4096)
    mode = owned_file(root / 'openbot-mode', 64).decode().strip()
    if mode not in ('custom', 'official'):
        return {'state': 'disabled'}
    # Native injectors still embed this factory root. A custom data root may
    # recover its network, but must not start repairs against another install.
    if root != FACTORY_SAND_DATA:
        return {'state': 'unsupported-path'}
    host = selected_host(host)
    helpers = helper_paths(root, 'computer-recovery.py')
    helper = helpers[0]
    process = watcher_process(root, helpers, host)
    lock = root / 'computer-recovery-watch.lock'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted recovery watcher lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state': 'running' if process == 'matching' else
                    'conflict' if process == 'conflict' else 'starting'}
        if process is not None:
            return {'state': 'conflict' if process == 'conflict' else 'starting'}
    finally:
        os.close(fd)
    # Release the inspection lock before starting the child. The watcher takes
    # its own exclusive lock, so concurrent boot probes cannot create two owners.
    log = root / 'computer-recovery-watch.log'
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted recovery watcher log')
        child = subprocess.Popen(['/usr/bin/python3', str(helper), '--host', str(host), '--watch'],
            stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, start_new_session=True)
    finally:
        os.close(fd)
    return {'state': 'started', 'pid': child.pid}


def ensure_optional_network(root):
    """Network recovery does not depend on Official/Custom or BOX/Temporal."""
    root = pathlib.Path(root)
    owned_directory(root, 'recovery bootstrap')
    flag = root / 'openbot-tailscale.json'
    if not flag.exists() and not flag.is_symlink():
        return {'state': 'disabled'}
    owned_file(flag, 4096)
    helper = helper_paths(root, 'tailscale-recovery.py')[0]
    spec = importlib.util.spec_from_file_location('openbot_tailscale_recovery', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        network = module.ensure_network(root)
    except BlockingIOError:
        network = {'state': 'recovery-in-progress'}
    except Exception as error:
        network = {'state': 'failed', 'errorClass': type(error).__name__}
    # Keep retrying on the Computer even when this one-shot transport cannot
    # reach it again. The network watcher never requires local-only mode.
    watcher = module.ensure_network_watcher(root)
    if watcher['state'] != 'disabled':
        network['watcher'] = watcher
    return network


def bootstrap(root, host=None):
    try:
        network = ensure_optional_network(root)
    except Exception as error:
        network = {'state': 'failed', 'errorClass': type(error).__name__}
    result = ensure_watcher(root, host=host)
    if network['state'] != 'disabled':
        result['network'] = network
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sand-data', default=str(FACTORY_SAND_DATA))
    parser.add_argument('--host-main', '--host', dest='host')
    args = parser.parse_args()
    try:
        print(json.dumps(bootstrap(args.sand_data, host=args.host)))
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'state': 'failed', 'errorType': type(error).__name__}))
        raise SystemExit(1)
