"""Avoid cold SDK opens only after a conservative read-only credential scan."""
import hashlib
import pathlib
import sys

MARK = 'openbot-credential-restore-preflight'
NATIVE_CLASS = 'var WidgetResponses = class {'
RETAINED_CLASS = 'var __openbot_box_WidgetResponses = class {'
START = '  async restoreCredentialRequestExpiryTimers() {'
END = '  cancelCredentialRequestExpiry('
STOCK_SHA256 = '90b3d219f6f52b1627da6a50bbb9e063638c898dceac80b8660246fcafe282f6'
OWNER_PREFIX_SHA256 = 'b3a740e4e7e1ee9d5d5a8fd22ccdd7c8aade2e5ec7ccbb689f001efc539e3357'
PATCHED_SHA256 = '7f9a2a45ed5d4afae60a446e6d5ec4770274df5f156758a4939baf13ff64f4ae'

# Read the same complete ordered metadata as the native restore path, one row
# at a time. Unknown rows and concurrent database/WAL changes cannot prove absence.
# No SandAgentDb/AgentStore is constructed and no credential action occurs here.
READONLY_PREFLIGHT = '''    const openbotCredentialPreflightStatements = new Set();
    const openbotCredentialRestoreHasNoPendingEntries = (sessionStore, agentId) => {
      /* openbot-credential-restore-preflight */
      let db;
      let statement;
      let verifiedEmpty = false;
      try {
        const fs = require("node:fs");
        const path = require("node:path");
        const agentDir = sessionStore.getAgentDir(agentId);
        if (typeof agentDir !== "string" || !path.isAbsolute(agentDir)) return false;
        const directory = path.resolve(agentDir);
        if (fs.realpathSync(directory) !== directory || !fs.lstatSync(directory).isDirectory()) return false;
        const dbPath = path.join(directory, "store.db");
        const fingerprint = (file) => {
          try {
            const info = fs.lstatSync(file, { bigint: true });
            if (!info.isFile() || info.isSymbolicLink()) throw new Error("Uncertain credential metadata path");
            return [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs].map(String).join(":");
          } catch (error) {
            if (error.code === "ENOENT") return null;
            throw error;
          }
        };
        const before = [dbPath, dbPath + "-wal", dbPath + "-journal"].map(fingerprint);
        if (before[0] === null || fs.realpathSync(dbPath) !== dbPath) return false;
        db = new (require("node:sqlite").DatabaseSync)(dbPath, { readOnly: true });
        db.exec("PRAGMA query_only = ON; PRAGMA busy_timeout = 250; BEGIN");
        const table = db.prepare("SELECT type FROM sqlite_master WHERE name = 'transcript_entries'").all();
        const columns = db.prepare("PRAGMA table_info(transcript_entries)").all();
        const triggers = db.prepare("SELECT name FROM sqlite_master WHERE type = 'trigger' LIMIT 1").all();
        if (table.length !== 1 || table[0].type !== "table" || columns.length !== 3 ||
            columns.some((column, index) => column.name !== ["seq", "id", "entry"][index] ||
              column.type.toUpperCase() !== ["INTEGER", "TEXT", "TEXT"][index]) || columns[0].pk !== 1 || triggers.length !== 0) {
          throw new Error("Uncertain credential metadata schema");
        }
        let noPending = true;
        let previousSeq = -Infinity;
        statement = db.prepare("SELECT seq, id, entry FROM transcript_entries ORDER BY seq");
        openbotCredentialPreflightStatements.add(statement);
        for (const row of statement.iterate()) {
          if (typeof row.seq !== "number" || !Number.isSafeInteger(row.seq) || row.seq <= previousSeq ||
              typeof row.id !== "string" || typeof row.entry !== "string") { noPending = false; break; }
          previousSeq = row.seq;
          const entry = parseTranscriptEntry(row.entry);
          if (entry == null) {
            const ignored = JSON.parse(row.entry);
            if (ignored == null || typeof ignored !== "object" || Array.isArray(ignored) ||
                ignored.kind !== "spend-initiation" || ignored.id !== row.id) { noPending = false; break; }
            // The native getter omits these metadata rows; keep their stored bytes untouched.
            continue;
          }
          if (entry.id !== row.id) { noPending = false; break; }
          if (entry.kind === "send-message" && entry.message.type === "credential-request" &&
              entry.credentialResolution == null) { noPending = false; break; }
        }
        db.exec("COMMIT");
        const after = [dbPath, dbPath + "-wal", dbPath + "-journal"].map(fingerprint);
        verifiedEmpty = noPending && before.every((value, index) => value === after[index]);
      } catch {
        // Uncertainty retains the complete native session/recovery/timer path.
      } finally {
        try { db?.close(); } catch { verifiedEmpty = false; }
        if (statement !== void 0) openbotCredentialPreflightStatements.delete(statement);
      }
      return verifiedEmpty;
    };
'''
LOOP_ANCHOR = '    for (const agentId of agentIds) {'
COLD_ANCHOR = '      let session;\n      try {\n        session = liveSession ?? await this.tm.sessionStore.openSession(agentId);'
COLD_REPLACEMENT = '''      if (liveSession == null && openbotCredentialRestoreHasNoPendingEntries(this.tm.sessionStore, agentId)) {
        await new Promise((resolve) => setImmediate(resolve));
        continue;
      }
''' + COLD_ANCHOR


def digest(source):
    return hashlib.sha256(source.encode('utf8')).hexdigest()


# These small native read/initialization/reconstruction components are the
# absence proof's dependencies. Their reviewed variants must also stay fixed:
# an unchanged restore loop cannot bless a new transcript migration elsewhere.
DEPENDENCY_CONTRACTS = (('schema-and-prepared-statements',
  'var SCHEMA = `',
  '// src/host/extensions/session/session-diagnostics.ts',
  None,
  ('9b55204c2f93fb57f5edff634a7bfcef41e65671d5d12a5c1b10e3565545de6f',)),
 ('database-open-initialization',
  'function openConfiguredDb(',
  'function recoverCorruptStoreDb(',
  None,
  ('508b3ba1d9957a96e3504203ba7c8dd8ec95cf23d3d51a4afd4e886ecc18c740',)),
 ('database-constructor',
  '  constructor(dbPath, options2 = {}) {',
  '  get isOpen() {',
  'var SandAgentDb = class {',
  ('2b764d15b6c132355c3c1dc0cc18c38b21a5db28bc0f37d9a813d7c2077fe016',
   'f5a98d6a3dfa0fc90057619d474e94b22a3478ff26e48dc1237b2c1b464e329f')),
 ('transcript-getter',
  '  getTranscriptEntries() {',
  '  getTranscriptEntryIds() {',
  'var SandAgentDb = class {',
  ('547d6f37adca27330e3892751300a6cff28da32819685c4c7a689900e8b9255e',)),
 ('transcript-row-parser',
  'function parseEntryRows(rows) {',
  'var SandCheckpointPublicationError',
  None,
  ('0c8afbb612f3ce4944c3926c383a962742e1c965e92295278b4074aeccd17c82',)),
 ('conversation-rebuild',
  'function sendMessageFromToolCall(',
  'function selectHiddenArtifactEntryIds(',
  None,
  ('e5ee1be729b957fcfb8886e0c161d5e76f05cda6f1907ebfe81053f6b99ed0eb',
   'f4efd397af8fbeeee20a98a55ec62d474973dd83406bf42bfb03b05816dd2ff3')),
 ('session-maintenance',
  'async function clearStaleCheckpointRootsOnce(',
  'async function cleanupLegacyGroupMemberDirs(',
  None,
  ('a2347f4b591d52c074c2ee1bd184adf58193856aa633eb31a2be86341a2c81d6',
   'a3fde53a68b2daf5f1f7d8f477485e99dc3b2cefe8ac613082934c9a34895b69')),
 ('session-open',
  '  async openSession(agentId) {',
  None,
  'var SandSessionMaterialization = class {',
  ('c66462bbcf66734ec3e65445c3670dc6f4bb555d40ee54e09155ee3b6a6c6921',
   'ccdb49e0fd7001fbd529d6a76094d1b1e83ba3a117d0fcd67d3c42d1c201d538')))


def dependency_component(source, start, end, owner=None):
    scope = source
    if owner is not None:
        if source.count(owner) != 1:
            raise RuntimeError('Ambiguous credential preflight dependency owner')
        begin = source.index(owner) + len(owner)
        try:
            finish = source.index('\n};', begin)
        except ValueError as error:
            raise RuntimeError('Missing credential preflight dependency class boundary') from error
        scope = source[begin:finish]
    if scope.count(start) != 1 or (end is not None and scope.count(end) != 1):
        raise RuntimeError('Ambiguous credential preflight dependency anchors')
    begin, finish = scope.index(start), scope.index(end) if end is not None else len(scope)
    if finish <= begin:
        raise RuntimeError('Reordered credential preflight dependency anchors')
    return scope[begin:finish]


def check_dependencies(source):
    for name, start, end, owner, reviewed in DEPENDENCY_CONTRACTS:
        if digest(dependency_component(source, start, end, owner)) not in reviewed:
            raise RuntimeError('Unreviewed credential preflight dependency: ' + name)


def patch(source):
    """Select the active native/retained class and reject unfamiliar components."""
    check_dependencies(source)
    if source.count(NATIVE_CLASS) != 1 or source.count(RETAINED_CLASS) > 1:
        raise RuntimeError('Expected the reviewed credential-response class composition')
    active = RETAINED_CLASS if RETAINED_CLASS in source else NATIVE_CLASS
    owner = source.index(active)
    if source.count(START) != (2 if active == RETAINED_CLASS else 1) or source.count(END) != source.count(START):
        raise RuntimeError('Ambiguous credential restore component anchors')
    try:
        start = source.index(START, owner)
        end = source.index(END, start)
    except ValueError as error:
        raise RuntimeError('Credential restore anchors escaped the active class') from error
    if digest(source[owner + len(active):start]) != OWNER_PREFIX_SHA256:
        raise RuntimeError('Credential restore owner prefix differs from the reviewed class')
    component = source[start:end]
    fingerprint = digest(component)
    if MARK in source:
        if source.count(MARK) != 1 or fingerprint != PATCHED_SHA256:
            raise RuntimeError('Modified or incomplete credential restore preflight')
        return source
    if fingerprint != STOCK_SHA256 or component.count(LOOP_ANCHOR) != 1 or component.count(COLD_ANCHOR) != 1:
        raise RuntimeError('Credential restore component differs from the reviewed source')
    updated = component.replace(LOOP_ANCHOR, READONLY_PREFLIGHT + LOOP_ANCHOR, 1).replace(COLD_ANCHOR, COLD_REPLACEMENT, 1)
    if digest(updated) != PATCHED_SHA256:
        raise RuntimeError('Credential restore preflight differs from the reviewed transformation')
    return source[:start] + updated + source[end:]


if __name__ == '__main__':
    for argument in sys.argv[1:]:
        path = pathlib.Path(argument)
        result = patch(path.read_text(encoding='utf8'))
        # Canonical recovery owns publication; this command emits a candidate.
        sys.stdout.write(result)
