"""Pin selected routine switches before a fresh return pauses any scheduler.

Native-proven paused definitions and exact selected switches supersede stale
copy flags. Every prior definition and raw run-history variant remains retained.
"""
import json
import base64
import pathlib
import uuid
import re
import math
import hashlib

def validator_digest(raw):return hashlib.sha256(raw).hexdigest()

SCHEMA='openbot-return-routine-authority/v1'
FIELDS=('name','prompt','schedule','trigger','triggerPresentation','provenance','sessionId')
BOOKKEEPING={'id','isEnabled','createdAt','lastRunAt','nextRunAt','runs','filePath','triggerDescription','pendingNotices','raisedNotices'}


def definitions(record):
    value={key:record[key]for key in FIELDS if key in record}
    if isinstance(value.get('trigger'),dict) and value['trigger'].get('type')=='cron':
        value['trigger']={key:item for key,item in value['trigger'].items()if key!='firesAt'}
    return value


def cloud_records(r,wrappers):
    if not isinstance(wrappers,list):raise RuntimeError('Return routine list schema is unknown')
    result={}
    for wrapper in wrappers:
        rid=wrapper['automationId']
        if not isinstance(rid,str) or str(uuid.UUID(rid))!=rid:raise RuntimeError('Return cloud routine ID is unknown')
        record=json.loads(wrapper['recordJson'])
        if (rid in result or not isinstance(record,dict) or type(record.get('isEnabled'))is not bool or
                set(record)-set(FIELDS)-BOOKKEEPING or not isinstance(record.get('id'),str)):
            raise RuntimeError('Return cloud routine definition requires review')
        result[rid]=record
    return result


def cloud_response(response):
    # Native protobuf JSON omits an empty repeated field. Only the known
    # response object may use that default; error/unknown envelopes never do.
    if not isinstance(response,dict)or set(response)-{'automations'}:
        raise RuntimeError('Return routine response schema is unknown')
    rows=response.get('automations',[])
    if not isinstance(rows,list):raise RuntimeError('Return routine list schema is unknown')
    return rows


def validate_codec(r,folder,plan,proof,tail,module,plan_path=None):
    if (proof.get('schema')!='openbot-return-routine-codec/v1' or
            proof.get('engineSha256')!=tail.REVIEWED_HOST_SHA or proof.get('targetId')!=plan['targetId'] or
            proof.get('bridgeSha256')!=tail.sha(tail.owned(pathlib.Path(__file__).with_name('return-routine-codec.mjs'))) or
            proof.get('sourcePlanFileSha256')!=tail.sha(tail.owned(plan_path or folder/'routine-plan.json')) or
            any(proof.get(key)is not True for key in ('allPaused','authoredSemanticsVerified','previousArchivesUnchanged')) or
            any(proof.get(key)is not False for key in ('sourceUpdated','liveApplied','historicalActionsReplayed')) or
            any(proof.get(key)!=0 for key in ('providerCalls','networkAttempts','toolAttempts'))):
        raise RuntimeError('Return native routine codec proof differs')
    output=proof.get('routines')
    if not isinstance(output,list)or len(output)!=len(plan['routines']):raise RuntimeError('Return routine proof count differs')
    seen=set()
    for source,row in zip(plan['routines'],output):
        rid=source['id']
        if not isinstance(rid,str)or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}',rid):raise RuntimeError('Return routine native ID is unsafe')
        relative='automations/'+rid+'/automation.json';runs='automations/'+rid+'/runs.json'
        if (rid in seen or row.get('id')!=rid or row.get('configPath')!=relative or row.get('runsPath')!=runs or
                row.get('sourceAutomationId')!=source['wrapper']['automationId'] or
                row.get('sourceRecordSha256')!=source['sourceRecordSha256'] or row.get('previousFiles')!=source['previousFiles'] or
                row.get('desiredEnabled')is not json.loads(source['wrapper']['recordJson'])['isEnabled'] or
                any(row.get(key)is not True for key in ('allPaused','fullHistoricalRunsRetained','nativeSerdeVerified','nativeStoreVerified')) or
                row.get('configSha256')!=tail.sha(tail.owned(folder/relative)) or
                row.get('runsSha256')!=tail.sha(tail.owned(folder/runs)) or
                json.loads(tail.owned(folder/relative)).get('enabled')is not False):
            raise RuntimeError('Return routine output does not match captured authority')
        projection=row.get('nativeVisibleProjection');encoded=row.get('nativeVisibleProjectionJson')
        if (not isinstance(encoded,str)or not isinstance(projection,list)or len(projection)>20 or
                json.loads(encoded)!=projection or validator_digest(encoded.encode())!=row.get('nativeVisibleProjectionSha256')or
                row.get('nativeVisibleRunCount')!=len(projection)or row.get('nativeRunHistoryLimit')!=20):
            raise RuntimeError('Return native recent-history projection differs')
        for previous in source['previousFiles']:
            if tail.sha(tail.owned(pathlib.Path(previous['path'])))!=previous['sha256']:
                raise RuntimeError('Return previous routine archive changed or is missing')
        seen.add(rid)


def validate_prequiesced(r,journal,entry,authority,validator):
    evidence=authority.get('quiescenceEvidence')
    if evidence is None:return False
    required={'capture','pausedReadback','quiesceJournal'};local_pair={'localCapture','localPausedReadback'}
    if not isinstance(evidence,dict) or set(evidence)not in(required,required|local_pair):
        raise RuntimeError('Return pre-quiesced evidence schema is unknown')
    capture=json.loads(validator.referenced(journal['backup'],evidence['capture']))
    paused=json.loads(validator.referenced(journal['backup'],evidence['pausedReadback']))
    validator.referenced(journal['backup'],evidence['quiesceJournal'])  # Preserve the full operation context.
    cloud=json.loads(validator.referenced(journal['backup'],authority['capturedCloud']))
    row=capture.get('sourceRow',{})
    if (capture.get('schema')!='openbot-retiring-routine-owner/v1' or capture.get('agentId')!=authority['ownerId'] or
            capture.get('destinationId')!=entry['newId'] or capture.get('cloudAuthority')!='server' or
            row.get('agentId')!=authority['ownerId'] or row.get('legacyAgentId')!=authority['ownerId'] or
            row.get('viewerIsOwner')is not True or str(row.get('id'))!=authority['ownerServerId'] or
            capture.get('cloudRaw')!=cloud):
        raise RuntimeError('Return pre-quiesced capture belongs to another owner or destination')
    original=cloud_records(r,cloud_response(cloud))
    desired={rid:record['isEnabled']for rid,record in original.items()}
    if original!=authority['cloudRecords'] or capture.get('originalDesiredCloud')!=desired:
        raise RuntimeError('Return pre-quiesced desired switches differ from immutable capture')
    if not isinstance(paused,dict)or 'cloud'not in paused:
        raise RuntimeError('Return paused routine response is missing')
    readback=cloud_records(r,cloud_response(paused['cloud']))
    if set(readback)!=set(original):raise RuntimeError('Return paused cloud readback lost a routine')
    for rid,record in readback.items():
        if record['isEnabled']is not False or r.routine_semantics(record)!=r.routine_semantics(original[rid]):
            raise RuntimeError('Return paused cloud readback changed authored state')
    local_required=authority['sourceId']!=authority['ownerId'] and any(
        value.get('enabled')is True for value in authority['localDefinitions'].values())
    if local_required and not local_pair.issubset(evidence):
        raise RuntimeError('Return pre-quiesced Local switches require their own capture and readback')
    if local_pair.issubset(evidence):
        local=json.loads(validator.referenced(journal['backup'],evidence['localCapture']))
        local_paused=json.loads(validator.referenced(journal['backup'],evidence['localPausedReadback']))
        source=local.get('sourceRow')or{};profile=local.get('profile')or{}
        if (local.get('schema')!='openbot-retiring-routine-owner/v1' or local.get('agentId')!=entry['oldId'] or
                local.get('destinationId')!=entry['newId'] or source.get('viewerIsOwner')is not True or
                source.get('agentId')!=entry['oldId'] or source.get('legacyAgentId')!=entry['oldId'] or
                str(source.get('id'))!=entry['sourceServerId'] or str(profile.get('serverId'))!=entry['sourceServerId']):
            raise RuntimeError('Return pre-quiesced Local capture binding differs')
        def local_records(rows):
            if not isinstance(rows,list)or len(rows)>512:raise RuntimeError('Return Local pause schema is unknown')
            result={}
            for value in rows:
                relative=value['nativePath'];parts=pathlib.PurePosixPath(relative).parts
                raw=base64.b64decode(value['rawBase64'],validate=True)
                if (len(parts)!=3 or parts[0]!='automations' or parts[2]!='automation.json' or relative in result or
                        validator.digest(raw)!=value['sha256'] or json.loads(raw)!=value['record'] or
                        type(value.get('enabled'))is not bool or value['record'].get('enabled')is not value['enabled']):
                    raise RuntimeError('Return Local pause payload differs from exact captured bytes')
                result[relative]=value['record']
            return result
        before=local_records(local.get('localRoutines'));after=local_records(local_paused.get('local'))
        if before!=authority['localDefinitions'] or set(after)!=set(before) or local.get('originalDesiredLocal')!={k:v['enabled']for k,v in before.items()}:
            raise RuntimeError('Return Local pause switches differ from immutable original authority')
        for relative,value in after.items():
            if value.get('enabled')is not False or {k:v for k,v in value.items()if k!='enabled'}!={k:v for k,v in before[relative].items()if k!='enabled'}:
                raise RuntimeError('Return Local pause readback changed authored state')
    return True


def runtime_history(actual,baseline,projection):
    """Accept the native recent window, never an arbitrary subset of old rows."""
    keys={'id','requestId','trigger','startedAt','finishedAt','status','detail','errorKind','event','coalescedRunIds'}
    def stamp(value):return type(value)in(int,float)and math.isfinite(value)and 0<=value<=9007199254740991
    def record(value):
        if (not isinstance(value,dict)or set(value)-keys or
                not isinstance(value.get('id'),str)or not value['id']or
                value.get('trigger')not in('manual','event','schedule')or value.get('status')not in('ok','error','running')or
                not stamp(value.get('startedAt'))or not(value.get('finishedAt')is None or stamp(value['finishedAt']))):return False
        if value.get('status')=='running'and value.get('finishedAt')is not None:return False
        if value.get('status')!='running'and(value.get('finishedAt')is None or value['finishedAt']<value['startedAt']):return False
        for key in('requestId','detail','errorKind'):
            if key in value and value[key]is not None and not isinstance(value[key],str):return False
        if 'coalescedRunIds'in value and(not isinstance(value['coalescedRunIds'],list)or
                not all(isinstance(item,str)for item in value['coalescedRunIds'])):return False
        return True
    def window(rows):
        return (isinstance(rows,list)and len(rows)<=20 and all(isinstance(value,dict)and
                isinstance(value.get('id'),str)and type(value.get('startedAt'))in(int,float)and
                math.isfinite(value['startedAt'])for value in rows)and
                all(rows[i]['startedAt']>=rows[i+1]['startedAt']for i in range(len(rows)-1)))
    if not window(actual)or not window(projection):return False
    prior={}
    for index,value in enumerate(projection):prior.setdefault(value['id'],[]).append((index,value))
    updates={};new=[];new_ids=set()
    head=max((value['startedAt']for value in projection),default=0)
    # A previously archived old ID cannot be invented as a fresh observation.
    archived={value.get('id')for value in baseline if isinstance(value,dict)}
    for value in actual:
        candidates=prior.get(value['id'])
        if candidates is None:
            if not record(value)or value['id']in archived or value['id']in new_ids or value['startedAt']<head:return False
            new_ids.add(value['id']);new.append(value);continue
        def stable(row):return {key:item for key,item in row.items()if key not in('status','finishedAt','detail','errorKind')}
        matched=None
        for index,old in candidates:
            if index in updates:continue
            if value==old or(record(value)and old.get('status')=='running'and value['status']in('ok','error')and stable(old)==stable(value)):
                matched=index;break
        if matched is None:return False
        updates[matched]=value
    expected=new+[updates.get(index,value)for index,value in enumerate(projection)]
    expected.sort(key=lambda value:value['startedAt'],reverse=True)
    return actual==expected[:20]


def authority_for(r,journal,entry,validator):
    reference=entry.get('returnRoutineAuthority')
    if not reference:return None
    authority=json.loads(validator.referenced(journal['backup'],reference))
    if (authority.get('schema')!=SCHEMA or authority.get('sourceId')!=entry['oldId'] or
            authority.get('newId')!=entry['newId'] or authority.get('states')!=entry.get('activationAutomationStates')):
        raise RuntimeError('Return routine switch authority belongs to another generation')
    captured=json.loads(validator.referenced(journal['backup'],authority['capturedCloud']))
    if cloud_records(r,cloud_response(captured))!=authority['cloudRecords']:
        raise RuntimeError('Return routine desired state differs from immutable source capture')
    native=authority.get('nativeProof');plan_ref=authority.get('nativePlan')
    if bool(native)!=bool(plan_ref):raise RuntimeError('Return routine native plan/proof pairing differs')
    materialized=authority.get('materialized',[])
    if native:
        proof=json.loads(validator.referenced(journal['backup'],native))
        plan=json.loads(validator.referenced(journal['backup'],plan_ref))
        if plan.get('targetId')!=entry['newId']:raise RuntimeError('Return routine proof targets another destination')
        zone_ref=authority.get('timeZoneSettings')
        if not zone_ref:raise RuntimeError('Return routine native timezone evidence is missing')
        zone=json.loads(validator.referenced(journal['backup'],zone_ref)).get('userTimeZone')
        current_zone=json.loads(validator.checked(r.m.SD,r.m.SD/'settings.json')).get('userTimeZone')
        if not isinstance(zone,str)or not zone or zone!=plan.get('userTimeZone')or current_zone!=zone:
            raise RuntimeError('Return routine native timezone changed or differs from proof')
        import importlib.util
        path=pathlib.Path(__file__).with_name('history-tail-repair.py')
        spec=importlib.util.spec_from_file_location('return_routine_tail_validator',path)
        tail=importlib.util.module_from_spec(spec);spec.loader.exec_module(tail)
        validate_codec(r,pathlib.Path(native['path']).parent,plan,proof,tail,None,pathlib.Path(plan_ref['path']))
        expected=sorted(path for row in proof['routines']for path in(row['configPath'],row['runsPath']))
        if sorted(materialized)!=expected:raise RuntimeError('Return materialized paths differ from native proof')
        mapping=r.module_identity_mapping(journal)if hasattr(r,'module_identity_mapping')else tail.module('remint-box-fleet').identity_mapping(journal)
        sources=authority['cloudRecords']
        proven_paths={row['configPath']for row in proof['routines']}
        if (set(authority['states'])!=set(authority['localDefinitions'])|proven_paths or
                any(type(value)is not bool for value in authority['states'].values()) or
                authority.get('matched')!={row['sourceAutomationId']:row['configPath']for row in proof['routines']}):
            raise RuntimeError('Return routine switches or source path mapping differ from proven scope')
        for relative,value in authority['localDefinitions'].items():
            if relative in proven_paths:continue
            expected=value['enabled']if authority['ownerId']==authority['sourceId']else False
            if authority['states'][relative]is not expected:
                raise RuntimeError('Return unselected Local routine switch has no authority')
        if {row['wrapper']['automationId']for row in plan['routines']}!=set(sources):
            raise RuntimeError('Return routine plan source roster differs')
        for planned,output in zip(plan['routines'],proof['routines']):
            source=sources[planned['wrapper']['automationId']]
            if json.loads(planned['wrapper']['recordJson'])!=r.m.remap_json(source,mapping):
                raise RuntimeError('Return routine authored definition differs from canonical source mapping')
            if authority['states'].get(output['configPath'])is not source['isEnabled']:
                raise RuntimeError('Return routine desired switch is not exact source authority')
        # Files installed on N must remain exactly the native-proven paused
        # candidate until activation; later restore toggles only enabled.
        for output in proof['routines']:
            for key,hash_key in(('configPath','configSha256'),('runsPath','runsSha256')):
                current=r.m.SD/'agents'/entry['newId']/output[key]
                raw=validator.checked(r.m.SD,current)
                if key=='configPath' and entry.get('phase')=='active':
                    candidate=json.loads(validator.checked(journal['backup'],pathlib.Path(native['path']).parent/output[key]))
                    value=json.loads(raw)
                    def authored(record):
                        mapped={key:item for key,item in record.items()if key not in('enabled','pendingNotices','raisedNotices')}
                        return r.routine_semantics(mapped),mapped.get('createdAt')
                    if authored(value)!=authored(candidate):
                        raise RuntimeError('Return active routine authored config changed before restore')
                elif key=='runsPath' and entry.get('phase')=='active':
                    baseline=json.loads(validator.checked(journal['backup'],pathlib.Path(native['path']).parent/output[key]))
                    actual=json.loads(raw)
                    if actual!=baseline and not runtime_history(actual,baseline,output.get('nativeVisibleProjection')):
                        raise RuntimeError('Return active routine history is not a lawful native recent window')
                elif validator.digest(raw)!=output[hash_key]:
                    raise RuntimeError('Return installed native routine differs from proof')
    elif materialized or authority['cloudRecords']:
        raise RuntimeError('Return materialized or captured cloud routines have no native proof')
    elif authority['states']!={relative:value.get('enabled')if authority['ownerId']==authority['sourceId']else False
                              for relative,value in authority['localDefinitions'].items()}:
        raise RuntimeError('Return Local routine switches differ from captured authority')
    return authority


def materialized_paths(r,journal,entry,validator):
    authority=authority_for(r,journal,entry,validator)
    return set(authority.get('materialized',[]))if authority else set()


def validate(r,journal,entry,validator):
    authority=authority_for(r,journal,entry,validator)
    if authority is None:return
    prequiesced=validate_prequiesced(r,journal,entry,authority,validator)
    paused=entry.get('returnRoutinePauseIntent')is True or prequiesced
    r.checked_routine_states(entry['newId'],authority['states'])
    destination=r.m.SD/'agents'/entry['newId']
    copied={str(path.relative_to(destination)):json.loads(validator.checked(r.m.SD,path))
            for path in(destination/'automations').rglob('automation.json')}
    if entry.get('phase')!='active'and(set(copied)!=set(authority['states'])or
            any(value.get('enabled')is not False for value in copied.values())):
        raise RuntimeError('Return destination routine roster must be exact and paused before activation')
    root=r.m.SD/'agents'/entry['oldId']
    paths={str(path.relative_to(root)):path for path in(root/'automations').rglob('automation.json')}
    if set(paths)!=set(authority['localDefinitions']):raise RuntimeError('Return routine list changed after intent capture')
    for relative,path in paths.items():
        native=json.loads(validator.checked(r.m.SD,path))
        captured=authority['localDefinitions'][relative]
        if {k:v for k,v in native.items()if k!='enabled'}!={k:v for k,v in captured.items()if k!='enabled'}:
            raise RuntimeError('Return routine authored state changed after capture')
        if native.get('enabled')!=captured['enabled'] and not(paused and native.get('enabled')is False):
            raise RuntimeError('Return local routine switch changed before quiescence')
    owner=authority['ownerId'];rows=[row for row in r.rows()if row.get('agentId')==owner]
    if (len(rows)!=1 or rows[0].get('viewerIsOwner')is not True or rows[0].get('legacyAgentId')!=owner or
            str(rows[0].get('id'))!=authority['ownerServerId']):
        raise RuntimeError('Return routine selected owner binding changed')
    request={'agentId':owner}
    if authority.get('timeZoneSettings'):
        request['timeZone']=json.loads(validator.referenced(journal['backup'],authority['timeZoneSettings']))['userTimeZone']
    fresh=cloud_records(r,cloud_response(r.m.rpc('ListGrokBotAgentAutomations',request)))
    if set(fresh)!=set(authority['cloudRecords']):raise RuntimeError('Return selected cloud routine list changed')
    for rid,record in fresh.items():
        if prequiesced and record['isEnabled']is not False:raise RuntimeError('Return pre-quiesced cloud source became enabled')
        captured=authority['cloudRecords'][rid]
        if r.routine_semantics(record)!=r.routine_semantics(captured):
            raise RuntimeError('Return selected cloud routine definition changed')
        if record['isEnabled']!=captured['isEnabled'] and not(paused and record['isEnabled']is False):
            raise RuntimeError('Return selected cloud routine switch changed before quiescence')
