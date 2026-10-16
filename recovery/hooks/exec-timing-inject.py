"""Opt-in timings separate daemon execution from host stream-consumer waits."""
MARK = 'openbot-exec-timing-v1'
ANCHOR = 'var BoxRemoteExecManager = class {'
HELPER = '''function openbotExecTimingEnabled() {
  try {
    const fs = require("node:fs");
    const info = fs.lstatSync("/home/box/sand-data/openbot-exec-timing.enable");
    return info.isFile() && info.uid === process.getuid();
  } catch { return false; }
}
function openbotExecTiming(request, phase, startedAtMs, extra = {}) {
  try {
    const args = request.message?.value ?? {};
    process.stderr.write("[openbot:exec-timing] " + JSON.stringify({
      phase, atMs: Date.now(), elapsedMs: Date.now() - startedAtMs,
      id: request.id, execId: request.execId, toolCallId: args.toolCallId,
      kind: request.message?.case, ...extra
    }) + "\\n");
  } catch { /* Diagnostics must not change execution or error handling. */ }
} /* openbot-exec-timing-v1 */
'''
BEFORE = '''    const response = this.client.exec(ctx, argsSerializer(this.nextId++));
    for await (const message of response) {
      if (message.element.case === "execClientMessage") {
        yield message.element.value;
      } else if (message.element.case === "execClientControlMessage" && message.element.value.message.case === "throw") {
        const thrown = message.element.value.message.value;
        const error42 = new Error(thrown.error);
        if (thrown.stackTrace !== void 0 && thrown.stackTrace.length > 0) {
          error42.stack = thrown.stackTrace;
        }
        throw error42;
      }
    }'''
AFTER = '''    const request = argsSerializer(this.nextId++);
    const trace = request.message?.case === "shellStreamArgs" && openbotExecTimingEnabled();
    const startedAtMs = Date.now();
    if (trace) openbotExecTiming(request, "request", startedAtMs);
    try {
      const response = this.client.exec(ctx, request);
      for await (const message of response) {
        if (message.element.case === "execClientMessage") {
          const value = message.element.value;
          if (trace) openbotExecTiming(request, "received", startedAtMs, {
            event: value.message?.value?.event?.case,
            daemonElapsedMs: value.localExecutionTimeMs,
            shellElapsedMs: value.message?.value?.event?.value?.localExecutionTimeMs
          });
          yield value;
          if (trace) openbotExecTiming(request, "consumed", startedAtMs);
        } else if (message.element.case === "execClientControlMessage" && message.element.value.message.case === "throw") {
          const thrown = message.element.value.message.value;
          const error42 = new Error(thrown.error);
          if (thrown.stackTrace !== void 0 && thrown.stackTrace.length > 0) {
            error42.stack = thrown.stackTrace;
          }
          throw error42;
        }
      }
    } finally {
      if (trace) openbotExecTiming(request, "closed", startedAtMs);
    }'''
LEGACY_AFTER = AFTER
AFTER = AFTER.replace(
    'request.message?.case === "shellStreamArgs" && openbotExecTimingEnabled()',
    '["shellStreamArgs", "shellArgs"].includes(request.message?.case) && openbotExecTimingEnabled()'
).replace(
    'event: value.message?.value?.event?.case,',
    'messageKind: value.message?.case, event: value.message?.value?.event?.case,'
)
AUDIT_BEFORE = '''      const startedAtMs = Date.now();
      let ran = false;
      let exitCode;
      try {
        for await (const chunk of inner.execute(ctx, args, options2)) {
          ran = true;
          if (chunk.event.case === "exit") exitCode = chunk.event.value.code | 0;
          yield chunk;
        }
        ran = true;
      } finally {
        if (ran) {'''
AUDIT_AFTER = '''      const startedAtMs = Date.now();
      const trace = openbotExecTimingEnabled();
      const traceRequest = { message: { case: "shellStreamArgs", value: args }, execId: options2?.execId };
      if (trace) openbotExecTiming(traceRequest, "audit-start", startedAtMs);
      let ran = false;
      let exitCode;
      try {
        for await (const chunk of inner.execute(ctx, args, options2)) {
          ran = true;
          if (chunk.event.case === "exit") exitCode = chunk.event.value.code | 0;
          if (trace) openbotExecTiming(traceRequest, "audit-received", startedAtMs, { event: chunk.event.case });
          yield chunk;
          if (trace) openbotExecTiming(traceRequest, "audit-consumed", startedAtMs);
        }
        ran = true;
      } finally {
        if (trace) openbotExecTiming(traceRequest, "audit-closed", startedAtMs);
        if (ran) {'''

SHELL_BEFORE = '''  const boxShellStream = {
    execute: (ctx, args, options2) => (async function* () {
      const connection = await connect6(ctx);
      guardAutoReviewBarrier();
      try {
        await awaitShellNavigationBaseline(ctx, connection);
        guardAutoReviewBarrier();'''
SHELL_AFTER = '''  const boxShellStream = {
    execute: (ctx, args, options2) => (async function* () {
      const setupTrace = openbotExecTimingEnabled();
      const setupStartedAtMs = Date.now();
      const setupRequest = { message: { case: "shellStreamArgs", value: args }, execId: options2?.execId };
      if (setupTrace) openbotExecTiming(setupRequest, "connection-start", setupStartedAtMs);
      const connection = await connect6(ctx);
      if (setupTrace) openbotExecTiming(setupRequest, "connection-ready", setupStartedAtMs);
      guardAutoReviewBarrier();
      try {
        if (setupTrace) openbotExecTiming(setupRequest, "baseline-start", setupStartedAtMs);
        await awaitShellNavigationBaseline(ctx, connection);
        if (setupTrace) openbotExecTiming(setupRequest, "baseline-ready", setupStartedAtMs);
        guardAutoReviewBarrier();'''


def patch(source):
    if MARK in source:
        if source.count(HELPER) != 1 or source.count(AFTER) + source.count(LEGACY_AFTER) != 1 or source.count(AUDIT_AFTER) != 1:
            raise RuntimeError('Exec timing patch is incomplete or ambiguous')
        source = source.replace(LEGACY_AFTER, AFTER, 1)
        if source.count(SHELL_AFTER) == 1 and SHELL_BEFORE not in source:
            return source
        if source.count(SHELL_BEFORE) == 1 and SHELL_AFTER not in source:
            return source.replace(SHELL_BEFORE, SHELL_AFTER, 1)
        raise RuntimeError('Shell setup timing patch is incomplete or ambiguous')
    if source.count(ANCHOR) != 1 or source.count(BEFORE) != 1 or source.count(AUDIT_BEFORE) != 1 or source.count(SHELL_BEFORE) != 1:
        raise RuntimeError('Exec timing anchors changed; no native source replaced')
    return source.replace(ANCHOR, HELPER + ANCHOR, 1).replace(BEFORE, AFTER, 1).replace(AUDIT_BEFORE, AUDIT_AFTER, 1).replace(SHELL_BEFORE, SHELL_AFTER, 1)
