"""Report acknowledged native backup metadata without downloading it per poll.

Only a parsed canonical read or a completed native write seeds the summary.
Snapshot, conflict handling, database capture and update acceptance stay native.
"""
MARKER = '/* openbot-canonical-store-status-v1 */'
HELPER = '''  rememberOpenbotCanonicalStoreStatus(snapshot) {
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
READ_TAIL_UPDATED = '''    if (persisted != null) {
      // This map comes from the canonical read, before live edits can enter it.
      this.rememberOpenbotCanonicalStoreStatus({ entries: Object.fromEntries(map4),
        fullyHydrated: this.fullyHydrated, updatedAtMs: this.openbotCanonicalReadAtMs });
    }
    this.initializeManifestRevision(map4, persisted);
    this.manifest ??= map4;'''
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


def patch(source):
    if MARKER in source:
        # A partial/foreign stamp is not a fixed point.
        for fragment in (HELPER, WRAPPER, READ_TAIL_UPDATED, READ_TIME_UPDATED, WRITE_UPDATED, CANONICAL_UPDATED):
            if source.count(fragment) != 1:
                raise RuntimeError('Canonical backup status patch is incomplete')
        return source
    replacements = [('  async readCanonicalManifestSnapshot() {', HELPER + '  async readCanonicalManifestSnapshot() {'),
                    ('  async readStoreStatus() {', WRAPPER), (READ_TAIL, READ_TAIL_UPDATED),
                    (READ_TIME, READ_TIME_UPDATED), (WRITE, WRITE_UPDATED), (CANONICAL, CANONICAL_UPDATED)]
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError('Native backup status contract is missing or ambiguous')
        source = source.replace(old, new, 1)
    return source
