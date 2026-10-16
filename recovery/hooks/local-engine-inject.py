"""Keep a tested local engine from silently upgrading to a retired BOX runner."""
MARK = 'openbot-local-engine-update-guard-v1'
LEGACY_HELPER = '''function openbotLocalEnginePinned() {
  const fs = require("node:fs"), root = "/home/box/sand-data/";
  try {
    if (!fs.existsSync(root + "openbot-local-only.enable") ||
        fs.readFileSync(root + "openbot-mode", "utf8").trim() !== "custom") return false;
    const info = fs.lstatSync(root + "openbot-local-engine.json");
    if (!info.isFile() || info.uid !== process.getuid()) return true;
    return JSON.parse(fs.readFileSync(root + "openbot-local-engine.json", "utf8")).enabled !== false;
  } catch (error) { return error.code !== "ENOENT"; }
} /* openbot-local-engine-update-guard-v1 */
'''
BOTH_MODE_HELPER = LEGACY_HELPER.replace(
    '    if (!fs.existsSync(root + "openbot-local-only.enable") ||\n'
    '        fs.readFileSync(root + "openbot-mode", "utf8").trim() !== "custom") return false;',
    '    if (!fs.existsSync(root + "openbot-local-only.enable")) return false;\n'
    '    const mode = fs.readFileSync(root + "openbot-mode", "utf8").trim();\n'
    '    if (mode !== "custom" && mode !== "official") return false;')
HELPER = LEGACY_HELPER
STAGE = '''  async runHostBundleFetchStage({
    source,
    localVersion,
    trigger: trigger2,
    forceNow
  }) {'''
IMAGE = 'function isImageAutoUpdateEnabled(environment) {'
STAGE_GUARD = '''
    if (openbotLocalEnginePinned()) return { ok: false, error: new Error("OpenBot local engine is pinned; review compatibility before enabling computer updates") };'''
IMAGE_GUARD = '\n  if (openbotLocalEnginePinned()) return false;'


def patch(source):
    if MARK in source:
        if source.count(BOTH_MODE_HELPER) == 1:
            source = source.replace(BOTH_MODE_HELPER, HELPER, 1)
        if source.count(HELPER) != 1 or source.count(STAGE + STAGE_GUARD) != 1 or source.count(IMAGE + IMAGE_GUARD) != 1:
            raise RuntimeError('Incomplete local engine update guard')
        return source
    if source.count(STAGE) != 1 or source.count(IMAGE) != 1:
        raise RuntimeError('Local engine update guard anchor changed')
    source = source.replace(STAGE, STAGE + STAGE_GUARD, 1)
    return source.replace(IMAGE, HELPER + IMAGE + IMAGE_GUARD, 1)
