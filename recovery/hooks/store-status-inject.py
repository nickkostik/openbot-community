"""Report acknowledged native backup metadata without downloading it per poll.

Only a parsed canonical read or a completed native write seeds the summary.
Snapshot, conflict handling, database capture and update acceptance stay native.
"""
import re

LEGACY_MARKER = '/* openbot-canonical-store-status-v1 */'
MARKER = '/* openbot-canonical-store-status-v2 */'
LEGACY_HELPER = '''  rememberOpenbotCanonicalStoreStatus(snapshot) {
    /* openbot-canonical-store-status-v1 */
    if (snapshot == null) return;
    if (this.openbotCanonicalStoreStatus?.lastSnapshotAtMs > snapshot.updatedAtMs) return;
    const manifest = new Map(Object.entries(snapshot.entries));
    const entries = [...manifest.values()];
    this.openbotCanonicalStoreStatus = {
      durable: entries.length > 0,
      fullyHydrated: snapshot.fullyHydrated,
      entryCount: entries.length,
      storeDbEntries: countStoreDbManifestEntries(manifest),
      agentDirEntries: countAgentDirManifestEntries(manifest),
      totalBytes: entries.reduce((sum, entry) => sum + (isBoxStoreManifestFileEntry(entry) ? entry.size : 0), 0),
      lastSnapshotAtMs: snapshot.updatedAtMs
    };
  }
'''
HELPER = '''  rememberOpenbotCanonicalStoreStatus(snapshot) {
    /* openbot-canonical-store-status-v2 */
    if (snapshot == null) return;
    if (this.openbotCanonicalStoreStatus?.lastSnapshotAtMs > snapshot.updatedAtMs) return;
    const values = snapshot.entries;
    const manifest = values instanceof Map ? values : {
      *keys() {
        for (const path in values) if (Object.hasOwn(values, path)) yield path;
      },
      *[Symbol.iterator]() {
        for (const path in values) if (Object.hasOwn(values, path)) yield [path, values[path]];
      }
    };
    let entryCount = 0;
    let totalBytes = 0;
    for (const [, entry] of manifest) {
      entryCount += 1;
      if (isBoxStoreManifestFileEntry(entry)) totalBytes += entry.size;
    }
    this.openbotCanonicalStoreStatus = {
      durable: entryCount > 0,
      fullyHydrated: snapshot.fullyHydrated,
      entryCount,
      storeDbEntries: countStoreDbManifestEntries(manifest),
      agentDirEntries: countAgentDirManifestEntries(manifest),
      totalBytes,
      lastSnapshotAtMs: snapshot.updatedAtMs
    };
  }
'''
WRAPPER = '''  async readStoreStatus() {
    const known = this.manifestStore.openbotCanonicalStoreStatus;
    if (known != null) return { ...known };
    if (this.openbotStoreStatusRead == null) {
      this.openbotStoreStatusRead = this.readStoreStatus_openbotNative().then((status) => {
        const confirmed = this.manifestStore.openbotCanonicalStoreStatus;
        return confirmed == null ? status : { ...confirmed };
      }).finally(() => { this.openbotStoreStatusRead = void 0; });
    }
    return this.openbotStoreStatusRead;
  }
  async readStoreStatus_openbotNative() {'''
# Seed only after native read/path/hydration validation has succeeded.
READ_TAIL = '''    this.initializeManifestRevision(map4, persisted);
    this.manifest ??= map4;'''
LEGACY_READ_TAIL_UPDATED = '''    if (persisted != null) {
      // This map comes from the canonical read, before live edits can enter it.
      this.rememberOpenbotCanonicalStoreStatus({ entries: Object.fromEntries(map4),
        fullyHydrated: this.fullyHydrated, updatedAtMs: this.openbotCanonicalReadAtMs });
    }
    this.initializeManifestRevision(map4, persisted);
    this.manifest ??= map4;'''
READ_TAIL_UPDATED = LEGACY_READ_TAIL_UPDATED.replace('Object.fromEntries(map4)', 'map4')
READ_TIME = '''        this.hydrationManifestReadBlocked = false;
        this.fullyHydrated = decoded.fullyHydrated;
        let isCanonicalSnapshot = true;'''
READ_TIME_UPDATED = READ_TIME.replace('        let isCanonicalSnapshot',
    '        this.openbotCanonicalReadAtMs = decoded.updatedAtMs;\n        let isCanonicalSnapshot')
WRITE = '''        new Uint8Array(Buffer.from(JSON.stringify(manifest), "utf8"))
      );
    } catch (error42) {'''
WRITE_UPDATED = WRITE.replace('    } catch',
    '      this.rememberOpenbotCanonicalStoreStatus(manifest);\n    } catch')
CANONICAL = '''    if (bytes == null) return null;
    return this.parseManifest(bytes);
  }
  reportManifestWriteConflict'''
CANONICAL_UPDATED = CANONICAL.replace('    return this.parseManifest(bytes);',
    '    const snapshot = this.parseManifest(bytes);\n    this.rememberOpenbotCanonicalStoreStatus(snapshot);\n    return snapshot;')


# Bundlers renumber local catch bindings without changing the acknowledged
# write contract. Match that binding only; never rename it or relax the write.
_IDENTIFIER = r'[$A-Za-z_][$A-Za-z_0-9]*'
WRITE_PATTERN = re.compile(re.escape(WRITE).replace('error42', _IDENTIFIER))
WRITE_UPDATED_PATTERN = re.compile(re.escape(WRITE_UPDATED).replace('error42', _IDENTIFIER))


def validate_patched(source, helper, marker, read_tail=READ_TAIL_UPDATED):
    fragments = (helper, WRAPPER, read_tail, READ_TIME_UPDATED, CANONICAL_UPDATED)
    if source.count(marker) != 1 or any(source.count(part) != 1 for part in fragments) or \
            len(WRITE_UPDATED_PATTERN.findall(source)) != 1 or WRITE_PATTERN.search(source):
        raise RuntimeError('Canonical backup status patch is incomplete')


def patch(source):
    if MARKER in source:
        if LEGACY_MARKER in source:
            raise RuntimeError('Canonical backup status patch has mixed versions')
        validate_patched(source, HELPER, MARKER)
        return source
    if LEGACY_MARKER in source:
        validate_patched(source, LEGACY_HELPER, LEGACY_MARKER, LEGACY_READ_TAIL_UPDATED)
        updated = source.replace(LEGACY_HELPER, HELPER, 1).replace(LEGACY_READ_TAIL_UPDATED, READ_TAIL_UPDATED, 1)
        validate_patched(updated, HELPER, MARKER)
        return updated
    replacements = [('  async readCanonicalManifestSnapshot() {', HELPER + '  async readCanonicalManifestSnapshot() {'),
                    ('  async readStoreStatus() {', WRAPPER), (READ_TAIL, READ_TAIL_UPDATED),
                    (READ_TIME, READ_TIME_UPDATED), (CANONICAL, CANONICAL_UPDATED)]
    # Check every anchor before constructing any output. An unfamiliar native
    # acknowledgement path remains unsupported rather than advertising success.
    if len(WRITE_PATTERN.findall(source)) != 1 or any(source.count(old) != 1 for old, _ in replacements):
        raise RuntimeError('Native backup status contract is missing or ambiguous')
    for old, new in replacements:
        source = source.replace(old, new, 1)
    source = WRITE_PATTERN.sub(lambda match: match.group(0).replace(
        '    } catch', '      this.rememberOpenbotCanonicalStoreStatus(manifest);\n    } catch'), source, count=1)
    validate_patched(source, HELPER, MARKER)
    return source
