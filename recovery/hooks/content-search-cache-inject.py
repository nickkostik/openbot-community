"""Select an optional reviewed sidecar with bounded search metadata connections."""
import hashlib
import pathlib
import sys

WORKER_SOURCE_SHA = '8b97f8fbe2a57679c4ebbe8d10912ed175dd02aa30f7b47842cff4d8b45c9819'
WORKER_PATCHED_SHA = 'f8a1274b7c1bb191789ac988bdbf7b18d4e3604039d123d8b2907fb9e1e06a95'
WORKER_COMPONENT_SHA = 'bf0618789744f47565ee6da6a0f56eb32f7b3c517d1f6d5842d158e94e19814e'
WORKER_PATCHED_COMPONENT_SHA = 'bb01e005f867bc291158b306506dfa16968c5de4051834fd36e87ac649e34c97'
WORKER_START = '  storeConnection(agentId) {'
WORKER_END = '  readStoreFingerprint(agentId) {'
OWNER = 'var SandSearchIndexWriter = class {'
WORKER_MARK = 'openbot-content-search-cache-v1'
HIT_ANCHOR = '    if (cached != null) return cached;\n'
HIT_REPLACEMENT = '''    if (cached != null) {
      this.storeConnections.delete(agentId);
      this.storeConnections.set(agentId, cached);
      return cached;
    }
'''
OPEN_ANCHOR = '      refuseSandDataDatabaseLinksSync(path);\n'
EVICT_INSERT = '''      /* openbot-content-search-cache-v1 */
      while (this.storeConnections.size >= 8) {
        const oldest = this.storeConnections.keys().next().value;
        const previous = this.storeConnections.get(oldest);
        // A failed close retains the native null/fingerprint fallback; do not open a ninth handle.
        previous.db.close();
        this.storeConnections.delete(oldest);
      }
'''

HOST_START = 'function defaultWorkerEntryPath() {'
HOST_RETAINED_START = 'function openbotContentSearchNativeWorkerEntryPath() {'
HOST_END = 'var SearchIndexWorkerChannel = class {'
HOST_COMPONENT_SHA = '35544fcb9b467bf5ce72752ce068da5a593e643d2d5a89571a7016eb08cc52c3'
HOST_PATCHED_COMPONENT_SHA = '251af8d15fa5504d3093f6fb0d9624d895dc5899582a3e22d051f7b38f92e837'
HOST_MARK = 'openbot-content-search-sidecar-v1'
HOST_SELECTOR = '''function defaultWorkerEntryPath() {
  /* openbot-content-search-sidecar-v1 */
  const nativePath = openbotContentSearchNativeWorkerEntryPath();
  try {
    const fs = require("node:fs"), path = require("node:path"), crypto = require("node:crypto");
    const root = path.resolve(process.env.OPENBOT_SAND_DATA || "/home/box/sand-data");
    if (fs.realpathSync(root) !== root) return nativePath;
    const readBounded = (file, maximum, owned, immutable = false) => {
      const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
      try {
        const info = fs.fstatSync(fd);
        if (!info.isFile() || info.size > maximum || (owned && info.uid !== process.getuid()) ||
            (immutable && (info.mode & 0o222) !== 0)) throw new Error("Untrusted search sidecar file");
        const bytes = fs.readFileSync(fd);
        const after = fs.fstatSync(fd);
        if (bytes.length > maximum || info.dev !== after.dev || info.ino !== after.ino ||
            info.size !== after.size || info.mtimeMs !== after.mtimeMs || info.ctimeMs !== after.ctimeMs) {
          throw new Error("Search sidecar changed during selection");
        }
        return bytes;
      } finally { fs.closeSync(fd); }
    };
    if (readBounded(path.join(root, "openbot-mode"), 64, true).toString().trim() !== "custom") return nativePath;
    const enabled = fs.lstatSync(path.join(root, "openbot-local-only.enable"));
    if (!enabled.isFile() || enabled.uid !== process.getuid()) return nativePath;
    const policy = JSON.parse(readBounded(path.join(root, "openbot-local-engine.json"), 16384, true).toString());
    const asset = policy.contentSearchWorker;
    if (policy.enabled !== true || asset?.schemaVersion !== 1 ||
        asset.sourceSha256 !== "WORKER_SOURCE_SHA_LITERAL" || asset.sha256 !== "WORKER_PATCHED_SHA_LITERAL") return nativePath;
    const selected = path.join(root, "openbot-local-engine", "sidecars", asset.sha256, "search-index-worker.cjs");
    if (asset.path !== selected || fs.realpathSync(selected) !== selected) return nativePath;
    for (let folder = path.dirname(selected); ; folder = path.dirname(folder)) {
      const info = fs.lstatSync(folder);
      if (!info.isDirectory() || info.uid !== process.getuid()) return nativePath;
      if (folder === root) break;
    }
    const digest = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
    if (digest(readBounded(nativePath, 1048576, false)) !== asset.sourceSha256 ||
        digest(readBounded(selected, 1048576, true, true)) !== asset.sha256) return nativePath;
    return selected;
  } catch { return nativePath; }
}
'''


def digest(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def patch_worker(source):
    """Reject unknown worker bytes; change only the reviewed synchronous cache lookup."""
    if digest(source) == WORKER_PATCHED_SHA and source.count(WORKER_MARK) == 1:
        return source
    if digest(source) != WORKER_SOURCE_SHA or WORKER_MARK in source or source.count(OWNER) != 1:
        raise RuntimeError('Unreviewed content-search worker source')
    if source.count(WORKER_START) != 1 or source.count(WORKER_END) != 1:
        raise RuntimeError('Ambiguous content-search worker cache component')
    begin, end = source.index(WORKER_START), source.index(WORKER_END)
    component = source[begin:end]
    if digest(component) != WORKER_COMPONENT_SHA or component.count(HIT_ANCHOR) != 1 or component.count(OPEN_ANCHOR) != 1:
        raise RuntimeError('Unreviewed content-search worker cache contract')
    updated = component.replace(HIT_ANCHOR, HIT_REPLACEMENT, 1).replace(OPEN_ANCHOR, OPEN_ANCHOR + EVICT_INSERT, 1)
    candidate = source[:begin] + updated + source[end:]
    if digest(updated) != WORKER_PATCHED_COMPONENT_SHA or digest(candidate) != WORKER_PATCHED_SHA:
        raise RuntimeError('Content-search worker transformation differs')
    return candidate


def patch(source):
    """Unknown unmarked host variants keep their current native worker selection."""
    marked = HOST_MARK in source
    if not marked and HOST_RETAINED_START in source:
        raise RuntimeError('Partial content-search sidecar host selection')
    start = HOST_RETAINED_START if marked else HOST_START
    if source.count(start) != 1 or source.count(HOST_END) != 1:
        if marked:
            raise RuntimeError('Ambiguous content-search sidecar host component')
        return source
    begin, end = source.index(start), source.index(HOST_END)
    component = source[begin:end]
    if marked:
        if source.count(HOST_MARK) != 1 or source.count(HOST_START) != 1 or digest(component) != HOST_PATCHED_COMPONENT_SHA:
            raise RuntimeError('Modified or partial content-search sidecar selection')
        return source
    if HOST_RETAINED_START in source or digest(component) != HOST_COMPONENT_SHA:
        return source
    selector = HOST_SELECTOR.replace('WORKER_SOURCE_SHA_LITERAL', WORKER_SOURCE_SHA).replace('WORKER_PATCHED_SHA_LITERAL', WORKER_PATCHED_SHA)
    updated = component.replace(HOST_START, HOST_RETAINED_START, 1) + selector
    if digest(updated) != HOST_PATCHED_COMPONENT_SHA:
        raise RuntimeError('Content-search host transformation differs')
    return source[:begin] + updated + source[end:]


if __name__ == '__main__':
    for argument in sys.argv[1:]:
        sys.stdout.write(patch(pathlib.Path(argument).read_text(encoding='utf8')))
