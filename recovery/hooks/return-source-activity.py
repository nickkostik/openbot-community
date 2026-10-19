"""Bounded read-only native Watch activity proof for a fresh return.

A missing/expired cloud overlay is not idle. Retain only activity metadata;
never publish or persist transcript bodies, credentials, or Watch frames.
"""
import json
import struct
import time
import urllib.request

MAX_FRAME = 8 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_FRAMES = 64


class ActivityUnverified(RuntimeError):
    pass


def idle_snapshot(frame, identities, now_ms):
    """Require a complete initial session snapshot and fresh explicit live rows."""
    state = frame.get('agentState') if isinstance(frame, dict) else None
    if not isinstance(state, dict) or state.get('snapshot') is not True:
        return None
    live = state.get('live', [])
    if not isinstance(live, list): raise ActivityUnverified('Cloud activity snapshot schema refused')
    sessions = {aid: [] for aid in identities}
    for row in live:
        if not isinstance(row, dict): raise ActivityUnverified('Cloud activity row schema refused')
        aid = row.get('agentId')
        if aid not in sessions: continue
        updated, stale = row.get('updatedAtMs'), row.get('staleAfterMs',0)
        # Proto JSON uses decimal strings for int64 fields; reject booleans,
        # floating values, overflows and implausible future freshness claims.
        def integer(value):
            if type(value) is int: return value
            if isinstance(value,str) and value.isascii() and value.isdigit(): return int(value)
            raise ActivityUnverified('Cloud activity timestamps are unknown')
        updated, stale = integer(updated), integer(stale)
        if stale==0:stale=90000  # Exact native fallback; expiry never proves idle.
        if not 0 < stale <= 120000 or not 0 <= now_ms-updated < stale:
            raise ActivityUnverified('Cloud activity snapshot is missing current freshness')
        session = row.get('sessionId', '')
        if not isinstance(session,str) or len(session)>1024 or any(ord(c)<32 for c in session):
            raise ActivityUnverified('Cloud source session identity is unknown')
        # These are native proto3 bool defaults on a PRESENT typed live row.
        for key in ('isRunning','isComposingMessage','isRetrying','hasRunningSubagents'):
            if row.get(key,False) is not False:
                raise ActivityUnverified('Cloud source is still running')
        if any(row.get(key) not in (None,'') for key in
               ('activity','awaiting','boxHandoffRequestId','boxHandoffInstruction','activeGroupMemberId','surfaceTaskId')):
            raise ActivityUnverified('Cloud source has pending activity or handoff state')
        sessions[aid].append(session)
    if any(not rows or len(rows)!=len(set(rows)) for rows in sessions.values()):
        raise ActivityUnverified('Cloud source has no complete fresh idle session evidence')
    return {aid:len(rows) for aid,rows in sessions.items()}


def check(recovery, identities, opener=None, now=None):
    if not identities: return {'checkedSources':0, 'freshIdle':True}
    identities=set(identities)
    if len(identities)>32: raise ActivityUnverified('Cloud activity source count exceeds bound')
    recovery.require()
    # The coordinator already initialized its existing authenticated routing
    # adapter with ListAgents. Reuse its egress/no-redirect policy and opaque
    # legitimate credential; never accept headers or endpoints from the panel.
    routing=recovery.r.m.routing
    if routing is None or recovery.r.m.H is None:
        raise ActivityUnverified('Cloud activity authenticated adapter is unavailable')
    headers={**recovery.r.m.H,'content-type':'application/connect+json','connect-protocol-version':'1',
             'connect-timeout-ms':'10000'}
    body=json.dumps({'cursors':[], 'includeUnlistedAgents':True, 'inlineBodyMaxBytes':0}).encode()
    request=urllib.request.Request('https://api2.cursor.sh/aiserver.v1.GrokBotService/WatchGrokBotTranscripts',
                                  data=struct.pack('!BI',0,len(body))+body,headers=headers)
    open_stream=opener or routing.open_no_redirect
    deadline=time.monotonic()+10; consumed=0
    def read_exact(stream,size):
        parts=[]
        while size:
            recovery.require()
            if time.monotonic()>deadline: raise ActivityUnverified('Cloud activity snapshot deadline exceeded')
            part=stream.read(size)
            if not part: raise ActivityUnverified('Cloud activity snapshot ended without settled evidence')
            parts.append(part);size-=len(part)
        return b''.join(parts)
    try:
        with open_stream(request,timeout=5) as stream:
            for _ in range(MAX_FRAMES):
                flags,size=struct.unpack('!BI',read_exact(stream,5));consumed+=size
                if flags&1 or flags&~2 or size>MAX_FRAME or consumed>MAX_TOTAL:
                    raise ActivityUnverified('Cloud activity frame bound exceeded')
                frame=json.loads(read_exact(stream,size))
                if flags&2: break
                result=idle_snapshot(frame,identities,int(time.time()*1000) if now is None else now)
                if result is not None:
                    recovery.require()
                    return {'checkedSources':len(identities),'freshIdle':True,'sessionCounts':result}
    except ActivityUnverified:
        raise
    except Exception:
        # No authenticated error/Watch body is propagated to public progress.
        raise ActivityUnverified('Cloud source activity could not be verified') from None
    raise ActivityUnverified('Cloud source activity snapshot was not received')
