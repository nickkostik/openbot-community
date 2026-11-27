#!/usr/bin/env python3
"""Compare the native bot settings endpoint with a running OpenBot."""

import argparse
import hashlib
import json
from pathlib import Path
import selectors
import subprocess
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


def read_settings(base):
    address = urlsplit(base)
    if (
        address.scheme != "http"
        or address.hostname != "127.0.0.1"
        or not address.port
        or address.username
        or address.path not in ("", "/")
        or address.query
        or address.fragment
    ):
        raise ValueError("Use the numeric loopback URL for the service")
    opener = build_opener(ProxyHandler({}))
    with opener.open(base.rstrip("/") + "/api/bot-models", timeout=8) as response:
        return json.load(response)


def fingerprint(root):
    values = {}
    for name in ("openbot-plan.json", "openbot-bot-models.json", "openbot-mode"):
        try:
            values[name] = hashlib.sha256((root / name).read_bytes()).digest()
        except FileNotFoundError:
            values[name] = None
    return values


def compare(binary, state, previous):
    before = fingerprint(state)
    with subprocess.Popen(
        [str(binary.resolve(strict=True)), "--sand-data", str(state)],
        stdout=subprocess.PIPE,
        text=True,
    ) as candidate:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(candidate.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=8):
                    raise RuntimeError("Rust comparison listener did not start")
            line = candidate.stdout.readline().strip()
            prefix = "OpenBot Rust listening on "
            if not line.startswith(prefix):
                raise RuntimeError("Rust comparison listener did not report an address")
            baseline = read_settings(previous)
            current = read_settings(line[len(prefix):])
            if before != fingerprint(state):
                raise RuntimeError("Saved state changed during the comparison")
            for field in ("assignments", "reasoning", "available"):
                if field not in baseline or baseline[field] != current.get(field):
                    raise RuntimeError(f"Different {field} between Rust and the running OpenBot")
            return {
                "match": True,
                "available": len(current["available"]),
                "assignments": len(current["assignments"]),
                "botsWithReasoning": len(current["reasoning"]),
                "stateFilesUnchanged": True,
            }
        finally:
            candidate.terminate()
            try:
                candidate.wait(timeout=8)
            except subprocess.TimeoutExpired:
                candidate.kill()
                candidate.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--sand-data", required=True, type=Path)
    parser.add_argument("--baseline", default="http://127.0.0.1:9280")
    args = parser.parse_args()
    print(json.dumps(compare(args.binary, args.sand_data, args.baseline), sort_keys=True))
