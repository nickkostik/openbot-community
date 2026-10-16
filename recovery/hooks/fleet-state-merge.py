#!/usr/bin/env python3
"""Preserve all remint branches and build a verified, non-executable history union.

This does not splice encrypted SDK roots, acknowledge pending completions, delete
agent identities, or turn recovered events into new live tasks. Snapshots contain
private conversations and credentials and must never be added to a repository.
"""
import argparse
import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import stat
import time
import uuid

SD = pathlib.Path('/home/box/sand-data')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def canonical_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Non-canonical agent identity')
    return value


def lineages(journal):
    """Only journal-proven ancestry; names are never a duplicate key."""
    edges = {}
    for generation in [*journal.get('previousGenerations', []), journal]:
        for entry in generation.get('entries', []):
            old, new = canonical_id(entry['oldId']), canonical_id(entry['newId'])
            if old == new or (old in edges and edges[old] != new):
                raise ValueError('Ambiguous remint ancestry')
            edges[old] = new
    result = {}
    for aid in set(edges) | set(edges.values()):
        target, seen = aid, set()
        while target in edges:
            if target in seen:
                raise ValueError('Cyclic remint ancestry')
            seen.add(target)
            target = edges[target]
        result.setdefault(target, []).append(aid)
    return {target: sorted(members) for target, members in sorted(result.items())}


def merge_entries(sources, mapping=None):
    """Retain differing same-ID payloads; deduplicate exact normalized records.

The result is an archival view with provenance, not a live automation inbox. IDs
are deterministic across retries. No prompt text, tool result or timestamp is
discarded; every original body is also retained in its branch snapshot.
"""
    mapping = mapping or {}
    groups = {}
    for source, entries in sorted(sources.items()):
        for value in entries:
            if not isinstance(value, dict) or not isinstance(value.get('id'), str):
                raise ValueError('Transcript entry has no string identity')
            raw = json.dumps(value, ensure_ascii=False)
            for old, new in sorted(mapping.items()):
                raw = raw.replace(old, new)
            entry = json.loads(raw)
            key = digest(encoded(entry))
            group = groups.setdefault(entry['id'], {})
            record = group.setdefault(key, {'entry': entry, 'sources': []})
            if source not in record['sources']:
                record['sources'].append(source)
    occupied = set(groups)
    output, conflicts = [], 0
    for original, variants in sorted(groups.items()):
        for index, (sha, record) in enumerate(sorted(variants.items())):
            aid = original
            if index:
                # Full digest avoids truncation collisions. Reserve original IDs.
                aid = original + '~openbot-branch-' + sha
                while aid in occupied:
                    aid += '~'
                occupied.add(aid)
                conflicts += 1
            output.append({'entry': {**record['entry'], 'id': aid},
                           'originalEntryId': original, 'payloadSha256': sha,
                           'sources': sorted(record['sources'])})
    output.sort(key=lambda row: (row['entry'].get('timestampMs', 0), row['entry']['id']))
    return output, conflicts


def read_entries(path):
    if not path.exists():
        return []
    if path.is_symlink():
        raise RuntimeError('Transcript DB must be a regular file')
    # This reader is for completed private backups, not a live WAL database.
    with contextlib.closing(sqlite3.connect('file:' + str(path) + '?mode=ro&immutable=1', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Transcript DB integrity failed')
        return [json.loads(row[0]) for row in db.execute('SELECT entry FROM transcript_entries ORDER BY seq')]


def inventory(root):
    """Hash files; preserve links as links without following external targets."""
    result = []
    for path in sorted(root.rglob('*')):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            result.append({'path': relative, 'link': os.readlink(path)})
        elif path.is_file():
            result.append({'path': relative, 'bytes': path.stat().st_size,
                           'sha256': digest(path.read_bytes())})
    return result


def same_inventory(left, right):
    # Filesystem traversal order is not data. Reject duplicate manifest paths.
    a = {item['path']: item for item in left}
    b = {item['path']: item for item in right}
    return len(a) == len(left) and len(b) == len(right) and a == b


def snapshot_tree(source, destination):
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError('Agent state directory is missing or not regular')
    shutil.copytree(source, destination, symlinks=True,
                    ignore=shutil.ignore_patterns('*.db', '*-wal', '*-shm'))
    for path in source.rglob('*.db'):
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError('SQLite snapshot source must be regular')
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)) as src:
            with contextlib.closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('SQLite backup integrity failed')


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(encoded(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def helper():
    spec = importlib.util.spec_from_file_location('fleet_merge_helper',
        pathlib.Path(__file__).with_name('migrate-local-bots.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preserve(sd=SD, include_platform=False):
    """Private snapshot first; publish only a validated detached history union."""
    sd = pathlib.Path(sd)
    journal_path = sd / 'openbot-box-remint.json'
    before = journal_path.read_bytes()
    cohorts = lineages(json.loads(before))
    mapping = {aid: target for target, members in cohorts.items() for aid in members if aid != target}
    root = sd / 'recovery-snapshots' / ('fleet-state-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, mode=0o700)
    manifest = {'schema': 1, 'privateOnly': True, 'liveApplied': False,
                'journalSha256': digest(before), 'startedAtMs': int(time.time()*1000),
                'identityMapping': mapping,
                'cohorts': [], 'unmappedBranches': [], 'missingSources': [], 'platformErrors': []}
    routing = helper() if include_platform else None
    # Preserve shared tools, skills, MCP/ACP configuration and selected models.
    for filename in ('settings.json', 'secrets.json', 'gateway.json'):
        path = sd / filename
        if path.exists():
            if path.is_symlink():
                raise RuntimeError('Shared state is not a regular file')
            (root / 'shared').mkdir(exist_ok=True, mode=0o700)
            shutil.copy2(path, root / 'shared' / filename)
    for path in sorted(sd.glob('openbot-*')):
        if path.is_file() and not path.is_symlink() and path.suffix in ('.json', '.cjs', '.enable', '.hold', ''):
            (root / 'shared').mkdir(exist_ok=True, mode=0o700)
            shutil.copy2(path, root / 'shared' / path.name)
    for name in ('workflows', 'skills', 'mcp', 'openbot-subagent-state', 'agent-transcripts'):
        path = sd / name
        if path.is_dir() and not path.is_symlink():
            snapshot_tree(path, root / 'shared' / name)
    for target, members in cohorts.items():
        sources = {}
        branches = []
        for aid in members:
            directory = sd / 'agents' / aid
            if not directory.is_dir():
                manifest['missingSources'].append(aid)
            else:
                backup = root / 'branches' / aid
                snapshot_tree(directory, backup)
                sources[aid + ':local'] = read_entries(backup / 'store.db')
                branches.append({'agentId': aid, 'inventory': inventory(backup)})
            transcripts = sd / 'agent-transcripts' / aid
            if transcripts.is_dir() and not transcripts.is_symlink():
                snapshot_tree(transcripts, root / 'session-journals' / aid)
            if include_platform:
                try:
                    rows = routing.fetch_transcript(aid, root)
                    sources[aid + ':platform'] = [json.loads(base64.b64decode(row['body'], validate=True)) for row in rows]
                except Exception as exc:
                    # No auth, response body or chat text in console errors.
                    manifest['platformErrors'].append({'agentId': aid, 'errorType': type(exc).__name__})
                time.sleep(0.15)
        records, conflicts = merge_entries(sources, mapping)
        history = root / 'merged-history' / (target + '.json')
        private_write(history, {'targetAgentId': target, 'archivalOnly': True, 'entries': records})
        manifest['cohorts'].append({'targetAgentId': target, 'members': members,
            'branches': branches, 'mergedEntries': len(records), 'idConflicts': conflicts,
            'historySha256': digest(history.read_bytes()),
            'sourceEntryCount': sum(len(values) for values in sources.values())})
    # An absent journal edge is not evidence that an agent is expendable. Retain
    # unclassified bots, probes and workers without inventing a merge by name.
    known = {aid for members in cohorts.values() for aid in members}
    for directory in sorted((sd / 'agents').iterdir()):
        if directory.name not in known and directory.is_dir() and not directory.is_symlink():
            backup = root / 'unmapped-branches' / directory.name
            snapshot_tree(directory, backup)
            manifest['unmappedBranches'].append({'directory': directory.name, 'inventory': inventory(backup)})
    if journal_path.read_bytes() != before:
        raise RuntimeError('Roster changed during preservation; snapshot is not prune proof')
    manifest['completedAtMs'] = int(time.time()*1000)
    manifest['pruneAllowed'] = False  # Native binding/session verification is separate.
    manifest['treeInventory'] = inventory(root)
    private_write(root / 'manifest.json', manifest)
    verify(root)
    return {'snapshot': str(root), 'cohorts': len(cohorts),
            'branches': sum(len(c['branches']) for c in manifest['cohorts']),
            'entries': sum(c['mergedEntries'] for c in manifest['cohorts']),
            'conflicts': sum(c['idConflicts'] for c in manifest['cohorts']),
            'missingSources': len(manifest['missingSources']),
            'platformErrors': len(manifest['platformErrors']),
            'unmappedBranches': len(manifest['unmappedBranches']), 'liveApplied': False, 'pruneAllowed': False}


def verify(root):
    root = pathlib.Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    actual = [item for item in inventory(root) if item['path'] != 'manifest.json']
    if not same_inventory(actual, manifest['treeInventory']):
        raise RuntimeError('Private preservation tree changed or is incomplete')
    for cohort in manifest['cohorts']:
        for branch in cohort['branches']:
            path = root / 'branches' / canonical_id(branch['agentId'])
            prefix = 'branches/' + branch['agentId'] + '/'
            branch_actual = [{**item, 'path': item['path'][len(prefix):]}
                             for item in actual if item['path'].startswith(prefix)]
            if not same_inventory(branch_actual, branch['inventory']):
                raise RuntimeError('Preserved branch changed or is incomplete')
        path = root / 'merged-history' / (canonical_id(cohort['targetAgentId']) + '.json')
        if digest(path.read_bytes()) != cohort['historySha256']:
            raise RuntimeError('Merged history changed')
        rows = json.loads(path.read_text())['entries']
        if len({r['entry']['id'] for r in rows}) != len(rows):
            raise RuntimeError('Merged history has duplicate identities')
        # Every raw source entry must remain recoverable after normalization.
        mapping = manifest['identityMapping']
        for branch in cohort['branches']:
            aid = branch['agentId']
            source = read_entries(root / 'branches' / aid / 'store.db')
            needed, _ = merge_entries({aid + ':local': source}, mapping)
            hashes = {r['payloadSha256'] for r in rows}
            if not {r['payloadSha256'] for r in needed}.issubset(hashes):
                raise RuntimeError('Merged history omitted a source payload')
        for aid in cohort['members']:
            platform = root / 'cloud-transcripts' / (aid + '.json')
            if platform.exists():
                remote = [json.loads(base64.b64decode(r['body'], validate=True))
                          for r in json.loads(platform.read_text())['entries']]
                needed, _ = merge_entries({aid + ':platform': remote}, mapping)
                if not {r['payloadSha256'] for r in needed}.issubset({r['payloadSha256'] for r in rows}):
                    raise RuntimeError('Merged history omitted a platform payload')
    return {'verified': True, 'liveApplied': False, 'pruneAllowed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'preserve', 'verify'))
    parser.add_argument('--snapshot', type=pathlib.Path)
    parser.add_argument('--include-platform', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'plan':
        groups = lineages(json.loads((SD / 'openbot-box-remint.json').read_text()))
        result = {'cohorts': len(groups), 'identities': sum(map(len, groups.values())), 'mutatesLiveState': False}
    elif args.command == 'verify':
        if args.snapshot is None:
            parser.error('--snapshot is required')
        result = verify(args.snapshot)
    else:
        result = preserve(include_platform=args.include_platform)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
