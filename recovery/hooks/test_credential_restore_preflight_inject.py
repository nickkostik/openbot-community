"""Synthetic behavior tests for the conservative credential restore preflight."""
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
import unittest
from unittest.mock import patch


HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    'credential_restore_preflight', HERE / 'credential-restore-preflight-inject.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
FIXTURE = HERE / 'fixtures' / 'credential-restore-synthetic.js'
PRODUCTION_STOCK_SHA256 = '90b3d219f6f52b1627da6a50bbb9e063638c898dceac80b8660246fcafe282f6'
PRODUCTION_PATCHED_SHA256 = '7f9a2a45ed5d4afae60a446e6d5ec4770274df5f156758a4939baf13ff64f4ae'
PRODUCTION_OWNER_PREFIX_SHA256 = 'b3a740e4e7e1ee9d5d5a8fd22ccdd7c8aade2e5ec7ccbb689f001efc539e3357'
SYNTHETIC_STOCK_SHA256 = 'c402225467fa88c12ed9b4ce8cc3506d58e60b2f6b3bfa79d2ee1209156447cc'
SYNTHETIC_PATCHED_SHA256 = 'c6cfe353e871ba3c6f9a59d4040e32d23ef280cddf2e75a80f418394f410b5e0'
SYNTHETIC_CURRENT_PATCHED_SHA256 = '34dcb6684d8bdbd47a11f5d9387b61a9c343076ed2ca500144c435bc0effb890'
SYNTHETIC_OWNER_PREFIX_SHA256 = hashlib.sha256(b'\n').hexdigest()


NODE_PREFLIGHT = r'''
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'credential-preflight-test-'));
const dir = path.join(root, request.scenario.nativeSafety ? 'test-agent' : 'agent'); fs.mkdirSync(dir);
const dbPath = path.join(dir, 'store.db');
const trace = {constructors:[], sql:[], parser:[], closes:0, iteration:0, all:0, heldAtClose:[], nativeSafety:[]};
const heldSets = [];
class HoldingSet extends Set { constructor(...args) { super(...args); heldSets.push(this); } }
let writer, changed = false;
const normal = id => ({id, kind:'send-message', message:{type:'text', text:'hello ' + id}});
try {
  if (!request.scenario.missing) {
    if (request.scenario.corrupt) fs.writeFileSync(dbPath, 'not a sqlite database');
    else {
      writer = new DatabaseSync(dbPath);
      if (request.scenario.wal) writer.exec('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;');
      if (request.scenario.schema === 'wrong-table') writer.exec('CREATE TABLE unrelated (value TEXT)');
      else if (request.scenario.schema === 'wrong-columns') writer.exec('CREATE TABLE transcript_entries (other TEXT)');
      else if (request.scenario.schema === 'view') writer.exec("CREATE VIEW transcript_entries AS SELECT 1 AS seq, 'id' AS id, '{}' AS entry");
      else {
        writer.exec('CREATE TABLE transcript_entries (seq INTEGER PRIMARY KEY, id TEXT, entry TEXT)');
        if (request.scenario.wal) writer.exec('PRAGMA wal_checkpoint(TRUNCATE)');
        const insert = writer.prepare('INSERT INTO transcript_entries(seq,id,entry) VALUES(?,?,?)');
        for (const row of request.scenario.rows || [])
          insert.run(row.seq, row.id, row.raw !== undefined ? row.raw : JSON.stringify(row.entry));
        if (request.scenario.trigger) writer.exec('CREATE TRIGGER test_metadata_trigger AFTER INSERT ON transcript_entries BEGIN SELECT 1; END');
      }
      if (!request.scenario.wal) { writer.close(); writer = null; }
    }
  }
  if (request.scenario.dbSymlink) {
    const actual = path.join(dir, 'actual.db'); fs.renameSync(dbPath, actual); fs.symlinkSync(actual, dbPath);
  }
  let probeDir = dir;
  if (request.scenario.dirSymlink) {
    probeDir = path.join(root, 'linked-agent'); fs.symlinkSync(dir, probeDir);
  }
  let mainOnlyCount = null;
  if (request.scenario.wal && request.scenario.schema === undefined) {
    const mainCopy = path.join(root, 'main-only.db'); fs.copyFileSync(dbPath, mainCopy);
    const mainDb = new DatabaseSync(mainCopy, {readOnly:true});
    mainOnlyCount = mainDb.prepare('SELECT COUNT(*) AS count FROM transcript_entries').get().count;
    mainDb.close();
  }
  const wrappedFs = new Proxy(fs, {get(target, key) {
    const value = target[key];
    if (typeof value !== 'function') return value;
    return (...args) => {
      if (request.scenario.fsFailure === key) throw Error('injected path uncertainty');
      return value.apply(target, args);
    };
  }});
  class ReadOnlyDatabase {
    constructor(file, options) {
      trace.constructors.push({file, options});
      if (request.scenario.openError) throw Error('injected SQL open failure');
      const real = new DatabaseSync(file, options);
      return new Proxy(real, {get(target, key) {
        if (key === 'close') return () => {
          trace.heldAtClose.push(heldSets.reduce((total, items) => total + items.size, 0));
          trace.closes++; target.close();
          if (request.scenario.closeError) throw Error('injected close uncertainty');
        };
        if (key === 'exec') return sql => {
          trace.sql.push(sql);
          if (request.scenario.sqlError) throw Error('injected SQL failure');
          return target.exec(sql);
        };
        if (key === 'prepare') return sql => {
          trace.sql.push(sql);
          if (request.scenario.sqlError) throw Error('injected SQL failure');
          const statement = target.prepare(sql);
          return new Proxy(statement, {get(stmt, method) {
            if (method === 'all') return (...args) => {
              if (/SELECT\s+seq\s*,\s*id\s*,\s*entry/i.test(sql)) {
                trace.all++;
                throw Error('unbounded row materialization');
              }
              return stmt.all(...args);
            };
            if (method === 'iterate') return (...args) => {
              trace.iteration++;
              if (request.scenario.iterationError) throw Error('injected cursor failure');
              return stmt.iterate(...args);
            };
            const property = stmt[method];
            return typeof property === 'function' ? property.bind(stmt) : property;
          }});
        };
        const value = target[key];
        return typeof value === 'function' ? value.bind(target) : value;
      }});
    }
  }
  const parseTranscriptEntry = raw => {
    if (typeof raw !== 'string') throw Error('native parser requires serialized JSON');
    const entry = JSON.parse(raw);
    trace.parser.push(entry?.id ?? null);
    if (request.scenario.parserError) throw Error('injected native parse failure');
    if (request.scenario.parserNull) return null;
    if (!entry || typeof entry !== 'object' || entry.kind !== 'send-message' ||
        !entry.message || !['text','credential-request'].includes(entry.message.type)) return null;
    if (!changed && request.scenario.changeDuringParse) {
      changed = true;
      if (request.scenario.changeDuringParse === 'wal') {
        writer.prepare('INSERT INTO transcript_entries(seq,id,entry) VALUES(?,?,?)')
          .run(999, 'concurrent', JSON.stringify(normal('concurrent')));
      } else if (request.scenario.changeDuringParse === 'inode') {
        const replacement = path.join(dir, 'replacement.db');
        fs.copyFileSync(dbPath, replacement); fs.renameSync(replacement, dbPath);
      } else {
        const now = new Date(Date.now() + 2000); fs.utimesSync(dbPath, now, now);
      }
    }
    return entry;
  };
  const wrappedRequire = name => {
    if (name === 'node:fs' || name === 'fs') return wrappedFs;
    if (name === 'node:path' || name === 'path') return path;
    if (name === 'node:sqlite') return {DatabaseSync:ReadOnlyDatabase};
    throw Error('unexpected dependency: ' + name);
  };
  const nativeId = id => {
    trace.nativeSafety.push('validate-id');
    if (request.scenario.nativeSafetyFailure === 'id') throw Error('native id refused');
    return id;
  };
  const nativeRefuse = file => {
    trace.nativeSafety.push('refuse-links');
    if (request.scenario.nativeSafetyFailure === 'links') throw Error('native links refused');
  };
  let nativeChecks = 0;
  const nativeCheck = (db, file) => {
    trace.nativeSafety.push('check-database');
    if (++nativeChecks === request.scenario.nativeSafetyFailure) throw Error('native database refused');
  };
  const probe = new Function('require', 'parseTranscriptEntry', 'Set',
    'assertValidSandAgentId', 'refuseSandDataDatabaseLinksSync', 'checkSandDataDatabaseSync',
    request.helper + '\nreturn openbotCredentialRestoreHasNoPendingEntries;')(
      wrappedRequire, parseTranscriptEntry, HoldingSet, nativeId, nativeRefuse, nativeCheck);
  const store = {getAgentDir: id => {
    if (id !== 'test-agent') throw Error('wrong getAgentDir identifier');
    if (request.scenario.getDirError) throw Error('getAgentDir failed');
    return request.scenario.dirValue !== undefined ? request.scenario.dirValue : probeDir;
  }};
  const readStoredRows = () => {
    const readonly = new DatabaseSync(dbPath, {readOnly:true});
    try { return readonly.prepare('SELECT seq,id,entry FROM transcript_entries ORDER BY seq').all(); }
    finally { readonly.close(); }
  };
  const storedBefore = request.scenario.verifyRows ? readStoredRows() : null;
  const result = probe(store, 'test-agent');
  process.stdout.write(JSON.stringify({result, trace, mainOnlyCount,
    storedBefore, storedAfter:request.scenario.verifyRows ? readStoredRows() : null,
    remainingStatements:heldSets.reduce((total, items) => total + items.size, 0),
    walExists:fs.existsSync(dbPath + '-wal')}));
} finally {
  if (writer) writer.close();
  fs.rmSync(root, {recursive:true, force:true});
}
'''


NODE_RESTORE = r'''
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
async function run(source) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'credential-restore-test-'));
  const agents = request.agents;
  const events = [], probes = [], pathChecks = [], failures = [], options = [];
  let yields = 0, sdkOpens = 0;
  const locations = new Map();
  const sessions = new Map();
  try {
    for (const agent of agents) {
      const directory = path.join(root, agent.id); fs.mkdirSync(directory); locations.set(agent.id, directory);
      if (!agent.missing) {
        const db = new DatabaseSync(path.join(directory, 'store.db'));
        if (agent.schemaError) db.exec('CREATE TABLE unrelated (id TEXT)');
        else {
          db.exec('CREATE TABLE transcript_entries(seq INTEGER PRIMARY KEY,id TEXT,entry TEXT)');
          const insert = db.prepare('INSERT INTO transcript_entries(seq,id,entry) VALUES(?,?,?)');
          for (const row of agent.rows || []) insert.run(row.seq, row.id,
            row.raw !== undefined ? row.raw : JSON.stringify(row.entry));
        }
        db.close();
      }
      const session = {
        getTranscript() {
          events.push('transcript:' + agent.id);
          return [...(agent.rows || [])].sort((a,b) => a.seq-b.seq).map(row =>
            row.raw !== undefined ? JSON.parse(row.raw) : row.entry);
        },
        dispose() { events.push('dispose:' + agent.id); }
      };
      sessions.set(agent.id, session);
    }
    const fakeFs = new Proxy(fs, {get(target, key) {
      const member = target[key];
      if (key !== 'realpathSync') return typeof member === 'function' ? member.bind(target) : member;
      return file => {
        const base = path.basename(file);
        pathChecks.push(base === 'store.db' ? path.basename(path.dirname(file)) : base);
        return target.realpathSync(file);
      };
    }});
    class ReadOnlyDatabase {
      constructor(file, settings) {
        const id = path.basename(path.dirname(file));
        probes.push(id); events.push('probe:' + id); options.push(settings);
        return new DatabaseSync(file, settings);
      }
    }
    const scopedRequire = name => {
      if (name === 'node:fs') return fakeFs;
      if (name === 'node:path') return path;
      if (name === 'node:sqlite') return {DatabaseSync:ReadOnlyDatabase};
      throw Error('unexpected dependency');
    };
    const parser = serialized => {
      const entry = JSON.parse(serialized);
      return entry && entry.kind === 'send-message' && entry.message &&
        ['text','credential-request'].includes(entry.message.type) ? entry : null;
    };
    const RestoreClass = new Function('require', 'parseTranscriptEntry', 'setImmediate',
      source + '\nreturn ' + request.className + ';')(
        scopedRequire, parser, callback => { yields++; setImmediate(callback); });
    const instance = new RestoreClass();
    instance.now = 100;
    instance.expire = (agent, id) => events.push('expired:' + agent + ':' + id);
    instance.restoreBrokered = (agent, id) => events.push('brokered:' + agent + ':' + id);
    instance.scheduleTimer = (agent, id) => events.push('timer:' + agent + ':' + id);
    instance.reportFailure = (agent, problem) => {
      failures.push({agent, name:problem.name}); events.push('failure:' + agent);
    };
    instance.tm = {sessionStore: {
      listAgentRecordIds: async () => agents.map(agent => agent.id),
      getAgentDir: id => {
        if (agents.find(agent => agent.id === id).getDirError) throw Error('getAgentDir failed');
        return locations.get(id);
      },
      openSession: async id => {
        sdkOpens++; events.push('open:' + id);
        if (agents.find(agent => agent.id === id).openError) throw Error('SDK open failure');
        return sessions.get(id);
      }
    }, liveSessions: {get: id => {
      events.push('lookup:' + id);
      return agents.find(agent => agent.id === id).live ? sessions.get(id) : undefined;
    }}};
    await instance.restoreCredentialRequestExpiryTimers();
    return {events, probes, pathChecks, failures, options, yields, sdkOpens};
  } finally { fs.rmSync(root, {recursive:true, force:true}); }
}
(async () => {
  const results = [];
  for (const source of request.sources) results.push(await run(source));
  process.stdout.write(JSON.stringify(results));
})().catch(error => { console.error(error); process.exitCode = 1; });
'''


def row(seq, *, pending=False, resolved=False, raw=None, kind='send-message'):
    value = {'seq': seq, 'id': f'row-{seq}'}
    if raw is not None:
        value['raw'] = raw
    else:
        value['entry'] = {'id': value['id'], 'kind': kind,
            'message': {'type': 'credential-request' if pending or resolved else 'text',
                        'text': 'synthetic message'}}
        if pending:
            value['entry']['credentialResolution'] = None
        if resolved:
            value['entry']['credentialResolution'] = {'status': 'resolved'}
    return value


def spend_row(seq):
    return {'seq':seq, 'id':f'row-{seq}', 'entry':{
        'id':f'row-{seq}', 'kind':'spend-initiation',
        'metadata':{'synthetic':True, 'reference':'unchanged-spend-metadata'}}}


def dependency_fixture():
    read = 'function syntheticRead() { return "read-v1"; }\n'
    end = 'function syntheticEnd() { return "end"; }\n'
    owner = 'var SyntheticStore = class {'
    initialize = '  initialize() { return "init-v1"; }\n'
    last = '  open() { return "open-v1"; }'
    source = read + end + owner + '\n' + initialize + last + '\n};\n'
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    contracts = (
        ('test-read', 'function syntheticRead(', 'function syntheticEnd(', None, (digest(read),)),
        ('test-init', '  initialize() {', '  open() {', owner, (digest(initialize),)),
        ('test-open', '  open() {', None, owner, (digest(last),)),
    )
    return source, contracts


@unittest.skipUnless(shutil.which('node'), 'Node with node:sqlite is required')
class CredentialRestorePreflightTests(unittest.TestCase):
    helper = m.READONLY_PREFLIGHT
    native_safety = False

    def probe(self, scenario):
        result = subprocess.run(['node', '--no-warnings', '-e', NODE_PREFLIGHT],
            input=json.dumps({'helper': self.helper, 'scenario': {
                'nativeSafety': self.native_safety, **scenario}}),
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['trace']['all'], 0)
        self.assertEqual(output['remainingStatements'], 0)
        for call in output['trace']['constructors']:
            self.assertIs(call['options'].get('readOnly'), True)
        return output

    def test_empty_normal_and_resolved_only_are_safe_to_skip(self):
        for rows in ([], [row(1)], [row(1, resolved=True)], [row(1), row(2, resolved=True)]):
            with self.subTest(rows=rows):
                output = self.probe({'rows': rows})
                self.assertTrue(output['result'])
                self.assertEqual(output['trace']['closes'], 1)
                self.assertEqual(output['trace']['parser'], [item['id'] for item in rows])
                self.assertEqual(output['trace']['iteration'], 1)
                self.assertEqual(output['trace']['heldAtClose'], [1])
                self.assertTrue(any(part.strip().upper() == 'BEGIN'
                                    for sql in output['trace']['sql'] for part in sql.split(';')))

    def test_unresolved_credentials_force_original_sdk_path(self):
        for rows in ([row(1, pending=True)], [row(1), row(2, pending=True)],
                     [row(1, resolved=True), row(2, pending=True)]):
            with self.subTest(rows=rows):
                self.assertFalse(self.probe({'rows': rows})['result'])

    def test_wal_pending_entry_is_seen_when_main_database_has_no_rows(self):
        output = self.probe({'wal': True, 'rows': [row(1, pending=True)]})
        self.assertEqual(output['mainOnlyCount'], 0)
        self.assertTrue(output['walExists'])
        self.assertFalse(output['result'])
        self.assertEqual(output['trace']['parser'], ['row-1'])

    def test_native_ignored_spend_metadata_continues_to_later_pending_credential(self):
        rows = [spend_row(1), row(2), row(3, pending=True)]
        output = self.probe({'rows':rows, 'verifyRows':True})
        self.assertFalse(output['result'])
        self.assertEqual(output['trace']['parser'], ['row-1', 'row-2', 'row-3'])
        self.assertEqual(output['storedBefore'], output['storedAfter'])

    def test_native_ignored_spend_only_and_resolved_rows_skip_without_mutating_storage(self):
        for rows in ([spend_row(1)], [spend_row(1), spend_row(2), row(3, resolved=True)]):
            with self.subTest(rows=rows):
                output = self.probe({'rows':rows, 'verifyRows':True})
                self.assertTrue(output['result'])
                self.assertEqual(output['trace']['parser'], [item['id'] for item in rows])
                self.assertEqual(output['storedBefore'], output['storedAfter'])

    def test_other_null_or_malformed_spend_metadata_still_falls_back(self):
        mismatched = spend_row(1)
        mismatched['entry']['id'] = 'different-id'
        for rows in ([mismatched], [row(1, kind='other-ignored-kind')],
                     [row(1, raw='{"kind":"spend-initiation"')],
                     [row(1, raw='["spend-initiation"]')]):
            with self.subTest(rows=rows):
                self.assertFalse(self.probe({'rows':rows})['result'])
        self.assertFalse(self.probe({'rows':[spend_row(1)], 'parserError':True})['result'])

    def test_native_parser_is_called_in_sql_sequence_order(self):
        output = self.probe({'rows': [row(8), row(2, resolved=True), row(5)]})
        self.assertTrue(output['result'])
        self.assertEqual(output['trace']['parser'], ['row-2', 'row-5', 'row-8'])

    def test_malformed_unknown_and_parser_uncertainty_fall_back(self):
        for scenario in ({'rows': [row(1, raw='{bad json')]},
                         {'rows': [row(1, raw='null')]},
                         {'rows': [row(1, kind='unknown-kind')]},
                         {'rows': [row(1)], 'parserError': True},
                         {'rows': [row(1)], 'parserNull': True}):
            with self.subTest(scenario=scenario):
                self.assertFalse(self.probe(scenario)['result'])

    def test_row_identity_type_and_unsafe_sequence_uncertainty_fall_back(self):
        mismatched = row(1)
        mismatched['entry']['id'] = 'different-entry-id'
        missing_id = row(1)
        missing_id['id'] = None
        for value in (mismatched, missing_id, row(9007199254740992)):
            with self.subTest(row=value):
                self.assertFalse(self.probe({'rows': [value]})['result'])

    def test_schema_sql_open_and_close_uncertainty_fall_back(self):
        for scenario in ({'schema': 'wrong-table'}, {'schema': 'wrong-columns'},
                         {'schema': 'view'}, {'trigger': True},
                         {'corrupt': True}, {'openError': True}, {'sqlError': True}, {'iterationError': True},
                         {'closeError': True}):
            with self.subTest(scenario=scenario):
                self.assertFalse(self.probe(scenario)['result'])

    def test_missing_symlink_and_path_uncertainty_fall_back(self):
        for scenario in ({'missing': True}, {'dbSymlink': True}, {'dirSymlink': True},
                         {'fsFailure': 'lstatSync'}, {'fsFailure': 'realpathSync'},
                         {'getDirError': True}, {'dirValue': 'relative/agent'}, {'dirValue': None}):
            with self.subTest(scenario=scenario):
                self.assertFalse(self.probe(scenario)['result'])

    def test_database_or_wal_change_during_scan_falls_back(self):
        for change in ('mtime', 'inode', 'wal'):
            with self.subTest(change=change):
                scenario = {'rows': [row(1), row(2)], 'changeDuringParse': change}
                if change == 'wal':
                    scenario['wal'] = True
                self.assertFalse(self.probe(scenario)['result'])


@unittest.skipUnless(shutil.which('node'), 'Node with node:sqlite is required')
class CurrentCredentialRestorePreflightTests(CredentialRestorePreflightTests):
    # Re-run the full absence/uncertainty/WAL suite against the guarded helper.
    helper = m.CURRENT_READONLY_PREFLIGHT
    native_safety = True

    def test_native_safety_is_required_before_open_and_after_the_complete_scan(self):
        output = self.probe({'rows': [row(1)], 'verifyRows': True})
        self.assertTrue(output['result'])
        self.assertEqual(output['trace']['nativeSafety'], [
            'validate-id', 'refuse-links', 'check-database', 'check-database'])
        self.assertEqual(output['storedBefore'], output['storedAfter'])

    def test_native_safety_refusal_never_becomes_an_absence_proof(self):
        for failure, opens in [('id', 0), ('links', 0), (1, 1), (2, 1)]:
            with self.subTest(failure=failure):
                output = self.probe({'rows': [row(1)], 'nativeSafetyFailure': failure})
                self.assertFalse(output['result'])
                self.assertEqual(len(output['trace']['constructors']), opens)
                self.assertEqual(output['trace']['closes'], opens)

    def test_valid_id_cannot_scan_a_different_agent_directory(self):
        output = self.probe({'rows': [], 'nativeSafety': False})
        self.assertFalse(output['result'])
        self.assertEqual(output['trace']['constructors'], [])


@unittest.skipUnless(shutil.which('node'), 'Node with node:sqlite is required')
class CredentialRestoreTransformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FIXTURE.read_text()
        cls.production_stock = m.STOCK_SHA256
        cls.production_patched = m.PATCHED_SHA256
        cls.production_owner_prefix = m.OWNER_PREFIX_SHA256
        cls.production_dependencies = m.DEPENDENCY_CONTRACTS

    def setUp(self):
        reviewed = patch.multiple(m, STOCK_SHA256=SYNTHETIC_STOCK_SHA256,
                                  PATCHED_SHA256=SYNTHETIC_PATCHED_SHA256,
                                  OWNER_PREFIX_SHA256=SYNTHETIC_OWNER_PREFIX_SHA256,
                                  DEPENDENCY_CONTRACTS=())
        reviewed.start()
        self.addCleanup(reviewed.stop)

    def run_restore(self, agents, source=None, class_name='WidgetResponses'):
        before = source or self.source
        result = subprocess.run(['node', '--no-warnings', '-e', NODE_RESTORE],
            input=json.dumps({'sources': [before, m.patch(before)],
                             'agents': agents, 'className': class_name}),
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        original, patched = json.loads(result.stdout)
        for settings in patched['options']:
            self.assertIs(settings.get('readOnly'), True)
        return original, patched

    def test_production_hashes_are_pinned_and_refuse_synthetic_component(self):
        self.assertEqual(self.production_stock, PRODUCTION_STOCK_SHA256)
        self.assertEqual(self.production_patched, PRODUCTION_PATCHED_SHA256)
        self.assertEqual(self.production_owner_prefix, PRODUCTION_OWNER_PREFIX_SHA256)
        self.assertEqual([contract[0] for contract in self.production_dependencies], [
            'schema-and-prepared-statements', 'database-open-initialization', 'database-constructor',
            'transcript-getter', 'transcript-row-parser', 'conversation-rebuild',
            'session-maintenance', 'session-open'])
        for name, start, end, owner, hashes in self.production_dependencies:
            self.assertTrue(start, name)
            self.assertTrue(hashes, name)
            for fingerprint in hashes:
                self.assertEqual(len(fingerprint), 64, name)
                int(fingerprint, 16)
        start = self.source.index(m.START)
        component = self.source[start:self.source.index(m.END, start)]
        self.assertEqual(hashlib.sha256(component.encode()).hexdigest(), SYNTHETIC_STOCK_SHA256)
        self.assertIn('Test-owned synthetic restore flow', self.source)
        with patch.multiple(m, STOCK_SHA256=self.production_stock, PATCHED_SHA256=self.production_patched,
                            OWNER_PREFIX_SHA256=self.production_owner_prefix,
                            DEPENDENCY_CONTRACTS=self.production_dependencies):
            with self.assertRaises(RuntimeError):
                m.patch(self.source)

    def test_only_preflight_and_cold_guard_are_added_and_exact_patch_is_idempotent(self):
        before = '/* unrelated prefix */\n' + self.source + '/* unrelated suffix */\n'
        after = m.patch(before)
        self.assertEqual(after.replace(m.READONLY_PREFLIGHT, '', 1).replace(
            m.COLD_REPLACEMENT, m.COLD_ANCHOR, 1), before)
        self.assertEqual(m.patch(after), after)
        self.assertEqual(after.count(m.MARK), 1)
        self.assertIn(m.COLD_ANCHOR, after)
        self.assertTrue(after.endswith('/* unrelated suffix */\n'))

    def test_empty_normal_and_resolved_cold_agents_do_not_open_sdk_sessions(self):
        agents = [{'id':'empty', 'rows':[]}, {'id':'normal', 'rows':[row(1)]},
                  {'id':'resolved', 'rows':[row(1, resolved=True)]}]
        original, after = self.run_restore(agents)
        self.assertEqual(original['sdkOpens'], 3)
        self.assertEqual(after['sdkOpens'], 0)
        self.assertEqual(after['probes'], ['empty', 'normal', 'resolved'])
        self.assertEqual(after['yields'], 3)
        self.assertFalse(any(item.startswith('dispose:') for item in after['events']))
        self.assertEqual(after['failures'], [])

    def test_live_sessions_never_run_preflight_or_dispose_and_keep_original_handlers(self):
        pending = row(1, pending=True)
        pending['entry']['message'].update(expiresAt=200, brokered=False)
        original, after = self.run_restore([{'id':'live', 'rows':[pending], 'live':True, 'missing':True}])
        self.assertEqual(after, original)
        self.assertEqual(after['pathChecks'], [])
        self.assertEqual(after['probes'], [])
        self.assertEqual(after['sdkOpens'], 0)
        self.assertEqual(after['yields'], 0)
        self.assertEqual(after['events'], ['lookup:live', 'transcript:live', 'timer:live:row-1'])

    def test_expired_brokered_and_future_candidates_use_original_handlers_in_order(self):
        expired, brokered, future = (row(index, pending=True) for index in (3, 1, 2))
        expired['entry']['message'].update(expiresAt=50, brokered=False)
        brokered['entry']['message'].update(expiresAt=200, brokered=True)
        future['entry']['message'].update(expiresAt=200, brokered=False)
        original, after = self.run_restore([{'id':'pending', 'rows':[expired, brokered, future]}])
        self.assertEqual([event for event in after['events'] if not event.startswith('probe:')],
                         original['events'])
        self.assertEqual(after['sdkOpens'], original['sdkOpens'])
        self.assertEqual(after['events'], ['lookup:pending', 'probe:pending', 'open:pending',
            'transcript:pending', 'brokered:pending:row-1', 'timer:pending:row-2',
            'expired:pending:row-3', 'dispose:pending'])
        self.assertEqual(after['yields'], 0)

    def test_ignored_spend_before_candidate_preserves_original_handler_and_metadata(self):
        pending = row(3, pending=True)
        pending['entry']['message'].update(expiresAt=200, brokered=True)
        original, after = self.run_restore([{'id':'pending', 'rows':[spend_row(1), row(2), pending]}])
        self.assertEqual([event for event in after['events'] if not event.startswith('probe:')],
                         original['events'])
        self.assertEqual(after['sdkOpens'], 1)
        self.assertIn('brokered:pending:row-3', after['events'])
        self.assertEqual(after['yields'], 0)

    def test_owner_prefix_drift_is_rejected_before_and_after_patch(self):
        for source in (self.source, m.patch(self.source)):
            drifted = source.replace(m.NATIVE_CLASS, m.NATIVE_CLASS + '\n  unexpectedOwnerField = true;', 1)
            with self.assertRaisesRegex(RuntimeError, 'owner prefix'):
                m.patch(drifted)

    def test_dependencies_are_validated_before_new_patch_and_idempotent_return(self):
        dependencies, contracts = dependency_fixture()
        source = dependencies + self.source
        with patch.object(m, 'DEPENDENCY_CONTRACTS', contracts):
            updated = m.patch(source)
            self.assertEqual(m.patch(updated), updated)
            for before in (source, updated):
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed.*test-read'):
                    m.patch(before.replace('read-v1', 'unreviewed-read'))

    def test_uncertainty_and_throwing_directory_lookup_keep_original_sdk_and_disposal(self):
        pending = row(1, pending=True)
        pending['entry']['message'].update(expiresAt=200, brokered=False)
        scenarios = [
            {'id':'missing', 'missing':True, 'rows':[pending]},
            {'id':'schema', 'schemaError':True, 'rows':[pending]},
            {'id':'directory-error', 'getDirError':True, 'rows':[pending]},
            {'id':'unknown', 'rows':[row(1, kind='unknown-kind')]},
            {'id':'malformed', 'rows':[row(1, raw='{invalid')]},
            {'id':'sdk-error', 'openError':True, 'rows':[pending]},
        ]
        for agent in scenarios:
            with self.subTest(agent=agent['id']):
                original, after = self.run_restore([agent])
                self.assertEqual([event for event in after['events'] if not event.startswith('probe:')],
                                 original['events'])
                self.assertEqual(after['failures'], original['failures'])
                self.assertEqual(after['sdkOpens'], 1)
                self.assertEqual(after['yields'], 0)

    def test_retained_class_is_selected_without_changing_native_class(self):
        retained = self.source.replace(m.NATIVE_CLASS, m.RETAINED_CLASS, 1)
        composed = self.source + retained
        after = m.patch(composed)
        self.assertTrue(after.startswith(self.source))
        self.assertEqual(after.count(m.MARK), 1)
        self.assertEqual(m.patch(after), after)
        original, result = self.run_restore([{'id':'empty','rows':[]}], source=composed,
                                            class_name='__openbot_box_WidgetResponses')
        self.assertEqual(original['sdkOpens'], 1)
        self.assertEqual(result['sdkOpens'], 0)

    def test_unknown_duplicate_and_partial_components_or_markers_are_refused(self):
        after = m.patch(self.source)
        variants = [
            self.source.replace(m.NATIVE_CLASS, 'var OtherResponses = class {'),
            self.source + self.source,
            self.source.replace(m.START, '  async unknownRestore() {'),
            self.source.replace(m.END, '  anotherCancel('),
            self.source.replace('this.scheduleTimer(agentId, item.id)', 'this.expire(agentId, item.id)'),
            self.source + '\n/* ' + m.MARK + ' */\n',
            after + '\n/* ' + m.MARK + ' */\n',
            after.replace(m.MARK, m.MARK + '-corrupted'),
            after.replace('liveSession == null && openbotCredentialRestoreHasNoPendingEntries',
                          'liveSession != null && openbotCredentialRestoreHasNoPendingEntries'),
            after.replace('verifiedEmpty = false;', 'verifiedEmpty = true;', 1),
        ]
        for value in variants:
            with self.subTest(source=value[:90]), self.assertRaises(RuntimeError):
                m.patch(value)

    def test_unreviewed_transformation_hash_is_refused(self):
        with patch.object(m, 'PATCHED_SHA256', '0' * 64):
            with self.assertRaises(RuntimeError):
                m.patch(self.source)

    def current_layout(self, contracts):
        return patch.multiple(m, CURRENT_SAFETY_ANCHOR='function syntheticRead(',
                              CURRENT_SAFETY_FUNCTIONS=('syntheticRead',),
                              CURRENT_DEPENDENCY_CONTRACTS=contracts,
                              CURRENT_PATCHED_SHA256=SYNTHETIC_CURRENT_PATCHED_SHA256)

    def test_current_layout_upgrades_exact_legacy_preflight_and_is_a_fixed_point(self):
        dependencies, contracts = dependency_fixture()
        old = m.patch(self.source)
        with self.current_layout(contracts):
            fresh = m.patch(dependencies + self.source)
            upgraded = m.patch(dependencies + old)
            self.assertEqual(fresh, upgraded)
            self.assertEqual(m.patch(upgraded), upgraded)
            self.assertNotIn(m.READONLY_PREFLIGHT, upgraded)
            self.assertEqual(upgraded.count(m.CURRENT_READONLY_PREFLIGHT), 1)
            self.assertEqual(upgraded.replace(m.CURRENT_READONLY_PREFLIGHT, m.READONLY_PREFLIGHT),
                             dependencies + old)

    def test_current_layout_cannot_skip_dependencies_or_accept_changed_prior_loop(self):
        dependencies, contracts = dependency_fixture()
        legacy = m.patch(self.source)
        with self.current_layout(contracts):
            current = m.patch(dependencies + self.source)
            self.assertEqual(m.patch(self.source), legacy)
            self.assertEqual(m.patch(legacy), legacy)
            for source in ((dependencies + self.source).replace('var SyntheticStore', 'var UnreviewedStore'),
                           current.removeprefix(dependencies),
                           (dependencies + legacy).replace('verifiedEmpty = false;', 'verifiedEmpty = true;', 1),
                           current.replace('        checkSandDataDatabaseSync(db, dbPath);', '', 1)):
                with self.subTest(source=source[:80]), self.assertRaises(RuntimeError):
                    m.patch(source)
            for before in (dependencies + self.source, dependencies + legacy, current):
                with self.assertRaisesRegex(RuntimeError, 'Unreviewed.*test-read'):
                    m.patch(before.replace('read-v1', 'unreviewed-read'))
                with self.assertRaisesRegex(RuntimeError, 'Ambiguous native credential safety'):
                    m.patch(before + '\nfunction syntheticRead (ignored) {}\n')

    def test_current_layout_checks_output_digest_for_both_fresh_and_upgrade_paths(self):
        dependencies, contracts = dependency_fixture()
        legacy = m.patch(self.source)
        with self.current_layout(contracts), patch.object(m, 'CURRENT_PATCHED_SHA256', '0' * 64):
            for source in (self.source, legacy):
                with self.subTest(source=source[:80]), self.assertRaises(RuntimeError):
                    m.patch(dependencies + source)


class CredentialRestoreDependencyGuardTests(unittest.TestCase):
    def test_current_native_safety_contract_set_is_separate_and_pinned(self):
        legacy = dict((row[0], row[4]) for row in m.DEPENDENCY_CONTRACTS)
        current = dict((row[0], row[4]) for row in m.CURRENT_DEPENDENCY_CONTRACTS)
        self.assertEqual(set(current) - set(legacy), {
            'native-no-follow-walk', 'native-data-file-safety',
            'native-agent-id-validation', 'native-agent-id-parser'})
        for name, hashes in current.items():
            self.assertTrue(hashes)
            for value in hashes:
                self.assertEqual(len(value), 64)
                int(value, 16)
            if name in m.CURRENT_DEPENDENCY_HASHES:
                self.assertTrue(set(hashes).isdisjoint(legacy[name]))

    def test_scoped_and_last_member_dependencies_extract_exact_synthetic_bytes(self):
        source, contracts = dependency_fixture()
        expected = ['function syntheticRead() { return "read-v1"; }\n',
                    '  initialize() { return "init-v1"; }\n',
                    '  open() { return "open-v1"; }']
        for contract, component in zip(contracts, expected):
            name, start, end, owner, hashes = contract
            self.assertEqual(m.dependency_component(source, start, end, owner), component, name)
        with patch.object(m, 'DEPENDENCY_CONTRACTS', contracts):
            m.check_dependencies(source)
            # Identical method names outside the selected owner cannot change it.
            m.check_dependencies(source + 'var OtherStore = class {\n' + expected[1] + expected[2] + '\n};\n')

    def test_dependency_fingerprints_reject_each_mutated_synthetic_component(self):
        source, contracts = dependency_fixture()
        with patch.object(m, 'DEPENDENCY_CONTRACTS', contracts):
            for old in ('read-v1', 'init-v1', 'open-v1'):
                with self.subTest(component=old), self.assertRaisesRegex(RuntimeError, 'Unreviewed'):
                    m.check_dependencies(source.replace(old, 'unreviewed-version'))

    def test_duplicate_missing_reordered_and_unterminated_dependency_anchors_refuse(self):
        source, contracts = dependency_fixture()
        read, initialize, last = contracts
        variants = (
            (source + 'function syntheticRead(', read),
            (source.replace('function syntheticRead(', 'function unknownRead('), read),
            (source.replace('function syntheticRead() { return "read-v1"; }\n' +
                            'function syntheticEnd() { return "end"; }\n',
                            'function syntheticEnd() { return "end"; }\n' +
                            'function syntheticRead() { return "read-v1"; }\n'), read),
            (source + initialize[3], initialize),
            (source.replace('\n};', ''), last),
            (source.replace('  open() {', '  unknownOpen() {'), last),
        )
        for value, contract in variants:
            _, start, end, owner, _ = contract
            with self.subTest(source=value), self.assertRaises(RuntimeError):
                m.dependency_component(value, start, end, owner)


if __name__ == '__main__':
    unittest.main()
