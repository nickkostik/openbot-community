"""Behavioral checks using an explicitly synthetic, test-owned roster component.

The fixture shares the transform's scheduling anchors but contains no copied host
module or agent data. Production fingerprints remain pinned independently and
reject the synthetic source. Mocked review hashes permit Node runtime checks of
the scheduling transform, failure branches, and worker limit with fake I/O.
"""
import hashlib
import importlib.util
import json
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parent
FIXTURE = HERE / 'fixtures' / 'native-roster-list-agents-synthetic.js'
PRODUCTION_STOCK_SHA256 = 'befae3821d1c1d4ee0676b99575fc3bd8e4ca970634d3e519fccdeda4dd0de87'
PRODUCTION_PATCHED_SHA256 = '9a6df18673c85f8788a6fd593e28718bfda8ef8869006b8995c82c37bcfb17be'
PRODUCTION_NATIVE_STOCK_SHA256 = 'e879c6304413940614bec292822431bf4124ec747cdcb2c935638f75fc5bc3f8'
PRODUCTION_NATIVE_PATCHED_SHA256 = '49a62157bc319c99f7db639710e6a60220a2e673ef55ddc6e04edfe24dcfd4da'
SYNTHETIC_STOCK_SHA256 = 'b3ccdea2396d9de8820d19d2b3cd1ea9dab007cecc95889400b0ad57155bb07e'
SYNTHETIC_PATCHED_SHA256 = '9c43d63e76b187ac70355a451158a06369630e177c0b80d2adea75c91b3542be'
SYNTHETIC_NATIVE_STOCK_SHA256 = '7ecf8915223b8a14172f5d71aff312dc537c84c061cc49e7f62798090c044605'
SYNTHETIC_NATIVE_PATCHED_SHA256 = '2087c45e867ca15527fd07518d0502b9b77ad978f63c8afb3dbe7afb62857566'
START = 'async function listAgents(host, activeAgentId) {'
END = 'async function summarizeAgentById('
PREFIX = '/* independent wrapper before the reviewed component */\nconst untouchedBefore = 7;\n'
SUFFIX = 'async function summarizeAgentById(host, agentId, activeAgentId) { return null; }\n/* independent wrapper after the reviewed component */\n'

spec = importlib.util.spec_from_file_location(
    'roster_read_concurrency', HERE / 'roster-read-concurrency-inject.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


NODE_HARNESS = r'''
const fs = require('node:fs');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function run(source, spec) {
  const entries = spec.entries || [];
  const config = spec.config || {};
  const byId = id => config[id] || {};
  const calls = [];
  const diagnostics = [];
  const memoryCalls = [];
  const live = new Set();
  const loaded = new Set();
  const loadFailed = new Set();
  const pruneCalls = [];
  let active = 0;
  let maxActive = 0;
  let outcome;
  const error = (code, stage) => Object.assign(new Error(stage), {code});
  const idOf = path => path.split('/').at(-2);
  const finish = id => { if (live.delete(id)) active--; };
  const wait = async id => pause(byId(id).delay ?? 2);
  const summary = (id, options = {}) => ({
    id,
    sortKey: byId(id).sortKey ?? 1,
    active: options.activeAgentId === id,
    preview: options.extras === null ? 'disk-preview:' + id : 'cached-preview:' + id,
    includeBlank: options.includeBlank ?? null,
    hasMemory: options.agentHasMemory ? options.agentHasMemory(id) : null,
    ...options.extra
  });
  const host = {
    rootDir: '/fake/agents',
    isAgentBeingDeleted(id) {
      const cfg = byId(id);
      const deleted = !!cfg.deletedInitial || (!!cfg.deletedAfterLoad && loaded.has(id)) ||
        (!!cfg.deletedAfterLoadError && loadFailed.has(id));
      if (deleted) finish(id);
      return deleted;
    },
    memory: {agentHasContent(id) { memoryCalls.push(id); return !!byId(id).memory; }},
    async loadCachedExtras({dirName, dbPath, dbStats}) {
      calls.push({stage: 'extras', id: dirName, dbPath, dbStats});
      await wait(dirName);
      if (byId(dirName).loadError) {
        loadFailed.add(dirName);
        throw error('LOAD', 'extras');
      }
      loaded.add(dirName);
      if (byId(dirName).nullExtras) return null;
      return {preview: 'cached-preview:' + dirName};
    },
    async recoverAgentWithMissingDb({dbPath, dirName, activeAgentId}) {
      calls.push({stage: 'recover', id: dirName, dbPath, activeAgentId});
      await wait(dirName);
      if (byId(dirName).recoveryError) throw error('RECOVER', 'recover');
      finish(dirName);
      if (byId(dirName).recoveryNull) return null;
      return summary(dirName, {activeAgentId, extra: {recovered: true}});
    },
    pruneExtrasCache(names) {
      pruneCalls.push([...names]);
      if (spec.pruneError) throw error('PRUNE', 'prune');
    }
  };
  const import_promises77 = {
    async readdir(root, options) {
      if (root !== host.rootDir || options.withFileTypes !== true) throw Error('wrong readdir arguments');
      if (spec.readdirError) throw error(spec.readdirError, 'readdir');
      return entries.map(entry => ({name: entry.name, isDirectory: () => entry.directory !== false}));
    }
  };
  const getAgentDbPath = (root, id) => root + '/' + id + '/store.db';
  const statIfExists = async dbPath => {
    const id = idOf(dbPath);
    if (live.has(id)) throw Error('duplicate hydration: ' + id);
    live.add(id); active++; maxActive = Math.max(maxActive, active);
    calls.push({stage: 'stat', id, dbPath});
    await wait(id);
    if (byId(id).statError) { finish(id); throw error('STAT', 'stat'); }
    return byId(id).missing ? null : {mtimeMs: 123, size: 456};
  };
  const buildSummary = async options => {
    const id = options.dirName;
    const fallback = options.includeBlank === true;
    calls.push({stage: fallback ? 'fallback' : 'primary', id,
      includeBlank: options.includeBlank, dbStats: options.dbStats,
      activeAgentId: options.activeAgentId, extras: options.extras});
    await wait(id);
    if (fallback ? byId(id).fallbackError : byId(id).buildError) {
      throw error(fallback ? 'FALLBACK' : 'BUILD', 'build');
    }
    finish(id);
    if (byId(id).blank && !options.includeBlank) return null;
    return summary(id, options);
  };
  const minimalAgentSummary = options => {
    const id = options.dirName;
    calls.push({stage: 'minimal', id, activeAgentId: options.activeAgentId,
      hasDbStats: options.dbStats !== undefined, dbStats: options.dbStats});
    finish(id);
    return summary(id, {activeAgentId: options.activeAgentId,
      extra: {minimal: true, hasDbStats: options.dbStats !== undefined}});
  };
  const reportSessionDiagnostic = item => diagnostics.push(item);
  const errorLogTag = err => err.code || err.name;
  const compareAgentSummaries = (a, b) => b.sortKey - a.sortKey;
  const listAgents = new Function('import_promises77', 'getAgentDbPath', 'statIfExists',
    'reportSessionDiagnostic', 'errorLogTag', 'minimalAgentSummary', 'buildSummary',
    'compareAgentSummaries', 'keepAgentDirs', source + '\nreturn listAgents;')(
      import_promises77, getAgentDbPath, statIfExists, reportSessionDiagnostic,
      errorLogTag, minimalAgentSummary, buildSummary, compareAgentSummaries,
      entries => entries.filter(entry => entry.isDirectory()).map(entry => entry.name));
  try {
    outcome = {result: await listAgents(host, spec.activeAgentId)};
  } catch (err) {
    outcome = {error: {code: err.code, message: err.message}};
  }
  // Promise.all rejects before its other already-running inputs settle. Waiting
  // here permits deterministic inspection without changing the rejection result.
  await pause(20);
  const canonical = values => values.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  return {...outcome, maxActive, active, calls: canonical(calls),
    diagnostics: canonical(diagnostics), memoryCalls: memoryCalls.sort(), pruneCalls};
}
(async () => {
  const output = [];
  for (const item of request) output.push(await run(item.source, item.spec));
  process.stdout.write(JSON.stringify(output));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


NODE_PENDING_STAT_FAILURE = r'''
const fs = require('node:fs');
const sources = JSON.parse(fs.readFileSync(0, 'utf8'));
const deferred = () => {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return {promise, resolve};
};
async function run(source) {
  const enteredSlow = deferred();
  const releaseSlow = deferred();
  const finishedSlow = deferred();
  const events = [];
  const diagnostics = [];
  let pruneCount = 0;
  let released = false;
  const host = {
    rootDir: '/fake/agents',
    isAgentBeingDeleted: () => false,
    loadCachedExtras: async () => ({}),
    memory: {agentHasContent: () => false},
    pruneExtrasCache() { pruneCount++; }
  };
  const io = {readdir: async () => ['bad', 'slow', 'queued'].map(name =>
    ({name, isDirectory: () => true}))};
  const getPath = (root, name) => root + '/' + name + '/store.db';
  const stat = async path => {
    const name = path.split('/').at(-2);
    events.push('stat:' + name);
    if (name === 'bad') {
      await enteredSlow.promise;
      throw Object.assign(new Error('stat failed'), {code: 'STAT'});
    }
    if (name === 'slow') {
      enteredSlow.resolve();
      await releaseSlow.promise;
    }
    return {mtimeMs: 123};
  };
  const build = async ({dirName}) => {
    events.push('build:' + dirName);
    if (dirName === 'slow') finishedSlow.resolve();
    return {id: dirName};
  };
  const list = new Function('import_promises77', 'getAgentDbPath', 'statIfExists',
    'reportSessionDiagnostic', 'errorLogTag', 'minimalAgentSummary', 'buildSummary',
    'compareAgentSummaries', 'keepAgentDirs', source + '\nreturn listAgents;')(
      io, getPath, stat, item => diagnostics.push(item), err => err.code,
      () => { throw Error('unexpected minimal fallback'); }, build, () => 0,
      entries => entries.filter(entry => entry.isDirectory()).map(entry => entry.name));
  const result = list(host, 'slow').then(
    () => ({outcome: 'resolved', beforeRelease: !released}),
    error => ({outcome: 'rejected', code: error.code, beforeRelease: !released}));
  let timer;
  const observed = await Promise.race([result, new Promise(resolve => {
    timer = setTimeout(() => resolve({outcome: 'hung'}), 1000);
  })]);
  clearTimeout(timer);
  released = true;
  releaseSlow.resolve();
  await finishedSlow.promise;
  // Drain promises from the surviving worker without a timing-dependent sleep.
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  return {...observed, pruneCount, diagnostics, events};
}
(async () => {
  const output = [];
  for (const source of sources) output.push(await run(source));
  process.stdout.write(JSON.stringify(output));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


NODE_SHARED_GATE_HARNESS = r'''
const fs = require('node:fs');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const nextTick = () => new Promise(resolve => setImmediate(resolve));
const until = async (predicate, label) => {
  const deadline = Date.now() + 2000;
  while (!predicate()) {
    if (Date.now() > deadline) throw Error('timed out waiting for ' + label);
    await nextTick();
  }
};
const deferred = () => {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return {promise, resolve};
};

function system(source, options = {}) {
  const rosters = new Map();
  const gates = new Map();
  const live = new Map();
  const starts = [];
  const pruned = [];
  const diagnostics = [];
  let active = 0;
  let peak = 0;
  const nameOf = path => path.split('/').at(-2);
  const gate = name => {
    if (!gates.has(name)) gates.set(name, deferred());
    return gates.get(name);
  };
  const finish = name => {
    const count = live.get(name) ?? 0;
    if (count > 0) {
      active--;
      if (count === 1) live.delete(name);
      else live.set(name, count - 1);
    }
  };
  const io = {readdir: async root => rosters.get(root).map(name =>
    ({name, isDirectory: () => true}))};
  const getPath = (root, name) => root + '/' + name + '/store.db';
  const stat = async path => {
    const name = nameOf(path);
    starts.push(name);
    live.set(name, (live.get(name) ?? 0) + 1);
    active++; peak = Math.max(peak, active);
    if (options.controlled) await gate(name).promise;
    else await pause(1);
    if (options.statFailure === name) {
      finish(name);
      throw Object.assign(new Error('stat failure'), {code: 'STAT'});
    }
    return {mtimeMs: 123};
  };
  const build = async ({dirName, includeBlank}) => {
    if (!options.controlled) await pause(1);
    if (options.buildFailure === dirName) {
      // The native fallback also fails, forcing the minimal-summary branch.
      throw Object.assign(new Error('build failure'), {code: 'BUILD'});
    }
    finish(dirName);
    return {id: dirName, includeBlank};
  };
  const minimal = ({dirName}) => {
    finish(dirName);
    if (options.minimalFailure === dirName) {
      throw Object.assign(new Error('minimal failure'), {code: 'MINIMAL'});
    }
    return {id: dirName, minimal: true};
  };
  const list = new Function('import_promises77', 'getAgentDbPath', 'statIfExists',
    'reportSessionDiagnostic', 'errorLogTag', 'minimalAgentSummary', 'buildSummary',
    'compareAgentSummaries', 'keepAgentDirs', source + '\nreturn listAgents;')(
      io, getPath, stat, item => diagnostics.push(item), err => err.code,
      minimal, build, () => 0,
      entries => entries.filter(entry => entry.isDirectory()).map(entry => entry.name));
  const host = (label, count) => {
    const root = '/fake/' + label;
    const names = Array.from({length: count}, (_, index) => label + '-' + index);
    rosters.set(root, names);
    return {rootDir: root, names,
      isAgentBeingDeleted: () => false,
      loadCachedExtras: async () => ({}),
      memory: {agentHasContent: () => false},
      pruneExtrasCache() { pruned.push(label); }};
  };
  const state = () => ({peak, active, starts, pruned, diagnostics,
    permits: list.__openbotRosterReadGate?.active ?? null,
    waiters: list.__openbotRosterReadGate?.waiters.length ?? null});
  return {list, host, state, gate};
}

async function run(source, mode) {
  if (mode === 'success' || mode === 'same-host') {
    const env = system(source);
    // Four simultaneous native roster calls share the same function instance.
    const shared = mode === 'same-host' ? env.host('shared', 39) : null;
    const hosts = shared ? [shared, shared, shared, shared] :
      [env.host('A', 39), env.host('B', 261), env.host('C', 39), env.host('D', 261)];
    const rows = await Promise.all(hosts.map(host => env.list(host, 'unused')));
    return {...env.state(), rows: rows.map(items => items.map(item => item.id)),
      expected: hosts.map(host => host.names)};
  }
  if (mode === 'fifo') {
    const env = system(source, {controlled: true});
    const a = env.list(env.host('A', 2), 'unused');
    await until(() => env.state().starts.length === 2, 'first two readers');
    const b = env.list(env.host('B', 2), 'unused');
    await until(() => env.state().waiters === 2, 'second call waiters');
    const c = env.list(env.host('C', 2), 'unused');
    await until(() => env.state().waiters === 4, 'third call waiters');
    for (const [release, next] of [['A-0', 'B-0'], ['A-1', 'B-1'],
                                  ['B-0', 'C-0'], ['B-1', 'C-1']]) {
      env.gate(release).resolve();
      await until(() => env.state().starts.includes(next), 'FIFO reader ' + next);
    }
    env.gate('C-0').resolve();
    env.gate('C-1').resolve();
    await Promise.all([a, b, c]);
    return env.state();
  }
  const failure = mode === 'stat-error' ? {statFailure: 'A-0'} :
    {buildFailure: 'A-0', minimalFailure: 'A-0'};
  const env = system(source, failure);
  const results = await Promise.allSettled([
    env.list(env.host('A', 7), 'unused'), env.list(env.host('B', 39), 'unused'),
    env.list(env.host('C', 39), 'unused')]);
  // A sibling worker may outlive the rejection. Drain all its queued work before
  // checking permit cleanup and reusing the same function for a subsequent call.
  await until(() => env.state().active === 0 && env.state().waiters === 0,
              'error-path readers to drain');
  const later = env.host('later', 39);
  const laterRows = await env.list(later, 'unused');
  return {...env.state(), outcomes: results.map(result => ({status: result.status,
    code: result.reason?.code, rows: result.value?.length})),
    later: laterRows.map(row => row.id), laterExpected: later.names};
}
(async () => {
  const results = [];
  for (const source of request.sources) results.push(await run(source, request.mode));
  process.stdout.write(JSON.stringify(results));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


class RosterReadConcurrencyInjectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = FIXTURE.read_text()
        cls.source = PREFIX + cls.baseline + SUFFIX
        cls.production_stock_hash = m.STOCK_SHA256
        cls.production_patched_hash = m.PATCHED_SHA256
        cls.production_native_stock_hash = m.NATIVE_STOCK_SHA256
        cls.production_native_patched_hash = m.NATIVE_PATCHED_SHA256

    def setUp(self):
        reviewed = patch.multiple(m, STOCK_SHA256=SYNTHETIC_STOCK_SHA256,
                                  PATCHED_SHA256=SYNTHETIC_PATCHED_SHA256)
        reviewed.start()
        self.addCleanup(reviewed.stop)

    def patched_component(self):
        patched = m.patch(self.source)
        return patched[len(PREFIX):-len(SUFFIX)]

    def run_variants(self, scenario, sources=None):
        if shutil.which('node') is None:
            self.fail('Node is required for the roster behavioral regression tests')
        payload = [{'source': source, 'spec': scenario}
                   for source in (sources or (self.baseline, self.patched_component()))]
        result = subprocess.run(['node', '-e', NODE_HARNESS], input=json.dumps(payload),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_same_behavior(self, before, after):
        self.assertEqual({key: value for key, value in before.items() if key != 'maxActive'},
                         {key: value for key, value in after.items() if key != 'maxActive'})

    def run_shared_gate(self, mode, include_baseline=False):
        sources = [self.patched_component()]
        if include_baseline:
            sources.insert(0, self.baseline)
        result = subprocess.run(['node', '-e', NODE_SHARED_GATE_HARNESS],
                                input=json.dumps({'sources': sources, 'mode': mode}),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_four_overlapping_39_and_261_row_calls_share_global_two_reader_cap(self):
        before, after = self.run_shared_gate('success', include_baseline=True)
        self.assertGreater(before['peak'], 2)
        self.assertEqual(after['peak'], 2)
        self.assertEqual(after['rows'], before['rows'])
        self.assertEqual(after['rows'], after['expected'])
        self.assertEqual([len(rows) for rows in after['rows']], [39, 261, 39, 261])
        self.assertEqual(after['active'], 0)
        self.assertEqual(after['permits'], 0)
        self.assertEqual(after['waiters'], 0)
        self.assertEqual(set(after['pruned']), {'A', 'B', 'C', 'D'})

    def test_global_permit_waiters_are_fifo_across_roster_calls(self):
        result, = self.run_shared_gate('fifo')
        self.assertEqual(result['starts'], ['A-0', 'A-1', 'B-0', 'B-1', 'C-0', 'C-1'])
        self.assertEqual(result['peak'], 2)
        self.assertEqual(result['active'], 0)
        self.assertEqual(result['permits'], 0)
        self.assertEqual(result['waiters'], 0)

    def test_overlapping_reads_of_identical_host_and_agent_ids_are_bounded(self):
        before, after = self.run_shared_gate('same-host', include_baseline=True)
        self.assertGreater(before['peak'], 2)
        self.assertEqual(after['peak'], 2)
        self.assertEqual(after['rows'], before['rows'])
        self.assertEqual(after['rows'], after['expected'])
        self.assertEqual([len(rows) for rows in after['rows']], [39, 39, 39, 39])
        self.assertEqual(after['active'], 0)
        self.assertEqual(after['permits'], 0)
        self.assertEqual(after['waiters'], 0)
        self.assertEqual(after['pruned'], ['shared'] * 4)

    def test_global_permits_release_after_uncaught_errors_and_later_call_completes(self):
        for mode, code in [('stat-error', 'STAT'), ('summary-error', 'MINIMAL')]:
            with self.subTest(mode=mode):
                result, = self.run_shared_gate(mode)
                self.assertEqual(result['outcomes'], [
                    {'status': 'rejected', 'code': code},
                    {'status': 'fulfilled', 'rows': 39},
                    {'status': 'fulfilled', 'rows': 39}])
                self.assertEqual(result['later'], result['laterExpected'])
                self.assertEqual(result['peak'], 2)
                self.assertEqual(result['active'], 0)
                self.assertEqual(result['permits'], 0)
                self.assertEqual(result['waiters'], 0)
                self.assertEqual(set(result['pruned']), {'B', 'C', 'later'})

    def test_synthetic_fixture_and_independent_production_hashes_are_pinned(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), SYNTHETIC_STOCK_SHA256)
        self.assertEqual(self.production_stock_hash, PRODUCTION_STOCK_SHA256)
        self.assertEqual(self.production_patched_hash, PRODUCTION_PATCHED_SHA256)
        self.assertEqual(self.production_native_stock_hash, PRODUCTION_NATIVE_STOCK_SHA256)
        self.assertEqual(self.production_native_patched_hash, PRODUCTION_NATIVE_PATCHED_SHA256)
        self.assertIn('Synthetic, test-owned component.', self.baseline)
        self.assertEqual(self.baseline.count(START), 1)
        self.assertNotIn(END, self.baseline)
        self.assertTrue(self.baseline.endswith('\n}\n'))
        self.assertEqual(m.MARK, 'openbot-roster-read-concurrency')

    def test_production_review_refuses_the_synthetic_fixture(self):
        with patch.multiple(m, STOCK_SHA256=self.production_stock_hash,
                            PATCHED_SHA256=self.production_patched_hash):
            with self.assertRaises(RuntimeError):
                m.patch(self.source)

    def test_keep_agent_dirs_variant_preserves_rows_fallback_and_global_bound(self):
        self.assert_native_variant('NATIVE_STOCK_SHA256', 'NATIVE_PATCHED_SHA256')

    def test_current_native_variant_preserves_rows_fallback_and_global_bound(self):
        self.assert_native_variant('CURRENT_NATIVE_STOCK_SHA256', 'CURRENT_NATIVE_PATCHED_SHA256')

    def assert_native_variant(self, stock_key, patched_key):
        native = self.baseline.replace(m.ORIGINAL_OPEN, m.NATIVE_ORIGINAL_OPEN).replace(
            'const id = entry.name;', 'const id = agentId;')
        self.assertEqual(hashlib.sha256(native.encode()).hexdigest(), SYNTHETIC_NATIVE_STOCK_SHA256)
        wrapped = PREFIX + native + SUFFIX
        with self.assertRaises(RuntimeError):
            m.patch(wrapped)
        with patch.multiple(m, **{stock_key: SYNTHETIC_NATIVE_STOCK_SHA256,
                                patched_key: SYNTHETIC_NATIVE_PATCHED_SHA256}):
            patched = m.patch(wrapped)
            self.assertEqual(m.patch(patched), patched)
            self.assertTrue(patched.startswith(PREFIX))
            self.assertTrue(patched.endswith(SUFFIX))
            body = patched[len(PREFIX):-len(SUFFIX)]
            for count in (39, 261):
                entries = [{'name': f'agent-{index}'} for index in range(count)]
                before, after = self.run_variants({'entries': entries,
                    'config': {'agent-1': {'loadError': True},
                               'agent-2': {'missing': True, 'recoveryError': True},
                               'agent-3': {'deletedInitial': True}}}, sources=(native, body))
                self.assert_same_behavior(before, after)
                self.assertEqual(after['maxActive'], 2)
                self.assertEqual(len(after['result']), count - 1)
            result = subprocess.run(['node', '-e', NODE_SHARED_GATE_HARNESS],
                                    input=json.dumps({'sources': [body], 'mode': 'success'}),
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            global_result, = json.loads(result.stdout)
            self.assertEqual(global_result['peak'], 2)
            self.assertEqual(global_result['rows'], global_result['expected'])
            self.assertEqual(global_result['permits'], 0)
            self.assertEqual(global_result['waiters'], 0)
            for drifted in [patched.replace('includeBlank: true', 'includeBlank: false'),
                            patched.replace(m.MARK, m.MARK + '-corrupted')]:
                with self.assertRaises(RuntimeError):
                    m.patch(drifted)

    def test_full_directory_scope_and_stable_sort_survive_bounded_workers(self):
        for count in (39, 261):
            with self.subTest(count=count):
                # Names deliberately disagree with lexicographic and completion order.
                names = [f'agent-{count - index:03d}' for index in range(count)]
                entries = [{'name': name} for name in names]
                entries.insert(3, {'name': 'roster-not-a-directory.json', 'directory': False})
                scenario = {'entries': entries, 'activeAgentId': names[-1], 'config': {
                    name: {'delay': (index % 3) + 1, 'sortKey': index % 4, 'memory': index % 2 == 0}
                    for index, name in enumerate(names)}}
                before, after = self.run_variants(scenario)
                self.assert_same_behavior(before, after)
                self.assertGreater(before['maxActive'], 2)
                self.assertEqual(after['maxActive'], 2)
                self.assertEqual(len(after['result']), count)
                self.assertEqual(after['active'], 0)
                self.assertEqual([row['id'] for row in after['result']],
                                 sorted(names, key=lambda name: -scenario['config'][name]['sortKey']))
                self.assertEqual(after['pruneCalls'], [[entry['name'] for entry in entries]])

    def test_recovery_deleted_blank_and_both_fallback_paths_match_baseline(self):
        config = {
            'normal': {'memory': True},
            'null-extras': {'nullExtras': True},
            'deleted-initial': {'deletedInitial': True},
            'deleted-after-extras': {'deletedAfterLoad': True},
            'deleted-in-error-handler': {'loadError': True, 'deletedAfterLoadError': True},
            'missing-recovered': {'missing': True},
            'missing-null': {'missing': True, 'recoveryNull': True},
            'missing-failed': {'missing': True, 'recoveryError': True},
            'load-degraded': {'loadError': True, 'memory': True},
            'build-degraded': {'buildError': True},
            'fallback-failed': {'loadError': True, 'fallbackError': True},
            'blank-normal': {'blank': True},
            'blank-degraded': {'blank': True, 'buildError': True},
        }
        before, after = self.run_variants({'entries': [{'name': name} for name in config],
                                          'config': config, 'activeAgentId': 'load-degraded'})
        self.assert_same_behavior(before, after)
        self.assertLessEqual(after['maxActive'], 2)
        self.assertEqual(after['active'], 0)
        rows = {row['id']: row for row in after['result']}
        self.assertEqual(set(rows), {'normal', 'null-extras', 'missing-recovered', 'missing-failed',
                                    'load-degraded', 'build-degraded', 'fallback-failed', 'blank-degraded'})
        self.assertFalse(rows['null-extras']['includeBlank'])
        self.assertEqual(rows['load-degraded']['preview'], 'disk-preview:load-degraded')
        self.assertTrue(rows['load-degraded']['includeBlank'])
        self.assertTrue(rows['load-degraded']['active'])
        self.assertTrue(rows['load-degraded']['hasMemory'])
        self.assertFalse(rows['missing-failed']['hasDbStats'])
        self.assertTrue(rows['fallback-failed']['hasDbStats'])
        self.assertEqual([(item['agentId'], item['kind']) for item in after['diagnostics']],
                         [(item['agentId'], item['kind']) for item in before['diagnostics']])
        self.assertEqual(sum(call['stage'] == 'extras' and call['id'] == 'load-degraded'
                             for call in after['calls']), 1)
        self.assertTrue(all(call['extras'] is None and call['includeBlank'] is True
                            for call in after['calls'] if call['stage'] == 'fallback'))

    def test_empty_and_file_only_rosters_do_not_hydrate_anything(self):
        for entries in ([], [{'name': 'notes.txt', 'directory': False}]):
            with self.subTest(entries=entries):
                before, after = self.run_variants({'entries': entries})
                self.assert_same_behavior(before, after)
                self.assertEqual(after['result'], [])
                self.assertEqual(after['maxActive'], 0)
                self.assertEqual(after['pruneCalls'], [[entry['name'] for entry in entries]])

    def test_directory_failure_semantics_are_preserved(self):
        for code in ('ENOENT', 'EACCES'):
            with self.subTest(code=code):
                before, after = self.run_variants({'readdirError': code})
                self.assert_same_behavior(before, after)
                self.assertEqual(after['pruneCalls'], [])
                self.assertEqual(after['calls'], [])
                if code == 'ENOENT':
                    self.assertEqual(after['result'], [])
                else:
                    self.assertEqual(after['error']['code'], code)

    def test_stat_failure_propagates_without_degraded_fallback_or_prune(self):
        before, after = self.run_variants({'entries': [{'name': 'stat-failure'}],
                                          'config': {'stat-failure': {'statError': True}}})
        self.assert_same_behavior(before, after)
        self.assertEqual(after['error']['code'], 'STAT')
        self.assertEqual(after['diagnostics'], [])
        self.assertEqual(after['pruneCalls'], [])
        self.assertEqual([call['stage'] for call in after['calls']], ['stat'])

    def test_stat_failure_rejects_promptly_while_sibling_and_queue_are_pending(self):
        result = subprocess.run(['node', '-e', NODE_PENDING_STAT_FAILURE],
                                input=json.dumps([self.baseline, self.patched_component()]),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for variant in json.loads(result.stdout):
            self.assertEqual(variant['outcome'], 'rejected')
            self.assertEqual(variant['code'], 'STAT')
            self.assertTrue(variant['beforeRelease'])
            self.assertEqual(variant['pruneCount'], 0)
            self.assertEqual(variant['diagnostics'], [])
            self.assertIn('stat:slow', variant['events'])
            self.assertIn('stat:queued', variant['events'])

    def test_active_agent_identity_reaches_recovery_and_minimal_summaries(self):
        config = {'recovered': {'missing': True},
                  'minimal-missing': {'missing': True, 'recoveryError': True},
                  'minimal-existing': {'buildError': True, 'fallbackError': True}}
        for active in config:
            with self.subTest(active=active):
                before, after = self.run_variants({'entries': [{'name': name} for name in config],
                                                  'config': config, 'activeAgentId': active})
                self.assert_same_behavior(before, after)
                self.assertEqual([row['id'] for row in after['result'] if row['active']], [active])

    def test_prune_failure_propagates_after_hydration(self):
        before, after = self.run_variants({'entries': [{'name': 'normal'}], 'pruneError': True})
        self.assert_same_behavior(before, after)
        self.assertEqual(after['error']['code'], 'PRUNE')
        self.assertEqual(after['active'], 0)

    def test_only_scheduling_bytes_change_and_exact_patch_is_idempotent(self):
        patched = m.patch(self.source)
        self.assertTrue(patched.startswith(PREFIX))
        self.assertTrue(patched.endswith(SUFFIX))
        self.assertEqual(m.patch(patched), patched)
        # Preserve the complete synthetic hydration body, including preview recovery,
        # includeBlank, active-agent/memory callbacks, diagnostics and minimal fallbacks.
        body = self.baseline.split('    entries.filter((entry) => entry.isDirectory()).map(async (entry) => {\n', 1)[1]
        body = body.rsplit('\n    })\n  );', 1)[0]
        self.assertIn(body, patched)
        self.assertIn('  host.pruneExtrasCache(new Set(entries.map((entry) => entry.name)));\n'
                      '  return summaries.filter((summary) => summary != null).sort(compareAgentSummaries);', patched)

    def test_missing_duplicate_unknown_and_drifted_components_are_refused(self):
        for source in (
                self.source.replace(START, 'async function listAgentsChanged(host, activeAgentId) {'),
                self.source.replace(END, 'async function summarizeAgentByIdChanged('),
                END + self.source.replace(END, ''),
                self.source + self.baseline,
                self.source + END,
                self.source.replace('includeBlank: false', 'includeBlank: true'),
                self.source.replace('extras: null', 'extras: {}'),
                self.source.replace('entry.isDirectory()', 'true'),
                self.source.replace('dbStats == null', 'dbStats === null')):
            with self.subTest(source=source[:80]):
                with self.assertRaises(RuntimeError):
                    m.patch(source)

    def test_marker_is_not_an_idempotence_bypass(self):
        patched = m.patch(self.source)
        for source in (
                self.source + '\n/* ' + m.MARK + ' */\n',
                self.source.replace(START, START + '\n  /* ' + m.MARK + ' */'),
                patched.replace(m.MARK, m.MARK + '-corrupted'),
                patched.replace('includeBlank: true', 'includeBlank: false'),
                patched.replace('extras: null', 'extras: {}'),
                patched + '\n/* ' + m.MARK + ' */\n'):
            with self.subTest(source=source[-100:]):
                with self.assertRaises(RuntimeError):
                    m.patch(source)

    def test_patch_file_parses_replaces_preserves_mode_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'synthetic-host.cjs'
            target.write_text(self.source)
            target.chmod(0o640)
            before = target.stat()
            self.assertTrue(m.patch_file(target))
            self.assertEqual(target.read_text(), m.patch(self.source))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertEqual((target.stat().st_uid, target.stat().st_gid),
                             (before.st_uid, before.st_gid))
            replaced_inode = target.stat().st_ino
            with patch.object(m.subprocess, 'run', side_effect=AssertionError('unexpected reparse')):
                self.assertFalse(m.patch_file(target))
            self.assertEqual(target.stat().st_ino, replaced_inode)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [target])

    def test_patch_file_syntax_refusal_preserves_target_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'synthetic-host.cjs'
            # The reviewed component is intact; the outer bundle deliberately
            # fails the real Node parser after the pure component transform.
            original = ('const syntaxIsBroken = ;\n' + self.source).encode()
            target.write_bytes(original)
            target.chmod(0o640)
            before = target.stat()
            with self.assertRaises(RuntimeError):
                m.patch_file(target)
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(target.stat().st_ino, before.st_ino)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [target])

    def test_patch_file_cas_preserves_concurrent_owner_edit_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'synthetic-host.cjs'
            target.write_text(self.source)
            concurrent = ('/* newer owner edit must win */\n' + self.source).encode()
            real_run = m.subprocess.run

            def edit_after_parse(*args, **kwargs):
                parsed = real_run(*args, **kwargs)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
                target.write_bytes(concurrent)
                return parsed

            with patch.object(m.subprocess, 'run', side_effect=edit_after_parse):
                with self.assertRaisesRegex(RuntimeError, 'changed before replacement'):
                    m.patch_file(target)
            self.assertEqual(target.read_bytes(), concurrent)
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [target])

    def test_patch_file_refuses_symlink_without_touching_its_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / 'owner-host.cjs'
            target.write_text(self.source)
            link = pathlib.Path(directory) / 'synthetic-host.cjs'
            link.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, 'regular file'):
                m.patch_file(link)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(), self.source)
            self.assertEqual(set(pathlib.Path(directory).iterdir()), {target, link})


if __name__ == '__main__':
    unittest.main()
