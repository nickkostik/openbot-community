"""Stream native direct-store byte uploads through replayable immutable Blobs."""
MARKER = '/* openbot-store-byte-upload-v1 */'
CLASS = 'var SandBoxStoreServiceObjectStore = class {'
HELPER = '''function openbotStoreUploadBody(bytes) {
  /* openbot-store-byte-upload-v1 */
  // Blob is an immutable snapshot with a replayable, backpressured fetch body.
  // Keep native coercion for uncommon inputs that are not ordinary byte views.
  if (!(bytes instanceof Uint8Array) ||
      (typeof SharedArrayBuffer !== "undefined" && bytes.buffer instanceof SharedArrayBuffer)) {
    return Buffer.from(bytes);
  }
  const { Blob: NativeBlob } = require("node:buffer");
  const parts = [];
  for (let offset = 0; offset < bytes.byteLength; offset += 65536) {
    parts.push(bytes.subarray(offset, offset + 65536));
  }
  return new NativeBlob(parts);
}
'''
OLD = '''    const putOnce = () => fetch(instruction.url, {
      method: "PUT",
      headers: { ...instruction.headers },
      body: Buffer.from(bytes),
      signal
    });'''
NEW = OLD.replace('body: Buffer.from(bytes)', 'body: openbotStoreUploadBody(bytes)')
REGIONS = (
    ('  async putContentAddressed(key, bytes, signal) {', '  async resolveMutableBaseline(key) {'),
    ('  async putMutable(key, bytes, signal) {', '  async putFromFile(key, srcPath, sha, size) {'),
)


def regions(source):
    if source.count(CLASS) != 1:
        raise RuntimeError('Native direct-store upload class is missing or ambiguous')
    class_start = source.index(CLASS)
    class_end = source.find('\n};', class_start)
    if class_end < 0:
        raise RuntimeError('Native direct-store upload class boundary changed')
    bounds = []
    for start, end in REGIONS:
        if source.count(start) != 1 or source.count(end) != 1:
            raise RuntimeError('Native direct-store upload methods are missing or ambiguous')
        a, b = source.index(start), source.index(end)
        if not class_start < a < b < class_end or (bounds and a < bounds[-1][1]):
            raise RuntimeError('Native direct-store upload method order changed')
        bounds.append((a, b))
    return bounds


def patch(source):
    bounds = regions(source)
    if MARKER in source:
        if source.count(MARKER) != 1 or source.count(HELPER + CLASS) != 1 or \
                source.count('openbotStoreUploadBody') != 3 or \
                any(source[a:b].count(NEW) != 1 or OLD in source[a:b] for a, b in bounds):
            raise RuntimeError('Native byte-upload patch is incomplete')
        return source
    if 'openbotStoreUploadBody' in source or any(source[a:b].count(OLD) != 1 for a, b in bounds):
        raise RuntimeError('Native direct-store upload body contract changed')
    for a, b in reversed(bounds):
        source = source[:a] + source[a:b].replace(OLD, NEW, 1) + source[b:]
    source = source.replace(CLASS, HELPER + CLASS, 1)
    if patch(source) != source:
        raise RuntimeError('Native byte-upload patch is not a fixed point')
    return source
