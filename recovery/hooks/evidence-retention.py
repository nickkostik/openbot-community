"""Retain exact immutable receipt closures across native SQLite exclusions.

This is an evidence archive, not a backup acknowledgement or a receipt validator.
Recovery is explicit, missing-only, and requires the canonical writer flock.
"""
import contextlib
import errno
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import stat
import tarfile
import tempfile
import uuid

LEGACY_SCHEMA = 'openbot-evidence-retention/v1'
SCHEMA = 'openbot-evidence-retention/v2'
LEGACY_REFERENCE_POLICY = 'absolute-reference-closure/v1'
LEGACY_PREPARED_POLICY = 'prepared-return-closure/v1'
REFERENCE_POLICY = 'typed-sdk-reference-closure/v2'
PREPARED_POLICY = 'prepared-return-closure/v2'
SDK_NAMESPACE = '@sdk-inputs/'
LINEAGE_REPORT = 'openbot-lineage-sdk-native-report/v1'
RESTORATION_REPORT = 'openbot-same-id-sdk-restoration/v1'
LINEAGE_REPORT_FIELDS = 'activeReachableRefs activeRootSha256 activeRootUnchanged allHistoricalSourceGraphsArchived blobCount engineSha256 historicalGraphsActivated liveApplied pendingWorkCreated schema sourceRoots sources targetAgentId walkerSha256'
LINEAGE_SOURCE_FIELDS = 'agentId allSourceBlobBytesArchived blobSnapshotRelativePath blobSnapshotSha256 fixedRootRevisions inputSnapshots nativeReachableRefs rootSha256 rootSlot sourceBlobCount sourceStoreRelativePath sourceStoreSha256 unresolvedProtoRefs'
DIRECTORY = 'openbot-evidence-retention'
MAX_FILES = 8192
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
_VALIDATOR = None


class BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, tar):
        # Reject oversized extension headers BEFORE tarfile reads/decodes them.
        # Our producer emits regular files and optional bounded PAX path headers.
        if self.type == tarfile.XHDTYPE:
            if self.size > 65536: raise RuntimeError('Retention archive metadata bound exceeded')
        elif self.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or self.size > MAX_FILE:
            raise RuntimeError('Invalid retention archive member type or size')
        result = super()._proc_member(tar)
        if result is not None and set(result.pax_headers) - {'path'}:
            raise RuntimeError('Unknown retention archive metadata')
        return result


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_digest(root, path, limit):
    """Hash archives without retaining a second compressed copy in memory."""
    relative(root, path); directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise RuntimeError('Retention archive must be bounded and owned')
        result = hashlib.sha256(); size = 0
        while True:
            raw = os.read(fd, 1024 * 1024)
            if not raw: break
            size += len(raw)
            if size > limit: raise RuntimeError('Retention archive grew beyond bound')
            result.update(raw)
        after = os.fstat(fd)
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise RuntimeError('Retention archive changed during read')
        return result.hexdigest()
    finally: os.close(fd)


def validator():
    global _VALIDATOR
    if _VALIDATOR is not None: return _VALIDATOR
    spec = importlib.util.spec_from_file_location('retention_receipt', pathlib.Path(__file__).with_name('history-merge-receipt.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    _VALIDATOR = module
    return _VALIDATOR


def directory(path):
    path = pathlib.Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Retention directory must be absolute')
    for parent in path.parents:
        if parent.is_symlink(): raise RuntimeError('Retention directory is symlinked')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Retention directory must be owned and regular')
    return path


def relative(root, path, legacy=False):
    path = pathlib.Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Retention path must be absolute and contained')
    try: name = path.relative_to(root).as_posix()
    except ValueError: raise RuntimeError('Retention path escapes backup')
    if not name or name == '.' or name.split('/')[0] == DIRECTORY:
        raise RuntimeError('Retention cannot archive its own metadata')
    if not legacy and name.startswith(SDK_NAMESPACE):
        raise RuntimeError('Retention source conflicts with reserved SDK namespace')
    return name


def legacy_policy(policy):
    return policy in (LEGACY_REFERENCE_POLICY, LEGACY_PREPARED_POLICY)


def valid_policy(policy):
    return policy in (REFERENCE_POLICY, PREPARED_POLICY, LEGACY_REFERENCE_POLICY, LEGACY_PREPARED_POLICY)


def sdk_relative(path, owner=None):
    """Only original per-owner SQLite inputs in immutable fleet captures."""
    if not isinstance(path, str): raise RuntimeError('Unsafe typed SDK input path')
    value = pathlib.PurePosixPath(path)
    if (value.is_absolute() or '..' in value.parts or
            value.as_posix() != path or len(value.parts) != 4):
        raise RuntimeError('Unsafe typed SDK input path')
    fleet, branch, agent, filename = value.parts
    if (not re.fullmatch(r'fleet-state-\d{8}-\d{6}-[0-9a-f]{8}', fleet) or branch != 'branches' or
            str(uuid.UUID(agent)) != agent or (owner is not None and agent != owner) or
            filename not in ('store.db', 'conversation-blobs.db')):
        raise RuntimeError('Typed SDK input owner or snapshot root differs')
    return value


def storage_target(root, name, legacy=False):
    """Map archive names, never grant authority; closure validation does that."""
    if not legacy and name.startswith(SDK_NAMESPACE):
        suffix = name[len(SDK_NAMESPACE):]; sdk_relative(suffix)
        return root.parent, root.parent / suffix
    canonical = relative(root, root / name, legacy)
    if canonical != name: raise RuntimeError('Noncanonical retention member path')
    return root, root / name


def storage_name(root, path, mode, legacy=False):
    p = pathlib.Path(path)
    try: p.relative_to(root)
    except ValueError:
        if legacy or not isinstance(mode, tuple) or mode[0] != 'sdk-input':
            raise RuntimeError('Retention path escapes backup')
        if not p.is_absolute() or '..' in p.parts: raise RuntimeError('Unsafe typed SDK input path')
        try: suffix = p.relative_to(root.parent).as_posix()
        except ValueError: raise RuntimeError('Typed SDK input escapes sibling snapshots')
        sdk_relative(suffix, mode[1])
        return SDK_NAMESPACE + suffix
    return relative(root, p, legacy)


def references(value):
    if isinstance(value, dict):
        # Native inventories also use relative path/sha256 pairs. They are not
        # canonical receipt references and must not be interpreted as paths
        # relative to an unrelated JSON file or the active agents directory.
        if isinstance(value.get('path'), str) and value['path'].startswith('/') and 'sha256' in value:
            yield {key: value[key] for key in ('path', 'sha256')}
        for child in value.values(): yield from references(child)
    elif isinstance(value, list):
        for child in value: yield from references(child)


def child_refs(raw):
    # Only JSON evidence contains file references. SQLite/blob bytes stay exact.
    if raw.lstrip()[:1] not in (b'{', b'['): return []
    try: value = json.loads(raw)
    except (ValueError, UnicodeError): raise RuntimeError('Malformed JSON retention evidence')
    return list(references(value))


def prepared_dependencies(raw, reference_policy=REFERENCE_POLICY):
    value = json.loads(raw); validator().exact(value, 'journal prepared indexed')
    journal, prepared = value['journal'], value['prepared']
    if (not isinstance(journal, dict) or not isinstance(value['indexed'], dict) or
            not isinstance(prepared, list) or not 1 <= len(prepared) <= 512 or
            not isinstance(journal.get('entries'), list)):
        raise RuntimeError('Unknown prepared-return retention schema')
    uuid.UUID(journal['officialReturnRequestId'])
    dependencies = []
    for entry in prepared:
        if not isinstance(entry, dict) or not entry.get('historyMergeReceipt') or entry not in journal['entries']:
            raise RuntimeError('Prepared retention entry differs from proposed authority')
        guards = entry.get('returnSourceGuards'); folder = entry.get('returnFolder')
        if not isinstance(guards, dict) or not isinstance(folder, str):
            raise RuntimeError('Prepared retention source inventory is absent')
        for agent, inventory in guards.items():
            if str(uuid.UUID(agent)) != agent or not isinstance(inventory, list):
                raise RuntimeError('Unknown prepared retention source inventory')
            for item in inventory:
                validator().exact(item, 'path sha256 bytes'); validator().sha(item['sha256'])
                if not isinstance(item['path'], str): raise RuntimeError('Unsafe prepared retention source inventory')
                name = pathlib.PurePosixPath(item['path'])
                if (name.is_absolute() or '..' in name.parts or
                        name.as_posix() != item['path'] or item['path'] in ('', '.') or
                        type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_FILE):
                    raise RuntimeError('Unsafe prepared retention source inventory')
                # All captured source files are immutable bytes, including
                # nested session SQL/SDK stores that native backup also omits.
                path = pathlib.Path(folder) / 'sources' / agent / name
                dependencies.append(({'path': str(path), 'sha256': item['sha256']}, 'opaque', item['bytes']))
    # Preserve the previous journal's EXACT JSON as metadata without treating
    # independent sibling generation archives as this receipt's write scope.
    previous = journal['officialReturnPreviousJournal']; validator().exact(previous, 'path sha256')
    return [(ref, reference_policy, None) for ref in references(prepared)] + dependencies + [(previous, 'opaque', None)]


def typed_dependencies(raw):
    if raw.lstrip()[:1] not in (b'{', b'['): return []
    value = json.loads(raw)
    schemas = (validator().SCHEMA, validator().DELTA_SCHEMA, validator().IDENTIFIER_SCHEMA,
               getattr(validator(), 'RESTORATION_SCHEMA', None))
    if isinstance(value, dict) and value.get('schema') in schemas and value.get('schema') is not None:
        # The report pins the pre-remint source checkpoint. Its bytes are
        # copied unchanged when a reviewed baseline acquires the new owner.
        target = value['oldId']
        if str(uuid.UUID(target)) != target: raise RuntimeError('Invalid typed SDK receipt target')
        roots = {}
        for source in value['sources']:
            if source.get('kind') != 'sqlite': continue
            owner, root_sha = source['agentId'], source['sdkRootSha256']
            if owner in roots or str(uuid.UUID(owner)) != owner: raise RuntimeError('Invalid typed SDK source owner')
            if root_sha is not None: validator().sha(root_sha)
            roots[owner] = root_sha
        ordinary = {key: item for key, item in value.items() if key != 'sdkProof'}
        return ([(ref, REFERENCE_POLICY, None) for ref in references(ordinary)] +
                [(value['sdkProof'], ('sdk-proof', target, tuple(sorted(roots.items()))), None)])
    return [(ref, REFERENCE_POLICY, None) for ref in references(value)]


def sdk_proof_dependencies(raw, mode):
    value = json.loads(raw)
    validator().exact(value, 'schema engineSha256 rootSlot rootSha256 blobs sourceRoots nativeReport')
    if value['schema'] != 'openbot-sdk-merge-proof/v1' or value['sourceRoots'] != dict(mode[2]):
        raise RuntimeError('Typed SDK proof differs from its anchored receipt')
    validator().sha(value['engineSha256'])
    return [(value['nativeReport'], ('sdk-report', value['engineSha256'], mode[2], mode[1]), None)]


def sdk_input_dependencies(raw, mode):
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('schema') != LINEAGE_REPORT:
        # Other reviewed native-report schemas still retain their exact bytes
        # and ordinary contained references; they acquire no external access.
        dependencies = typed_dependencies(raw)
        if isinstance(value, dict) and value.get('schema') == RESTORATION_REPORT:
            inputs = value.get('inputFiles')
            if not isinstance(inputs, dict) or not 1 <= len(inputs) <= MAX_FILES:
                raise RuntimeError('Unknown SDK restoration input inventory')
            for path, sha in inputs.items():
                if not isinstance(path, str) or not pathlib.Path(path).is_absolute():
                    raise RuntimeError('Unsafe SDK restoration input path')
                validator().sha(sha)
                dependencies.append(({'path': path, 'sha256': sha}, REFERENCE_POLICY, None))
        return dependencies
    validator().exact(value, LINEAGE_REPORT_FIELDS)
    if (value['engineSha256'] != mode[1] or value['sourceRoots'] != dict(mode[2]) or
            value['walkerSha256'] != validator().IDENTIFIER_WORKER_SHA or
            any(value[k] is not True for k in ('activeRootUnchanged', 'allHistoricalSourceGraphsArchived')) or
            any(value[k] is not False for k in ('liveApplied', 'historicalGraphsActivated', 'pendingWorkCreated')) or
            value['targetAgentId'] != mode[3] or
            not isinstance(value['sources'], list) or not 0 <= len(value['sources']) <= 512 or
            any(type(value[k]) is not int or not 0 <= value[k] <= MAX_FILES * MAX_FILE
                for k in ('activeReachableRefs', 'blobCount'))):
        raise RuntimeError('Typed SDK input provenance differs from pinned proof')
    dependencies = []; owners = set()
    for source in value['sources']:
        validator().exact(source, LINEAGE_SOURCE_FIELDS)
        owner = source['agentId']
        if (owner in owners or owner not in value['sourceRoots'] or str(uuid.UUID(owner)) != owner or
                source['rootSha256'] != value['sourceRoots'][owner] or source['allSourceBlobBytesArchived'] is not True or
                source['sourceStoreRelativePath'] != 'sources/' + owner + '/store.db' or
                source['blobSnapshotRelativePath'] != 'sources/' + owner + '/conversation-blobs.db' or
                type(source['sourceBlobCount']) is not int or not 0 <= source['sourceBlobCount'] <= MAX_FILES * MAX_FILE or
                type(source['nativeReachableRefs']) is not int or not 0 <= source['nativeReachableRefs'] <= MAX_FILES * MAX_FILE or
                type(source['unresolvedProtoRefs']) is not int or source['unresolvedProtoRefs'] != 0):
            raise RuntimeError('Typed SDK input source ownership differs')
        owners.add(owner); snapshots = source['inputSnapshots']; filenames = set(); source_parent = None
        if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 2:
            raise RuntimeError('Typed SDK input snapshot list differs')
        for reference in snapshots:
            validator().exact(reference, 'path sha256'); validator().sha(reference['sha256'])
            path = pathlib.Path(reference['path'])
            if path.parent.name != owner or path.name in filenames or path.name not in ('store.db', 'conversation-blobs.db'):
                raise RuntimeError('Typed SDK input snapshot owner differs')
            if path.name == 'store.db' and reference['sha256'] != source['sourceStoreSha256']:
                raise RuntimeError('Typed SDK source store hash differs')
            if source_parent is not None and path.parent != source_parent:
                raise RuntimeError('Typed SDK inputs come from different captures')
            source_parent = path.parent
            filenames.add(path.name); dependencies.append((reference, ('sdk-input', owner), None))
        # A separate blob store is optional: original SDK blobs can live in
        # store.db itself. Keep precisely the producer's pinned raw inputs.
        if 'store.db' not in filenames:
            raise RuntimeError('Typed SDK input graph source is incomplete')
    if owners != set(value['sourceRoots']): raise RuntimeError('Typed SDK source coverage differs')
    return dependencies


def closure(root, reference, read, policy=REFERENCE_POLICY):
    if not valid_policy(policy): raise RuntimeError('Unknown retention dependency policy')
    legacy = legacy_policy(policy)
    pending = [(reference, policy, None)]; found = {}; expanded = set(); total = 0
    while pending:
        ref, mode, expected_size = pending.pop(); validator().exact(ref, 'path sha256'); validator().sha(ref['sha256'])
        name = storage_name(root, ref['path'], mode, legacy)
        if name in found:
            if found[name]['sha256'] != ref['sha256']: raise RuntimeError('Retention reference hash conflicts')
            if expected_size is not None and found[name]['size'] != expected_size:
                raise RuntimeError('Prepared retention source inventory size differs')
            if (name, mode) in expanded: continue
        elif len(found) >= MAX_FILES: raise RuntimeError('Retention closure file bound exceeded')
        raw = read(name)
        if len(raw) > MAX_FILE or digest(raw) != ref['sha256']:
            raise RuntimeError('Retention immutable evidence differs')
        if expected_size is not None and len(raw) != expected_size:
            raise RuntimeError('Prepared retention source inventory size differs')
        if name not in found: total += len(raw)
        if total > MAX_TOTAL: raise RuntimeError('Retention closure byte bound exceeded')
        found[name] = {'path': name, 'sha256': ref['sha256'], 'size': len(raw)}
        expanded.add((name, mode))
        if mode in (PREPARED_POLICY, LEGACY_PREPARED_POLICY):
            pending.extend(prepared_dependencies(raw, LEGACY_REFERENCE_POLICY if legacy else REFERENCE_POLICY))
        elif mode == LEGACY_REFERENCE_POLICY:
            pending.extend((child, LEGACY_REFERENCE_POLICY, None) for child in child_refs(raw))
        elif mode == REFERENCE_POLICY: pending.extend(typed_dependencies(raw))
        elif isinstance(mode, tuple) and mode[0] == 'sdk-proof': pending.extend(sdk_proof_dependencies(raw, mode))
        elif isinstance(mode, tuple) and mode[0] == 'sdk-report': pending.extend(sdk_input_dependencies(raw, mode))
        elif isinstance(mode, tuple) and mode[0] == 'sdk-input': pass  # Exact SQLite bytes; no interpretation or rewriting.
        elif mode != 'opaque': raise RuntimeError('Unknown retention dependency policy')
    return sorted(found.values(), key=lambda item: item['path'])


def read_evidence(root, name, legacy=False):
    container, path = storage_target(root, name, legacy)
    wal = pathlib.Path(str(path) + '-wal')
    def wal_state():
        try: info = wal.lstat()
        except FileNotFoundError: return None
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    before = wal_state()
    raw = validator().checked(container, path)
    if raw.startswith(b'SQLite format 3\x00'):
        # Captures must be normalized BEFORE their receipt hash is produced.
        # Never recreate an already pinned database using SQLite backup().
        after = wal_state()
        if before != after:
            raise RuntimeError('Retention SQLite WAL changed during read')
        if after is not None and (legacy or not name.startswith(SDK_NAMESPACE) or
                not stat.S_ISREG(after[2]) or after[3] != os.getuid() or after[4] != 0):
            raise RuntimeError('Retention requires a normalized immutable SQLite capture')
        # Original, already accepted SDK inputs may have stable empty sidecars.
        # Zero WAL bytes carry no uncheckpointed rows; preserve the pinned main
        # file exactly, without opening SQLite or rewriting/deleting sidecars.
    return raw


@contextlib.contextmanager
def parent_fd(root, path, make=False):
    relative(root, path); directory(root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.relative_to(root).parts[:-1]:
            if make:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd); os.fsync(fd)
                except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            info = os.fstat(child)
            if info.st_uid != os.getuid():
                os.close(child); raise RuntimeError('Retention parent is foreign')
            os.close(fd); fd = child
        yield fd
    finally: os.close(fd)


def create(path, raw, root=None):
    """Fsync a private temporary file, then atomically create without overwrite."""
    with parent_fd(root or path.parent, path, make=True) as parent:
        name = '.retention-' + os.urandom(12).hex()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            try: os.link(name, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                existing = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(existing, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                            info.st_size != len(raw) or stream.read(len(raw) + 1) != raw):
                        raise RuntimeError('Existing retention evidence differs')
            os.fsync(parent)
        finally: os.unlink(name, dir_fd=parent)


def locations(root, reference, legacy=False):
    validator().exact(reference, 'path sha256'); validator().sha(reference['sha256'])
    relative(root, reference['path'])
    base = root / DIRECTORY
    prefix = '' if legacy else 'v2-'
    return base, base / (prefix + reference['sha256'] + '.json'), base / (prefix + reference['sha256'] + '.tar.gz')


def retain(root, reference, policy=REFERENCE_POLICY):
    """Called after canonical validation and before accepting its exact receipt."""
    root = directory(root); legacy = legacy_policy(policy); base, metadata, archive = locations(root, reference, legacy)
    if not base.exists():
        base.mkdir(mode=0o700)
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    directory(base)
    if not valid_policy(policy): raise RuntimeError('Unknown retention dependency policy')
    entries = closure(root, reference, lambda name: read_evidence(root, name, legacy), policy)
    # Tar metadata is deterministic; gzip mtime is fixed for resumable creation.
    fd, temporary = tempfile.mkstemp(prefix='.archive-', dir=base)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            with gzip.GzipFile(fileobj=stream, mode='wb', mtime=0, filename='') as zipped:
                with tarfile.open(fileobj=zipped, mode='w|', format=tarfile.PAX_FORMAT) as tar:
                    for entry in entries:
                        raw = read_evidence(root, entry['path'], legacy)
                        if digest(raw) != entry['sha256']: raise RuntimeError('Retention source changed during capture')
                        info = tarfile.TarInfo(entry['path']); info.size = len(raw); info.mode = 0o600
                        tar.addfile(info, io.BytesIO(raw))
                        if stream.tell() > MAX_ARCHIVE: raise RuntimeError('Retention compressed byte bound exceeded')
            stream.flush(); os.fsync(stream.fileno())
        if pathlib.Path(temporary).stat().st_size > MAX_ARCHIVE:
            raise RuntimeError('Retention compressed byte bound exceeded')
        archive_sha = file_digest(base, pathlib.Path(temporary), MAX_ARCHIVE)
        manifest = {'schema': LEGACY_SCHEMA if legacy else SCHEMA, 'policy': policy, 'root': str(root), 'receipt': reference,
                    'archiveSha256': archive_sha, 'files': entries}
        if len(encoded(manifest)) > MAX_MANIFEST: raise RuntimeError('Retention manifest bound exceeded')
        try: os.link(temporary, archive, follow_symlinks=False)
        except FileExistsError:
            if file_digest(base, archive, MAX_ARCHIVE) != archive_sha:
                raise RuntimeError('Existing retention archive differs')
        create(metadata, encoded(manifest))
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
    return {'files': len(entries), 'archiveSha256': manifest['archiveSha256'], 'uploaded': False}


def retain_prepared(root, reference):
    return retain(root, reference, PREPARED_POLICY)


def require_lock(sd, fd):
    if fd is None: raise RuntimeError('Retention recovery requires the dispatch writer lock')
    expected = pathlib.Path(sd) / 'openbot-custom-dispatch.lock'
    actual = os.fstat(fd); info = expected.lstat()
    if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(actual.st_mode) or
            (actual.st_dev, actual.st_ino, actual.st_uid) != (info.st_dev, info.st_ino, os.getuid())):
        raise RuntimeError('Retention dispatch writer lock differs')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def restore(root, reference, sd, lock_fd, policy=REFERENCE_POLICY):
    """Restore only absent, exact evidence after checking the ENTIRE closure."""
    require_lock(sd, lock_fd); root = directory(root)
    if not valid_policy(policy): raise RuntimeError('Unknown retention dependency policy')
    legacy = legacy_policy(policy); base, metadata, archive = locations(root, reference, legacy)
    if not legacy and not metadata.exists() and not metadata.is_symlink():
        # Read old successful v1 archives under their original stricter scope;
        # do not rewrite their metadata or grant them v2 external capabilities.
        old = LEGACY_PREPARED_POLICY if policy == PREPARED_POLICY else LEGACY_REFERENCE_POLICY
        return restore(root, reference, sd, lock_fd, old)
    if not metadata.exists() and not metadata.is_symlink():
        return {'restored': 0, 'retained': False}  # Legacy validation still fails closed if evidence is absent.
    directory(base)
    manifest = json.loads(validator().checked(base, metadata, MAX_MANIFEST))
    validator().exact(manifest, 'schema policy root receipt archiveSha256 files')
    if (manifest['policy'] != policy or manifest['schema'] != (LEGACY_SCHEMA if legacy else SCHEMA) or
            manifest['root'] != str(root) or manifest['receipt'] != reference):
        raise RuntimeError('Retention manifest belongs to different evidence')
    if not isinstance(manifest['files'], list) or not 1 <= len(manifest['files']) <= MAX_FILES:
        raise RuntimeError('Retention manifest file bound exceeded')
    expected = {}
    for entry in manifest['files']:
        validator().exact(entry, 'path sha256 size'); validator().sha(entry['sha256'])
        name = entry['path']; storage_target(root, name, legacy)
        if name in expected or type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_FILE:
            raise RuntimeError('Invalid retention member')
        expected[name] = entry
    if sum(item['size'] for item in expected.values()) > MAX_TOTAL:
        raise RuntimeError('Retention manifest byte bound exceeded')
    missing = False
    for name, item in expected.items():
        try:
            container, path = storage_target(root, name, legacy)
            with parent_fd(container, path) as parent:
                fd = os.open(pathlib.Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        raise RuntimeError('Existing retention evidence is foreign or not regular')
                    if info.st_size != item['size']:
                        raise RuntimeError('Existing immutable evidence differs; recovery refused')
                finally: os.close(fd)
        except FileNotFoundError: missing = True
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise RuntimeError('Retention evidence path is symlinked or not regular') from exc
            raise
    if not missing:
        # This is admission preflight, not validation. Avoid inflating every
        # historical closure on each scheduler tick. The unchanged canonical
        # validators still hash and validate the selected complete parent chain.
        return {'restored': 0, 'retained': True, 'validationRequired': True}
    archive_sha = file_digest(base, archive, MAX_ARCHIVE)
    if archive_sha != validator().sha(manifest['archiveSha256']): raise RuntimeError('Retention archive changed')
    with tempfile.TemporaryDirectory(prefix='.restore-', dir=base) as stage:
        staging = pathlib.Path(stage); seen = set()
        with tarfile.open(archive, mode='r|gz', tarinfo=BoundedTarInfo) as tar:
            for member in tar:
                item = expected.get(member.name)
                if not member.isfile() or item is None or member.name in seen or member.size != item['size']:
                    raise RuntimeError('Invalid or duplicate retention archive member')
                seen.add(member.name)
                with tar.extractfile(member) as stream: value = stream.read(MAX_FILE + 1)
                if len(value) != item['size'] or digest(value) != item['sha256']:
                    raise RuntimeError('Retention archive member changed')
                # Stage by digest, never archive path; no extractall or links.
                (staging / digest(member.name.encode())).write_bytes(value)
        if seen != set(expected): raise RuntimeError('Retention archive is incomplete')
        if file_digest(base, archive, MAX_ARCHIVE) != archive_sha:
            raise RuntimeError('Retention archive changed during recovery')
        def staged(name):
            path = staging / digest(name.encode())
            if not path.exists(): raise RuntimeError('Retention reference is absent from archive')
            return path.read_bytes()
        if closure(root, reference, staged, policy) != manifest['files']:
            raise RuntimeError('Retention archive does not match exact receipt closure')
        # Refuse every conflict before making even the first absent file.
        for name, item in expected.items():
            container, path = storage_target(root, name, legacy); cursor = container
            for part in path.relative_to(container).parts[:-1]:
                cursor /= part
                if cursor.exists() or cursor.is_symlink(): directory(cursor)
            if path.exists() or path.is_symlink():
                if digest(validator().checked(container, path)) != item['sha256']:
                    raise RuntimeError('Existing immutable evidence differs; recovery refused')
        restored = 0
        for name in sorted(expected):
            require_lock(sd, lock_fd); container, path = storage_target(root, name, legacy)
            if path.exists(): continue
            create(path, staged(name), container); restored += 1
        return {'restored': restored, 'retained': True}


def recover_journal(journal, sd, lock_fd):
    """Explicit dispatch preflight; normal receipt validators remain read-only."""
    restored = 0; seen = set()
    for generation in [*journal.get('previousGenerations', []), journal]:
        for entry in generation.get('entries', []):
            ref = entry.get('historyMergeReceipt')
            if ref and ref['sha256'] not in seen:
                require_lock(sd, lock_fd)
                seen.add(ref['sha256'])
                restored += restore(generation['backup'], ref, sd, lock_fd)['restored']
    return {'restored': restored, 'receipts': len(seen)}


def recover_pending(journal, sd, lock_fd, job):
    """Recover pinned PREPARED intents before a resumed writer inspects SQL."""
    require_lock(sd, lock_fd); root = pathlib.Path(journal['backup']); restored = 0
    spec = importlib.util.spec_from_file_location('retention_tail', pathlib.Path(__file__).with_name('history-tail-repair.py'))
    tail = importlib.util.module_from_spec(spec); spec.loader.exec_module(tail)
    for _, state in tail.tail_rounds(journal, job):
        for reference in state.get('intents', []):
            restored += restore(root, reference, sd, lock_fd)['restored']
    path = pathlib.Path(sd) / 'openbot-return-handoff.json'
    if path.exists() or path.is_symlink():
        state = json.loads(validator().checked(pathlib.Path(sd), path))
        if state.get('jobId') == job and state.get('preparedIntent'):
            restored += restore(root, state['preparedIntent'], sd, lock_fd, PREPARED_POLICY)['restored']
    return {'restored': restored}
