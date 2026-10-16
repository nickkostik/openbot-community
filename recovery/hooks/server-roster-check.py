"""Compare the active copy journal with actual owner-visible server bindings.

Local profiles and summaries are deliberately patched and cannot prove server
routing. This read-only monitor never remints bots or sends a billable probe.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import stat
import time
import uuid

HERE = pathlib.Path(__file__).parent


def read_owned(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4 * 1024 * 1024:
        raise RuntimeError('Routing authority must be an owned bounded regular file')
    return json.loads(path.read_bytes())


def authority(journal):
    expected = {}
    for row in journal['entries']:
        if row.get('phase') == 'active':
            aid, sid = row['newId'], row['serverId']
        elif row.get('sourceServerId') is not None:
            # During a complete-copy handoff the remaining visible sources
            # still need their drift hold. One activated copy must not clear
            # the alert for all the untransferred Temporal identities.
            aid, sid = row['oldId'], row['sourceServerId']
        else:
            continue
        if str(uuid.UUID(aid)) != aid or aid in expected or not isinstance(sid,str) or not sid.isascii() or not sid.isdigit() or int(sid)<=0:
            raise RuntimeError('Invalid active BOX identity authority')
        expected[aid]=sid
    return expected


def compare(expected, rows):
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
        raise RuntimeError('Unknown platform roster schema')
    indexed={}
    for row in rows:indexed.setdefault(row.get('agentId'),[]).append(row)
    counts={'box':0,'temporal':0,'missing':0,'invalidBindings':0}
    drift=[]
    for aid,sid in sorted(expected.items()):
        matches=indexed.get(aid,[])
        if not matches:counts['missing']+=1;drift.append(aid);continue
        if len(matches)!=1:counts['invalidBindings']+=1;drift.append(aid);continue
        row=matches[0]
        if row.get('id')!=sid or row.get('legacyAgentId')!=aid or row.get('viewerIsOwner') is not True:
            counts['invalidBindings']+=1;drift.append(aid)
        elif row.get('harness')=='box':counts['box']+=1
        elif row.get('harness')=='temporal':counts['temporal']+=1;drift.append(aid)
        else:counts['invalidBindings']+=1;drift.append(aid)
    return {'state':'drift' if drift else 'bound-box','expectedAgents':len(expected),'counts':counts,
            'driftAgentIds':drift,'nativeSendVerified':False}


def platform_rows():
    spec=importlib.util.spec_from_file_location('routing_monitor',HERE/'diagnose-routing.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.rpc('ListGrokBotAgents',{},module.headers())
    if 'http' in result:raise RuntimeError('Platform roster request failed')
    return result.get('agents')


def check(sd, write, now=None, fetch=platform_rows, interval_ms=120000):
    now=int(time.time()*1000) if now is None else now
    journal_path=sd/'openbot-box-remint.json'
    if not journal_path.exists():return {'state':'no-authority','nativeSendVerified':False}
    expected=authority(read_owned(journal_path))
    if not expected:return {'state':'awaiting-activation','nativeSendVerified':False}
    fingerprint=hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()
    path=sd/'openbot-server-roster.json'
    try:cached=read_owned(path)
    except FileNotFoundError:cached={}
    if cached.get('authoritySha256')==fingerprint and type(cached.get('checkedAtMs')) is int and 0<=now-cached['checkedAtMs']<interval_ms:
        return {**cached,'cached':True}
    report={**compare(expected,fetch()),'authoritySha256':fingerprint,'checkedAtMs':now}
    write(path,(json.dumps(report,indent=2)+'\n').encode())
    alert=sd/'openbot-server-roster.alert.json'
    if report['state']=='drift':write(alert,(json.dumps(report,indent=2)+'\n').encode())
    elif alert.exists():
        read_owned(alert);alert.unlink()
    return report
