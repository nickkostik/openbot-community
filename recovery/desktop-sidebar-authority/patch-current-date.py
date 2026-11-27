#!/usr/bin/env python3
"""Repair message dates and sidebar chronology in the Linux desktop renderer."""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import tempfile
from datetime import datetime, timezone

ASAR = pathlib.Path(os.environ.get('GROKBOT_ASAR', '/opt/Grok Bot/resources/app.asar'))
BACKUPS = pathlib.Path(os.environ.get('GROKBOT_ASAR_PATCH_BACKUPS', str(pathlib.Path.home() / '.local/share/openbot/desktop-sidebar-backups')))
IDENT = rb'[$A-Za-z_][$A-Za-z_0-9]*'
STOCK = re.compile(rb'function (?P<helper>'+IDENT+rb')\(e\)\{return (?P<predicate>'+IDENT+rb')\(e\)\?e:null\}function (?P<reader>'+IDENT+rb')\(e\)\{return (?P=helper)\(e\.timestampMs\)\}function (?P<next>'+IDENT+rb')\(')
FIXED = re.compile(rb'var (?P<helper>'+IDENT+rb')=e=>(?P<predicate>'+IDENT+rb')\(e\)&&Math\.abs\(e\)<=864e13\?e:null,(?P<reader>'+IDENT+rb')=e=>(?P=helper)\(e\.timestampMs\); *function (?P<next>'+IDENT+rb')\(')
TIMESTAMP_READER = re.compile(rb'function '+IDENT+rb'\(e\)\{return '+IDENT+rb'\(e\.timestampMs\)\}')
SIDEBAR_SCORE = re.compile(
    rb'function (?P<score>' + IDENT + rb')\(e,t=`row-updated`\)\{'
    rb'return t===`activity`\?Math\.max\(e\.localActivityAt\?\?0,e\.lastActivityAt\?\?0,e\.createdAt\):'
    rb'(?P<shared>' + IDENT + rb')\(e\)\?Math\.max\(e\.lastActivityAt\?\?0,e\.createdAt\):'
    rb'Math\.max\(e\.updatedAt,e\.lastActivityAt\?\?0\)\}'
    rb'(?=function ' + IDENT + rb'\(e,t,n=`row-updated`\)\{return (?P=score)\(t,n\)-(?P=score)\(e,n\)\})'
)


def sort_temporal_sidebar(asset):
    matches = list(SIDEBAR_SCORE.finditer(asset))
    if len(matches) != 1:
        return asset
    match = matches[0]
    shared = match['shared']
    shared_helper = b'function ' + shared + b'(e){return e.harness===`temporal`&&e.viewerIsOwner===!1&&!e.isGroup}'
    if shared in (b'n', b'r') or asset.count(shared_helper) != 1:
        return asset

    # Restoring a profile can make both updatedAt and createdAt newer than its
    # imported messages. Use the recorded activity date first for owned chats.
    # Keep the stock score for shared chats and other harnesses. The short
    # JavaScript bindings keep the archived asset offsets unchanged.
    replacement = (
        b'function ' + match['score'] + b'(e,t){let n=e.lastActivityAt??0,r=e.createdAt;'
        b'return e.harness===`temporal`&&e.viewerIsOwner?n>0&&n<=864e13?n:r:'
        b'Math.max(n,t===`activity`?Math.max(e.localActivityAt??0,r):' + shared + b'(e)?r:e.updatedAt)}'
    )
    if len(replacement) > len(match[0]):
        return asset
    return asset[:match.start()] + replacement.ljust(len(match[0]), b' ') + asset[match.end():]


def guard_renderer_dates(asset):
    stock = list(STOCK.finditer(asset))
    fixed = list(FIXED.finditer(asset))
    readers = stock or fixed
    # Current Linux builds share one timeline reader. Earlier builds shipped
    # two. Refuse a mixed layout or a native reader we cannot account for.
    if (len(readers) not in (1, 2) or (stock and fixed) or
            len(TIMESTAMP_READER.findall(asset)) != len(stock) or
            b'timeOfDay.format(r)' not in asset or b'new Date(t)' not in asset):
        raise ValueError('Unknown or partially modified renderer; original preserved')
    for match in readers:
        predicate, reader = (match.group(k) for k in ('predicate', 'reader'))
        predicate_anchor = b'function '+predicate+b'(e){return typeof e==`number`&&Number.isFinite(e)&&e>0}'
        if asset.count(predicate_anchor) != 1 or asset.count(reader+b'(t)') < 1:
            raise ValueError('Timestamp reader contract changed; original preserved')

    changed = asset
    for match in stock:
        helper, predicate, reader, nxt = (match.group(k) for k in ('helper', 'predicate', 'reader', 'next'))
        tail = b'function '+nxt+b'('
        replacement = b'var '+helper+b'=e=>'+predicate+b'(e)&&Math.abs(e)<=864e13?e:null,'+reader+b'=e=>'+helper+b'(e.timestampMs);'
        if len(replacement)+len(tail) > len(match.group()):
            raise ValueError('Timestamp guard does not fit renderer; original preserved')
        replacement = replacement.ljust(len(match.group())-len(tail), b' ')+tail
        changed = changed.replace(match.group(), replacement, 1)
    return changed


def main():
    before = ASAR.read_bytes()
    _, header_size, _, json_size = struct.unpack_from('<IIII', before)
    header = json.loads(before[16:16+json_size]); assets = header
    for part in ('dist','renderer','assets'):
        assets = assets['files'][part]
    candidates = [(name,entry) for name,entry in assets['files'].items() if re.fullmatch(r'index\.eager-app-[A-Za-z0-9_-]+\.js',name)]
    if len(candidates) != 1:
        raise ValueError('Unknown or ambiguous renderer asset; original preserved')
    asset_name,entry = candidates[0]
    start = 8 + header_size + int(entry['offset']); end = start + entry['size']
    if start < 8 + header_size or end > len(before):
        raise ValueError('Renderer asset lies outside archive')
    asset = before[start:end]
    integrity = entry.get('integrity',{})
    block_size = integrity.get('blockSize',0)
    if integrity.get('algorithm') != 'SHA256' or block_size <= 0 or integrity.get('hash') != hashlib.sha256(asset).hexdigest() or integrity.get('blocks') != [hashlib.sha256(asset[i:i+block_size]).hexdigest() for i in range(0,len(asset),block_size)]:
        raise ValueError('Renderer integrity does not match archive; original preserved')
    dated = guard_renderer_dates(asset)
    changed = sort_temporal_sidebar(dated)
    if changed == asset:
        print('Desktop archive already current'); return
    if len(changed) != len(asset):
        raise ValueError('Renderer asset length changed')
    node = os.environ.get('GROKBOT_NODE_BIN') or shutil.which('node')
    if not node:
        raise ValueError('Node is required for renderer syntax validation; original preserved')
    subprocess.run([node, '--check', '--input-type=module'], input=changed, check=True, capture_output=True, timeout=30)
    spec = importlib.util.spec_from_file_location('authority', pathlib.Path(__file__).with_name('patch-sidebar-authority.py'))
    authority = importlib.util.module_from_spec(spec); spec.loader.exec_module(authority)
    result = authority.refresh_integrity(before, before[:start] + changed + before[end:])
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    saved = BACKUPS / ('app.asar.pre-current-date-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    shutil.copy2(ASAR, saved)
    if hashlib.sha256(saved.read_bytes()).digest() != hashlib.sha256(before).digest():
        raise ValueError('Archive backup did not verify')
    fd, name = tempfile.mkstemp(prefix='.grokbot-date-', suffix='.asar', dir=ASAR.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(result); f.flush(); os.fsync(f.fileno()); os.fchmod(f.fileno(), ASAR.stat().st_mode & 0o777)
            if os.geteuid() == 0:
                os.fchown(f.fileno(), ASAR.stat().st_uid, ASAR.stat().st_gid)
        if ASAR.read_bytes() != before:
            raise ValueError('Archive changed during preparation; original preserved')
        os.replace(name, ASAR)
    finally:
        pathlib.Path(name).unlink(missing_ok=True)
    print(json.dumps({'asset': asset_name, 'backup': str(saved),
                      'sha256': hashlib.sha256(result).hexdigest(),
                      'sidebarOrderAdjusted': changed != dated}))

if __name__ == '__main__':
    main()
