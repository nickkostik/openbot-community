// Synthetic test-owned worker. No native worker module or captured agent data.
var SandSearchIndexWriter = class {
  constructor(db, agentsRootDir) {
    this.db = db;
    this.agentsRootDir = agentsRootDir;
    this.index = new Map();
    this.fingerprints = new Map();
    this.reindexed = [];
  }
  storeConnections = new Map();
  storeDbPath(agentId) { return this.agentsRootDir + "/" + agentId + "/store.db"; }
  evictStoreConnection(agentId) {
    const old = this.storeConnections.get(agentId);
    if (old === undefined) return;
    this.storeConnections.delete(agentId);
    try { old.db.close(); } catch {}
  }
  close() {
    for (const saved of this.storeConnections.values()) {
      try { saved.db.close(); } catch {}
    }
    this.storeConnections.clear();
  }
  storeConnection(agentId) {
    const cached = this.storeConnections.get(agentId);
    if (cached != null) return cached;
    const path = this.storeDbPath(agentId);
    if (!syntheticExists(path)) return null;
    try {
      refuseSandDataDatabaseLinksSync(path);
      const db = new SyntheticDatabase(path, { readOnly: true });
      let saved = false;
      try {
        syntheticCheckDatabase(db, path);
        db.exec("synthetic connection settings");
        const result = { db };
        this.storeConnections.set(agentId, result);
        saved = true;
        return result;
      } finally {
        if (!saved) db.close();
      }
    } catch {
      return null;
    }
  }
  readStoreFingerprint(agentId) {
    const handle = this.storeConnection(agentId);
    if (handle === null) return null;
    try {
      const sample = handle.db.prepare("synthetic fingerprint").get();
      if (typeof sample?.count !== "number" || typeof sample?.maxSeq !== "number") return null;
      return sample.count + ":" + sample.maxSeq;
    } catch {
      this.evictStoreConnection(agentId);
      return null;
    }
  }
  reindexAgent(agentId) {
    this.evictStoreConnection(agentId);
    const handle = this.storeConnection(agentId);
    if (handle === null) return;
    let stored;
    try { stored = handle.db.prepare("synthetic transcript rows").all(); }
    catch { this.evictStoreConnection(agentId); return; }
    const accepted = [];
    for (const item of stored) {
      try {
        const parsed = JSON.parse(item.entry);
        if (typeof parsed?.id === "string" && typeof parsed.text === "string") accepted.push(parsed);
      } catch {}
    }
    this.index.set(agentId, accepted);
    this.fingerprints.set(agentId, stored.length + ":" + Math.max(0, ...stored.map(item => item.seq)));
    this.reindexed.push(agentId);
  }
  reconcile(agentIds) {
    for (const agentId of agentIds) {
      const fingerprint = this.readStoreFingerprint(agentId);
      if (fingerprint !== null && this.fingerprints.get(agentId) !== fingerprint) this.reindexAgent(agentId);
    }
  }
  search(needle) {
    const hits = [];
    for (const [agentId, rows] of this.index) {
      for (const item of rows) if (item.text.includes(needle)) hits.push({agentId, id:item.id, text:item.text});
    }
    return hits.sort((left, right) => (left.agentId + ":" + left.id).localeCompare(right.agentId + ":" + right.id));
  }
};
