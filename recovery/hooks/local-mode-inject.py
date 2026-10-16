"""Keep this OpenBot installation local when its explicit mode flag is present."""
import pathlib
import re
import subprocess
import sys

FLAG = '/home/box/sand-data/openbot-local-only.enable'
MARK = 'openbot-local-mode'

# The server proxy roster is independent of profile/identity serialization.
# A BOX-labelled local summary must never silently take this Temporal fast path.
PROXY_STOCK = '''isProxiedAgent: (agentId) => {
      refreshRoster();
      return requiredAgents.has(agentId);
    }'''
PROXY_LOCAL = PROXY_STOCK.replace(
    '      refreshRoster();',
    '      if (openbotLocalFleetEnabled()) return false; /* openbot-local-mode: local-dispatch */\n'
    '      refreshRoster();')

LEGACY_FLEET_HELPERS = '''function openbotLocalFleetEnabled() {
  const fs = require("node:fs");
  try {
    return fs.existsSync("/home/box/sand-data/openbot-local-only.enable") &&
      fs.readFileSync("/home/box/sand-data/openbot-mode", "utf8").trim() === "custom";
  } catch { return false; }
} /* openbot-local-mode: fleet policy */
function openbotRetiredRoutineOwner(agentId) {
  if (!openbotLocalFleetEnabled()) return false;
  const fs = require("node:fs");
  const root = "/home/box/sand-data/";
  const journalPath = root + "openbot-local-migration.json";
  try {
    if (!fs.existsSync(journalPath)) return false;
    const journal = JSON.parse(fs.readFileSync(journalPath, "utf8"));
    if (!Array.isArray(journal.entries)) return true;
    const entry = journal.entries.find((row) => row.oldId === agentId &&
      (row.finalized === true || journal.finalized === true || journal.migrationEffectsFinalized === true));
    if (!entry) return false;
    if (typeof entry.newId !== "string" || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(entry.newId) || entry.newId === agentId) return true;
    const currentPath = root + "agents/" + entry.newId + "/profile.json";
    if (!fs.existsSync(currentPath)) return false;
    const current = JSON.parse(fs.readFileSync(currentPath, "utf8"));
    return current != null && typeof current === "object" && current.harness === "box";
  } catch { return true; } // Unreadable migration authority must not start duplicate work.
}
'''

FLEET_HELPERS = LEGACY_FLEET_HELPERS[:LEGACY_FLEET_HELPERS.index('function openbotRetiredRoutineOwner(')] + '''function openbotRetiredRoutineOwner(agentId) {
  if (!openbotLocalFleetEnabled()) return false;
  const fs = require("node:fs"), root = "/home/box/sand-data/";
  try {
    for (const filename of ["openbot-local-migration.json", "openbot-box-remint.json"]) {
      const journalPath = root + filename;
      if (!fs.existsSync(journalPath)) continue;
      const journal = JSON.parse(fs.readFileSync(journalPath, "utf8"));
      if (!Array.isArray(journal.entries)) return true;
      const entry = journal.entries.find((row) => row.oldId === agentId &&
        (filename === "openbot-box-remint.json" ? row.phase === "active" :
          row.finalized === true || journal.finalized === true || journal.migrationEffectsFinalized === true));
      if (!entry) continue;
      if (typeof entry.newId !== "string" || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(entry.newId) || entry.newId === agentId) return true;
      const currentPath = root + "agents/" + entry.newId + "/profile.json";
      if (!fs.existsSync(currentPath)) continue;
      const current = JSON.parse(fs.readFileSync(currentPath, "utf8"));
      if (current != null && typeof current === "object" && current.harness === "box") return true;
    }
    return false;
  } catch { return true; } // Unreadable migration authority must not start duplicate work.
} /* openbot-local-mode: remint-retirement */
'''

LEGACY_REMINT_HELPERS = FLEET_HELPERS
FLEET_HELPERS = FLEET_HELPERS.replace(
    '      const entry = journal.entries.find((row) => row.oldId === agentId &&',
    '      if (journal.previousGenerations !== undefined && !Array.isArray(journal.previousGenerations)) return true;\n'
    '      const prior = (journal.previousGenerations || []).flatMap((generation) => {\n'
    '        if (!Array.isArray(generation.entries)) throw new Error("Invalid remint history");\n'
    '        return generation.entries;\n'
    '      });\n'
    '      const entry = [...journal.entries, ...prior].find((row) => row.oldId === agentId &&') + \
    '/* openbot-local-mode: generation-retirement */\n'

ROUTINE_HOLD_HELPER = '''function openbotHeldRoutineOwner(agentId) {
  if (!openbotLocalFleetEnabled()) return false;
  const fs = require("node:fs"), root = "/home/box/sand-data/";
  for (const [filename, key] of [["openbot-routine-holds.json", "heldAgentIds"],
                               ["openbot-server-roster.alert.json", "driftAgentIds"]]) {
    try {
      const info = fs.lstatSync(root + filename);
      if (!info.isFile() || info.uid !== process.getuid() || info.size > 1024 * 1024) return true;
      const value = JSON.parse(fs.readFileSync(root + filename, "utf8"));
      if (!Array.isArray(value[key]) || value[key].some((id) => typeof id !== "string" || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id))) return true;
      if (value[key].includes(agentId)) return true;
    } catch (error) { if (error.code !== "ENOENT") return true; }
  }
  return false;
} /* openbot-local-mode: held-routines */
'''
HOLD_HOSTING = '      if (openbotRetiredRoutineOwner(agentId)) return { harness: null, isServerBacked: false, onSessionBox: sessionBox };'
LEGACY_HELD_HOSTING = HOLD_HOSTING.replace('openbotRetiredRoutineOwner(agentId)', 'openbotRetiredRoutineOwner(agentId) || openbotHeldRoutineOwner(agentId)')
# Native sync treats null harness + no server binding as a legacy BOX owner.
# Retired/held owners must take its excluded server-backed branch instead.
HELD_HOSTING = LEGACY_HELD_HOSTING.replace('isServerBacked: false', 'isServerBacked: true')
MODE_POLICY_STOCK = LEGACY_FLEET_HELPERS[:LEGACY_FLEET_HELPERS.index('function openbotRetiredRoutineOwner(')]
MODE_POLICY_LOCAL = MODE_POLICY_STOCK.replace(
    '    return fs.existsSync("/home/box/sand-data/openbot-local-only.enable") &&\n'
    '      fs.readFileSync("/home/box/sand-data/openbot-mode", "utf8").trim() === "custom";',
    '    if (!fs.existsSync("/home/box/sand-data/openbot-local-only.enable")) return false;\n'
    '    const mode = fs.readFileSync("/home/box/sand-data/openbot-mode", "utf8").trim();\n'
    '    return mode === "custom" || mode === "official";')


def patch(source):
    # Upgrade the previous both-mode policy before matching retirement helpers.
    if source.count(MODE_POLICY_LOCAL) == 1:
        source = source.replace(MODE_POLICY_LOCAL, MODE_POLICY_STOCK, 1)
    retirement_tag = MARK + ': remint-retirement'
    if MARK + ': fleet policy' in source and retirement_tag not in source:
        if source.count(LEGACY_FLEET_HELPERS) != 1:
            raise RuntimeError('Expected one legacy fleet helper for the remint retirement upgrade')
        source = source.replace(LEGACY_FLEET_HELPERS, FLEET_HELPERS, 1)
    elif source.count(retirement_tag) > 1:
        raise RuntimeError('Ambiguous remint retirement helper')
    generation_tag = MARK + ': generation-retirement'
    if retirement_tag in source and generation_tag not in source:
        if source.count(LEGACY_REMINT_HELPERS) != 1:
            raise RuntimeError('Expected one remint retirement helper for history upgrade')
        source = source.replace(LEGACY_REMINT_HELPERS, FLEET_HELPERS, 1)
    elif generation_tag in source and source.count(generation_tag) != 1:
        raise RuntimeError('Ambiguous generation retirement helper')
    creation = 'const getCreationPolicy = async (signal = lifetime.signal) => {'
    if MARK + ': identity' not in source:
        if source.count(creation) != 1:
            raise RuntimeError('Expected one identity creation policy anchor')
        source = source.replace(creation, creation + '\n' +
            '    if (openbotLocalFleetEnabled()) {\n' +
            '      applyReads(false);\n' +
            '      return { isLegacy: true, durableIdentityEnabled: false, durableIdentityWritesEnabled: false, temporalCreationEnabled: false };\n' +
            f'    }} /* {MARK}: identity stays local */\n', 1)
    # A later experiment refresh must not re-enable sync-down and rewrite BOX profiles.
    reads = 'const applyReads = (isOn) => {'
    if MARK + ': reads' not in source:
        if source.count(reads) != 1:
            raise RuntimeError('Expected one identity read switch')
        source = source.replace(reads, reads + '\n' +
            f'    if (openbotLocalFleetEnabled()) isOn = false; /* {MARK}: reads stay local */', 1)
    migration = re.compile(r'(name: "sand-harness-migration-window",[\s\S]{0,700}?\}\)\.run\(async \(deadlineSignal\) => \{)')
    if MARK + ': migration' not in source:
        if len(migration.findall(source)) != 1:
            raise RuntimeError('Expected one named harness migration window anchor')
        source = migration.sub(lambda match: match.group(1) + '\n' +
            f'        if (openbotLocalFleetEnabled()) return "disabled"; /* {MARK}: migration disabled */', source, count=1)
    # Earlier guards ignored mode and blocked native identity/migration in Official.
    for marker, tail in [('identity stays local', ' {'), ('reads stay local', ' isOn = false;'),
                         ('migration disabled', ' return "disabled";')]:
        old = f'if (require("node:fs").existsSync("{FLAG}"))' + tail
        current = 'if (openbotLocalFleetEnabled())' + tail
        if old in source:
            if source.count(old) != 1:
                raise RuntimeError('Ambiguous mode guard: ' + marker)
            source = source.replace(old, current, 1)
        if source.count(current) != 1:
            raise RuntimeError('Incomplete mode guard: ' + marker)
    # Cover the whole installed fleet, not only the manually recovered allowlist.
    # Read/write guards also protect the interval before the recovery scan runs.
    edits = (
        ('fleet policy', 'function profileHarnessFromJson(parsed2) {', FLEET_HELPERS + 'function profileHarnessFromJson(parsed2) {'),
        ('profile-read', 'function profileHarnessFromJson(parsed2) {',
         'function profileHarnessFromJson(parsed2) {\n  if (openbotLocalFleetEnabled()) return "box"; /* openbot-local-mode: profile-read */'),
        ('profile-write', 'function serializeSandProfileFile(profile, binding = {}) {',
         'function serializeSandProfileFile(profile, binding = {}) {\n  if (openbotLocalFleetEnabled()) binding = { ...binding, harness: "box" }; /* openbot-local-mode: profile-write */'),
        ('retired-routines', '    const readHosting = (agentId) => {',
         '    const readHosting = (agentId) => {\n      if (openbotRetiredRoutineOwner(agentId)) return { harness: null, isServerBacked: false, onSessionBox: sessionBox }; /* openbot-local-mode: retired-routines */'),
    )
    for marker, anchor, replacement in edits:
        tag = MARK + ': ' + marker
        if tag in source:
            if source.count(tag) != 1:
                raise RuntimeError('Ambiguous local fleet policy marker: ' + marker)
            continue
        if source.count(anchor) != 1:
            raise RuntimeError('Expected one local fleet policy anchor: ' + marker)
        source = source.replace(anchor, replacement, 1)
    # The desktop coordinator retains a previously observed temporal harness
    # when the local roster omits harness. Explicit BOX clears that sticky route.
    roster_tag = MARK + ': roster-box'
    if roster_tag not in source:
        anchor = '...readSandProfileHarness(profilePath) === "temporal" ? { harness: "temporal" } : {}'
        if source.count(anchor) != 2:
            raise RuntimeError('Expected both local agent summary harness anchors')
        source = source.replace(anchor,
            '...openbotLocalFleetEnabled() ? { harness: "box" } : readSandProfileHarness(profilePath) === "temporal" ? { harness: "temporal" } : {} /* ' + roster_tag + ' */')
    elif source.count(roster_tag) != 2:
        raise RuntimeError('Incomplete explicit BOX roster policy')
    # An empty published bot still needs a local row before its first message.
    # Otherwise the desktop has only its temporal server row to route by.
    blank_tag = MARK + ': bound-blank-roster'
    if blank_tag not in source:
        anchor = 'if (!isActive && !includeBlank) {'
        if source.count(anchor) != 1:
            raise RuntimeError('Expected one blank agent summary filter')
        source = source.replace(anchor,
            'if (!isActive && !includeBlank && !(openbotLocalFleetEnabled() && readSandProfileServerId(profilePath) !== null)) { /* ' + blank_tag + ' */', 1)
    elif source.count(blank_tag) != 1:
        raise RuntimeError('Ambiguous bound blank roster policy')
    dispatch_tag = MARK + ': local-dispatch'
    if dispatch_tag not in source:
        if source.count(PROXY_STOCK) != 1:
            raise RuntimeError('Expected one native Temporal proxy dispatch predicate')
        source = source.replace(PROXY_STOCK, PROXY_LOCAL, 1)
    elif source.count(dispatch_tag) != 1 or source.count(PROXY_LOCAL) != 1:
        raise RuntimeError('Incomplete local dispatch predicate')
    # A stale locally forced BOX label must not re-enable the Temporal owner's
    # server schedules while an explicit transfer hold/drift alert is present.
    hold_tag = MARK + ': held-routines'
    if hold_tag not in source:
        anchor = 'function profileHarnessFromJson(parsed2) {'
        if source.count(anchor) != 1 or source.count(HOLD_HOSTING) != 1:
            raise RuntimeError('Expected one native routine hold adapter')
        source = source.replace(anchor, ROUTINE_HOLD_HELPER + anchor, 1)
        source = source.replace(HOLD_HOSTING, HELD_HOSTING, 1)
    elif source.count(ROUTINE_HOLD_HELPER) == 1 and source.count(LEGACY_HELD_HOSTING) == 1 and HELD_HOSTING not in source:
        source = source.replace(LEGACY_HELD_HOSTING, HELD_HOSTING, 1)
    elif source.count(ROUTINE_HOLD_HELPER) != 1 or source.count(HELD_HOSTING) != 1 or LEGACY_HELD_HOSTING in source:
        raise RuntimeError('Incomplete native routine hold adapter')
    if source.count(MODE_POLICY_STOCK) != 1 or MODE_POLICY_LOCAL in source:
        raise RuntimeError('Incomplete Custom local fleet policy')
    return source


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        path = pathlib.Path(arg)
        source = path.read_text()
        updated = patch(source)
        if updated == source:
            print(path, 'already patched')
            continue
        check = subprocess.run(['node', '--check'], input=updated, text=True, capture_output=True)
        if check.returncode:
            raise RuntimeError(check.stderr)
        temp = path.with_name(path.name + '.local-mode.tmp')
        temp.write_text(updated)
        temp.chmod(path.stat().st_mode)
        temp.replace(path)
        print(path, 'local mode patch applied')
