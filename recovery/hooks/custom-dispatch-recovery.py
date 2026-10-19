#!/usr/bin/env python3
"""Resume the verified BOX handoff when an opted-in installation selects Custom.

The mode file is user authority. A healthy hop or a patched local harness label
never substitutes for the owner-visible server binding and published history.
"""
import argparse
import contextlib
import fcntl
import importlib.util
import hashlib
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
ROSTER_SCOPE_TTL_MS = 120000


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


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def journal_progress(root):
    journal = read(root, 'openbot-box-remint.json')
    entries = journal.get('entries')
    phases = ('planned', 'copied', 'registered-box', 'active')
    if not isinstance(entries, list) or not entries or any(
            not isinstance(e, dict) or e.get('phase') not in phases or
            not isinstance(e.get('oldId'), str) or not isinstance(e.get('newId'), str) for e in entries):
        raise RuntimeError('Unrecognized transfer journal')
    if len({(e['oldId'], e['newId']) for e in entries}) != len(entries):
        raise RuntimeError('Ambiguous transfer journal')
    return journal, {phase: sum(e['phase'] == phase for e in entries) for phase in phases}


def publication_proof(root, journal):
    proof = read(root, 'openbot-custom-dispatch-verification.json')
    results = proof.get('results')
    pairs = {(e['oldId'], e['newId']) for e in journal['entries']}
    if (not isinstance(results, list) or len(results) != len(pairs) or
            any(not isinstance(r, dict) or r.get('status') != 'verified-box' for r in results) or
            {(r.get('oldId'), r.get('newId')) for r in results} != pairs):
        return None
    return {'journalSha256': fingerprint(journal), 'verificationSha256': fingerprint(proof)}


def roster_scope(rows, profiles, covered):
    """Known local archives stay retired; unknown/cloud-only sources need review."""
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError('Unknown owner roster schema')
    counts = {'untrackedSourceAgents': 0, 'untrackedVisibleAgents': 0,
              'unknownSourceAgents': 0, 'hiddenArchiveAgents': 0}
    seen = set()
    for row in rows:
        aid = row.get('agentId')
        if not isinstance(aid, str) or str(uuid.UUID(aid)) != aid or aid in seen:
            raise RuntimeError('Ambiguous owner roster identity')
        seen.add(aid)
        if aid in covered or row.get('viewerIsOwner') is False:
            continue
        sid = row.get('id')
        bound = (row.get('legacyAgentId') == aid and isinstance(sid, str) and
                 sid.isascii() and sid.isdigit() and int(sid) > 0)
        if row.get('harness') == 'box' and row.get('viewerIsOwner') is True and bound:
            continue  # Actual owned BOX sources are never reminted.
        local = profiles.get(aid, {})
        profile, settings = local.get('profile', {}), local.get('settings', {})
        matching = (bound and str(profile.get('serverId')) == sid and
                    profile.get('harness') in ('box', 'temporal'))
        hidden = settings.get('hiddenFromSidebar') if matching else None
        if row.get('harness') == 'temporal' and row.get('viewerIsOwner') is True and hidden is True:
            counts['hiddenArchiveAgents'] += 1
            continue
        counts['untrackedSourceAgents'] += 1
        if row.get('harness') == 'temporal' and row.get('viewerIsOwner') is True and hidden is False:
            counts['untrackedVisibleAgents'] += 1
        else:
            counts['unknownSourceAgents'] += 1
    return counts


def roster_receipt_current(progress, journal, now=None):
    now = int(time.time() * 1000) if now is None else now
    checked = progress.get('rosterCheckedAtMs')
    return (progress.get('rosterComplete') is True and type(checked) is int and
            0 <= now - checked < ROSTER_SCOPE_TTL_MS and
            progress.get('rosterScopeJournalSha256') == fingerprint(journal) and
            all(progress.get(key) == 0 for key in ('untrackedSourceAgents', 'untrackedVisibleAgents', 'unknownSourceAgents')))


def verified_worker(root, progress):
    """A held flock can be a maintenance keeper; it does not identify a worker."""
    pid = progress.get('workerPid')
    if type(pid) is not int or pid <= 0:
        return False
    try:
        proc = pathlib.Path('/proc') / str(pid) / 'cmdline'
        if proc.stat().st_uid != os.getuid():
            return False
        with proc.open('rb') as stream:
            command = stream.read(8193)
        if len(command) > 8192:
            return False
        argv = command.decode().rstrip('\0').split('\0')
        if argv[:4] != ['/usr/bin/python3', str(HERE / 'custom-dispatch-recovery.py'), '--root', str(root)]:
            return False
        return not argv[4:] or (len(argv[4:]) == 2 and argv[4] == '--worker-lock-fd' and argv[5].isdigit())
    except (OSError, UnicodeError):
        return False


def diagnostic_status(root):
    """The model's free-form response and private coordinator errors stay private."""
    try:
        script = HERE / 'transfer-diagnostics.py'
        info = script.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Untrusted diagnostic adapter')
        return load('transfer-diagnostics').status(root)
    except Exception:
        return {'status': 'unavailable'}


def start_diagnostic(root):
    """Advisory work is isolated, bounded and never holds up transfer recovery."""
    try:
        script = HERE / 'transfer-diagnostics.py'
        info = script.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or not allowed(root):
            return False
        advisor = load('transfer-diagnostics')
        advisor.snapshot(advisor.read_json(root / STATE, 65536))
        advisor.selected_model(root)
        # No shell, ACP tools, error text, inherited writer FD, or agent identity.
        subprocess.Popen(['/usr/bin/timeout', '--signal=TERM', '15s', '/usr/bin/python3',
                          str(script), '--state', str(root / STATE), '--run'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        return True
    except Exception:
        return False


def snapshot(root):
    """Refresh journal counts without network work or any success from a lock."""
    try:
        progress = read(root, STATE)
    except FileNotFoundError:
        progress = {}
    progress = {**progress, 'enabled': allowed(root), 'publicationVerified': False,
                'nativeAppDeliveryVerified': False, 'billingVerified': False,
                'diagnosticAssistant': 'unavailable', 'diagnosticReason': 'unavailable'}
    advisory = diagnostic_status(root)
    progress['diagnosticAssistant'] = advisory.get('status', 'unavailable')
    progress['diagnosticReason'] = advisory.get('reason', 'none')
    progress['diagnosticAdviceCode'] = advisory.get('adviceCode')
    progress.pop('counts', None)
    progress.pop('agentCount', None)
    progress['verifiedAgents'] = 0
    saved_scope = dict(progress)
    progress['rosterComplete'] = False
    try:
        journal, counts = journal_progress(root)
        progress.update(counts=counts, agentCount=len(journal['entries']))
        progress['rosterComplete'] = roster_receipt_current(saved_scope, journal)
        try:
            proof = publication_proof(root, journal)
        except FileNotFoundError:
            proof = None
        complete = counts['active'] == len(journal['entries'])
        progress['publicationVerified'] = bool(complete and proof and progress.get('publicationProof') == proof)
        if progress['publicationVerified']:
            progress['verifiedAgents'] = len(journal['entries'])
        if progress.get('phase', 'idle') in ('bound-box', 'idle') and not progress['publicationVerified']:
            progress['phase'] = ('copying' if counts['planned'] else 'registering' if counts['copied']
                                 else 'waiting-for-publication')
        if progress.get('phase') == 'bound-box' and not progress['rosterComplete']:
            progress['phase'] = 'checking-bindings'
    except FileNotFoundError:
        if progress.get('phase') == 'bound-box':
            progress['phase'] = 'waiting-for-publication'
    except Exception:
        progress.update(phase='review-needed', diagnosticCode='preflight-refused')
    try:
        with lock(root):
            progress['lockHeld'] = False
    except BlockingIOError:
        progress['lockHeld'] = True
    progress['workerRunning'] = progress['lockHeld'] and verified_worker(root, progress)
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
        return snapshot(root)


class Recovery:
    def __init__(self, root, reminter=None, sleep=time.sleep, clock=time.monotonic, worker_lock_fd=None):
        self.root = root
        self.worker_lock_fd = worker_lock_fd
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
        self.progress = {'workerPid': os.getpid(), 'jobId': previous.get('jobId') or str(uuid.uuid4()), 'phase': 'checking-bindings', 'startedAtMs': int(time.time() * 1000),
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
        if phase == 'bound-box':
            journal, counts = journal_progress(self.root)
            proof = publication_proof(self.root, journal)
            if not roster_receipt_current(self.progress, journal):
                raise RuntimeError('Current owner roster completeness must be checked before completion')
            if counts['active'] != len(journal['entries']) or not proof:
                raise RuntimeError('Publication proof missing for completed transfer')
            self.progress['publicationProof'] = proof
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

    def refresh_publication_bindings(self, rows=None):
        """Refresh publisher authority from real owned BOX rows, without cutover."""
        self.require()
        rows = self.r.rows() if rows is None else rows
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError('Malformed platform roster; publication authority was not changed')
        box = {}
        for row in rows:
            if row.get('harness') != 'box' or row.get('viewerIsOwner') is not True:
                continue
            aid = self.module.canonical(row['agentId'])
            sid = self.r.publisher.require_box_row(row, aid)
            if aid in box:
                raise RuntimeError('Ambiguous BOX publication authority')
            box[aid] = sid
        value = {'schemaVersion': 1, 'checkedAtMs': int(time.time()*1000), 'boxAgents': box}
        self.r.m.atomic_bytes(self.root / 'openbot-publication-bindings.json',
                              self.r.m.json_bytes(value), pre_commit=self.require)
        return {'phase': 'publication-bindings-refreshed', 'boxAgentCount': len(box),
                'checkedAtMs': value['checkedAtMs'], 'nativeAppDeliveryVerified': False}

    def check_roster_scope(self, journal, rows=None, profiles=None, prospective=()):
        self.require()
        rows = self.r.rows() if rows is None else rows
        profiles = self.r.profiles() if profiles is None else profiles
        lineage = self.module.identity_mapping(journal) if journal else {}
        covered = set(lineage) | set(lineage.values()) | set(prospective)
        counts = roster_scope(rows, profiles, covered)
        complete = counts['untrackedSourceAgents'] == 0
        self.progress.update(**counts, rosterComplete=complete,
                             rosterCheckedAtMs=int(time.time() * 1000),
                             rosterScopeJournalSha256=fingerprint(journal) if journal else None)
        if not complete:
            # No journal append, next-generation reset, source hide, or blind
            # local-only copy may substitute for a complete cloud/SDK import.
            self.status('review-needed', refusalPhase='checking-bindings', diagnosticCode='history-conflict')
            start_diagnostic(self.root)
        return complete

    def run(self):
        self.require()
        self.status('checking-bindings')
        self.r.m.rpc = self.pace(self.r.m.rpc)
        original_gateway = self.r.m.gateway
        def gateway(method, body):
            self.require()
            return original_gateway(method, body)
        self.r.m.gateway = gateway
        original_rows = self.r.rows
        def rows():
            result = original_rows()
            self.refresh_publication_bindings(result)
            return result
        self.r.rows = rows
        journal = self.r.read() if (self.root / 'openbot-box-remint.json').exists() else None
        if journal is not None and not self.check_roster_scope(journal):
            return self.progress
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
            if journal is None and not self.check_roster_scope(None, rows, profiles, [e['oldId'] for e in sources]):
                return self.progress
            if journal is not None and sources:
                raise RuntimeError('Current transfer authority requires review; automatic generation replacement is refused')
            if expected and report['state'] == 'bound-box':
                self.r.reselect_active()
            if not sources:
                if not expected or report['state'] != 'bound-box':
                    raise RuntimeError('No verified active BOX authority; handoff requires recovery')
                self.r.restore_routines()
                self.r.finalize_preferences()
                self.r.refresh_peer_roster()
                results = self.r.verify()
                self.r.m.write(self.root / 'openbot-custom-dispatch-verification.json', {'results': results})
                if len(results) != len(expected) or any(row['status'] != 'verified-box' for row in results):
                    self.status('waiting-for-publication')
                    return self.progress
                if not self.check_roster_scope(self.r.read()):
                    return self.progress
                self.status('bound-box', agentCount=len(expected), nextCheckAtMs=int(time.time()*1000)+120000)
                return self.progress
            self.status('copying', agentCount=len(sources))
            journal = self.r.initialize()
        self.progress['agentCount'] = len(journal['entries'])
        tail = load('history-tail-repair')
        if tail.pending(journal, self.progress['jobId']):
            # A crash can happen after UI commit and before SDK/receipt pin.
            # Resume that exact durable plan before publication/verification.
            self.status('preserving-late-history')
            repaired = tail.TailRepair(self, self.worker_lock_fd).run()
            self.require(); self.r.publish()
            self.status('waiting-for-publication', lateHistoryRepair=repaired,
                        nextCheckAtMs=int(time.time()*1000)+60000)
            return self.progress
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
        if not self.check_roster_scope(self.r.read()):
            return self.progress
        # Check all known source heads before any source is hidden. A bounded
        # literal cloud append can be preserved on hidden registered copies;
        # unsupported or active-copy changes still require explicit review.
        tail = load('history-tail-repair')
        try:
            cache = {}
            for entry in self.r.read()['entries']:
                load('history-merge-receipt').source_freshness(self.r, self.r.read(), entry, cache)
        except RuntimeError as error:
            if str(error) != tail.FRESHNESS_ERROR:
                raise
            self.status('preserving-late-history', verifiedAgents=verified)
            repaired = tail.TailRepair(self, self.worker_lock_fd).run()
            self.require()
            self.r.publish()
            self.status('waiting-for-publication', lateHistoryRepair=repaired,
                        nextCheckAtMs=int(time.time()*1000)+60000)
            return self.progress
        self.status('activating', verifiedAgents=verified)
        self.r.activate()
        self.r.restore_routines()
        active = self.r.read()
        expected = load('server-roster-check').authority(active)
        report = load('server-roster-check').compare(expected, self.r.rows())
        if len(expected) != len(entries) or report['state'] != 'bound-box':
            raise RuntimeError('BOX authority changed during activation')
        if not self.check_roster_scope(active):
            return self.progress
        self.status('bound-box', agentCount=len(expected), verifiedAgents=verified,
                    nextCheckAtMs=int(time.time()*1000)+120000)
        return self.progress


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path('/home/box/sand-data'))
    parser.add_argument('--request', action='store_true')
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--worker-lock-fd', type=int)
    parser.add_argument('--refresh-publication-bindings', action='store_true',
                        help='Refresh owned BOX publisher authority only; do not mint, copy, activate or send.')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(snapshot(args.root)))
        return
    if args.request:
        result = maybe_start(args.root, requested=True)
        print(json.dumps(snapshot(args.root)))
        return
    # A requested worker inherits the parent's flock with no unlock/spawn gap.
    if args.worker_lock_fd is not None:
        inherited = os.fstat(args.worker_lock_fd)
        expected = (args.root / LOCK).lstat()
        if (inherited.st_dev, inherited.st_ino, inherited.st_uid) != (expected.st_dev, expected.st_ino, os.getuid()):
            raise RuntimeError('Invalid inherited transfer lock')
        fcntl.flock(args.worker_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    held = contextlib.nullcontext() if args.worker_lock_fd is not None else lock(args.root)
    with held as acquired_fd:
        recovery = Recovery(args.root, worker_lock_fd=args.worker_lock_fd if args.worker_lock_fd is not None else acquired_fd)
        try:
            if args.refresh_publication_bindings:
                print(json.dumps(recovery.refresh_publication_bindings()))
            else:
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
            if recovery.progress['phase'] == 'review-needed':
                start_diagnostic(args.root)
            raise SystemExit(1) from None


if __name__ == '__main__':
    main()
