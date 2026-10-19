#!/usr/bin/env python3
"""Validate explicitly reviewed, immutable transcript and SDK merge baselines.

This authorizes a staged copy, never changes a source or replays a tool. Evidence
is pinned in the migration journal; later new turns may extend its transcript.
"""
import base64
import contextlib
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import stat

SCHEMA='openbot-history-merge-receipt/v1'
DELTA_SCHEMA='openbot-history-merge-receipt/incremental-v1'
MAX_RECEIPT_DEPTH=16
MAX_FILE=128*1024*1024
MAX_RECORDS=200000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def sha(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise RuntimeError('Invalid evidence SHA256')
    return value


def exact(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys.split()):
        raise RuntimeError('Unknown history merge evidence schema')
    return value


def checked(root,path,limit=MAX_FILE):
    """Check every component before reading; do not resolve away symlinks."""
    root=pathlib.Path(root);path=pathlib.Path(path)
    if not root.is_absolute() or not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Evidence path must be absolute and contained')
    try: relative=path.relative_to(root)
    except ValueError: raise RuntimeError('Evidence path escapes journal backup')
    for ancestor in root.parents:
        if ancestor.is_symlink():raise RuntimeError('Evidence path is symlinked')
    current=root
    for part in [None,*relative.parts]:
        if part is not None:current=current/part
        info=current.lstat()
        if info.st_uid!=os.getuid() or (current!=path and not stat.S_ISDIR(info.st_mode)):
            raise RuntimeError('Evidence path is foreign or symlinked')
        if current==path and (not stat.S_ISREG(info.st_mode) or info.st_size>limit):
            raise RuntimeError('Evidence must be a bounded owned regular file')
    raw=path.read_bytes()
    if len(raw)>limit:raise RuntimeError('Evidence grew beyond its bound')
    return raw


def referenced(root,reference):
    exact(reference,'path sha256')
    raw=checked(root,reference['path'])
    if digest(raw)!=sha(reference['sha256']):
        raise RuntimeError('Immutable merge evidence changed')
    return raw


def db_entries(path,immutable=True):
    with contextlib.closing(sqlite3.connect('file:'+str(path)+'?mode=ro'+('&immutable=1' if immutable else ''),uri=True)) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise RuntimeError('Merge baseline database integrity failed')
        records=[json.loads(row[0]) for row in conn.execute('SELECT entry FROM transcript_entries ORDER BY seq LIMIT ?', (MAX_RECORDS+1,))]
    if len(records)>MAX_RECORDS:raise RuntimeError('Merge transcript exceeds evidence bound')
    ids=[value.get('id') for value in records]
    if any(not isinstance(value,str) or not value for value in ids) or len(set(ids))!=len(ids):
        raise RuntimeError('Merge transcript identifiers are ambiguous')
    return records


def payload(r,value,mapping,without_id=False):
    normalized=r.merger.remap_transcript_owner(value,mapping)
    if without_id:normalized={k:v for k,v in normalized.items() if k!='id'}
    return json.dumps(normalized,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def source_records(root,source):
    exact(source,'agentId kind path sha256 generation sdkRootSha256 blobSnapshot')
    raw=referenced(root,{k:source[k] for k in ('path','sha256')})
    generation=source['generation']
    if generation is not None and (type(generation) not in (int,str) or len(str(generation))>128):
        raise RuntimeError('Invalid captured source generation')
    if source['sdkRootSha256'] is not None:sha(source['sdkRootSha256'])
    if source['kind']=='sqlite':
        referenced(root,source['blobSnapshot'])
        with contextlib.closing(sqlite3.connect('file:'+str(source['blobSnapshot']['path'])+'?mode=ro&immutable=1',uri=True)) as conn:
            source_blobs={row[0]:digest(row[1]) for row in conn.execute('SELECT id,data FROM blobs')}
        with contextlib.closing(sqlite3.connect('file:'+str(source['path'])+'?mode=ro&immutable=1',uri=True)) as conn:
            metadata=conn.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
        if not metadata:raise RuntimeError('Source SDK metadata is absent')
        root_slot=json.loads(bytes.fromhex(metadata[0])).get('latestRootBlobId')
        if source['sdkRootSha256'] is None:
            if source_blobs:raise RuntimeError('Absent source SDK requires an empty source blob store')
        elif source_blobs.get(root_slot)!=source['sdkRootSha256']:
            raise RuntimeError('Source SDK root differs from its immutable snapshot')
        return db_entries(source['path'])
    if source['kind']!='platform':raise RuntimeError('Unknown source evidence kind')
    if source['blobSnapshot'] is not None:raise RuntimeError('Platform source cannot claim a local SDK snapshot')
    captured=json.loads(raw)
    if isinstance(captured,dict):
        exact(captured,'generation entries')
        if captured['generation']!=generation:raise RuntimeError('Captured generation does not match receipt')
        captured=captured['entries']
    if not isinstance(captured,list) or len(captured)>MAX_RECORDS:
        raise RuntimeError('Invalid platform transcript capture')
    result=[json.loads(base64.b64decode(item['body'],validate=True)) for item in captured]
    if len({v['id'] for v in result})!=len(result):raise RuntimeError('Ambiguous platform transcript capture')
    return result



def chat_event(value):
    kind=value.get('kind')
    if kind=='message' and value.get('role') in ('user','assistant'):return True
    if kind in ('send-message','user-attachment'):return True
    if kind in ('voice-call','spend-initiation','event','feedback','notice'):return False
    raise RuntimeError('Unclassified event kind requires a reviewed ordering policy')

def validate(r,journal,entry,reference=None,_depth=0):
    if _depth>MAX_RECEIPT_DEPTH:raise RuntimeError('History merge receipt chain exceeds bound')
    reference=reference or entry.get('historyMergeReceipt')
    if not reference:raise RuntimeError('Divergent same-ID history requires a reviewed immutable merge receipt')
    root=pathlib.Path(journal['backup'])
    receipt=json.loads(referenced(root,reference))
    incremental=receipt.get('schema')==DELTA_SCHEMA
    fields='schema oldId newId selectionSourceId backup baseline sources projections sourceOrders orderResolutions sdkProof'
    exact(receipt,fields+(' previousReceipt' if incremental else ''))
    if incremental:
        previous=receipt['previousReceipt']
        if previous==reference:raise RuntimeError('History merge receipt cycle')
        validate(r,journal,entry,previous,_depth+1)
        validate_extension(root,receipt,json.loads(referenced(root,previous)))
    if (receipt['schema'] not in (SCHEMA,DELTA_SCHEMA) or receipt['backup']!=str(root) or
            receipt['oldId']!=entry['oldId'] or receipt['newId']!=entry['newId'] or
            receipt['selectionSourceId']!=entry.get('selectionSourceId',entry['oldId'])):
        raise RuntimeError('Merge receipt belongs to a different lineage or journal')
    mapping=r.module_identity_mapping(journal) if hasattr(r,'module_identity_mapping') else None
    if mapping is None:
        # Import the same mapping used by the caller, without loading a new live controller.
        direct={e['oldId']:e['newId'] for g in [*journal.get('previousGenerations',[]),journal] for e in g.get('entries',[])}
        mapping={}
        for old in direct:
            current=old;seen=set()
            while current in direct:
                if current in seen:raise RuntimeError('History lineage contains a cycle')
                seen.add(current);current=direct[current]
            mapping[old]=current
    exact(receipt['baseline'],'store blobs')
    store=receipt['baseline']['store'];blobs=receipt['baseline']['blobs']
    referenced(root,store);referenced(root,blobs)
    baseline=db_entries(store['path']);indexed={value['id']:value for value in baseline}
    for value in baseline:chat_event(value)
    with contextlib.closing(sqlite3.connect('file:'+str(store['path'])+'?mode=ro&immutable=1',uri=True)) as conn:
        metadata_row=conn.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
    if not metadata_row or json.loads(bytes.fromhex(metadata_row[0])).get('agentId')!=entry['newId']:
        raise RuntimeError('Merge baseline metadata has a different owner')
    if not isinstance(receipt['sources'],list) or not 1<=len(receipt['sources'])<=512:
        raise RuntimeError('Invalid merge source count')
    sources={}
    for source in receipt['sources']:
        r.entry(journal,entry['newId'])  # Revalidate the selected journal identity.
        key=(source['agentId'],source['kind'])
        if key in sources or source['agentId'] not in mapping or mapping[source['agentId']]!=entry['newId']:
            raise RuntimeError('Merge source is outside the selected lineage')
        sources[key]=source_records(root,source)
    expected_sources={entry['oldId'],entry.get('selectionSourceId',entry['oldId'])}
    if not expected_sources.issubset({key[0] for key in sources}):
        raise RuntimeError('Both selected and terminal source evidence are required')
    projections={}
    for item in receipt['projections']:
        exact(item,'sourceId sourceKind sourceEntryId targetEntryId')
        key=(item['sourceId'],item['sourceKind'],item['sourceEntryId'])
        if key in projections:raise RuntimeError('Duplicate source projection')
        projections[key]=item['targetEntryId']
    orders={}
    for item in receipt['sourceOrders']:
        exact(item,'sourceId sourceKind entryIds');key=(item['sourceId'],item['sourceKind'])
        if key in orders:raise RuntimeError('Duplicate source order')
        orders[key]=item['entryIds']
    resolutions={}
    if not isinstance(receipt['orderResolutions'],list) or len(receipt['orderResolutions'])>32:
        raise RuntimeError('Invalid reviewed ordering resolutions')
    for item in receipt['orderResolutions']:
        exact(item,'sourceId sourceKind beforeId afterId beforePayloadSha256 afterPayloadSha256 reason')
        key=(item['sourceId'],item['sourceKind'],item['beforeId'],item['afterId'])
        if key in resolutions:raise RuntimeError('Duplicate order resolution')
        resolutions[key]=item
    expected_projection=set();used_resolutions=set();positions={v['id']:i for i,v in enumerate(baseline)}
    for key,records in sources.items():
        ids=[v['id'] for v in records]
        if orders.get(key)!=ids:raise RuntimeError('Source order provenance changed or is missing')
        target_ids=[]
        for value in records:
            projection=(*key,value['id']);expected_projection.add(projection)
            target=projections.get(projection)
            if target not in indexed or payload(r,value,mapping,True)!=payload(r,indexed[target],mapping,True):
                raise RuntimeError('Source payload is absent from the reviewed baseline')
            target_ids.append(target)
        if len(set(target_ids))!=len(target_ids):
            raise RuntimeError('Distinct messages from one source cannot collapse into one target')
        chat_records=[value for value in records if chat_event(value)]
        chat_targets=[projections[(*key,value['id'])] for value in chat_records]
        for i in range(1,len(chat_records)):
            if positions[chat_targets[i-1]]<=positions[chat_targets[i]]:continue
            before,after=chat_records[i-1:i+1];resolution_key=(*key,before['id'],after['id'])
            resolution=resolutions.get(resolution_key)
            # The one supported contradiction is an already-arrived next user
            # record inserted before the previous turn's last sent reply.
            before_id=re.fullmatch(r'(t[0-9]+)s[0-9]+',before['id'])
            after_id=re.fullmatch(r'(t[0-9]+)u',after['id'])
            delta=before.get('timestampMs',0)-after.get('timestampMs',0)
            if (not resolution or resolution['reason']!='cross-turn-adjacent-arrival' or
                    not before_id or not after_id or before_id[1]==after_id[1] or not 0<delta<=1000 or
                    resolution['beforePayloadSha256']!=digest(payload(r,before,mapping)) or
                    resolution['afterPayloadSha256']!=digest(payload(r,after,mapping))):
                raise RuntimeError('Source order is not preserved by the merge baseline')
            used_resolutions.add(resolution_key)
    if set(projections)!=expected_projection or set(orders)!=set(sources) or used_resolutions!=set(resolutions):
        raise RuntimeError('Unknown or unused source projection evidence')
    sdk=json.loads(referenced(root,receipt['sdkProof']))
    exact(sdk,'schema engineSha256 rootSlot rootSha256 blobs sourceRoots nativeReport')
    if sdk['schema']!='openbot-sdk-merge-proof/v1':raise RuntimeError('Unknown SDK proof schema')
    sha(sdk['engineSha256']);json.loads(referenced(root,sdk['nativeReport']))
    source_roots={s['agentId']:s['sdkRootSha256'] for s in receipt['sources'] if s['kind']=='sqlite'}
    if sdk['sourceRoots']!=source_roots:raise RuntimeError('SDK source root provenance differs')
    if not isinstance(sdk['blobs'],dict) or len(sdk['blobs'])>MAX_RECORDS:
        raise RuntimeError('Invalid SDK blob evidence')
    with contextlib.closing(sqlite3.connect('file:'+str(blobs['path'])+'?mode=ro&immutable=1',uri=True)) as conn:
        actual={row[0]:digest(row[1]) for row in conn.execute('SELECT id,data FROM blobs')}
    if actual!=sdk['blobs']:raise RuntimeError('SDK baseline blob graph differs from native proof')
    if any(value is not None and value not in actual.values() for value in source_roots.values()):
        raise RuntimeError('Source SDK root archive is absent from the merged blob baseline')
    if sdk['rootSha256'] is None:
        if actual:raise RuntimeError('Absent SDK root requires an empty blob store')
    elif json.loads(bytes.fromhex(metadata_row[0])).get('latestRootBlobId')!=sdk['rootSlot']:
        raise RuntimeError('Merge baseline metadata points at a different SDK root')
    elif actual.get(sdk['rootSlot'])!=sha(sdk['rootSha256']):
        raise RuntimeError('SDK baseline root differs from native proof')
    active_path=r.m.SD/'agents'/entry['newId']/'store.db'
    for directory in (r.m.SD/'agents',active_path.parent):
        info=directory.lstat()
        if info.st_uid!=os.getuid() or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('Live merge target directory is foreign or symlinked')
    info=active_path.lstat()
    if info.st_uid!=os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_size>MAX_FILE:
        raise RuntimeError('Live merge target must be a bounded owned regular file')
    active=db_entries(active_path,immutable=False)
    wanted=[payload(r,value,mapping) for value in baseline];cursor=0
    for value in active:
        if cursor<len(wanted) and payload(r,value,mapping)==wanted[cursor]:cursor+=1
    if cursor!=len(wanted):raise RuntimeError('Live transcript lost or changed a baseline message or order')
    return {'baselineEntries':len(baseline),'sourceCount':len(sources),'receiptSha256':reference['sha256'],
            'pruneAllowed':False}



def validate_extension(root,current,previous):
    """An explicit new receipt extends evidence; it never edits prior artifacts."""
    for key in ('oldId','newId','selectionSourceId','backup'):
        if current[key]!=previous[key]:raise RuntimeError('Incremental receipt changes identity')
    exact(current['baseline'],'store blobs')
    referenced(root,current['baseline']['store']);referenced(root,current['baseline']['blobs'])
    old_rows=db_entries(previous['baseline']['store']['path'])
    new_rows=db_entries(current['baseline']['store']['path'])
    if len(new_rows)<=len(old_rows) or new_rows[:len(old_rows)]!=old_rows:
        raise RuntimeError('Incremental transcript must strictly append the immutable prefix')
    old_sources={(s['agentId'],s['kind']):s for s in previous['sources']}
    new_sources={(s['agentId'],s['kind']):s for s in current['sources']}
    if set(old_sources)!=set(new_sources):raise RuntimeError('Incremental source identities changed')
    for key,source in old_sources.items():
        target=new_sources[key]
        for field in ('generation','sdkRootSha256','blobSnapshot'):
            if source[field]!=target[field]:raise RuntimeError('Incremental source generation or SDK changed')
        old_records=source_records(root,source);new_records=source_records(root,target)
        if new_records[:len(old_records)]!=old_records:
            raise RuntimeError('Incremental source changed its immutable prefix')
        if source['kind']=='sqlite' and source!=target:
            raise RuntimeError('Incremental import cannot replace original SQLite evidence')
    for field in ('projections','orderResolutions'):
        if current[field][:len(previous[field])]!=previous[field]:
            raise RuntimeError('Incremental provenance changed earlier projections')
    old_orders={(v['sourceId'],v['sourceKind']):v['entryIds'] for v in previous['sourceOrders']}
    new_orders={(v['sourceId'],v['sourceKind']):v['entryIds'] for v in current['sourceOrders']}
    if set(old_orders)!=set(new_orders) or any(new_orders[k][:len(v)]!=v for k,v in old_orders.items()):
        raise RuntimeError('Incremental provenance changed earlier source order')
    old_sdk=json.loads(referenced(root,previous['sdkProof']))
    new_sdk=json.loads(referenced(root,current['sdkProof']))
    if any(old_sdk[k]!=new_sdk[k] for k in ('engineSha256','rootSlot','sourceRoots')):
        raise RuntimeError('Incremental SDK source provenance changed')
    for blob,sha256 in old_sdk['blobs'].items():
        if blob==old_sdk['rootSlot']:
            if sha256 not in new_sdk['blobs'].values():
                raise RuntimeError('Incremental SDK must archive its previous exact root')
        elif new_sdk['blobs'].get(blob)!=sha256:
            raise RuntimeError('Incremental SDK lost or changed an archived blob')



def source_freshness(r,journal,entry,cache=None):
    """Refuse a cutover if any reviewed source acquired a later history head.

    Platform generation and append-only head are compared through the owner RPC.
    This is a freshness gate, not a merge or a publication/delivery assertion.
    """
    reference=entry.get('historyMergeReceipt')
    if not reference:return {'reviewedSources':0,'receiptPresent':False}
    validate(r,journal,entry,reference)
    root=pathlib.Path(journal['backup']);receipt=json.loads(referenced(root,reference))
    cache={} if cache is None else cache
    for source in receipt['sources']:
        key=(source['kind'],source['agentId'])
        if source['kind']=='platform':
            captured=json.loads(referenced(root,{k:source[k]for k in ('path','sha256')}))
            rows=captured['entries'] if isinstance(captured,dict)else captured
            expected_row=max(rows,key=lambda x:int(x['seq'])) if rows else None
            expected={'generation':source['generation'],'head':None if expected_row is None else
                (int(expected_row['seq']),digest(base64.b64decode(expected_row['body'],validate=True)))}
            if key not in cache:
                response=r.m.rpc('ListGrokBotTranscriptEntries',{'agentId':source['agentId'],'limit':1})
                live=response.get('entries',[])
                latest=max(live,key=lambda x:int(x['seq'])) if live else None
                cache[key]={'generation':response.get('generation'),'head':None if latest is None else
                    (int(latest['seq']),digest(base64.b64decode(latest['body'],validate=True)))}
        else:
            def head(path):
                with contextlib.closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True))as db:
                    count=db.execute('SELECT COUNT(*) FROM transcript_entries').fetchone()[0]
                    row=db.execute('SELECT seq,entry FROM transcript_entries ORDER BY seq DESC LIMIT 1').fetchone()
                return {'count':count,'head':None if row is None else(int(row[0]),digest(json.dumps(json.loads(row[1]),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()))}
            expected=head(source['path']);expected['sdkRootSha256']=source['sdkRootSha256']
            if key not in cache:
                directory=r.m.SD/'agents'/source['agentId']
                for candidate in (r.m.SD/'agents',directory,directory/'store.db'):
                    info=candidate.lstat()
                    if info.st_uid!=os.getuid() or candidate.is_symlink():raise RuntimeError('Source history path is foreign or symlinked')
                current=head(directory/'store.db')
                with contextlib.closing(sqlite3.connect('file:'+str(directory/'store.db')+'?mode=ro',uri=True))as db:
                    row=db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
                if not row:raise RuntimeError('Source SDK metadata missing during freshness check')
                slot=json.loads(bytes.fromhex(row[0])).get('latestRootBlobId')
                blob_path=directory/'conversation-blobs.db';row=None
                if blob_path.exists() or blob_path.is_symlink():
                    info=blob_path.lstat()
                    if info.st_uid!=os.getuid()or not stat.S_ISREG(info.st_mode):raise RuntimeError('Source SDK path is foreign or symlinked')
                    with contextlib.closing(sqlite3.connect('file:'+str(blob_path)+'?mode=ro',uri=True))as db:
                        row=db.execute('SELECT data FROM blobs WHERE id=?',(slot,)).fetchone() if slot is not None else None
                current['sdkRootSha256']=digest(row[0])if row else None;cache[key]=current
        if cache[key]!=expected:
            raise RuntimeError('Source history changed after reviewed merge; incremental preservation required before activation')
    return {'reviewedSources':len(receipt['sources']),'receiptPresent':True,'sourceHeadsUnchanged':True}


def accept(r,journal,entry,path):
    raw=checked(journal['backup'],path)
    reference={'path':str(path),'sha256':digest(raw)}
    pinned=entry.get('historyMergeReceipt')
    if pinned not in (None,reference):
        candidate=json.loads(raw)
        if candidate.get('schema')!=DELTA_SCHEMA or candidate.get('previousReceipt')!=pinned:
            raise RuntimeError('A different immutable merge receipt is already pinned')
    elif pinned is None and json.loads(raw).get('schema')==DELTA_SCHEMA:
        raise RuntimeError('Incremental merge requires a previously pinned immutable receipt')
    result=validate(r,journal,entry,reference)
    entry['historyMergeReceipt']=reference
    return result
