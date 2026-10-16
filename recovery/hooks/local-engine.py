"""Select an explicitly pinned, hash-checked local engine before native patches.

An updated host can delete the BOX execution path altogether. A feature-gate
patch cannot reconstruct it. This installation may retain its tested engine in
sand-data; it never downloads or guesses a replacement during a recovery tick.
"""
import hashlib
import json
import os
import pathlib
import re
import stat

MAX_BYTES = 48 * 1024 * 1024


def owned_bytes(path, limit):
    path = pathlib.Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise RuntimeError('Local engine artifact is not an owned bounded regular file')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError('Local engine artifact exceeds its bound')
        return data
    finally:
        os.close(fd)


def policy(sd):
    path = sd / 'openbot-local-engine.json'
    try:
        data = owned_bytes(path, 16384)
    except FileNotFoundError:
        return None
    value = json.loads(data)
    if not isinstance(value, dict) or value.get('schemaVersion') != 1 or type(value.get('enabled')) is not bool:
        raise RuntimeError('Unsupported local engine policy')
    if value['enabled'] is False:
        return None
    for key in ('wrappedSha256', 'stockSha256'):
        if not isinstance(value.get(key), str) or not re.fullmatch('[0-9a-f]{64}', value[key]):
            raise RuntimeError('Local engine policy needs both exact SHA256 hashes')
    if 'generationId' in value and (not isinstance(value['generationId'], str) or
                                     not re.fullmatch('[0-9a-f]{32}', value['generationId'])):
        raise RuntimeError('Invalid local engine generation')
    return value


def artifact_directory(sd, config):
    directory = pathlib.Path(sd) / 'openbot-local-engine'
    paths = [directory]
    if 'generationId' in config:
        paths.extend([directory / 'generations', directory / 'generations' / config['generationId']])
    for path in paths:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Local engine directory must be owned and not symlinked')
    return paths[-1]


def require_runner(source):
    anchor = '  async sendPrompt(prompt, options2) {'
    if source.count(anchor) != 1:
        raise RuntimeError('Local engine has no unambiguous native send runner')
    body = source.split(anchor, 1)[1].split('\n  nextTurnEpoch(', 1)[0]
    if len(body) < 4000 or 'runServerScheduledAutomation' not in source or 'runAgentTurn' not in source:
        raise RuntimeError('Native BOX execution was removed; a gate patch is insufficient')


def preserve_payload_stamp(source, current):
    """Accept only a fingerprint change to the complete canonical wrapper."""
    stamp = re.compile(r'^/\* openbot-payload [0-9a-f]{16} \*/$', re.MULTILINE)
    if source.count('/* openbot-stock-wrap */') == 1 and isinstance(current, str):
        if len(stamp.findall(source)) == len(stamp.findall(current)) == 1:
            if stamp.sub('/* openbot-payload */', source) == stamp.sub('/* openbot-payload */', current):
                return current
    return source


def select_source(sd, path, current):
    config = policy(sd)
    if config is None:
        return current
    directory = artifact_directory(sd, config)
    stock = path.name == 'host-main.cjs.pre-openbot'
    name, key = ('stock.cjs', 'stockSha256') if stock else ('wrapped.cjs', 'wrappedSha256')
    data = owned_bytes(directory / name, MAX_BYTES)
    if hashlib.sha256(data).hexdigest() != config[key]:
        raise RuntimeError('Pinned local engine hash differs; no native source replaced')
    source = data.decode('utf8')
    require_runner(source)
    if source.count('/* openbot-stock-wrap */') != (0 if stock else 1):
        raise RuntimeError('Pinned local engine wrap kind differs')
    # Reconcile changes only this header when an owner deploys new payload
    # modules. Restoring the old stamp would make recovery and the guard
    # repeatedly rewrite the same healthy engine and restart its service.
    # Preserve the current source only when EVERY other byte still matches
    # the trusted, hash-checked engine; an actual host update is restored.
    return preserve_payload_stamp(source, current) if not stock else source


def summary(sd):
    config = policy(sd)
    return {'state': 'not-pinned'} if config is None else {
        'state': 'pinned', 'wrappedSha256': config['wrappedSha256'],
        **({'generationId': config['generationId']} if 'generationId' in config else {}),
        'clientVersion': config.get('clientVersion'), 'nativeSendVerified': False}
