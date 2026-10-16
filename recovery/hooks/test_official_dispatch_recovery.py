import importlib.util
import json
import pathlib
import unittest
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
