"""Typed SDK provenance alone may retain exact sibling snapshot SQLite inputs."""
import contextlib
import copy
import io
import json
import os
import pathlib
import signal
import shutil
import sqlite3
import tarfile
import unittest
from unittest import mock
from test_evidence_retention import E, H, OLD, NEW, RetentionTests, ref


class TypedRetentionTests(RetentionTests):
    # Inherit the complete v1 closure/security regressions against v2 too.
    def lineage(self, separate_blob=True):
        receipt = self.prepare_receipt(); value = json.loads(receipt.read_bytes())
        source = value['sources'][0]; source_store = pathlib.Path(source['path'])
        baseline = pathlib.Path(value['baseline']['store']['path'])
        blobs = {'root': b'{"prior":"old-root"}', 'old-root': b'complete original context'}
        for target in (source['blobSnapshot']['path'], value['baseline']['blobs']['path']):
            with contextlib.closing(sqlite3.connect(target)) as conn:
                conn.executemany('INSERT INTO blobs VALUES(?,?)', blobs.items()); conn.commit()
        for target, slot in ((source_store, 'old-root'), (baseline, 'root')):
            with contextlib.closing(sqlite3.connect(target)) as conn:
                metadata = json.loads(bytes.fromhex(conn.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()[0]))
                metadata['latestRootBlobId'] = slot
                conn.execute("UPDATE kv SET value=? WHERE key='metadata'", (json.dumps(metadata).encode().hex(),)); conn.commit()
        source.update(ref(source_store)); source['blobSnapshot'] = ref(pathlib.Path(source['blobSnapshot']['path']))
        source['sdkRootSha256'] = E.digest(blobs['old-root'])
        for key in ('store', 'blobs'): value['baseline'][key] = ref(pathlib.Path(value['baseline'][key]['path']))
        captured = self.backup.parent / 'fleet-state-20260101-123456-1234abcd' / 'branches' / OLD
        captured.mkdir(parents=True); raw_store = captured / 'store.db'; shutil.copyfile(source_store, raw_store)
        raw_blob = captured / 'conversation-blobs.db'
        if separate_blob:
            shutil.copyfile(source['blobSnapshot']['path'], raw_blob)
            # Raw source bytes deliberately differ from normalized blob capture.
            with contextlib.closing(sqlite3.connect(raw_blob)) as conn:
                conn.execute('CREATE TABLE raw_provenance(value)'); conn.commit()
        else:
            with contextlib.closing(sqlite3.connect(raw_store)) as conn:
                conn.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)')
                conn.executemany('INSERT INTO blobs VALUES(?,?)', blobs.items()); conn.commit()
            shutil.copyfile(raw_store, source_store); source.update(ref(source_store))
        roots = {OLD: source['sdkRootSha256']}
        report = {'schema': E.LINEAGE_REPORT, 'engineSha256': 'a' * 64, 'walkerSha256': H.IDENTIFIER_WORKER_SHA,
            'targetAgentId': OLD, 'sourceRoots': roots, 'activeRootSha256': E.digest(blobs['root']),
            'activeRootUnchanged': True, 'allHistoricalSourceGraphsArchived': True, 'historicalGraphsActivated': False,
            'liveApplied': False, 'pendingWorkCreated': False, 'activeReachableRefs': 2, 'blobCount': 2,
            'sources': [{'agentId': OLD, 'rootSha256': roots[OLD], 'rootSlot': 'old-root',
                'sourceStoreRelativePath': 'sources/' + OLD + '/store.db', 'sourceStoreSha256': ref(raw_store)['sha256'],
                'blobSnapshotRelativePath': 'sources/' + OLD + '/conversation-blobs.db',
                'blobSnapshotSha256': source['blobSnapshot']['sha256'], 'sourceBlobCount': 2,
                'allSourceBlobBytesArchived': True, 'fixedRootRevisions': [], 'nativeReachableRefs': 2,
                'unresolvedProtoRefs': 0, 'inputSnapshots': [ref(raw_store)] + ([ref(raw_blob)] if separate_blob else [])}]}
        sdk_path = pathlib.Path(value['sdkProof']['path']); sdk = json.loads(sdk_path.read_bytes())
        report_path = pathlib.Path(sdk['nativeReport']['path']); self.write(report_path, report)
        sdk.update(rootSha256=E.digest(blobs['root']), rootSlot='root', blobs={k: E.digest(v) for k, v in blobs.items()},
                   sourceRoots=roots, nativeReport=ref(report_path))
        self.write(sdk_path, sdk); value['sdkProof'] = ref(sdk_path); self.write(receipt, value)
        return receipt, value, report_path, report, raw_store, raw_blob

    def pin_report(self, receipt, value, report_path, report):
        self.write(report_path, report); sdk_path = pathlib.Path(value['sdkProof']['path'])
        sdk = json.loads(sdk_path.read_bytes()); sdk['nativeReport'] = ref(report_path)
        self.write(sdk_path, sdk); value['sdkProof'] = ref(sdk_path); self.write(receipt, value)

    def test_excluded_sibling_originals_and_normalized_graph_restore_byte_exact(self):
        receipt, value, _, _, raw_store, raw_blob = self.lineage()
        self.r.accept_history_merge(NEW, str(receipt)); journal = self.r.read()
        expected = H.validate(self.r, journal, journal['entries'][0], _check_live=False)
        paths = [raw_store, raw_blob, pathlib.Path(value['sources'][0]['path']), pathlib.Path(value['sources'][0]['blobSnapshot']['path'])]
        before = {p: p.read_bytes() for p in paths}; self.assertNotEqual(before[raw_blob], before[paths[-1]])
        # Model native any-depth exclusions in BOTH immutable snapshot roots.
        for p in paths: p.unlink()
        with self.locked() as fd:
            self.assertEqual(E.recover_journal(journal, self.root, fd)['restored'], 4)
            self.assertEqual(E.recover_journal(journal, self.root, fd)['restored'], 0)
        self.assertEqual({p: p.read_bytes() for p in paths}, before)
        self.assertEqual(H.validate(self.r, journal, journal['entries'][0], _check_live=False), expected)
        with contextlib.closing(sqlite3.connect(paths[-1])) as conn:
            self.assertEqual(dict(conn.execute('SELECT id,data FROM blobs'))['old-root'], b'complete original context')

    def test_nonempty_sdk_can_have_only_store_db_raw_input(self):
        receipt, value, _, _, raw_store, raw_blob = self.lineage(False)
        self.assertFalse(raw_blob.exists()); self.r.accept_history_merge(NEW, str(receipt))
        exact = raw_store.read_bytes(); raw_store.unlink()
        with self.locked() as fd: self.assertEqual(E.recover_journal(self.r.read(), self.root, fd)['restored'], 1)
        self.assertEqual(raw_store.read_bytes(), exact)
        H.validate(self.r, self.r.read(), self.r.read()['entries'][0], _check_live=False)

    def test_only_typed_raw_inputs_allow_stable_empty_legacy_wal(self):
        receipt, value, _, _, raw_store, _ = self.lineage(False)
        wal = pathlib.Path(str(raw_store) + '-wal'); wal.write_bytes(b'')
        before = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (raw_store, wal)}
        name = E.storage_name(self.backup, raw_store, ('sdk-input', OLD))
        self.assertEqual(E.read_evidence(self.backup, name), before[raw_store][0])
        E.retain(self.backup, ref(receipt))
        self.assertEqual({p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in before}, before)
        for raw in (b'committed WAL frames', b'x'):
            wal.write_bytes(raw)
            with self.assertRaisesRegex(RuntimeError, 'normalized'): E.read_evidence(self.backup, name)
        wal.write_bytes(b''); checked = E.validator().checked
        for fault in ('grow', 'replace'):
            def changing(root, path, *args):
                result = checked(root, path, *args)
                if fault == 'replace': wal.unlink()
                wal.write_bytes(b'new frames' if fault == 'grow' else b'')
                return result
            with mock.patch.object(E.validator(), 'checked', side_effect=changing), self.assertRaisesRegex(RuntimeError, 'changed'):
                E.read_evidence(self.backup, name)
            wal.write_bytes(b'')
        wal.unlink(); wal.symlink_to(raw_store)
        with self.assertRaisesRegex(RuntimeError, 'normalized'): E.read_evidence(self.backup, name)
        wal.unlink()
        canonical = pathlib.Path(value['sources'][0]['path']); pathlib.Path(str(canonical) + '-wal').write_bytes(b'')
        with self.assertRaisesRegex(RuntimeError, 'normalized'):
            E.read_evidence(self.backup, E.relative(self.backup, canonical))

    def test_interrupted_external_restore_resumes_without_replacing_existing_bytes(self):
        receipt, _, _, _, raw_store, raw_blob = self.lineage(); E.retain(self.backup, ref(receipt))
        expected = {p: p.read_bytes() for p in (raw_store, raw_blob)}; raw_store.unlink(); raw_blob.unlink()
        create = E.create; calls = [0]
        def interrupted(path, raw, root=None):
            calls[0] += 1
            if calls[0] == 2: raise InterruptedError()
            return create(path, raw, root)
        with self.locked() as fd:
            with mock.patch.object(E, 'create', side_effect=interrupted), self.assertRaises(InterruptedError):
                E.restore(self.backup, ref(receipt), self.root, fd)
            self.assertEqual(E.restore(self.backup, ref(receipt), self.root, fd)['restored'], 1)
        self.assertEqual({p: p.read_bytes() for p in expected}, expected)

    def test_generic_json_and_unanchored_sdk_proof_never_grant_external_authority(self):
        receipt, value, _, _, raw_store, _ = self.lineage()
        for payload in ({'foreign': ref(raw_store)}, json.loads(pathlib.Path(value['sdkProof']['path']).read_bytes())):
            path = receipt.parent / 'unanchored.json'; self.write(path, payload)
            with self.assertRaisesRegex(RuntimeError, 'escapes'): E.retain(self.backup, ref(path))

    def test_same_id_report_required_plan_map_is_retained_without_external_privileges(self):
        receipt = self.prepare_receipt(); value = json.loads(receipt.read_bytes())
        sdk_path = pathlib.Path(value['sdkProof']['path']); sdk = json.loads(sdk_path.read_bytes())
        report_path = pathlib.Path(sdk['nativeReport']['path']); plan = receipt.parent / 'required-plan.json'
        self.write(plan, {'schema': 'opaque-plan', 'selected': OLD})
        report = {'schema': E.RESTORATION_REPORT, 'inputFiles': {str(plan): ref(plan)['sha256']}}
        self.pin_report(receipt, value, report_path, report); self.r.accept_history_merge(NEW, str(receipt))
        expected = plan.read_bytes(); plan.unlink()
        with self.locked() as fd: self.assertEqual(E.recover_journal(self.r.read(), self.root, fd)['restored'], 1)
        self.assertEqual(plan.read_bytes(), expected)
        H.validate(self.r, self.r.read(), self.r.read()['entries'][0], _check_live=False)
        outside = self.backup.parent / 'unrelated-plan.json'; outside.write_bytes(expected)
        report['inputFiles'] = {str(outside): ref(outside)['sha256']}
        self.pin_report(receipt, value, report_path, report)
        with self.assertRaisesRegex(RuntimeError, 'escapes'): E.retain(self.backup, ref(receipt))

    def test_report_semantics_and_owners_fail_closed(self):
        receipt, value, report_path, report, raw_store, raw_blob = self.lineage()
        for fault in ('engine', 'walker', 'target', 'roots', 'flag', 'unresolved', 'count', 'bool-count',
                      'owner', 'duplicate', 'hash', 'mixed-capture', 'missing-store', 'source-coverage'):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(report); source = changed['sources'][0]
                if fault == 'engine': changed['engineSha256'] = 'b' * 64
                if fault == 'walker': changed['walkerSha256'] = 'b' * 64
                if fault == 'target': changed['targetAgentId'] = NEW
                if fault == 'roots': changed['sourceRoots'][OLD] = 'b' * 64
                if fault == 'flag': changed['liveApplied'] = True
                if fault == 'unresolved': source['unresolvedProtoRefs'] = 1
                if fault == 'count': source['nativeReachableRefs'] = -1
                if fault == 'bool-count': changed['activeReachableRefs'] = True
                if fault == 'owner': source['inputSnapshots'][0]['path'] = str(raw_store.parent.parent / NEW / 'store.db')
                if fault == 'duplicate': source['inputSnapshots'][1] = source['inputSnapshots'][0]
                if fault == 'hash': source['inputSnapshots'][0]['sha256'] = 'b' * 64
                if fault == 'mixed-capture': source['inputSnapshots'][1]['path'] = str(raw_blob).replace('1234abcd', '5678abcd')
                if fault == 'missing-store': source['inputSnapshots'] = [source['inputSnapshots'][1]]
                if fault == 'source-coverage': changed['sources'] = []
                self.pin_report(receipt, value, report_path, changed)
                with self.assertRaises(RuntimeError): E.retain(self.backup, ref(receipt))
                self.assertFalse(E.locations(self.backup, ref(receipt))[1].exists())

    def test_typed_paths_reject_live_agents_traversal_and_reserved_namespace(self):
        receipt, value, report_path, report, raw_store, _ = self.lineage()
        for target in (self.root / 'agents' / OLD / 'store.db', raw_store.parent / '..' / OLD / 'store.db',
                       self.backup.parent / 'arbitrary' / 'branches' / OLD / 'store.db'):
            changed = copy.deepcopy(report); changed['sources'][0]['inputSnapshots'][0]['path'] = str(target)
            self.pin_report(receipt, value, report_path, changed)
            with self.assertRaises(RuntimeError): E.retain(self.backup, ref(receipt))
        local = self.backup / '@sdk-inputs' / 'local'; local.parent.mkdir(); local.write_bytes(b'opaque')
        anchor = receipt.parent / 'collision.json'; self.write(anchor, ref(local))
        with self.assertRaisesRegex(RuntimeError, 'reserved'): E.retain(self.backup, ref(anchor))

    def test_external_existing_conflict_foreign_owner_and_symlink_refuse_before_restore(self):
        receipt, _, _, _, raw_store, raw_blob = self.lineage(); E.retain(self.backup, ref(receipt))
        original = raw_blob.read_bytes(); raw_store.unlink(); raw_blob.write_bytes(b'x' * len(original))
        with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'differs'): E.restore(self.backup, ref(receipt), self.root, fd)
        self.assertFalse(raw_store.exists()); raw_blob.write_bytes(original)
        if os.getuid() == 0:
            os.chown(raw_blob, 65534, 65534)
            try:
                with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'foreign'): E.restore(self.backup, ref(receipt), self.root, fd)
            finally: os.chown(raw_blob, os.getuid(), os.getgid())
        raw_blob.unlink(); raw_blob.symlink_to(self.source / 'store.db')
        with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'symlinked|regular'): E.restore(self.backup, ref(receipt), self.root, fd)
        self.assertFalse(raw_store.exists())

    def test_manifest_and_archive_cannot_authorize_unreferenced_sibling_file(self):
        receipt, _, _, _, raw_store, _ = self.lineage(); E.retain(self.backup, ref(receipt)); reference = ref(receipt)
        _, metadata, archive = E.locations(self.backup, reference); manifest = json.loads(metadata.read_bytes())
        extra = '@sdk-inputs/fleet-state-20260101-010101-deadbeef/branches/' + NEW + '/store.db'; raw = b'no authority'
        with tarfile.open(archive, 'r:gz') as tar: items = [(m.name, tar.extractfile(m).read()) for m in tar]
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as tar:
            for name, data in [*items, (extra, raw)]:
                info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))
        archive.write_bytes(stream.getvalue()); manifest['archiveSha256'] = E.digest(stream.getvalue())
        manifest['files'] = sorted([*manifest['files'], {'path': extra, 'sha256': E.digest(raw), 'size': len(raw)}], key=lambda x: x['path'])
        self.write(metadata, manifest); raw_store.unlink()
        with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'exact receipt closure'): E.restore(self.backup, reference, self.root, fd)
        self.assertFalse(raw_store.exists()); self.assertFalse(E.storage_target(self.backup, extra)[1].exists())

    def test_legacy_v1_archive_fallback_is_root_only_and_never_rewritten(self):
        receipt, a, b = self.simple(); reference = ref(receipt)
        E.retain(self.backup, reference, E.LEGACY_REFERENCE_POLICY)
        _, metadata, archive = E.locations(self.backup, reference, True); exact = {p: p.read_bytes() for p in (metadata, archive)}
        for p in E.locations(self.backup, reference)[1:]: p.unlink()
        a.unlink(); b.unlink()
        with self.locked() as fd: self.assertEqual(E.restore(self.backup, reference, self.root, fd)['restored'], 2)
        self.assertEqual({p: p.read_bytes() for p in exact}, exact)
        foreign = self.backup.parent / 'foreign.db'; foreign.write_bytes(b'private')
        anchor = receipt.parent / 'foreign.json'; self.write(anchor, {'foreign': ref(foreign)})
        with self.assertRaisesRegex(RuntimeError, 'escapes'): E.retain(self.backup, ref(anchor), E.LEGACY_REFERENCE_POLICY)

    def test_fifo_leaf_and_archive_are_rejected_promptly_without_writes(self):
        receipt, a, b = self.simple(); a.unlink(); os.mkfifo(a)
        old = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('FIFO read blocked')))
        try:
            signal.alarm(2)
            with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'regular'): E.restore(self.backup, ref(receipt), self.root, fd)
            signal.alarm(0); a.unlink(); b.unlink(); archive = E.locations(self.backup, ref(receipt))[2]; archive.unlink(); os.mkfifo(archive)
            signal.alarm(2)
            with self.locked() as fd, self.assertRaisesRegex(RuntimeError, 'owned'): E.restore(self.backup, ref(receipt), self.root, fd)
        finally: signal.alarm(0); signal.signal(signal.SIGALRM, old)
        self.assertFalse(a.exists()); self.assertFalse(b.exists())


if __name__ == '__main__': unittest.main()
