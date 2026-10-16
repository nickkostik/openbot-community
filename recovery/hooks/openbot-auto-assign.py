#!/usr/bin/env python3
"""Audit default inheritance without pinning bots to a stale provider/model."""
import argparse
import json
import os
import pathlib
import stat
import uuid

SD = pathlib.Path('/home/box/sand-data')


def read_json(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4 * 1024 * 1024:
        raise RuntimeError('Expected an owned bounded regular JSON file')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError('Expected a JSON object')
    return value


def audit(root=SD):
    mode = (root / 'openbot-mode').read_text().strip()
    if mode not in ('official', 'custom'):
        raise RuntimeError('Unknown saved OpenBot mode')
    plan = read_json(root / 'openbot-plan.json')
    wildcard = plan.get('agents', {}).get('*', {})
    models = plan.get('catalog', {}).get('models', [])
    if not isinstance(models, list) or any(not isinstance(row, dict) for row in models):
        raise RuntimeError('Unknown catalog model schema')
    model_ids = {row.get('id') for row in models}
    matches = [row for row in models if row.get('providerId') == wildcard.get('providerId') and
               row.get('slug') == wildcard.get('modelId')]
    selected = matches[0].get('id') if len(matches) == 1 else None
    if mode == 'custom' and (not isinstance(selected, str) or selected not in model_ids):
        raise RuntimeError('Custom default is absent from the catalog')
    path = root / 'openbot-bot-models.json'
    settings = read_json(path) if path.exists() or path.is_symlink() else {}
    pins = settings.get('assignments', {})
    if not isinstance(pins, dict) or any(not isinstance(v, str) for v in pins.values()):
        raise RuntimeError('Unknown per-bot model schema')
    agents = root / 'agents'
    if agents.is_symlink() or not agents.is_dir():
        raise RuntimeError('Expected a regular agent directory')
    inherited = 0
    pinned = 0
    stale = []
    for directory in sorted(agents.iterdir()):
        try:
            if str(uuid.UUID(directory.name)) != directory.name:
                continue
        except ValueError:
            continue
        if directory.is_symlink():
            raise RuntimeError('Agent directory must not be symlinked')
        profile_path = directory / 'profile.json'
        if not profile_path.exists() and not profile_path.is_symlink():
            continue
        profile = read_json(profile_path)
        if profile.get('id', directory.name) != directory.name:
            raise RuntimeError('Profile identity differs from its directory')
        settings_path = directory / 'settings.json'
        bot = read_json(settings_path) if settings_path.exists() or settings_path.is_symlink() else {}
        if bot.get('hiddenFromSidebar') is True:
            continue
        chosen = pins.get(directory.name)
        if not chosen:
            inherited += 1
        else:
            pinned += 1
            if chosen not in model_ids:
                stale.append(directory.name)
    return {'ok': True, 'operation': 'audit-default-inheritance', 'mode': mode,
            'globalModelId': selected, 'effectiveDefault': selected if mode == 'custom' else 'official',
            'visibleBots': inherited + pinned, 'inheritsDefault': inherited,
            'explicitPins': pinned, 'stalePinBotIds': stale, 'settingsChanged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=SD)
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.root)))
        return 0
    except Exception as error:
        # Parsing exceptions can include private file contents: expose the type only.
        print(json.dumps({'ok': False, 'operation': 'audit-default-inheritance',
                          'errorType': type(error).__name__, 'settingsChanged': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
