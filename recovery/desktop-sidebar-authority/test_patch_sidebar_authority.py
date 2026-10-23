from __future__ import annotations

import importlib.util
import hashlib
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "patch-sidebar-authority.py"


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        "grokbot_sidebar_authority_patch", MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load patcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PatchTests(unittest.TestCase):
    def fixture(self, variant="stock"):
        patcher = load_patcher()
        roster = {"stock": patcher.ROSTER_STOCK, "v1": patcher.ROSTER_V1, "fixed": patcher.ROSTER_FIXED}[variant]
        return b"\n".join([roster, patcher.TIME_STOCK, patcher.ROW_TIME_STOCK, *[pair[1 if variant == "v1" else 0] for pair in patcher.LEGACY_PATCHES]])

    def test_stock_and_first_recovery_upgrade_to_same_fixed_point(self):
        patcher = load_patcher()
        outputs = []
        for variant in ("stock", "v1", "fixed"):
            original = self.fixture(variant)
            fixed = patcher.transform(original)
            self.assertEqual(len(original), len(fixed))
            self.assertEqual(patcher.transform(fixed), fixed)
            self.assertEqual(patcher.patch_state(fixed)["roster"], "fixed")
            self.assertTrue(all(v == "stock" for k, v in patcher.patch_state(fixed).items() if k.startswith("visibility")))
            outputs.append(fixed)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])

    def test_unknown_or_duplicate_bundle_is_rejected(self):
        patcher = load_patcher()
        for data in (b"unknown bundle", self.fixture() * 2):
            with self.assertRaises(ValueError):
                patcher.transform(data)

    def test_corrupt_timestamp_cannot_reach_date_formatter(self):
        patcher = load_patcher()
        script = "const Es=e=>typeof e==='number'&&Number.isFinite(e);" + patcher.TIME_FIXED.decode().split('function _dt(')[0]
        script += "const values=[0,1792616646840.5,864e13,-864e13,179261664684048608,Infinity,NaN,'123',null,undefined];"
        script += "process.stdout.write(JSON.stringify(values.map(timestampMs=>{const t=QK({timestampMs});if(t!==null)new Intl.DateTimeFormat('en').format(new Date(t));return t})));"
        result = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script], text=True))
        self.assertEqual(result[:4], [0, 1792616646840.5, 864e13, -864e13])
        self.assertEqual(result[4:], [None] * 6)

    def test_date_bucket_and_message_row_guards_upgrade_together(self):
        patcher = load_patcher()
        original = self.fixture().replace(patcher.TIME_STOCK, patcher.TIME_V2)
        fixed = patcher.transform(original)
        self.assertEqual(len(fixed), len(original))
        self.assertIn(patcher.ROW_TIME_FIXED, fixed)
        self.assertEqual(patcher.patch_state(fixed)['timestamp'], 'fixed')
        inconsistent = original.replace(patcher.ROW_TIME_STOCK, patcher.ROW_TIME_FIXED)
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            patcher.transform(inconsistent)

    def test_message_row_omits_only_an_unrenderable_date(self):
        patcher = load_patcher()
        script = "const Es=e=>typeof e==='number'&&Number.isFinite(e);" + patcher.TIME_FIXED.decode().split('function _dt(')[0]
        script += "const Y={jsx:(tag,props)=>({tag,...props})};function row(r){let t=[],i='time',c;"
        script += patcher.ROW_TIME_FIXED.decode() + "{dateTime:new Date(r).toISOString(),label:new Intl.DateTimeFormat('en').format(r)}),t[5]=i,t[6]=r,t[7]=c):c=t[7];return c}"
        script += "process.stdout.write(JSON.stringify([row(179261664684048608),row(1792616646840)]));"
        result = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script], text=True))
        self.assertIsNone(result[0])
        self.assertEqual(result[1]['dateTime'], '2026-10-21T21:04:06.840Z')

    def merge(self, cached, authoritative, pending=None, protected=None):
        patcher = load_patcher()
        script = "const M7=({row})=>row,nxn=(r,v)=>({...r,...v}),j7=()=>({}),Aj=()=>0;\n"
        script += patcher.ROSTER_FIXED.decode()
        script += "\nprocess.stdout.write(JSON.stringify(cxn(" + ",".join([
            json.dumps(cached), json.dumps(authoritative),
            "new Map(" + json.dumps(pending or []) + ")",
            "new Set(" + json.dumps(protected or []) + ")",
        ]) + ")));"
        return json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script], text=True))

    def test_authority_replaces_stale_path_and_visibility_but_keeps_preview(self):
        cached = [{"id": "canonical", "name": "OLD", "harness": "box", "path": "/retired/store.db", "isHiddenFromSidebar": True, "isRemovedFromSidebar": True, "updatedAt": 20, "lastMessagePreview": "real history"}, {"id": "probe", "harness": "box"}]
        incoming = [{"id": "canonical", "name": "Example Assistant", "harness": "temporal", "path": "/agents/canonical", "isHiddenFromSidebar": False, "updatedAt": 10}]
        result = self.merge(cached, incoming)
        self.assertEqual([r["id"] for r in result], ["canonical"])
        self.assertEqual(result[0]["name"], "Example Assistant")
        self.assertEqual(result[0]["path"], "/agents/canonical")
        self.assertFalse(result[0]["isHiddenFromSidebar"])
        self.assertNotIn("isRemovedFromSidebar", result[0])
        self.assertEqual(result[0]["lastMessagePreview"], "real history")
        self.assertEqual(result[0]["updatedAt"], 20)

    def test_explicit_server_hide_and_same_name_rooms_are_preserved(self):
        rows = [{"id": "one", "name": "Coding Swarm", "harness": "temporal", "isGroup": True, "isHiddenFromSidebar": True, "updatedAt": 1}, {"id": "two", "name": "Coding Swarm", "harness": "temporal", "isGroup": True, "isHiddenFromSidebar": False, "updatedAt": 2}]
        result = self.merge(rows, rows)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["isHiddenFromSidebar"])
        self.assertFalse(result[1]["isHiddenFromSidebar"])

    def test_incomplete_roster_never_prunes_and_pending_creations_survive(self):
        cached = [{"id": "old", "harness": "box"}, {"id": "pending"}, {"id": "protected"}]
        self.assertEqual(self.merge(cached, None), cached)
        self.assertEqual([r["id"] for r in self.merge(cached, [], [["pending", {}]], ["protected"])], ["pending", "protected"])

    def archive(self, content):
        digest = hashlib.sha256(content).hexdigest()
        entry = {"size": len(content), "offset": "0", "integrity": {"algorithm": "SHA256", "hash": digest, "blockSize": 4194304, "blocks": [digest]}}
        header = json.dumps({"files": {"app.js": entry}}, separators=(",", ":")).encode()
        padding = b"\0" * (-(4 + len(header)) % 4)
        payload = struct.pack("<II", 4 + len(header) + len(padding), len(header)) + header + padding
        return struct.pack("<II", 4, len(payload)) + payload + content

    def test_archive_integrity_and_atomic_idempotent_install(self):
        patcher = load_patcher()
        with tempfile.TemporaryDirectory() as folder:
            patcher.ASAR = Path(folder) / "app.asar"
            patcher.BACKUP_ROOT = Path(folder) / "backups"
            original = self.archive(self.fixture())
            patcher.ASAR.write_bytes(original)
            patcher.main()
            installed = patcher.ASAR.read_bytes()
            self.assertEqual(len(installed), len(original))
            _, size, _, json_size = struct.unpack_from("<IIII", installed)
            header = json.loads(installed[16:16 + json_size])
            self.assertEqual(header["files"]["app.js"]["integrity"]["hash"], hashlib.sha256(installed[8 + size:]).hexdigest())
            patcher.main()
            self.assertEqual(patcher.ASAR.read_bytes(), installed)
            backups = list(patcher.BACKUP_ROOT.iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)


class CacheRecoveryTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location("cache_recovery", HERE / "repair-client-cache.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cache_repair_preserves_history_and_separate_same_name_rooms(self):
        module = self.module()
        cached = [{"id": "one", "title": "SIDEBAR_TEST", "lastMessageId": "history", "isHiddenFromSidebar": True}, {"id": "two", "memberIds": ["one"]}, {"id": "retired"}]
        agents = [{"agentId": name, "name": "Coding Swarm", "kind": "GROK_BOT_AGENT_KIND_ROOM"} for name in ("one", "two")]
        repaired = module.reconcile_rows(cached, agents, True)
        self.assertEqual([r["id"] for r in repaired], ["one", "two"])
        self.assertEqual(repaired[0]["lastMessageId"], "history")
        self.assertNotIn("title", repaired[0])
        self.assertFalse(repaired[0]["isHiddenFromSidebar"])
        self.assertEqual(repaired[1]["memberIds"], ["one"])
        self.assertTrue(cached[0]["isHiddenFromSidebar"])
        with self.assertRaises(ValueError):
            module.reconcile_rows([], agents, True)
        imported = module.reconcile_rows(cached[:1], agents, False, cached[1:2])
        self.assertEqual(imported[1]["memberIds"], ["one"])
        self.assertTrue(imported[0]["isHiddenFromSidebar"])

    def test_electron_rewritten_process_titles_are_detected_before_cache_writes(self):
        module = self.module()
        executable = Path("/opt/Grok Bot/grok-bot")
        self.assertTrue(module.is_desktop_main(executable, b"/opt/Grok Bot/grok-bot\0--ozone-platform=wayland\0"))
        self.assertTrue(module.is_desktop_main(executable, b"/opt/Grok Bot/grok-bot --ozone-platform=wayland\0"))
        self.assertFalse(module.is_desktop_main(executable, b"/opt/Grok Bot/grok-bot --type=renderer\0"))

    def test_incomplete_or_mixed_harness_authority_is_rejected(self):
        module = self.module()
        agent_id = "00000000-0000-4000-8000-000000000001"
        roster = {"agents": [{"agentId": agent_id, "harness": "temporal"}]}
        settings = {"settings": {"mainAgentId": agent_id, "sidebarSections": {"sections": [{"agentIds": [agent_id]}]}}}
        module.canonical_state(roster, settings)
        settings["settings"]["sidebarSections"]["sections"][0]["agentIds"] = []
        with self.assertRaises(ValueError):
            module.canonical_state(roster, settings)
        roster["agents"][0]["harness"] = "box"
        with self.assertRaises(ValueError):
            module.canonical_state(roster, settings)


if __name__ == "__main__":
    unittest.main()
