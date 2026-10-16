#!/usr/bin/env python3
"""Route Official's roster to owned stock counterparts without deleting BOX state.

This restores identity selection only. Divergent SDK context and transcript
branches remain preserved; this is not proof of a completed live history merge.
"""
import argparse
import importlib.util
import json
import pathlib
import time
import uuid

SD = pathlib.Path('/home/box/sand-data')


def load():
    spec = importlib.util.spec_from_file_location('official_reminter',
        pathlib.Path(__file__).with_name('remint-box-fleet.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class Recovery:
    def __init__(self, root=SD, reminter=None):
        self.root = pathlib.Path(root)
        self.module = load()
        self.r = reminter or self.module.Reminter()

    def require(self):
        if (self.root / 'openbot-mode').read_text().strip() != 'official':
            raise RuntimeError('Official selection changed; handoff cancelled')

    def rpc(self, method, body):
        self.require()
        return self.r.m.rpc(method, body)

    def gateway(self, method, body):
        self.require()
        return self.r.m.gateway(method, body)

    def confirmed_mapping(self):
        path = self.root / 'openbot-official-dispatch-state.json'
        if not path.exists() and not path.is_symlink():
            return {}
        state = self.module.owned_json(path)
        if state.get('phase') != 'stock-roster-selected' or state.get('mode') != 'official':
            return {}
        mapping = state.get('identityMapping')
        if not isinstance(mapping, dict):
            raise RuntimeError('Confirmed stock selection mapping is invalid')
        for source, target in mapping.items():
            self.module.canonical(source)
            self.module.canonical(target)
        return mapping

    def targets(self, journal, rows):
        by_id = {row['agentId']: row for row in rows}
        if len(by_id) != len(rows):
            raise RuntimeError('Platform roster contains duplicate identities')
        confirmed = self.confirmed_mapping()
        mapping, selected = {}, []
        for entry in journal['entries']:
            if entry['phase'] != 'active':
                raise RuntimeError('Incomplete remint must be reviewed before Official roster handoff')
            old, new = self.module.canonical(entry['oldId']), self.module.canonical(entry['newId'])
            current, previous = by_id.get(new), by_id.get(old)
            # A migrated plain agent is not a replacement for a typed room.
            target = current if current and current.get('harness') == 'temporal' and \
                current.get('kind') == entry['kind'] else previous
            # Upstream can migrate a preserved BOX copy after Official already
            # selected its stock peer. That is not a request to change the
            # conversation identity. Revalidate the confirmed selection below.
            # Older receipts contain only new -> old, without old -> old.
            remembered = confirmed.get(new)
            if remembered in (old, new):
                if old in confirmed and confirmed[old] != remembered:
                    raise RuntimeError('Confirmed stock selection mapping is inconsistent')
                candidate = by_id.get(remembered)
                if candidate and candidate.get('harness') == 'temporal' and \
                        candidate.get('kind') == entry['kind']:
                    target = candidate
            if not target or target.get('harness') != 'temporal' or target.get('viewerIsOwner') is not True:
                raise RuntimeError('No verified owned stock counterpart exists')
            if target.get('kind') != entry['kind']:
                raise RuntimeError('Stock counterpart kind changed')
            aid = target['agentId']
            if target.get('legacyAgentId') != aid:
                raise RuntimeError('Stock counterpart identity is ambiguous')
            expected_sid = entry['sourceServerId'] if aid == old else entry['serverId']
            if str(target.get('id')) != str(expected_sid):
                raise RuntimeError('Stock counterpart binding changed')
            profile = self.module.owned_json(self.root / 'agents' / aid / 'profile.json')
            if str(profile.get('serverId')) != str(target['id']):
                raise RuntimeError('Native stock counterpart binding differs')
            for candidate, expected in ((old, entry['sourceServerId']), (new, entry['serverId'])):
                row = by_id.get(candidate)
                if not row or row.get('viewerIsOwner') is not True or row.get('legacyAgentId') != candidate or \
                        str(row.get('id')) != str(expected):
                    raise RuntimeError('Counterpart ownership or binding changed before visibility handoff')
                if candidate in mapping:
                    raise RuntimeError('Official handoff pairs overlap')
                mapping[candidate] = aid
            selected.append((entry, aid))
        return mapping, selected

    def plan(self):
        """Inspect current authority without applying visibility or profile writes."""
        self.require()
        journal = self.r.read()
        mapping, selected = self.targets(journal, self.rpc('ListGrokBotAgents', {}).get('agents', []))
        return {'phase': 'planned', 'mode': 'official', 'agentCount': len(selected),
                'identityMapping': mapping, 'liveHistoryMerged': False, 'pruneAllowed': False}

    def run(self):
        self.require()
        with self.r.m.migration_lock():
            journal = self.r.read()
            mapping, selected = self.targets(journal, self.rpc('ListGrokBotAgents', {}).get('agents', []))
            settings = self.rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {})
            host = self.gateway('getHostSettings', {})
            backup = self.root / 'recovery-snapshots' / ('official-roster-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
            backup.mkdir(mode=0o700, parents=True)
            self.r.m.write(backup / 'platform-settings.json', settings)
            self.r.m.write(backup / 'native-settings.json', host)
            paired = self.module.transfer_preferences(settings, mapping)
            self.rpc('UpdateGrokBotUserRuntimeSettings', paired)
            for entry, aid in selected:
                self.require()
                path = self.root / 'agents' / aid / 'profile.json'; before = self.r.m.checked_bytes(path)
                profile = json.loads(before)
                self.r.m.atomic_bytes(backup / (aid + '.profile.json'), before)
                if profile.get('harness') != 'temporal':
                    self.r.m.atomic_bytes(path, self.r.m.json_bytes({**profile, 'harness': 'temporal'}),
                                          expected=before, pre_commit=self.require)
                self.rpc('SetGrokBotAgentClientState', {'agentId': aid, 'hiddenFromSidebar': False})
                self.gateway('setAgentHiddenFromSidebar', {'id': aid, 'isHidden': False})
                for counterpart in (entry['oldId'], entry['newId']):
                    if counterpart != aid:
                        self.rpc('SetGrokBotAgentClientState', {'agentId': counterpart, 'hiddenFromSidebar': True})
                        self.gateway('setAgentHiddenFromSidebar', {'id': counterpart, 'isHidden': True})
                # BOX DBs, contexts, routines and model overrides stay untouched.
            desired = self.module.remap_preferences(settings, mapping)
            self.rpc('UpdateGrokBotUserRuntimeSettings', desired)
            main = settings.get('mainAgentId')
            if main in mapping and mapping[main] != main:
                self.rpc('SetGrokBotMainAgent', {'agentId': mapping[main]})
            notifications = {k: v for k, v in host.get('notifications', {}).items()
                             if k in ('isEnabled', 'allowedApps', 'minIntervalMs', 'maxPerWindow', 'windowMs')}
            sections = self.module.preserve_native_section_state(desired['sidebarSections']['sections'], host.get('sidebarSections', []))
            self.gateway('setHostSettings', {'notifications': notifications, 'sidebarSections': sections,
                                            'pinnedAgentIds': desired['pinnedAgents']['agentIds']})
            confirmed = self.rpc('GetGrokBotUserRuntimeSettings', {}).get('settings', {})
            if self.module.remap_preferences(confirmed, {}) != desired or confirmed.get('mainAgentId') != mapping.get(main, main):
                raise RuntimeError('Official platform sections/main pointer did not persist')
            native = self.gateway('getHostSettings', {})
            actual_sections = [{**s, 'agentIds': s.get('agentIds', []), 'isCollapsed': s.get('isCollapsed', False)}
                               for s in native.get('sidebarSections', [])]
            if actual_sections != sections or native.get('pinnedAgentIds', []) != desired['pinnedAgents']['agentIds']:
                raise RuntimeError('Official native sections/pins did not persist')
            if native.get('notifications') != host.get('notifications'):
                raise RuntimeError('Official handoff changed notification settings')
            for entry, aid in selected:
                if self.module.owned_json(self.root/'agents'/aid/'settings.json').get('hiddenFromSidebar',False):
                    raise RuntimeError('Stock counterpart remains hidden')
                for counterpart in (entry['oldId'], entry['newId']):
                    if counterpart != aid and not self.module.owned_json(self.root/'agents'/counterpart/'settings.json').get('hiddenFromSidebar',False):
                        raise RuntimeError('Unselected counterpart remains visible in Official')
            result = {'phase': 'stock-roster-selected', 'mode': 'official', 'agentCount': len(selected),
                      'identityMapping': mapping, 'backup': str(backup), 'liveHistoryMerged': False,
                      'nativeAppDeliveryVerified': False, 'pruneAllowed': False}
            self.require(); self.r.m.write(self.root / 'openbot-official-dispatch-state.json', result)
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true', help='Inspect the live roster without applying changes')
    args = parser.parse_args()
    recovery = Recovery()
    print(json.dumps(recovery.plan() if args.plan else recovery.run()))


if __name__ == '__main__':
    main()
