#!/usr/bin/env python3
"""Follow vendor releases, inherit compatible instructions and verify BOX turns.

Never treats a retired BOX bundle or a syntactic check as a working upgrade.
Unported instructions and executable changes stay in a durable coverage report.
"""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import signal
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import urllib.error
import uuid

HERE = pathlib.Path(__file__).resolve().parent
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_SOURCE = 48 * 1024 * 1024
VERSION = re.compile(r'[0-9a-f]{7,40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
TERMINAL = {'verified', 'assessed', 'compatibility-required', 'local-release-ready'}
TRIAL_PHASES = {'prepared', 'activated', 'verified'}


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def owned_json(path, limit=65536):
    return json.loads(load('local-engine').owned_bytes(path, limit))


def write_json(path, value, expected=None):
    load('computer-recovery').atomic_bytes(path, (json.dumps(value, indent=2) + '\n').encode(), expected)


@contextlib.contextmanager
def mutation_lock(root):
    descriptor = os.open(root / 'computer-recovery.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted recovery mutation lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def read_policy(root):
    try:
        value = owned_json(root / 'openbot-upstream-sync.json', 16384)
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or value.get('schemaVersion') != 1 or type(value.get('enabled')) is not bool:
        raise RuntimeError('Invalid upstream synchronization policy')
    if not value['enabled']:
        return None
    if type(value.get('pollSeconds', 1800)) is not int or not 60 <= value.get('pollSeconds', 1800) <= 86400:
        raise RuntimeError('Invalid upstream poll interval')
    if value.get('strategy', 'instructions-and-compatible-engine') != 'instructions-and-compatible-engine':
        raise RuntimeError('Unsupported upstream strategy')
    return value


def state(root):
    try:
        value = owned_json(root / 'openbot-upstream-sync-state.json')
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}


def trial(root):
    try:
        value = owned_json(root / 'openbot-upstream-sync-trial.json', 131072)
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict) or value.get('schemaVersion') != 1:
        raise RuntimeError('Invalid engine update trial journal')
    return value


def trial_bytes(value, key):
    raw = base64.b64decode(value[key], validate=True)
    if not 0 < len(raw) <= 16384:
        raise RuntimeError('Trial manifest exceeds its bound')
    return raw


def maybe_start(root):
    """Cheap supervisor tick. Network, parsing and verification run separately."""
    policy = read_policy(root)
    interrupted = trial(root).get('phase') in TRIAL_PHASES
    if policy is None and not interrupted:
        return {'state': 'disabled'}
    progress = state(root)
    if not interrupted and progress.get('nextCheckAtMs', 0) > int(time.time() * 1000):
        return {'state': progress.get('phase', 'waiting'),
                'upstreamVersion': progress.get('upstreamVersion'),
                'featureParity': progress.get('featureParity', 'unknown')}
    lock_path = root / 'openbot-upstream-sync.lock'
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted upstream lock')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state': 'checking'}
        # Claim the spawn before releasing the lock; a killed child cannot cause
        # fifteen-second spawn storms, and a later tick can still recover it.
        write_json(root / 'openbot-upstream-sync-state.json',
                   {**progress, 'nextCheckAtMs': int(time.time() * 1000) + 60000})
        log = root / 'openbot-upstream-sync.log'
        if log.exists():
            info = log.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError('Untrusted upstream log')
            if info.st_size > 256000:
                log.replace(root / 'openbot-upstream-sync.previous.log')
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            child = subprocess.Popen(['/usr/bin/python3', str(HERE / 'local-upstream-sync.py'),
                '--root', str(root), '--apply'], stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                start_new_session=True, close_fds=True)
        finally:
            os.close(fd)
        return {'state': 'started', 'pid': child.pid}
    finally:
        os.close(descriptor)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download(url, path, maximum, expected_sha=None):
    """Bounded verified streaming; never follow an artifact to another origin."""
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, headers={'User-Agent': 'OpenBot-upstream-sync'})
    digest = hashlib.sha256(); size = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with opener.open(request, timeout=25) as response, os.fdopen(fd, 'wb', closefd=False) as target:
            declared = response.headers.get('Content-Length')
            if declared is not None and not 0 < int(declared) <= maximum:
                raise RuntimeError('Vendor artifact exceeds its declared bound')
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > maximum:
                    raise RuntimeError('Vendor artifact exceeds its bound')
                digest.update(block); target.write(block)
            target.flush(); os.fsync(target.fileno())
        if not size or (expected_sha is not None and digest.hexdigest() != expected_sha):
            raise RuntimeError('Vendor artifact digest differs')
        return digest.hexdigest()
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)


def fetch_text(url, maximum):
    with urllib.request.build_opener(NoRedirect).open(url, timeout=15) as response:
        data = response.read(maximum + 1)
    if not 0 < len(data) <= maximum:
        raise RuntimeError('Vendor metadata exceeds its bound')
    return data.decode('utf8').strip()


def vendor_origin(source):
    """Resolve only the two reviewed literal constant layouts; never evaluate JS."""
    def assignment(key, expression):
        lhs = r'\b' + re.escape(key) + r'\s*=(?!=)'
        if len(re.findall(lhs, source)) != 1:
            raise RuntimeError('Vendor origin is not an unambiguous literal')
        matches = re.findall(lhs + r'\s*' + expression + r'\s*;', source)
        if len(matches) != 1:
            raise RuntimeError('Vendor origin is not an unambiguous literal')
        return matches[0]

    def literal(key):
        return assignment(key, r'(["`])([a-zA-Z0-9_-]+)\1')[1]

    prefix = literal('SAND_HOST_BUNDLE_S3_PREFIX')
    legacy = ('SAND_HOST_BUNDLE_S3_BUCKET', 'SAND_HOST_BUNDLE_S3_REGION')
    if any(re.search(r'\b' + key + r'\b', source) for key in legacy):
        # Mixed layouts could disagree about the actual fetch origin.
        if re.search(r'\bSAND_HOST_BUNDLE_PUBLIC_ORIGIN\b', source):
            raise RuntimeError('Vendor origin mixes reviewed layouts')
        bucket, region = (literal(key) for key in legacy)
    else:
        bucket = literal('PUBLIC_VM_DAEMON_BUCKET')
        region = literal('PUBLIC_VM_DAEMON_BUCKET_REGION')
        assignment('PUBLIC_VM_DAEMON_BUCKET_ORIGIN', re.escape(
            '`https://${PUBLIC_VM_DAEMON_BUCKET}.s3.${PUBLIC_VM_DAEMON_BUCKET_REGION}.amazonaws.com`'))
        assignment('SAND_HOST_BUNDLE_PUBLIC_ORIGIN', r'PUBLIC_VM_DAEMON_BUCKET_ORIGIN')
        assignment('SAND_HOST_BUNDLE_PUBLIC_BASE_URL', re.escape(
            '`${SAND_HOST_BUNDLE_PUBLIC_ORIGIN}/${SAND_HOST_BUNDLE_S3_PREFIX}`'))
    return 'https://' + bucket + '.s3.' + region + '.amazonaws.com/' + prefix, prefix


def client_version(source):
    values = re.findall(r'stampedVersionBaseOf\(\s*true\s*\?\s*"([0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.]+)?)"', source)
    return values[0] if len(values) == 1 else None


def unpack_host(archive, destination):
    """Extract just the regular host source; never extract archive paths/links."""
    found = None; count = 0; expanded = 0
    with tarfile.open(archive, mode='r|gz') as tar:
        for member in tar:
            count += 1; expanded += max(0, member.size)
            if count > 20000 or expanded > 2 * 1024 * 1024 * 1024:
                raise RuntimeError('Vendor archive expansion exceeds its bound')
            if member.name.rsplit('/', 1)[-1] != 'host-main.cjs':
                continue
            if found is not None or not member.isfile() or not 0 < member.size <= MAX_SOURCE:
                raise RuntimeError('Vendor host source is not one bounded regular file')
            stream = tar.extractfile(member)
            found = stream.read(MAX_SOURCE + 1)
            if len(found) != member.size:
                raise RuntimeError('Vendor source is truncated')
    if found is None:
        raise RuntimeError('Vendor archive has no host source')
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(found); output.flush(); os.fsync(output.fileno())
    return hashlib.sha256(found).hexdigest()


def stage_vendor(root, version, base, prefix):
    if not VERSION.fullmatch(version):
        raise RuntimeError('Invalid vendor version')
    folder = root / 'openbot-upstream/releases' / version
    folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    for path in (folder.parent.parent, folder.parent, folder):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted vendor release directory')
    metadata = folder / 'vendor.json'
    if metadata.exists():
        data = owned_json(metadata)
        raw = load('local-engine').owned_bytes(folder / 'host-main.cjs', MAX_SOURCE)
        if data.get('version') != version or hashlib.sha256(raw).hexdigest() != data.get('sourceSha256'):
            raise RuntimeError('Cached vendor source differs from its manifest')
        return folder, data
    url = base + '/' + prefix + '-' + version + '.tgz'
    digest = fetch_text(url + '.sha256', 2048).split()[0]
    if not SHA256.fullmatch(digest):
        raise RuntimeError('Invalid vendor archive digest')
    archive = folder / 'vendor-host.tgz'
    if archive.exists():
        # A stopped download is never accepted as a complete vendor release.
        archive.unlink()
    download(url, archive, MAX_ARCHIVE, digest)
    host = folder / 'host-main.cjs'
    host.unlink(missing_ok=True)
    host_sha = unpack_host(archive, host)
    source = load('local-engine').owned_bytes(host, MAX_SOURCE).decode('utf8')
    data = {'version': version, 'archiveSha256': digest, 'sourceSha256': host_sha,
            'clientVersion': client_version(source), 'capturedAtMs': int(time.time() * 1000)}
    write_json(metadata, data)
    return folder, data


def prune_releases(root, latest, keep=4):
    """Bound the cache; protect the selected source and any unfinished trial."""
    base = root / 'openbot-upstream/releases'
    protected = {latest}
    pin = load('local-engine').policy(root)
    if pin and pin.get('upstreamVersion'):
        protected.add(pin['upstreamVersion'])
    active = trial(root)
    if active.get('phase') in TRIAL_PHASES:
        protected.add(active.get('upstreamVersion'))
    releases = []
    for folder in base.iterdir():
        if not VERSION.fullmatch(folder.name):
            continue
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted cached release directory')
        try:
            metadata = owned_json(folder / 'vendor.json')
        except FileNotFoundError:
            continue
        if metadata.get('version') != folder.name or type(metadata.get('capturedAtMs')) is not int:
            raise RuntimeError('Invalid release retention metadata')
        releases.append((metadata['capturedAtMs'], folder))
    releases.sort(key=lambda row: row[0], reverse=True)
    retained = protected | {folder.name for _, folder in releases[:keep]}
    removed = []
    for _, folder in releases:
        if folder.name not in retained:
            shutil.rmtree(folder); removed.append(folder.name)
    return removed


def syntax(source, node):
    result = subprocess.run([node, '--check'], input=source, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Candidate engine syntax validation failed')


def inherit(root, source_path, upstream_path, output, node):
    output.mkdir(mode=0o700)
    env = {**os.environ, 'OPENBOT_PACKAGE_ROOT': str(root / 'openbot')}
    result = subprocess.run([node, '--max-old-space-size=512', str(HERE / 'upstream-instructions.mjs'),
        str(source_path), str(upstream_path), str(output)], env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Instruction compatibility analysis failed')
    source = load('local-engine').owned_bytes(output / 'candidate.cjs', MAX_SOURCE)
    report = owned_json(output / 'instructions.json', 4 * 1024 * 1024)
    if report.get('sourceSha256') != hashlib.sha256(source).hexdigest():
        raise RuntimeError('Instruction candidate differs from its report')
    return source, report


def compatibility_recipe(vendor):
    recipes = owned_json(HERE / 'latest-box-compatibility.json')
    if recipes.get('schemaVersion') != 1 or not isinstance(recipes.get('reviewedSources'), dict):
        raise RuntimeError('Invalid BOX compatibility recipe registry')
    recipe = recipes['reviewedSources'].get(vendor['sourceSha256'])
    if recipe is None:
        return None
    if (recipe.get('version') != vendor.get('version') or
            recipe.get('clientVersion') != vendor.get('clientVersion') or
            recipe.get('strategy') != 'latest-host-with-box-ports'):
        raise RuntimeError('Reviewed BOX source identity differs from the vendor metadata')
    return recipe


def compatibility_base(root, pin):
    """Successive latest-host ports must start from the original BOX interfaces."""
    engine = load('local-engine'); seen = set(); value = pin
    for _ in range(32):
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str) or
                not re.fullmatch('[0-9a-f]{64}', value[key]) for key in ('stockSha256', 'wrappedSha256')):
            raise RuntimeError('Invalid BOX compatibility baseline')
        generation = value.get('generationId')
        if generation is not None and (not isinstance(generation, str) or
                not re.fullmatch('[0-9a-f]{32}', generation)):
            raise RuntimeError('Invalid BOX compatibility baseline generation')
        directory = engine.artifact_directory(root, value)
        identity = (generation, value['stockSha256'])
        if identity in seen:
            raise RuntimeError('BOX compatibility baseline history is cyclic')
        seen.add(identity)
        if value.get('upstreamSyncStrategy') == 'latest-host-with-box-ports':
            value = value.get('boxCompatibilityBase') or owned_json(directory / 'previous-policy.json', 16384)
            continue
        source = directory / 'stock.cjs'
        raw = engine.owned_bytes(source, MAX_SOURCE)
        if hashlib.sha256(raw).hexdigest() != value['stockSha256']:
            raise RuntimeError('BOX compatibility baseline digest differs')
        engine.require_runner(raw.decode('utf8'))
        return source, {key: value[key] for key in ('generationId', 'stockSha256', 'wrappedSha256') if key in value}
    raise RuntimeError('BOX compatibility baseline history exceeds its bound')


def compose(root, source_path, upstream_path, output, node, *, package_root=None, host_main=None):
    env = {**os.environ, 'OPENBOT_PACKAGE_ROOT': str(package_root or root / 'openbot')}
    result = subprocess.run([node, '--max-old-space-size=640', str(HERE / 'latest-local-engine.mjs'),
        str(source_path), str(upstream_path), str(output)], env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Latest BOX composition failed its reviewed contracts')
    source = load('local-engine').owned_bytes(output / 'candidate.cjs', MAX_SOURCE)
    report = owned_json(output / 'compatibility.json', 4 * 1024 * 1024)
    if report.get('candidateSha256') != hashlib.sha256(source).hexdigest():
        raise RuntimeError('Latest BOX candidate differs from its compatibility report')
    result = subprocess.run([node, str(HERE / 'verify-latest-local-engine.cjs'),
        str(output / 'candidate.cjs'), report['candidateSha256'],
        str(host_main or os.environ.get('SAND_HOST_MAIN', '/opt/sand/sand-host/host-main.cjs'))],
        capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Latest BOX candidate failed native offline contracts')
    contracts = json.loads(result.stdout)
    if contracts.get('loaded') is not True or contracts.get('latestDatabasePersistence') is not True:
        raise RuntimeError('Latest BOX offline contract proof is incomplete')
    write_json(output / 'native-offline-contracts.json', contracts)
    report['nativeOfflineContracts'] = contracts
    return source, report


def wrap(source, retained):
    pattern = re.compile(r'^/\* openbot-stock-wrap \*/\n(?:/\* openbot-payload [0-9a-f]{8,64} \*/\n)?'
                         r'var __openbotRuntime = require\((?:\'[^\']+\'|"[^"]+")\);\n'
                         r'function createProtoSessionProvider\(\) \{\n'
                         r'  return __openbotRuntime\.(?:attachSession|wrapSession)\(createProtoSessionProvider_stock, arguments\);\n\}\n')
    match = pattern.match(retained)
    needle = 'function createProtoSessionProvider('
    if not match or source.count(needle) != 1 or '/* openbot-stock-wrap */' in source:
        raise RuntimeError('New BOX engine has no compatible wrap factory')
    return match[0] + source.replace(needle, 'function createProtoSessionProvider_stock(', 1)


def gateway(root, method, body=None):
    token = owned_json(root / 'gateway.json')['token']
    request = urllib.request.Request('http://127.0.0.1:1340/api/' + method,
        data=json.dumps(body or {}).encode(), headers={'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def configuration_hashes(root):
    return {name: hashlib.sha256(load('local-engine').owned_bytes(root / name, 4 * 1024 * 1024)).hexdigest()
            for name in ('openbot-plan.json', 'openbot-bot-models.json', 'openbot-box-remint.json',
                         'openbot-local-routines.json', 'openbot-mode')}


def is_delivered_canary(entry, started, marker):
    if entry.get('timestampMs', 0) < started:
        return False
    message = entry.get('message') or {}
    if message.get('type') == 'text':
        content = message.get('content')
    elif (entry.get('kind') == 'message' and entry.get('role') == 'assistant' and
            entry.get('isStreaming') is False):
        content = entry.get('content')
    else:
        return False
    return isinstance(content, str) and content.strip() == marker


def native_canary(root, folder, max_seconds=120, fixture_root=None):
    """The reply must come from reading an unpredictable local file, not a ping."""
    engine = load('local-engine')
    if engine.owned_bytes(root / 'openbot-mode', 64).decode().strip() != 'custom':
        raise RuntimeError('Custom mode is required for a non-Official update canary')
    journal = owned_json(root / 'openbot-box-remint.json', 4 * 1024 * 1024)
    ids = [row['newId'] for row in journal.get('entries', []) if row.get('name') == 'Q' and
           isinstance(row.get('newId'), str)]
    if len(set(ids)) != 1 or str(uuid.UUID(ids[0])) != ids[0]:
        raise RuntimeError('No unambiguous recovered BOX canary identity')
    aid = ids[0]
    roster = gateway(root, 'listAgents')
    target = next((row for row in roster if row.get('id') == aid), {})
    idle_deadline = time.monotonic() + max_seconds
    while target.get('harness') == 'box' and target.get('isRunning'):
        # A checkpointed turn can resume on boot before the canary starts.
        # Let it finish; a correct upgrade must not overwrite that user turn.
        if time.monotonic() >= idle_deadline:
            raise RuntimeError('Native canary bot remained busy after recovery')
        time.sleep(1)
        roster = gateway(root, 'listAgents')
        target = next((row for row in roster if row.get('id') == aid), {})
    if target.get('harness') != 'box':
        raise RuntimeError('Native canary bot is unavailable')
    marker = 'LOCAL_UPSTREAM_VERIFIED_' + uuid.uuid4().hex
    # sand-data/recovery-snapshots is intentionally a protected host-only store.
    # Put just this random, nonsecret fixture in the bot-accessible home tree;
    # updater manifests, archives and credentials never become tool-accessible.
    fixture_directory = fixture_root or root.parent / 'openbot-update-checks'
    fixture_directory.mkdir(mode=0o700, exist_ok=True)
    info = fixture_directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Untrusted update fixture directory')
    fixture = fixture_directory / ('read-token-' + uuid.uuid4().hex + '.txt')
    engine_data = (marker + '\n').encode()
    fd = os.open(fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(engine_data); stream.flush(); os.fsync(stream.fileno())
    write_json(folder / 'native-canary.json', {'fixturePath': str(fixture),
        'contentSha256': hashlib.sha256(engine_data).hexdigest(), 'agentId': aid})
    task = ('Owner-authorized read-only local engine update verification. Read exactly ' + str(fixture) +
            ' using a local file tool, then deliver its exact content as one message. '
            'The content is unpredictable and was not given in this prompt. Do not guess. '
            'Do not modify files or settings, contact peers or external services, or create routines.')
    started = int(time.time() * 1000)
    if engine.owned_bytes(root / 'openbot-mode', 64).decode().strip() != 'custom':
        raise RuntimeError('Mode changed before update canary dispatch')
    try:
        result = gateway(root, 'sendPrompt', {'agentId': aid, 'clientNonce': uuid.uuid4().hex, 'prompt': task})
    except urllib.error.HTTPError as error:
        # Private diagnostic only. Never put a server response in public status
        # or exception text; it may contain user data. Bound even error bodies.
        write_json(folder / 'native-canary-error.json', {'httpStatus': error.code,
            'responseBody': error.read(16384).decode('utf8', 'replace')})
        raise
    if result.get('accepted') is not True:
        raise RuntimeError('Native update canary was not accepted')
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        tail = gateway(root, 'getAgentTranscriptTail', {'id': aid, 'limit': 20})
        for entry in tail.get('entries', []):
            if is_delivered_canary(entry, started, marker):
                return {'agentId': aid, 'accepted': True, 'delivered': True, 'replyEntryId': entry['id'],
                        'startedAtMs': started, 'replyAtMs': entry['timestampMs'],
                        'replyLatencyMs': entry['timestampMs'] - started,
                        'fixtureContentSha256': hashlib.sha256(engine_data).hexdigest()}
        trace_path = root / ('openbot-upstream-turn-' + aid + '.json')
        startup_path = root / 'openbot-upstream-startup.json'
        if trace_path.exists() and startup_path.exists():
            trace, startup = owned_json(trace_path, 16384), owned_json(startup_path, 16384)
            if (trace.get('phase') == 'failed' and isinstance(trace.get('atMs'), int) and
                    trace['atMs'] >= started and trace.get('pid') == startup.get('pid')):
                write_json(folder / 'native-canary-turn-error.json', trace)
                raise RuntimeError('Native local-file canary failed during execution')
        time.sleep(1)
    raise RuntimeError('No delivered local-file canary before the deadline')


def reload_ready(root, allow_quiescent=False):
    if gateway(root, 'getHostStatus').get('isBusy') is False:
        return True
    if allow_quiescent:
        pause = gateway(root, 'getPauseState')
        return pause.get('owner') == 'upgrade' and pause.get('quiescent') is True
    return False


def request_checkpoint(root):
    token = owned_json(root / 'gateway.json')['token']
    request = urllib.request.Request('http://127.0.0.1:1340/prepare-upgrade', data=b'{}',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def checkpoint_for_reload(root, timeout=360, prepare=request_checkpoint):
    """Use the native checkpoint contract; never infer quiescence from roster counts."""
    if gateway(root, 'getHostStatus').get('isBusy') is False:
        return
    deadline = time.monotonic() + timeout
    requested = prepare(root)
    if requested.get('quiescing') is not True:
        raise RuntimeError('Native checkpoint declined the update')
    last_refresh = time.monotonic()
    while time.monotonic() < deadline:
        pause = gateway(root, 'getPauseState')
        if pause.get('owner') == 'upgrade' and pause.get('quiescent') is True:
            return
        # pauseTurnsForUpgrade also advances the native paused-run sweep. Only
        # reading getPauseState leaves wedged checkpoints waiting indefinitely.
        if time.monotonic() - last_refresh >= 3:
            requested = prepare(root)
            if requested.get('quiescing') is not True:
                raise RuntimeError('Native checkpoint ownership changed')
            last_refresh = time.monotonic()
        time.sleep(1)
    raise RuntimeError('Native checkpoint did not become quiescent')


def checkpoint_reload(root, host):
    checkpoint_for_reload(root)
    return reload_selected(root, host, allow_quiescent=True)


def reload_selected(root, host, allow_quiescent=False):
    recovery = load('computer-recovery'); recovery.SD = root; recovery.HOST = host
    progress_path = root / 'computer-recovery-state.json'
    progress = owned_json(progress_path) if progress_path.exists() else {}
    def arm():
        progress['pendingHostReload'] = True; write_json(progress_path, progress)
    changed = []
    for path in (root / 'host-main.cjs.pre-openbot', host):
        if recovery.repair_bundle(path, root / 'recovery-snapshots/computer-update-bundles',
                                  lambda source: syntax(source, '/exec-daemon/node'),
                                  arm if path == host else lambda: None):
            changed.append(path.name)
    pin = load('local-engine').policy(root)
    if pin and 'hostVersion' in pin:
        if not isinstance(pin['hostVersion'], str) or not VERSION.fullmatch(pin['hostVersion']):
            raise RuntimeError('Invalid selected host version')
        version_path = host.parent / 'version'
        previous = load('local-engine').owned_bytes(version_path, 128)
        target = (pin['hostVersion'] + '\n').encode()
        if previous != target:
            recovery.atomic_bytes(version_path, target, expected=previous,
                                  mode=stat.S_IMODE(version_path.stat().st_mode))
    pids = recovery.host_pids()
    if changed and len(pids) == 1 and reload_ready(root, allow_quiescent):
        if recovery.host_pids() != pids:
            raise RuntimeError('Native process changed before update reload')
        os.kill(pids[0], signal.SIGTERM)
        # Native shutdown flushes the durable store and the next boot restores
        # isolated agent workers. A 45-second window can roll back a valid
        # engine before its authenticated supervisor has even launched it.
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                new_pids = recovery.host_pids()
                startup_ready = True
                if pin and pin.get('upstreamSyncStrategy') == 'latest-host-with-box-ports':
                    trace_path = root / 'openbot-upstream-startup.json'
                    trace = owned_json(trace_path, 16384) if trace_path.exists() else {}
                    startup_ready = trace.get('phase') == 'ready' and trace.get('pid') in new_pids
                    if trace.get('phase') == 'failed' and trace.get('pid') in new_pids:
                        raise RuntimeError('Latest local engine failed its native startup contract')
                status = gateway(root, 'getHostStatus')
                expected = pin.get('hostVersion') if pin else None
                if new_pids != pids and startup_ready and status.get('hostVersion') and (
                        expected is None or status['hostVersion'] == expected):
                    progress['pendingHostReload'] = False; progress['lastReloadedPid'] = pids[0]
                    write_json(progress_path, progress)
                    return {'reloaded': True, 'previousPid': pids[0]}
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise RuntimeError('Native gateway did not return after update reload')
    if changed:
        return {'reloaded': False, 'deferred': True}
    return {'reloaded': False, 'alreadyCurrent': True}


def save_trial(root, value):
    write_json(root / 'openbot-upstream-sync-trial.json', value)


def rollback_trial(root, host, value, reload=reload_selected):
    """Recover an interrupted trial without restoring any user configuration."""
    engine = load('local-engine'); refresh = load('refresh-local-engine')
    path = root / 'openbot-local-engine.json'
    current = engine.owned_bytes(path, 16384)
    original, selected = trial_bytes(value, 'originalPolicy'), trial_bytes(value, 'selectedPolicy')
    if current not in (original, selected):
        save_trial(root, {**value, 'phase': 'authority-changed', 'closedAtMs': int(time.time() * 1000)})
        return {'phase': 'verification-failed', 'nativeDeliveryVerified': False,
                'rolledBack': False, 'engineAuthorityChangedConcurrently': True}
    if current == selected:
        refresh.atomic(path, original, selected)
    if value.get('previousHostVersion') is not None:
        version_path = host.parent / 'version'
        previous_version = trial_bytes(value, 'previousHostVersion')
        selected_version = trial_bytes(value, 'selectedHostVersion')
        current_version = engine.owned_bytes(version_path, 128)
        # An unrelated version edit belongs to its writer, even on rollback.
        if current_version == selected_version:
            load('computer-recovery').atomic_bytes(version_path, previous_version,
                expected=current_version, mode=stat.S_IMODE(version_path.stat().st_mode))
    deferred = False
    try:
        deferred = reload(root, host).get('deferred', False)
    except Exception:
        deferred = True
    if deferred:
        recovery_path = root / 'computer-recovery-state.json'
        progress = owned_json(recovery_path) if recovery_path.exists() else {}
        write_json(recovery_path, {**progress, 'pendingHostReload': True})
    save_trial(root, {**value, 'phase': 'rolled-back', 'closedAtMs': int(time.time() * 1000),
                     'reloadDeferred': deferred})
    return {'phase': 'verification-failed', 'nativeDeliveryVerified': False,
            'rolledBack': True, 'engineAuthorityChangedConcurrently': False, 'reloadDeferred': deferred}


def recover_interrupted_trial(root, host, reload=reload_selected):
    value = trial(root)
    if value.get('phase') not in TRIAL_PHASES:
        return None
    if value['phase'] == 'verified' and load('local-engine').owned_bytes(
            root / 'openbot-local-engine.json', 16384) == trial_bytes(value, 'selectedPolicy'):
        save_trial(root, {**value, 'phase': 'completed', 'closedAtMs': int(time.time() * 1000)})
        pin = json.loads(trial_bytes(value, 'selectedPolicy'))
        return {**state(root), **value['result'], 'phase': 'verified', 'interruptedUpdateRecovered': True,
                'featureParity': 'partial' if pin.get('upstreamSyncStrategy') == 'instruction-backport' else 'local-tested',
                'lastAppliedAtMs': int(time.time() * 1000),
                'nextCheckAtMs': int(time.time() * 1000) + 1800000}
    result = rollback_trial(root, host, value, reload)
    return {**state(root), **result, 'interruptedUpdateRecovered': True,
            'nextCheckAtMs': int(time.time() * 1000) + 60000}


def activate_and_verify(root, host, candidates, original_policy, metadata, folder,
                        canary=native_canary, reload=reload_selected):
    """Only the engine rolls back. User models, roles and routine flags are untouched."""
    before = configuration_hashes(root)
    if metadata.get('upstreamSyncStrategy') == 'latest-host-with-box-ports':
        _, baseline = compatibility_base(root, json.loads(original_policy))
        metadata = {**metadata, 'boxCompatibilityBase': baseline}
    refresh = load('refresh-local-engine'); selected = None; journal = None
    version_path = host.parent / 'version'
    previous_version = None
    if 'hostVersion' in metadata:
        previous_version = load('local-engine').owned_bytes(version_path, 128)
    def prepare(manifest):
        nonlocal selected, journal
        selected = manifest
        journal = {'schemaVersion': 1, 'phase': 'prepared', 'trialId': uuid.uuid4().hex,
                   'startedAtMs': int(time.time() * 1000),
                   'originalPolicy': base64.b64encode(original_policy).decode(),
                   'selectedPolicy': base64.b64encode(manifest).decode(),
                   'upstreamVersion': metadata.get('upstreamVersion'), 'configurationHashes': before}
        if previous_version is not None:
            journal.update(previousHostVersion=base64.b64encode(previous_version).decode(),
                selectedHostVersion=base64.b64encode((metadata['hostVersion'] + '\n').encode()).decode())
        # Persist the rollback authority before any live manifest can change.
        save_trial(root, journal)
    try:
        activation = refresh.activate_pair(root, candidates, original_policy, metadata, before_activate=prepare)
        journal['phase'] = 'activated'; save_trial(root, journal)
        restarted = reload(root, host)
        if restarted.get('deferred'):
            raise RuntimeError('Host became busy; tentative engine awaits idle reload')
        proof = canary(root, folder)
        if proof.get('delivered') is not True:
            raise RuntimeError('Update canary did not prove delivery')
        if configuration_hashes(root) != before:
            raise RuntimeError('Shared configuration changed during update verification')
        result = {'phase': 'verified', 'generationId': activation['generationId'],
                  'nativeDeliveryVerified': True, 'nativeCanary': proof, 'configurationUnchanged': True}
        journal.update(phase='verified', result=result); save_trial(root, journal)
        return result
    except Exception as error:
        result = rollback_trial(root, host, journal, reload) if journal else {
            'phase': 'verification-failed', 'nativeDeliveryVerified': False, 'rolledBack': False}
        return {**result, 'errorType': type(error).__name__}


def save_local_release(root, candidates, original_policy, metadata, report):
    """Prepare both local sources in Official; never select an untested live engine."""
    engine = load('local-engine')
    if engine.owned_bytes(root / 'openbot-mode', 64).decode().strip() != 'official':
        raise RuntimeError('Mode changed before the saved local release')
    if report.get('nativeBoxRunnerPresent') is not True:
        raise RuntimeError('A partial instruction backport is not a complete local release')
    if metadata.get('upstreamSyncStrategy') == 'latest-host-with-box-ports':
        _, baseline = compatibility_base(root, json.loads(original_policy))
        metadata = {**metadata, 'boxCompatibilityBase': baseline}
    before = configuration_hashes(root)
    saved = load('refresh-local-engine').activate_pair(root, candidates, original_policy, metadata, select=False)
    if configuration_hashes(root) != before or engine.owned_bytes(root / 'openbot-local-engine.json', 16384) != original_policy:
        raise RuntimeError('Configuration changed while saving a local release')
    ready = {'schemaVersion': 1, 'phase': 'local-release-ready',
             'manifest': saved['manifest'], 'savedAtMs': int(time.time() * 1000),
             'basePolicySha256': hashlib.sha256(original_policy).hexdigest(),
             'nativeDeliveryVerified': False, 'activationDeferred': True}
    write_json(root / 'openbot-upstream-local-ready.json', ready)
    return {'phase': ready['phase'], 'generationId': saved['generationId'],
            'localEngineSaved': True, 'nativeDeliveryVerified': False, 'activationDeferred': True,
            'configurationUnchanged': True}


def assessment_key(original_policy, upstream_sha):
    digest = hashlib.sha256(original_policy + upstream_sha.encode())
    paths = [HERE / name for name in ('local-upstream-sync.py', 'upstream-instructions.mjs',
                                     'computer-recovery.py', 'local-engine.py', 'latest-local-engine.mjs',
                                     'latest-box-ports.cjs', 'latest-box-compatibility.json',
                                     'verify-latest-local-engine.cjs')]
    paths += [HERE / name for name in load('computer-recovery').PATCHES]
    for path in paths:
        digest.update(path.name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def assess(root, folder, directory, pin, current, original_policy, vendor, node):
    """Reuse a complete digest-checked assessment; never repeatedly parse a wait."""
    engine = load('local-engine')
    key = assessment_key(original_policy, vendor['sourceSha256'])
    attempt = folder / ('assessment-' + key)
    if attempt.exists():
        info = attempt.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted assessment directory')
        report = owned_json(attempt / 'assessment.json', 4 * 1024 * 1024)
        if report.get('cacheKey') != key or report.get('upstreamSourceSha256') != vendor['sourceSha256']:
            raise RuntimeError('Cached assessment provenance differs')
        candidates = {}
        for kind, digest in report.get('candidateHashes', {}).items():
            if kind not in ('stock', 'wrapped'):
                raise RuntimeError('Unknown cached candidate kind')
            raw = engine.owned_bytes(attempt / ('candidate-' + kind + '.cjs'), MAX_SOURCE)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise RuntimeError('Cached instruction candidate digest differs')
            engine.require_runner(raw.decode()); candidates[kind] = raw
        if report.get('phase') != 'compatibility-required' and set(candidates) != {'stock', 'wrapped'}:
            raise RuntimeError('Cached engine pair is incomplete')
        return attempt, candidates, report
    staging = folder / ('assessment-staging-' + uuid.uuid4().hex)
    staging.mkdir(mode=0o700)
    vendor_source = engine.owned_bytes(folder / 'host-main.cjs', MAX_SOURCE).decode('utf8')
    syntax(vendor_source.encode(), node)
    try:
        engine.require_runner(vendor_source); compatible = True
    except RuntimeError:
        compatible = False
    vendor_native_box_present = compatible
    # Both branches retain a universal module inventory. A full compatible
    # candidate inherits code as well; its BOX behavior still needs a live test.
    stock, inventory = inherit(root, directory / 'stock.cjs', folder / 'host-main.cjs', staging / 'stock', node)
    if inventory.get('originalSha256') != pin['stockSha256'] or inventory.get('upstreamSha256') != vendor['sourceSha256']:
        raise RuntimeError('Instruction candidate provenance differs')
    candidates = {}; incompatibility = False; composition = None
    if compatible:
        recovery = load('computer-recovery')
        try:
            if not isinstance(vendor.get('clientVersion'), str):
                raise RuntimeError('New native engine has no known client version')
            for kind, source in {'stock': vendor_source, 'wrapped': wrap(vendor_source, current['wrapped'].decode())}.items():
                transformed = recovery.patched(source)
                if recovery.patched(transformed) != transformed:
                    raise RuntimeError('New native engine is not a fixed point')
                engine.require_runner(transformed); syntax(transformed.encode(), node)
                candidates[kind] = transformed.encode()
        except RuntimeError:
            incompatibility = True; candidates = {}
        strategy = 'full-compatible-engine'
    elif compatibility_recipe(vendor) is not None:
        recovery = load('computer-recovery')
        source_path, baseline = compatibility_base(root, pin)
        composed, composition = compose(root, source_path, folder / 'host-main.cjs', staging / 'composition', node)
        if (composition.get('retainedSha256') != baseline['stockSha256'] or
                composition.get('upstreamSha256') != vendor['sourceSha256']):
            raise RuntimeError('Latest BOX composition provenance differs')
        for kind, source in {'stock': composed.decode(), 'wrapped': wrap(composed.decode(), current['wrapped'].decode())}.items():
            transformed = recovery.patched(source)
            if recovery.patched(transformed) != transformed:
                raise RuntimeError('Latest BOX engine is not a canonical fixed point')
            engine.require_runner(transformed); syntax(transformed.encode(), node)
            candidates[kind] = transformed.encode()
        compatible = True; strategy = 'latest-host-with-box-ports'
    else:
        wrapped, report = inherit(root, directory / 'wrapped.cjs', folder / 'host-main.cjs', staging / 'wrapped', node)
        if report.get('originalSha256') != pin['wrappedSha256'] or report.get('upstreamSha256') != vendor['sourceSha256']:
            raise RuntimeError('Wrapped instruction provenance differs')
        if {(x['module'], x['symbol'], x['newSha256']) for x in inventory['applied']} != {
                (x['module'], x['symbol'], x['newSha256']) for x in report['applied']}:
            raise RuntimeError('Stock and wrapped instruction upgrades disagree')
        candidates = {'stock': stock, 'wrapped': wrapped}; strategy = 'instruction-backport'
        for raw in candidates.values(): engine.require_runner(raw.decode()); syntax(raw, node)
    summary = {'phase': 'compatibility-required' if incompatibility else 'assessed', 'cacheKey': key,
        'upstreamSourceSha256': vendor['sourceSha256'], 'strategy': strategy,
        'vendorNativeBoxRunnerPresent': vendor_native_box_present,
        'candidateNativeBoxRunnerPresent': compatible, 'nativeBoxRunnerPresent': compatible,
        'featureParity': 'pending' if compatible else 'partial', 'nativeDeliveryVerified': False,
        'appliedInstructionCount': len(inventory['applied']) if not compatible else None,
        'pendingInstructionCount': (None if composition else
            len(inventory['pending']) if not compatible or incompatibility else 0),
        'upstreamSourceSections': len(inventory['upstreamSections']),
        'pendingSourceSections': (None if composition else
            len(inventory['upstreamSections']) if not compatible or incompatibility else 0),
        **({'compatibilityReport': 'composition/compatibility.json',
            'retainedCompatibilityModules': len(composition['retainedModules']),
            'inheritedBindingCount': len(composition['inheritedBindings']),
            'restoredSharedInterfaces': composition.get('restoredSharedInterfaces', []),
            'fullFeatureParityVerified': False,
            'instructionInventoryBasis': 'retained-engine-backport-comparison',
            'retainedBackportPendingInstructionCount': len(inventory['pending']),
            'retainedBackportChangedSourceSections': len(inventory['upstreamSections'])} if composition else {}),
        'candidateHashes': {kind: hashlib.sha256(raw).hexdigest() for kind, raw in candidates.items()},
        'candidateChanged': any(hashlib.sha256(raw).hexdigest() != pin[kind + 'Sha256'] for kind, raw in candidates.items())}
    for kind, raw in candidates.items():
        load('computer-recovery').atomic_bytes(staging / ('candidate-' + kind + '.cjs'), raw)
    write_json(staging / 'assessment.json', summary)
    os.replace(staging, attempt)
    return attempt, candidates, summary


def sync(root, host, apply=False, force=False, checkpoint=False):
    if apply and trial(root).get('phase') in TRIAL_PHASES:
        try:
            with mutation_lock(root):
                return recover_interrupted_trial(root, host, reload=checkpoint_reload if checkpoint else reload_selected)
        except BlockingIOError:
            return {**state(root), 'phase': 'waiting-for-recovery-lock',
                    'nextCheckAtMs': int(time.time() * 1000) + 60000}
    policy = read_policy(root)
    if policy is None:
        return {'phase': 'disabled'}
    engine = load('local-engine'); pin = engine.policy(root)
    if pin is None:
        raise RuntimeError('Release follower requires an explicit local engine authority')
    before_policy = engine.owned_bytes(root / 'openbot-local-engine.json', 16384)
    directory = engine.artifact_directory(root, pin)
    current = {kind: engine.owned_bytes(directory / (kind + '.cjs'), MAX_SOURCE)
               for kind in ('stock', 'wrapped')}
    if any(hashlib.sha256(raw).hexdigest() != pin[kind + 'Sha256'] for kind, raw in current.items()):
        raise RuntimeError('Retained engine differs from its trusted manifest')
    base, prefix = vendor_origin(current['stock'].decode('utf8'))
    version = fetch_text(base + '/sand-host-bundle-latest.version', 128)
    if not VERSION.fullmatch(version):
        raise RuntimeError('Invalid latest vendor version')
    now = int(time.time() * 1000); previous = state(root)
    completed = previous.get('upstreamVersion') == version and previous.get('phase') in TERMINAL
    authority_matches = pin.get('upstreamVersion') == version if previous.get('phase') == 'verified' else (
        previous.get('baseEngineSourceHashes') == {kind: pin[kind + 'Sha256'] for kind in ('stock', 'wrapped')})
    if previous.get('phase') == 'local-release-ready':
        authority_matches = authority_matches and engine.owned_bytes(root / 'openbot-mode', 64).decode().strip() == 'official'
    if not force and completed and authority_matches:
        # Native injector refreshes must not clear upstream provenance or trigger
        # another visible test turn for the same vendor release.
        return {**previous, 'lastCheckedAtMs': now, 'nextCheckAtMs': now + policy.get('pollSeconds', 1800) * 1000}
    # A transient provider/disconnect failure must not freeze a release forever.
    # maybe_start still enforces nextCheckAtMs, so retrying a rolled-back trial
    # observes the configured poll interval rather than probing every tick.
    folder, vendor = stage_vendor(root, version, base, prefix)
    prune_releases(root, version)
    # Delivery proofs and candidate reports belong to one source/engine pair.
    # A newly captured release must not inherit a previous generation's reply,
    # rollback or readiness flags while waiting for its own assessment.
    progress = {'phase': 'captured', 'startedAtMs': now, 'lastCheckedAtMs': now,
        'upstreamVersion': version, 'upstreamClientVersion': vendor['clientVersion'],
        'baseClientVersion': pin.get('clientVersion'), 'upstreamSourceSha256': vendor['sourceSha256'],
        'nextCheckAtMs': now + policy.get('pollSeconds', 1800) * 1000, 'nativeDeliveryVerified': False,
        'featureParity': 'unknown', 'fullFeatureParityVerified': False,
        'lastAppliedAtMs': previous.get('lastAppliedAtMs'),
        'appliedInstructionCount': None, 'pendingInstructionCount': None,
        'pendingSourceSections': None,
        'baseEngineSourceHashes': {kind: pin[kind + 'Sha256'] for kind in ('stock', 'wrapped')}}
    if apply:
        write_json(root / 'openbot-upstream-sync-state.json', progress)
    # Parsing uses a bounded separate process and does not compete with a busy
    # bot. Capturing a release still works while chats and routines are running.
    if gateway(root, 'getHostStatus').get('isBusy') is not False and not (apply and checkpoint):
        return {**progress, 'phase': 'waiting-for-idle', 'nextCheckAtMs': now + 60000}
    available = next((int(line.split()[1]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemAvailable:')), 0)
    if available < 1500 * 1024:
        return {**progress, 'phase': 'waiting-for-memory', 'nextCheckAtMs': now + 60000}
    node = str(root / 'node22/bin/node')
    attempt, candidates, report = assess(root, folder, directory, pin, current, before_policy, vendor, node)
    progress.update({k: v for k, v in report.items() if k not in ('cacheKey', 'candidateHashes', 'phase')})
    progress['assessment'] = str(attempt.relative_to(root))
    native_compatible = report['nativeBoxRunnerPresent']; strategy = report['strategy']
    if report['phase'] == 'compatibility-required':
        return {**progress, 'phase': 'compatibility-required'}
    if not report['candidateChanged']:
        return {**progress, 'phase': 'assessed'}
    if not apply:
        return {**progress, 'phase': 'validated-not-activated'}
    metadata = {'upstreamVersion': version, 'upstreamClientVersion': vendor['clientVersion'],
                'upstreamSourceSha256': vendor['sourceSha256'], 'upstreamSyncStrategy': strategy}
    if native_compatible:
        metadata['clientVersion'] = vendor['clientVersion']
        metadata['hostVersion'] = version
    mode = engine.owned_bytes(root / 'openbot-mode', 64).decode().strip()
    if mode == 'official':
        if not native_compatible:
            return {**progress, 'phase': 'compatibility-required', 'localEngineSaved': False}
        try:
            with mutation_lock(root):
                if read_policy(root) != policy or not (root / 'openbot-local-only.enable').exists():
                    return {**progress, 'phase': 'policy-changed', 'nextCheckAtMs': now + 60000}
                result = save_local_release(root, candidates, before_policy, metadata, report)
                return {**progress, **result, 'finishedAtMs': int(time.time() * 1000)}
        except BlockingIOError:
            return {**progress, 'phase': 'waiting-for-recovery-lock', 'nextCheckAtMs': now + 60000}
    if mode != 'custom':
        return {**progress, 'phase': 'waiting-for-custom-verification', 'nextCheckAtMs': now + 60000}
    if gateway(root, 'getHostStatus').get('isBusy') is not False and not checkpoint:
        return {**progress, 'phase': 'waiting-for-idle', 'nextCheckAtMs': now + 60000}
    try:
        with mutation_lock(root):
            if gateway(root, 'getHostStatus').get('isBusy') is not False and not checkpoint:
                return {**progress, 'phase': 'waiting-for-idle', 'nextCheckAtMs': now + 60000}
            if read_policy(root) != policy or not (root / 'openbot-local-only.enable').exists():
                return {**progress, 'phase': 'policy-changed', 'nextCheckAtMs': now + 60000}
            if engine.owned_bytes(root / 'openbot-mode', 64).decode().strip() != 'custom':
                return {**progress, 'phase': 'waiting-for-custom-verification', 'nextCheckAtMs': now + 60000}
            write_json(root / 'openbot-upstream-sync-state.json', progress)
            result = activate_and_verify(root, host, candidates, before_policy, metadata, attempt,
                                         reload=checkpoint_reload if checkpoint else reload_selected)
    except BlockingIOError:
        return {**progress, 'phase': 'waiting-for-recovery-lock', 'nextCheckAtMs': now + 60000}
    final = {**progress, **result, 'finishedAtMs': int(time.time() * 1000)}
    if result['phase'] == 'verified':
        final['featureParity'] = 'local-tested' if native_compatible else 'partial'
        final['lastAppliedAtMs'] = final['finishedAtMs']
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path('/home/box/sand-data'))
    parser.add_argument('--host', type=pathlib.Path, default=pathlib.Path('/opt/sand/sand-host/host-main.cjs'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--checkpoint', action='store_true',
        help='Checkpoint busy local work through the native upgrade API before reloading (with --apply)')
    args = parser.parse_args()
    try:
        os.nice(10)
    except OSError:
        pass
    # Prevent duplicate upgrade tests. Network and parsing never hold the native
    # recovery mutation lock; activation holds it only during an idle upgrade.
    fd = os.open(args.root / 'openbot-upstream-sync.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted upstream synchronization lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        result = sync(args.root, args.host, args.apply, args.force, args.checkpoint)
        if args.apply and result.get('phase') != 'disabled':
            write_json(args.root / 'openbot-upstream-sync-state.json', result)
            value = trial(args.root)
            if result.get('phase') == 'verified' and value.get('phase') == 'verified':
                save_trial(args.root, {**value, 'phase': 'completed', 'closedAtMs': int(time.time() * 1000)})
        print(json.dumps({k: v for k, v in result.items() if k not in ('nativeCanary', 'assessment')}), flush=True)
    except Exception as error:
        result = {'phase': 'check-failed', 'errorType': type(error).__name__,
                  'nextCheckAtMs': int(time.time() * 1000) + 300000}
        if args.apply:
            write_json(args.root / 'openbot-upstream-sync-state.json', {**state(args.root), **result})
        print(json.dumps(result), flush=True)
        raise SystemExit(1)
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
