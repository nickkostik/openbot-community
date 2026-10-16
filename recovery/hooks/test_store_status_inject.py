import importlib.util
import json
import pathlib
import shutil
import subprocess
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location('store_status', HERE / 'store-status-inject.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


@unittest.skipUnless(shutil.which('node'), 'Node is required for native metadata behavior')
class StoreStatusTests(unittest.TestCase):
    def run_native(self, body):
        # Execute the injected native methods, with actual async promises and
        # network/store seams, rather than emulating their behavior in Python.
        prefix = '''const assert = require('node:assert/strict');
function isBoxStoreManifestFileEntry(entry) { return entry.kind !== 'symlink'; }
function countStoreDbManifestEntries(map) { return [...map.keys()].filter(k => k.endsWith('/store.db')).length; }
function countAgentDirManifestEntries(map) { return [...map.keys()].filter(k => k.startsWith('agents/')).length; }
class ManifestStore {
HELPER
  async commit(snapshot) { await this.put(snapshot); this.rememberOpenbotCanonicalStoreStatus(snapshot); }
}
class Sync {
WRAPPER
    const snapshot = await this.manifestStore.read();
    if (snapshot == null) return { durable: false, entryCount: 0 };
    this.manifestStore.rememberOpenbotCanonicalStoreStatus(snapshot);
    return { ...this.manifestStore.openbotCanonicalStoreStatus };
  }
}
const store = new ManifestStore(); const sync = new Sync(); sync.manifestStore = store;
const snapshot = { entries: { 'agents/a/store.db': { kind: 'file', size: 17 },
 'agents/a/link': { kind: 'symlink', target: '../data' } }, fullyHydrated: true, updatedAtMs: 100 };
'''.replace('HELPER', m.HELPER).replace('WRAPPER', m.WRAPPER)
        result = subprocess.run(['node', '-e', prefix + '\n(async () => {\n' + body +
                                 '\n})().catch(e => { console.error(e); process.exitCode = 1; });'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_poll_after_acknowledged_write_does_not_fetch_or_count_symlinks_as_bytes(self):
        self.run_native('''store.put = async () => {}; await store.commit(snapshot);
store.read = async () => { throw new Error('unexpected download'); };
const value = await sync.readStoreStatus(); assert.equal(value.durable, true);
assert.equal(value.totalBytes, 17); assert.equal(value.entryCount, 2);
assert.equal(value.storeDbEntries, 1); assert.equal(value.lastSnapshotAtMs, 100);
value.totalBytes = 999; assert.equal((await sync.readStoreStatus()).totalBytes, 17);''')

    def test_failed_commit_does_not_advertise_unuploaded_entries(self):
        self.run_native('''store.put = async () => { throw new Error('failed upload'); };
await assert.rejects(store.commit(snapshot)); assert.equal(store.openbotCanonicalStoreStatus, undefined);
store.read = async () => null; assert.equal((await sync.readStoreStatus()).durable, false);''')

    def test_parallel_cold_reads_share_one_canonical_download(self):
        self.run_native('''let reads = 0;
store.read = async () => { reads++; await new Promise(r => setTimeout(r, 10)); return snapshot; };
const results = await Promise.all([sync.readStoreStatus(), sync.readStoreStatus(), sync.readStoreStatus()]);
assert.equal(reads, 1); assert(results.every(r => r.storeDbEntries === 1));
await sync.readStoreStatus(); assert.equal(reads, 1);''')

    def test_rejected_read_releases_claim_and_can_retry(self):
        self.run_native('''store.read = async () => { throw new Error('transport'); };
await assert.rejects(sync.readStoreStatus()); assert.equal(sync.openbotStoreStatusRead, undefined);
store.read = async () => snapshot; assert.equal((await sync.readStoreStatus()).durable, true);''')

    def test_slow_old_read_does_not_override_a_later_confirmed_write(self):
        self.run_native('''store.rememberOpenbotCanonicalStoreStatus({ ...snapshot, updatedAtMs: 200 });
store.rememberOpenbotCanonicalStoreStatus(snapshot);
assert.equal((await sync.readStoreStatus()).lastSnapshotAtMs, 200);''')

    def test_acknowledged_empty_reset_reports_empty_not_old_success(self):
        self.run_native('''store.put = async () => {}; await store.commit(snapshot);
await store.commit({ entries: {}, fullyHydrated: true, updatedAtMs: 300 });
const status = await sync.readStoreStatus(); assert.equal(status.durable, false);
assert.equal(status.entryCount, 0); assert.equal(status.totalBytes, 0);''')

    def test_unknown_and_partially_stamped_contracts_are_rejected(self):
        for source in ('unknown latest bundle', m.HELPER):
            with self.assertRaises(RuntimeError): m.patch(source)


if __name__ == '__main__': unittest.main()
