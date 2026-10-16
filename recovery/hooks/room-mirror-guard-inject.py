"""Keep empty room member lists out of the local group store.

mirrorRoomMembers is the one group.json writer that lacks the non-empty guard
sealGroup and setGroupMembers both enforce. Its input comes from
toRemoteGrokBotAgent, which maps a room wire's memberAgentIds through
`(wire.memberAgentIds ?? []).filter((id) => DIR_NAME_SAFE_UUID.test(id))`, so a
room whose server-side memberAgentIds is absent - or whose ids are not
directory-safe UUIDs - arrives as []. mirrorRoomMembers then overwrites
group.json with an empty list, and readSandGroupConfig reads an empty list back
as null, so the directory silently stops being a group everywhere (roster
isGroup, isGroupSession, isGroupAgentId, setGroupMembers' early return,
isServerRoomSession). This injector gives it the same `length > 0` guard, so an
empty list is never written and the file is simply left absent/untouched.
"""
import pathlib
import subprocess
import sys

MARK = 'openbot-room-mirror-nonempty'
ANCHOR = 'function mirrorRoomMembers(agentDir, memberIds) {'
GUARD = f'\n  if (!Array.isArray(memberIds) || memberIds.length === 0) return; /* {MARK} */'


def patch(source):
    if MARK in source:
        return source
    if source.count(ANCHOR) != 1:
        raise RuntimeError('Expected one mirrorRoomMembers anchor')
    return source.replace(ANCHOR, ANCHOR + GUARD, 1)


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
        temp = path.with_name(path.name + '.room-mirror-guard.tmp')
        temp.write_text(updated)
        temp.chmod(path.stat().st_mode)
        temp.replace(path)
        print(path, 'room mirror guard patch applied')
