"""Pure, reviewed-source transform preserving authored cron timing on native writes.

This module does not read agents, write a host, select an engine, reload a process,
change enabled flags, or dispatch work. The engine supervisor must stage, syntax
check, wrap, and verify a new generation before its normal idle activation gate.
"""
import hashlib

MARK = 'openbot-native-routine-trigger-v1'
START = 'function serializeStoredAutomationConfig(config3) {'
END = '\nfunction clampAutomationRunDetail('
OLD = '      ...config3.trigger.type === "cron" ? {} : { trigger: serializeStoredTrigger(config3.trigger) },'
NEW = '      trigger: serializeStoredTrigger(config3.trigger), /* openbot-native-routine-trigger-v1 */'
BEFORE_BLOCK_SHA256 = '6effbe26134b14fdc6bf5c97dcc3f8f9e7dc314ea4d1580ce1e7a05433a2b1ce'
AFTER_BLOCK_SHA256 = 'b3f0f465834ead246d10f342dd3d07b35f67aad13df49392cfb187efa041b7cb'
# Sources are reviewed stock artifacts, never arbitrary caller-supplied digests.
REVIEWED = {
    '9daa44269382eb968600cf455f79c488da6e81e7cb36c27dc8ecd94234226dea':
        'fa69abc37853ac3382caf8e2f89ca791ad40617204a1bc79eeb442415ebcc0f6',
    '74278c1ec791b38a235bf68e386dbbe86428bd2f9966baceb29a1173bc574dc3':
        '9b5f0560fc980adcfa1e08d4b65c7ae367cc76c616d88ba9befd1c01e8a598b6',
}
MAX_BYTES = 128 * 1024 * 1024


def sha256(source):
    return hashlib.sha256(source.encode('utf8')).hexdigest()


def serializer_block(source):
    if source.count(START) != 1 or source.count(END) != 1:
        raise RuntimeError('Native routine serializer boundary changed')
    start = source.index(START)
    end = source.index(END)
    if end <= start:
        raise RuntimeError('Native routine serializer boundary order changed')
    return source[start:end]


def patch(source):
    """Return a fixed-point stock candidate; refuse unknown versions before edit."""
    if not isinstance(source, str) or len(source.encode('utf8')) > MAX_BYTES:
        raise RuntimeError('Native routine source exceeds its bound')
    original_sha = sha256(source)
    if original_sha in REVIEWED.values():
        if (sha256(serializer_block(source)) != AFTER_BLOCK_SHA256 or
                source.count(MARK) != 1 or source.count(NEW) != 1 or OLD in source):
            raise RuntimeError('Native routine preservation patch is incomplete')
        return source
    expected = REVIEWED.get(original_sha)
    if expected is None:
        raise RuntimeError('Unreviewed native routine writer source')
    block = serializer_block(source)
    if (sha256(block) != BEFORE_BLOCK_SHA256 or block.count(OLD) != 1 or
            source.count(OLD) != 1 or MARK in source):
        raise RuntimeError('Native routine serializer source changed')
    result = source.replace(OLD, NEW, 1)
    if (sha256(result) != expected or
            sha256(serializer_block(result)) != AFTER_BLOCK_SHA256):
        raise RuntimeError('Native routine preservation output differs from review')
    return result


# Canonical injector authority pins the exact native component and its public
# parser/trigger serializer dependencies. Its enclosing bundle remains under the
# supervisor's independent engine/source CAS; no caller supplies these digests.
DEPENDENCIES = (
    ('function parseStoredAutomationConfig(', '\nfunction serializeStoredAutomationConfig(',
     '1444c734cbf60e3a7a60108394dcbdf0e4ff684a427fd282a32e0436d9dd622f'),
    ('function serializeStoredTrigger(', '\nfunction triggerIdentity(',
     '78b74e04b37f88326b7b0b143d48b6a8b27e884679422fb0ebedb38c4a66728f'),
)

# The 0.76.0-pre.16 vendor build only renames local parser/serializer bindings.
# Pin that exact component separately; do not normalize arbitrary source text.
CURRENT_COMPONENT = {
    'start': 'function serializeStoredAutomationConfig(config2) {',
    'old': '      ...config2.trigger.type === "cron" ? {} : { trigger: serializeStoredTrigger(config2.trigger) },',
    'new': '      trigger: serializeStoredTrigger(config2.trigger), /* openbot-native-routine-trigger-v1 */',
    'before': '9d3f4768d4c49d1e88d96519f0940d8e61005f6c538a5d65c7c01546ee596ff9',
    'after': '07c71248ab799077671d2764337c8b73aa681d491741e60af81495d832b12b46',
    'dependencies': (
        ('function parseStoredAutomationConfig(', '\nfunction serializeStoredAutomationConfig(',
         'a0e655f0396d44a13a92c06ccaa63c3dc35cc670bd9e9f557a5117f300d43aaa'),
        DEPENDENCIES[1],
    ),
}


def patch_component(source):
    """Canonical injector for a separately CAS-verified engine preparation.

    Wrapper/header changes are allowed outside the reviewed native component.
    Changed dependencies, partial markers and ambiguous copies always refuse.
    This API performs the same one-line edit and never writes or activates it.
    """
    if not isinstance(source, str) or len(source.encode('utf8')) > MAX_BYTES:
        raise RuntimeError('Native routine source exceeds its bound')
    layouts = ({'start': START, 'old': OLD, 'new': NEW,
                'before': BEFORE_BLOCK_SHA256, 'after': AFTER_BLOCK_SHA256,
                'dependencies': DEPENDENCIES}, CURRENT_COMPONENT)
    matching = [layout for layout in layouts if source.count(layout['start']) == 1]
    if (len(matching) != 1 or source.count('function serializeStoredAutomationConfig(') != 1 or
            source.count(END) != 1):
        raise RuntimeError('Native routine serializer boundary changed')
    layout = matching[0]
    for start, end, expected in layout['dependencies']:
        if source.count(start) != 1 or source.count(end) != 1:
            raise RuntimeError('Native routine dependency boundary changed')
        a, b = source.index(start), source.index(end)
        if b <= a or sha256(source[a:b]) != expected:
            raise RuntimeError('Native routine dependency source changed')
    start = source.index(layout['start'])
    end = source.index(END)
    if end <= start:
        raise RuntimeError('Native routine serializer boundary order changed')
    block = source[start:end]
    if sha256(block) == layout['after']:
        if source.count(MARK) != 1 or source.count(layout['new']) != 1 or layout['old'] in source:
            raise RuntimeError('Native routine preservation patch is incomplete')
        return source
    if (sha256(block) != layout['before'] or block.count(layout['old']) != 1 or
            source.count(layout['old']) != 1 or MARK in source):
        raise RuntimeError('Native routine serializer source changed')
    result = source.replace(layout['old'], layout['new'], 1)
    if sha256(result[start:result.index(END)]) != layout['after']:
        raise RuntimeError('Native routine preservation output differs from review')
    return result


def proof(source, result):
    """Describe only the transform; never claim activation or native delivery."""
    if patch(source) != result:
        raise RuntimeError('Native routine patch proof differs from candidate')
    return {'schema': 'openbot-native-routine-trigger-patch/v1',
            'sourceSha256': sha256(source), 'candidateSha256': sha256(result),
            'serializerSha256': sha256(serializer_block(result)),
            'authoredTriggerRetained': True, 'legacyScheduleRetained': True,
            'historyLimitChanged': False, 'agentFilesChanged': False,
            'modeChanged': False, 'activated': False, 'liveApplied': False}
