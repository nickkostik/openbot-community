"""Patch the BOX turn gates as a checked transformation of the native source."""
import os
import pathlib
import re
import subprocess
import sys
import tempfile

MARK = 'openbot-force-local-infer'
TURN_MARK = 'openbot-local-box-turn-gate-v1'
TURN_ANCHOR = 'const boxHarnessTurnGate = {'
LEGACY_TURN_REPLACEMENT = '''const boxHarnessTurnGate = {
      isBlocked: () => {
        try {
          const fs = require("node:fs");
          if (fs.existsSync("/home/box/sand-data/openbot-local-only.enable") &&
              fs.readFileSync("/home/box/sand-data/openbot-mode", "utf8").trim() === "custom") return false;
        } catch {}
        return true;
      }, /* openbot-local-box-turn-gate-v1 */'''
BOTH_MODE_TURN_REPLACEMENT = LEGACY_TURN_REPLACEMENT.replace(
    '          if (fs.existsSync("/home/box/sand-data/openbot-local-only.enable") &&\n'
    '              fs.readFileSync("/home/box/sand-data/openbot-mode", "utf8").trim() === "custom") return false;',
    '          if (fs.existsSync("/home/box/sand-data/openbot-local-only.enable")) {\n'
    '            const mode = fs.readFileSync("/home/box/sand-data/openbot-mode", "utf8").trim();\n'
    '            if (mode === "custom" || mode === "official") return false;\n'
    '          }')
TURN_REPLACEMENT = LEGACY_TURN_REPLACEMENT


def patch_hardcoded_turn_gate(source):
    """New hosts retire BOX turns without consulting the feature gate reader."""
    if TURN_MARK in source:
        if source.count(BOTH_MODE_TURN_REPLACEMENT) == 1:
            source = source.replace(BOTH_MODE_TURN_REPLACEMENT, TURN_REPLACEMENT, 1)
        if source.count(TURN_MARK) != 1 or source.count(TURN_REPLACEMENT) != 1:
            raise RuntimeError('BOX turn gate patch drifted or is ambiguous')
        if source.count(TURN_ANCHOR) != 1:
            raise RuntimeError('Expected one BOX turn gate implementation')
        return source
    if source.count(TURN_ANCHOR) != 1:
        raise RuntimeError('Expected one BOX turn gate implementation')
    anchor = re.compile(re.escape(TURN_ANCHOR) + r'\s*isBlocked: \(\) => true,')
    matches = list(anchor.finditer(source))
    if len(matches) == 1:
        match = matches[0]
        return source[:match.start()] + TURN_REPLACEMENT + source[match.end():]
    # Older bundles still consult the feature gate patched below. Accept only
    # that known shape; an unfamiliar gate must not be reported as repaired.
    legacy = re.compile(re.escape(TURN_ANCHOR) +
        r'\s*isBlocked: \(\) => [A-Za-z0-9_$]+(?:\.[A-Za-z0-9_$]+)*'
        r'\.checkFeatureGate\("grok_bot_box_harness_turns_blocked"\),')
    if len(list(legacy.finditer(source))) != 1:
        raise RuntimeError('Unsupported BOX turn gate implementation')
    return source


def patch(source):
    source = patch_hardcoded_turn_gate(source)
    anchor = re.compile(r'checkFeatureGate\(([A-Za-z0-9_$]+), ([A-Za-z0-9_$]+)\) \{')
    matches = list(anchor.finditer(source))
    if len(matches) != 1:
        raise RuntimeError('Expected one feature gate implementation')
    match = matches[0]
    name = match.group(1)
    legacy_inject = ('\n    if (%s === "grok_bot_box_harness_turns_blocked" || '
              '%s === "grok_bot_temporal_harness" || %s === "sand_create_temporal_agents") '
              '{ return false; /* %s */ }\n' % (name, name, name, MARK))
    inject = legacy_inject.replace('if (', 'if (openbotLocalFleetEnabled() && (', 1).replace(
        ') { return false;', ')) { return false;', 1)
    if MARK in source:
        if source.startswith(legacy_inject, match.end()):
            source = source[:match.end()] + inject + source[match.end() + len(legacy_inject):]
        if source.count(MARK) != 1 or not source.startswith(inject, match.end()):
            raise RuntimeError('BOX feature gate patch drifted or is ambiguous')
        return source
    return source[:match.end()] + inject + source[match.end():]


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        path = pathlib.Path(arg)
        before = path.read_text(encoding='utf8')
        updated = patch(before)
        if before == updated:
            continue
        checked = subprocess.run(['node', '--check'], input=updated, text=True, capture_output=True)
        if checked.returncode:
            raise RuntimeError('BOX gate syntax check failed')
        fd, name = tempfile.mkstemp(prefix='.openbot-gate-', suffix='.cjs', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf8') as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            pathlib.Path(name).chmod(path.stat().st_mode)
            if path.read_text(encoding='utf8') != before:
                raise RuntimeError('Native bundle changed while staging the gate')
            os.replace(name, path)
        finally:
            pathlib.Path(name).unlink(missing_ok=True)
