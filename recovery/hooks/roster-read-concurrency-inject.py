"""Bound native roster hydration without changing which histories are read.

The reviewed native implementation hydrates every agent directory concurrently.
Some previews need a complete transcript read, so that fan-out can create large
simultaneous allocations. Two workers retain the existing per-agent behavior,
fallbacks, result order and final sort. A shared FIFO gate also bounds reads
across overlapping roster calls; it does not impose a process memory limit.
"""
import hashlib
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

MARK = 'openbot-roster-read-concurrency'
START = 'async function listAgents(host, activeAgentId) {'
END = 'async function summarizeAgentById('
STOCK_SHA256 = 'befae3821d1c1d4ee0676b99575fc3bd8e4ca970634d3e519fccdeda4dd0de87'
PATCHED_SHA256 = '9a6df18673c85f8788a6fd593e28718bfda8ef8869006b8995c82c37bcfb17be'
NATIVE_STOCK_SHA256 = 'e879c6304413940614bec292822431bf4124ec747cdcb2c935638f75fc5bc3f8'
NATIVE_PATCHED_SHA256 = '49a62157bc319c99f7db639710e6a60220a2e673ef55ddc6e04edfe24dcfd4da'
CURRENT_NATIVE_STOCK_SHA256 = '2ea4fd9957930cf3734a38a25037b709675ccbcf58ef4aff98939ca1aa75abb4'
CURRENT_NATIVE_PATCHED_SHA256 = '8c748f8d27a9c17c2a68d0f620cb9ec9ef6813d60ac712788890f55c847d508e'

ORIGINAL_OPEN = '''  const summaries = await Promise.all(
    entries.filter((entry) => entry.isDirectory()).map(async (entry) => {'''
BOUNDED_OPEN = '''  /* openbot-roster-read-concurrency */
  const rosterReadGate = listAgents.__openbotRosterReadGate ??= { active: 0, waiters: [] };
  const withRosterReadPermit = async (read) => {
    await new Promise((resolve) => {
      if (rosterReadGate.active < 2) {
        rosterReadGate.active += 1;
        resolve();
      } else {
        rosterReadGate.waiters.push(resolve);
      }
    });
    try {
      return await read();
    } finally {
      const nextWaiter = rosterReadGate.waiters.shift();
      if (nextWaiter !== void 0) nextWaiter();
      else rosterReadGate.active -= 1;
    }
  };
  const rosterDirectories = entries.filter((entry) => entry.isDirectory());
  const summaries = new Array(rosterDirectories.length);
  let nextRosterIndex = 0;
  const hydrateRosterEntry = async (entry) => {'''
ORIGINAL_CLOSE = '''    })
  );
  host.pruneExtrasCache'''
BOUNDED_CLOSE = '''    };
  await Promise.all(Array.from({ length: Math.min(2, rosterDirectories.length) }, async () => {
    while (nextRosterIndex < rosterDirectories.length) {
      const index = nextRosterIndex++;
      summaries[index] = await withRosterReadPermit(() => hydrateRosterEntry(rosterDirectories[index]));
    }
  }));
  host.pruneExtrasCache'''
NATIVE_ORIGINAL_OPEN = '''  const summaries = await Promise.all(
    keepAgentDirs(entries, host.reportSkippedAgentDirs).map(async (agentId) => {'''
NATIVE_BOUNDED_OPEN = BOUNDED_OPEN.replace(
    'entries.filter((entry) => entry.isDirectory())',
    'keepAgentDirs(entries, host.reportSkippedAgentDirs)').replace(
        'const hydrateRosterEntry = async (entry) => {',
        'const hydrateRosterEntry = async (agentId) => {')


def digest(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def patch(source):
    """Transform only the exact reviewed roster component; fail closed on drift."""
    if source.count(START) != 1 or source.count(END) != 1:
        raise RuntimeError('Expected one native roster component and following anchor')
    start, end = source.index(START), source.index(END)
    if end <= start:
        raise RuntimeError('Native roster component anchors are out of order')
    component = source[start:end]
    fingerprint = digest(component)
    reviewed = {
        STOCK_SHA256: (PATCHED_SHA256, ORIGINAL_OPEN, BOUNDED_OPEN),
        NATIVE_STOCK_SHA256: (NATIVE_PATCHED_SHA256, NATIVE_ORIGINAL_OPEN, NATIVE_BOUNDED_OPEN),
        CURRENT_NATIVE_STOCK_SHA256: (CURRENT_NATIVE_PATCHED_SHA256, NATIVE_ORIGINAL_OPEN, NATIVE_BOUNDED_OPEN),
    }
    if MARK in source:
        if source.count(MARK) != 1 or fingerprint not in {value[0] for value in reviewed.values()}:
            raise RuntimeError('Modified or incomplete native roster concurrency patch')
        return source
    if fingerprint not in reviewed:
        raise RuntimeError('Native roster component differs from the reviewed source')
    expected, original_open, bounded_open = reviewed[fingerprint]
    if component.count(original_open) != 1 or component.count(ORIGINAL_CLOSE) != 1:
        raise RuntimeError('Native roster hydration anchors changed')
    updated = component.replace(original_open, bounded_open, 1).replace(
        ORIGINAL_CLOSE, BOUNDED_CLOSE, 1)
    if digest(updated) != expected:
        raise RuntimeError('Native roster transformation differs from the reviewed patch')
    return source[:start] + updated + source[end:]


def patch_file(path):
    """Stage and parse the complete bundle before replacing an unchanged file."""
    path = pathlib.Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError('Host source must be a regular file')
    before = path.read_bytes()
    updated = patch(before.decode('utf-8')).encode('utf-8')
    if updated == before:
        return False
    result = subprocess.run(['node', '--check'], input=updated, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace'))
    descriptor, name = tempfile.mkstemp(prefix='.roster-read-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(handle.fileno(), info.st_uid, info.st_gid)
        if path.read_bytes() != before or path.lstat().st_ino != info.st_ino:
            raise RuntimeError('Host source changed before replacement')
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)
    return True


if __name__ == '__main__':
    for argument in sys.argv[1:]:
        target = pathlib.Path(argument)
        print(target, 'patched' if patch_file(target) else 'already patched')
