#!/usr/bin/env python3
"""Publish existing local identities without minting replacements or sending turns.

The default action is a read-only plan. Explicit --agent-id or --all-individuals
permits registration, local serverId binding, and working-state export. Rooms
are deliberately skipped; registering a room requires its separate API.
"""
import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import uuid


SD = pathlib.Path('/home/box/sand-data')
PROFILE_FIELDS = ('name', 'description', 'title', 'avatarShape', 'avatarColor')
SECRET_FIELDS = frozenset(('apikey', 'authorization', 'accesstoken', 'refreshtoken',
                          'bearertoken', 'password', 'privatekey', 'credential',
                          'renewalcredential', 'token', 'secret'))


class SafetyError(RuntimeError):
    """A prerequisite failed; do not mutate the identity or profile."""


def load_helper(filename):
    path = pathlib.Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    if spec is None or spec.loader is None:
        raise SafetyError('Required sibling helper is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # The helpers have guarded main entrypoints.
    return module


def read_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise SafetyError('Required JSON file is missing or invalid') from exc


def canonical_id(value):
    if not isinstance(value, str):
        raise SafetyError('Roster identity must be a UUID')
    try:
        parsed = str(uuid.UUID(value))
    except ValueError as exc:
        raise SafetyError('Roster identity must be a UUID') from exc
    if parsed != value:
        raise SafetyError('Roster identity must be a canonical UUID')
    return value


def load_allowlist(path, expected_count=35):
    records = read_json(path)
    if not isinstance(records, list):
        raise SafetyError('Allowlist must be a list of existing identities')
    entries = {}
    for row in records:
        if not isinstance(row, dict):
            raise SafetyError('Allowlist contains an invalid record')
        aid = canonical_id(row.get('id') or row.get('newId'))
        if row.get('id') and row.get('newId') and row['id'] != row['newId']:
            raise SafetyError('Allowlist identity fields disagree')
        if aid in entries:
            raise SafetyError('Allowlist contains duplicate identities')
        if not isinstance(row.get('isGroup', False), bool):
            raise SafetyError('Allowlist group marker is not boolean')
        entries[aid] = {'id': aid, 'isGroup': row.get('isGroup', False)}
    if expected_count is not None and len(entries) != expected_count:
        raise SafetyError('Allowlist size changed; review the roster before publication')
    return entries


def require_safe_profile(profile, aid):
    if not isinstance(profile, dict) or profile.get('harness') != 'box':
        raise SafetyError('Local profile must already use the box harness')
    for field in ('id', 'agentId', 'legacyAgentId'):
        if field in profile and profile[field] != aid:
            raise SafetyError('Local profile identity differs from its existing UUID')
    for field in PROFILE_FIELDS:
        if field in profile and not isinstance(profile[field], str):
            raise SafetyError('Local profile metadata is not a string')

    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = ''.join(c for c in key.lower() if c.isalnum())
                if normalized in SECRET_FIELDS:
                    raise SafetyError('Refusing to back up a profile containing credential fields')
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(profile)


def require_box_row(row, aid):
    if not isinstance(row, dict) or row.get('harness') != 'box':
        raise SafetyError('Platform identity is not BOX; no local binding was changed')
    if row.get('agentId') != aid or row.get('legacyAgentId') != aid:
        raise SafetyError('Platform identity fields differ from the existing UUID')
    server_id = row.get('id')
    if not isinstance(server_id, str) or not server_id.isascii() or not server_id.isdigit() or int(server_id) <= 0:
        raise SafetyError('Platform identity has no valid numeric server id')
    return server_id


def find_existing(rows, aid, profile):
    matches = [row for row in rows if aid in (row.get('agentId'), row.get('legacyAgentId'))]
    if len(matches) > 1:
        raise SafetyError('Multiple platform rows claim the same local identity')
    existing = matches[0] if matches else None
    if existing is not None:
        require_box_row(existing, aid)
    bound_id = profile.get('serverId')
    if bound_id is not None:
        bound_id = str(bound_id)
        bound_rows = [row for row in rows if row.get('id') == bound_id]
        if len(bound_rows) != 1:
            raise SafetyError('Existing local serverId is missing or ambiguous on the platform')
        require_box_row(bound_rows[0], aid)
        if existing is None or existing['id'] != bound_id:
            raise SafetyError('Existing local serverId conflicts with the current platform identity')
    return existing


def request_fields(aid, profile):
    require_safe_profile(profile, aid)
    return {'legacyAgentId': aid, 'agentId': aid, 'harness': 1,
            'createIntent': 2, 'createCaller': 4,
            'introductionSuppressed': True, 'kickstartRequested': False,
            **{field: profile.get(field, '') for field in PROFILE_FIELDS}}


def atomic_json(path, value, mode=0o600):
    path = pathlib.Path(path)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), mode)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def bind_profile(path, row, aid, run_dir, expected_profile):
    """Validate before backing up; replace only the current unchanged profile."""
    server_id = require_box_row(row, aid)
    require_safe_profile(expected_profile, aid)
    path = pathlib.Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise SafetyError('Profile must be a regular file')
    original = path.read_bytes()
    current = json.loads(original)
    if current != expected_profile:
        raise SafetyError('Profile changed during publication; rerun the plan')
    if current.get('serverId') is not None and str(current['serverId']) != server_id:
        raise SafetyError('Refusing to overwrite a different local serverId')
    if current.get('serverId') == server_id:
        return False
    backup = pathlib.Path(run_dir) / (aid + '.profile.before.json')
    if not backup.exists():
        atomic_json(backup, current)
    updated = {**current, 'serverId': server_id}
    descriptor, name = tempfile.mkstemp(prefix='.openbot-profile-', dir=path.parent)
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(handle.fileno(), info.st_uid, info.st_gid)
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != original or path.lstat().st_ino != info.st_ino:
            raise SafetyError('Profile changed before replacement; no binding was written')
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return True


class Publisher:
    def __init__(self, root, allowlist, platform_rpc, gateway_rpc):
        self.root = pathlib.Path(root)
        self.allowlist = allowlist
        self.platform_rpc = platform_rpc
        self.gateway_rpc = gateway_rpc

    def call(self, method, body):
        result = self.platform_rpc(method, body)
        if not isinstance(result, dict) or 'http' in result or 'error' in result:
            status = result.get('http', 'unknown') if isinstance(result, dict) else 'unknown'
            raise SafetyError(f'{method} failed (HTTP {status}); response body omitted')
        return result

    def rows(self):
        result = self.call('ListGrokBotAgents', {})
        rows = result.get('agents', [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise SafetyError('Platform roster is malformed')
        return rows

    def profile(self, aid):
        canonical_id(aid)
        if aid not in self.allowlist:
            raise SafetyError('Identity is outside the existing fleet allowlist')
        profile = read_json(self.root / 'agents' / aid / 'profile.json')
        require_safe_profile(profile, aid)
        return profile

    def plan(self):
        rows = self.rows()
        results = []
        for aid, entry in self.allowlist.items():
            try:
                profile = self.profile(aid)
                if entry['isGroup']:
                    results.append({'id': aid, 'status': 'skipped-group'})
                    continue
                existing = find_existing(rows, aid, profile)
                results.append({'id': aid, 'status': 'reuse-box' if existing else 'register-existing',
                                'serverId': existing['id'] if existing else None,
                                'profileBound': bool(existing and profile.get('serverId') == existing['id'])})
            except SafetyError as exc:
                results.append({'id': aid, 'status': 'blocked', 'error': str(exc)})
        return results

    def verify(self, aid, rows=None):
        profile = self.profile(aid)
        if self.allowlist[aid]['isGroup']:
            return {'id': aid, 'status': 'skipped-group'}
        row = find_existing(self.rows() if rows is None else rows, aid, profile)
        if row is None:
            raise SafetyError('Local identity is absent from the platform roster')
        server_id = require_box_row(row, aid)
        transcript = self.call('ListGrokBotTranscriptEntries', {'agentId': aid, 'limit': 1})
        entries = transcript.get('entries', [])
        if not isinstance(entries, list):
            raise SafetyError('Transcript API returned malformed entries')
        bound = profile.get('serverId') == server_id
        return {'id': aid, 'serverId': server_id, 'status': 'verified' if bound and entries else 'incomplete',
                'harness': 'box', 'profileBound': bound, 'transcriptAccessible': True,
                'transcriptEntriesSampled': len(entries), 'generation': transcript.get('generation', 0)}

    def publish(self, aid, run_dir):
        profile = self.profile(aid)
        if self.allowlist[aid]['isGroup']:
            return {'id': aid, 'status': 'skipped-group'}
        run_dir = pathlib.Path(run_dir)
        ledger = {'id': aid, 'status': 'prepared'}
        ledger_path = run_dir / (aid + '.publication.json')
        row = find_existing(self.rows(), aid, profile)
        atomic_json(ledger_path, ledger)
        if row is None:
            ledger['status'] = 'registering-existing'
            atomic_json(ledger_path, ledger)
            result = self.call('CreateGrokBotAgent', request_fields(aid, profile))
            row = result.get('agent')
            require_box_row(row, aid)
            ledger['registration'] = 'created-existing'
        else:
            ledger['registration'] = 'reused-box'
        ledger.update(status='validated-box', serverId=require_box_row(row, aid))
        atomic_json(ledger_path, ledger)
        # Recheck the authoritative row immediately before the profile mutation.
        live = find_existing(self.rows(), aid, profile)
        if live is None or require_box_row(live, aid) != row['id']:
            raise SafetyError('Platform identity changed during publication')
        ledger['profileChanged'] = bind_profile(self.root / 'agents' / aid / 'profile.json',
                                               live, aid, run_dir, profile)
        ledger['status'] = 'bound-box'
        atomic_json(ledger_path, ledger)
        try:
            exported = self.gateway_rpc('exportGrokBotWorkingState',
                {'agentId': aid, 'expectedServerId': row['id'], 'trigger': 'manual'})
        except Exception as exc:
            raise SafetyError('Working-state export failed; profile remains bound to verified BOX identity') from exc
        if not isinstance(exported, dict):
            raise SafetyError('Working-state export returned an invalid response')
        ledger['exportOutcome'] = exported.get('outcome') if exported.get('outcome') in ('exported', 'skipped') else 'unknown'
        ledger['status'] = 'export-attempted'
        atomic_json(ledger_path, ledger)
        # Exporting blobs is not evidence that transcript rows are visible.
        verification = self.verify(aid)
        ledger.update(verification)
        if ledger['exportOutcome'] != 'exported':
            ledger['status'] = 'incomplete'
        atomic_json(ledger_path, ledger)
        return ledger


def run_directory(root, resume=None):
    snapshots = (pathlib.Path(root) / 'recovery-snapshots').resolve()
    if resume:
        run = pathlib.Path(resume).resolve()
        if not run.is_relative_to(snapshots) or not run.is_dir():
            raise SafetyError('Resume directory must be an existing private recovery snapshot')
        return run
    snapshots.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    run = snapshots / (stamp + '-publish-local-' + uuid.uuid4().hex[:8])
    run.mkdir(mode=0o700)
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--plan', action='store_true', help='Read-only plan (default)')
    action.add_argument('--agent-id', help='Publish one existing allowlisted individual UUID')
    action.add_argument('--all-individuals', action='store_true', help='Publish all allowlisted individuals')
    action.add_argument('--verify', action='store_true', help='Read-only BOX identity/transcript verification')
    parser.add_argument('--root', type=pathlib.Path, default=SD)
    parser.add_argument('--resume-run', type=pathlib.Path, help='Reuse a previous private publication ledger')
    args = parser.parse_args(argv)
    if args.resume_run and not (args.agent_id or args.all_individuals):
        parser.error('--resume-run requires an explicit publication action')
    try:
        allowed = load_allowlist(args.root / 'openbot-recovery' / 'roster.json')
        auth = load_helper('diagnose-routing.py')
        gateway = load_helper('update-fleet-instructions.py')
        gateway.SD = args.root
        headers = auth.headers('prod')
        publisher = Publisher(args.root, allowed, lambda method, body: auth.rpc(method, body, headers), gateway.gw)
        run = None
        if args.agent_id or args.all_individuals:
            ids = [args.agent_id] if args.agent_id else list(allowed)
            # Reject out-of-fleet input before creating a ledger or calling APIs.
            for aid in ids:
                canonical_id(aid)
                if aid not in allowed:
                    raise SafetyError('Identity is outside the existing fleet allowlist')
            run = run_directory(args.root, args.resume_run)
            results = []
            for aid in ids:
                try:
                    result = publisher.publish(aid, run)
                except SafetyError as exc:
                    result = {'id': aid, 'status': 'blocked', 'error': str(exc)}
                    atomic_json(run / (aid + '.error.json'), result)
                results.append(result)
                print(json.dumps(result), flush=True)
        elif args.verify:
            rows = publisher.rows()
            results = []
            for aid in allowed:
                try:
                    result = publisher.verify(aid, rows)
                except SafetyError as exc:
                    result = {'id': aid, 'status': 'blocked', 'error': str(exc)}
                results.append(result)
                print(json.dumps(result), flush=True)
        else:
            results = publisher.plan()
            for result in results:
                print(json.dumps(result), flush=True)
        counts = {}
        for result in results:
            counts[result['status']] = counts.get(result['status'], 0) + 1
        print(json.dumps({'counts': counts, 'runDirectory': str(run) if run else None}), flush=True)
        return 1 if counts.get('blocked') or counts.get('incomplete') else 0
    except SafetyError as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
