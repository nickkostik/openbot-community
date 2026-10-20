"""Native exclusion recovery preserves exact receipt parents and SDK evidence."""
import base64
import contextlib
import copy
import fcntl
import gzip
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sqlite3
import tarfile
import tempfile
import types
import unittest
from unittest import mock
import test_history_merge_receipt as fixture
import test_history_identifier_projection as identifiers


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), pathlib.Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


E = load('evidence-retention'); H = fixture.H; OLD = fixture.OLD; NEW = fixture.NEW


def ref(path): return {'path': str(path), 'sha256': E.digest(path.read_bytes())}


class RetentionTests(unittest.TestCase):
    setUp = fixture.ReceiptTests.setUp
    write = fixture.ReceiptTests.write
    rpc = fixture.ReceiptTests.rpc
    gateway = fixture.ReceiptTests.gateway
    prepare_projection = identifiers.IdentifierProjectionTests.prepare_projection

    def prepare_receipt(self):
        path = fixture.ReceiptTests.prepare_receipt(self)
        empty = path.parent / 'empty-cloud.json'; self.write(empty, {'generation': 1, 'entries': []})
        self.receipt['sources'].append({'agentId': OLD, 'kind': 'platform', **ref(empty),
                                       'generation': 1, 'sdkRootSha256': None, 'blobSnapshot': None})
        self.receipt['sourceOrders'].append({'sourceId': OLD, 'sourceKind': 'platform', 'entryIds': []})
        self.write(path, self.receipt); return path

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(self.root / 'openbot-custom-dispatch.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); yield fd
        finally: os.close(fd)

    def exclude_native_databases(self):
        # Exact problematic native backup basenames, including SDK stores at
        # ANY depth; only separately added live agents' stores survive backup.
        for path in self.backup.rglob('*'):
            if path.is_file() and path.name in ('store.db', 'conversation-blobs.db'):
                path.unlink()

    def tail(self, parent, number):
        value = json.loads(parent.read_bytes()); value.update(schema=H.DELTA_SCHEMA, previousReceipt=ref(parent))
        value.pop('identifierProof', None)
        folder = parent.parent / ('tail-' + str(number)); folder.mkdir()
        for key, name in [('store', 'store.db'), ('blobs', 'conversation-blobs.db')]:
            target = folder / name; shutil.copyfile(value['baseline'][key]['path'], target)
            value['baseline'][key] = ref(target)
        event = {'id': 'tail-' + str(number), 'kind': 'message', 'role': 'user', 'content': 'new input ' + str(number)}
        for path in [pathlib.Path(value['baseline']['store']['path']), self.root / 'agents' / NEW / 'store.db']:
            with contextlib.closing(sqlite3.connect(path)) as conn:
                seq = conn.execute('SELECT MAX(seq) FROM transcript_entries').fetchone()[0] + 1
                conn.execute('INSERT INTO transcript_entries VALUES(?,?,?)', (seq, event['id'], json.dumps(event))); conn.commit()
        value['baseline']['store'] = ref(pathlib.Path(value['baseline']['store']['path']))
        source = next(s for s in value['sources'] if s['kind'] == 'platform')
        captured = json.loads(pathlib.Path(source['path']).read_bytes())
        captured['entries'].append({'seq': str(number), 'body': base64.b64encode(json.dumps(event).encode()).decode()})
        cloud = folder / 'cloud.json'; self.write(cloud, captured); source.update(ref(cloud))
        next(item for item in value['sourceOrders'] if item['sourceKind'] == 'platform')['entryIds'].append(event['id'])
        value['projections'].append({'sourceId': OLD, 'sourceKind': 'platform', 'sourceEntryId': event['id'], 'targetEntryId': event['id']})
        receipt = folder / 'receipt.json'; self.write(receipt, value)
        self.r.accept_history_merge(NEW, str(receipt)); return receipt

    def test_native_exclusion_after_identifier_and_two_tails_restores_exact_parent_chain(self):
        original, projection, *_ = self.prepare_projection()
        self.r.accept_history_merge(NEW, str(projection))
        tail1 = self.tail(projection, 1); tail2 = self.tail(tail1, 2)
        journal = self.r.read(); expected = H.validate(self.r, journal, journal['entries'][0])
        pinned = {str(p): p.read_bytes() for p in self.backup.rglob('*') if p.is_file() and E.DIRECTORY not in p.parts}
        live = {str(p): p.read_bytes() for p in (self.root / 'agents').rglob('*') if p.is_file()}
        self.exclude_native_databases()
        with self.assertRaises(FileNotFoundError): H.validate(self.r, journal, journal['entries'][0])
        with self.locked() as fd:
            result = E.recover_journal(journal, self.root, fd)
            self.assertGreater(result['restored'], 0)
            self.assertEqual(E.recover_journal(journal, self.root, fd)['restored'], 0)
        self.assertEqual(H.validate(self.r, journal, journal['entries'][0]), expected)
        self.assertEqual({path: pathlib.Path(path).read_bytes() for path in pinned}, pinned)
        self.assertEqual({path: pathlib.Path(path).read_bytes() for path in live}, live)
        self.assertEqual(journal['entries'][0]['historyMergeReceipt'], ref(tail2))
        self.assertEqual(json.loads(projection.read_bytes())['previousReceipt'], ref(original))

    def test_full_sdk_blob_store_and_source_root_survive_exclusion(self):
        path = self.prepare_receipt(); value = json.loads(path.read_bytes())
        # Relocate captured native SQL names; never relocate the live store.
        for key, name in [('store', 'store.db'), ('blobs', 'conversation-blobs.db')]:
            target = path.parent / 'baseline' / name; target.parent.mkdir(exist_ok=True)
            shutil.copyfile(value['baseline'][key]['path'], target); value['baseline'][key] = ref(target)
        source = value['sources'][0]
        for key, name in [('path', 'store.db'), ('blobSnapshot', 'conversation-blobs.db')]:
            target = path.parent / 'sources' / OLD / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source[key] if key == 'path' else source[key]['path'], target)
            if key == 'path': source.update(ref(target))
            else: source[key] = ref(target)
        blobs = {'root': b'{"child":"child","source":"source-root"}', 'child': b'complete prior context', 'source-root': b'original context'}
        for target in [source['blobSnapshot']['path'], value['baseline']['blobs']['path']]:
            with contextlib.closing(sqlite3.connect(target)) as conn:
                conn.executemany('INSERT INTO blobs VALUES(?,?)', blobs.items()); conn.commit()
        for target, slot in [(source['path'], 'source-root'), (value['baseline']['store']['path'], 'root')]:
            with contextlib.closing(sqlite3.connect(target)) as conn:
                metadata = json.loads(bytes.fromhex(conn.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()[0]))
                metadata['latestRootBlobId'] = slot
                conn.execute("UPDATE kv SET value=? WHERE key='metadata'", (json.dumps(metadata).encode().hex(),)); conn.commit()
        source.update(ref(pathlib.Path(source['path']))); source['blobSnapshot'] = ref(pathlib.Path(source['blobSnapshot']['path']))
        source['sdkRootSha256'] = E.digest(blobs['source-root'])
        for key in ('store', 'blobs'): value['baseline'][key] = ref(pathlib.Path(value['baseline'][key]['path']))
        sdk = pathlib.Path(value['sdkProof']['path']); proof = json.loads(sdk.read_bytes())
        proof.update(rootSha256=E.digest(blobs['root']), blobs={k: E.digest(v) for k, v in blobs.items()}, sourceRoots={OLD: source['sdkRootSha256']})
        self.write(sdk, proof); value['sdkProof'] = ref(sdk); self.write(path, value)
        self.r.accept_history_merge(NEW, str(path)); journal = self.r.read()
        self.exclude_native_databases()
        with self.locked() as fd: E.recover_journal(journal, self.root, fd)
        H.validate(self.r, journal, journal['entries'][0])
        with contextlib.closing(sqlite3.connect(value['baseline']['blobs']['path'])) as conn:
            self.assertEqual(dict(conn.execute('SELECT id,data FROM blobs')), blobs)

    def test_committed_wal_is_normalized_before_new_snapshot_hash_without_source_changes(self):
        source = self.backup / 'wal-source'; source.mkdir(); writer = sqlite3.connect(source / 'store.db')
        self.addCleanup(writer.close); writer.execute('PRAGMA journal_mode=WAL'); writer.execute('CREATE TABLE entries(value)')
        writer.execute('INSERT INTO entries VALUES(?)', ('committed only in WAL',)); writer.commit()
        original = {p.name: p.read_bytes() for p in source.iterdir()}
        with self.assertRaisesRegex(RuntimeError, 'normalized'):
            E.retain(self.backup, ref(source / 'store.db'))
        target = self.backup / 'snapshot'
        # Use the production FreshReturn capture: stage raw DB+WAL before opening
        # SQLite, so even the native SHM read marks remain byte-exact.
        capture = load('official-return-handoff').Capture(types.SimpleNamespace(root=self.root, r=self.r, require=lambda: None))
        capture.database(source / 'store.db', target / 'store.db')
        self.assertEqual({p.name: p.read_bytes() for p in source.iterdir()}, original)
        store = target / 'store.db'; manifest = self.backup / 'capture.json'; self.write(manifest, {'store': ref(store)})
        E.retain(self.backup, ref(manifest)); expected = store.read_bytes(); store.unlink()
        with self.locked() as fd: E.restore(self.backup, ref(manifest), self.root, fd)
        self.assertEqual(store.read_bytes(), expected)
        with contextlib.closing(sqlite3.connect(store)) as conn:
            self.assertEqual(conn.execute('SELECT value FROM entries').fetchone()[0], 'committed only in WAL')
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'delete')

    def simple(self):
        folder = self.backup / 'simple'; folder.mkdir()
        a = folder / 'store.db'; a.write_bytes(b'first immutable bytes')
        b = folder / 'conversation-blobs.db'; b.write_bytes(b'full context bytes')
        receipt = folder / 'receipt.json'; self.write(receipt, {'baseline': {'store': ref(a), 'blobs': ref(b)}})
        E.retain(self.backup, ref(receipt)); return receipt, a, b

    def test_conflicting_existing_file_refuses_before_restoring_any_file(self):
        receipt, a, b = self.simple(); a.unlink(); b.write_bytes(b'changed')
        with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'differs'):
            E.restore(self.backup, ref(receipt), self.root, fd)
        self.assertFalse(a.exists()); self.assertEqual(b.read_bytes(), b'changed')

    def test_interrupted_missing_only_restore_resumes_without_overwriting(self):
        receipt, a, b = self.simple(); a.unlink(); b.unlink(); original = E.create; count = [0]
        def fault(path, raw, root=None):
            count[0] += 1
            if count[0] == 2: raise InterruptedError()
            return original(path, raw, root)
        with self.locked() as fd:
            with mock.patch.object(E, 'create', side_effect=fault), self.assertRaises(InterruptedError):
                E.restore(self.backup, ref(receipt), self.root, fd)
            result = E.restore(self.backup, ref(receipt), self.root, fd)
            self.assertEqual(result['restored'], 1)
        self.assertEqual(a.read_bytes(), b'first immutable bytes'); self.assertEqual(b.read_bytes(), b'full context bytes')

    def test_lock_symlink_and_foreign_owner_are_rejected(self):
        receipt, a, _ = self.simple(); a.unlink()
        with self.assertRaisesRegex(RuntimeError, 'writer lock'): E.restore(self.backup, ref(receipt), self.root, None)
        with self.locked() as fd:
            other = os.open(self.root / 'other.lock', os.O_RDWR | os.O_CREAT, 0o600)
            try:
                with self.assertRaisesRegex(RuntimeError, 'differs'): E.restore(self.backup, ref(receipt), self.root, other)
            finally: os.close(other)
            a.symlink_to(self.root / 'agents' / NEW / 'store.db')
            with self.assertRaisesRegex(RuntimeError, 'symlinked|regular'): E.restore(self.backup, ref(receipt), self.root, fd)
            a.unlink()
            if os.getuid() == 0:
                os.chown(receipt, 65534, 65534)
                try:
                    with self.assertRaisesRegex(RuntimeError, 'foreign'): E.restore(self.backup, ref(receipt), self.root, fd)
                finally: os.chown(receipt, 0, 0)

    def test_corrupt_archive_and_manifest_path_escape_are_rejected_before_writes(self):
        receipt, a, b = self.simple(); _, metadata, archive = E.locations(self.backup, ref(receipt))
        a.unlink(); b.unlink(); raw = archive.read_bytes(); archive.write_bytes(raw[:-5])
        with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'archive changed'):
            E.restore(self.backup, ref(receipt), self.root, fd)
        archive.write_bytes(raw); value = json.loads(metadata.read_bytes()); value['files'][0]['path'] = '../outside'
        self.write(metadata, value)
        with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'contained'):
            E.restore(self.backup, ref(receipt), self.root, fd)
        self.assertFalse(a.exists()); self.assertFalse(b.exists())

    def test_archive_duplicate_link_missing_extra_and_oversize_members_are_rejected(self):
        receipt, a, b = self.simple(); reference = ref(receipt); _, metadata, archive = E.locations(self.backup, reference)
        manifest = json.loads(metadata.read_bytes()); original = archive.read_bytes(); a.unlink(); b.unlink()
        with tarfile.open(fileobj=io.BytesIO(original), mode='r:gz') as tar:
            values = [(member.name, tar.extractfile(member).read()) for member in tar]
        for fault in ('duplicate', 'link', 'missing', 'extra', 'oversize'):
            with self.subTest(fault=fault):
                result = io.BytesIO()
                with tarfile.open(fileobj=result, mode='w:gz') as tar:
                    selected = values[:-1] if fault == 'missing' else values
                    if fault == 'duplicate': selected = [*selected, selected[0]]
                    if fault == 'extra': selected = [*selected, ('unreferenced', b'x')]
                    for index, (name, raw) in enumerate(selected):
                        info = tarfile.TarInfo(name); info.size = len(raw)
                        if index == 0 and fault == 'link': info.type = tarfile.SYMTYPE; info.linkname = '/outside'; info.size = 0
                        if index == 0 and fault == 'oversize': raw += b'x'; info.size += 1
                        tar.addfile(info, io.BytesIO(raw))
                archive.write_bytes(result.getvalue()); modified = copy.deepcopy(manifest)
                modified['archiveSha256'] = E.digest(result.getvalue()); self.write(metadata, modified)
                with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'member|incomplete'):
                    E.restore(self.backup, reference, self.root, fd)
                self.assertFalse(a.exists()); self.assertFalse(b.exists())

    def test_retention_failure_never_accepts_receipt_and_validators_remain_read_only(self):
        path = self.prepare_receipt(); journal = self.r.read()
        with mock.patch.object(E, 'MAX_FILES', 1), self.assertRaisesRegex(RuntimeError, 'bound'):
            E.retain(self.backup, ref(path))
        before = {str(p): p.read_bytes() for p in self.backup.rglob('*') if p.is_file()}
        H.validate(self.r, journal, journal['entries'][0], ref(path))
        self.assertEqual({str(p): p.read_bytes() for p in self.backup.rglob('*') if p.is_file()}, before)
        with mock.patch.object(H.importlib.util, 'spec_from_file_location', side_effect=RuntimeError('archive unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'archive unavailable'): self.r.accept_history_merge(NEW, str(path))
        self.assertNotIn('historyMergeReceipt', self.r.read()['entries'][0])

    def test_capture_is_create_only_idempotent_and_archive_limits_fail_closed(self):
        receipt, a, _ = self.simple(); reference = ref(receipt)
        _, metadata, archive = E.locations(self.backup, reference)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (metadata, archive)}
        E.retain(self.backup, reference)
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}, before)
        a.unlink()
        with self.locked() as fd, mock.patch.object(E, 'MAX_TOTAL', 1), self.assertRaisesRegex(RuntimeError, 'byte bound'):
            E.restore(self.backup, reference, self.root, fd)
        self.assertFalse(a.exists())
        if os.getuid() == 0:
            os.chown(archive, 65534, 65534)
            try:
                with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'owned'):
                    E.restore(self.backup, reference, self.root, fd)
            finally: os.chown(archive, 0, 0)

    def test_oversized_pax_metadata_is_rejected_before_tarfile_decodes_it(self):
        receipt, a, _ = self.simple(); reference = ref(receipt)
        _, metadata, archive = E.locations(self.backup, reference)
        header = tarfile.TarInfo('pax'); header.type = tarfile.XHDTYPE; header.size = 65537
        raw = gzip.compress(header.tobuf() + b'\x00' * (65537 + 1024))
        archive.write_bytes(raw); manifest = json.loads(metadata.read_bytes()); manifest['archiveSha256'] = E.digest(raw)
        self.write(metadata, manifest); a.unlink()
        with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'metadata bound'):
            E.restore(self.backup, reference, self.root, fd)
        self.assertFalse(a.exists())

    def prepared(self):
        receipt, _, _ = self.simple(); folder = receipt.parent
        source = folder / 'sources' / OLD; source.mkdir(parents=True)
        profile = source / 'profile.json'
        self.write(profile, {'toolConfig': {'path': '/outside/private-file', 'sha256': 'f' * 64}})
        entry = {'newId': NEW, 'historyMergeReceipt': ref(receipt), 'returnFolder': str(folder),
            'returnSourceGuards': {OLD: [{'path': 'profile.json', 'sha256': ref(profile)['sha256'], 'bytes': profile.stat().st_size}]}}
        previous = folder / 'previous-journal.json'; self.write(previous, {'previousGenerations': []})
        value = {'journal': {'entries': [entry], 'officialReturnRequestId': NEW, 'officialReturnPreviousJournal': ref(previous)},
                 'prepared': [entry], 'indexed': {}}
        intent = folder / 'prepared-intent.json'; self.write(intent, value)
        return intent, value, profile

    def test_typed_inventory_bytes_are_opaque_and_restore_requires_exact_policy(self):
        intent, value, profile = self.prepared(); reference = ref(intent)
        E.retain_prepared(self.backup, reference); expected = profile.read_bytes(); profile.unlink()
        with self.locked() as fd:
            with self.assertRaisesRegex(RuntimeError, 'different evidence'):
                E.restore(self.backup, reference, self.root, fd)
            E.restore(self.backup, reference, self.root, fd, E.PREPARED_POLICY)
        self.assertEqual(profile.read_bytes(), expected)
        self.assertEqual(json.loads(profile.read_bytes())['toolConfig']['path'], '/outside/private-file')

    def test_typed_inventory_declared_size_path_and_owner_are_enforced(self):
        intent, value, _ = self.prepared()
        for fault in ('bytes', 'path', 'owner'):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(value); entry = changed['prepared'][0]; item = entry['returnSourceGuards'][OLD][0]
                if fault == 'bytes': item['bytes'] += 1
                if fault == 'path': item['path'] = '../outside'
                if fault == 'owner': entry['returnSourceGuards']['../owner'] = entry['returnSourceGuards'].pop(OLD)
                changed['journal']['entries'] = [entry]; self.write(intent, changed)
                with self.assertRaises((RuntimeError, ValueError)): E.retain_prepared(self.backup, ref(intent))
                _, metadata, _ = E.locations(self.backup, ref(intent)); self.assertFalse(metadata.exists())


if __name__ == '__main__': unittest.main()
