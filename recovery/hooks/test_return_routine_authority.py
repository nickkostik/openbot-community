"""Selected routine authority, immutable pause captures, and safe restoration.

The routine materializer below is an injected native-codec boundary. It tests
Python proof binding and switch ownership, not native codec execution.
"""
import base64
import contextlib
import copy
import importlib.util
import json
import pathlib
import shutil
import sqlite3
import unittest

import test_remint_box_fleet as fleet

SPEC = importlib.util.spec_from_file_location('return_routine_authority', pathlib.Path(__file__).with_name('return-routine-authority.py'))
R = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)
THIRD = '00000000-0000-4000-8000-000000000004'
ROUTINE = '00000000-0000-4000-8000-000000000005'
NEW_RECIPE = '00000000-0000-4000-8000-000000000006'


def native_run(identifier, started, status='ok'):
    return {'id': identifier, 'trigger': 'manual', 'startedAt': started,
            'finishedAt': None if status == 'running' else started + 1, 'status': status}


class RoutineAuthorityTests(unittest.TestCase):
    write = fleet.RemintTests.write
    rpc = fleet.RemintTests.rpc
    gateway = fleet.RemintTests.gateway

    def protected(self):
        return {p: p.read_bytes() for root in (self.source, self.target) for p in root.rglob('*') if p.is_file()}

    def assert_protected(self, before):
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertFalse(any(name.startswith(('Create', 'Set', 'Update')) for name, _ in self.calls))
        self.assertEqual(self.gateway_calls, [])

    def setUp(self):
        fleet.RemintTests.setUp(self)
        self.r.stage(fleet.OLD); self.r.register(fleet.OLD); self.r.activate()
        self.tail = fleet.M.helper('history-tail-repair')
        self.validator = fleet.M.helper('history-merge-receipt')
        self.target = self.root / 'agents' / fleet.NEW
        self.write(self.root / 'settings.json', {'userTimeZone': 'America/Chicago', 'unrelated': True})
        local_path = self.target / 'automations/routine/automation.json'
        value = json.loads(local_path.read_bytes()); value['enabled'] = False; self.write(local_path, value)
        self.write(self.target / 'automations/routine/runs.json', [native_run('prior-local-run', 100)])
        self.write(self.source / 'automations/routine/runs.json', [native_run('prior-official-local-run', 200)])
        self.folder = self.backup / 'routine-candidate'; self.folder.mkdir(mode=0o700)
        for aid in (fleet.OLD, fleet.NEW):
            shutil.copytree(self.root / 'agents' / aid, self.folder / 'sources' / aid)
        self.record = {'id': 'routine', 'name': 'Official schedule', 'prompt': 'Changed Official definition',
                       'trigger': {'type': 'cron', 'schedule': '0 * * * *', 'firesAt': 100},
                       'schedule': '0 * * * *', 'isEnabled': True, 'createdAt': 1, 'lastRunAt': None,
                       'provenance': 'user', 'sessionId': fleet.OLD, 'runs': [native_run('cloud-run', 300)]}
        self.wrappers = [{'automationId': ROUTINE, 'recordJson': json.dumps(self.record)}]
        self.write_cloud()
        self.mapping = {fleet.OLD: THIRD, fleet.NEW: THIRD}
        self.materializer = self.codec
        self.current = self.r.read()['entries'][0]
        self.indexed = {row['agentId']: row for row in self.r.rows()}
        self.codec_plans = []
        self.write(self.root / 'openbot-official-dispatch-state.json', {
            'phase': 'stock-roster-selected', 'mode': 'official', 'identityMapping': {fleet.NEW: fleet.OLD}})
        self.calls.clear(); self.gateway_calls.clear()

    def write_cloud(self):
        self.cloud_routines[fleet.OLD] = copy.deepcopy(self.wrappers)
        self.write(self.folder / 'routines' / (fleet.OLD + '.json'), {'automations': self.wrappers})
        self.write(self.folder / 'routines' / (fleet.NEW + '.json'), {'automations': []})

    def codec(self, folder, plan):
        self.codec_plans.append(copy.deepcopy(plan)); output = []
        for source in plan['routines']:
            record = json.loads(source['wrapper']['recordJson']); rid = source['id']
            config_path = 'automations/' + rid + '/automation.json'; runs_path = 'automations/' + rid + '/runs.json'
            config = {key: value for key, value in record.items() if key in R.FIELDS or key in ('createdAt', 'lastRunAt')}
            config['enabled'] = False
            runs = list(record.get('runs', []))
            for previous in source['previousFiles']:
                if previous['kind'] == 'runs':
                    runs.extend(json.loads(pathlib.Path(previous['path']).read_bytes()))
            unique = {self.tail.encoded(run): run for run in runs}
            retained = sorted(unique.values(), key=lambda run: run['startedAt'], reverse=True)
            projection = retained[:20]
            self.tail.write(folder / config_path, config, True)
            self.tail.write(folder / runs_path, retained, True)
            output.append({'id': rid, 'sourceAutomationId': source['wrapper']['automationId'],
                'sourceRecordSha256': source['sourceRecordSha256'], 'previousFiles': source['previousFiles'],
                'configPath': config_path, 'runsPath': runs_path, 'desiredEnabled': record['isEnabled'],
                'configSha256': self.tail.sha((folder / config_path).read_bytes()),
                'runsSha256': self.tail.sha((folder / runs_path).read_bytes()),
                'nativeVisibleProjection': projection,
                'nativeVisibleProjectionJson': self.tail.encoded(projection).decode(),
                'nativeVisibleProjectionSha256': self.tail.sha(self.tail.encoded(projection)),
                'nativeVisibleRunCount': len(projection), 'nativeRunHistoryLimit': 20, 'retainedRunCount': len(retained),
                **{key: True for key in ('allPaused', 'fullHistoricalRunsRetained', 'nativeSerdeVerified', 'nativeStoreVerified')}})
        proof = {'schema': 'openbot-return-routine-codec/v1', 'engineSha256': self.tail.REVIEWED_HOST_SHA,
                 'bridgeSha256': self.tail.sha(pathlib.Path(R.__file__).with_name('return-routine-codec.mjs').read_bytes()),
                 'sourcePlanFileSha256': self.tail.sha((folder / 'routine-plan.json').read_bytes()),
                 'targetId': plan['targetId'], 'routines': output,
                 **{key: True for key in ('allPaused', 'authoredSemanticsVerified', 'previousArchivesUnchanged')},
                 **{key: False for key in ('sourceUpdated', 'liveApplied', 'historicalActionsReplayed')},
                 'providerCalls': 0, 'networkAttempts': 0, 'toolAttempts': 0}
        self.tail.write(folder / 'return-routine-proof.json', proof, True)
        return proof

    def prepare(self):
        # This immutable authority is produced at a reviewed materializer
        # boundary. The consumer under test does not depend on FreshReturn.
        records = R.cloud_records(self.r, self.wrappers)
        paths = {str(path.relative_to(self.folder / 'sources' / fleet.NEW)): json.loads(path.read_bytes())
                 for path in (self.folder / 'sources' / fleet.NEW / 'automations').rglob('automation.json')}
        rows = []
        for wrapper in self.wrappers:
            record = json.loads(wrapper['recordJson']); rid = record['id']; previous_files = []
            for directory in sorted((self.folder / 'sources').iterdir()):
                for name, kind in (('automation.json', 'config'), ('runs.json', 'runs')):
                    path = directory / 'automations' / rid / name
                    if path.exists(): previous_files.append({'kind': kind, **self.tail.reference(path)})
            mapped = self.r.m.remap_json(record, self.mapping)
            raw = self.tail.encoded(mapped).decode()
            rows.append({'id': rid, 'wrapper': {'automationId': wrapper['automationId'], 'recordJson': raw},
                         'sourceRecordSha256': self.tail.sha(raw.encode()), 'previousFiles': previous_files})
        plan = {'schemaVersion': 1, 'targetId': THIRD, 'userTimeZone': 'America/Chicago', 'routines': rows}
        self.tail.write(self.folder / 'routine-plan.json', plan, True)
        proof = self.materializer(self.folder, plan)
        R.validate_codec(self.r, self.folder, plan, proof, self.tail, fleet.M)
        states = {relative: False for relative in paths}
        states.update({row['configPath']: row['desiredEnabled'] for row in proof['routines']})
        self.tail.write(self.folder / 'time-zone-settings.json', json.loads((self.root / 'settings.json').read_bytes()), True)
        authority = {'schema': R.SCHEMA, 'sourceId': fleet.NEW, 'newId': THIRD, 'ownerId': fleet.OLD,
            'ownerServerId': '123', 'selection': 'official-selection', 'localDefinitions': paths,
            'cloudRecords': records, 'matched': {row['sourceAutomationId']: row['configPath'] for row in proof['routines']},
            'states': states, 'capturedCloud': self.tail.reference(self.folder / 'routines' / (fleet.OLD + '.json')),
            'nativeProof': self.tail.reference(self.folder / 'return-routine-proof.json'),
            'nativePlan': self.tail.reference(self.folder / 'routine-plan.json'),
            'timeZoneSettings': self.tail.reference(self.folder / 'time-zone-settings.json'),
            'materialized': sorted(path for row in proof['routines'] for path in (row['configPath'], row['runsPath']))}
        self.tail.write(self.folder / 'routine-authority.json', authority, True)
        reference = self.tail.reference(self.folder / 'routine-authority.json')
        previous = self.r.read()
        entry = {'oldId': fleet.NEW, 'newId': THIRD, 'sourceServerId': '456', 'serverId': '789',
                 'selectionSourceId': fleet.OLD, 'selectionSourceServerId': '123', 'name': 'Specialist',
                 'kind': fleet.KIND, 'phase': 'copied', 'returnRoutineAuthority': reference,
                 'activationAutomationStates': states}
        journal = {'backup': str(self.backup), 'previousGenerations': [previous], 'entries': [entry]}
        target = self.root / 'agents' / THIRD; shutil.copytree(self.target, target)
        for relative in authority['materialized']:
            destination = target / relative; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.folder / relative, destination)
        profile = json.loads((target / 'profile.json').read_bytes()); profile['serverId'] = '789'
        self.write(target / 'profile.json', profile)
        self.roster.append(fleet.row(THIRD, 'box', '789')); self.r.save(journal)
        return journal, entry, authority

    def pin_authority(self, journal, entry, authority):
        self.tail.write(self.folder / 'routine-authority.json', authority)
        entry['returnRoutineAuthority'] = self.tail.reference(self.folder / 'routine-authority.json')
        self.r.save(journal)

    def evidence(self, journal, entry, authority, local_pair=False):
        capture = {'schema': 'openbot-retiring-routine-owner/v1', 'agentId': fleet.OLD,
            'destinationId': THIRD, 'cloudAuthority': 'server', 'sourceRow': fleet.row(fleet.OLD, 'temporal', '123'),
            'cloudRaw': {'automations': copy.deepcopy(self.wrappers)},
            'originalDesiredCloud': {row['automationId']: json.loads(row['recordJson'])['isEnabled'] for row in self.wrappers}}
        paused_rows = []
        for row in self.wrappers:
            value = json.loads(row['recordJson']); value['isEnabled'] = False
            paused_rows.append({**row, 'recordJson': json.dumps(value)})
        paused = {'cloud': {'automations': paused_rows}}
        documents = {'capture': capture, 'pausedReadback': paused, 'quiesceJournal': {'operation': 'retained pause receipt'}}
        if local_pair:
            rows = []; after = []
            for relative, record in authority['localDefinitions'].items():
                raw = (self.folder / 'sources' / fleet.NEW / relative).read_bytes()
                def wrapper(raw, record):
                    return {'nativePath': relative, 'record': record, 'enabled': record['enabled'],
                            'rawBase64': base64.b64encode(raw).decode(), 'sha256': self.validator.digest(raw)}
                rows.append(wrapper(raw, record))
                value = {**record, 'enabled': False}; after.append(wrapper(self.tail.encoded(value), value))
            documents['localCapture'] = {'schema': 'openbot-retiring-routine-owner/v1', 'agentId': fleet.NEW,
                'destinationId': THIRD, 'sourceRow': fleet.row(fleet.NEW, 'box', '456'), 'profile': {'serverId': '456'},
                'localRoutines': rows, 'originalDesiredLocal': {key: value['enabled'] for key, value in authority['localDefinitions'].items()}}
            documents['localPausedReadback'] = {'local': after}
        authority['quiescenceEvidence'] = {}
        for key, value in documents.items():
            path = self.folder / (key + '.json'); self.tail.write(path, value)
            authority['quiescenceEvidence'][key] = self.tail.reference(path)
        return documents

    def repin_evidence(self, authority, key, document):
        path = self.folder / (key + '.json'); self.tail.write(path, document)
        authority['quiescenceEvidence'][key] = self.tail.reference(path)

    def test_selected_official_true_supersedes_stale_local_false_and_keeps_original_bytes(self):
        raw = (self.folder / 'routines' / (fleet.OLD + '.json')).read_bytes()
        before = self.protected(); journal, entry, authority = self.prepare()
        self.assertEqual(authority['selection'], 'official-selection')
        self.assertIs(authority['localDefinitions']['automations/routine/automation.json']['enabled'], False)
        self.assertIs(entry['activationAutomationStates']['automations/routine/automation.json'], True)
        plan_record = json.loads(self.codec_plans[0]['routines'][0]['wrapper']['recordJson'])
        self.assertEqual(plan_record['sessionId'], THIRD)
        self.assertEqual(authority['cloudRecords'][ROUTINE]['sessionId'], fleet.OLD)
        self.assertEqual((self.folder / 'routines' / (fleet.OLD + '.json')).read_bytes(), raw)
        self.assertEqual(R.authority_for(self.r, journal, entry, self.validator), authority)
        self.assert_protected(before)

    def test_changed_definition_and_new_recipe_materialize_paused_with_all_run_archives(self):
        new = {**self.record, 'id': 'new-recipe', 'name': 'New Official recipe', 'isEnabled': False, 'runs': []}
        self.wrappers.append({'automationId': NEW_RECIPE, 'recordJson': json.dumps(new)}); self.write_cloud()
        before = {p: p.read_bytes() for p in (self.folder / 'sources').rglob('*') if p.is_file()}
        journal, entry, authority = self.prepare()
        self.assertEqual(authority['states'], {'automations/routine/automation.json': True,
                                             'automations/new-recipe/automation.json': False})
        candidate = self.root / 'agents' / THIRD / 'automations'
        self.assertEqual(json.loads((candidate / 'routine/automation.json').read_bytes())['prompt'], 'Changed Official definition')
        self.assertFalse(json.loads((candidate / 'new-recipe/automation.json').read_bytes())['enabled'])
        runs = json.loads((candidate / 'routine/runs.json').read_bytes())
        self.assertEqual({run['id'] for run in runs}, {'prior-local-run', 'prior-official-local-run', 'cloud-run'})
        self.assertEqual(len(self.codec_plans[0]['routines'][0]['previousFiles']), 4)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(R.materialized_paths(self.r, journal, entry, self.validator), set(authority['materialized']))

    def test_prequiesced_capture_retains_true_authority_while_readback_is_false(self):
        journal, entry, authority = self.prepare(); self.evidence(journal, entry, authority)
        self.assertTrue(R.validate_prequiesced(self.r, journal, entry, authority, self.validator))
        self.assertIs(authority['states']['automations/routine/automation.json'], True)
        self.cloud_routines[fleet.OLD] = json.loads((self.folder / 'pausedReadback.json').read_bytes())['cloud']['automations']
        self.pin_authority(journal, entry, authority)
        R.validate(self.r, journal, entry, self.validator)
        self.assertIs(entry['activationAutomationStates']['automations/routine/automation.json'], True)

    def test_prequiesced_local_true_requires_its_own_exact_capture_and_paused_readback(self):
        for root in (self.target, self.folder / 'sources' / fleet.NEW):
            path = root / 'automations/routine/automation.json'; value = json.loads(path.read_bytes())
            value['enabled'] = True; self.write(path, value)
        journal, entry, authority = self.prepare(); self.evidence(journal, entry, authority)
        with self.assertRaisesRegex(RuntimeError, 'Local switches require their own capture'):
            R.validate_prequiesced(self.r, journal, entry, authority, self.validator)
        self.evidence(journal, entry, authority, local_pair=True)
        self.assertTrue(R.validate_prequiesced(self.r, journal, entry, authority, self.validator))

    def test_missing_quiescence_component_or_wrong_owner_binding_is_refused(self):
        journal, entry, authority = self.prepare(); documents = self.evidence(journal, entry, authority)
        original = copy.deepcopy(authority)
        for key in ('capture', 'pausedReadback', 'quiesceJournal'):
            changed = copy.deepcopy(original); changed['quiescenceEvidence'].pop(key)
            with self.subTest(missing=key), self.assertRaises(RuntimeError):
                R.validate_prequiesced(self.r, journal, entry, changed, self.validator)
        for change in ({'destinationId': fleet.NEW}, {'sourceRow': fleet.row(fleet.OLD, 'temporal', '999')},
                       {'originalDesiredCloud': {ROUTINE: False}}):
            changed = copy.deepcopy(original); capture = {**documents['capture'], **change}
            self.repin_evidence(changed, 'capture', capture)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                R.validate_prequiesced(self.r, journal, entry, changed, self.validator)

    def test_local_pair_wrong_sid_raw_hash_or_desired_map_is_refused(self):
        journal, entry, authority = self.prepare(); documents = self.evidence(journal, entry, authority, local_pair=True)
        for change in ('sid', 'raw', 'sha', 'definition', 'desired'):
            altered = copy.deepcopy(authority); local = copy.deepcopy(documents['localCapture'])
            if change == 'sid': local['profile']['serverId'] = '999'
            if change == 'raw': local['localRoutines'][0]['rawBase64'] = base64.b64encode(b'{}').decode()
            if change == 'sha': local['localRoutines'][0]['sha256'] = '0' * 64
            if change == 'definition':
                row = local['localRoutines'][0]; row['record']['prompt'] = 'different local authored prompt'
                raw = self.tail.encoded(row['record']); row['rawBase64'] = base64.b64encode(raw).decode()
                row['sha256'] = self.validator.digest(raw)
            if change == 'desired': local['originalDesiredLocal']['automations/routine/automation.json'] = True
            self.repin_evidence(altered, 'localCapture', local)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                R.validate_prequiesced(self.r, journal, entry, altered, self.validator)

    def test_paused_readback_definition_change_is_not_switch_only_quiescence(self):
        journal, entry, authority = self.prepare(); documents = self.evidence(journal, entry, authority)
        paused = documents['pausedReadback']; record = json.loads(paused['cloud']['automations'][0]['recordJson'])
        record['prompt'] = 'changed during pause'; paused['cloud']['automations'][0]['recordJson'] = json.dumps(record)
        self.repin_evidence(authority, 'pausedReadback', paused)
        with self.assertRaisesRegex(RuntimeError, 'changed authored state'):
            R.validate_prequiesced(self.r, journal, entry, authority, self.validator)

    def test_new_authority_supersedes_legacy_server_authority_without_overwriting_true(self):
        journal, entry, authority = self.prepare()
        entry['serverRoutineAuthority'] = {'path': str(self.backup / 'unusable-legacy.json'), 'sha256': '0' * 64}
        self.r.save(journal)
        self.assertEqual(self.r.server_routine_states(journal, entry, current=True), {})
        self.r.pause_server_routine_authority(journal, entry)
        self.assertIs(entry['activationAutomationStates']['automations/routine/automation.json'], True)
        self.assertFalse(entry.get('serverRoutinePauseIntent', False))

    def test_materialized_new_recipe_can_restore_without_old_local_config(self):
        new = {**self.record, 'id': 'new-recipe', 'runs': []}
        self.wrappers = [{'automationId': NEW_RECIPE, 'recordJson': json.dumps(new)}]; self.write_cloud()
        journal, entry, authority = self.prepare(); entry['phase'] = 'active'; self.r.save(journal)
        self.assertFalse((self.target / 'automations/new-recipe/automation.json').exists())
        result = self.r.restore_entry_routines(journal, entry)
        self.assertEqual(result['state'], 'restored')
        self.assertTrue(json.loads((self.root / 'agents' / THIRD / 'automations/new-recipe/automation.json').read_bytes())['enabled'])
        self.assertFalse(json.loads((self.root / 'agents' / THIRD / 'automations/routine/automation.json').read_bytes())['enabled'])

    def test_unrelated_missing_old_local_config_cannot_use_materialized_exception(self):
        journal, entry, authority = self.prepare(); entry['phase'] = 'active'
        relative = 'automations/unrelated/automation.json'
        self.write(self.root / 'agents' / THIRD / relative, {'enabled': False, 'prompt': 'unrelated'})
        entry['activationAutomationStates'][relative] = True
        authority['states'][relative] = True; self.pin_authority(journal, entry, authority)
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            self.r.restore_entry_routines(journal, entry)
        self.assertFalse(json.loads((self.root / 'agents' / THIRD / relative).read_bytes())['enabled'])

    def test_timezone_change_invalidates_materialized_authority(self):
        journal, entry, authority = self.prepare()
        self.write(self.root / 'settings.json', {'userTimeZone': 'America/Los_Angeles', 'unrelated': True})
        with self.assertRaisesRegex(RuntimeError, '[Tt]imezone|[Tt]ime.zone'):
            R.authority_for(self.r, journal, entry, self.validator)

    def test_nested_cron_fires_at_drift_is_derived_while_schedule_changes_refuse(self):
        nested = {'type': 'group', 'listeners': [
            {'type': 'cron', 'schedule': '0 * * * *', 'firesAt': 100},
            {'type': 'group', 'listeners': [{'type': 'cron', 'schedule': '0 9 * * *', 'firesAt': 200}]}]}
        self.record['trigger'] = nested
        self.record['triggerPresentation'] = {'version': 1, 'trigger': copy.deepcopy(nested)}
        self.wrappers = [{'automationId': ROUTINE, 'recordJson': json.dumps(self.record)}]; self.write_cloud()
        journal, entry, authority = self.prepare(); documents = self.evidence(journal, entry, authority)
        paused = documents['pausedReadback']; record = json.loads(paused['cloud']['automations'][0]['recordJson'])
        for trigger in (record['trigger'], record['triggerPresentation']['trigger']):
            trigger['listeners'][0]['firesAt'] = 999
            trigger['listeners'][1]['listeners'][0]['firesAt'] = 1000
        paused['cloud']['automations'][0]['recordJson'] = json.dumps(record)
        self.repin_evidence(authority, 'pausedReadback', paused)
        self.assertTrue(R.validate_prequiesced(self.r, journal, entry, authority, self.validator))
        record['trigger']['listeners'][1]['listeners'][0]['schedule'] = '0 10 * * *'
        paused['cloud']['automations'][0]['recordJson'] = json.dumps(record)
        self.repin_evidence(authority, 'pausedReadback', paused)
        with self.assertRaisesRegex(RuntimeError, 'changed authored state'):
            R.validate_prequiesced(self.r, journal, entry, authority, self.validator)

    def test_timezone_preserves_unrelated_settings_changes_but_requires_matching_plan(self):
        journal, entry, authority = self.prepare()
        self.write(self.root / 'settings.json', {'userTimeZone': 'America/Chicago', 'unrelated': False})
        self.assertEqual(R.authority_for(self.r, journal, entry, self.validator), authority)
        authority.pop('timeZoneSettings'); self.pin_authority(journal, entry, authority)
        with self.assertRaisesRegex(RuntimeError, 'timezone evidence is missing'):
            R.authority_for(self.r, journal, entry, self.validator)

    def test_materialized_config_tampering_is_rejected_before_restore(self):
        journal, entry, authority = self.prepare()
        path = self.root / 'agents' / THIRD / 'automations/routine/automation.json'
        value = json.loads(path.read_bytes()); value['prompt'] = 'unreviewed replacement'
        self.write(path, value)
        with self.assertRaisesRegex(RuntimeError, 'differs from proof'):
            R.authority_for(self.r, journal, entry, self.validator)
        self.assertFalse(json.loads(path.read_bytes())['enabled'])

    def test_plan_and_proof_cannot_jointly_claim_another_target(self):
        journal, entry, authority = self.prepare()
        plan_path = pathlib.Path(authority['nativePlan']['path'])
        proof_path = pathlib.Path(authority['nativeProof']['path'])
        plan = json.loads(plan_path.read_bytes()); proof = json.loads(proof_path.read_bytes())
        plan['targetId'] = NEW_RECIPE; proof['targetId'] = NEW_RECIPE
        self.tail.write(plan_path, plan)
        proof['sourcePlanFileSha256'] = self.tail.sha(plan_path.read_bytes()); self.tail.write(proof_path, proof)
        authority['nativePlan'] = self.tail.reference(plan_path); authority['nativeProof'] = self.tail.reference(proof_path)
        self.pin_authority(journal, entry, authority)
        with self.assertRaises(RuntimeError):
            R.authority_for(self.r, journal, entry, self.validator)

    def test_nonempty_cloud_authority_requires_native_plan_and_proof(self):
        journal, entry, authority = self.prepare()
        authority.update(nativeProof=None, nativePlan=None, materialized=[])
        self.pin_authority(journal, entry, authority)
        with self.assertRaises(RuntimeError):
            R.authority_for(self.r, journal, entry, self.validator)

    def test_matched_map_must_be_exact_native_source_to_config_projection(self):
        journal, entry, authority = self.prepare()
        projections = ({}, {ROUTINE: 'automations/other/automation.json'},
                       {NEW_RECIPE: 'automations/routine/automation.json'},
                       {ROUTINE: 'automations/routine/automation.json', NEW_RECIPE: 'automations/routine/automation.json'})
        for matched in projections:
            with self.subTest(matched=matched):
                authority['matched'] = matched; self.pin_authority(journal, entry, authority)
                with self.assertRaises(RuntimeError):
                    R.authority_for(self.r, journal, entry, self.validator)

    def test_unmatched_local_false_switch_cannot_be_arbitrarily_enabled(self):
        relative = 'automations/local-only/automation.json'
        local = {'enabled': False, 'prompt': 'preserve separate Local-only recipe'}
        self.write(self.target / relative, local)
        self.write(self.folder / 'sources' / fleet.NEW / relative, local)
        journal, entry, authority = self.prepare()
        self.assertIs(authority['states'][relative], False)
        authority['states'][relative] = True; entry['activationAutomationStates'][relative] = True
        self.pin_authority(journal, entry, authority)
        with self.assertRaises(RuntimeError):
            R.authority_for(self.r, journal, entry, self.validator)

    def test_active_derived_scheduler_state_and_appended_runs_preserve_later_user_pause(self):
        nested = {'type': 'group', 'listeners': [
            {'type': 'cron', 'schedule': '0 * * * *', 'firesAt': 100,
             'window': {'earlyMinutes': 0, 'lateMinutes': 2}},
            {'type': 'group', 'listeners': [{'type': 'cron', 'schedule': '0 9 * * *', 'firesAt': 200}]}]}
        self.record['trigger'] = nested
        self.record['triggerPresentation'] = {'version': 1, 'trigger': copy.deepcopy(nested)}
        self.wrappers = [{'automationId': ROUTINE, 'recordJson': json.dumps(self.record)}]; self.write_cloud()
        journal, entry, authority = self.prepare()
        entry.update(phase='active', routinesRestored=True,
                     routineRestoreProgress={'automations/routine/automation.json': True})
        self.r.save(journal)
        config_path = self.root / 'agents' / THIRD / 'automations/routine/automation.json'
        runs_path = config_path.with_name('runs.json')
        config = json.loads(config_path.read_bytes()); config['lastRunAt'] = 1234
        for trigger in (config['trigger'], config['triggerPresentation']['trigger']):
            trigger['listeners'][0]['firesAt'] = 10000
            trigger['listeners'][1]['listeners'][0]['firesAt'] = 20000
        config['enabled'] = False  # The user paused after the first restore.
        self.write(config_path, config)
        runs = json.loads(runs_path.read_bytes()); runs.insert(0, native_run('new-legitimate-run', 400))
        self.write(runs_path, runs)
        self.assertEqual(R.authority_for(self.r, journal, entry, self.validator), authority)
        self.assertEqual(self.r.restore_routines()[0]['state'], 'already-restored')
        self.assertFalse(json.loads(config_path.read_bytes())['enabled'])
        self.assertEqual(json.loads(runs_path.read_bytes()), runs)
        self.assertFalse(any(name == 'setAgentAutomationEnabled' for name, _ in self.gateway_calls))
        for change in ('prompt', 'schedule', 'window'):
            altered = copy.deepcopy(config)
            if change == 'prompt': altered['prompt'] = 'different authored prompt'
            if change == 'schedule': altered['trigger']['listeners'][1]['listeners'][0]['schedule'] = '0 10 * * *'
            if change == 'window': altered['trigger']['listeners'][0]['window']['lateMinutes'] = 5
            self.write(config_path, altered)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                R.authority_for(self.r, journal, entry, self.validator)

    def test_changed_active_history_requires_the_pinned_native_visible_projection(self):
        journal, entry, authority = self.prepare(); entry['phase'] = 'active'; self.r.save(journal)
        runs_path = self.root / 'agents' / THIRD / 'automations/routine/runs.json'
        runs = json.loads(runs_path.read_bytes()); runs.insert(0, native_run('new-current-run', 400))
        self.write(runs_path, runs)
        proof_path = pathlib.Path(authority['nativeProof']['path'])
        proof = json.loads(proof_path.read_bytes())
        proof['routines'][0].pop('nativeVisibleProjection')
        proof['routines'][0].pop('nativeVisibleProjectionSha256')
        proof['routines'][0].pop('nativeVisibleProjectionJson')
        self.tail.write(proof_path, proof); authority['nativeProof'] = self.tail.reference(proof_path)
        self.pin_authority(journal, entry, authority)
        with self.assertRaises(RuntimeError):
            R.authority_for(self.r, journal, entry, self.validator)

    def test_native_projection_json_hash_count_and_window_limit_are_exact(self):
        journal, entry, authority = self.prepare()
        proof_path = pathlib.Path(authority['nativeProof']['path'])
        original = json.loads(proof_path.read_bytes())
        for change in ('hash', 'json-array', 'count', 'limit'):
            proof = copy.deepcopy(original); row = proof['routines'][0]
            if change == 'hash': row['nativeVisibleProjectionSha256'] = '0' * 64
            if change == 'json-array':
                row['nativeVisibleProjectionJson'] = '[]'
                row['nativeVisibleProjectionSha256'] = self.tail.sha(b'[]')
            if change == 'count': row['nativeVisibleRunCount'] += 1
            if change == 'limit': row['nativeRunHistoryLimit'] = 21
            self.tail.write(proof_path, proof); authority['nativeProof'] = self.tail.reference(proof_path)
            self.pin_authority(journal, entry, authority)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                R.authority_for(self.r, journal, entry, self.validator)

    def test_preactive_extra_hidden_destination_routine_refuses_before_any_write(self):
        journal, entry, authority = self.prepare()
        extra = self.root / 'agents' / THIRD / 'automations/untracked-hidden/automation.json'
        self.write(extra, {'enabled': False, 'prompt': 'untracked hidden destination recipe'})
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        calls = copy.deepcopy(self.calls)
        with self.assertRaisesRegex(RuntimeError, 'destination routine roster must be exact and paused'):
            R.validate(self.r, journal, entry, self.validator)
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}, before)
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.gateway_calls, [])

    def test_preactive_known_local_only_destination_switch_must_remain_false(self):
        relative = 'automations/local-only/automation.json'
        local = {'enabled': False, 'prompt': 'retained separate Local recipe'}
        self.write(self.target / relative, local)
        self.write(self.folder / 'sources' / fleet.NEW / relative, local)
        journal, entry, authority = self.prepare()
        self.assertIs(authority['states'][relative], False)
        target = self.root / 'agents' / THIRD / relative
        self.write(target, {**local, 'enabled': True})
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        calls = copy.deepcopy(self.calls)
        with self.assertRaisesRegex(RuntimeError, 'destination routine roster must be exact and paused'):
            R.validate(self.r, journal, entry, self.validator)
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}, before)
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.gateway_calls, [])

    def test_codec_helper_and_output_hashes_are_bound_to_the_captured_plan(self):
        original = self.materializer
        def wrong(folder, plan):
            proof = original(folder, plan); proof['bridgeSha256'] = '0' * 64
            return proof
        self.materializer = wrong
        with self.assertRaisesRegex(RuntimeError, 'codec proof differs'):
            self.prepare()


class RuntimeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.baseline = [native_run('baseline-' + str(index), 1000 - index) for index in range(25)]
        self.projection = copy.deepcopy(self.baseline[:20])

    def test_empty_or_truncated_old_subset_is_not_a_native_recent_window(self):
        for actual in ([], self.projection[:1], self.projection[:19], self.projection[1:]):
            with self.subTest(length=len(actual)):
                self.assertFalse(R.runtime_history(actual, self.baseline, self.projection))

    def test_unknown_older_run_cannot_replace_preserved_baseline_head(self):
        actual = [*self.projection[:-1], native_run('unproven-old-run', 900)]
        self.assertFalse(R.runtime_history(actual, self.baseline, self.projection))
        self.assertFalse(R.runtime_history([native_run('unproven-old-only', 900)], self.baseline, self.projection))

    def test_exact_twenty_newest_native_window_after_one_new_run_is_allowed(self):
        actual = [native_run('new-native-run', 1001), *self.projection[:19]]
        self.assertEqual(len(actual), 20)
        self.assertTrue(R.runtime_history(actual, self.baseline, self.projection))

    def test_previous_running_run_may_complete_without_changing_its_identity(self):
        self.baseline[0] = native_run('baseline-0', 1000, status='running')
        self.projection[0] = copy.deepcopy(self.baseline[0])
        actual = copy.deepcopy(self.projection); actual[0] = native_run('baseline-0', 1000, status='ok')
        self.assertTrue(R.runtime_history(actual, self.baseline, self.projection))
        actual[0]['startedAt'] = 1001
        self.assertFalse(R.runtime_history(actual, self.baseline, self.projection))

    def test_trusted_native_legacy_variants_survive_exact_window_and_one_new_observation(self):
        projection = [native_run('same-historical-id', 1000),
                      {**native_run('same-historical-id', 999), 'detail': 'second retained record variant'}]
        projection.extend(native_run('negative-history-' + str(index), -index) for index in range(1, 19))
        # These are trusted native parser outputs, not new observations to
        # admit under the strict current run grammar.
        projection[0]['finishedAt'] = None
        projection[2]['finishedAt'] = None
        baseline = copy.deepcopy(projection)
        self.assertEqual(len(projection), 20)
        self.assertTrue(R.runtime_history(copy.deepcopy(projection), baseline, projection))
        actual = [native_run('strict-new-observation', 1001), *copy.deepcopy(projection[:19])]
        self.assertEqual(len(actual), 20)
        self.assertTrue(R.runtime_history(actual, baseline, projection))
        self.assertFalse(R.runtime_history([projection[0], *projection[2:]], baseline, projection))
        self.assertFalse(R.runtime_history(actual[:-1], baseline, projection))

    def test_changed_history_cannot_infer_a_missing_native_projection(self):
        actual = [native_run('new-native-run', 1001), *self.projection[:19]]
        self.assertFalse(R.runtime_history(actual, self.baseline, None))



if __name__ == '__main__':
    unittest.main()
