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
