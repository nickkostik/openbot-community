#!/usr/bin/env python3
"""Keep completed native workers resumable, preserving transcript WAL guards."""
import argparse
import pathlib

MARKER = '/* openbot-subagent-state-v1 */'
MODULE = '/home/box/sand-data/openbot/payload/subagent-state.cjs'


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise RuntimeError('Unknown worker state anchor; native bundle unchanged')
    return source.replace(before, after, 1)


def patch(source):
    if MARKER in source:
        if (source.count(MARKER) != 1 or source.count(MODULE) != 1
                or source.count('setPersistenceAllowed?.') != 1
                or source.count('this.openbotSubagentStore?.saveCheckpoint(state.toBinary());') != 1
                or source.count('runner.state = ConversationStateStructure.fromBinary(openbotCheckpoint);') != 1):
            raise RuntimeError('Partial worker state patch; native bundle unchanged')
        return source
    start = '  function createSubagentRunner(agentId, args, directionEpoch, loopDetection, inherited) {'
    end = '  function dispatchAutomationSubagent(args) {'
    if source.count(start) != 1 or source.count(end) != 1:
        raise RuntimeError('Unknown worker runner boundary; native bundle unchanged')
    begin, stop = source.index(start), source.index(end)
    if stop <= begin:
        raise RuntimeError('Unexpected worker runner order')
    block = source[begin:stop]
    block = replace_once(block, '    return host.constructRunner({',
        f'    {MARKER}\n    const openbotSavedWorker = require("{MODULE}").openFor(host.getConversationId(), agentId);\n'
        '    const runner = host.constructRunner({')
    block = replace_once(block, '    });\n  }\n',
        '    });\n    runner.fallbackBlobStore = openbotSavedWorker.blobStore;\n'
        '    const openbotCheckpoint = openbotSavedWorker.loadCheckpoint();\n'
        '    if (openbotCheckpoint != null) runner.state = ConversationStateStructure.fromBinary(openbotCheckpoint);\n'
        '    runner.openbotSubagentStore = openbotSavedWorker;\n    return runner;\n  }\n')
    updated = source[:begin] + block + source[stop:]
    updated = replace_once(updated, '      setLocalState: (state) => {\n        this.state = state;\n      },',
        '      setLocalState: (state) => {\n        this.openbotSubagentStore?.saveCheckpoint(state.toBinary());\n'
        '        this.state = state;\n      },')
    updated = replace_once(updated,
        '        const transcriptPersistence = options2.prewarm === true ? "disabled" : settle.noteBaseState(baseState, privacyMode);',
        '        const transcriptPersistence = options2.prewarm === true ? "disabled" : settle.noteBaseState(baseState, privacyMode);\n'
        '        host.getBlobStore().setPersistenceAllowed?.(transcriptPersistence === "enabled");')
    return updated


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=pathlib.Path)
    parser.add_argument('destination', type=pathlib.Path)
    args = parser.parse_args()
    args.destination.write_text(patch(args.source.read_text()), encoding='utf8')
