"""Keep recovered plain-text messages compatible with native desktop schemas.

Older room histories can contain send-message bodies with content but no type.
Those bodies also appear in roster pushMessageContent: one malformed preview
makes the desktop reject the entire listAgents response. Normalize the known
text shape on read and before persistence/live publication, without editing IDs,
history, typed cards, or unrelated malformed payloads.
"""

MARK = 'openbot-transcript-text-compat-v1'
HELPER = '''function openbotNormalizeLegacyTextEntry(entry) {
  if (entry == null || typeof entry !== "object" || entry.kind !== "send-message") return entry;
  const message = entry.message;
  if (message == null || typeof message !== "object" || Array.isArray(message) ||
      message.type !== undefined || typeof message.content !== "string") return entry;
  return { ...entry, message: { ...message, type: "text" } };
} /* openbot-transcript-text-compat-v1: helper */
'''
READ_ANCHOR = 'function transcriptEntryOfJson(parsed2) {'
WRITE_ANCHOR = 'function withTimestampMs(entry) {'
READ_PATCH = READ_ANCHOR + '\n  parsed2 = openbotNormalizeLegacyTextEntry(parsed2); /* ' + MARK + ': read */'
WRITE_PATCH = WRITE_ANCHOR + '\n  entry = openbotNormalizeLegacyTextEntry(entry); /* ' + MARK + ': write */'


def patch(source):
    if source.count(READ_ANCHOR) != 1 or source.count(WRITE_ANCHOR) != 1:
        raise RuntimeError('Expected the native transcript read and write boundaries')
    if MARK in source:
        if (source.count(MARK) != 3 or source.count(HELPER) != 1 or
                source.count(READ_PATCH) != 1 or source.count(WRITE_PATCH) != 1):
            raise RuntimeError('Transcript text compatibility patch is incomplete or drifted')
        return source
    if 'openbotNormalizeLegacyTextEntry' in source:
        raise RuntimeError('Unexpected transcript text compatibility helper')
    return source.replace(READ_ANCHOR, HELPER + READ_PATCH, 1).replace(
        WRITE_ANCHOR, WRITE_PATCH, 1)
