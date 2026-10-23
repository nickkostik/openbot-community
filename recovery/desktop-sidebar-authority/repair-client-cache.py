#!/usr/bin/env python3
"""Reconcile a stopped Linux client's cache with a reviewed Temporal roster.

Dry-run by default. Never delete transcripts, profiles, routines, or server rows.
Require complete section coverage and existing cached history for every UUID.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def canonical_state(roster: dict, runtime: dict):
    agents = roster["agents"]
    ids = [agent["agentId"] for agent in agents]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("empty or duplicate authoritative IDs")
    for agent in agents:
        uuid.UUID(agent["agentId"])
        if agent.get("harness") != "temporal":
            raise ValueError("this recovery requires a complete Temporal roster")
    settings = runtime["settings"]
    sections = settings["sidebarSections"]["sections"]
    assigned = [agent_id for section in sections for agent_id in section.get("agentIds", [])]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(ids):
        raise ValueError("section membership must cover the authoritative IDs exactly once")
    if not set(settings.get("pinnedAgents", {}).get("agentIds", [])).issubset(ids):
        raise ValueError("pins reference unknown agents")
    if settings.get("mainAgentId") not in ids:
        raise ValueError("main agent is not in the authoritative roster")
    return agents, sections, settings["mainAgentId"]


def reconcile_rows(cached: list, agents: list, restore_visible: bool, fallback: list | None = None):
    by_id = {row["id"]: row for row in cached}
    if len(by_id) != len(cached):
        raise ValueError("duplicate UUIDs in the client cache")
    for row in fallback or []:
        by_id.setdefault(row["id"], row)
    if any(agent["agentId"] not in by_id for agent in agents):
        raise ValueError("canonical history metadata missing from cache; refusing to invent it")
    rows = []
    for agent in agents:
        agent_id = agent["agentId"]
        row = copy.deepcopy(by_id[agent_id])
        row.update(id=agent_id, name=agent["name"], description=agent.get("description", ""),
                   harness="temporal", path=f"/agents/{agent_id}",
                   isGroup=agent.get("kind") == "GROK_BOT_AGENT_KIND_ROOM")
        # Generated test titles are not authoritative names. Keep actual previews.
        row.pop("title", None)
        for field in ("avatarShape", "avatarColor", "voiceId", "voiceSpeed", "voiceLanguage"):
            if field in agent:
                row[field] = agent[field]
        if restore_visible:
            row.update(isHiddenFromSidebar=False, isRemovedFromSidebar=False)
        rows.append(row)
    return rows


def decode_key(path: Path):
    return base64.b32decode(path.stem.upper() + "=" * (-len(path.stem) % 8)).decode()


def atomic_write(path: Path, content: bytes):
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def is_desktop_main(executable: Path, command: bytes):
    return executable.name == "grok-bot" and bool(command) and b"--type=" not in command


def require_stopped():
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            command = (entry / "cmdline").read_bytes()
            executable = (entry / "exe").readlink()
        except OSError:
            continue
        # Electron can rewrite all argv into one NUL-terminated process title.
        if is_desktop_main(executable, command):
            raise ValueError("quit Grok Bot normally before applying the cache recovery")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--persistence", type=Path, default=Path.home() / ".config/Grok Bot/sand-client-persistence")
    parser.add_argument("--backups", type=Path, default=Path.home() / ".local/share/openbot/desktop-cache-backups")
    parser.add_argument("--restore-visible", action="store_true", help="clear stale hidden flags for the reviewed fleet")
    parser.add_argument("--fallback-cache", type=Path, help="verified same-account cache from another client, matched only by UUID")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    agents, sections, main_id = canonical_state(json.loads(args.roster.read_text()), json.loads(args.settings.read_text()))
    files = {}
    for path in args.persistence.glob("*.blob"):
        try:
            key = decode_key(path)
        except (ValueError, UnicodeError):
            continue
        for suffix in ("roster.last-roster", "sidebar.last-sections", "selection.last-agent"):
            if key.endswith("." + suffix):
                if suffix in files:
                    raise ValueError("multiple accounts found; choose an isolated persistence directory")
                files[suffix] = (key, path, path.read_bytes())
    if len(files) != 3 or len({key.rsplit(".", 2)[0] for key, _, _ in files.values()}) != 1:
        raise ValueError("expected matching roster, sections and selection for one account")
    docs = {suffix: json.loads(content) for suffix, (_, _, content) in files.items()}
    cached = docs["roster.last-roster"]["value"]["rows"]
    fallback = json.loads(args.fallback_cache.read_text())["value"]["rows"] if args.fallback_cache else None
    rows = reconcile_rows(cached, agents, args.restore_visible, fallback)
    docs["roster.last-roster"]["value"]["rows"] = rows
    docs["sidebar.last-sections"]["value"]["sections"] = sections
    selection = docs["selection.last-agent"]["value"]
    changed_selection = selection.get("agentId") not in {row["id"] for row in rows}
    if changed_selection:
        selection["agentId"] = main_id
    cached_ids, canonical_ids = {r["id"] for r in cached}, {r["id"] for r in rows}
    report = {"apply": args.apply, "before": len(cached), "after": len(rows),
              "retiredCacheRows": len(cached_ids - canonical_ids), "importedByUuid": len(canonical_ids - cached_ids), "restoredVisibility": args.restore_visible,
              "selectionRepaired": changed_selection, "sections": {s["name"]: len(s.get("agentIds", [])) for s in sections}}
    if args.apply:
        require_stopped()
        backup = args.backups / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup.mkdir(parents=True, mode=0o700)
        for _, path, original in files.values():
            if path.read_bytes() != original:
                raise ValueError("client cache changed during preparation")
            shutil.copy2(path, backup / path.name)
        try:
            for suffix, (_, path, _) in files.items():
                atomic_write(path, json.dumps(docs[suffix], separators=(",", ":"), ensure_ascii=False).encode())
        except Exception:
            for _, path, original in files.values():
                atomic_write(path, original)
            raise
        report["backup"] = str(backup)
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f"cache recovery: {error}", file=sys.stderr)
        raise SystemExit(2)
