"""Publish Custom transcripts only for actual owner-visible BOX bindings.

Local profile reads are intentionally forced to BOX for execution. They cannot
classify the server's old Temporal archives for the sequential bulk publisher.
"""
MARK = 'openbot-local-publication: authoritative-roster'
ANCHOR = 'var transcriptPublishExtension = defineHostExtension({'
STOCK = 'isTemporalAgent: (agentId) => readSandProfileHarness(getSandProfilePath((0, import_node_path172.join)(agentsRootDir, agentId))) === "temporal",'
HELPER = '''function openbotSkipTranscriptPublication(agentId, agentsRootDir) {
  if (!openbotLocalFleetEnabled()) return false;
  const fs = require("node:fs"), root = "/home/box/sand-data/";
  try {
    const path = root + "openbot-publication-bindings.json", info = fs.lstatSync(path);
    if (!info.isFile() || info.uid !== process.getuid() || info.size > 1024 * 1024) return true;
    const manifest = JSON.parse(fs.readFileSync(path, "utf8"));
    if (manifest.schemaVersion !== 1 || !manifest.boxAgents || typeof manifest.boxAgents !== "object" || Array.isArray(manifest.boxAgents)) return true;
    const sid = manifest.boxAgents[agentId];
    if (typeof sid !== "string" || !/^[1-9][0-9]*$/.test(sid)) return true;
    const profilePath = agentsRootDir + "/" + agentId + "/profile.json", profileInfo = fs.lstatSync(profilePath);
    if (!profileInfo.isFile() || profileInfo.uid !== process.getuid() || profileInfo.size > 1024 * 1024) return true;
    const profile = JSON.parse(fs.readFileSync(profilePath, "utf8"));
    return profile.serverId !== sid;
  } catch { return true; }
} /* openbot-local-publication: authoritative-roster */
'''


def patch(source):
    if MARK in source:
        return source
    if source.count(ANCHOR) != 1 or source.count(STOCK) != 1:
        raise RuntimeError('Expected one native transcript publisher policy')
    if 'function openbotLocalFleetEnabled()' not in source:
        raise RuntimeError('Local publication requires the explicit mode policy')
    source = source.replace(ANCHOR, HELPER + '\n' + ANCHOR, 1)
    return source.replace(STOCK, STOCK.replace('readSandProfileHarness',
        'openbotSkipTranscriptPublication(agentId, agentsRootDir) || readSandProfileHarness'), 1)
