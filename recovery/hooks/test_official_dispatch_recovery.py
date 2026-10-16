import importlib.util
import json
import pathlib
import unittest
import copy
import test_remint_box_fleet as fleet

SPEC = importlib.util.spec_from_file_location('official_dispatch', pathlib.Path(__file__).with_name('official-dispatch-recovery.py'))
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


class OfficialTests(fleet.RemintTests):
    def selected_official(self):
        self.r.stage(fleet.OLD); self.r.register(fleet.OLD); self.r.activate()
        (self.root / 'openbot-mode').write_text('official')
        self.calls.clear(); self.gateway_calls.clear()
        return M.Recovery(self.root, self.r)

    def test_owned_stock_roster_keeps_sections_profiles_models_and_box_databases(self):
        recovery = self.selected_official()
        database = (self.root/'agents'/fleet.NEW/'store.db').read_bytes()
        overrides = (self.root/'openbot-bot-models.json').read_bytes()
        result = recovery.run()
        self.assertEqual(result['phase'], 'stock-roster-selected')
        self.assertFalse(result['liveHistoryMerged']); self.assertFalse(result['pruneAllowed'])
        self.assertEqual(self.server_settings['mainAgentId'], fleet.OLD)
        self.assertEqual(self.server_settings['pinnedAgents']['agentIds'], [fleet.OLD])
        self.assertEqual(self.server_settings['sidebarSections']['sections'][0]['agentIds'], [fleet.OLD, fleet.OTHER])
        self.assertEqual((self.root/'agents'/fleet.NEW/'store.db').read_bytes(), database)
        self.assertEqual((self.root/'openbot-bot-models.json').read_bytes(), overrides)
        source = json.loads((self.source/'profile.json').read_text())
        self.assertEqual(source['harness'], 'temporal')
        self.assertEqual(source['description'], self.profile['description'])
        self.assertFalse(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertTrue(json.loads((self.root/'agents'/fleet.NEW/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertFalse(any(name.startswith('Create') for name, _ in self.calls))

    def test_stock_counterpart_must_be_owned_temporal_with_matching_binding(self):
        recovery = self.selected_official()
        for change in ({'harness':'box'}, {'viewerIsOwner':False}, {'id':'999'}):
            before = dict(self.roster[0]); self.roster[0].update(change)
            self.calls.clear()
            with self.subTest(change=change), self.assertRaises(RuntimeError): recovery.run()
            self.assertFalse(any(name == 'UpdateGrokBotUserRuntimeSettings' for name, _ in self.calls))
            self.roster[0] = before

    def test_custom_selection_cancels_without_mutation(self):
        recovery = self.selected_official()
        (self.root/'openbot-mode').write_text('custom')
        with self.assertRaises(RuntimeError): recovery.run()
        self.assertEqual(self.calls, [])

    def migrated_copy(self):
        recovery = self.selected_official()
        self.roster[1]['harness'] = 'temporal'
        # Official had already selected the original before upstream migrated
        # the preserved copy. Test the actual inverse visibility handoff.
        self.server_settings['sidebarSections']['sections'][0]['agentIds'] = [fleet.OLD, fleet.OTHER]
        self.server_settings['pinnedAgents']['agentIds'] = [fleet.OLD]
        self.server_settings['mainAgentId'] = fleet.OLD
        self.write(self.source/'settings.json', {'hiddenFromSidebar': False, 'notificationsEnabled': False})
        self.write(self.root/'agents'/fleet.NEW/'settings.json', {'hiddenFromSidebar': True, 'notificationsEnabled': False})
        self.host_settings['sidebarSections'] = [{'id': 'me', 'agentIds': [fleet.OLD], 'isCollapsed': True}]
        return recovery

    def test_migrated_copy_remaps_both_ids_and_hides_the_original_without_merging_state(self):
        recovery = self.migrated_copy()
        protected = {p: p.read_bytes() for p in self.root.rglob('*')
                     if p.is_file() and (p.name in ('store.db', 'automation.json', 'openbot-bot-models.json'))}
        notifications = copy.deepcopy(self.host_settings['notifications'])
        result = recovery.run()
        self.assertEqual(result['identityMapping'], {fleet.OLD: fleet.NEW, fleet.NEW: fleet.NEW})
        self.assertFalse(result['liveHistoryMerged']); self.assertFalse(result['pruneAllowed'])
        self.assertEqual(self.server_settings['mainAgentId'], fleet.NEW)
        self.assertEqual(self.server_settings['sidebarSections']['sections'][0]['agentIds'], [fleet.NEW, fleet.OTHER])
        self.assertEqual(self.server_settings['pinnedAgents']['agentIds'], [fleet.NEW])
        self.assertTrue(self.host_settings['sidebarSections'][0]['isCollapsed'])
        self.assertEqual(self.host_settings['notifications'], notifications)
        self.assertTrue(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertFalse(json.loads((self.root/'agents'/fleet.NEW/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertEqual({p: p.read_bytes() for p in protected}, protected)
        self.assertEqual((self.root/'openbot-mode').read_text(), 'official')
        self.assertFalse(any(name.startswith('Create') for name, _ in self.calls))
        # A resumed run must not change sections, hide the selected identity,
        # or create another bot when both counterpart rows remain Temporal.
        recovery.run()
        self.assertEqual(self.server_settings['sidebarSections']['sections'][0]['agentIds'], [fleet.NEW, fleet.OTHER])

    def test_confirmed_stock_selection_survives_later_migration_of_the_copy(self):
        recovery = self.selected_official()
        first = recovery.run()
        self.assertEqual(first['identityMapping'], {fleet.OLD:fleet.OLD, fleet.NEW:fleet.OLD})
        self.roster[1]['harness'] = 'temporal'
        protected = {p:p.read_bytes() for p in self.root.rglob('*')
                     if p.is_file() and p.name in ('store.db','automation.json','openbot-bot-models.json')}
        self.calls.clear(); self.gateway_calls.clear()
        planned = recovery.plan()
        self.assertEqual(planned['identityMapping'], first['identityMapping'])
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})])
        self.assertEqual(self.gateway_calls, [])
        result = recovery.run()
        self.assertEqual(result['identityMapping'], first['identityMapping'])
        self.assertEqual(self.server_settings['mainAgentId'], fleet.OLD)
        self.assertEqual(self.server_settings['pinnedAgents']['agentIds'], [fleet.OLD])
        self.assertEqual(self.server_settings['sidebarSections']['sections'][0]['agentIds'], [fleet.OLD, fleet.OTHER])
        self.assertFalse(json.loads((self.source/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertTrue(json.loads((self.root/'agents'/fleet.NEW/'settings.json').read_text())['hiddenFromSidebar'])
        self.assertEqual({p:p.read_bytes() for p in protected}, protected)
        self.assertFalse(result['liveHistoryMerged']); self.assertFalse(result['pruneAllowed'])

    def test_legacy_sparse_confirmed_selection_remains_read_only_during_plan(self):
        recovery = self.selected_official(); recovery.run()
        state = self.root/'openbot-official-dispatch-state.json'
        value = json.loads(state.read_text())
        self.write(state, {**value, 'identityMapping':{fleet.NEW:fleet.OLD}})
        self.roster[1]['harness'] = 'temporal'
        before = {p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.calls.clear(); self.gateway_calls.clear()
        self.assertEqual(recovery.plan()['identityMapping'], {fleet.OLD:fleet.OLD, fleet.NEW:fleet.OLD})
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})]); self.assertEqual(self.gateway_calls, [])
        self.assertEqual({p:p.read_bytes() for p in before}, before)

    def test_unavailable_confirmed_stock_counterpart_uses_an_eligible_owned_peer(self):
        recovery = self.selected_official(); recovery.run()
        self.roster[0]['harness'] = 'box'; self.roster[1]['harness'] = 'temporal'
        self.assertEqual(recovery.plan()['identityMapping'], {fleet.OLD:fleet.NEW, fleet.NEW:fleet.NEW})

    def test_confirmed_counterpart_still_requires_journal_binding_and_ownership(self):
        recovery = self.selected_official(); recovery.run()
        self.roster[1]['harness'] = 'temporal'
        for change in ({'viewerIsOwner':False}, {'id':'999'}, {'legacyAgentId':fleet.OTHER}):
            before = dict(self.roster[0]); self.roster[0].update(change); self.calls.clear()
            with self.subTest(change=change), self.assertRaises(RuntimeError): recovery.run()
            self.assertEqual(self.calls, [('ListGrokBotAgents', {})])
            self.roster[0] = before

    def test_another_generation_or_incomplete_receipt_does_not_pin_a_counterpart(self):
        recovery = self.migrated_copy()
        for state in [
            {'phase':'stock-roster-selected','mode':'official','identityMapping':{fleet.OTHER:fleet.OLD}},
            {'phase':'stock-roster-prepared','mode':'official','identityMapping':{fleet.NEW:fleet.OLD}},
            {'phase':'stock-roster-selected','mode':'custom','identityMapping':{fleet.NEW:fleet.OLD}},
        ]:
            self.write(self.root/'openbot-official-dispatch-state.json', state)
            with self.subTest(state=state):
                self.assertEqual(recovery.plan()['identityMapping'], {fleet.OLD:fleet.NEW, fleet.NEW:fleet.NEW})

    def test_malformed_confirmed_mapping_refuses_before_settings_writes(self):
        recovery = self.migrated_copy()
        for mapping in [[], {fleet.NEW:'not-an-identity'}, {fleet.NEW:fleet.OLD, fleet.OLD:fleet.NEW}]:
            self.write(self.root/'openbot-official-dispatch-state.json',
                       {'phase':'stock-roster-selected','mode':'official','identityMapping':mapping})
            self.calls.clear(); self.gateway_calls.clear()
            with self.subTest(mapping=mapping), self.assertRaises((RuntimeError, ValueError)): recovery.run()
            self.assertEqual(self.calls, [('ListGrokBotAgents', {})]); self.assertEqual(self.gateway_calls, [])

    def test_symlinked_confirmed_selection_is_not_trusted(self):
        recovery = self.migrated_copy()
        target = self.root/'other-receipt.json'
        self.write(target, {'phase':'stock-roster-selected','mode':'official',
                            'identityMapping':{fleet.NEW:fleet.OLD}})
        (self.root/'openbot-official-dispatch-state.json').symlink_to(target)
        self.calls.clear(); self.gateway_calls.clear()
        with self.assertRaises(RuntimeError): recovery.run()
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})]); self.assertEqual(self.gateway_calls, [])

    def test_plan_reads_authority_without_mutating_either_service_or_local_files(self):
        recovery = self.migrated_copy()
        files = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        settings = copy.deepcopy(self.server_settings)
        result = recovery.plan()
        self.assertEqual(result['phase'], 'planned')
        self.assertEqual(result['identityMapping'][fleet.OLD], fleet.NEW)
        self.assertFalse(result['liveHistoryMerged']); self.assertFalse(result['pruneAllowed'])
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})])
        self.assertEqual(self.gateway_calls, [])
        self.assertEqual(self.server_settings, settings)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, files)

    def test_plain_migrated_agent_cannot_replace_a_typed_room(self):
        recovery = self.migrated_copy()
        journal = self.r.read()
        journal['entries'][0]['kind'] = 'GROK_BOT_AGENT_KIND_ROOM'
        self.roster[0]['kind'] = 'GROK_BOT_AGENT_KIND_ROOM'
        self.write(self.root/'openbot-box-remint.json', journal)
        result = recovery.plan()
        self.assertEqual(result['identityMapping'], {fleet.OLD: fleet.OLD, fleet.NEW: fleet.OLD})

    def test_unselected_counterpart_ownership_and_binding_are_checked_before_writes(self):
        recovery = self.migrated_copy()
        for change in ({'viewerIsOwner': False}, {'legacyAgentId': fleet.OTHER}, {'id': '999'}):
            before = dict(self.roster[0]); self.roster[0].update(change); self.calls.clear()
            with self.subTest(change=change), self.assertRaises(RuntimeError): recovery.run()
            self.assertEqual(self.calls, [('ListGrokBotAgents', {})])
            self.roster[0] = before

    def test_journal_binding_is_authoritative_even_if_profile_and_roster_agree(self):
        recovery = self.migrated_copy()
        self.roster[1]['id'] = '999'
        path = self.root/'agents'/fleet.NEW/'profile.json'
        self.write(path, {**json.loads(path.read_text()), 'serverId': '999'})
        with self.assertRaisesRegex(RuntimeError, 'binding changed'): recovery.run()
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})])

    def test_duplicate_roster_rows_or_overlapping_pairs_refuse_before_writes(self):
        recovery = self.migrated_copy()
        self.roster.append(copy.deepcopy(self.roster[0]))
        with self.assertRaisesRegex(RuntimeError, 'duplicate identities'): recovery.run()
        self.roster.pop(); self.calls.clear()
        journal = self.r.read(); journal['entries'].append(copy.deepcopy(journal['entries'][0]))
        self.write(self.root/'openbot-box-remint.json', journal)
        with self.assertRaisesRegex(RuntimeError, 'not one-to-one'): recovery.run()
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(RuntimeError, 'pairs overlap'): recovery.targets(journal, self.roster)

    def test_original_counterpart_kind_change_is_also_refused(self):
        recovery = self.selected_official()
        self.roster[0]['kind'] = 'GROK_BOT_AGENT_KIND_ROOM'
        with self.assertRaisesRegex(RuntimeError, 'kind changed'): recovery.run()
        self.assertEqual(self.calls, [('ListGrokBotAgents', {})])

    def test_mode_change_during_rpc_stops_following_writes(self):
        recovery = self.selected_official(); original = self.r.m.rpc
        def switching(method, body):
            result = original(method, body)
            if method == 'GetGrokBotUserRuntimeSettings': (self.root/'openbot-mode').write_text('custom')
            return result
        self.r.m.rpc = switching
        with self.assertRaises(RuntimeError): recovery.run()
        self.assertFalse(any(name == 'UpdateGrokBotUserRuntimeSettings' for name, _ in self.calls))


if __name__ == '__main__': unittest.main()
