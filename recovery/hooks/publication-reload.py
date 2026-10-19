"""Durable, idle-only native publication reload requests, independent of telemetry."""
import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import signal
import stat
import tempfile
import time
import uuid

HOST = pathlib.Path('/opt/sand/sand-host/host-main.cjs')
NAME = 'openbot-publication-reload.json'
QUIET_SECONDS = 2
SIGNAL_RETRY_SECONDS = 10


def checked(path, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (private and info.st_mode & 0o077):
            raise RuntimeError('Publication reload requires an owned regular file')
        data = stream.read(65537 if private else -1)
        if private and len(data) > 65536:
            raise RuntimeError('Publication reload marker exceeds its bound')
        return data, info


def custom(sd):
    try:
        flag, _ = checked(sd / 'openbot-local-only.enable')
        mode, _ = checked(sd / 'openbot-mode')
        return mode.strip() == b'custom'
    except FileNotFoundError:
        return False


def host_pids(host):
    result = []
    for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = path.read_bytes().split(b'\0')
            if args[0] == b'/exec-daemon/node' and os.fsencode(host) in args and b'guard' not in args:
                result.append(int(path.parent.name))
        except OSError:
            continue
    return result


def identity(pid):
    try:
        text = (pathlib.Path('/proc') / str(pid) / 'stat').read_text()
        fields = text[text.rfind(')') + 2:].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'startTicks': fields[19]}
    except (FileNotFoundError, ProcessLookupError):
        return None


@contextlib.contextmanager
def locked(sd):
    fd = os.open(sd / (NAME + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+b') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('Publication reload lock is not private and owned')
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read(sd):
    try:
        raw, _ = checked(sd / NAME, private=True)
    except FileNotFoundError:
        return None
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get('schema') != 1 or
            str(uuid.UUID(value['requestId'])) != value['requestId'] or
            value.get('mode') != 'custom' or value.get('phase') not in ('pending', 'signal-issued', 'acknowledged') or
            not isinstance(value.get('target'), dict) or type(value['target'].get('pid')) is not int or
            value['target']['pid'] <= 0 or not isinstance(value['target'].get('startTicks'), str) or
            len(value.get('hostSha256', '')) != 64):
        raise RuntimeError('Invalid publication reload request')
    return value


def write(sd, value):
    # A writer and the consumer share this lock; telemetry writers never touch it.
    try:
        checked(sd / NAME, private=True)
    except FileNotFoundError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=NAME + '.', dir=sd)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write((json.dumps(value, indent=2) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, sd / NAME)
        directory = os.open(sd, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def request(sd, host, host_pids):
    sd, host = pathlib.Path(sd), pathlib.Path(host)
    with locked(sd):
        if not custom(sd):
            raise RuntimeError('Custom local mode is required for publication reload')
        pids = host_pids()
        target = identity(pids[0]) if len(pids) == 1 else None
        if target is None:
            raise RuntimeError('A unique native host is required for publication reload')
        source, _ = checked(host)
        value = {'schema': 1, 'requestId': str(uuid.uuid4()), 'mode': 'custom',
                 'phase': 'pending', 'target': target, 'hostSha256': hashlib.sha256(source).hexdigest(),
                 'requestedAtMs': int(time.time() * 1000)}
        if not custom(sd) or host_pids() != pids or identity(pids[0]) != target:
            raise RuntimeError('Native owner or mode changed before publication reload request')
        write(sd, value)
        return value


def consume(sd, host, host_pids, gateway_status):
    sd, host = pathlib.Path(sd), pathlib.Path(host)
    with locked(sd):
        value = read(sd)
        if value is None:
            return {'phase': 'absent'}
        result = {'phase': value['phase'], 'requestId': value['requestId'], 'publicationVerified': False}
        if value['phase'] == 'acknowledged':
            return result
        if not custom(sd):
            return {**result, 'phase': 'waiting-custom'}
        source, info = checked(host)
        digest = hashlib.sha256(source).hexdigest()
        if digest != value['hostSha256']:
            return {**result, 'phase': 'waiting-request-refresh'}
        if time.time() - info.st_mtime < QUIET_SECONDS:
            return {**result, 'phase': 'waiting-stable-source'}
        pids = host_pids()
        current = identity(pids[0]) if len(pids) == 1 else None
        if current is None:
            return {**result, 'phase': 'waiting-native-host'}
        if current != value['target']:
            # The registered fleet predates this native process; its startup sweep
            # can now be verified separately. A reload acknowledgement is not delivery.
            if not custom(sd) or host_pids() != pids or identity(pids[0]) != current:
                return {**result, 'phase': 'waiting-native-host'}
            value.update(phase='acknowledged', acknowledgedAtMs=int(time.time() * 1000), replacement=current)
            write(sd, value)
            return {**result, 'phase': 'acknowledged'}
        if (value['phase'] == 'signal-issued' and
                time.time() * 1000 - value['signalIssuedAtMs'] < SIGNAL_RETRY_SECONDS * 1000):
            return {**result, 'phase': 'waiting-native-replacement'}
        if gateway_status().get('isBusy') is not False:
            return {**result, 'phase': 'waiting-idle'}
        # Recheck all authorities after the potentially slow gateway call.
        fresh, fresh_info = checked(host)
        if (not custom(sd) or host_pids() != pids or identity(pids[0]) != current or
                hashlib.sha256(fresh).hexdigest() != digest or
                time.time() - fresh_info.st_mtime < QUIET_SECONDS):
            return {**result, 'phase': 'waiting-authority'}
        # Persist intent before the signal. A watcher crash in this gap is
        # recovered by a grace-bounded retry against this same process identity.
        value.update(phase='signal-issued', signalIssuedAtMs=int(time.time() * 1000),
                     signalAttempts=value.get('signalAttempts', 0) + 1)
        write(sd, value)
        try:
            os.kill(pids[0], signal.SIGTERM)
        except OSError:
            value['phase'] = 'pending'
            write(sd, value)
            raise
        return {**result, 'phase': 'signal-issued', 'pid': pids[0]}
