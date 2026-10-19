async function listAgents(host, activeAgentId) {
  // Synthetic, test-owned component. This is not a native host source excerpt.
  let entries;
  try {
    entries = await import_promises77.readdir(host.rootDir, { withFileTypes: true });
  } catch (problem) {
    if (problem.code === "ENOENT") return [];
    throw problem;
  }
  const summaries = await Promise.all(
    entries.filter((entry) => entry.isDirectory()).map(async (entry) => {
      const id = entry.name;
      if (host.isAgentBeingDeleted(id)) return null;
      const dbPath = getAgentDbPath(host.rootDir, id);
      const dbStats = await statIfExists(dbPath);
      const report = (kind, problem) => reportSessionDiagnostic({
        family: "summary_build", kind, agentId: id, errorClass: errorLogTag(problem)
      });
      const minimal = () => minimalAgentSummary({
        dirName: id, dbPath, dbStats: dbStats ?? undefined, activeAgentId
      });
      if (dbStats == null) {
        try {
          return await host.recoverAgentWithMissingDb({dbPath, dirName: id, activeAgentId});
        } catch (problem) {
          report("recovery_build_failed", problem);
          return minimal();
        }
      }
      const common = {dbPath, dirName: id, dbStats, activeAgentId,
        agentHasMemory: candidate => host.memory.agentHasContent(candidate)};
      try {
        const extras = await host.loadCachedExtras({dirName: id, dbPath, dbStats});
        if (host.isAgentBeingDeleted(id)) return null;
        return await buildSummary({...common, extras, includeBlank: false});
      } catch (problem) {
        if (host.isAgentBeingDeleted(id)) return null;
        report("degraded", problem);
        try {
          return await buildSummary({...common, extras: null, includeBlank: true});
        } catch (fallbackProblem) {
          report("degraded_failed", fallbackProblem);
          return minimal();
        }
      }
    })
  );
  host.pruneExtrasCache(new Set(entries.map((entry) => entry.name)));
  return summaries.filter((summary) => summary != null).sort(compareAgentSummaries);
}
