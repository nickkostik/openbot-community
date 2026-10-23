#!/usr/bin/env python3
"""Repair the reviewed Linux roster merge and invalid date rendering.

Accept stock or the earlier five-patch recovery. Keep ASAR offsets unchanged,
refresh affected integrity digests, back up, and install atomically. Unknown
bundles fail before any write. Profiles and routines are never modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

ASAR = Path(os.environ.get("GROKBOT_ASAR", "/opt/Grok Bot/resources/app.asar"))
BACKUP_ROOT = Path(os.environ.get("GROKBOT_ASAR_PATCH_BACKUPS", str(Path.home() / ".local/share/openbot/desktop-sidebar-backups")))
PRUNE_STOCK = b"o.harness===`temporal`&&!a.has(o.id)&&!n.has(o.id)"
PRUNE_V1 = b"/*!o.harness-box-prune*/!a.has(o.id)&&!n.has(o.id)"
ROSTER_STOCK = (
    b"function cxn(e,t,n=new Map,r=new Set){if(t===null&&n.size===0)return e;let i=new Map(e.map(e=>[e.id,e])),a=new Set(t?.map(e=>e.id));for(let o of e)t!==null&&"
    + PRUNE_STOCK
    + b"&&!r.has(o.id)&&i.delete(o.id);for(let e of t??[]){if(e.harness!==`temporal`)continue;let t=i.get(e.id);if(t===void 0){i.set(e.id,M7({row:e,fallback:n.get(e.id)}));continue}let{suggestions:r,...a}=t;i.set(e.id,{...nxn(M7({row:a,fallback:e}),{...j7(t),...j7(e)}),id:e.id,name:e.name,description:e.description,...e.title===void 0?{}:{title:e.title},...e.avatarShape===void 0?{}:{avatarShape:e.avatarShape},...e.avatarColor===void 0?{}:{avatarColor:e.avatarColor},avatarVersion:e.avatarVersion??null,avatarPhoto:e.avatarPhoto??null,avatarDataUrl:e.avatarDataUrl??(e.avatarVersion===t.avatarVersion?t.avatarDataUrl:null),createdAt:e.createdAt,updatedAt:Math.max(t.updatedAt,e.updatedAt),harness:e.harness,isGroup:e.isGroup,memberIds:e.memberIds,...e.suggestions===void 0?{}:{suggestions:e.suggestions}})}for(let[e,t]of n){let n=i.get(e);if(n===void 0)continue;let r=M7({row:n,fallback:t});i.set(e,r.harness===void 0&&t.harness===`temporal`?{...r,harness:t.harness}:r)}return[...i.values()].sort(Aj)}"
)
ROSTER_V1 = ROSTER_STOCK.replace(PRUNE_STOCK, PRUNE_V1)
ROSTER_FIXED = (
    b'function cxn(e,t,n=new Map,r=new Set){if(t===null&&n.size===0)return e;let i=new Map(e.map(e=>[e.id,e])),a=new Set(t?.map(e=>e.id));for(let o of e)t!==null&&/*!o.harness-box-prune*/!a.has(o.id)&&!n.has(o.id)&&!r.has(o.id)&&i.delete(o.id);for(let e of t??[]){if(e.harness!==`temporal`)continue;let t=i.get(e.id);if(!t){i.set(e.id,M7({row:e,fallback:n.get(e.id)}));continue}let{suggestions:r,...a}=t,o={...nxn(M7({row:a,fallback:e}),{...j7(t),...j7(e)})};for(let r of ["id","name","description","createdAt","harness","isGroup","memberIds","path","isHiddenFromSidebar","isRemovedFromSidebar"])o[r]=e[r];for(let r of ["title","avatarShape","avatarColor","suggestions"])e[r]!==void 0&&(o[r]=e[r]);o.avatarVersion=e.avatarVersion??null;o.avatarPhoto=e.avatarPhoto??null;o.avatarDataUrl=e.avatarDataUrl??(e.avatarVersion===t.avatarVersion?t.avatarDataUrl:null);o.updatedAt=Math.max(t.updatedAt,e.updatedAt);i.set(e.id,o)}for(let[e,t]of n){let n=i.get(e);if(!n)continue;let r=M7({row:n,fallback:t});i.set(e,r.harness===void 0&&t.harness===`temporal`?{...r,harness:t.harness}:r)}return[...i.values()].sort(Aj)}'
).ljust(len(ROSTER_STOCK), b" ")

# Restore the broad first-generation visibility bypasses on upgrade.
LEGACY_PATCHES = (
    (b"if(t.harness!==`temporal`)return t;", b"if(t.harness===`temporal`)return t;"),
    (b";o!=null&&(t.isHiddenFromSidebar===!0!==o||", b";o===!1&& (t.isHiddenFromSidebar===!0!==o||"),
    (b"function z7(e){return e.isTeamDefault&&!e.viewerIsOwner}", b"var z7=e=>e.isTeamDefault||e.harness[0]==`t`;           "),
    (b"function Xp(e){return e.viewerIsOwner===!1&&e.isTeamDefault===!0}", b"var Xp=e=>e.isTeamDefault||e.harness[0]==`t`;                    "),
)

# Some recovered platform messages contain finite values outside Date's range.
# Keep their content and IDs intact; an unknown date must not crash the chat.
TIME_STOCK = b"function gdt(e){return Es(e)?e:null}function QK(e){return gdt(e.timestampMs)}function _dt("
TIME_V2 = b"var gdt=e=>Es(e)&&Math.abs(e)<=864e13?e:null,QK=e=>gdt(e.timestampMs);".ljust(len(TIME_STOCK) - len(b"function _dt("), b" ") + b"function _dt("
ROW_TIME_STOCK = b"t[5]!==i||t[6]!==r?(c=r==null?null:(0,Y.jsx)(`time`,"
ROW_TIME_FIXED = ROW_TIME_STOCK.replace(b"c=r==null", b"c=gdt(r)==null")
TIME_FIXED = TIME_V2[:-len(b"function _dt(")].rstrip().ljust(
    len(TIME_STOCK) - len(b"function _dt(") - (len(ROW_TIME_FIXED) - len(ROW_TIME_STOCK)), b" "
) + b"function _dt("
if len(TIME_FIXED) + len(ROW_TIME_FIXED) != len(TIME_STOCK) + len(ROW_TIME_STOCK):
    raise ValueError("timestamp recovery changes asset size")

# Reviewed 0.75.3 renames bundled helpers and separates date-bucket and row
# timestamp readers. Keep exact anchors: a future or mixed bundle still refuses.
def rename_0753(value: bytes) -> bytes:
    for old, new in ((b'cxn', b'tSn'), (b'nxn', b'Yxn'), (b'Aj', b'kj')):
        value = value.replace(old, new)
    return value


ROSTER_0753_STOCK = rename_0753(ROSTER_STOCK)
ROSTER_0753_V1 = rename_0753(ROSTER_V1)
ROSTER_0753_FIXED = rename_0753(ROSTER_FIXED)
TIME_0753_STOCK = TIME_STOCK.replace(b'gdt', b'Rdt').replace(b'QK', b'rq').replace(b'_dt', b'zdt').replace(b'Es', b'ks')
TIME_0753_FIXED = TIME_V2.replace(b'gdt', b'Rdt').replace(b'QK', b'rq').replace(b'_dt', b'zdt').replace(b'Es', b'ks')
ROW_TIME_0753_FIXED = ROW_TIME_STOCK.replace(b'c=r==null', b'c=nJ(r)==null')
EXTRA_TIME_0753_STOCK = b'function nJ(e){return ks(e)?e:null}function rJ(e){return nJ(e.timestampMs)}function jft('
EXTRA_TIME_0753_FIXED = b'var nJ=e=>ks(e)&&Math.abs(e)<=864e13?e:null,rJ=e=>nJ(e.timestampMs);'.ljust(
    len(EXTRA_TIME_0753_STOCK) - len(b'function jft(') - (len(ROW_TIME_0753_FIXED) - len(ROW_TIME_STOCK)), b' '
) + b'function jft('
LAYOUTS = {
    '0.75.0': {'rosters': (ROSTER_STOCK, ROSTER_V1, ROSTER_FIXED),
               'times': (TIME_STOCK, TIME_V2, TIME_FIXED),
               'rows': (ROW_TIME_STOCK, ROW_TIME_FIXED), 'legacy': LEGACY_PATCHES},
    '0.75.3': {'rosters': (ROSTER_0753_STOCK, ROSTER_0753_V1, ROSTER_0753_FIXED),
               'times': (TIME_0753_STOCK, TIME_0753_FIXED),
               'rows': (ROW_TIME_STOCK, ROW_TIME_0753_FIXED),
               'extraTime': (EXTRA_TIME_0753_STOCK, EXTRA_TIME_0753_FIXED),
               'legacy': tuple((a.replace(b'Xp', b'Wp'), b.replace(b'Xp', b'Wp')) for a, b in LEGACY_PATCHES)},
}


def patch_state(data: bytes) -> dict[str, str]:
    layouts = [name for name, layout in LAYOUTS.items() if any(value in data for value in layout['rosters'])]
    if len(layouts) != 1:
        raise ValueError("unsupported or ambiguous roster merge layout")
    release = layouts[0]; layout = LAYOUTS[release]
    variants = list(zip(('stock', 'v1', 'fixed'), layout['rosters']))
    found = [name for name, value in variants if data.count(value) == 1]
    if len(found) != 1 or sum(data.count(value) for _, value in variants) != 1:
        raise ValueError("unsupported or ambiguous roster merge layout")
    if len({len(value) for _, value in variants}) != 1:
        raise ValueError("roster patch changes byte length")
    state = {"release": release, "roster": found[0]}
    times = list(zip(('stock', 'v2', 'fixed') if release == '0.75.0' else ('stock', 'fixed'), layout['times']))
    found_times = [name for name, value in times if data.count(value) == 1]
    if len(found_times) != 1 or sum(data.count(value) for _, value in times) != 1:
        raise ValueError("unsupported transcript timestamp layout")
    state["timestamp"] = found_times[0]
    row_stock, row_fixed = layout['rows']
    if data.count(row_stock) + data.count(row_fixed) != 1:
        raise ValueError("unsupported message timestamp layout")
    if (row_fixed in data) != (found_times[0] == "fixed"):
        raise ValueError("inconsistent timestamp recovery layout")
    if 'extraTime' in layout:
        stock, fixed = layout['extraTime']
        if data.count(stock) + data.count(fixed) != 1 or (fixed in data) != (found_times[0] == 'fixed'):
            raise ValueError('inconsistent secondary timestamp recovery layout')
        if len(layout['times'][0]) != len(layout['times'][-1]) or len(stock) + len(row_stock) != len(fixed) + len(row_fixed):
            raise ValueError('timestamp recovery changes asset size')
    for index, (stock, legacy) in enumerate(layout['legacy']):
        if len(stock) != len(legacy) or data.count(stock) + data.count(legacy) != 1:
            raise ValueError(f"unsupported visibility layout {index}")
        state[f"visibility-{index}"] = "stock" if stock in data else "v1"
    return state


def transform(data: bytes) -> bytes:
    state = patch_state(data)
    layout = LAYOUTS[state['release']]
    result = data
    if state["roster"] != "fixed":
        result = result.replace(layout['rosters'][0 if state['roster'] == 'stock' else 1], layout['rosters'][2], 1)
    for stock, legacy in layout['legacy']:
        result = result.replace(legacy, stock, 1)
    if state["timestamp"] != "fixed":
        result = result.replace(layout['times'][0 if state['timestamp'] == 'stock' else 1], layout['times'][-1], 1)
        result = result.replace(layout['rows'][0], layout['rows'][1], 1)
        if 'extraTime' in layout:
            result = result.replace(*layout['extraTime'], 1)
    if len(result) != len(data):
        raise ValueError("patch changed archive length")
    patch_state(result)
    return result


def packed_files(node: dict):
    for entry in node.get("files", {}).values():
        if "files" in entry:
            yield from packed_files(entry)
        elif "offset" in entry and not entry.get("unpacked"):
            yield entry


def refresh_integrity(original: bytes, patched: bytes) -> bytes:
    if len(original) < 16 or len(original) != len(patched):
        raise ValueError("invalid archive size")
    _, header_size, _, json_size = struct.unpack_from("<IIII", original)
    start = 8 + header_size
    if not 16 + json_size <= start <= len(original):
        raise ValueError("invalid archive header")
    raw_header = original[16:16 + json_size]
    header = json.loads(raw_header)
    for entry in packed_files(header):
        offset, size = start + int(entry["offset"]), int(entry["size"])
        if offset < start or offset + size > len(patched):
            raise ValueError("file outside archive bounds")
        content = patched[offset:offset + size]
        integrity = entry.get("integrity")
        if not integrity or original[offset:offset + size] == content:
            continue
        if integrity.get("algorithm") != "SHA256" or not isinstance(integrity.get("blockSize"), int) or integrity["blockSize"] <= 0:
            raise ValueError("unsupported file integrity format")
        integrity["hash"] = hashlib.sha256(content).hexdigest()
        block_size = integrity["blockSize"]
        integrity["blocks"] = [hashlib.sha256(content[pos:pos + block_size]).hexdigest() for pos in range(0, len(content), block_size)]
    encoded = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) != len(raw_header):
        raise ValueError("integrity update changes header size")
    return patched[:16] + encoded + patched[16 + json_size:]


def main() -> int:
    if not sys.platform.startswith("linux"):
        raise ValueError("this recovery supports the reviewed Linux desktop only")
    original = ASAR.read_bytes()
    patched = refresh_integrity(original, transform(original))
    if original == patched:
        print(f"grokbot-sidebar-authority-patch: already applied to {ASAR}")
        return 0
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = BACKUP_ROOT / f"app.asar.pre-sidebar-authority-{stamp}"
    shutil.copy2(ASAR, backup)
    before = ASAR.stat()
    temp = ASAR.with_name(f".{ASAR.name}.sidebar-authority-{os.getpid()}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(patched)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, before.st_mode & 0o777)
        if os.geteuid() == 0:
            os.chown(temp, before.st_uid, before.st_gid)
        if ASAR.read_bytes() != original:
            raise ValueError("archive changed during preparation; refusing to overwrite it")
        os.replace(temp, ASAR)
        dir_fd = os.open(ASAR.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        if ASAR.read_bytes() != patched:
            shutil.copy2(backup, ASAR)
            raise ValueError("post-write validation failed; restored backup")
    finally:
        temp.unlink(missing_ok=True)
    print(f"grokbot-sidebar-authority-patch: applied; backup={backup}; state={patch_state(patched)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"grokbot-sidebar-authority-patch: {error}", file=sys.stderr)
        raise SystemExit(2)
