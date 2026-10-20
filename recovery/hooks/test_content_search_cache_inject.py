"""Synthetic content-search worker tests; no native bundle or captured stores."""
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import unittest
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures' / 'content-search-cache-synthetic.js'
spec = importlib.util.spec_from_file_location('content_search_cache', HERE / 'content-search-cache-inject.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PRODUCTION_SOURCE_SHA = '8b97f8fbe2a57679c4ebbe8d10912ed175dd02aa30f7b47842cff4d8b45c9819'
PRODUCTION_COMPONENT_SHA = 'bf0618789744f47565ee6da6a0f56eb32f7b3c517d1f6d5842d158e94e19814e'
PRODUCTION_PATCHED_SHA = 'f8a1274b7c1bb191789ac988bdbf7b18d4e3604039d123d8b2907fb9e1e06a95'
PRODUCTION_PATCHED_COMPONENT_SHA = 'bb01e005f867bc291158b306506dfa16968c5de4051834fd36e87ac649e34c97'
SYNTHETIC_SOURCE_SHA = 'ca41effd6a9137689a26c5a1288f7f4c1165e6e0ca10d2cb4b947d40b4da83f3'
SYNTHETIC_COMPONENT_SHA = '665d09ab25ec4a01f0115756e1ec49e17099c2e69ef9787fd5eb53608b643968'
SYNTHETIC_PATCHED_SHA = 'bdbd27c1992a63d864f93bc77a96d5270045809963d08377bfb57a62734c1a5f'
SYNTHETIC_PATCHED_COMPONENT_SHA = '5159218eeb4eddb2a75d7467ea2415db653bd284e06d18a08e0438acd52e7570'
PRODUCTION_HOST_COMPONENT_SHA = '35544fcb9b467bf5ce72752ce068da5a593e643d2d5a89571a7016eb08cc52c3'
PRODUCTION_HOST_PATCHED_COMPONENT_SHA = '251af8d15fa5504d3093f6fb0d9624d895dc5899582a3e22d051f7b38f92e837'
SYNTHETIC_HOST_COMPONENT_SHA = 'edb075b77440a72958035d758e035dbb6ed755cf1922d152a6d6d9573b4f7027'
SYNTHETIC_HOST_PATCHED_COMPONENT_SHA = '2606edb418c651a84c5c1bc4895b84796d6d39808e6772bc51053f6cf45ff1cf'
HOST_FIXTURE = '''function defaultWorkerEntryPath() {
  return "/synthetic/native-worker.cjs";
}
var SearchIndexWorkerChannel = class {};
'''


NODE_WORKER = r'''
const fs = require('node:fs');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));

function run(source) {
  const records = new Map(), events = [], handles = [];
  let live = 0, peak = 0, activeCloseViolations = 0, serial = 0;
  const record = id => {
    if (!records.has(id)) records.set(id, {rows:[
      {seq:1, entry:JSON.stringify({id:'first', text:'needle for ' + id})},
      {seq:2, entry:JSON.stringify({id:'second', text:'ordinary text for ' + id})},
      {seq:3, entry:'invalid JSON ignored by the synthetic index'},
      {seq:4, entry:JSON.stringify({id:'last', text:'another needle for ' + id})}
    ]});
    return records.get(id);
  };
  const idOf = path => path.split('/').at(-2);
  class SyntheticDatabase {
    constructor(path, options) {
      this.id = idOf(path); this.settings = options; this.serial = ++serial;
      const data = record(this.id);
      events.push('attempt:' + this.id);
      if (data.openError) throw Error('cannot open synthetic store');
      this.closed = false; this.activeSQL = 0; handles.push(this);
      live++; peak = Math.max(peak, live); events.push('open:' + this.id);
    }
    perform(kind, operation) {
      if (this.closed) throw Error('query used a closed database');
      this.activeSQL++; events.push('sql-start:' + this.id + ':' + kind);
      try {
        if (record(this.id)[kind + 'Error']) throw Error('synthetic ' + kind + ' failure');
        return operation();
      } finally { this.activeSQL--; events.push('sql-end:' + this.id + ':' + kind); }
    }
    exec() { return this.perform('exec', () => undefined); }
    prepare(sql) {
      if (sql === 'synthetic fingerprint') return {get: () => this.perform('fingerprint', () => {
        const data = record(this.id);
        return data.badFingerprint ? {count:'unknown', maxSeq:null} :
          {count:data.rows.length, maxSeq:Math.max(0, ...data.rows.map(row => row.seq))};
      })};
      if (sql === 'synthetic transcript rows') return {all: () => this.perform('rows', () =>
        record(this.id).rows.map(row => ({...row})))};
      throw Error('unexpected synthetic SQL');
    }
    close() {
      if (this.activeSQL !== 0) { activeCloseViolations++; throw Error('closed during SQL'); }
      if (this.closed) throw Error('database closed twice');
      if (record(this.id).closeError) {
        events.push('close-failed:' + this.id);
        throw Error('close failed before handle release');
      }
      this.closed = true; live--; events.push('close:' + this.id);
    }
  }
  const exists = path => !record(idOf(path)).missing;
  const security = path => {
    const id = idOf(path); events.push('security:' + id);
    if (record(id).linkError) throw Error('unsafe path');
  };
  const check = (db, path) => { if (record(idOf(path)).checkError) throw Error('invalid schema'); };
  const Worker = new Function('SyntheticDatabase', 'syntheticExists',
    'refuseSandDataDatabaseLinksSync', 'syntheticCheckDatabase',
    source + '\nreturn SandSearchIndexWriter;')(SyntheticDatabase, exists, security, check);
  const writer = new Worker({}, '/synthetic/agents');
  const keys = () => [...writer.storeConnections.keys()];
  const cleanup = () => {
    for (const data of records.values()) data.closeError = false;
    writer.close();
  };
  let result;
  if (request.mode === 'lru') {
    for (let i=0; i<8; i++) writer.storeConnection('A' + i);
    const before = writer.storeConnections.get('A0');
    const hit = writer.storeConnection('A0');
    const afterHit = keys();
    writer.storeConnection('A8');
    const afterNinth = keys();
    writer.storeConnection('A4');
    writer.storeConnection('A9');
    const final = keys();
    result = {sameHit:hit === before, afterHit, afterNinth, final,
      retained:writer.storeConnections.size, liveBeforeClose:live};
  } else if (request.mode === 'results') {
    const ids = Array.from({length:request.count}, (_, i) => 'agent-' + i);
    writer.reconcile(ids);
    const initial = writer.search('needle');
    const firstFingerprints = Object.fromEntries(writer.fingerprints);
    const firstReindexed = [...writer.reindexed];
    writer.reconcile(ids);
    const unchangedReindexed = [...writer.reindexed];
    record(ids[0]).rows.push({seq:9, entry:JSON.stringify({id:'new', text:'fresh needle'})});
    writer.reconcile(ids);
    result = {initial, final:writer.search('needle'), firstFingerprints,
      fingerprints:Object.fromEntries(writer.fingerprints), firstReindexed,
      unchangedReindexed, reindexed:[...writer.reindexed], retained:writer.storeConnections.size};
  } else if (request.mode === 'reindex') {
    writer.readStoreFingerprint('one');
    const before = writer.storeConnections.get('one');
    const beforeSerial = before.db.serial;
    writer.reindexAgent('one');
    const after = writer.storeConnections.get('one');
    result = {beforeClosed:before.db.closed, reopened:beforeSerial !== after.db.serial,
      fingerprint:writer.readStoreFingerprint('one'), hits:writer.search('needle'), retained:keys()};
  } else {
    for (let i=0; i<8; i++) writer.storeConnection('A' + i);
    record('A0').closeError = true;
    const closeFailed = writer.storeConnection('A8');
    const failedKeys = keys(), failedLive = live;
    const openedNinthOnFailure = events.includes('open:A8');
    record('A0').closeError = false;
    const retry = writer.storeConnection('A8');
    record('bad-schema').checkError = true;
    const badSchema = writer.storeConnection('bad-schema');
    record('bad-exec').execError = true;
    const badExec = writer.storeConnection('bad-exec');
    record('bad-open').openError = true;
    const badOpen = writer.storeConnection('bad-open');
    record('bad-link').linkError = true;
    const beforeLink = keys();
    const badLink = writer.storeConnection('bad-link');
    const afterLink = keys();
    record('missing').missing = true;
    const missing = writer.storeConnection('missing');
    record('A4').fingerprintError = true;
    const failedFingerprint = writer.readStoreFingerprint('A4');
    const fingerprintEvicted = !writer.storeConnections.has('A4');
    writer.index.set('bad-rows', [{id:'retained',text:'old needle'}]);
    record('bad-rows').rowsError = true;
    writer.reindexAgent('bad-rows');
    const reindexEvicted = !writer.storeConnections.has('bad-rows');
    record('bad-fingerprint').badFingerprint = true;
    const malformedFingerprint = writer.readStoreFingerprint('bad-fingerprint');
    const malformedStillCached = writer.storeConnections.has('bad-fingerprint');
    result = {closeFailed:closeFailed === null, failedKeys, failedLive, openedNinthOnFailure,
      retrySucceeded:retry !== null, badSchema:badSchema === null, badExec:badExec === null,
      badOpen:badOpen === null, badLink:badLink === null, missing:missing === null,
      beforeLink, afterLink, failedFingerprint, fingerprintEvicted, reindexEvicted,
      retainedOldIndex:writer.index.get('bad-rows'), malformedFingerprint, malformedStillCached,
      retained:writer.storeConnections.size};
  }
  cleanup();
  return {...result, peak, live, activeCloseViolations, events,
    cacheAfterClose:writer.storeConnections.size,
    readOnly:handles.every(db => db.settings.readOnly === true),
    allClosed:handles.every(db => db.closed)};
}
const output = request.sources.map(run);
process.stdout.write(JSON.stringify(output));
'''


NODE_HOST_SELECTOR = r'''
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
function run(scenario) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'search-sidecar-test-'));
  const root = path.join(base, 'data'); fs.mkdirSync(root);
  const native = path.join(base, 'native-worker.cjs'); fs.writeFileSync(native, request.native);
  const sidecar = path.join(root, 'openbot-local-engine', 'sidecars', request.patchedSha, 'search-index-worker.cjs');
  fs.mkdirSync(path.dirname(sidecar), {recursive:true}); fs.writeFileSync(sidecar, request.patched);
  const mode = path.join(root, 'openbot-mode'); fs.writeFileSync(mode, 'custom\n');
  const flag = path.join(root, 'openbot-local-only.enable'); fs.writeFileSync(flag, '');
  const policyFile = path.join(root, 'openbot-local-engine.json');
  const policy = {enabled:true, contentSearchWorker:{schemaVersion:1,
    sourceSha256:request.sourceSha, sha256:request.patchedSha, path:sidecar}};
  let foreignAsset = false, foreignFolder = false;
  try {
    switch (scenario) {
      case 'official': fs.writeFileSync(mode, 'official'); break;
      case 'missing-mode': fs.unlinkSync(mode); break;
      case 'missing-flag': fs.unlinkSync(flag); break;
      case 'disabled-policy': policy.enabled = false; break;
      case 'wrong-schema': policy.contentSearchWorker.schemaVersion = 2; break;
      case 'wrong-policy-source': policy.contentSearchWorker.sourceSha256 = '0'.repeat(64); break;
      case 'unknown-native': fs.appendFileSync(native, '\n// unreviewed native source\n'); break;
      case 'missing-asset': fs.unlinkSync(sidecar); break;
      case 'corrupt-asset': fs.appendFileSync(sidecar, '\n// corrupt asset\n'); break;
      case 'wrong-path': policy.contentSearchWorker.path = native; break;
      case 'symlink-asset': {
        const actual = path.join(base, 'actual-sidecar.cjs');
        fs.renameSync(sidecar, actual); fs.symlinkSync(actual, sidecar); break;
      }
      case 'symlink-flag': {
        const actual = path.join(base, 'actual-flag');
        fs.renameSync(flag, actual); fs.symlinkSync(actual, flag); break;
      }
      case 'foreign-asset': foreignAsset = true; break;
      case 'foreign-folder': foreignFolder = true; break;
    }
    if (fs.existsSync(sidecar) && scenario !== 'symlink-asset')
      fs.chmodSync(sidecar, scenario === 'writable-asset' ? 0o666 : 0o444);
    fs.writeFileSync(policyFile, JSON.stringify(policy));
    const fdPaths = new Map(), fstatCounts = new Map();
    const overrideStat = (info, changes) => new Proxy(info, {get(target, property) {
      if (Object.hasOwn(changes, property)) return changes[property];
      const value = target[property];
      return typeof value === 'function' ? value.bind(target) : value;
    }});
    const fakeFs = new Proxy(fs, {get(target, name) {
      if (name === 'openSync') return (...args) => {
        const fd = target.openSync(...args); fdPaths.set(fd, args[0]); fstatCounts.set(fd, 0); return fd;
      };
      if (name === 'fstatSync') return fd => {
        const info = target.fstatSync(fd), file = fdPaths.get(fd);
        const count = (fstatCounts.get(fd) ?? 0) + 1; fstatCounts.set(fd, count);
        if (file === sidecar && foreignAsset) return overrideStat(info, {uid:process.getuid()+1});
        if (file === sidecar && scenario === 'asset-read-race' && count > 1)
          return overrideStat(info, {mtimeMs:info.mtimeMs+1});
        return info;
      };
      if (name === 'lstatSync') return file => {
        const info = target.lstatSync(file);
        return foreignFolder && file === path.dirname(sidecar) ?
          overrideStat(info, {uid:process.getuid()+1}) : info;
      };
      if (name === 'closeSync') return fd => { fdPaths.delete(fd); return target.closeSync(fd); };
      const value = target[name];
      return typeof value === 'function' ? value.bind(target) : value;
    }});
    const scopedRequire = name => name === 'node:fs' ? fakeFs : require(name);
    const scopedProcess = {env:{OPENBOT_SAND_DATA:root}, getuid:()=>process.getuid()};
    const select = new Function('require', 'process', 'nativePath',
      'function openbotContentSearchNativeWorkerEntryPath() { return nativePath; }\n' +
      request.selector + '\nreturn defaultWorkerEntryPath();');
    const selected = select(scopedRequire, scopedProcess, native);
    return {scenario, choseSidecar:selected === sidecar, choseNative:selected === native};
  } finally { fs.rmSync(base, {recursive:true, force:true}); }
}
process.stdout.write(JSON.stringify(request.scenarios.map(run)));
'''


@unittest.skipUnless(shutil.which('node'), 'Node is required for worker behavior')
class ContentSearchCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FIXTURE.read_text()
        cls.production_source = m.WORKER_SOURCE_SHA
        cls.production_component = m.WORKER_COMPONENT_SHA
        cls.production_patched = m.WORKER_PATCHED_SHA
        cls.production_patched_component = m.WORKER_PATCHED_COMPONENT_SHA

    def setUp(self):
        reviewed = patch.multiple(m, WORKER_SOURCE_SHA=SYNTHETIC_SOURCE_SHA,
            WORKER_COMPONENT_SHA=SYNTHETIC_COMPONENT_SHA,
            WORKER_PATCHED_SHA=SYNTHETIC_PATCHED_SHA,
            WORKER_PATCHED_COMPONENT_SHA=SYNTHETIC_PATCHED_COMPONENT_SHA)
        reviewed.start()
        self.addCleanup(reviewed.stop)

    def run_worker(self, mode, count=0, compare=False):
        sources = [m.patch_worker(self.source)]
        if compare:
            sources.insert(0, self.source)
        result = subprocess.run(['node', '-e', NODE_WORKER], input=json.dumps(
            {'sources':sources, 'mode':mode, 'count':count}), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = json.loads(result.stdout)
        for output in outputs:
            self.assertEqual(output['activeCloseViolations'], 0)
            self.assertTrue(output['readOnly'])
            self.assertTrue(output['allClosed'])
            self.assertEqual(output['live'], 0)
            self.assertEqual(output['cacheAfterClose'], 0)
        return outputs

    def test_production_fingerprints_are_pinned_and_synthetic_source_is_unreviewed(self):
        self.assertEqual(self.production_source, PRODUCTION_SOURCE_SHA)
        self.assertEqual(self.production_component, PRODUCTION_COMPONENT_SHA)
        self.assertEqual(self.production_patched, PRODUCTION_PATCHED_SHA)
        self.assertEqual(self.production_patched_component, PRODUCTION_PATCHED_COMPONENT_SHA)
        self.assertEqual(hashlib.sha256(self.source.encode()).hexdigest(), SYNTHETIC_SOURCE_SHA)
        with patch.multiple(m, WORKER_SOURCE_SHA=self.production_source,
            WORKER_COMPONENT_SHA=self.production_component, WORKER_PATCHED_SHA=self.production_patched,
            WORKER_PATCHED_COMPONENT_SHA=self.production_patched_component):
            with self.assertRaises(RuntimeError):
                m.patch_worker(self.source)

    def test_lru_touches_hits_evicts_oldest_and_never_opens_ninth_handle(self):
        output, = self.run_worker('lru')
        self.assertTrue(output['sameHit'])
        self.assertEqual(output['afterHit'], ['A1','A2','A3','A4','A5','A6','A7','A0'])
        self.assertEqual(output['afterNinth'], ['A2','A3','A4','A5','A6','A7','A0','A8'])
        self.assertEqual(output['final'], ['A3','A5','A6','A7','A0','A8','A4','A9'])
        self.assertEqual(output['retained'], 8)
        self.assertEqual(output['liveBeforeClose'], 8)
        self.assertEqual(output['peak'], 8)
        self.assertLess(output['events'].index('close:A1'), output['events'].index('open:A8'))
        self.assertLess(output['events'].index('close:A2'), output['events'].index('open:A9'))

    def test_every_agent_search_result_and_synchronous_fingerprint_reconcile_are_preserved(self):
        for count in (39, 261):
            with self.subTest(count=count):
                before, after = self.run_worker('results', count=count, compare=True)
                for key in ('initial','final','firstFingerprints','fingerprints','firstReindexed',
                            'unchangedReindexed','reindexed'):
                    self.assertEqual(after[key], before[key], key)
                self.assertEqual(len(after['initial']), count * 2)
                self.assertEqual(len(after['final']), count * 2 + 1)
                self.assertEqual(after['firstReindexed'], after['unchangedReindexed'])
                self.assertEqual(after['reindexed'], after['firstReindexed'] + ['agent-0'])
                self.assertEqual(after['fingerprints']['agent-0'], '5:9')
                self.assertEqual(after['retained'], 8)
                self.assertGreater(before['peak'], 8)
                self.assertEqual(after['peak'], 8)

    def test_explicit_reindex_closes_and_reopens_before_reading_rows(self):
        before, after = self.run_worker('reindex', compare=True)
        for key in ('beforeClosed','reopened','fingerprint','hits','retained'):
            self.assertEqual(after[key], before[key], key)
        self.assertTrue(after['beforeClosed'])
        self.assertTrue(after['reopened'])
        self.assertEqual(after['fingerprint'], '4:4')
        self.assertLess(after['events'].index('close:one'),
                        after['events'].index('open:one', after['events'].index('close:one')))

    def test_failed_close_blocks_ninth_open_and_native_error_fallbacks_stay_intact(self):
        output, = self.run_worker('errors')
        self.assertTrue(output['closeFailed'])
        self.assertEqual(output['failedKeys'], [f'A{i}' for i in range(8)])
        self.assertEqual(output['failedLive'], 8)
        self.assertFalse(output['openedNinthOnFailure'])
        self.assertTrue(output['retrySucceeded'])
        self.assertEqual(output['peak'], 8)
        for key in ('badSchema','badExec','badOpen','badLink','missing',
                    'fingerprintEvicted','reindexEvicted','malformedStillCached'):
            self.assertTrue(output[key], key)
        self.assertEqual(output['beforeLink'], output['afterLink'])
        self.assertIsNone(output['failedFingerprint'])
        self.assertIsNone(output['malformedFingerprint'])
        self.assertEqual(output['retainedOldIndex'], [{'id':'retained','text':'old needle'}])

    def test_exact_transform_changes_only_cache_hit_and_preopen_eviction(self):
        after = m.patch_worker(self.source)
        self.assertEqual(m.patch_worker(after), after)
        self.assertEqual(after.replace(m.HIT_REPLACEMENT, m.HIT_ANCHOR, 1).replace(
            m.OPEN_ANCHOR + m.EVICT_INSERT, m.OPEN_ANCHOR, 1), self.source)

    def test_unknown_sources_component_drift_duplicate_and_partial_markers_are_refused(self):
        after = m.patch_worker(self.source)
        for source in (self.source + '\n// unknown full worker bytes\n',
                       self.source.replace('synthetic connection settings', 'unknown settings'),
                       self.source + self.source,
                       self.source + '\n/* ' + m.WORKER_MARK + ' */\n',
                       after + '\n/* ' + m.WORKER_MARK + ' */\n',
                       after.replace(m.WORKER_MARK, m.WORKER_MARK + '-corrupted'),
                       after.replace('this.storeConnections.delete(agentId);', '// missing cache touch', 1)):
            with self.subTest(source=source[:90]), self.assertRaises(RuntimeError):
                m.patch_worker(source)

    def test_component_guards_refuse_drift_even_with_reviewed_outer_source_hash(self):
        drifted = self.source.replace('synthetic connection settings', 'changed component semantics')
        with patch.object(m, 'WORKER_SOURCE_SHA', hashlib.sha256(drifted.encode()).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, 'cache contract'):
                m.patch_worker(drifted)
        for source in (self.source.replace(m.WORKER_START, m.WORKER_START + '\n' + m.WORKER_START),
                       self.source.replace(m.WORKER_END, m.WORKER_END + '\n' + m.WORKER_END)):
            with self.subTest(source=source[:60]), patch.object(
                    m, 'WORKER_SOURCE_SHA', hashlib.sha256(source.encode()).hexdigest()):
                with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
                    m.patch_worker(source)
        with patch.object(m, 'WORKER_PATCHED_COMPONENT_SHA', '0' * 64):
            with self.assertRaisesRegex(RuntimeError, 'transformation differs'):
                m.patch_worker(self.source)


@unittest.skipUnless(shutil.which('node'), 'Node is required for host selection behavior')
class ContentSearchHostSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = FIXTURE.read_text()
        cls.production_host_component = m.HOST_COMPONENT_SHA
        cls.production_host_patched_component = m.HOST_PATCHED_COMPONENT_SHA

    def setUp(self):
        reviewed = patch.multiple(m, WORKER_SOURCE_SHA=SYNTHETIC_SOURCE_SHA,
            WORKER_COMPONENT_SHA=SYNTHETIC_COMPONENT_SHA,
            WORKER_PATCHED_SHA=SYNTHETIC_PATCHED_SHA,
            WORKER_PATCHED_COMPONENT_SHA=SYNTHETIC_PATCHED_COMPONENT_SHA,
            HOST_COMPONENT_SHA=SYNTHETIC_HOST_COMPONENT_SHA,
            HOST_PATCHED_COMPONENT_SHA=SYNTHETIC_HOST_PATCHED_COMPONENT_SHA)
        reviewed.start()
        self.addCleanup(reviewed.stop)

    def select(self, scenarios):
        selector = m.HOST_SELECTOR.replace('WORKER_SOURCE_SHA_LITERAL', m.WORKER_SOURCE_SHA).replace(
            'WORKER_PATCHED_SHA_LITERAL', m.WORKER_PATCHED_SHA)
        result = subprocess.run(['node', '-e', NODE_HOST_SELECTOR], input=json.dumps({
            'native':self.worker, 'patched':m.patch_worker(self.worker), 'selector':selector,
            'sourceSha':m.WORKER_SOURCE_SHA, 'patchedSha':m.WORKER_PATCHED_SHA,
            'scenarios':scenarios}), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_valid_custom_local_asset_selects_reviewed_immutable_sidecar(self):
        output, = self.select(['valid'])
        self.assertTrue(output['choseSidecar'])
        self.assertFalse(output['choseNative'])

    def test_disabled_missing_unknown_or_untrusted_assets_keep_native_worker(self):
        scenarios = ['official','missing-mode','missing-flag','disabled-policy','wrong-schema',
            'wrong-policy-source','unknown-native','missing-asset','corrupt-asset','wrong-path',
            'symlink-asset','symlink-flag','writable-asset','foreign-asset','foreign-folder','asset-read-race']
        for output in self.select(scenarios):
            with self.subTest(scenario=output['scenario']):
                self.assertFalse(output['choseSidecar'])
                self.assertTrue(output['choseNative'])

    def test_known_host_transform_is_idempotent_and_preserves_outer_bytes(self):
        source = '/* unrelated prefix */\n' + HOST_FIXTURE + '/* unrelated suffix */\n'
        after = m.patch(source)
        self.assertEqual(m.patch(after), after)
        self.assertEqual(after.count(m.HOST_MARK), 1)
        selector = m.HOST_SELECTOR.replace('WORKER_SOURCE_SHA_LITERAL', m.WORKER_SOURCE_SHA).replace(
            'WORKER_PATCHED_SHA_LITERAL', m.WORKER_PATCHED_SHA)
        self.assertEqual(after.replace(selector, '', 1).replace(
            m.HOST_RETAINED_START, m.HOST_START, 1), source)

    def test_production_host_components_are_pinned_and_unknown_unmarked_variants_are_unchanged(self):
        self.assertEqual(self.production_host_component, PRODUCTION_HOST_COMPONENT_SHA)
        self.assertEqual(self.production_host_patched_component, PRODUCTION_HOST_PATCHED_COMPONENT_SHA)
        for source in ('const noKnownWorkerSelector = true;\n',
                       HOST_FIXTURE.replace('/synthetic/native-worker.cjs', '/unknown/worker.cjs'),
                       HOST_FIXTURE + HOST_FIXTURE):
            with self.subTest(source=source):
                self.assertEqual(m.patch(source), source)
        with patch.multiple(m, HOST_COMPONENT_SHA=self.production_host_component,
                            HOST_PATCHED_COMPONENT_SHA=self.production_host_patched_component):
            self.assertEqual(m.patch(HOST_FIXTURE), HOST_FIXTURE)

    def test_marked_host_drift_partial_and_duplicate_markers_refuse(self):
        after = m.patch(HOST_FIXTURE)
        for source in (
                after + '\n/* ' + m.HOST_MARK + ' */\n',
                after.replace(m.HOST_MARK, m.HOST_MARK + '-corrupted'),
                after.replace('policy.enabled !== true', 'policy.enabled !== false'),
                after.replace(m.HOST_RETAINED_START, 'function missingNativeEntryPath() {'),
                HOST_FIXTURE.replace(m.HOST_START, m.HOST_RETAINED_START),
                HOST_FIXTURE + '\n/* ' + m.HOST_MARK + ' */\n',
                after + '\nfunction defaultWorkerEntryPath() { return "overridden"; }\n'):
            with self.subTest(source=source[:90]), self.assertRaises(RuntimeError):
                m.patch(source)


if __name__ == '__main__':
    unittest.main()
