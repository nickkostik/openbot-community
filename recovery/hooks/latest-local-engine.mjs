#!/usr/bin/env node
/** Build a latest-host candidate with a separately audited BOX compatibility layer. */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, mkdirSync, constants, openSync, closeSync, fstatSync } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(resolve(process.env.OPENBOT_PACKAGE_ROOT ||
  fileURLToPath(new URL("../../", import.meta.url)), "package.json"));
const { Parser, tokTypes, parse } = require("acorn");
const { analyze } = require("eslint-scope");
const { installPeerDeliveryCoalescing, installLocalEngineStartupTrace,
  localSendOptions, installLocalEngineSendTrace, composeLocalRunnerGates,
  installLocalEngineTurnTrace, localTaskArguments, localRequestContextOptions, localEmailEnabled, localBuiltinCatalogOptions } = createRequire(import.meta.url)("./latest-box-ports.cjs");
const MAX_SOURCE = 48 * 1024 * 1024;
const HEADER = /^\s*((?:src\/|\.\.\/|node_modules\/)[^\s]+\.(?:[cm]?[jt]sx?|json|md|txt))\s*$/;
const digest = (data) => createHash("sha256").update(data).digest("hex");
const stem = (name) => name.replace(/\d+$/, "");
const NAMESPACE = "__openbot_box_";

function walk(node, visit) {
  if (!node?.type) return;
  visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (["start", "end", "range", "loc"].includes(key)) continue;
    if (value?.type) walk(value, visit);
    else if (Array.isArray(value)) for (const child of value) if (child?.type) walk(child, visit);
  }
}

function scan(source, options, visit) {
  const StreamingParser = Parser.extend((Base) => class extends Base {
    parseTopLevel(node) {
      node.body = []; node.sourceType = this.options.sourceType;
      const exports = Object.create(null);
      while (this.type !== tokTypes.eof) visit(this.parseStatement(null, true, exports));
      this.next(); return this.finishNode(node, "Program");
    }
  });
  return StreamingParser.parse(source, options);
}

export function ownerAt(markers, position) {
  let left = 0, right = markers.length - 1, module = "<entry>";
  while (left <= right) {
    const mid = (left + right) >> 1;
    if (markers[mid].start <= position) { module = markers[mid].module; left = mid + 1; }
    else right = mid - 1;
  }
  return module;
}

/** Resolve lexical bindings one statement at a time, with bounded parser memory. */
export function indexBundle(source) {
  if (Buffer.byteLength(source) > MAX_SOURCE) throw new Error("Source exceeds its bound");
  const markers = [], globals = new Map(), byKey = new Map(), classes = new Map(), statements = [];
  const options = { ecmaVersion: "latest", sourceType: "script", ranges: true, allowHashBang: true };
  function add(id, value, shorthand) {
    if (id?.type !== "Identifier") {
      if (id?.type === "ObjectPattern") for (const property of id.properties) {
        if (property.type === "RestElement") add(property.argument);
        else add(property.value, undefined, property.shorthand ? property.key.name : undefined);
      }
      else if (id?.type === "ArrayPattern") { for (const element of id.elements) if (element) add(element); }
      else if (id?.type === "RestElement") add(id.argument);
      else if (id?.type === "AssignmentPattern") add(id.left, undefined, shorthand);
      else throw new Error("Unsupported top-level binding pattern at " + id?.start);
      return;
    }
    if (globals.has(id.name)) throw new Error("Duplicate top-level binding: " + id.name);
    const module = ownerAt(markers, id.start), key = module + "::" + stem(id.name);
    const record = { name: id.name, module, key, definition: { start: id.start, end: id.end,
      ...(shorthand ? { shorthand } : {}) }, refs: [] };
    if (/^Function/.test(value?.type || "")) {
      record.signature = value.params.map((param) => param.type === "RestElement" ? "rest" : "arg").join(",");
    }
    globals.set(id.name, record);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(record);
    if (value?.type === "ClassExpression" || value?.type === "ClassDeclaration") {
      classes.set(id.name, { start: value.start, end: value.end, members: value.body.body.map((member) => ({
        name: member.key?.name ?? member.key?.value, kind: member.kind ?? "field",
        start: member.start, end: member.end, computed: member.computed,
      })) });
    }
  }
  scan(source, { ...options, onComment(block, text, start, end) {
    const match = !block && text.match(HEADER);
    if (match) markers.push({ module: match[1], start, end });
  } }, (node) => {
    statements.push({ start: node.start, end: node.end });
    if (node.type === "VariableDeclaration") for (const decl of node.declarations) add(decl.id, decl.init);
    else if (node.type === "FunctionDeclaration" || node.type === "ClassDeclaration") add(node.id, node);
  });
  const unresolved = [];
  scan(source, options, (node) => {
    const shorts = new Map();
    walk(node, (child) => {
      if (child.type === "Property" && child.shorthand) {
        const value = child.value.type === "AssignmentPattern" ? child.value.left : child.value;
        if (value.type === "Identifier") shorts.set(value.start, child.key.name);
      }
    });
    const program = { type: "Program", body: [node], sourceType: "script", range: [node.start, node.end] };
    const scopes = analyze(program, { ecmaVersion: 2022, sourceType: "script", optimistic: true, ignoreEval: true });
    const refs = [...scopes.globalScope.through];
    for (const variable of scopes.globalScope.variables) refs.push(...variable.references);
    const seen = new Set();
    for (const ref of refs) {
      const id = ref.identifier;
      if (seen.has(id.start)) continue;
      seen.add(id.start);
      const record = globals.get(id.name), data = { start: id.start, end: id.end,
        module: ownerAt(markers, id.start), read: ref.isRead(), write: ref.isWrite(),
        ...(shorts.has(id.start) ? { shorthand: shorts.get(id.start) } : {}) };
      if (record) record.refs.push(data);
      else unresolved.push({ ...data, name: id.name });
    }
  });
  return { source, markers, globals, byKey, classes, statements, unresolved };
}

export function counterpart(record, target) {
  const exact = target.globals.get(record.name);
  const rows = target.byKey.get(record.key) || [];
  const matched = exact?.module === record.module ? exact : rows.length === 1 ? rows[0] : undefined;
  // An extra argument can be a new permission/policy port. It needs an explicit
  // adapter; binding by name alone would silently pass arguments in wrong slots.
  return matched && matched.signature === record.signature ? matched : undefined;
}

export function applyEdits(source, edits, start = 0, end = source.length) {
  const unique = new Map();
  for (const edit of edits) {
    if (edit.start < start || edit.end > end) continue;
    const key = edit.start + ":" + edit.end;
    if (unique.has(key) && unique.get(key).text !== edit.text) throw new Error("Conflicting binding edit");
    unique.set(key, edit);
  }
  let pos = start; const chunks = [];
  for (const edit of [...unique.values()].sort((a, b) => a.start - b.start || a.end - b.end)) {
    if (edit.start < pos || edit.end < edit.start) throw new Error("Overlapping source edit");
    chunks.push(source.slice(pos, edit.start), edit.text); pos = edit.end;
  }
  chunks.push(source.slice(pos, end)); return chunks.join("");
}

export function planCompatibility(old, latest, rootModules, dependencyModules = []) {
  const roots = new Set(rootModules), modules = new Set([...roots, ...dependencyModules]), forced = new Set();
  for (const record of old.globals.values()) if (roots.has(record.module)) forced.add(record.name);
  for (;;) {
    const before = modules.size;
    for (const record of old.globals.values()) {
      if (!counterpart(record, latest) && record.refs.some((ref) => modules.has(ref.module))) modules.add(record.module);
    }
    if (modules.size === before) break;
  }
  const declared = new Map(), read = new Map();
  for (const record of old.globals.values()) {
    if (modules.has(record.module)) declared.set(record.name,
      record.name.startsWith("openbot") && !latest.globals.has(record.name) ? record.name : NAMESPACE + record.name);
    const matched = counterpart(record, latest);
    read.set(record.name, forced.has(record.name) ? declared.get(record.name) :
      matched?.name ?? declared.get(record.name));
  }
  const oldEdits = [], newEdits = [];
  const textFor = (ref, name) => ref.shorthand ? ref.shorthand + ": " + name : name;
  for (const record of old.globals.values()) {
    if (declared.has(record.name)) oldEdits.push({ ...record.definition, text: textFor(record.definition, declared.get(record.name)) });
    for (const ref of record.refs) if (modules.has(ref.module)) {
      const name = ref.write && !ref.read && declared.has(record.name) ? declared.get(record.name) : read.get(record.name);
      if (!name) throw new Error("Unresolved legacy dependency: " + record.key);
      if (name !== record.name) oldEdits.push({ ...ref, text: textFor(ref, name) });
    }
    const matched = counterpart(record, latest);
    if (forced.has(record.name) && matched) for (const ref of matched.refs) {
      if (ref.start === matched.definition.start) continue;
      if (ref.write && ref.module !== record.module) throw new Error("Cannot redirect an external upstream writer: " + record.key);
      newEdits.push({ ...ref, text: textFor(ref, declared.get(record.name)) });
    }
  }
  // These additions are independent of Temporal execution and are required by
  // the latest gateway and upgrade consumers. Keep them on the restored types.
  for (const [name, memberName] of [["AgentLifecycle", "forgetAgentDeletion"],
    ["TranscriptManager", "forgetAgentDeletion"], ["UpgradeRecreateResume", "runningTurns"]]) {
    if (!forced.has(name)) continue;
    const previous = old.classes.get(name), current = latest.classes.get(name);
    const member = current?.members.find((row) => row.name === memberName);
    if (!previous || !member || previous.members.some((row) => row.name === memberName))
      throw new Error("Reviewed local class addition changed: " + name + "." + memberName);
    const refs = [...latest.globals.values()].flatMap((record) => record.refs)
      .filter((ref) => ref.start >= member.start && ref.end <= member.end);
    if (refs.length) throw new Error("New class member acquired an unreviewed global dependency");
    oldEdits.push({ start: previous.end - 1, end: previous.end - 1,
      text: "\n  " + latest.source.slice(member.start, member.end) + "\n" });
  }
  const segments = [];
  for (let i = 0; i < old.markers.length; i++) if (modules.has(old.markers[i].module)) {
    const start = old.markers[i].start, end = old.markers[i + 1]?.start ?? old.source.length;
    segments.push(applyEdits(old.source, oldEdits, start, end));
  }
  const occupied = new Set([...latest.globals.keys()]);
  for (const name of declared.values()) if (occupied.has(name)) throw new Error("Compatibility namespace collision");
  // Upstream can remove a helper's state and its initializer call together.
  // Initializing the copied state is still necessary when an old local method
  // uses that helper. Do not run unrelated retained or latest initializers.
  const initializers = [];
  for (const initializer of old.globals.values()) {
    if (!initializer.name.startsWith("init_") || !declared.has(initializer.name)) continue;
    const statement = old.statements.find((row) => row.start <= initializer.definition.start && row.end >= initializer.definition.end);
    if (!statement || !old.source.slice(statement.start, statement.end).includes("= __esm(")) continue;
    const ownsMissingState = [...old.globals.values()].some((record) => !counterpart(record, latest) &&
      record.refs.some((ref) => ref.write && ref.start >= statement.start && ref.end <= statement.end));
    if (ownsMissingState) initializers.push(declared.get(initializer.name));
  }
  return { modules, forced, declared, read, oldEdits, newEdits, initializers, legacy: segments.join("") };
}

function ownedSource(path) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || info.size > MAX_SOURCE) throw new Error("Untrusted source");
    return readFileSync(fd, "utf8");
  } finally { closeSync(fd); }
}

function checkedReplace(source, before, after) {
  if (source.split(before).length !== 2) throw new Error("Host compatibility anchor changed: " + before.slice(0, 80));
  return source.replace(before, after);
}

function oldRange(old, plan, start, end) {
  const edits = [];
  for (const record of old.globals.values()) for (const ref of record.refs) {
    if (ref.start < start || ref.end > end) continue;
    const name = plan.read.get(record.name);
    if (!name) throw new Error("Missing host adapter dependency: " + record.key);
    if (name !== record.name) edits.push({ ...ref, text: ref.shorthand ? ref.shorthand + ": " + name : name });
  }
  return applyEdits(old.source, edits, start, end);
}

/** Restore only reviewed local interfaces on the latest shared implementations. */
export function restoreSharedClassMembers(old, latest, plan, ports) {
  const edits = [], restored = [];
  for (const [name, names] of Object.entries(ports)) {
    const previous = old.classes.get(name), current = latest.classes.get(name);
    if (!previous || !current || plan.forced.has(name)) throw new Error("Unreviewed shared class: " + name);
    const absent = previous.members.filter((member) => !current.members.some((other) =>
      member.name === other.name && member.kind === other.kind));
    if (absent.some((member) => member.computed || typeof member.name !== "string") ||
      absent.length !== names.length || absent.some((member) => !names.includes(member.name)))
      throw new Error("The removed shared interface changed: " + name);
    edits.push({start:current.end - 1, end:current.end - 1, text:absent.map((member) =>
      "\n  " + oldRange(old, plan, member.start, member.end)).join("") + "\n"});
    restored.push({class:name, members:[...names]});
  }
  return {edits, restored};
}

/** Keep an unused copied reader from being mistaken for the live gate reader. */
export function retireUnusedSharedFeatureReader(legacy) {
  const index = indexBundle(legacy), name = "__openbot_box_SandExperimentServiceCore";
  const copied = index.classes.get(name);
  if (!copied) return legacy;
  // Shared experiment methods bind to the latest class. Refuse to rename this
  // compatibility method if any copied code can still instantiate its class.
  if (index.globals.get(name)?.refs.some((ref) => ref.read))
    throw new Error("Copied experiment reader is still live");
  const member = copied.members.find((row) => row.name === "checkFeatureGate");
  if (!member || !legacy.startsWith("checkFeatureGate(", member.start))
    throw new Error("Copied experiment reader interface changed");
  const source = legacy.slice(member.start, member.end);
  const marker = "/* openbot-force-local-infer */";
  if (source.split(marker).length > 2) throw new Error("Copied feature gate patch is ambiguous");
  return applyEdits(legacy, [{...member, text:source.replace(/^checkFeatureGate\(/,
    "retainedFeatureGate(").replace(marker, "/* openbot-retained-feature-reader */")}]);
}

/** Register removed experiments before the latest registry builds its readers. */
export function restoreLocalExperimentEntries(old, latest, plan, names) {
  if (!names?.length) return [];
  if (!Array.isArray(names) || new Set(names).size !== names.length ||
      names.some((name) => typeof name !== "string")) throw new Error("Unreviewed experiment entries");
  const object = (bundle) => {
    const record = bundle.globals.get("EXPERIMENTS");
    const statement = record && bundle.statements.find((row) =>
      row.start <= record.definition.start && row.end >= record.definition.end);
    if (!statement) throw new Error("No shared experiment catalog");
    const ast = parse(bundle.source.slice(statement.start, statement.end), {ecmaVersion:"latest"});
    const entry = ast.body[0]?.declarations?.find((row) => row.id.name === "EXPERIMENTS");
    if (entry?.init?.type !== "ObjectExpression") throw new Error("Experiment catalog shape changed");
    return {node:entry.init, offset:statement.start};
  };
  const previous = object(old), current = object(latest);
  const values = names.map((name) => {
    const key = (row) => row.key?.name ?? row.key?.value;
    const matches = previous.node.properties.filter((row) => key(row) === name);
    if (matches.length !== 1 || matches[0].computed ||
        current.node.properties.some((row) => key(row) === name))
      throw new Error("Reviewed experiment catalog changed: " + name);
    return oldRange(old, plan, previous.offset + matches[0].start, previous.offset + matches[0].end);
  });
  const end = current.offset + current.node.end - 1;
  const before = latest.source.slice(current.offset + current.node.start + 1, end).trimEnd();
  return [{start:end, end, text:(before && !before.endsWith(",") ? "," : "") +
    "\n" + values.join(",\n") + "\n"}];
}

/** Preserve the latest adapter while restoring reviewed removed API exports. */
export function restoreSharedApiMembers(old, latest, plan, ports) {
  const edits = [], restored = [];
  for (const [name, names] of Object.entries(ports ?? {})) {
    if (!Array.isArray(names) || new Set(names).size !== names.length ||
        names.some((key) => typeof key !== "string") || plan.forced.has(name))
      throw new Error("Unreviewed shared API: " + name);
    const object = (bundle, record) => {
      const statement = record && bundle.statements.find((row) =>
        row.start <= record.definition.start && row.end >= record.definition.end);
      if (!statement) throw new Error("Missing shared API: " + name);
      const fn = parse(bundle.source.slice(statement.start, statement.end),
        {ecmaVersion:"latest"}).body[0];
      const returns = fn.type === "FunctionDeclaration" ?
        fn.body.body.filter((row) => row.type === "ReturnStatement") : [];
      if (returns.length !== 1 || returns[0].argument?.type !== "ObjectExpression")
        throw new Error("Shared API return shape changed: " + name);
      const node = returns[0].argument;
      if (node.properties.some((row) => row.type !== "Property" || row.computed ||
          row.kind !== "init" || typeof (row.key.name ?? row.key.value) !== "string"))
        throw new Error("Shared API exports changed: " + name);
      return {node, offset:statement.start, source:bundle.source.slice(statement.start, statement.end)};
    };
    const previous = object(old, old.globals.get(name));
    const current = object(latest, counterpart(old.globals.get(name), latest));
    const key = (row) => row.key.name ?? row.key.value;
    const absent = previous.node.properties.filter((row) =>
      !current.node.properties.some((other) => key(row) === key(other)));
    if (absent.length !== names.length || absent.some((row) => !names.includes(key(row))))
      throw new Error("Removed shared API exports changed: " + name);
    const end = current.offset + current.node.end - 1;
    const before = latest.source.slice(current.offset + current.node.start + 1, end).trimEnd();
    const members = absent.map((row) => {
      if (name === "createBackendRelaySources" && key(row) === "requestDrain") {
        // Modern relay polling no longer has the legacy notifyGate closure.
        // Wake the current serialized tick; retain its registration/ack logic.
        if (!current.source.includes("let timer = null;") ||
            !current.source.includes("async function tick() {"))
          throw new Error("Relay drain closure changed");
        return "requestDrain: () => { if (timer != null) void tick(); }";
      }
      return oldRange(old, plan, previous.offset + row.start, previous.offset + row.end);
    });
    edits.push({start:end, end, text:(before && !before.endsWith(",") ? "," : "") +
      "\n" + members.join(",\n") + "\n"});
    restored.push({function:name, exports:[...names]});
  }
  return {edits, restored};
}

/** A removed optional email extension must not prevent every local turn. */
export function adaptOptionalEmailApi(source, policy) {
  if (policy === undefined) return source;
  if (policy !== "optional-native-capability") throw new Error("Unreviewed email capability port");
  const enabled = "(" + localEmailEnabled.toString() + ")(extensions.api(\"email\"))";
  source = checkedReplace(source,
    '      isAgentEmailAllowedByTeamAdmin: () => extensions.api("email").isEnabled(),',
    '      isAgentEmailAllowedByTeamAdmin: () => ' + enabled + ',');
  return checkedReplace(source,
    '      ...extensions.api("email").isEnabled() ? { email: extensions.api("email").email } : {},',
    '      ...' + enabled + ' ? { email: extensions.api("email").email } : {},');
}

export function adaptBuiltinCatalogOptions(source, policy) {
  if (policy === undefined) return source;
  if (policy !== "optional-native-reader") throw new Error("Unreviewed builtin catalog port");
  return checkedReplace(source, 'registry3?.getExternalCatalogOptions()',
    '(' + localBuiltinCatalogOptions.toString() + ')(registry3)');
}

/** Replace only reviewed empty local readers in the latest gateway object. */
export function restoreLocalGatewayReaders(old, latest, plan, names) {
  if (!Array.isArray(names) || new Set(names).size !== names.length ||
      names.some((name) => !["getSubagents", "getAsyncTasks"].includes(name)))
    throw new Error("Unreviewed local gateway reader");
  const object = (bundle) => {
    const record = bundle.globals.get("createHostGatewayApi");
    const statement = record && bundle.statements.find((row) =>
      row.start <= record.definition.start && row.end >= record.definition.end);
    if (!statement) throw new Error("Missing gateway reader factory");
    const fn = parse(bundle.source.slice(statement.start, statement.end),
      {ecmaVersion:"latest"}).body[0];
    if (fn.type !== "FunctionDeclaration") throw new Error("Gateway reader shape changed");
    const variables = fn.body.body.filter((row) => row.type === "VariableDeclaration")
      .flatMap((row) => row.declarations).filter((row) => row.id.name === "api");
    const returns = fn.body.body.filter((row) => row.type === "ReturnStatement");
    const result = returns[0]?.argument;
    const direct = result?.type === "Identifier" && result.name === "api";
    const wrapped = result?.type === "CallExpression" &&
      result.callee.type === "Identifier" && result.callee.name === "wrapGatewayApiWithServerAgentProxy" &&
      result.arguments.length === 1 && result.arguments[0].type === "ObjectExpression" &&
      result.arguments[0].properties.some((row) => row.type === "Property" &&
        row.key.name === "api" && row.value.type === "Identifier" && row.value.name === "api");
    if (variables.length !== 1 || variables[0].init?.type !== "ObjectExpression" ||
        returns.length !== 1 || (!direct && !wrapped)) throw new Error("Gateway reader shape changed");
    return {node:variables[0].init, offset:statement.start};
  };
  const previous = object(old), current = object(latest), edits = [];
  for (const name of names) {
    const member = (entry) => {
      const rows = entry.node.properties.filter((row) => row.type === "Property" &&
        (row.key.name ?? row.key.value) === name);
      if (rows.length !== 1 || rows[0].computed || rows[0].kind !== "init")
        throw new Error("Gateway reader export changed: " + name);
      return rows[0];
    };
    const before = member(previous), after = member(current), value = after.value;
    if (value.type !== "ArrowFunctionExpression" || !value.async || value.params.length ||
        value.body.type !== "ArrayExpression" || value.body.elements.length)
      throw new Error("Gateway reader is no longer an empty stub: " + name);
    const original = before.value;
    if (original.type !== "ArrowFunctionExpression" || original.params.length !== 1 ||
        original.params[0].name !== "args" || original.body.type !== "CallExpression" ||
        original.body.callee.object?.name !== "manager" ||
        original.body.callee.property?.name !== name || original.body.arguments.length !== 1 ||
        original.body.arguments[0].object?.name !== "args" ||
        original.body.arguments[0].property?.name !== "id")
      throw new Error("Local gateway reader contract changed: " + name);
    edits.push({start:current.offset + after.start, end:current.offset + after.end,
      text:oldRange(old, plan, previous.offset + before.start, previous.offset + before.end)});
  }
  return {edits, restored:[...names]};
}

/** Restore the removed local routine and live voice dispatch boundaries. */
export function restoreLocalGatewayActions(latest, names = []) {
  if (!Array.isArray(names) || new Set(names).size !== names.length ||
      names.some((name) => !["runAgentAutomationNow", "nudgeVoiceCall"].includes(name)))
    throw new Error("Unreviewed local gateway action");
  if (!names.length) return {edits:[], restored:[]};
  const record = latest.globals.get("createHostGatewayApi");
  const statement = record && latest.statements.find((row) =>
    row.start <= record.definition.start && row.end >= record.definition.end);
  if (!statement) throw new Error("Missing gateway action factory");
  const fn = parse(latest.source.slice(statement.start, statement.end),
    {ecmaVersion:"latest"}).body[0];
  const declarations = fn.type === "FunctionDeclaration" ? fn.body.body
    .filter((row) => row.type === "VariableDeclaration").flatMap((row) => row.declarations)
    .filter((row) => row.id.name === "api") : [];
  if (declarations.length !== 1 || declarations[0].init?.type !== "ObjectExpression")
    throw new Error("Gateway action object changed");
  const edits = [];
  for (const name of names) {
  const rows = declarations[0].init.properties.filter((row) => row.type === "Property" &&
    (row.key.name ?? row.key.value) === name);
  const action = rows[0]?.value;
  if (rows.length !== 1 || rows[0].computed || action?.type !== "ArrowFunctionExpression" ||
      !action.async || action.params.length !== 1 || action.params[0].name !== "args" ||
      action.body.type !== "BlockStatement") throw new Error("Gateway action contract changed");
  if (name === "nudgeVoiceCall") {
    const branch = action.body.body[1];
    const check = branch?.test;
    const refusal = branch?.consequent?.body;
    if (branch?.type !== "IfStatement" || branch.alternate ||
        check?.type !== "UnaryExpression" || check.operator !== "!" ||
        check.argument?.type !== "CallExpression" ||
        check.argument.callee?.object?.name !== "proxy" ||
        check.argument.callee?.property?.name !== "isProxiedAgent" ||
        check.argument.arguments.length !== 1 ||
        check.argument.arguments[0]?.object?.name !== "args" ||
        check.argument.arguments[0]?.property?.name !== "id" ||
        refusal?.length !== 1 || refusal[0].type !== "ReturnStatement" ||
        latest.source.slice(statement.start + refusal[0].start,
          statement.start + refusal[0].end) !==
          'return { kind: "refused", refusal: "agent-unavailable" };')
      throw new Error("Local voice dispatch contract changed");
    edits.push({start:statement.start + refusal[0].start,
      end:statement.start + refusal[0].end,
      text:'return manager.nudgeVoiceCall({ agentId: args.id, callId: args.callId, ' +
        'request: args.request, sink: { kind: "box-loop" } });'});
    continue;
  }
  const calls = [];
  walk(action.body, (node) => {
    if (node.type === "CallExpression" && node.callee.type === "MemberExpression" &&
        !node.callee.computed && node.callee.object.name === "manager" &&
        node.callee.property.name === "runAgentAutomationNow") calls.push(node);
  });
  const call = calls[0], final = action.body.body.at(-1);
  if (calls.length !== 1 || final?.type !== "ReturnStatement" || final.argument !== call ||
      call.arguments.length !== 1 || call.arguments[0].type !== "Identifier" ||
      call.arguments[0].name !== "agentId") throw new Error("Local routine dispatch contract changed");
  // Keep the latest ID normalization, session routing and telemetry intact.
  edits.push({start:statement.start + call.arguments[0].end,
    end:statement.start + call.arguments[0].end, text:", args.automationId"});
  }
  return {edits, restored:[...names]};
}

function restoreSharedBehavior(old, latest, plan, mcpListingReader) {
  const edits = [];
  for (const [name, memberName] of [["FileMemoryStore", "constructor"],
    ["MemoryService", "createAgentStore"], ["HostMcpAuthCompletion", "resolve"]]) {
    const a = old.classes.get(name)?.members.find((m) => m.name === memberName);
    const b = latest.classes.get(name)?.members.find((m) => m.name === memberName);
    if (!a || !b) throw new Error("Shared behavior port changed: " + name + "." + memberName);
    edits.push({...b, text:oldRange(old, plan, a.start, a.end)});
  }
  const mcp = latest.classes.get("McpHostService")?.members.find((m) => m.name === "constructor");
  if (!mcp) throw new Error("Missing per-agent MCP constructor port");
  let source = latest.source.slice(mcp.start, mcp.end);
  const dependency = (name) => {
    const mapped = plan.read.get(name);
    if (!mapped) throw new Error("Missing per-agent MCP dependency: " + name);
    return mapped;
  };
  source = checkedReplace(source, "      settingsStore: deps.settings,",
    "      boxServers: async () => {\n" +
    "        const turn = this.boxServersTurn;\n" +
    "        return turn === void 0 ? {} : " + dependency("playwrightBoxMcpServersForBox") + "(deps.foreverBox.box, turn.ctx, turn.agentId, { kind: 'image_only' });\n" +
    "      },\n      settingsStore: deps.settings,");
  source = checkedReplace(source, "      mcp: this.hostMcp.mcp,",
    "      mcp: this.hostMcp.mcp,\n" +
    "      mcpForAgent: (agentId) => ({\n" +
    "        ...this.hostMcp.mcp,\n" +
    "        getTools: (ctx, mcpConfigJson) => {\n" +
    "          this.boxServersTurn = { ctx, agentId };\n" +
    "          this.hostMcp.reconcileBoxServers().catch((error) => { " + dependency("reportFallback") + "('mcp_service', error); });\n" +
    "          return this.hostMcp.mcp.getTools(ctx, mcpConfigJson);\n" +
    "        }\n      }),");
  if (mcpListingReader !== undefined) {
    if (mcpListingReader !== "live-native-catalog" ||
        source.includes("peekCursorScmBuiltinListed:"))
      throw new Error("Unreviewed native MCP listing reader");
    source = checkedReplace(source, "      mcp: this.hostMcp.mcp,",
      "      mcp: this.hostMcp.mcp,\n" +
      "      peekCursorScmBuiltinListed: (provider) => this.hostMcp.peekCursorScmBuiltinListed(provider),");
  }
  edits.push({...mcp, text:source});
  const handoff = latest.classes.get("BoxHandoffService")?.members.find((m) => m.name === "start");
  if (!handoff) throw new Error("Missing box handoff port");
  edits.push({...handoff, text:checkedReplace(latest.source.slice(handoff.start, handoff.end),
    "    this.reportBoxHelp(request5, requestId2, facts);", "    void this.captureSnapshot(request5, requestId2, facts);")});
  return edits;
}

/** Keep modern SandHost lifecycle code while restoring the removed runner ports. */
export function adaptSandHost(old, latest, plan, healthPolicy) {
  const before = old.classes.get("SandHost"), target = latest.classes.get("SandHost");
  if (!before || !target) throw new Error("No unambiguous host class");
  let source = latest.source.slice(target.start, target.end);
  const original = oldRange(old, plan, before.start, before.end);
  const absent = before.members.filter((member) => !target.members.some((other) =>
    other.name === member.name && other.kind === member.kind));
  if (absent.some((member) => member.computed || typeof member.name !== "string")) throw new Error("Unreviewed host member");
  const approved = new Set(["ctx", "runnerComposition", "kickstartIfPending", "requestDiskSaverAudit", "requireRunnerComposition"]);
  if (healthPolicy !== undefined && healthPolicy !== "optional-approval-roster")
    throw new Error("Unreviewed local health contract");
  if (healthPolicy === "optional-approval-roster") approved.add("allRunningAgentsAwaitApproval");
  if (absent.some((member) => !approved.has(member.name)) || absent.length !== approved.size)
    throw new Error("The removed host interface changed; explicit review required");
  source = source.slice(0, -1) + absent.map((member) => "\n  " + oldRange(old, plan, member.start, member.end)).join("") + "\n}";
  const compositionStart = original.indexOf("    this.runnerComposition =");
  const compositionEnd = original.indexOf("    const hostUpgrade =", compositionStart);
  if (compositionStart < 0 || compositionEnd < compositionStart) throw new Error("Runner composition boundary changed");
  source = checkedReplace(source,
    '    this.rosterBookkeeping = createHostRosterBookkeeping(hostExtensions);\n' +
    '    hostExtensions.api("local-tool-permission").bindLiveComputerCheck(\n' +
    '      (agentId) => this.hostExtensions?.api("local-exec").checkLiveComputerForAsk(agentId) ?? false\n    );\n',
    '    this.rosterBookkeeping = createHostRosterBookkeeping(hostExtensions);\n' + original.slice(compositionStart, compositionEnd));
  for (const name of ["getHealth", "allRunningAgentsAwaitApproval"]) {
    const a = before.members.find((member) => member.name === name), b = target.members.find((member) => member.name === name);
    if (!a || (!b && !(healthPolicy === "optional-approval-roster" && name === "allRunningAgentsAwaitApproval")))
      throw new Error("Missing local health contract");
    let local = oldRange(old, plan, a.start, a.end);
    if (healthPolicy === "optional-approval-roster" && name === "allRunningAgentsAwaitApproval") {
      local = checkedReplace(local, '    if (autoReview === void 0) return false;',
        '    if (autoReview === void 0 || typeof autoReview.agentIdsWithPendingApprovals !== "function") return false;');
    }
    source = checkedReplace(source, b ? latest.source.slice(b.start, b.end) : oldRange(old, plan, a.start, a.end), local);
  }
  source = checkedReplace(source, '  async dispose() {\n    this.hostExtensions?.api("managed-setup").dispose();',
    '  async dispose() {\n    if (this.transcript.isPausingForUpgrade()) this.transcript.markAllRunningAgentsForUpgradeResume();\n' +
    '    this.hostExtensions?.api("managed-setup").dispose();');
  source = checkedReplace(source, '    await this.transcript.dispose();\n    await this.hostExtensions?.stop();',
    '    await this.transcript.dispose();\n    await this.runnerComposition?.dispose();\n' +
    '    this.runnerComposition = void 0;\n    await this.hostExtensions?.stop();');
  source = checkedReplace(source, '      releaseAgentBox: (agentId) => this.releaseAgentBox(agentId),\n      handleDesktopMcpAuthCompletion:',
    '      kickstartIfPending: (agentId) => this.kickstartIfPending(agentId),\n' +
    '      requestDiskSaverAudit: (agentId) => this.requestDiskSaverAudit(agentId),\n' +
    '      releaseAgentBox: (agentId) => this.releaseAgentBox(agentId),\n      handleDesktopMcpAuthCompletion:');
  source = checkedReplace(source, '    this.transcript.subscribeOutline((payload) => this.emit({ channel: "outline", payload }));',
    '    this.transcript.subscribeOutline((payload) => this.emit({ channel: "outline", payload }));\n' +
    '    this.transcript.subscribeSubagents((payload) => this.emit({ channel: "subagents", payload }));\n' +
    '    this.transcript.subscribeAsyncTasks((payload) => this.emit({ channel: "async-tasks", payload }));');
  source = checkedReplace(source, '    const resumeOwnership = this.requireHostExtensions().api("resume-ownership");',
    '    this.transcript.setConnectorConnectCardObserver(\n' +
    '      (event) => this.mcpAuthCompletion.registerConnectCard(event)\n    );\n' +
    '    const resumeOwnership = this.requireHostExtensions().api("resume-ownership");');
  source = checkedReplace(source, 'this.hostEvents.on("session.box-handoff-ended", async ({ agentId, requestId: requestId2, resolution }) => {',
    'this.hostEvents.on("session.box-handoff-ended", async ({ agentId, requestId: requestId2, resolution, trigger: trigger2 }) => {');
  source = checkedReplace(source,
    '      await this.transcript.resolveBoxRequestEntry(agentId, requestId2, resolution, {\n' +
    '        wakeOutcomeUnseen: true\n      });\n      await this.emitForeverBox(agentId);',
    '      const entryId = await this.transcript.resolveBoxRequestEntry(agentId, requestId2, resolution, {\n' +
    '        wakeOutcomeUnseen: true\n      });\n      await this.emitForeverBox(agentId);\n' +
    '      await this.transcript.resumeAfterBoxHandoff(agentId, trigger2, { wakeOutcomeEntryIds: entryId == null ? [] : [entryId] });');
  return { start: target.start, end: target.end, text: source, restoredMembers: [...approved] };
}

const REVIEWED_SOURCES = JSON.parse(ownedSource(fileURLToPath(new URL("./latest-box-compatibility.json", import.meta.url))));
const LOCAL_ROOTS = ["SandAgentRunner", "createHostRunnerComposition", "TurnExecutionRegistry",
  "automationsExtension", "notifyBusExtension", "resumeOwnershipExtension"];

export function buildLatestLocalEngine(retained, upstream, reviewedSource) {
  const sourceSha = digest(upstream);
  if (reviewedSource ? sourceSha !== reviewedSource :
    REVIEWED_SOURCES.schemaVersion !== 1 || !REVIEWED_SOURCES.reviewedSources[sourceSha])
    throw new Error("Upstream release has not been reviewed for BOX composition");
  // The shared serde functions should inherit the latest implementation. Peel
  // this owned normalization patch before linking, then the canonical recovery
  // pass reapplies it at the latest read/write boundaries, exactly once.
  let linkSource = retained;
  if (retained.includes("openbot-transcript-text-compat-v1")) {
    const helperStart = retained.indexOf("function openbotNormalizeLegacyTextEntry(entry) {");
    const helperEnd = retained.indexOf("} /* openbot-transcript-text-compat-v1: helper */\n", helperStart);
    if (helperStart < 0 || helperEnd < helperStart || retained.split("openbot-transcript-text-compat-v1").length !== 4)
      throw new Error("Incomplete retained transcript normalization patch");
    linkSource = retained.slice(0, helperStart) + retained.slice(helperEnd + "} /* openbot-transcript-text-compat-v1: helper */\n".length);
    for (const [param, kind] of [["parsed2", "read"], ["entry", "write"]]) {
      linkSource = checkedReplace(linkSource,
        `\n  ${param} = openbotNormalizeLegacyTextEntry(${param}); /* openbot-transcript-text-compat-v1: ${kind} */`, "");
    }
  }
  const old = indexBundle(linkSource), latest = indexBundle(upstream);
  const sharedPorts = REVIEWED_SOURCES.reviewedSources[sourceSha]?.sharedClassPorts;
  const sharedApiPorts = REVIEWED_SOURCES.reviewedSources[sourceSha]?.sharedApiPorts;
  if (!sharedPorts) throw new Error("No reviewed shared interface recipe");
  const roots = new Set([...old.globals.values()].filter((record) =>
    record.module.startsWith("src/host/extensions/transcript/")).map((record) => record.module));
  const extensionPorts = REVIEWED_SOURCES.reviewedSources[sourceSha]?.retainedExtensions ?? [];
  if (!Array.isArray(extensionPorts) || new Set(extensionPorts).size !== extensionPorts.length ||
      extensionPorts.some((name) => !["inferenceExtension", "autoReviewExtension", "teamAdminPolicyExtension", "sessionExtension"].includes(name)))
    throw new Error("Unreviewed local extension port");
  for (const name of [...LOCAL_ROOTS, ...extensionPorts]) {
    const record = old.globals.get(name);
    if (!record) throw new Error("Missing retained BOX port: " + name);
    roots.add(record.module);
  }
  if (extensionPorts.includes("autoReviewExtension")) {
    // The latest service only settles old server cards. A local runner needs
    // its controller, owner/team policy and awaiting-state lifecycle together.
    // Retain that reviewed service with the matching local extension factory.
    const service = old.globals.get("AutoReviewService");
    if (!service || service.module !== "src/host/extensions/auto-review/auto-review-service.ts")
      throw new Error("Unreviewed local approval service");
    roots.add(service.module);
  }
  if (extensionPorts.includes("teamAdminPolicyExtension")) {
    const service = old.globals.get("createTeamAdminPolicyService");
    if (!service || service.module !== "src/host/extensions/team-admin-policy/team-admin-policy-service.ts")
      throw new Error("Unreviewed local team policy service");
    roots.add(service.module);
  }
  if (extensionPorts.includes("sessionExtension")) {
    // Keep the local form lifecycle paired with the retained transcript
    // reply processor: holds carry consented values, not just an opaque ID.
    const service = old.globals.get("UserFormService");
    if (!service || service.module !== "src/host/extensions/session/user-form-service.ts")
      throw new Error("Unreviewed local session form service");
    roots.add(service.module);
  }
  const dependencies = [...Object.keys(sharedPorts), ...Object.keys(sharedApiPorts ?? {})].map((name) => {
    const record = old.globals.get(name);
    if (!record) throw new Error("Missing shared compatibility dependency: " + name);
    return record.module;
  });
  const plan = planCompatibility(old, latest, roots, dependencies);
  const registry = latest.globals.get("hostExtensionRegistry");
  if (!registry) throw new Error("Latest host has no extension registry");
  const pos = latest.markers.find((marker) => marker.module === registry.module)?.start;
  if (pos === undefined) throw new Error("Latest registry has no module boundary");
  const host = adaptSandHost(old, latest, plan, REVIEWED_SOURCES.reviewedSources[sourceSha]?.localHealthContract);
  const changes = plan.newEdits.filter((edit) => edit.start < host.start || edit.end > host.end);
  if (changes.length !== plan.newEdits.length) throw new Error("Unreviewed upstream binding inside the host adapter");
  const shared = restoreSharedClassMembers(old, latest, plan, sharedPorts);
  const sharedApis = restoreSharedApiMembers(old, latest, plan, sharedApiPorts);
  const gatewayReaders = restoreLocalGatewayReaders(old, latest, plan,
    REVIEWED_SOURCES.reviewedSources[sourceSha]?.localGatewayReaders);
  const gatewayActions = restoreLocalGatewayActions(latest,
    REVIEWED_SOURCES.reviewedSources[sourceSha]?.localGatewayActions);
  changes.push(...shared.edits, ...sharedApis.edits, ...gatewayReaders.edits, ...gatewayActions.edits,
    ...restoreSharedBehavior(old, latest, plan, REVIEWED_SOURCES.reviewedSources[sourceSha]?.mcpListingReader));
  const restoredExperiments = REVIEWED_SOURCES.reviewedSources[sourceSha]?.restoredExperiments ?? [];
  changes.push(...restoreLocalExperimentEntries(old, latest, plan, restoredExperiments));
  const retainedGates = REVIEWED_SOURCES.reviewedSources[sourceSha]?.retainedRunnerGates;
  const names = latest.globals.get("SAND_RUNNER_GATE_NAMES");
  const namesStatement = names && latest.statements.find((row) => row.start <= names.definition.start && row.end >= names.definition.end);
  if (!namesStatement || !retainedGates || Object.values(retainedGates).some((value) => typeof value !== "boolean"))
    throw new Error("No reviewed retained runner gate catalog");
  changes.push({start:namesStatement.start, end:namesStatement.start,
    text:"Object.assign(SAND_RUNNER_GATE_DEFAULTS, " + JSON.stringify(retainedGates) + ");\n"});
  // The upstream refusal class stays available for auditing, but no live
  // binding points to it. Its identical method spelling would make native
  // injectors mistake two send runners for one ambiguous implementation.
  const retired = latest.classes.get("SendPipeline")?.members.find((member) => member.name === "sendPrompt");
  if (!retired) throw new Error("Missing upstream BOX refusal boundary");
  const retiredText = upstream.slice(retired.start, retired.end);
  const keyOffset = retiredText.indexOf("sendPrompt(");
  if (keyOffset < 0) throw new Error("Unsupported upstream refusal method");
  changes.push({ start: retired.start + keyOffset, end: retired.start + keyOffset + "sendPrompt".length,
    text: "retiredBoxSendPrompt" });
  changes.push(host, { start: pos, end: pos, text:
    '\n/* openbot-latest-box-compatibility-v1 */\nHostExtensions.NotifyBus = "notify-bus";\n' + retireUnusedSharedFeatureReader(plan.legacy) + "\n" +
    plan.initializers.map((name) => name + "();\n").join("") +
    "(" + installPeerDeliveryCoalescing.toString() + ")(__openbot_box_AgentToAgentMessaging);\n" });
  changes.push({ start: targetBootBoundary(latest), end: targetBootBoundary(latest), text:
    "(" + installLocalEngineStartupTrace.toString() + ")(" +
    "SandHost, require('node:fs'), '/home/box/sand-data/openbot-upstream-startup.json');\n" +
    "(" + installLocalEngineSendTrace.toString() + ")(" +
    "__openbot_box_SendPipeline, require('node:fs'), '/home/box/sand-data/openbot-upstream-send-error.json');\n" +
    "(" + installLocalEngineTurnTrace.toString() + ")(" +
    "__openbot_box_TurnRuntime, require('node:fs'), '/home/box/sand-data/openbot-upstream-turn-');\n" });
  let candidate = applyEdits(upstream, changes);
  if (sharedPorts.SandExperimentServiceCore) {
    candidate = checkedReplace(candidate,
      "      offerTaskSubagentRoster: () => service.offerTaskSubagentRoster(),",
      "      offerTaskSubagentRoster: () => service.offerTaskSubagentRoster(),\n" +
      "      offerUpdateStateSplit: () => service.offerUpdateStateSplit(),\n" +
      "      offerVoiceSkill: () => service.offerVoiceSkill(),");
  }
  const addedGates = REVIEWED_SOURCES.reviewedSources[sourceSha]?.addedRunnerGates;
  if (!addedGates || Object.values(addedGates).some((value) => typeof value !== "boolean"))
    throw new Error("No reviewed local runner gate catalog");
  candidate = checkedReplace(candidate, "  return __openbot_box_composeSandRunnerGates({",
    "  const composeLocal = (wirings) => (" + composeLocalRunnerGates.toString() + ")(" +
    "wirings, SAND_RUNNER_GATE_DEFAULTS, __openbot_box_composeSandRunnerGates, " + JSON.stringify(addedGates) + ");\n" +
    "  return composeLocal({");
  const contextReaders = REVIEWED_SOURCES.reviewedSources[sourceSha]?.requestContextReaders;
  if (contextReaders !== undefined) {
    if (contextReaders !== "optional-native-capabilities") throw new Error("Unreviewed native request context readers");
    candidate = checkedReplace(candidate,
      "      requestContext: __openbot_box_createHostRequestContext({\n" +
      "        transcriptsFolder: transcriptsDir,\n" +
      "        shell: environment.shell,\n" +
      "        resolveUserTimeZone: () => settings.getUserTimeZone(),\n" +
      "        resolveRules: () => extensions.api(\"managed-setup\").resolveTeamRules(),\n" +
      "        resolveUserFullName: () => auth2.getUserFullName()\n" +
      "      }),",
      "      requestContext: __openbot_box_createHostRequestContext((" + localRequestContextOptions.toString() + ")({\n" +
      "        transcriptsFolder: transcriptsDir,\n" +
      "        shell: environment.shell,\n" +
      "        resolveUserTimeZone: () => settings.getUserTimeZone()\n" +
      "      }, auth2, extensions.api(\"managed-setup\"))),");
  }
  candidate = checkedReplace(candidate,
    "      createSandTaskTool({\n        props,\n        remoteBoxResourceAccessor,",
    "      createSandTaskTool({\n        turn: (" + localTaskArguments.toString() +
    ")({config:config3,toolSession,summarizationHandler}, turn).turn,\n        props,\n        remoteBoxResourceAccessor,");
  candidate = checkedReplace(candidate,
    "      await manager.sendPrompt(args.prompt, {\n" +
    "        agentId: args.agentId,\n        directAddressedAcceptance: args.directAddressedAcceptance,\n" +
    "        attachmentPaths: args.attachmentPaths ?? []\n      });",
    "      await manager.sendPrompt(args.prompt, (" + localSendOptions.toString() + ")(args, deps.environment));");
  candidate = checkedReplace(candidate, "  notificationsExtension,", "  __openbot_box_notifyBusExtension,\n  notificationsExtension,");
  candidate = adaptOptionalEmailApi(candidate, REVIEWED_SOURCES.reviewedSources[sourceSha]?.emailCapability);
  candidate = adaptBuiltinCatalogOptions(candidate, REVIEWED_SOURCES.reviewedSources[sourceSha]?.builtinCatalogOptions);
  scan(candidate, { ecmaVersion: "latest", sourceType: "script", allowHashBang: true }, () => {});
  const retainedModules = [...plan.modules].sort();
  return { source: candidate, report: { schemaVersion: 1, upstreamSha256: digest(upstream),
    retainedSha256: digest(retained), candidateSha256: digest(candidate), strategy: "latest-host-with-box-ports",
    retainedModules, restoredHostMembers: host.restoredMembers, restoredSharedInterfaces:shared.restored,
    restoredExperiments,
    restoredExtensions: extensionPorts,
    restoredSharedApis: sharedApis.restored,
    restoredGatewayReaders: gatewayReaders.restored,
    restoredGatewayActions: gatewayActions.restored,
    addedRunnerGates: Object.keys(addedGates),
    retainedRunnerGates: Object.keys(retainedGates),
    requestContextReaders: contextReaders ?? "required-legacy-readers",
    inheritedBindings: [...old.globals.values()].filter((record) => record.refs.some((ref) => plan.modules.has(ref.module)) &&
      !plan.forced.has(record.name) && counterpart(record, latest)).map((record) => ({
        module: record.module, retainedSymbol: record.name, upstreamSymbol: counterpart(record, latest).name })),
    forcedBindings: plan.forced.size, upstreamModules: new Set(latest.markers.map((row) => row.module)).size,
    nativeDeliveryVerified: false, featureParity: "requires-native-validation" } };
}

function targetBootBoundary(bundle) {
  const anchor = "bootSandHost(runHostMain).catch(";
  const at = bundle.source.indexOf(anchor);
  if (at < 0 || bundle.source.indexOf(anchor, at + anchor.length) >= 0)
    throw new Error("Ambiguous native boot boundary");
  return at;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [retainedPath, upstreamPath, output] = process.argv.slice(2);
  if (!retainedPath || !upstreamPath || !output) throw new Error("Usage: latest-local-engine.mjs retained.cjs upstream.cjs output-directory");
  const result = buildLatestLocalEngine(ownedSource(retainedPath), ownedSource(upstreamPath));
  mkdirSync(output, { mode: 0o700 });
  writeFileSync(resolve(output, "candidate.cjs"), result.source, { mode: 0o600, flag: "wx" });
  writeFileSync(resolve(output, "compatibility.json"), JSON.stringify(result.report, null, 2) + "\n", { mode: 0o600, flag: "wx" });
  process.stdout.write(JSON.stringify({ sourceSha256: result.report.candidateSha256,
    retainedModules: result.report.retainedModules.length, nativeDeliveryVerified: false }) + "\n");
}
