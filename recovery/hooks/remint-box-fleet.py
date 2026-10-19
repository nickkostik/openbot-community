#!/usr/bin/env python3
"""Stage complete fresh BOX identities without destroying a previous migration.

Plan/verify are read-only. Init, stage, register and activate require explicit
commands. The native client and billing are separate verification boundaries.
"""
import argparse
import base64
import contextlib
import importlib.util
import json
import os
import pathlib
import sqlite3
import stat
import time
import uuid

SD = pathlib.Path('/home/box/sand-data')
JOURNAL = SD / 'openbot-box-remint.json'
HERE = pathlib.Path(__file__).resolve().parent


def helper(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owned_json(path):
    info = pathlib.Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Expected an owned regular JSON file')
    value = json.loads(pathlib.Path(path).read_text())
    if not isinstance(value, dict):
        raise RuntimeError('Expected a JSON object')
    return value


def canonical(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise RuntimeError('Expected a canonical UUID')
    return value


def source_candidates(rows, profiles):
    """Hidden archives, unregistered placeholders and probes are not resurrected."""
    matches = {}
    for row in rows:
        aid = canonical(row['agentId'])
        if aid in matches:
            raise RuntimeError('Ambiguous platform identity')
        matches[aid] = row
    selected = []
    for aid, value in profiles.items():
        row = matches.get(aid)
        if not row or row.get('harness') != 'temporal' or value['settings'].get('hiddenFromSidebar', False):
            continue
        if row.get('viewerIsOwner') is not True or row.get('legacyAgentId') != aid:
            raise RuntimeError('Visible source is not an owned matching identity')
        if row.get('name', '').startswith(('OpenBot Local Routing Verification', 'OpenBot Atomic Local Verification')):
            continue
        profile = value['profile']
        if profile.get('harness') != 'box' or str(profile.get('serverId')) != row['id']:
            raise RuntimeError('Source binding must be repaired before copying')
        selected.append({'oldId': aid, 'sourceServerId': row['id'],
                         'name': row.get('name', profile.get('name', '')),
                         'kind': row.get('kind'), 'phase': 'planned'})
    return selected


def remap_preferences(settings, mapping):
    """Remap identity positions only, preserving labels and unrelated settings."""
    sections = [{**section, 'agentIds': list(dict.fromkeys(mapping.get(aid, aid) for aid in section.get('agentIds', [])))}
                for section in settings.get('sidebarSections', {}).get('sections', [])]
    pins = list(dict.fromkeys(mapping.get(aid, aid) for aid in settings.get('pinnedAgents', {}).get('agentIds', [])))
    return {'sidebarSections': {'sections': sections}, 'pinnedAgents': {'agentIds': pins}}


def transfer_preferences(settings, mapping):
    """Keep both identities in their position until visibility handoff finishes."""
    final = remap_preferences(settings, mapping)
    ancestors = {}
    for old, new in mapping.items():
        if old != new:
            ancestors.setdefault(new, []).append(old)
    def pair(ids):
        return list(dict.fromkeys(value for aid in ids
            for value in [*ancestors.get(aid, []), aid]))
    return {'sidebarSections': {'sections': [{**section, 'agentIds': pair(section['agentIds'])}
            for section in final['sidebarSections']['sections']]},
            'pinnedAgents': {'agentIds': pair(final['pinnedAgents']['agentIds'])}}



def preserve_native_section_state(sections, current):
    """Platform membership omits the Computer's per-section collapsed state."""
    collapsed = {section['id']: section.get('isCollapsed',False) for section in current}
    return [{**section,'isCollapsed':collapsed.get(section['id'],False)} for section in sections]


def copy_bot_overrides(settings, old, new):
    updated = json.loads(json.dumps(settings))
    for name in ('assignments', 'reasoning'):
        values = updated.get(name, {})
        if not isinstance(values, dict):
            raise RuntimeError('Unknown per-bot override schema')
        if old in values:
            if new in values and values[new] != values[old]:
                raise RuntimeError('Destination override was independently edited')
            values[new] = values[old]
    return updated


def registration_kind(entry):
    if entry.get('registrationStrategy')=='native-box-room' and str(entry['kind']).endswith('ROOM'):
        return 'GROK_BOT_AGENT_KIND_AGENT'
    return entry['kind']


def identity_mapping(journal):
    """Follow earlier remints when copying group members and peer references."""
    generations = [*journal.get('previousGenerations', []), journal]
    direct = {}
    for generation in generations:
        for entry in generation.get('entries', []):
            old, new = canonical(entry['oldId']), canonical(entry['newId'])
            if old in direct and direct[old] != new:
                raise RuntimeError('Historical remint identity has conflicting destinations')
            direct[old] = new
    resolved = {}
    for old in direct:
        target, seen = old, set()
        while target in direct:
            if target in seen:
                raise RuntimeError('Historical remint identity map contains a cycle')
            seen.add(target); target = direct[target]
        resolved[old] = target
    return resolved


def lineage_mapping(journal, destinations):
    """Select complete proven chains, never unrelated earlier generations."""
    return {old:new for old,new in identity_mapping(journal).items() if new in destinations}


def terminal_sources(previous, rows, profiles):
    """Return from a saved stock selection through its terminal local copy."""
    lineage = identity_mapping(previous)
    indexed = {row['agentId']:row for row in rows}
    if len(indexed) != len(rows):
        raise RuntimeError('Ambiguous platform identity')
    entries=[];seen=set()
    for entry in previous['entries']:
        terminal=entry['newId']
        if lineage.get(terminal,terminal) != terminal:
            raise RuntimeError('Previous copy is not a terminal identity')
        original=entry.get('selectionSourceId',entry['oldId'])
        original_sid=entry.get('selectionSourceServerId',entry['sourceServerId'])
        selected=indexed.get(original);local=indexed.get(terminal)
        if not selected or profiles.get(original,{}).get('settings',{}).get('hiddenFromSidebar',False):
            continue
        for aid,row,sid in ((original,selected,original_sid),(terminal,local,entry['serverId'])):
            profile=profiles.get(aid,{}).get('profile',{})
            if (not row or row.get('viewerIsOwner') is not True or row.get('legacyAgentId')!=aid
                    or str(row.get('id'))!=str(sid) or str(profile.get('serverId'))!=str(sid)
                    or row.get('harness')!='temporal' or row.get('kind')!=(registration_kind(entry) if aid==terminal else entry['kind'])):
                raise RuntimeError('Return lineage ownership or binding changed')
        if terminal in seen:
            raise RuntimeError('Return lineages overlap')
        seen.add(terminal)
        entries.append({'oldId':terminal,'sourceServerId':str(local['id']),
            'selectionSourceId':original,'selectionSourceServerId':str(original_sid),
            'name':selected.get('name',''),'kind':entry['kind'],'phase':'planned',
            'requiresHistoryMerge':True,
            **({'registrationStrategy':entry['registrationStrategy']} if 'registrationStrategy' in entry else {})})
    return entries


class Reminter:
    def __init__(self):
        self.m = helper('migrate-local-bots')
        self.publisher = helper('publish-local-fleet')
        self.merger = helper('fleet-state-merge')

    def transcript_key(self, value, mapping):
        """Compare evidence with owner-only normalization, never quoted IDs."""
        return json.dumps(self.merger.remap_transcript_owner(value, mapping),
                          sort_keys=True, separators=(',', ':'))

    def require_local_mode(self):
        flag=SD/'openbot-local-only.enable'
        info=flag.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or (SD/'openbot-mode').read_text().strip()!='custom':
            raise RuntimeError('Explicit Custom local-only mode is required for remint writes')

    def rows(self):
        return self.m.rpc('ListGrokBotAgents', {}).get('agents', [])

    def profiles(self):
        profiles = {}
        root = SD / 'agents'
        if root.is_symlink():
            raise RuntimeError('Agent root must not be symlinked')
        for directory in sorted(root.iterdir()):
            try:
                aid = canonical(directory.name)
            except (ValueError, RuntimeError):
                continue
            if directory.is_symlink():
                raise RuntimeError('Refusing a symlinked agent directory')
            if not (directory / 'profile.json').exists():
                continue
            profiles[aid] = {'profile': owned_json(directory / 'profile.json'),
                             'settings': owned_json(directory / 'settings.json') if (directory / 'settings.json').exists() else {}}
        return profiles

    def read(self):
        journal = owned_json(JOURNAL)
        entries = journal.get('entries', [])
        old = [canonical(e['oldId']) for e in entries]
        new = [canonical(e['newId']) for e in entries]
        if len(set(old)) != len(old) or len(set(new)) != len(new) or set(old) & set(new):
            raise RuntimeError('Remint identity map is not one-to-one')
        identity_mapping(journal)
        return journal

    def save(self, journal):
        self.m.write(JOURNAL, journal)

    def plan(self):
        return self.read() if JOURNAL.exists() else {'entries': source_candidates(self.rows(), self.profiles())}

    def initialize(self, next_generation=False, return_from_terminal=False):
        self.require_local_mode()
        with self.m.migration_lock():
            previous = self.read() if JOURNAL.exists() else None
            if previous and not next_generation:
                return previous
            if previous and any(e['phase'] != 'active' for e in previous['entries']):
                raise RuntimeError('Finish the current remint before starting another generation')
            rows, profiles = self.rows(), self.profiles()
            if return_from_terminal and (not previous or not next_generation):
                raise RuntimeError('Terminal return requires an explicit next generation')
            entries = terminal_sources(previous, rows, profiles) if return_from_terminal else source_candidates(rows, profiles)
            if not entries:
                raise RuntimeError('No visible Temporal source needs reminting')
            backup = SD / 'recovery-snapshots' / ('box-remint-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
            backup.mkdir(parents=True, mode=0o700)
            self.m.write(backup / 'server-roster.json', rows)
            self.m.write(backup / 'server-settings.json', self.m.rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {}))
            for name in ('openbot-bot-models.json', 'openbot-plan.json', 'openbot-pause-bots.json', 'openbot-local-migration.json'):
                path = SD / name
                if path.exists():
                    self.m.atomic_bytes(backup / name, self.m.checked_bytes(path))
            for entry in entries:
                entry['newId'] = str(uuid.uuid4())
            journal = {'startedAtMs': int(time.time()*1000), 'backup': str(backup), 'entries': entries,
                       'nativeAppDeliveryVerified': False, 'billingVerified': False}
            if previous:
                self.m.write(backup / 'previous-journal.json', previous)
                journal['previousGenerations'] = [*previous.get('previousGenerations', []),
                    {k:v for k,v in previous.items() if k != 'previousGenerations'}]
            identity_mapping(journal)
            self.save(journal)
            return journal

    def entry(self, journal, aid):
        canonical(aid)
        matches = [e for e in journal['entries'] if aid in (e['oldId'], e['newId'])]
        if len(matches) != 1:
            raise RuntimeError('Identity is not in the current remint journal')
        return matches[0]

    def stage(self, aid):
        self.require_local_mode()
        with self.m.migration_lock():
            journal = self.read(); entry = self.entry(journal, aid)
            if entry['phase'] != 'planned':
                return entry
            old, new = entry['oldId'], entry['newId']
            source = SD / 'agents' / old
            before = owned_json(source / 'profile.json')
            if before.get('harness') not in (('box','temporal') if entry.get('requiresHistoryMerge') else ('box',)) or before.get('serverId') != entry['sourceServerId']:
                raise RuntimeError('Source binding changed before copy')
            self.m.atomic_bytes(pathlib.Path(journal['backup']) / (old + '.profile.before.json'), self.m.checked_bytes(source / 'profile.json'))
            mapping = identity_mapping(journal)
            entry['copyProof'] = self.m.copy_agent(old, new, mapping, journal)
            after = owned_json(SD / 'agents' / new / 'profile.json')
            if {k:v for k,v in after.items() if k not in ('serverId','harness')} != {
                    k:self.m.remap_json(v, mapping) for k,v in before.items() if k not in ('serverId','harness')}:
                raise RuntimeError('Copied role/profile is inconsistent')
            # The native main bot may have a placeholder locally but a real server name.
            if after.get('name') in ('New Agent', 'New Bot', '') and entry['name'] not in ('New Agent', 'New Bot', ''):
                after['name'] = entry['name']
                self.m.write(SD / 'agents' / new / 'profile.json', after)
            staged_settings=owned_json(SD/'agents'/new/'settings.json')
            self.m.write(SD/'agents'/new/'settings.json',{**staged_settings,'hiddenFromSidebar':True})
            models = SD / 'openbot-bot-models.json'; raw = self.m.checked_bytes(models)
            updated = copy_bot_overrides(json.loads(raw), old, new)
            if updated != json.loads(raw):
                self.m.atomic_bytes(models, self.m.json_bytes(updated), expected=raw)
            if entry.get('requiresHistoryMerge') or entry['copyProof'].get('historyIdConflicts', 0):
                # A completed private staging copy must remain resumable even
                # when live registration requires review of divergent branches.
                entry['phase'] = 'copied'; self.save(journal)
            self.local_identity(entry)
            entry['phase'] = 'copied'; self.save(journal)
            return entry

    def local_identity(self, entry):
        if entry.get('requiresHistoryMerge') or entry.get('copyProof', {}).get('historyIdConflicts', 0):
            helper('history-merge-receipt').validate(self, self.read(), entry)
        aid = entry['newId']; directory = SD / 'agents' / aid
        profile = owned_json(directory / 'profile.json')
        self.publisher.require_safe_profile(profile, aid)
        if (directory / 'store.db').is_symlink():
            raise RuntimeError('Store must not be symlinked')
        conn = sqlite3.connect('file:' + str(directory / 'store.db') + '?mode=ro', uri=True)
        try:
            row = conn.execute("SELECT value FROM kv WHERE key='metadata'").fetchone()
            if not row or json.loads(bytes.fromhex(row[0])).get('agentId') != aid:
                raise RuntimeError('Copied store metadata identity is inconsistent')
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Copied store integrity failed')
            count = conn.execute('SELECT COUNT(*) FROM transcript_entries').fetchone()[0]
        finally:
            conn.close()
        if count < entry.get('copyProof', {}).get('transcriptEntries', 0):
            raise RuntimeError('Copied transcript entries are missing')
        return profile, count

    def accept_history_merge(self, aid, receipt):
        """Pin reviewed immutable evidence without erasing original copy conflicts."""
        self.require_local_mode()
        with self.m.migration_lock():
            journal=self.read();entry=self.entry(journal,aid)
            if entry['phase']!='copied':
                raise RuntimeError('Only a staged copy may accept reviewed history evidence')
            proof=helper('history-merge-receipt').accept(self,journal,entry,receipt)
            self.save(journal)
            return proof

    def restage_room(self, aid, adopt_id=None):
        """Retain a refused hidden room copy; never replace an active destination."""
        self.require_local_mode()
        with self.m.migration_lock():
            journal=self.read();entry=self.entry(journal,aid)
            if not str(entry['kind']).endswith('ROOM') or entry['phase']!='copied':
                raise RuntimeError('Only an unactivated refused room copy may be restaged')
            previous=entry['newId'];rows={r['agentId']:r for r in self.rows()}
            if rows.get(previous,{}).get('harness')!='temporal':
                raise RuntimeError('Previous room was not a confirmed refused Temporal registration')
            new=canonical(adopt_id) if adopt_id else str(uuid.uuid4())
            if new in {e[k] for e in journal['entries'] for k in ('oldId','newId')}:
                raise RuntimeError('Replacement room identity overlaps the current map')
            mapping={e['oldId']:e['newId'] for e in journal['entries']};mapping[entry['oldId']]=new
            before=json.loads(json.dumps(journal))
            self.m.write(pathlib.Path(journal['backup'])/('journal-before-room-'+previous+'.json'),before)
            target=SD/'agents'/new
            if adopt_id:
                self.publisher.require_box_row(rows.get(new),new)
                if owned_json(target/'settings.json').get('hiddenFromSidebar') is not True:
                    raise RuntimeError('Adopted room must be a hidden staged copy')
                if (target/'.openbot-local-copy').read_text().strip()!=entry['oldId']:
                    raise RuntimeError('Adopted room has a different source')
                stats=entry['copyProof']
            else:
                stats=self.m.copy_agent(entry['oldId'],new,mapping,journal)
            source_group=owned_json(SD/'agents'/entry['oldId']/'group.json')
            group=owned_json(target/'group.json')
            expected=[mapping.get(member,member) for member in source_group.get('memberIds',[])]
            if not expected or group.get('memberIds')!=expected:
                raise RuntimeError('Replacement room membership differs from the explicit source')
            settings=owned_json(target/'settings.json')
            self.m.write(target/'settings.json',{**settings,'hiddenFromSidebar':True})
            models=SD/'openbot-bot-models.json';raw=self.m.checked_bytes(models)
            updated=copy_bot_overrides(json.loads(raw),entry['oldId'],new)
            if updated!=json.loads(raw):self.m.atomic_bytes(models,self.m.json_bytes(updated),expected=raw)
            entry.setdefault('refusedDestinations',[]).append({'agentId':previous,'harness':'temporal'})
            entry.update(newId=new,phase='copied',copyProof=stats,registrationStrategy='native-box-room')
            self.local_identity(entry);self.save(journal)
            return entry

    def register(self, aid, native_box_room=False):
        self.require_local_mode()
        with self.m.migration_lock():
            journal = self.read(); entry = self.entry(journal, aid)
            if entry['phase'] not in ('copied', 'registered-box', 'active'):
                raise RuntimeError('Copy must be verified before registration')
            profile, count = self.local_identity(entry)
            if native_box_room:
                if not str(entry['kind']).endswith('ROOM'):
                    raise RuntimeError('Native BOX room registration requires an original ROOM')
                entry['registrationStrategy']='native-box-room'
            if str(entry['kind']).endswith('ROOM'):
                group = owned_json(SD / 'agents' / entry['newId'] / 'group.json')
                members = group.get('memberIds', [])
                if not isinstance(members, list) or not members or len(set(members)) != len(members):
                    raise RuntimeError('Room requires explicit nonempty unique members')
                by_id = {r['agentId']:r for r in self.rows()}
                for member in members:
                    self.publisher.require_box_row(by_id.get(member), canonical(member))
            rows = [r for r in self.rows() if entry['newId'] in (r.get('agentId'),r.get('legacyAgentId'))]
            if len(rows) > 1:
                raise RuntimeError('Ambiguous destination registration')
            if rows:
                row = rows[0]
            elif str(entry['kind']).endswith('ROOM') and entry.get('registrationStrategy')!='native-box-room':
                result = self.m.rpc('CreateGrokBotRoom', {'agentId':entry['newId'], 'name':entry['name'],
                    'description':profile.get('description',''), 'memberAgentIds':members})
                row = result.get('agent', {})
            else:
                row = self.m.rpc('CreateGrokBotAgent', self.publisher.request_fields(entry['newId'], profile)).get('agent', {})
            # Never bind or activate a Temporal return; keep the source untouched.
            try:
                self.publisher.require_box_row(row, entry['newId'])
            except self.publisher.SafetyError:
                if row.get('agentId')==entry['newId'] and row.get('legacyAgentId')==entry['newId'] and row.get('viewerIsOwner') is True:
                    self.m.rpc('SetGrokBotAgentClientState',{'agentId':entry['newId'],'hiddenFromSidebar':True})
                raise
            if row.get('kind') != registration_kind(entry):
                raise RuntimeError('Destination server kind differs from the source')
            self.publisher.bind_profile(SD/'agents'/entry['newId']/'profile.json', row, entry['newId'],
                                        pathlib.Path(journal['backup']), profile)
            if entry['phase'] != 'active':
                self.m.rpc('SetGrokBotAgentClientState',{'agentId':entry['newId'],'hiddenFromSidebar':True})
            entry.update(phase='active' if entry['phase']=='active' else 'registered-box',
                         serverId=row['id'], registeredAtMs=int(time.time()*1000))
            self.save(journal)
            self.m.gateway('listAgents', {})  # Advertise the copy; publication needs a native sweep/mutation.
            return entry

    def publish(self):
        """Request one supervised reload; the existing watcher waits for idle."""
        self.require_local_mode()
        with self.m.migration_lock():
            journal=self.read();rows={r['agentId']:r for r in self.rows()}
            eligible=[e for e in journal['entries'] if e['phase'] in ('registered-box','active')]
            if not eligible:raise RuntimeError('No registered BOX copy is ready for publication')
            for entry in eligible:
                profile,count=self.local_identity(entry)
                sid=self.publisher.require_box_row(rows.get(entry['newId']),entry['newId'])
                if profile.get('serverId')!=sid:raise RuntimeError('BOX binding changed before publication')
            recovery=SD/'computer-recovery-state.json';raw=self.m.checked_bytes(recovery)
            state=owned_json(recovery)
            self.m.atomic_bytes(recovery,self.m.json_bytes({**state,'pendingHostReload':True}),expected=raw)
            journal['publicationReloadRequestedAtMs']=int(time.time()*1000);self.save(journal)
            return {'registeredCopies':len(eligible),'supervisedReloadRequested':True,
                    'publicationVerified':False,'nativeAppDeliveryVerified':False}

    def verify(self, aid=None):
        journal = self.read(); server = {r['agentId']:r for r in self.rows()}
        native_rows = self.m.gateway('listAgents', {})
        native = {r['id']:r for r in (native_rows if isinstance(native_rows,list) else native_rows.get('agents',[]))}
        results = []
        for entry in journal['entries']:
            if aid and aid not in (entry['oldId'],entry['newId']):
                continue
            result = {'oldId':entry['oldId'], 'newId':entry['newId'], 'name':entry['name'], 'phase':entry['phase']}
            try:
                profile,count = self.local_identity(entry)
                row = server.get(entry['newId'])
                sid = self.publisher.require_box_row(row, entry['newId'])
                if profile.get('serverId') != sid or row.get('kind') != registration_kind(entry):
                    raise RuntimeError('Platform/local binding differs')
                if native.get(entry['newId'],{}).get('harness') != 'box':
                    raise RuntimeError('Native roster did not advertise the BOX copy')
                group_path=SD/'agents'/entry['newId']/'group.json'
                if group_path.exists() or str(entry['kind']).endswith('ROOM'):
                    group=owned_json(group_path)
                    members=group.get('memberIds',[])
                    current=native[entry['newId']]
                    if not isinstance(members,list):
                        raise RuntimeError('Native group membership is incomplete')
                    if not members and str(entry['kind']).endswith('ROOM'):
                        raise RuntimeError('Native room has no configured responders')
                    # The stock reader treats an empty group file as an ordinary agent.
                    # Preserve this existing file without inventing responders or claiming a room.
                    if not members:
                        if current.get('isGroup') is not False or current.get('memberIds',[]) != []:
                            raise RuntimeError('Empty group definition differs from native interpretation')
                    elif current.get('isGroup') is not True or current.get('memberIds') != members:
                        raise RuntimeError('Native group membership is incomplete')
                    for member in members:self.publisher.require_box_row(server.get(member),member)
                    result.update(nativeGroupVerified=bool(members),groupMemberCount=len(members),replyReady=bool(members),
                                  emptyGroupDefinitionPreserved=not members,
                                  platformRoomKindVerified=str(row.get('kind','')).endswith('ROOM'))
                conn=sqlite3.connect('file:'+str(SD/'agents'/entry['newId']/'store.db')+'?mode=ro',uri=True)
                try:
                    local_entries=[json.loads(r[0]) for r in conn.execute('SELECT entry FROM transcript_entries ORDER BY seq')]
                    ids=[item['id'] for item in local_entries]
                finally:
                    conn.close()
                published={}; before=None;generation=None
                while True:
                    body={'agentId':entry['newId'],'limit':500}
                    if before is not None:body['beforeSeq']=str(before)
                    if generation is not None:body['generation']=generation
                    page=self.m.rpc('ListGrokBotTranscriptEntries',body)
                    generation=page.get('generation',generation);entries=page.get('entries',[])
                    if not entries:break
                    for item in entries:
                        published[int(item['seq'])]=json.loads(base64.b64decode(item['body'],validate=True))
                    earliest=min(int(item['seq']) for item in entries)
                    if earliest<=1:break
                    if before is not None and earliest>=before:
                        raise RuntimeError('Platform transcript pagination did not advance')
                    before=earliest
                cloud_entries=[published[k] for k in sorted(published)]
                cloud_ids=[item['id'] for item in cloud_entries]
                if cloud_ids[:len(ids)] != ids:
                    raise RuntimeError('Copied history is not fully published in order')
                if cloud_entries[:len(local_entries)] != local_entries:
                    raise RuntimeError('Published history payload differs from the copied conversation')
                result.update(status='verified-box', localEntries=len(ids), publishedEntries=len(cloud_ids))
            except Exception as exc:
                result.update(status='not-verified', errorType=type(exc).__name__,
                              message=str(exc) if isinstance(exc,RuntimeError) else 'Verification failed')
            results.append(result)
        return results

    def activate(self, aid=None):
        self.require_local_mode()
        with self.m.migration_lock():
            journal = self.read()
            results = self.verify(aid)
            if not results:
                raise RuntimeError('No destination selected for activation')
            if any(r['status'] != 'verified-box' for r in results):
                raise RuntimeError('Every destination must be verified before fleet activation')
            selected={r['newId'] for r in results}
            # Copies stay paused while they are hidden and their BOX handoff is
            # unverified. Capture the source's CURRENT switches before retiring
            # it, then restore those switches only after the handoff succeeds.
            for entry in journal['entries']:
                if entry['phase'] == 'active' or entry['newId'] not in selected:
                    continue
                if 'activationAutomationStates' not in entry:
                    current={str(path.relative_to(SD/'agents'/entry['oldId'])):
                             owned_json(path).get('enabled', False)
                             for path in (SD/'agents'/entry['oldId']/'automations').rglob('automation.json')}
                    copied=entry.get('copyProof',{}).get('automationStates')
                    if not isinstance(copied,dict) or set(current) != set(copied):
                        raise RuntimeError('Source routine list changed after copying; cutover refused')
                    self.checked_routine_states(entry['newId'],current)
                    entry['activationAutomationStates']=current
                    self.save(journal)
            mapping = lineage_mapping(journal, selected)
            # Sidebar hiding and disabling a local JSON file do not stop the
            # retired Temporal bot's server scheduler. Retire both authorities
            # before exposing the replacement or changing the user's positions.
            retired = set(mapping)
            for old in sorted(retired):
                self.pause_cloud_routines(old, pathlib.Path(journal['backup']))
                for path in (SD/'agents'/old/'automations').rglob('automation.json'):
                    raw=self.m.checked_bytes(path);definition=json.loads(raw)
                    if definition.get('enabled') is True:
                        self.m.atomic_bytes(pathlib.Path(journal['backup'])/'retired-automations'/old/path.parent.name/'automation.json',raw)
                        self.m.atomic_bytes(path,self.m.json_bytes({**definition,'enabled':False}),expected=raw)
            settings = self.m.rpc('GetGrokBotUserRuntimeSettings', {}).get('settings',{})
            transitional = transfer_preferences(settings, mapping)
            self.m.rpc('UpdateGrokBotUserRuntimeSettings', transitional)
            confirmed=self.m.rpc('GetGrokBotUserRuntimeSettings',{}).get('settings',{})
            if remap_preferences(confirmed,{}) != transitional:
                raise RuntimeError('Platform sidebar remap did not persist')
            host_settings = self.m.gateway('getHostSettings',{})
            self.m.write(pathlib.Path(journal['backup'])/'host-settings.before.json',host_settings)
            notifications = {k:v for k,v in host_settings.get('notifications',{}).items()
                             if k in ('isEnabled','allowedApps','minIntervalMs','maxPerWindow','windowMs')}
            self.m.gateway('setHostSettings', {'notifications':notifications,
                'sidebarSections':preserve_native_section_state(transitional['sidebarSections']['sections'],host_settings.get('sidebarSections',[])),
                'pinnedAgentIds':transitional['pinnedAgents']['agentIds']})
            after_host=self.m.gateway('getHostSettings',{})
            normalize=lambda sections:[{**section,'agentIds':section.get('agentIds',[]),
                'isCollapsed':section.get('isCollapsed',False)} for section in sections]
            if normalize(after_host.get('sidebarSections',[])) != preserve_native_section_state(transitional['sidebarSections']['sections'],host_settings.get('sidebarSections',[])) or after_host.get('pinnedAgentIds',[]) != transitional['pinnedAgents']['agentIds']:
                raise RuntimeError('Native sidebar remap did not persist')
            if after_host.get('notifications') != host_settings.get('notifications'):
                raise RuntimeError('Notification settings changed during remap')
            for entry in journal['entries']:
                if entry['phase'] == 'active' or entry['newId'] not in selected:
                    continue
                old,new=entry['oldId'],entry['newId']
                # Stop retired routines only; preserve later user changes on copies.
                for path in (SD/'agents'/old/'automations').rglob('automation.json'):
                    raw=self.m.checked_bytes(path);definition=json.loads(raw)
                    if definition.get('enabled') is True:
                        self.m.atomic_bytes(pathlib.Path(journal['backup'])/'retired-automations'/old/path.parent.name/'automation.json',raw)
                        self.m.atomic_bytes(path,self.m.json_bytes({**definition,'enabled':False}),expected=raw)
                copied_settings=owned_json(SD/'agents'/new/'settings.json')
                notification_state={k:copied_settings[k] for k in ('notificationsEnabled','notifyOnUpdatesEnabled')
                                    if isinstance(copied_settings.get(k),bool)}
                self.m.rpc('SetGrokBotAgentClientState',{'agentId':new,'hiddenFromSidebar':False,**notification_state})
                self.m.gateway('setAgentHiddenFromSidebar',{'id':new,'isHidden':False})
                for ancestor in (value for value,target in mapping.items() if target==new):
                    self.m.rpc('SetGrokBotAgentClientState',{'agentId':ancestor,'hiddenFromSidebar':True})
                    self.m.gateway('setAgentHiddenFromSidebar',{'id':ancestor,'isHidden':True})
                entry['phase']='active';self.save(journal)
                self.restore_entry_routines(journal,entry)
            self._finalize_preferences(journal, mapping)
            return results

    def _finalize_preferences(self, journal, mapping):
        # Read current preferences on resume so an intervening user edit is
        # retained. Collapsing paired IDs is idempotent, including a crash after
        # the last visibility write but before the final settings writes.
        settings = self.m.rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {})
        desired = remap_preferences(settings, mapping)
        if remap_preferences(settings, {}) != desired:
            self.m.rpc('UpdateGrokBotUserRuntimeSettings', desired)
        main = settings.get('mainAgentId')
        if main in mapping:
            self.m.rpc('SetGrokBotMainAgent', {'agentId': mapping[main]})
        confirmed = self.m.rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {})
        if remap_preferences(confirmed, {}) != desired:
            raise RuntimeError('Final platform sidebar remap did not persist')
        if confirmed.get('mainAgentId') != mapping.get(main, main):
            raise RuntimeError('Platform main-bot pointer did not persist')
        host = self.m.gateway('getHostSettings', {})
        sections = preserve_native_section_state(desired['sidebarSections']['sections'], host.get('sidebarSections', []))
        notifications = {k: v for k, v in host.get('notifications', {}).items()
                         if k in ('isEnabled', 'allowedApps', 'minIntervalMs', 'maxPerWindow', 'windowMs')}
        self.m.gateway('setHostSettings', {'notifications': notifications,
            'sidebarSections': sections, 'pinnedAgentIds': desired['pinnedAgents']['agentIds']})
        after = self.m.gateway('getHostSettings', {})
        normalize = lambda values: [{**s, 'agentIds': s.get('agentIds', []),
            'isCollapsed': s.get('isCollapsed', False)} for s in values]
        if normalize(after.get('sidebarSections', [])) != sections or after.get('pinnedAgentIds', []) != desired['pinnedAgents']['agentIds']:
            raise RuntimeError('Final native sidebar remap did not persist')
        if after.get('notifications') != host.get('notifications'):
            raise RuntimeError('Notification settings changed during final remap')
        self.refresh_peer_roster()
        if all(e['phase'] == 'active' for e in journal['entries']):
            journal['preferencesFinalized'] = True
            self.save(journal)
        return {'state': 'finalized', 'agentCount': len(mapping)}

    def finalize_preferences(self):
        """Finish interrupted visibility handoff without creating or hiding bots."""
        self.require_local_mode()
        with self.m.migration_lock():
            journal = self.read()
            if not journal['entries'] or any(e['phase'] != 'active' for e in journal['entries']):
                raise RuntimeError('Every copy must be active before finalizing preferences')
            if journal.get('preferencesFinalized') is True:
                return {'state': 'already-finalized', 'agentCount': len(journal['entries'])}
            mapping = lineage_mapping(journal, {e['newId'] for e in journal['entries']})
            return self._finalize_preferences(journal, mapping)

    def reselect_active(self):
        """Reuse verified copies after Official without copying another identity.

        A visible source with new history or changed instructions needs an
        explicit merge. Never conceal that branch merely to finish a switch.
        """
        self.require_local_mode()
        with self.m.migration_lock():
            journal = self.read()
            if not journal['entries'] or any(e['phase'] != 'active' for e in journal['entries']):
                raise RuntimeError('Every copy must already be active before reselection')
            mapping = {e['oldId']: e['newId'] for e in journal['entries']}
            settings = self.m.rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {})
            visible = any(not owned_json(SD/'agents'/e['oldId']/'settings.json').get('hiddenFromSidebar', False)
                          or owned_json(SD/'agents'/e['newId']/'settings.json').get('hiddenFromSidebar', False)
                          for e in journal['entries'])
            preferences_changed = remap_preferences(settings, mapping) != remap_preferences(settings, {}) or settings.get('mainAgentId') in mapping
            if not visible and not preferences_changed and journal.get('preferencesFinalized') is True:
                return {'state': 'already-selected', 'agentCount': len(mapping)}
            results = self.verify()
            if len(results) != len(mapping) or any(r['status'] != 'verified-box' for r in results):
                raise RuntimeError('Every existing BOX copy must be verified before reselection')
            rows = self.rows()
            indexed = {row['agentId']: row for row in rows}
            if len(indexed) != len(rows):
                raise RuntimeError('Ambiguous reselection roster')
            backup = SD/'recovery-snapshots'/('box-reselection-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
            backup.mkdir(parents=True, mode=0o700)
            self.m.write(backup/'platform-settings.json', settings)
            lineage = identity_mapping(journal)
            def entries(aid):
                with contextlib.closing(sqlite3.connect('file:' + str(SD/'agents'/aid/'store.db') + '?mode=ro', uri=True)) as conn:
                    return [json.loads(row[0]) for row in conn.execute('SELECT entry FROM transcript_entries ORDER BY seq')]
            def normalized(value):
                return json.dumps(self.m.remap_json(value, lineage), sort_keys=True, separators=(',', ':'))
            for entry in journal['entries']:
                old, new = entry['oldId'], entry['newId']
                source = indexed.get(old)
                if (not source or source.get('viewerIsOwner') is not True or source.get('legacyAgentId') != old
                        or str(source.get('id')) != entry['sourceServerId']):
                    raise RuntimeError('Source ownership or binding changed before reselection')
                if source.get('kind') != entry['kind']:
                    raise RuntimeError('Source kind changed before reselection')
                if self.publisher.require_box_row(indexed.get(new), new) != entry['serverId']:
                    raise RuntimeError('BOX journal binding changed before reselection')
                source_profile = owned_json(SD/'agents'/old/'profile.json')
                target_profile = owned_json(SD/'agents'/new/'profile.json')
                if str(source_profile.get('serverId')) != entry['sourceServerId']:
                    raise RuntimeError('Local source binding changed before reselection')
                role = lambda p: {k:v for k,v in p.items() if k not in ('serverId','harness')}
                if normalized(role(source_profile)) != normalized(role(target_profile)):
                    raise RuntimeError('Source/copy instructions differ; explicit reconciliation is required')
                cloud = self.m.fetch_transcript(old, backup)
                source_entries = entries(old) + [json.loads(base64.b64decode(r['body'], validate=True)) for r in cloud]
                target_entries = {self.transcript_key(value, lineage) for value in entries(new)}
                if any(self.transcript_key(value, lineage) not in target_entries for value in source_entries):
                    raise RuntimeError('Source has uncopied history; explicit reconciliation is required')
            # All pairs pass before the first visibility or scheduler mutation.
            for entry in journal['entries']:
                self.require_local_mode()
                old, new = entry['oldId'], entry['newId']
                self.pause_cloud_routines(old, backup)
                for path in (SD/'agents'/old/'automations').rglob('automation.json'):
                    raw = self.m.checked_bytes(path); definition = json.loads(raw)
                    if definition.get('enabled') is True:
                        self.m.atomic_bytes(backup/'retired-automations'/old/path.parent.name/'automation.json', raw)
                        self.m.atomic_bytes(path, self.m.json_bytes({**definition, 'enabled': False}), expected=raw,
                                            pre_commit=self.require_local_mode)
                self.require_local_mode()
                self.m.rpc('SetGrokBotAgentClientState', {'agentId':new, 'hiddenFromSidebar':False})
                self.require_local_mode()
                self.m.gateway('setAgentHiddenFromSidebar', {'id':new, 'isHidden':False})
                self.require_local_mode()
                self.m.rpc('SetGrokBotAgentClientState', {'agentId':old, 'hiddenFromSidebar':True})
                self.require_local_mode()
                self.m.gateway('setAgentHiddenFromSidebar', {'id':old, 'isHidden':True})
            self.require_local_mode()
            self._finalize_preferences(journal, mapping)
            return {'state':'reselected', 'agentCount':len(mapping), 'backup':str(backup),
                    'liveHistoryMerged':False, 'sdkContextsMerged':False, 'pruneAllowed':False}

    def checked_routine_states(self, aid, states):
        """Accept only captured switches for owned, unchanged routine paths."""
        canonical(aid)
        if not isinstance(states,dict):
            raise RuntimeError('Missing copied routine switch authority')
        for relative,enabled in states.items():
            path=pathlib.PurePosixPath(relative)
            if (type(enabled) is not bool or len(path.parts)!=3 or
                    path.parts[0]!='automations' or path.parts[2]!='automation.json' or
                    str(path)!=relative or path.parts[1] in ('.','..')):
                raise RuntimeError('Invalid captured routine switch')
            directory=SD/'agents'/aid/'automations'/path.parts[1]
            for parent in [SD/'agents'/aid,SD/'agents'/aid/'automations',directory]:
                info=parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid():
                    raise RuntimeError('Copied routine directory is missing, foreign or symlinked')
            if type(owned_json(directory/'automation.json').get('enabled')) is not bool:
                raise RuntimeError('Copied routine switch is not a boolean')
        return states

    def restore_entry_routines(self, journal, entry):
        if entry['phase']!='active':
            raise RuntimeError('Unactivated copies must remain paused')
        if entry.get('routinesRestored') is True:
            return {'newId':entry['newId'],'state':'already-restored','settingsChanged':False}
        aid=entry['newId']
        profile=owned_json(SD/'agents'/aid/'profile.json')
        sid=self.publisher.require_box_row({r['agentId']:r for r in self.rows()}.get(aid),aid)
        if sid!=entry['serverId'] or profile.get('serverId')!=sid or profile.get('harness')!='box':
            raise RuntimeError('BOX binding changed before restoring routines')
        states=self.checked_routine_states(aid,entry.get('activationAutomationStates',
                                                      entry.get('copyProof',{}).get('automationStates')))
        for relative in states:
            if owned_json(SD/'agents'/entry['oldId']/relative).get('enabled') is not False:
                raise RuntimeError('Retired local routine is still enabled')
        self.pause_cloud_routines(entry['oldId'],pathlib.Path(journal['backup']))
        native=self.m.gateway('getAgentAutomations',{'id':aid})
        if not isinstance(native,list):
            raise RuntimeError('Unknown native routine list schema')
        indexed={r['id']:r for r in native}
        if len(indexed)!=len(native):
            raise RuntimeError('Native routine identifiers are ambiguous')
        progress=entry.setdefault('routineRestoreProgress',{})
        restored=0
        for relative,enabled in states.items():
            if relative in progress:continue  # Preserve later user pauses on retry.
            rid=pathlib.PurePosixPath(relative).parts[1]
            if rid not in indexed or type(indexed[rid].get('isEnabled')) is not bool:
                raise RuntimeError('Native runner did not load the copied routine')
            if enabled:
                after=self.m.gateway('setAgentAutomationEnabled',
                    {'id':aid,'automationId':rid,'isEnabled':True})
                confirmed=[r for r in after if r.get('id')==rid] if isinstance(after,list) else []
                if (len(confirmed)!=1 or confirmed[0].get('isEnabled') is not True or
                        owned_json(SD/'agents'/aid/relative).get('enabled') is not True):
                    raise RuntimeError('Copied routine enable did not persist in the native runner')
                restored+=1
            progress[relative]=enabled;self.save(journal)
        entry['routinesRestored']=True;self.save(journal)
        return {'newId':aid,'state':'restored','enabled':restored,
                'preservedDisabled':sum(enabled is False for enabled in states.values())}

    def restore_routines(self, aid=None):
        """Resume only captured source-enabled routines on verified active copies."""
        self.require_local_mode()
        with self.m.migration_lock():
            journal=self.read()
            selected=[e for e in journal['entries'] if aid is None or aid in (e['oldId'],e['newId'])]
            if not selected:raise RuntimeError('No copy selected for routine restoration')
            return [self.restore_entry_routines(journal,entry) for entry in selected]

    def pause_cloud_routines(self, aid, backup):
        """Preserve and disable the server schedules, with a read-after-write check."""
        canonical(aid)
        original = self.m.rpc('ListGrokBotAgentAutomations', {'agentId':aid})
        records = original.get('automations', [])
        if not isinstance(records, list):
            raise RuntimeError('Unknown cloud routine list schema')
        enabled = []
        for wrapper in records:
            record = json.loads(wrapper['recordJson'])
            if not isinstance(record, dict) or not isinstance(record.get('isEnabled'), bool):
                raise RuntimeError('Unknown cloud routine record schema')
            if record['isEnabled']:
                enabled.append(canonical(wrapper['automationId']))
        path = backup / 'retired-server-automations' / (aid + '.json')
        if not path.exists():
            self.m.write(path, original)
        for automation_id in enabled:
            self.m.rpc('SetGrokBotAgentAutomationEnabled',
                       {'agentId':aid,'automationId':automation_id,'isEnabled':False})
        # A current all-disabled response already proves the state when no
        # mutation was needed. Only writes need the additional confirmation;
        # archived generations otherwise double the handoff's RPC budget.
        confirmed = self.m.rpc('ListGrokBotAgentAutomations', {'agentId':aid}) if enabled else original
        if any(json.loads(row['recordJson']).get('isEnabled') is not False
               for row in confirmed.get('automations', [])):
            raise RuntimeError('Retired cloud routines did not remain paused')
        return {'agentId':aid,'paused':len(enabled),'verifiedDisabled':True}

    def refresh_peer_roster(self, existing_copies=None):
        """Remap the existing colleague allowlist; do not invent or send to peers."""
        self.require_local_mode()
        journal = self.read()
        mapping = lineage_mapping(journal, {e['newId'] for e in journal['entries'] if e['phase']=='active'})
        proven=dict(mapping)
        for old,new in (existing_copies or {}).items():
            canonical(old);canonical(new)
            if old in mapping and mapping[old] != new:
                raise RuntimeError('Existing copy conflicts with the active journal')
            if old == new:
                raise RuntimeError('Existing copy must have a separate identity')
            mapping[old] = new
        if any(sum(value==new for value in mapping.values())>1 and proven.get(old)!=new for old,new in mapping.items()) or set(mapping) & set(mapping.values()):
            raise RuntimeError('Peer identity map is not one-to-one')
        path = SD/'openbot-recovery'/'roster.json'
        if not path.exists() and not path.is_symlink():
            return {'status':'absent','changed':0}
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Peer roster must be an owned regular file')
        raw = self.m.checked_bytes(path)
        records = json.loads(raw)
        if not isinstance(records,list) or any(not isinstance(r,dict) for r in records):
            raise RuntimeError('Peer roster must be a list of identity records')
        old_ids = [canonical(r.get('id')) for r in records]
        if len(set(old_ids)) != len(old_ids):
            raise RuntimeError('Peer roster contains duplicate identities')
        rows = {r['agentId']:r for r in self.rows()}
        native_rows = self.m.gateway('listAgents',{})
        native = {r['id']:r for r in (native_rows if isinstance(native_rows,list) else native_rows.get('agents',[]))}
        updated=[];changed=0;existing_proofs=[]
        for record in records:
            old = record['id']; new = mapping.get(old)
            if new is None:
                updated.append(record);continue
            source = SD/'agents'/old; destination = SD/'agents'/new
            profile = owned_json(destination/'profile.json')
            self.publisher.require_safe_profile(profile,new)
            sid = self.publisher.require_box_row(rows.get(new),new)
            if profile.get('serverId') != sid or native.get(new,{}).get('harness') != 'box':
                raise RuntimeError('Peer copy is not bound and advertised as BOX')
            if owned_json(source/'settings.json').get('hiddenFromSidebar') is not True or owned_json(destination/'settings.json').get('hiddenFromSidebar',False):
                raise RuntimeError('Peer copy has not completed visibility cutover')
            if old in (existing_copies or {}):
                # Separate Q/Dating recovery has no entry in this batch journal.
                # Explicit mapping requires matching role, retained source
                # payloads, and the destination's actual published payload/order.
                before = owned_json(source/'profile.json')
                if before.get('description','') != profile.get('description',''):
                    raise RuntimeError('Existing peer copy has a different role')
                histories=[]
                for directory in (source,destination):
                    conn=sqlite3.connect('file:'+str(directory/'store.db')+'?mode=ro',uri=True)
                    try:
                        histories.append([json.loads(r[0]) for r in conn.execute('SELECT entry FROM transcript_entries ORDER BY seq')])
                    finally:
                        conn.close()
                source_entries,target_entries=histories
                source_ids,target_ids=([item['id'] for item in entries] for entries in histories)
                kept=set(source_ids)
                if not kept.issubset(set(target_ids)):
                    raise RuntimeError('Existing peer copy did not retain source history IDs')
                lineage={**identity_mapping(journal),**mapping}
                target_payloads={self.transcript_key(item,lineage) for item in target_entries}
                if any(self.transcript_key(item,lineage) not in target_payloads for item in source_entries):
                    raise RuntimeError('Existing peer copy did not retain source history payload')
                published={};before_seq=None;generation=None
                while True:
                    request={'agentId':new,'limit':200}
                    if before_seq is not None:request['beforeSeq']=str(before_seq)
                    if generation is not None:request['generation']=generation
                    page=self.m.rpc('ListGrokBotTranscriptEntries',request)
                    if generation is not None and page.get('generation',generation)!=generation:
                        raise RuntimeError('Existing peer transcript generation changed')
                    generation=page.get('generation',generation)
                    entries=page.get('entries',[])
                    if not entries:break
                    for entry in entries:
                        seq=int(entry['seq']);payload=json.loads(base64.b64decode(entry['body'],validate=True))
                        if seq in published and published[seq]!=payload:
                            raise RuntimeError('Existing peer transcript payload changed during pagination')
                        published[seq]=payload
                    earliest=min(int(entry['seq']) for entry in entries)
                    if earliest<=1:break
                    if before_seq is not None and earliest>=before_seq:
                        raise RuntimeError('Existing peer transcript pagination did not advance')
                    before_seq=earliest
                cloud_entries=[published[seq] for seq in sorted(published)]
                cloud_ids=[item['id'] for item in cloud_entries]
                if cloud_ids[:len(target_ids)] != target_ids:
                    raise RuntimeError('Existing peer copy order differs from its published history')
                if cloud_entries[:len(target_entries)]!=target_entries:
                    raise RuntimeError('Existing peer published history payload differs from its local copy')
                existing_proofs.append({'sourceId':old,'targetId':new,'sourceEntries':len(source_ids),
                    'sourceIdsRetained':True,'destinationPublicationVerified':True,
                    'sourcePayloadsRetained':True,'destinationPayloadsPublished':True,
                    'sourceOrderPreserved':[item for item in target_ids if item in kept]==source_ids})
            current=native[new]
            members=current.get('memberIds',[]) if current.get('isGroup') is True else []
            if current.get('isGroup') is True:
                if not members or len(set(members)) != len(members):
                    raise RuntimeError('Peer room lacks explicit unique responders')
                for member in members:
                    self.publisher.require_box_row(rows.get(canonical(member)),member)
            previous=record.get('previousIds',[])
            if not isinstance(previous,list):
                raise RuntimeError('Peer roster ancestry must be a list')
            previous=[canonical(value) for value in previous]
            if old not in previous:previous.append(old)
            updated.append({**record,'id':new,'name':current.get('name',record.get('name','')),
                            'isGroup':current.get('isGroup',False),'memberIds':members,'previousIds':previous})
            changed+=1
        consolidated={}
        for record in updated:
            target=record['id']
            if target not in consolidated:
                consolidated[target]=record;continue
            prior=consolidated[target]
            comparable=lambda value:{k:v for k,v in value.items() if k!='previousIds'}
            if comparable(prior)!=comparable(record):
                raise RuntimeError('Peer remap would introduce duplicate identities with conflicting metadata')
            previous=list(dict.fromkeys([*prior.get('previousIds',[]),*record.get('previousIds',[])]))
            if any(mapping.get(ancestor)!=target for ancestor in previous if ancestor in mapping):
                raise RuntimeError('Peer remap would introduce duplicate identities')
            consolidated[target]={**prior,'previousIds':previous}
        updated=list(consolidated.values())
        if changed:
            backup=pathlib.Path(journal['backup'])/'peer-roster-before.json'
            if not backup.exists():self.m.atomic_bytes(backup,raw,expected=None)
            self.m.atomic_bytes(path,self.m.json_bytes(updated),expected=raw)
        return {'status':'updated' if changed else 'unchanged','changed':changed,'entries':len(records),
                'existingCopyProofs':existing_proofs}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','init','stage','accept-history-merge','restage-room','register','publish','verify','activate','restore-routines','refresh-roster'))
    parser.add_argument('--agent-id')
    parser.add_argument('--return-from-terminal',action='store_true')
    parser.add_argument('--receipt')
    parser.add_argument('--next-generation',action='store_true',help='For init only: preserve a completed journal and stage another observed Temporal-to-BOX recovery.')
    parser.add_argument('--native-box-room',action='store_true',help='Register an existing native BOX group as BOX AGENT; preserves local membership, does not repair platform ROOM kind.')
    parser.add_argument('--adopt-copy',help='For restage-room only: reuse a verified hidden BOX copy with this UUID.')
    parser.add_argument('--existing-copy',action='append',default=[],metavar='OLD=NEW',help='For refresh-roster only: verify a separately recovered complete BOX copy before remapping its existing peer entry.')
    args=parser.parse_args();r=Reminter()
    if args.return_from_terminal and (args.command!='init' or not args.next_generation):parser.error('--return-from-terminal requires init --next-generation')
    if args.receipt and args.command!='accept-history-merge':parser.error('--receipt is for accept-history-merge only')
    if args.next_generation and args.command!='init':parser.error('--next-generation is for init only')
    if args.native_box_room and args.command!='register':parser.error('--native-box-room is for register only')
    if args.adopt_copy and args.command!='restage-room':parser.error('--adopt-copy is for restage-room only')
    if args.existing_copy and args.command!='refresh-roster':parser.error('--existing-copy is for refresh-roster only')
    if args.command in ('stage','register','restage-room','accept-history-merge') and not args.agent_id:
        parser.error('--agent-id is required for writes to a copy')
    if args.command=='refresh-roster':
        mapping={}
        for item in args.existing_copy:
            parts=item.split('=')
            if len(parts)!=2 or parts[0] in mapping:parser.error('Use unique --existing-copy OLD=NEW pairs')
            mapping[canonical(parts[0])]=canonical(parts[1])
        with r.m.migration_lock():result=r.refresh_peer_roster(mapping)
    elif args.command in ('verify','activate','restore-routines'):result=getattr(r,args.command.replace('-','_'))(args.agent_id)
    elif args.command=='register':result=r.register(args.agent_id,args.native_box_room)
    elif args.command=='accept-history-merge':
        if not args.receipt:parser.error('--receipt is required')
        result=r.accept_history_merge(args.agent_id,args.receipt)
    elif args.command=='stage':result=r.stage(args.agent_id)
    elif args.command=='restage-room':result=r.restage_room(args.agent_id,args.adopt_copy)
    elif args.command=='init':result=r.initialize(args.next_generation,args.return_from_terminal)
    else:result=getattr(r,{'init':'initialize'}.get(args.command,args.command))()
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
