"""Publish Custom transcripts only for actual owner-visible BOX bindings.

Local profile reads are intentionally forced to BOX for execution. They cannot
classify the server's old Temporal archives for the sequential bulk publisher.
"""
import pathlib
import re

MARK = 'openbot-local-publication: authoritative-roster'
ANCHOR = 'var transcriptPublishExtension = defineHostExtension({'
STOCK = 'isTemporalAgent: (agentId) => readSandProfileHarness(getSandProfilePath((0, import_node_path172.join)(agentsRootDir, agentId))) === "temporal",'
STOCK_PATTERN = re.compile(r'^[ \t]*(?P<policy>' + re.escape(STOCK).replace(
    'import_node_path172', r'(?P<path_alias>import_node_path[0-9]*)') + r')$', re.MULTILINE)


def native_publication_policy(source):
    """Accept bundler alias renumbering, not a changed native predicate."""
    matches = list(STOCK_PATTERN.finditer(source))
    if len(matches) != 1:
        raise RuntimeError('Expected one native transcript publisher policy')
    match = matches[0]
    declaration = 'var ' + match['path_alias'] + ' = require("node:path");'
    if len(re.findall(r'^[ \t]*' + re.escape(declaration) + r'$', source, re.MULTILINE)) != 1:
        raise RuntimeError('Expected one native publisher node:path binding')
    return match['policy']
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


OBSERVER_MARK = 'openbot-transcript-publication-observer-v1'
OBSERVER_EXPORT = 'module.exports = { openbotCreateTranscriptPublicationObserver };'
OBSERVER = pathlib.Path(__file__).with_name('transcript-publication-observer.cjs').read_text().replace(OBSERVER_EXPORT, '')
PUBLISHER = '    const publisher = new TranscriptEntryPublisher({'
REPORT = '      report: (level, metadata) => context2.deps.telemetry.logs.reportTranscriptPublish(level, metadata)'
DRAIN = '  async drainAgent(agentId) {\n    const state = this.dirty.get(agentId);'
OBSERVED_DRAIN = '  async drainAgent(agentId) {\n    if (this.openbotPublicationObserver?.retryBlocked(agentId)) return;\n    const state = this.dirty.get(agentId);'
FAILURE = '      this.redirtyUnlessClearSuperseded(agentId, state);\n      this.report('
OBSERVED_FAILURE = '      this.redirtyUnlessClearSuperseded(agentId, state);\n      this.openbotPublicationObserver?.captureFailure(agentId, error42);\n      this.report('
COMMIT = '      commit: async (commit2) => {\n        const response = await client.commitGrokBotTranscriptEntries({'
OBSERVED_COMMIT = '      commit: async (commit2) => {\n        openbotPublicationObserver.commitAttempt(commit2);\n        const response = await client.commitGrokBotTranscriptEntries({'
READY = '    const createPublishSoon = (name17, delayMs, scope) =>'
OBSERVED_READY = '    publisher.openbotPublicationObserver = openbotPublicationObserver;\n' + READY
STOP = '    context2.onStop(() => {\n      unsubscribe?.();'
SETUP = '''    const openbotPublicationObserver = openbotCreateTranscriptPublicationObserver({
      getPublisher: () => publisher,
      enabled: () => openbotLocalFleetEnabled(),
      isAllowed: (agentId) => !openbotSkipTranscriptPublication(agentId, agentsRootDir),
      statePath: require("node:path").join(getSandRootDir(), "openbot-transcript-publication-status.json"),
      onStatusWriteError: () => context2.host.log("OpenBot publication observer could not persist private metadata", "warn")
    });
''' + PUBLISHER
OBSERVED_REPORT = '''      report: (level, metadata) => {
        try { context2.deps.telemetry.logs.reportTranscriptPublish(level, metadata); }
        finally { openbotPublicationObserver.observe(level, metadata); }
      }'''
OBSERVED_STOP = '    context2.onStop(() => {\n      openbotPublicationObserver.dispose();\n      unsubscribe?.();'


def patch(source):
    if MARK not in source:
        if source.count(ANCHOR) != 1:
            raise RuntimeError('Expected one native transcript publisher policy')
        stock = native_publication_policy(source)
        if 'function openbotLocalFleetEnabled()' not in source:
            raise RuntimeError('Local publication requires the explicit mode policy')
        source = source.replace(ANCHOR, HELPER + '\n' + ANCHOR, 1)
        source = source.replace(stock, stock.replace('readSandProfileHarness',
            'openbotSkipTranscriptPublication(agentId, agentsRootDir) || readSandProfileHarness'), 1)
    if source.count(MARK) != 1 or HELPER not in source:
        raise RuntimeError('Incomplete native publication ownership policy')
    if OBSERVER_MARK in source:
        if (source.count(OBSERVER_MARK) != 1 or OBSERVER not in source or
                any(anchor not in source for anchor in (SETUP, OBSERVED_REPORT, OBSERVED_STOP, OBSERVED_FAILURE, OBSERVED_COMMIT, OBSERVED_READY, OBSERVED_DRAIN))):
            raise RuntimeError('Incomplete native publication retry observer')
        return source
    if any(source.count(anchor) != 1 for anchor in (ANCHOR, PUBLISHER, REPORT, STOP, FAILURE, COMMIT, READY, DRAIN)):
        raise RuntimeError('Expected one native publisher retry contract')
    source = source.replace(ANCHOR, OBSERVER + '\n' + ANCHOR, 1)
    return (source.replace(PUBLISHER, SETUP, 1).replace(REPORT, OBSERVED_REPORT, 1)
            .replace(STOP, OBSERVED_STOP, 1).replace(FAILURE, OBSERVED_FAILURE, 1)
            .replace(COMMIT, OBSERVED_COMMIT, 1).replace(READY, OBSERVED_READY, 1)
            .replace(DRAIN, OBSERVED_DRAIN, 1))
