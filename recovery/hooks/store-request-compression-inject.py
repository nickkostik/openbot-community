"""Use native Connect gzip for large store RPCs, preserving all commit checks."""

MARKER = '/* openbot-store-request-compression-v1 */'
FACTORY = '''function createSandBoxStoreServiceClient(deps) {
  return createPromiseClient(
    GrokBotService,
    createSandBackendTransport(sandBoxStoreTransportOptions(deps))
  );
}'''
FACTORY_UPDATED = FACTORY.replace(
    'createSandBackendTransport(sandBoxStoreTransportOptions(deps))',
    'createSandBackendTransport({ ...sandBoxStoreTransportOptions(deps), openbotStoreRpcCompression: true })')
SAFE = '''    sendCompression: null,
    acceptCompression: [compressionGzip, compressionBrotli],
    ...validateReadWriteMaxBytes(void 0, void 0, void 0)
  });
}
function createSandBackendTransport(options2) {'''
SAFE_UPDATED = SAFE.replace('    sendCompression: null,',
    '    /* openbot-store-request-compression-v1 */\n'
    '    sendCompression: options2.openbotStoreRpcCompression === true ? compressionGzip : null,').replace(
        '...validateReadWriteMaxBytes(void 0, void 0, void 0)',
        '...validateReadWriteMaxBytes(void 0, void 0, options2.openbotStoreRpcCompression === true ? 1048576 : void 0)')
BACKEND = '''    httpVersion: options2.httpVersion ?? "1.1"
  });
}
function createSandCursorBackendClient(service, options2) {'''
BACKEND_UPDATED = BACKEND.replace('    httpVersion: options2.httpVersion ?? "1.1"',
    '    httpVersion: options2.httpVersion ?? "1.1",\n'
    '    openbotStoreRpcCompression: options2.openbotStoreRpcCompression === true')


def patch(source):
    replacements = [(SAFE, SAFE_UPDATED), (BACKEND, BACKEND_UPDATED), (FACTORY, FACTORY_UPDATED)]
    if MARKER in source:
        if source.count(MARKER) != 1 or any(source.count(new) != 1 for _, new in replacements):
            raise RuntimeError('Native store request compression patch is incomplete')
        return source
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError('Native store transport contract is missing or ambiguous')
        source = source.replace(old, new, 1)
    return source
