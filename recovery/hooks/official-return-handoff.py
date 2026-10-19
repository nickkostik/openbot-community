"""Build a bounded fresh Local generation from retained default-session sources.

Only the Custom coordinator writes new hidden candidates under its dispatch
lock. Native current checkpoints remain byte-exact; literal history is appended
offline. Samples and complete double captures are not an admission fence.
"""
import base64
import contextlib
import hashlib
import json
import os
import pathlib
import sqlite3
import stat
import tempfile
import subprocess
import time
import uuid

STATE = 'openbot-return-handoff.json'
SCHEMA = 'openbot-official-return-handoff/v1'
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_RECORDS = 200000
MAX_SOURCES = 512


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > MAX_FILE:
        raise RuntimeError('Return capture requires bounded owned regular files')
    return info


def directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Return capture requires owned directories')


class Capture:
    def __init__(self, recovery):
        self.recovery, self.r, self.root = recovery, recovery.r, recovery.root
        self.total = 0
        self.requests = 0
        self.started = time.monotonic()

    def require(self):
        self.recovery.require()

    def put(self, path, raw):
        self.require()
        self.total += len(raw)
        if len(raw) > MAX_FILE or self.total > MAX_TOTAL:
            raise RuntimeError('Return capture evidence bound exceeded')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Every evidence file is immutable: no replacing prior capture artifacts.
        with path.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        return {'sha256': sha(raw), 'bytes': len(raw)}

    def database(self, path, destination):
        regular(path)
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Copy raw database + committed WAL into a private area
        # first. Even a read-only native SQLite connection may
        # create/update SHM; it must never open the active store.
        with tempfile.TemporaryDirectory(prefix='sqlite-',dir=destination.parent) as temporary:
            isolated=pathlib.Path(temporary)/path.name
            before={}
            for suffix in ('','-wal'):
                native=pathlib.Path(str(path)+suffix)
                if native.exists():
                    regular(native); data=native.read_bytes(); before[suffix]=sha(data)
                    if len(data)>MAX_FILE: raise RuntimeError('Return WAL snapshot bound exceeded')
                    copy=pathlib.Path(str(isolated)+suffix);copy.write_bytes(data);copy.chmod(0o600)
            for suffix,digest in before.items():
                if sha(pathlib.Path(str(path)+suffix).read_bytes())!=digest:
                    raise RuntimeError('Return native SQLite changed during raw snapshot')
            if pathlib.Path(str(path)+'-wal').exists()!=('-wal'in before):
                raise RuntimeError('Return native WAL appeared during snapshot')
            with contextlib.closing(sqlite3.connect(isolated.as_uri()+'?mode=ro',uri=True,timeout=2)) as source_db:
                with contextlib.closing(sqlite3.connect(':memory:')) as snapshot:
                    started=time.monotonic()
                    def progress(*_):
                        self.require()
                        if time.monotonic()-started>20:
                            raise RuntimeError('Return SQLite snapshot deadline exceeded')
                    source_db.backup(snapshot,pages=256,progress=progress)
                    if snapshot.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                        raise RuntimeError('Return SQLite snapshot integrity failed')
                    snapshot.execute('PRAGMA journal_mode=DELETE')
                    raw=snapshot.serialize()
        self.put(destination,raw)
        return raw

    def tree(self, source, destination):
        directory(source)
        result = []
        for path in sorted(source.rglob('*')):
            self.require()
            relative = str(path.relative_to(source))
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                # Retain the link as evidence; never follow it into another tree.
                result.append({'path': relative, 'link': os.readlink(path)})
            elif stat.S_ISDIR(info.st_mode):
                directory(path)
            elif path.name.endswith(('-wal', '-shm')):
                continue  # SQLite backup includes committed WAL content.
            else:
                regular(path)
                target = destination / relative
                if path.suffix == '.db':
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    raw=self.database(path,target)
                else:
                    raw = path.read_bytes()
                result.append({'path':relative,'sha256':sha(raw),'bytes':len(raw)}) if path.suffix=='.db' else result.append({'path':relative,**self.put(target,raw)})
            if len(result) > MAX_RECORDS:
                raise RuntimeError('Return capture file count bound exceeded')
        return result

    def cloud(self, aid):
        entries, before, generation = {}, None, None
        for _ in range(MAX_RECORDS // 500 + 1):
            self.require()
            self.requests+=1
            if self.requests>256 or time.monotonic()-self.started>180:
                raise RuntimeError('Return complete source capture request or time bound exceeded')
            body = {'agentId': aid, 'limit': 500}
            if before is not None: body['beforeSeq'] = str(before)
            if generation is not None: body['generation'] = generation
            response = self.r.m.rpc('ListGrokBotTranscriptEntries', body)
            rows = response.get('entries')
            if not isinstance(rows, list) or len(rows) > 500:
                raise RuntimeError('Return transcript page schema refused')
            new_generation = response.get('generation')
            if generation is not None and new_generation != generation:
                raise RuntimeError('Return transcript generation changed')
            generation = new_generation
            if not rows: break
            for row in rows:
                seq = int(row['seq'])
                if seq <= 0 or (seq in entries and entries[seq] != row):
                    raise RuntimeError('Return transcript sequence changed')
                decoded = json.loads(base64.b64decode(row['body'], validate=True))
                if not isinstance(decoded, dict) or not isinstance(decoded.get('id'), str):
                    raise RuntimeError('Return transcript payload schema refused')
                entries[seq] = row
            if len(entries) > MAX_RECORDS or len(encoded(entries)) > MAX_FILE:
                raise RuntimeError('Return transcript capture bound exceeded')
            earliest = min(int(row['seq']) for row in rows)
            if before is not None and earliest >= before:
                raise RuntimeError('Return transcript pagination did not advance')
            if earliest <= 1: break
            before = earliest
        else:
            raise RuntimeError('Return transcript pagination bound exceeded')
        return {'generation': generation, 'entries': [entries[k] for k in sorted(entries)]}



class FreshReturn(Capture):
    """Prepare new identities only; current active stores are never destinations."""
    def __init__(self, recovery, worker_lock_fd, checkpoint=None, append=None, activity=None, routines=None):
        super().__init__(recovery)
        self.tail = recovery.module.helper('history-tail-repair')
        self.validator = recovery.module.helper('history-merge-receipt')
        self.activity_module = recovery.module.helper('return-source-activity')
        self.activity = activity or (lambda ids: self.activity_module.check(self.recovery, ids))
        self.lock_fd = worker_lock_fd
        self.source_cache = {}
        self.checkpoint = checkpoint or self.run_checkpoint
        self.routines = routines or self.run_routines
        self.append = append or self.tail.TailRepair(recovery, worker_lock_fd).run_codec

    def require(self):
        import fcntl
        super().require()
        if self.lock_fd is None: raise RuntimeError('Official return requires inherited dispatch lock')
        actual, expected = os.fstat(self.lock_fd), (self.root/'openbot-custom-dispatch.lock').lstat()
        if (actual.st_dev, actual.st_ino, actual.st_uid) != (expected.st_dev, expected.st_ino, os.getuid()):
            raise RuntimeError('Official return dispatch lock differs')
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def reviewed_host(self):
        engine=self.recovery.module.helper('local-engine')
        generations=self.root/'openbot-local-engine'/'generations'
        directory(generations)
        candidates=sorted(generations.iterdir())
        if len(candidates)>64: raise RuntimeError('Return retained codec search bound exceeded')
        host=None
        for candidate in candidates:
            if len(candidate.name)!=32 or any(c not in '0123456789abcdef'for c in candidate.name): continue
            path=engine.artifact_directory(self.root,{'generationId':candidate.name})/'stock.cjs'
            if sha(engine.owned_bytes(path,engine.MAX_BYTES))==self.tail.REVIEWED_HOST_SHA:
                host=path;break
        if host is None: raise RuntimeError('Return reviewed current checkpoint codec is unavailable')
        return host

    def run_routines(self,folder,plan):
        host=self.reviewed_host()
        command=['/exec-daemon/node','--max-old-space-size=768',str(pathlib.Path(__file__).with_name('return-routine-codec.mjs')),
                 '--host',str(host),'--candidate-dir',str(folder),'--plan-file',str(folder/'routine-plan.json')]
        result=subprocess.run(command,capture_output=True,timeout=90)
        self.put(folder/'routine-codec-private-result.json',encoded({'returnCode':result.returncode,
                 'stdout':result.stdout.decode(errors='replace'),'stderr':result.stderr.decode(errors='replace')}))
        if result.returncode:raise RuntimeError('Return native routine codec refused authored state')
        return json.loads(self.tail.owned(folder/'return-routine-proof.json'))

    def run_checkpoint(self, folder, plan):
        host=self.reviewed_host()
        native=self.recovery.module.helper('computer-recovery').HOST
        command=['/exec-daemon/node','--max-old-space-size=768',str(pathlib.Path(__file__).with_name('current-local-sdk-proof.mjs')),
                 '--host',str(host),'--worker',str(native.parent/'agent-isolation/agent-store-worker.cjs'),
                 '--allocator-host',str(native),'--candidate-dir',str(folder),'--plan-file',str(folder/'current-checkpoint-plan.json')]
        result=subprocess.run(command,capture_output=True,timeout=90)
        self.put(folder/'current-checkpoint-private-result.json',encoded({'returnCode':result.returncode,
                 'stdout':result.stdout.decode(errors='replace'),'stderr':result.stderr.decode(errors='replace')}))
        if result.returncode: raise RuntimeError('Return current typed checkpoint codec refused')
        return json.loads(self.tail.owned(folder/'current-local-sdk-proof.json'))

    def status(self, phase, **values):
        self.state.update(phase=phase, **values, updatedAtMs=int(time.time()*1000))
        self.r.m.atomic_bytes(self.root/STATE, self.r.m.json_bytes(self.state), pre_commit=self.require)
        self.recovery.status('preparing-return' if phase=='preparing' else 'waiting-for-publication' if phase=='waiting-for-publication' else 'review-needed',
                             returnHandoffState=phase, returnPreparedAgents=self.state.get('preparedAgents', 0),
                             returnExpectedAgents=self.state.get('expectedAgents', 0),
                             returnReason=self.state.get('reason','none'),returnSessionScope='known-default-session',
                             returnAdmissionFenced=False,returnFutureMessagesMerged=False,
                             returnRetainedSources=self.state.get('retainedSources',0),
                             returnReconciliation=self.state.get('reconciliation','pending'),
                             returnSourceCheckedAtMs=self.state.get('reconciliationCheckedAtMs',0))

    def observe(self,identities):
        identities=sorted(set(identities));counts={};sampled=0
        for offset in range(0,max(1,len(identities)),32):
            batch=identities[offset:offset+32];proof=self.activity(batch)
            if (not isinstance(proof,dict)or proof.get('scope')!='known-default-session'or
                    proof.get('knownSessionId')!=''or proof.get('observedNotRunning')is not True or
                    proof.get('admissionFenced')is not False or proof.get('checkedSources')!=len(batch)or
                    type(proof.get('sampledAtMs'))is not int or not 0<=int(time.time()*1000)-proof['sampledAtMs']<120000 or
                    not isinstance(proof.get('sessionCounts'),dict)or set(proof['sessionCounts'])!=set(batch)or
                    any(type(value)is not int or value not in(0,1)for value in proof['sessionCounts'].values())):
                raise RuntimeError('Return known-session observation proof differs')
            counts.update(proof['sessionCounts']);sampled=max(sampled,proof['sampledAtMs'])
        return {'scope':'known-default-session','knownSessionId':'','observedNotRunning':True,
                'admissionFenced':False,'sampledAtMs':sampled,'checkedSources':len(identities),'sessionCounts':counts}

    def local_idle(self, identities):
        # The manager's live overlay samples LOCAL work even when an owned
        # current identity drifted to Temporal. Separate owner API observations
        # are mandatory for its known cloud default branch.
        result=self.r.m.gateway('listAgents',{})
        rows=result if isinstance(result,list)else result.get('agents')
        if not isinstance(rows,list):raise RuntimeError('Return native running-state schema is unknown')
        for aid in identities:
            found=[row for row in rows if isinstance(row,dict)and row.get('id')==aid]
            if (len(found)!=1 or found[0].get('harness')not in('box','temporal')or
                    found[0].get('isRunning')is not False or found[0].get('isRunningTurn')is not False):
                raise RuntimeError('Return current Local turn is running or its live state is unknown')

    def capture(self, journal, entry, folder, members, indexed):
        snapshots = []; guards = {}; bindings={}
        for generation in [*journal.get('previousGenerations',[]),journal]:
            for recorded in generation['entries']:
                for aid,sid in ((recorded['oldId'],recorded.get('sourceServerId')),
                                (recorded['newId'],recorded.get('serverId')),
                                (recorded.get('selectionSourceId'),recorded.get('selectionSourceServerId'))):
                    if aid is None or sid is None: continue
                    if aid in bindings and bindings[aid]!=str(sid):
                        raise RuntimeError('Return historical binding is ambiguous')
                    bindings[aid]=str(sid)
        for aid in members:
            row = indexed.get(aid)
            local = self.root/'agents'/aid
            if row is None or not local.exists():
                raise RuntimeError('Return lineage source is missing; complete capture requires review')
            if (row.get('viewerIsOwner') is not True or row.get('legacyAgentId') != aid or
                    str(row.get('id'))!=bindings.get(aid) or row.get('harness') not in ('box','temporal')):
                raise RuntimeError('Return lineage ownership changed')
            if row.get('viewerSessionId','')!='':
                raise RuntimeError('Return source has a known nondefault viewer session')
            profile = json.loads(self.tail.owned(local/'profile.json'))
            if str(profile.get('serverId')) != str(row['id']):
                raise RuntimeError('Return lineage native/server binding changed')
            files = self.tree(local, folder/'sources'/aid)
            if any('link' in file for file in files):
                raise RuntimeError('Return source links require explicit preservation review')
            guards[aid] = files
            if not (folder/'sources'/aid/'store.db').exists():
                raise RuntimeError('Return requires complete current native SDK store')
            transcript = self.cloud(aid)
            cloud_path = folder/'cloud'/(aid+'.json'); self.put(cloud_path, encoded(transcript))
            routines = self.r.m.rpc('ListGrokBotAgentAutomations', {'agentId': aid})
            for wrapper in self.recovery.module.helper('return-routine-authority').cloud_response(routines):
                record=json.loads(wrapper['recordJson'])
                if record.get('sessionId','')!='':
                    raise RuntimeError('Return source routine has a known nondefault session')
            self.put(folder/'routines'/(aid+'.json'), encoded(routines))
            store = folder/'sources'/aid/'store.db'; blobs = folder/'sources'/aid/'conversation-blobs.db'
            with self.tail.db(store, True) as db:
                metadata = json.loads(bytes.fromhex(db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()[0]))
            if metadata.get('agentId')!=aid:raise RuntimeError('Return source SDK owner binding changed')
            slot = metadata.get('latestRootBlobId')
            if not blobs.exists():
                if slot is not None: raise RuntimeError('Return source SDK graph is missing')
                with contextlib.closing(sqlite3.connect(blobs)) as db:
                    db.execute('CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)');db.commit()
                blobs.chmod(0o600)
            graph = self.tail.graph(blobs)
            if (slot is None and graph) or (slot is not None and slot not in graph):
                raise RuntimeError('Return source SDK checkpoint is incomplete')
            if aid==entry['newId'] and slot is None:
                raise RuntimeError('Return requires a current typed Local SDK checkpoint')
            snapshots.extend([
                {'agentId':aid, 'kind':'sqlite', **self.tail.reference(store), 'generation':None,
                 'sdkRootSha256':graph.get(slot), 'blobSnapshot':self.tail.reference(blobs)},
                {'agentId':aid, 'kind':'platform', **self.tail.reference(cloud_path),
                 'generation':transcript['generation'], 'sdkRootSha256':None, 'blobSnapshot':None}])
        return snapshots, guards

    def source_values(self, source):
        key=(source['path'],source['sha256'])
        if key not in self.source_cache:
            if source['kind']=='sqlite': values=self.validator.db_entries(source['path'])
            else: values=[json.loads(base64.b64decode(row['body'],validate=True))for row in
                          json.loads(pathlib.Path(source['path']).read_bytes())['entries']]
            self.source_cache[key]=values
        return self.source_cache[key]

    def literal(self, event, source_id):
        # Native wake is inert historical request-source provenance, not a
        # notification to replay or evidence of delivery. Group-member authors
        # are accepted only when their explicit UUID is this owned source.
        value=dict(event)
        if 'agentId' in value:
            if value.pop('agentId')!=source_id: raise RuntimeError('Return event owner differs')
        if 'author' in value:
            author=value.pop('author')
            if (value.get('kind')!='send-message' or not isinstance(author,dict) or
                    set(author)!={'id','name'} or author['id']!=source_id or
                    not isinstance(author['name'],str) or len(author['name'])>1024):
                raise RuntimeError('Return text peer author requires review')
        if 'wake' in value:
            wake=value.pop('wake')
            if (value.get('kind')!='send-message' or not isinstance(wake,str) or
                    not 0<len(wake)<=1024 or any(ord(c)<32 for c in wake)):
                raise RuntimeError('Return historical wake metadata requires review')
        return self.tail.literal(value)

    def union(self, entry, folder, sources, mapping):
        terminal = entry['newId']; base = folder/'sources'/terminal/'store.db'
        store = folder/'store.db'; self.tail.snapshot(base, store)
        current = self.validator.db_entries(store)
        published = next(self.source_values(s) for s in sources if s['agentId']==terminal and s['kind']=='platform')
        if published[:len(current)] != current:
            raise RuntimeError('Current Local published prefix is incomplete or divergent')
        last = max((value.get('timestampMs',0) for value in current), default=0)
        projections=[]; orders=[]; indexed={}; baseline=[]; occupied=set(); additions={}; literal_records={}; delta_sources=set()
        for value in current:
            normalized = self.r.merger.remap_transcript_owner(value, mapping)
            if value['id'] in occupied: raise RuntimeError('Current Local UI identifiers are ambiguous')
            occupied.add(value['id']); baseline.append(normalized)
            indexed[(value['id'], sha(encoded({k:v for k,v in normalized.items() if k!='id'})))] = value['id']
        for source in sources:
            values=self.source_values(source)
            orders.append({'sourceId':source['agentId'], 'sourceKind':source['kind'], 'entryIds':[v['id'] for v in values]})
            if len({value['id'] for value in values}) != len(values):
                raise RuntimeError('Return source has duplicate event identities')
            for value in values:
                normalized=self.r.merger.remap_transcript_owner(value, mapping)
                key=(value['id'], sha(encoded({k:v for k,v in normalized.items() if k!='id'})))
                if key not in indexed:
                    # Full envelopes are checked by the PR30 literal reader;
                    # no tools, peer destinations, rich payloads or streaming.
                    literal_records[key]=(*self.literal(value,source['agentId']),value)
                    if value['timestampMs'] < last:
                        raise RuntimeError('Official return is not a completed literal append')
                    additions[key]=normalized
                    if source['kind']=='platform': delta_sources.add(source['agentId'])
        if len(additions)>self.tail.MAX_ROWS:
            raise RuntimeError('Official return exceeds bounded literal import')
        records=[]; text_bytes=0
        for key,value in sorted(additions.items(),key=lambda item:(item[1]['timestampMs'],item[0])):
            role,text,original=literal_records[key]; identifier=value['id']
            if identifier in occupied or len(identifier.encode())>128:
                identifier=self.r.merger.compact_branch_identifier(identifier+':'+sha(encoded(value)),occupied)
            occupied.add(identifier); indexed[key]=identifier
            baseline.append({**value,'id':identifier})
            records.append({'id':identifier,'originalId':value['id'],'kind':value['kind'],'role':role,'text':text,
                            'timestampMs':value['timestampMs'],'sourcePayloadSha256':sha(encoded(original)), 'literalText':True})
            text_bytes+=len(text.encode())
        if text_bytes>self.tail.MAX_TEXT: raise RuntimeError('Official return exceeds literal byte bound')
        positions={value['id']:i for i,value in enumerate(baseline)}
        for source in sources:
            order=[]
            for value in self.source_values(source):
                normalized=self.r.merger.remap_transcript_owner(value,mapping)
                target=indexed[(value['id'],sha(encoded({k:v for k,v in normalized.items() if k!='id'})))]
                projections.append({'sourceId':source['agentId'],'sourceKind':source['kind'],
                                    'sourceEntryId':value['id'],'targetEntryId':target})
                if self.validator.chat_event(value): order.append(positions[target])
            if order!=sorted(order): raise RuntimeError('Return source ordering requires review')
        with contextlib.closing(sqlite3.connect(store)) as db:
            metadata=json.loads(bytes.fromhex(db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()[0]))
            metadata['agentId']=mapping[terminal]
            db.execute("UPDATE kv SET value=? WHERE key='metadata'", (encoded(metadata).hex(),))
            db.execute('DELETE FROM transcript_entries')
            db.executemany('INSERT INTO transcript_entries(seq,id,entry)VALUES(?,?,?)',
                           [(i+1,value['id'],encoded(value).decode())for i,value in enumerate(baseline)])
            db.commit()
        return {'records':records,'deltaSources':sorted(delta_sources),'sources':sources,'projections':projections,'sourceOrders':orders,'orderResolutions':[]}

    def archives(self, entry, folder, sources):
        terminal=entry['newId']; blobs=folder/'conversation-blobs.db'
        self.tail.snapshot(folder/'sources'/terminal/'conversation-blobs.db',blobs)
        root_slot=bytes('sand-live-conversation-root-v1__','ascii').hex()
        source_roots={}; graphs=[]
        with contextlib.closing(sqlite3.connect(blobs)) as target:
            for source in sources:
                if source['kind']!='sqlite': continue
                path=pathlib.Path(source['blobSnapshot']['path'])
                with self.tail.db(path,True) as db:
                    for identifier,data in db.execute('SELECT id,data FROM blobs'):
                        if identifier==root_slot: identifier=sha(data)
                        prior=target.execute('SELECT data FROM blobs WHERE id=?',(identifier,)).fetchone()
                        if prior and prior[0]!=data: raise RuntimeError('Return SDK archive blob collision')
                        target.execute('INSERT OR IGNORE INTO blobs(id,data)VALUES(?,?)',(identifier,data))
                source_roots[source['agentId']]=source['sdkRootSha256']
                graphs.append({'agentId':source['agentId'],**source['blobSnapshot'],'rootSHA':source['sdkRootSha256']})
            target.commit()
        return root_slot, source_roots, graphs

    def settled_store(self, store):
        with self.tail.db(store,True) as db:
            for key,value in db.execute("SELECT key,value FROM kv WHERE key IN('episodePending','awaitingUserResponse')"):
                try:pending=json.loads(value)
                except (ValueError,TypeError):raise RuntimeError('Return pending native state schema is unknown')from None
                if pending not in (None,False,[],{}):
                    raise RuntimeError('Return pending episode or awaiting response must settle before copying')
            tables=[row[0]for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                if table in ('kv','blobs','transcript_entries') or table.startswith('sqlite_'):continue
                quoted='"'+table.replace('"','""')+'"'
                if table=='automation_completion_inbox':
                    columns={row[1]for row in db.execute('PRAGMA table_info('+quoted+')')}
                    if not {'id','acknowledged'}.issubset(columns):
                        raise RuntimeError('Return completion inbox schema is unknown')
                    if db.execute('SELECT COUNT(*) FROM '+quoted+' WHERE acknowledged=0 OR acknowledged IS NULL OR acknowledged NOT IN(0,1)').fetchone()[0]:
                        raise RuntimeError('Return pending completions must settle before copying')
                elif db.execute('SELECT EXISTS(SELECT 1 FROM '+quoted+' LIMIT 1)').fetchone()[0]:
                    raise RuntimeError('Return unknown native queue requires review')

    def prepare(self, journal, entry, folder, members, indexed, new_id):
        cloud_sources=sorted(members)
        before_activity=self.observe(cloud_sources)
        self.local_idle([entry['newId']])
        sources,guards=self.capture(journal,entry,folder,members,indexed)
        self.local_idle([entry['newId']])
        mapping={aid:new_id for aid in members}
        merged=self.union(entry,folder,sources,mapping)
        if not merged['records'] and indexed[entry['newId']].get('harness')=='box': return None
        if str(entry['kind']).endswith('ROOM'): raise RuntimeError('Return rooms require a typed membership checkpoint')
        after_activity=self.observe(cloud_sources)
        self.put(folder/'known-session-observations.json',encoded({'before':before_activity,'after':after_activity}))
        routine_ref,routine_states=self.recovery.module.helper('return-routine-builder').prepare(self,entry,folder,indexed)
        terminal=entry['newId']; selected=entry.get('selectionSourceId',entry['oldId'])
        for source_id in members:self.settled_store(folder/'sources'/source_id/'store.db')
        current_profile=json.loads(self.tail.owned(folder/'sources'/terminal/'profile.json'))
        official_profile=json.loads(self.tail.owned(folder/'sources'/selected/'profile.json'))
        role=lambda profile:{k:v for k,v in profile.items() if k not in ('id','agentId','legacyAgentId','serverId','harness')}
        if role(current_profile)!=role(official_profile):
            raise RuntimeError('Return role instructions differ')
        slot,roots,graphs=self.archives(entry,folder,sources)
        route=self.tail.TailRepair(self.recovery,self.lock_fd).route(entry)
        root=roots[terminal]
        base_plan={'schemaVersion':1,'sourceId':terminal,'targetId':new_id,'expectedRootSHA':root,
                   'expectedStoreSHA':sha(self.tail.owned(folder/'store.db')),'sourceRoots':roots,
                   'sourceGraphs':graphs,'selectedRoute':route}
        self.tail.write(folder/'current-checkpoint-plan.json',base_plan,True)
        native_base=self.checkpoint(folder,base_plan)
        base_report=json.loads(self.validator.referenced(journal['backup'],native_base['nativeReport']))
        if (native_base.get('schema')!='openbot-sdk-merge-proof/v1' or
                native_base.get('blobs')!=self.tail.graph(folder/'conversation-blobs.db') or
                base_report.get('schema')!='openbot-current-local-sdk-report/v1' or
                base_report.get('helperSha256')!=sha(self.tail.owned(pathlib.Path(__file__).with_name('current-local-sdk-proof.mjs'))) or
                base_report.get('sourceId')!=terminal or base_report.get('targetId')!=new_id or
                base_report.get('rootSha256')!=root or base_report.get('workerSha256')!=self.validator.IDENTIFIER_WORKER_SHA or
                any(base_report.get(key) is not True for key in ('rootRoundtrip','currentRootByteExact',
                    'completeContextExact','noPendingTools','quotedHistoryUnchanged','graphUnchanged',
                    'allSourcesByteUnchanged','metadataOwnerVerified')) or
                base_report.get('structuralOwnerReferences')!=[] or base_report.get('unresolvedProtoRefs')!=0 or
                any(base_report.get(key)!=0 for key in ('providerCalls','networkAttempts','toolAttempts')) or
                base_report.get('capacity',{}).get('fits') is not True or
                native_base.get('engineSha256')!=self.tail.REVIEWED_HOST_SHA or
                native_base.get('rootSha256')!=root or native_base.get('rootSlot')!=slot or
                native_base.get('sourceRoots')!=roots):
            raise RuntimeError('Return current native checkpoint proof differs')
        plan={'schemaVersion':1,'engineSha256':self.tail.REVIEWED_HOST_SHA,'records':merged['records'],
              'sourceSnapshots':[source['sha256']for source in sources], 'provenance':merged['projections'],
              'selectedRoute':route,'expectedRootSHA':root,'expectedStoreSHA':sha(self.tail.owned(folder/'store.db'))}
        self.tail.write(folder/'plan.json',plan,True)
        native=self.append(folder,plan) if merged['records'] else None
        if native is None:
            proof=native_base
        else:
            context=native.get('context',{})
            if (native.get('schema')!='openbot-history-tail-codec/v1' or
                    native.get('engineSha256')!=self.tail.REVIEWED_HOST_SHA or
                    native.get('oldRootSha256')!=root or native.get('rootSlot')!=slot or
                    native.get('workerSHA')!=self.validator.IDENTIFIER_WORKER_SHA or
                    native.get('blobs')!=self.tail.graph(folder/'conversation-blobs.db') or
                    native.get('rootSha256')!=native['blobs'].get(slot) or
                    any(native.get(key)!=0 for key in ('networkAttempts','toolAttempts','providerCalls')) or
                    native.get('historicalActionsReplayed') is not False or
                    any(context.get(key) is not True for key in ('roundtrip','literalEquality','originalPrefixPreserved','otherRootFieldsPreserved')) or
                    native.get('sourcePlanFileSHA')!=sha(self.tail.owned(folder/'plan.json')) or
                    native.get('bridgeSha256')!=sha(self.tail.owned(pathlib.Path(__file__).with_name('history-tail-codec.mjs'))) or
                    native.get('capacity',{}).get('fits') is not True or
                    any(native.get('files',{}).get(name,{}).get('sha256')!=sha(self.tail.owned(folder/name))
                        for name in ('store.db','conversation-blobs.db'))):
                raise RuntimeError('Return detached native append proof differs')
            proof={'schema':'openbot-sdk-merge-proof/v1','engineSha256':native['engineSha256'],
                   'rootSlot':slot,'rootSha256':native['rootSha256'],'blobs':native['blobs'],
                   'sourceRoots':roots,'nativeReport':self.tail.reference(folder/'proof.json')}
        self.tail.write(folder/'sdk-proof.json',proof,True)
        receipt={'schema':self.validator.SCHEMA,'oldId':terminal,'newId':new_id,'selectionSourceId':selected,
                 'backup':journal['backup'],'baseline':{'store':self.tail.reference(folder/'store.db'),
                 'blobs':self.tail.reference(folder/'conversation-blobs.db')},
                 **{key:merged[key]for key in ('sources','projections','sourceOrders','orderResolutions')},
                 'sdkProof':self.tail.reference(folder/'sdk-proof.json')}
        self.tail.write(folder/'receipt.json',receipt,True)
        retained=[]
        for source in sources:
            if source['kind']!='platform':continue
            aid=source['agentId'];local=next(value for value in sources if value['agentId']==aid and value['kind']=='sqlite')
            retained.append({'agentId':aid,'serverId':str(indexed[aid]['id']),'sessionId':'',
                             'platform':{key:source[key]for key in('path','sha256')},'generation':source['generation'],
                             'store':{key:local[key]for key in('path','sha256')},'blobs':local['blobSnapshot'],
                             'sdkRootSha256':local['sdkRootSha256']})
        self.tail.write(folder/'retained-sources.json',{'schema':'openbot-retained-return-sources/v1',
                        'requestId':self.state['requestId'],'destinationId':new_id,'scope':'known-default-session',
                        'knownSessionId':'','admissionFenced':False,'futureMessagesMerged':False,
                        'recordedAtMs':int(time.time()*1000),'sources':retained},True)
        states={}
        for path in (folder/'sources'/terminal/'automations').rglob('automation.json'):
            value=json.loads(self.tail.owned(path));relative=str(path.relative_to(folder/'sources'/terminal))
            if type(value.get('enabled')) is not bool: raise RuntimeError('Return routine switch is unknown')
            states[relative]=value['enabled']
        return {'copyProof':{'transcriptEntries':len(self.validator.db_entries(folder/'store.db')),
                            'historyIdConflicts':0,'automationStates':states},
                'oldId':terminal,'newId':new_id,'sourceServerId':entry['serverId'],
                'selectionSourceId':selected,'selectionSourceServerId':entry.get('selectionSourceServerId',entry['sourceServerId']),
                'name':entry['name'],'kind':entry['kind'],'requiresHistoryMerge':True,'phase':'copied',
                'historyMergeReceipt':self.tail.reference(folder/'receipt.json'),
                'returnRoutineAuthority':routine_ref,'activationAutomationStates':routine_states,
                'returnCurrentCheckpoint':self.tail.reference(folder/'current-checkpoint-plan.json'),
                'returnRetainedSources':self.tail.reference(folder/'retained-sources.json'),
                'returnFolder':str(folder),'returnSourceGuards':guards,'returnRoute':route,'returnCloudActivitySources':sorted(cloud_sources),
                **({'registrationStrategy':entry['registrationStrategy']}if 'registrationStrategy'in entry else {})}

    def fresh(self, journal, prepared, indexed, folder):
        self.local_idle([entry['oldId']for entry in prepared])
        self.observe(sorted({aid for entry in prepared for aid in entry.get('returnCloudActivitySources',[])}))
        if self.r.read()!=journal or {row['agentId']:row for row in self.r.rows()}!=indexed:
            raise RuntimeError('Return authority changed during staging')
        for entry in prepared:
            evidence=pathlib.Path(entry['returnFolder'])
            for aid,expected in entry['returnSourceGuards'].items():
                actual=self.tree(self.root/'agents'/aid, folder/'freshness'/aid)
                if actual!=expected: raise RuntimeError('Return current native checkpoint changed')
                old=json.loads((evidence/'cloud'/(aid+'.json')).read_bytes())
                if self.cloud(aid)!=old: raise RuntimeError('Return Official history changed')
                routines=self.r.m.rpc('ListGrokBotAgentAutomations',{'agentId':aid})
                if encoded(routines)!=(evidence/'routines'/(aid+'.json')).read_bytes():
                    raise RuntimeError('Return routine authority changed')
            if self.tail.TailRepair(self.recovery,self.lock_fd).route({**entry,'newId':entry['oldId']})!=entry['returnRoute']:
                raise RuntimeError('Return selected model route changed')

    def install(self, entry):
        import shutil
        source=pathlib.Path(entry['returnFolder'])/'sources'/entry['oldId']
        target=self.root/'agents'/entry['newId']
        if target.exists():
            marker=json.loads(self.tail.owned(target/'.openbot-official-return.json'))
            if marker!={'requestId':self.state['requestId'],'receipt':entry['historyMergeReceipt']}:
                raise RuntimeError('Return destination already exists outside this request')
            if (self.tail.graph(target/'conversation-blobs.db')!=self.tail.graph(pathlib.Path(entry['returnFolder'])/'conversation-blobs.db') or
                    self.tail.rows(target/'store.db')!=self.tail.rows(pathlib.Path(entry['returnFolder'])/'store.db') or
                    json.loads(self.tail.owned(target/'settings.json')).get('hiddenFromSidebar') is not True or
                    any(json.loads(self.tail.owned(p)).get('enabled') is not False for p in(target/'automations').rglob('automation.json'))):
                raise RuntimeError('Prepared return destination changed; never overwrite it')
            return
        temp=pathlib.Path(entry['returnFolder'])/('install-agent-'+uuid.uuid4().hex)
        shutil.copytree(source,temp)
        profile=json.loads(self.tail.owned(temp/'profile.json'))
        for key in ('id','agentId','legacyAgentId'):
            if key in profile:
                if profile[key]!=entry['oldId']: raise RuntimeError('Return profile owner is ambiguous')
                profile[key]=entry['newId']
        profile.pop('serverId',None); profile['harness']='box'
        self.r.publisher.require_safe_profile(profile,entry['newId'])
        self.tail.write(temp/'profile.json',profile)
        settings=json.loads(self.tail.owned(temp/'settings.json')); settings['hiddenFromSidebar']=True
        self.tail.write(temp/'settings.json',settings)
        states={}
        for path in temp.rglob('automation.json'):
            relative=str(path.relative_to(temp)); value=json.loads(self.tail.owned(path))
            if not relative.startswith('automations/') or type(value.get('enabled')) is not bool:
                raise RuntimeError('Return routine switch has unknown semantics')
            states[relative]=value['enabled']; value['enabled']=False;self.tail.write(path,value)
        if entry.get('returnRoutineAuthority'):
            authority=json.loads(self.validator.referenced(self.r.read()['backup'],entry['returnRoutineAuthority']))
            for relative in authority.get('materialized',[]):
                source=pathlib.Path(entry['returnFolder'])/relative
                destination=temp/relative;destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                shutil.copyfile(source,destination);destination.chmod(0o600)
        for name in ('store.db','conversation-blobs.db'):
            shutil.copyfile(pathlib.Path(entry['returnFolder'])/name,temp/name)
        self.tail.write(temp/'.openbot-official-return.json',{'requestId':self.state['requestId'],'receipt':entry['historyMergeReceipt']},True)
        (temp/'.openbot-local-copy').write_text(entry['oldId']+'\n');(temp/'.openbot-local-copy').chmod(0o600)
        entry['copyProof']={'transcriptEntries':len(self.validator.db_entries(temp/'store.db')),
                            'historyIdConflicts':0,'automationStates':states}
        self.require(); temp.rename(target)

    def finish_models(self, journal):
        if not journal.get('officialReturnRequestId') or journal.get('officialReturnModelsCopied') is True: return
        path=self.root/'openbot-bot-models.json'
        if not path.exists():
            journal['officialReturnModelsCopied']=True;self.r.save(journal);return
        raw=self.tail.owned(path); value=json.loads(raw)
        for entry in journal['entries']:
            if entry.get('returnFolder'):
                value=self.recovery.module.copy_bot_overrides(value,entry['oldId'],entry['newId'])
        if encoded(value)!=encoded(json.loads(raw)):
            self.r.m.atomic_bytes(path,self.r.m.json_bytes(value),expected=raw,pre_commit=self.require)
        self.require();journal['officialReturnModelsCopied']=True;self.r.save(journal)

    def needed(self,entry,members,indexed,folder):
        terminal=entry['newId']
        if terminal in getattr(self.r,'return_source_changed_ids',set()):return True
        if indexed.get(terminal,{}).get('harness')=='temporal': return True
        mapping={aid:terminal for aid in members}
        path=folder/'preview'/terminal/'store.db'
        self.database(self.root/'agents'/terminal/'store.db',path)
        current={self.r.transcript_key(value,mapping)for value in self.validator.db_entries(path)}
        for aid in members:
            local=folder/'preview'/aid/'store.db'
            if aid!=terminal: self.database(self.root/'agents'/aid/'store.db',local)
            values=self.validator.db_entries(local)
            values.extend(json.loads(base64.b64decode(row['body'],validate=True))for row in self.cloud(aid)['entries'])
            if any(self.r.transcript_key(value,mapping)not in current for value in values): return True
        return False

    def run(self):
        self.require()
        with self.r.m.migration_lock():
            return self._run()

    def _run(self):
        import copy
        import tempfile
        self.require();journal=self.r.read()
        if any(e['phase']!='active'for e in journal['entries']):
            self.finish_models(journal);return journal
        old={}
        if (self.root/STATE).exists(): old=json.loads(self.tail.owned(self.root/STATE))
        job=self.recovery.progress['jobId']; lineage=self.recovery.module.identity_mapping(journal)
        same=(old.get('jobId')==job and old.get('journalSha256')==sha(encoded(journal)))
        request=old.get('requestId')if same else str(uuid.uuid4())
        self.recovery.module.canonical(request)
        destinations=old.get('destinations',{})if same else {e['newId']:str(uuid.uuid4())for e in journal['entries']}
        self.state={'schema':SCHEMA,'jobId':job,'requestId':request,'journalSha256':sha(encoded(journal)),
                    'destinations':destinations,'expectedAgents':len(journal['entries']),'preparedAgents':0,
                    'authorityCommitted':False,'nativeAppDeliveryVerified':False,'reason':'none',
                    'sessionScope':'known-default-session','admissionFenced':False,'futureMessagesMerged':False,
                    'reconciliation':'pending','retainedSources':0}
        if same and old.get('preparedIntent'):
            try: return self.resume_prepared(journal,old)
            except Exception:
                self.status('review-needed',reason='unsupported-or-changing-return')
                raise
        self.status('preparing')
        backup=pathlib.Path(journal['backup']);directory(backup)
        parent=backup/('official-return-'+request)
        if parent.exists(): directory(parent)
        parent.mkdir(mode=0o700,exist_ok=True)
        attempt=pathlib.Path(tempfile.mkdtemp(prefix='attempt-',dir=parent));attempt.chmod(0o700)
        self.put(attempt/'previous-journal.json',encoded(journal))
        indexed={row['agentId']:row for row in self.r.rows()}
        settings=self.r.m.rpc('GetGrokBotUserRuntimeSettings',{})
        self.put(attempt/'platform-settings.json',encoded(settings))
        models=self.tail.owned(self.root/'openbot-bot-models.json')if(self.root/'openbot-bot-models.json').exists()else None
        prepared=[]
        try:
            selected=[]
            for entry in journal['entries']:
                members=sorted({aid for aid,target in lineage.items()if target==entry['newId']}|{entry['newId']})
                folder=pathlib.Path(tempfile.mkdtemp(prefix='openbot-history-tail-',dir=attempt));folder.chmod(0o700)
                self.put(folder/'.openbot-detached-history-tail.json',encoded({'schemaVersion':1,'detached':True}))
                if self.needed(entry,members,indexed,folder):selected.append((entry,members,folder))
            # Every changed destination is known before authored routine peer
            # references are remapped. Never point at an unplanned fresh UUID.
            proposed=copy.deepcopy(journal)
            proposed['previousGenerations']=[*journal.get('previousGenerations',[]),{k:v for k,v in journal.items()if k!='previousGenerations'}]
            proposed['entries']=[{'oldId':entry['newId'],'newId':destinations[entry['newId']]}for entry,_,_ in selected]
            self.return_mapping=self.recovery.module.identity_mapping(proposed)
            for entry,members,folder in selected:
                result=self.prepare(journal,entry,folder,members,indexed,destinations[entry['newId']])
                if result is None:raise RuntimeError('Return source checkpoint advanced without a supported literal delta')
                prepared.append(result)
                self.status('preparing',preparedAgents=len(prepared))
            if not prepared: raise RuntimeError('Return request has no supported fresh history delta')
            self.fresh(journal,prepared,indexed,attempt)
            self.state.update(retainedSources=sum(len(entry['returnSourceGuards'])for entry in prepared),
                              reconciliation='current',reconciliationCheckedAtMs=int(time.time()*1000))
            if self.r.m.rpc('GetGrokBotUserRuntimeSettings',{})!=settings:
                raise RuntimeError('Return preferences changed during staging')
            if (self.tail.owned(self.root/'openbot-bot-models.json')if(self.root/'openbot-bot-models.json').exists()else None)!=models:
                raise RuntimeError('Return model settings changed during staging')
            next_journal=copy.deepcopy(journal)
            next_journal['previousGenerations']=[*journal.get('previousGenerations',[]),{k:v for k,v in journal.items()if k!='previousGenerations'}]
            by_old={entry['oldId']:entry for entry in prepared}
            next_journal['entries']=[by_old.get(entry['newId'],entry)for entry in next_journal['entries']]
            for key in ('publicationReloadRequestedAtMs','publicationReloadRequestId','preferencesFinalized','officialReturnModelsCopied'):
                next_journal.pop(key,None)
            next_journal.update(officialReturnRequestId=request,officialReturnPreviousJournal=self.tail.reference(attempt/'previous-journal.json'))
            for entry in prepared:
                self.validator.validate(self.r,next_journal,entry,entry['historyMergeReceipt'],_check_live=False)
            self.tail.write(attempt/'prepared-intent.json',{'journal':next_journal,'prepared':prepared,'indexed':indexed},True)
            self.state['preparedIntent']=self.tail.reference(attempt/'prepared-intent.json')
            self.status('preparing',preparedAgents=len(prepared))
            for entry in prepared:
                # Validate evidence against the exact proposed generation before
                # installing even a hidden destination. No old active phase flip.
                self.validator.validate(self.r,next_journal,entry,entry['historyMergeReceipt'],_check_live=False)
                self.install(entry)
                self.validator.validate(self.r,next_journal,entry,entry['historyMergeReceipt'])
                self.recovery.module.helper('return-routine-authority').authority_for(self.r,next_journal,entry,self.validator)
            self.require();self.local_idle([entry['oldId']for entry in prepared])
            self.observe(sorted({aid for entry in prepared for aid in entry.get('returnCloudActivitySources',[])}))
            if self.r.read()!=journal: raise RuntimeError('Return journal changed before authority commit')
            self.r.save(next_journal);self.state['authorityCommitted']=True
            self.finish_models(next_journal)
            self.status('waiting-for-publication',preparedAgents=len(prepared))
            return next_journal
        except Exception:
            self.status('review-needed',reason='unsupported-or-changing-return')
            raise

    def resume_prepared(self,journal,state):
        self.state=state
        reference=state['preparedIntent']
        raw=self.validator.referenced(journal['backup'],reference)
        intent=json.loads(raw);proposed=intent['journal'];prepared=intent['prepared']
        if proposed.get('officialReturnRequestId')!=state['requestId']:
            raise RuntimeError('Return prepared intent belongs to another request')
        folder=pathlib.Path(tempfile.mkdtemp(prefix='resume-',dir=pathlib.Path(reference['path']).parent));folder.chmod(0o700)
        self.fresh(journal,prepared,intent['indexed'],folder)
        for entry in prepared:
            self.validator.validate(self.r,proposed,entry,entry['historyMergeReceipt'],_check_live=False)
            self.install(entry)
            self.validator.validate(self.r,proposed,entry,entry['historyMergeReceipt'])
            self.recovery.module.helper('return-routine-authority').authority_for(self.r,proposed,entry,self.validator)
        self.require();self.local_idle([entry['oldId']for entry in prepared])
        self.observe(sorted({aid for entry in prepared for aid in entry.get('returnCloudActivitySources',[])}))
        if self.r.read()!=journal: raise RuntimeError('Return journal changed before resumed commit')
        self.r.save(proposed);self.state['authorityCommitted']=True;self.finish_models(proposed)
        self.status('waiting-for-publication',preparedAgents=len(prepared))
        return proposed


def retained_source_changes(r,journal):
    """Compare complete captured default branches; retain every original owner.

    This reads originals, never restores old roots into an active destination.
    A later edit/deletion/checkpoint change is a new reconciliation obligation,
    not evidence that messages after the previous snapshots were merged.
    """
    from types import SimpleNamespace
    references=[entry for entry in journal['entries']if entry.get('returnRetainedSources')]
    if not references:return set()
    # Reuse the same owned immutable evidence policy as canonical receipts.
    import importlib.util
    def helper(name):
        spec=importlib.util.spec_from_file_location('return_monitor_'+name.replace('-','_'),pathlib.Path(__file__).with_name(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    validator=helper('history-merge-receipt');tail=helper('history-tail-repair')
    capture=Capture(SimpleNamespace(r=r,root=r.m.SD,require=r.require_local_mode))
    indexed={row['agentId']:row for row in r.rows()};changed=set();retained=set()
    with tempfile.TemporaryDirectory(prefix='return-source-check-',dir=journal['backup'])as temporary:
        temporary=pathlib.Path(temporary);temporary.chmod(0o700)
        for entry in references:
            validator.validate(r,journal,entry,_check_live=False)
            effective=json.loads(validator.referenced(journal['backup'],entry['historyMergeReceipt']))
            effective_sources={(value['agentId'],value['kind']):value for value in effective['sources']}
            inventory=json.loads(validator.referenced(journal['backup'],entry['returnRetainedSources']))
            if (inventory.get('schema')!='openbot-retained-return-sources/v1'or inventory.get('destinationId')!=entry['newId']or
                    inventory.get('scope')!='known-default-session'or inventory.get('knownSessionId')!=''or
                    inventory.get('admissionFenced')is not False or inventory.get('futureMessagesMerged')is not False or
                    not isinstance(inventory.get('sources'),list)or not 0<len(inventory['sources'])<=MAX_SOURCES):
                raise RuntimeError('Retained source scope or inventory proof differs')
            if {value['agentId']for value in inventory['sources']}!={value['agentId']for value in effective['sources']}:
                raise RuntimeError('Retained source inventory differs from the effective receipt')
            seen=set()
            for source in inventory['sources']:
                aid=source['agentId'];row=indexed.get(aid)
                if aid in seen or source.get('sessionId')!='':raise RuntimeError('Retained source session scope differs')
                seen.add(aid);retained.add(aid)
                if (row is None or row.get('viewerIsOwner')is not True or row.get('legacyAgentId')!=aid or
                        str(row.get('id'))!=source['serverId']or row.get('harness')not in('box','temporal')or
                        row.get('viewerSessionId','')!=''):
                    raise RuntimeError('Retained source owner or known-session binding changed')
                # Preserve the original inventory forever, while an accepted
                # immutable incremental receipt can advance a captured cutoff.
                validator.referenced(journal['backup'],source['platform'])
                validator.referenced(journal['backup'],source['store']);validator.referenced(journal['backup'],source['blobs'])
                platform=effective_sources[(aid,'platform')];native=effective_sources[(aid,'sqlite')]
                expected_cloud=json.loads(validator.referenced(journal['backup'],{key:platform[key]for key in('path','sha256')}))
                expected_store={key:native[key]for key in('path','sha256')};validator.referenced(journal['backup'],expected_store)
                expected_blobs=native['blobSnapshot'];validator.referenced(journal['backup'],expected_blobs)
                local=r.m.SD/'agents'/aid
                profile=json.loads(validator.checked(r.m.SD,local/'profile.json'))
                if str(profile.get('serverId'))!=source['serverId']:
                    raise RuntimeError('Retained source native owner binding changed')
                if capture.cloud(aid)!=expected_cloud:changed.add(entry['newId'])
                live_store=temporary/(aid+'-store.db')
                if live_store.exists():live_store=temporary/(aid+'-'+uuid.uuid4().hex+'-store.db')
                capture.database(local/'store.db',live_store)
                if validator.db_entries(live_store)!=validator.db_entries(expected_store['path']):changed.add(entry['newId'])
                native_blobs=local/'conversation-blobs.db'
                actual_graph={}
                if native_blobs.exists()or native_blobs.is_symlink():
                    live_blobs=temporary/(aid+'-'+uuid.uuid4().hex+'-blobs.db')
                    capture.database(native_blobs,live_blobs);actual_graph=tail.graph(live_blobs)
                if actual_graph!=tail.graph(pathlib.Path(expected_blobs['path'])):changed.add(entry['newId'])
                with tail.db(live_store,True)as db:
                    row=db.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
                if not row:raise RuntimeError('Retained source SDK metadata is missing')
                metadata=json.loads(bytes.fromhex(row[0]));slot=metadata.get('latestRootBlobId')
                if metadata.get('agentId')!=aid:raise RuntimeError('Retained source SDK owner changed')
                if (actual_graph.get(slot)if slot is not None else None)!=native['sdkRootSha256']:
                    changed.add(entry['newId'])
    state_path=r.m.SD/STATE
    if state_path.exists():
        raw=tail.owned(state_path);state=json.loads(raw)
        if state.get('schema')==SCHEMA:
            state.update(reconciliation='changed'if changed else'current',retainedSources=len(retained),
                         reconciliationCheckedAtMs=int(time.time()*1000))
            r.m.atomic_bytes(state_path,r.m.json_bytes(state),expected=raw,pre_commit=r.require_local_mode)
    return changed
