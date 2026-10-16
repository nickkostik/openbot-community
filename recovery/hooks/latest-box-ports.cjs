"use strict";

// Upstream now coalesces same-ID peer deliveries. Retain that behavior around
// the BOX receiver, which must still queue and wake its local recipient.
function installPeerDeliveryCoalescing(PeerMessaging) {
  const original = PeerMessaging.prototype.receiveFromPeer;
  if (typeof original !== "function" || original.openbotPeerCoalescing === true)
    throw new Error("Unexpected local peer receiver");
  const pending = new WeakMap();
  const receive = async function (args) {
    if (typeof args?.messageId !== "string" || !args.messageId || typeof args.toAgentId !== "string")
      return original.call(this, args);
    let deliveries = pending.get(this);
    if (deliveries === undefined) { deliveries = new Map(); pending.set(this, deliveries); }
    const key = args.toAgentId + "\0" + args.messageId;
    if (deliveries.has(key)) return deliveries.get(key);
    const operation = Promise.resolve().then(() => original.call(this, args));
    deliveries.set(key, operation);
    try { return await operation; }
    finally { if (deliveries.get(key) === operation) deliveries.delete(key); }
  };
  receive.openbotPeerCoalescing = true;
  PeerMessaging.prototype.receiveFromPeer = receive;
}

// Host status can answer before asynchronous startup has finished. Record only
// bundle locations (never exception messages, prompts or credentials) so a
// controlled upgrade cannot mistake a partially started host for a healthy one.
function installLocalEngineStartupTrace(Host, fs, file) {
  const original = Host.prototype.start;
  if (typeof original !== "function") throw new Error("Missing host startup contract");
  const record = (phase, error) => {
    const frames = String(error?.stack ?? "").split("\n").slice(1, 12).flatMap((line) => {
      const match = line.match(/host-main\.cjs:(\d+):(\d+)\)?$/);
      return match ? [{ line: Number(match[1]), column: Number(match[2]) }] : [];
    });
    const data = { phase, pid: process.pid, atMs: Date.now(),
      ...(error ? { errorClass: error.name === "TypeError" ? "TypeError" :
        error.name === "ReferenceError" ? "ReferenceError" : "Error", frames } : {}) };
    try {
      const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
      try {
        const info = fs.fstatSync(fd);
        if (!info.isFile() || info.uid !== process.getuid()) throw new Error("Untrusted startup trace");
        fs.fchmodSync(fd, 0o600);
        fs.ftruncateSync(fd, 0);
        fs.writeFileSync(fd, JSON.stringify(data) + "\n");
        fs.fsyncSync(fd);
      } finally { fs.closeSync(fd); }
    } catch {
      // Diagnostic storage must not change the native startup outcome.
    }
  };
  Host.prototype.start = async function (...args) {
    record("starting");
    try {
      const result = await original.apply(this, args);
      record("ready");
      return result;
    } catch (error) {
      record("failed", error);
      throw error;
    }
  };
}

function localSendOptions(args, environment) {
  return {
    agentId: args.agentId, directAddressedAcceptance: args.directAddressedAcceptance,
    attachmentPaths: args.attachmentPaths ?? [], attachmentNames: args.attachmentNames ?? [],
    richText: args.richText, replyToId: args.replyToId, clientNonce: args.clientNonce,
    isFork: args.isFork, automationWriteProvenance: args.automationWriteProvenance,
    traceparent: args.traceparent, enterEpochMs: args.enterEpochMs, composedAtMs: args.composedAtMs,
    mcpConfigJson: args.mcpConfigJson, machineId: args.machineId,
    awaitTurn: environment.sendAcceptReturnDisabled === true,
  };
}

function installLocalEngineSendTrace(Pipeline, fs, file) {
  const original = Pipeline.prototype.sendPrompt;
  if (typeof original !== "function") throw new Error("Missing local send contract");
  Pipeline.prototype.sendPrompt = async function (...args) {
    try { return await original.apply(this, args); }
    catch (error) {
      try {
        const frames = String(error?.stack ?? "").split("\n").slice(1, 12).flatMap((line) => {
          const match = line.match(/host-main\.cjs:(\d+):(\d+)\)?$/);
          return match ? [{ line: Number(match[1]), column: Number(match[2]) }] : [];
        });
        const data = { phase: "failed", pid: process.pid, atMs: Date.now(),
          errorClass: error.name === "TypeError" ? "TypeError" :
            error.name === "ReferenceError" ? "ReferenceError" : "Error", frames };
        const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
        try {
          const info = fs.fstatSync(fd);
          if (!info.isFile() || info.uid !== process.getuid()) throw new Error("Untrusted send trace");
          fs.fchmodSync(fd, 0o600); fs.ftruncateSync(fd, 0);
          fs.writeFileSync(fd, JSON.stringify(data) + "\n"); fs.fsyncSync(fd);
        } finally { fs.closeSync(fd); }
      } catch {
        // Never replace the native delivery exception with a diagnostic error.
      }
      throw error;
    }
  };
}

// Latest tools and the retained loop need the union of their gate interfaces.
// New gates follow reviewed upstream defaults; existing local readers keep
// their live flag, policy and permission decisions.
function composeLocalRunnerGates(wirings, defaults, compose, added) {
  const classified = { ...wirings };
  for (const [name, value] of Object.entries(defaults)) {
    if (Object.hasOwn(classified, name)) continue;
    if (!Object.hasOwn(added, name) || added[name] !== value || typeof value !== "boolean")
      throw new Error("Unreviewed local runner gate: " + name);
    classified[name] = { kind: "fixed", value, why: "reviewed upstream default for the local adapter" };
  }
  const readers = compose(classified);
  for (const [name, wiring] of Object.entries(wirings)) {
    if (Object.hasOwn(readers, name)) continue;
    if (typeof wiring === "function") readers[name] = wiring;
    else if (wiring?.kind === "fixed" && typeof wiring.value === "boolean") readers[name] = () => wiring.value;
    else throw new Error("Unsupported retained runner gate: " + name);
  }
  return readers;
}

// TurnRuntime settles errors internally after durable acceptance. Observe the
// execution boundary without retaining prompts, exception messages or tool args.
function installLocalEngineTurnTrace(Runtime, fs, prefix) {
  const original = Runtime.prototype.runAgentTurn;
  if (typeof original !== "function") throw new Error("Missing local turn contract");
  const record = (session, phase, result, error) => {
    if (!/^[a-f0-9-]{36}$/.test(session?.id ?? "")) return;
    const frames = String(error?.stack ?? "").split("\n").slice(1, 16).flatMap((line) => {
      const match = line.match(/host-main\.cjs:(\d+):(\d+)\)?$/);
      return match ? [{line:Number(match[1]), column:Number(match[2])}] : [];
    });
    // Only a JavaScript member-call signature is useful for interface ports.
    // Do not record free-form messages, receivers, prompts, arguments or URLs.
    const missingMember = error?.name === "TypeError" ? String(error.message ?? "").match(
      /^(?:[A-Za-z_$][A-Za-z0-9_$]*\.)+([A-Za-z_$][A-Za-z0-9_$]{0,63}) is not a function$/)?.[1] : undefined;
    const data = {phase, pid:process.pid, atMs:Date.now(),
      ...(result ? {aborted:result.aborted === true, pausedForUpgrade:result.pausedForUpgrade === true} : {}),
      ...(error ? {errorClass:["TypeError", "ReferenceError"].includes(error.name) ? error.name : "Error", frames,
        ...(missingMember ? {missingMember} : {})} : {})};
    try {
      const fd = fs.openSync(prefix + session.id + ".json",
        fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
      try {
        const info = fs.fstatSync(fd);
        if (!info.isFile() || info.uid !== process.getuid()) throw new Error("Untrusted turn trace");
        fs.fchmodSync(fd, 0o600); fs.ftruncateSync(fd, 0);
        fs.writeFileSync(fd, JSON.stringify(data) + "\n"); fs.fsyncSync(fd);
      } finally { fs.closeSync(fd); }
    } catch { /* Diagnostics cannot change execution. */ }
  };
  Runtime.prototype.runAgentTurn = async function (session, ...args) {
    record(session, "started");
    try {
      const result = await original.call(this, session, ...args);
      record(session, "returned", result); return result;
    } catch (error) { record(session, "failed", null, error); throw error; }
  };
}

function localTaskArguments(args, turn) {
  return {...args, turn:{...turn, config:args.config, toolSession:args.toolSession,
    summarizationHandler:args.summarizationHandler}};
}

// New hosts no longer expose the optional legacy name and team-rule readers.
// Let the retained request context use its defaults when those APIs are absent,
// while preserving live reads and their receiver on compatible older hosts.
function localRequestContextOptions(base, auth, managedSetup) {
  if (auth == null || managedSetup == null) throw new TypeError("Missing native context service");
  const options = {...base};
  for (const [field, service, method] of [
    ["resolveUserFullName", auth, "getUserFullName"],
    ["resolveRules", managedSetup, "resolveTeamRules"],
  ]) {
    const reader = service[method];
    if (reader === undefined) continue;
    if (typeof reader !== "function") throw new TypeError("Invalid native context reader: " + method);
    options[field] = () => reader.call(service);
  }
  return options;
}

// Upstream can remove an optional extension while the local runner still has
// its tool wiring. Absence means unavailable; live policy remains authoritative.
function localEmailEnabled(service) {
  if (service == null) return false;
  if (typeof service.isEnabled !== "function") throw new TypeError("Invalid native email capability");
  return service.isEnabled() === true;
}

function localBuiltinCatalogOptions(registry) {
  if (registry == null || registry.getExternalCatalogOptions === undefined) return undefined;
  if (typeof registry.getExternalCatalogOptions !== "function") throw new TypeError("Invalid native catalog reader");
  return registry.getExternalCatalogOptions();
}

module.exports = { installPeerDeliveryCoalescing, installLocalEngineStartupTrace,
  localSendOptions, installLocalEngineSendTrace, composeLocalRunnerGates, installLocalEngineTurnTrace,
  localTaskArguments, localRequestContextOptions, localEmailEnabled, localBuiltinCatalogOptions };
