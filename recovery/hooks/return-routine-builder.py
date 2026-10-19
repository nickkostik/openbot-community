"""Capture selected routine intent and materialize a fresh paused generation."""
import json
import pathlib
SCHEMA="openbot-return-routine-authority/v1"
FIELDS=("name","prompt","schedule","trigger","triggerPresentation","provenance","sessionId")

def prepare(builder,entry,folder,indexed):
    consumer=builder.recovery.module.helper('return-routine-authority')
    terminal=entry['newId']; selected=entry.get('selectionSourceId',entry['oldId'])
    original=json.loads(builder.tail.owned(folder/'sources'/selected/'settings.json'))
    current=json.loads(builder.tail.owned(folder/'sources'/terminal/'settings.json'))
    owner=terminal;selection='current-local'
    visible=(original.get('hiddenFromSidebar'),current.get('hiddenFromSidebar'))
    if visible not in((False,True),(True,False)):
        raise RuntimeError('Return selected routine owner visibility is ambiguous')
    # A real preserved Official selection is authority only while its explicit
    # selected source is visible and the terminal Local counterpart is hidden.
    if indexed[terminal]['harness']in('box','temporal')and original.get('hiddenFromSidebar')is False and current.get('hiddenFromSidebar')is True:
        path=builder.root/'openbot-official-dispatch-state.json'
        proof=json.loads(builder.tail.owned(path))
        if (proof.get('phase')!='stock-roster-selected' or proof.get('mode')!='official' or
                proof.get('identityMapping',{}).get(terminal)!=selected):
            raise RuntimeError('Return selected routine owner is not proven')
        owner=selected;selection='official-selection'
    paths={};states={}
    source=folder/'sources'/terminal
    for path in (source/'automations').rglob('automation.json'):
        relative=str(path.relative_to(source));parts=pathlib.PurePosixPath(relative).parts
        native=json.loads(builder.tail.owned(path))
        if (len(parts)!=3 or parts[0]!='automations' or type(native.get('enabled'))is not bool or
                set(native)-set(FIELDS)-{'enabled','createdAt','lastRunAt','pendingNotices','raisedNotices','triggerDescription'}):
            raise RuntimeError('Return native routine semantics require review')
        paths[relative]=native;states[relative]=native['enabled']
    raw=json.loads(builder.tail.owned(folder/'routines'/(owner+'.json')))
    records=consumer.cloud_records(builder,consumer.cloud_response(raw))
    if owner!=terminal:states={relative:False for relative in paths}
    matched={};materialized=[];proof_ref=None;plan_ref=None;zone_ref=None
    if records:
        settings_raw=builder.tail.owned(builder.root/'settings.json')
        settings=json.loads(settings_raw);zone=settings.get('userTimeZone')
        if not isinstance(zone,str) or not zone:raise RuntimeError('Return native routine timezone is unknown')
        builder.put(folder/'time-zone-settings.json',settings_raw);zone_ref=builder.tail.reference(folder/'time-zone-settings.json')
        mapping=builder.return_mapping
        rows=[]
        for wrapper in raw['automations']:
            record=records[wrapper['automationId']]
            rid=record['id'];relative='automations/'+rid+'/automation.json'
            previous=[]
            for directory in sorted((folder/'sources').iterdir()):
                for name,kind in (('automation.json','config'),('runs.json','runs')):
                    path=directory/'automations'/rid/name
                    if path.exists():previous.append({'kind':kind,**builder.tail.reference(path)})
            mapped=builder.r.m.remap_json(record,mapping)
            record_json=builder.tail.encoded(mapped).decode()
            rows.append({'id':rid,'wrapper':{'automationId':wrapper['automationId'],'recordJson':record_json},
                         'sourceRecordSha256':builder.tail.sha(record_json.encode()),'previousFiles':previous})
        plan={'schemaVersion':1,'targetId':builder.state['destinations'][terminal],'userTimeZone':zone,'routines':rows}
        builder.tail.write(folder/'routine-plan.json',plan,True)
        proof=builder.routines(folder,plan)
        consumer.validate_codec(builder.r,folder,plan,proof,builder.tail,builder.recovery.module)
        proof_ref=builder.tail.reference(folder/'return-routine-proof.json');plan_ref=builder.tail.reference(folder/'routine-plan.json')
        # A selected cloud set is authoritative for its own switches. Retain
        # unmatched Local variants paused, with original configs/runs archived.
        if owner!=terminal:states={relative:False for relative in paths}
        for row in proof['routines']:
            relative=row['configPath'];states[relative]=row['desiredEnabled']
            matched[row['sourceAutomationId']]=relative
            materialized.extend((relative,row['runsPath']))
    authority={'schema':SCHEMA,'sourceId':terminal,'newId':builder.state['destinations'][terminal],
               'ownerId':owner,'ownerServerId':str(indexed[owner]['id']),'selection':selection,
               'localDefinitions':paths,'cloudRecords':records,'matched':matched,'states':states,
               'capturedCloud':builder.tail.reference(folder/'routines'/(owner+'.json')),
               'nativeProof':proof_ref,'nativePlan':plan_ref,'timeZoneSettings':zone_ref,
               'materialized':sorted(materialized)}
    builder.tail.write(folder/'routine-authority.json',authority,True)
    return builder.tail.reference(folder/'routine-authority.json'),states

