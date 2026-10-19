"""Bounded observations of known default sessions, never an admission fence.

A present typed owner status is required. Missing Watch overlays alone do not
prove idle; unknown/nondefault or pending observations refuse the scoped path.
No transcript bodies or credentials are retained by this reader.
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


def live_default(row, aid, now_ms):
    if not isinstance(row,dict)or row.get('agentId')!=aid:
        raise ActivityUnverified('Cloud activity owner binding is unknown')
    if row.get('sessionId','')!='':
        raise ActivityUnverified('Cloud source has a known nondefault session')
    def integer(value):
        if type(value)is int:return value
        if isinstance(value,str)and value.isascii()and value.isdigit():return int(value)
        raise ActivityUnverified('Cloud activity timestamps are unknown')
    updated=integer(row.get('updatedAtMs'));stale=integer(row.get('staleAfterMs',0))
    if stale==0:stale=90000  # Exact native duration fallback, not terminal evidence.
    if not 0<stale<=120000 or not 0<=now_ms-updated<stale:
        raise ActivityUnverified('Cloud activity snapshot is missing current freshness')
    for key in('isRunning','isComposingMessage','isRetrying','hasRunningSubagents'):
        if row.get(key,False)is not False:raise ActivityUnverified('Cloud source is still running')
    for key in('activity','awaiting'):
        if key in row:raise ActivityUnverified('Cloud source has pending activity or handoff state')
    for key in('boxHandoffRequestId','boxHandoffInstruction','activeGroupMemberId','surfaceTaskId'):
        if key in row and(not isinstance(row[key],str)or row[key]):
            raise ActivityUnverified('Cloud source has pending activity or handoff state')


def status_sample(response,identities,now_ms):
    rows=response.get('statuses')if isinstance(response,dict)else None
    if not isinstance(rows,list)or len(rows)!=len(identities):
        raise ActivityUnverified('Cloud default-session status is missing or incomplete')
    seen=set()
    for row in rows:
        if not isinstance(row,dict)or row.get('agentId')not in identities or row['agentId']in seen:
            raise ActivityUnverified('Cloud default-session status identity differs')
        seen.add(row['agentId'])
        # isRunning is a non-optional proto3 bool on a PRESENT typed status.
        # Missing status/live containers are deliberately different cases.
        if row.get('isRunning',False)is not False:
            raise ActivityUnverified('Cloud source is still running')
        if 'live'in row:live_default(row['live'],row['agentId'],now_ms)
    if seen!=set(identities):raise ActivityUnverified('Cloud default-session status is missing or incomplete')


def known_metadata(frame,identities):
    if not isinstance(frame,dict):return
    roster=frame.get('rosterChanged')
    if isinstance(roster,dict)and roster.get('agentId')in identities and isinstance(roster.get('agent'),dict):
        if roster['agent'].get('viewerSessionId','')!='':
            raise ActivityUnverified('Cloud source has a known nondefault viewer session')
    computer=frame.get('computerActions')
    if isinstance(computer,dict)and any(isinstance(action,dict)and action.get('agentId')in identities
            for action in computer.get('actions',[])):
        raise ActivityUnverified('Cloud source has pending computer activity')
    for key in('rows','cleared','cursorTooOld','turnFailed'):
        if key not in frame:continue
        value=frame[key]
        if not isinstance(value,dict):raise ActivityUnverified('Cloud session metadata schema is unknown')
        if value.get('agentId')not in identities:continue
        if value.get('sessionId','')!='':raise ActivityUnverified('Cloud source has a known nondefault session')
        if key=='turnFailed':raise ActivityUnverified('Cloud source has an unsettled failed turn observation')


def default_snapshot(frame,identities,now_ms):
    known_metadata(frame,identities)
    state=frame.get('agentState')if isinstance(frame,dict)else None
    if not isinstance(state,dict)or state.get('snapshot')is not True:return None
    live=state.get('live',[])
    if not isinstance(live,list):raise ActivityUnverified('Cloud activity snapshot schema refused')
    counts={aid:0 for aid in identities}
    for row in live:
        if not isinstance(row,dict):raise ActivityUnverified('Cloud activity row schema refused')
        aid=row.get('agentId')
        if aid not in counts:continue
        live_default(row,aid,now_ms);counts[aid]+=1
        if counts[aid]>1:raise ActivityUnverified('Cloud source has ambiguous default session evidence')
    return counts


def check(recovery,identities,opener=None,now=None):
    identities=set(identities)
    def millis():return int(time.time()*1000)if now is None else now
    def proof(counts):return {'checkedSources':len(identities),'scope':'known-default-session','knownSessionId':'',
                             'observedNotRunning':True,'admissionFenced':False,'sampledAtMs':millis(),'sessionCounts':counts}
    if not identities:return proof({})
    if len(identities)>32:raise ActivityUnverified('Cloud activity source count exceeds bound')
    recovery.require()
    routing=recovery.r.m.routing
    if routing is None or recovery.r.m.H is None:
        raise ActivityUnverified('Cloud activity authenticated adapter is unavailable')
    request_body={'agentIds':sorted(identities)}
    status_sample(recovery.r.m.rpc('ListGrokBotAgentStatuses',request_body),identities,millis())
    headers={**recovery.r.m.H,'content-type':'application/connect+json','connect-protocol-version':'1',
             'connect-timeout-ms':'10000'}
    body=json.dumps({'cursors':[], 'includeUnlistedAgents':True, 'inlineBodyMaxBytes':0}).encode()
    request=urllib.request.Request('https://api2.cursor.sh/aiserver.v1.GrokBotService/WatchGrokBotTranscripts',
                                  data=struct.pack('!BI',0,len(body))+body,headers=headers)
    open_stream=opener or routing.open_no_redirect;deadline=time.monotonic()+10;consumed=0
    def read_exact(stream,size):
        parts=[]
        while size:
            recovery.require()
            if time.monotonic()>deadline:raise ActivityUnverified('Cloud activity snapshot deadline exceeded')
            part=stream.read(size)
            if not part:raise ActivityUnverified('Cloud activity snapshot ended without settled evidence')
            parts.append(part);size-=len(part)
        return b''.join(parts)
    try:
        with open_stream(request,timeout=5)as stream:
            for _ in range(MAX_FRAMES):
                flags,size=struct.unpack('!BI',read_exact(stream,5));consumed+=size
                if flags&1 or flags&~2 or size>MAX_FRAME or consumed>MAX_TOTAL:
                    raise ActivityUnverified('Cloud activity frame bound exceeded')
                frame=json.loads(read_exact(stream,size))
                if flags&2:break
                result=default_snapshot(frame,identities,millis())
                if result is not None:
                    status_sample(recovery.r.m.rpc('ListGrokBotAgentStatuses',request_body),identities,millis())
                    recovery.require();return proof(result)
    except ActivityUnverified:raise
    except Exception:raise ActivityUnverified('Cloud source activity could not be verified')from None
    raise ActivityUnverified('Cloud source activity snapshot was not received')
