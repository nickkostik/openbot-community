"""Coalesce native Chrome-session backup events without changing native flush gates."""
MARKER = '/* openbot-store-chrome-snapshot-v1 */'
CLASS = 'var DefaultBoxStoreSyncService = class {'
OLD = '''      onSessionChange: () => {
        void this.sync?.snapshotNow({ chromeSessionOnly: true, waitForInFlight: true }).catch((error64) => {
          this.deps.log(`chrome-session snapshot rejected: ${errorMessage(error64)}`);
        });
      },'''
# The retained local engine differs only in this lexical catch binding.
OLD_VARIANTS = (OLD, OLD.replace('error64', 'error42'))
NEW = '''      onSessionChange: () => {
        openbotQueueChromeSessionSnapshot(this);
      },'''
HELPER = '''/* openbot-store-chrome-snapshot-v1 */
var openbotChromeSnapshotQueues = new WeakMap();
function openbotQueueChromeSessionSnapshot(service) {
  const sync = service.sync;
  if (sync == null || sync.stopped) return;
  let state = openbotChromeSnapshotQueues.get(sync);
  if (state == null) {
    state = { dirty: false, running: false };
    openbotChromeSnapshotQueues.set(sync, state);
  }
  state.dirty = true;
  if (state.running) return;
  state.running = true;
  const drain = async () => {
    try {
      while (state.dirty && !sync.stopped) {
        state.dirty = false;
        try {
          await sync.snapshotNow({ chromeSessionOnly: true, waitForInFlight: true });
        } catch (error64) {
          service.deps.log(`chrome-session snapshot rejected: ${errorMessage(error64)}`);
        }
        // Let already-waiting full flushes acquire the native cycle first.
        await new Promise((resolve) => setImmediate(resolve));
      }
    } finally {
      state.running = false;
    }
  };
  void drain().catch((error64) => {
    service.deps.log(`chrome-session snapshot rejected: ${errorMessage(error64)}`);
  });
}
'''


def bounds(source):
    if source.count(CLASS) != 1:
        raise RuntimeError('Native backup service is missing or ambiguous')
    start = source.index(CLASS)
    end = source.find('\n};', start)
    if end < 0:
        raise RuntimeError('Native backup service boundary changed')
    return start, end


def patch(source):
    start, end = bounds(source)
    region = source[start:end]
    if MARKER in source:
        if source.count(MARKER) != 1 or source.count(HELPER + CLASS) != 1 or \
                source.count('openbotQueueChromeSessionSnapshot') != 2 or \
                source.count('openbotChromeSnapshotQueues') != 3 or \
                region.count(NEW) != 1 or any(old in region for old in OLD_VARIANTS):
            raise RuntimeError('Native Chrome backup queue patch is incomplete')
        return source
    if 'openbotQueueChromeSessionSnapshot' in source or 'openbotChromeSnapshotQueues' in source or \
            sum(region.count(old) for old in OLD_VARIANTS) != 1:
        raise RuntimeError('Native Chrome backup callback contract changed')
    source = source[:start] + region.replace(next(old for old in OLD_VARIANTS if old in region), NEW, 1) + source[end:]
    source = source.replace(CLASS, HELPER + CLASS, 1)
    if patch(source) != source:
        raise RuntimeError('Native Chrome backup queue patch is not a fixed point')
    return source
