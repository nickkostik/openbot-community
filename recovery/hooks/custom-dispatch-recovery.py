#!/usr/bin/env python3
"""Resume the verified BOX handoff when an opted-in installation selects Custom.

The mode file is user authority. A healthy hop or a patched local harness label
never substitutes for the owner-visible server binding and published history.
"""
import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import time
import urllib.error
import uuid

HERE = pathlib.Path(__file__).resolve().parent
STATE = 'openbot-custom-dispatch-state.json'
LOCK = 'openbot-custom-dispatch.lock'


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(root, name):
    return load('server-roster-check').read_owned(root / name)


def write(root, value):
    load('computer-recovery').atomic_bytes(root / STATE, (json.dumps(value, indent=2) + '\n').encode())


def allowed(root):
    try:
        directory = root.lstat()
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid():
            return False
        for path in (root / 'openbot-mode', root / 'openbot-local-only.enable'):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                return False
        return (root / 'openbot-mode').read_text().strip() == 'custom'
    except FileNotFoundError:
        return False


@contextlib.contextmanager
def lock(root):
    fd = os.open(root / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted Custom dispatch lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def snapshot(root):
    """Public progress contains no exception bodies, credentials or agent IDs."""
    try:
        progress = read(root, STATE)
    except FileNotFoundError:
        progress = {}
    progress = {**progress, "enabled": allowed(root),
                "diagnosticAssistant": "unavailable",
                "diagnosticReason": "No bounded local diagnostic assistant adapter is installed"}
    try:
        with lock(root):
            progress["workerRunning"] = False
    except BlockingIOError:
        progress["workerRunning"] = True
    return progress


def maybe_start(root, now=None, requested=False):
    """Keep network/copy work outside the fifteen-second recovery tick."""
    if not allowed(root):
        return {'phase': 'disabled', 'nativeAppDeliveryVerified': False}
    now = int(time.time() * 1000) if now is None else now
    try:
        with lock(root) as worker_fd:
            # Read only after claiming the writer lock; a completed worker may
            # have published newer state while this request was waiting.
            try:
                progress = read(root, STATE)
            except FileNotFoundError:
                progress = {}
            if not requested and (progress.get('phase') == 'review-needed' or progress.get('nextCheckAtMs', 0) > now):
                return progress
            if not allowed(root):
                return {'phase': 'disabled', 'nativeAppDeliveryVerified': False}
            claimed = {**progress, 'jobId': progress.get('jobId') or str(uuid.uuid4()),
                       'phase': 'starting', 'nextCheckAtMs': now + 60000}
            write(root, claimed)
            fd = os.open(root / 'openbot-custom-dispatch.log',
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise RuntimeError('Untrusted Custom dispatch log')
                child = subprocess.Popen(['/usr/bin/python3', str(HERE / 'custom-dispatch-recovery.py'),
                                          '--root', str(root), '--worker-lock-fd', str(worker_fd)],
                                         stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                                         start_new_session=True, close_fds=True, pass_fds=(worker_fd,))
            finally:
                os.close(fd)
            return {**claimed, 'pid': child.pid}
    except BlockingIOError:
        return {**snapshot(root), 'workerRunning': True}


class Recovery:
    def __init__(self, root, reminter=None, sleep=time.sleep, clock=time.monotonic):
        self.root = root
        self.module = load('remint-box-fleet')
        self.module.SD = root
        self.module.JOURNAL = root / 'openbot-box-remint.json'
        self.r = reminter or self.module.Reminter()
        self.r.m.SD = root
        self.r.m.JOURNAL = root / 'openbot-local-migration.json'
        self.sleep, self.clock, self.last_request = sleep, clock, -10.0
        try:
            previous = read(root, STATE)
        except FileNotFoundError:
            previous = {}
        self.progress = {'jobId': previous.get('jobId') or str(uuid.uuid4()), 'phase': 'checking-bindings', 'startedAtMs': int(time.time() * 1000),
                         'rateLimitRetries': 0, 'nativeAppDeliveryVerified': False, 'billingVerified': False,
                         'liveHistoryMerged': False, 'sdkContextsMerged': False}

    def require(self):
        if not allowed(self.root):
            raise RuntimeError('Custom mode was withdrawn; handoff stopped')
        self.r.require_local_mode()

    def status(self, phase, **values):
        self.progress.update(phase=phase, **values)
        self.progress['updatedAtMs'] = int(time.time() * 1000)
        if (self.root / 'openbot-box-remint.json').exists():
            try:
                entries = self.r.read().get('entries', [])
                self.progress['counts'] = {phase: sum(e.get('phase') == phase for e in entries)
                                           for phase in ('planned', 'copied', 'registered-box', 'active')}
                self.progress['agentCount'] = len(entries)
            except Exception:
                # A refused/corrupt journal must still publish its review state.
                self.progress.pop('counts', None)
        write(self.root, self.progress)

    def pace(self, call):
        def paced(method, body):
            refreshed = False
            for attempt in range(8):
                self.require()
                self.sleep(max(0, self.last_request + 1.2 - self.clock()))
                self.require()
                self.last_request = self.clock()
                self.progress['lastOperation'] = method
                self.progress['controlPlaneCalls'] = self.progress.get('controlPlaneCalls', 0) + 1
                if self.progress['controlPlaneCalls'] % 20 == 0:
                    self.status(self.progress['phase'])
                try:
                    return call(method, body)
                except RuntimeError as error:
                    # The authenticated adapter bounds error bodies. Never copy
                    # their text into public progress, logs or exception reports.
                    message = str(error)
                    if "'http': 401" in message and not refreshed and attempt < 7:
                        # A long history/cutover pass can outlive its short-lived
                        # token. Renew from the legitimate host credential once.
                        self.r.m.H = None
                        refreshed = True
                        continue
                    rate_limited = "'http': 429" in message and 'isRetryable' in message
                    unavailable = any("'http': " + str(code) in message for code in (500, 502, 503, 504))
                    # The native host briefly disappears during an idle update.
                    # Wait for its legitimate renewal credential; never replace
                    # it with a saved user token or treat the gap as completion.
                    unavailable = unavailable or message == 'Running host renewal credential missing'
                    if (not rate_limited and not unavailable) or attempt == 7:
                        raise
                    delay = min(120, 5 * 2 ** attempt)
                    self.status('waiting-for-rate-limit' if rate_limited else 'waiting-for-control-plane', retryDelaySeconds=delay,
                                rateLimitRetries=self.progress['rateLimitRetries'] + int(rate_limited))
                    self.sleep(delay)
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    if attempt == 7:
                        raise
                    delay = min(120, 5 * 2 ** attempt)
                    self.status('waiting-for-control-plane', retryDelaySeconds=delay)
                    self.sleep(delay)
        return paced

    def run(self):
        self.require()
        self.r.m.rpc = self.pace(self.r.m.rpc)
        original_gateway = self.r.m.gateway
        def gateway(method, body):
            self.require()
            return original_gateway(method, body)
        self.r.m.gateway = gateway
        original_rows = self.r.rows
        def rows():
            result = original_rows()
            box = {}
            for row in result:
                if row.get('harness') != 'box' or row.get('viewerIsOwner') is not True:
                    continue
                aid = self.module.canonical(row['agentId'])
                sid = self.r.publisher.require_box_row(row, aid)
                if aid in box:
                    raise RuntimeError('Ambiguous BOX publication authority')
                box[aid] = sid
            self.r.m.write(self.root / 'openbot-publication-bindings.json',
                           {'schemaVersion': 1, 'checkedAtMs': int(time.time()*1000), 'boxAgents': box})
            return result
        self.r.rows = rows
        journal = self.r.read() if (self.root / 'openbot-box-remint.json').exists() else None
        if journal is None or all(e['phase'] == 'active' for e in journal['entries']):
            rows = self.r.rows()
            expected = load('server-roster-check').authority(journal) if journal else {}
            report = load('server-roster-check').compare(expected, rows)
            profiles = self.r.profiles()
            if expected and report['state'] == 'bound-box':
                # Official can reveal the recorded sources while leaving their
                # real BOX counterparts intact. Do not mint another generation
                # from those already-paired originals (or require fake labels).
                paired = {e['oldId'] for e in journal['entries']}
                profiles = {aid:p for aid,p in profiles.items() if aid not in paired}
            sources = self.module.source_candidates(rows, profiles)
            if expected and report['state'] == 'bound-box':
                self.r.reselect_active()
            if not sources:
                if not expected or report['state'] != 'bound-box':
                    raise RuntimeError('No verified active BOX authority; handoff requires recovery')
                self.r.restore_routines()
                self.r.finalize_preferences()
                self.r.refresh_peer_roster()
                self.status('bound-box', agentCount=len(expected), nextCheckAtMs=int(time.time()*1000)+120000)
                return self.progress
            self.status('copying', agentCount=len(sources))
            journal = self.r.initialize(next_generation=journal is not None)
        self.progress['agentCount'] = len(journal['entries'])
        for entry in journal['entries']:
            self.require()
            if entry['phase'] == 'planned':
                self.status('copying'); self.r.stage(entry['oldId'])
        # Register members before rooms. Registered entries are revalidated by
        # verify rather than recreated, so a restart keeps the same new IDs.
        entries = sorted(self.r.read()['entries'], key=lambda e: str(e['kind']).endswith('ROOM'))
        for entry in entries:
            self.require()
            if entry['phase'] == 'copied':
                self.status('registering'); self.r.register(entry['oldId'])
        journal = self.r.read()
        if not journal.get('publicationReloadRequestedAtMs'):
            self.status('waiting-for-publication'); self.r.publish()
        results = self.r.verify()
        self.r.m.write(self.root / 'openbot-custom-dispatch-verification.json', {'results': results})
        verified = sum(row['status'] == 'verified-box' for row in results)
        if verified != len(entries):
            self.status('waiting-for-publication', verifiedAgents=verified,
                        nextCheckAtMs=int(time.time()*1000)+60000)
            return self.progress
        self.require()
        self.status('activating', verifiedAgents=verified)
        self.r.activate()
        self.r.restore_routines()
        active = self.r.read()
        expected = load('server-roster-check').authority(active)
        report = load('server-roster-check').compare(expected, self.r.rows())
        if len(expected) != len(entries) or report['state'] != 'bound-box':
            raise RuntimeError('BOX authority changed during activation')
        self.status('bound-box', agentCount=len(expected), verifiedAgents=verified,
                    nextCheckAtMs=int(time.time()*1000)+120000)
        return self.progress


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path('/home/box/sand-data'))
    parser.add_argument('--request', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--worker-lock-fd', type=int)
    args = parser.parse_args()
    if args.status:
        print(json.dumps(snapshot(args.root)))
        return
    if args.request:
        result = maybe_start(args.root, requested=True)
        print(json.dumps({**snapshot(args.root), **result}))
        return
    # A requested worker inherits the parent's flock with no unlock/spawn gap.
    if args.worker_lock_fd is not None:
        inherited = os.fstat(args.worker_lock_fd)
        expected = (args.root / LOCK).lstat()
        if (inherited.st_dev, inherited.st_ino, inherited.st_uid) != (expected.st_dev, expected.st_ino, os.getuid()):
            raise RuntimeError('Invalid inherited transfer lock')
        fcntl.flock(args.worker_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = contextlib.nullcontext() if args.worker_lock_fd is not None else lock(args.root)
    with held:
        recovery = Recovery(args.root)
        try:
            recovery.run()
        except Exception as error:
            recovery.r.m.write(args.root / 'openbot-custom-dispatch-error.json', {
                'phase': recovery.progress['phase'], 'errorType': type(error).__name__,
                'message': str(error)[:4096], 'atMs': int(time.time()*1000)})
            retry_ms = 60000 if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)) else 300000
            transient = isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))
            # Exception bodies stay in the owned private error artifact. The
            # panel gets a safe phase and actionable diagnostic category only.
            message = str(error).lower()
            category = ('history-conflict' if any(word in message for word in ('history', 'transcript', 'sdk', 'diverg'))
                        else 'identity-conflict' if any(word in message for word in ('identity', 'ownership', 'authority', 'ambiguous'))
                        else 'preflight-refused')
            recovery.status(('waiting-for-control-plane' if transient else 'review-needed')
                            if allowed(args.root) else 'cancelled-mode-change',
                            refusalPhase=recovery.progress['phase'], diagnosticCode=category,
                            errorType=type(error).__name__, nextCheckAtMs=int(time.time()*1000)+retry_ms)
            raise SystemExit(1) from None


if __name__ == '__main__':
    main()
