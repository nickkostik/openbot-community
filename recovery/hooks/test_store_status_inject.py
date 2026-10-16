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
    def contract_fixture(self, error='error64'):
        return '''class ManifestStore {
  objectStore() { return this.store; }
  initializeManifestRevision() {}
  parseManifest(bytes) { return JSON.parse(bytes); }
  async readManifest(decoded) {
    const map4 = new Map(Object.entries(decoded.entries));
    const persisted = {};
        this.hydrationManifestReadBlocked = false;
        this.fullyHydrated = decoded.fullyHydrated;
        let isCanonicalSnapshot = true;
    this.initializeManifestRevision(map4, persisted);
    this.manifest ??= map4;
    return this.manifest;
  }
  async writeManifestAttempt(storeId, manifest) {
    try {
      await this.objectStore(storeId).put(
        "manifest.json",
        new Uint8Array(Buffer.from(JSON.stringify(manifest), "utf8"))
      );
    } catch (CATCH_BINDING) {
      throw CATCH_BINDING;
    }
  }
  async readCanonicalManifestSnapshot() {
    const bytes = await this.store.get();
    if (bytes == null) return null;
    return this.parseManifest(bytes);
  }
  reportManifestWriteConflict() {}
}
class Sync {
  async readStoreStatus() { return this.manifestStore.readCanonicalManifestSnapshot(); }
}
'''.replace('CATCH_BINDING', error)

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

    def test_summary_does_not_materialize_a_second_full_manifest(self):
        self.run_native('''const entries = Object.entries, MapClass = globalThis.Map;
Object.entries = () => { throw Error('full entry-array materialization'); };
globalThis.Map = class { constructor() { throw Error('full manifest-map materialization'); } };
try { store.rememberOpenbotCanonicalStoreStatus(snapshot); }
finally { Object.entries = entries; globalThis.Map = MapClass; }
const status = await sync.readStoreStatus();
assert.equal(status.entryCount, 2); assert.equal(status.totalBytes, 17);
assert.equal(status.storeDbEntries, 1); assert.equal(status.agentDirEntries, 2);''')

    def test_summary_counts_only_own_entries(self):
        self.run_native('''const inherited = Object.create({ 'agents/stale/store.db': {kind:'file',size:999} });
Object.assign(inherited, snapshot.entries);
store.rememberOpenbotCanonicalStoreStatus({...snapshot, entries:inherited});
const status = await sync.readStoreStatus(); assert.equal(status.entryCount,2);
assert.equal(status.totalBytes,17); assert.equal(status.storeDbEntries,1);''')

    def test_local_catch_binding_renumbering_keeps_acknowledgement_and_error_semantics(self):
        for binding in ('error42', 'error64', '$uploadError'):
            patched = m.patch(self.contract_fixture(binding))
            self.assertEqual(m.patch(patched), patched)
            self.assertIn('catch (' + binding + ')', patched)
            script = '''const assert=require('node:assert/strict');
function isBoxStoreManifestFileEntry(v){return v.kind==='file';}
function countStoreDbManifestEntries(m){return [...m.keys()].filter(k=>k.endsWith('/store.db')).length;}
function countAgentDirManifestEntries(m){return new Set([...m.keys()].map(k=>k.split('/')[1])).size;}
''' + patched + '''
(async()=>{const m=new ManifestStore(),failure=new Error('CAS refusal');
m.store={put:async()=>{throw failure;}};
const snapshot={entries:{'agents/a/store.db':{kind:'file',size:17}},updatedAtMs:100,fullyHydrated:true};
await assert.rejects(m.writeManifestAttempt('store',snapshot),e=>e===failure);
assert.equal(m.openbotCanonicalStoreStatus,undefined);
m.store.put=async(key,bytes)=>{assert.deepEqual(JSON.parse(Buffer.from(bytes)),snapshot);};
await m.writeManifestAttempt('store',snapshot);
assert.equal(m.openbotCanonicalStoreStatus.lastSnapshotAtMs,100);
assert.equal(m.openbotCanonicalStoreStatus.totalBytes,17);
const fromEntries=Object.fromEntries;
Object.fromEntries=()=>{throw Error('canonical Map must not be copied to an object');};
try { await m.readManifest({...snapshot,updatedAtMs:200}); }
finally { Object.fromEntries=fromEntries; }
assert.equal(m.openbotCanonicalStoreStatus.lastSnapshotAtMs,200);
assert.equal(m.openbotCanonicalStoreStatus.totalBytes,17);
})().catch(e=>{console.error(e);process.exitCode=1;});'''
            result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_complete_legacy_patch_upgrades_but_mixed_or_partial_versions_refuse(self):
        current = m.patch(self.contract_fixture())
        legacy = current.replace(m.HELPER, m.LEGACY_HELPER).replace(m.READ_TAIL_UPDATED, m.LEGACY_READ_TAIL_UPDATED)
        self.assertEqual(m.patch(legacy), current)
        for source in (current + m.MARKER, current + m.LEGACY_MARKER,
                       legacy.replace(m.WRAPPER, 'unknown native reader'),
                       self.contract_fixture() + self.contract_fixture()):
            with self.assertRaises(RuntimeError): m.patch(source)

    def test_unknown_and_partially_stamped_contracts_are_rejected(self):
        for source in ('unknown latest bundle', m.HELPER):
            with self.assertRaises(RuntimeError): m.patch(source)


if __name__ == '__main__': unittest.main()
