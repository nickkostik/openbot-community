#!/usr/bin/env node
/** Backport vendor instruction data without transplanting executable code. */
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, lstatSync, mkdirSync, openSync, closeSync, fstatSync, constants } from "node:fs";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(resolve(process.env.OPENBOT_PACKAGE_ROOT ||
  fileURLToPath(new URL("../../", import.meta.url)), "package.json"));
const { parse, Parser, tokTypes } = require("acorn");
const MAX_SOURCE = 48 * 1024 * 1024;
const sha = (value) => createHash("sha256").update(value).digest("hex");
const stem = (name) => name.replace(/\d+$/, "");
const header = /^\s*((?:src\/|\.\.\/|node_modules\/)[^\s]+\.(?:[cm]?[jt]sx?|json|md|txt))\s*$/;
const instructionName = /(?:PROMPT|DESCRIPTION|GUIDANCE|INSTRUCTION|REMINDER|RULE|POLICY|POINTER|HINT|EXPLANATION|BOUNDARY)/;
const instructionModule = /(?:grok-bot-harness\/src\/(?:runner|tasks)\/|voice-call-harness\/dist\/prompt\/|src\/shared\/mcp\/mcp-custom-instructions\.)/;

function children(node) {
  const result = [];
  for (const [key, value] of Object.entries(node)) {
    if (["start", "end", "loc", "range"].includes(key)) continue;
    if (value?.type) result.push(value);
    else if (Array.isArray(value)) for (const item of value) if (item?.type) result.push(item);
  }
  return result;
}

function canonical(node) {
  if (Array.isArray(node)) return node.map(canonical);
  if (node && typeof node === "object") {
    const result = {};
    for (const key of Object.keys(node).sort()) {
      if (["start", "end", "loc", "range", "raw"].includes(key)) continue;
      result[key] = canonical(node[key]);
    }
    return result;
  }
  return node;
}

// A vendor bundle contains hundreds of thousands of syntax nodes. Visit and
// discard each top-level statement instead of retaining two complete trees.
// This parser extension is tested against the exact acorn version in the lock.
function scanProgram(source, options, visit = () => {}) {
  const StreamingParser = Parser.extend((Base) => class extends Base {
    parseTopLevel(node) {
      node.body = []; node.sourceType = this.options.sourceType;
      const exports = Object.create(null);
      while (this.type !== tokTypes.eof) visit(this.parseStatement(null, true, exports));
      this.next();
      return this.finishNode(node, "Program");
    }
  });
  return StreamingParser.parse(source, options);
}

function potentialData(node, depth = 0) {
  if (depth > 24) return false;
  switch (node.type) {
    case "Literal": case "Identifier": return true;
    case "TemplateLiteral": return node.expressions.every((n) => potentialData(n, depth + 1));
    case "MemberExpression": return potentialData(node.object, depth + 1) &&
      (!node.computed || potentialData(node.property, depth + 1));
    case "ArrayExpression": return node.elements.every((n) => n && potentialData(n, depth + 1));
    case "ObjectExpression": return node.properties.every((n) => n.type === "Property" && !n.method &&
      n.kind === "init" && potentialData(n.value, depth + 1));
    case "NewExpression": return node.callee.type === "Identifier" && ["Map", "Set"].includes(node.callee.name) &&
      node.arguments.every((n) => potentialData(n, depth + 1));
    default: return false;
  }
}

function index(source) {
  if (Buffer.byteLength(source) > MAX_SOURCE) throw new Error("Source exceeds its bound");
  const markers = [];
  const options = { ecmaVersion: "latest", sourceType: "script", allowHashBang: true,
    onComment(block, text, start, end) {
      if (!block) {
        const match = text.match(header);
        if (match) markers.push({ module: match[1], start, end });
      }
    } };
  function owner(position) {
    let left = 0, right = markers.length - 1, found = "<entry>";
    while (left <= right) {
      const middle = (left + right) >> 1;
      if (markers[middle].start <= position) { found = markers[middle].module; left = middle + 1; }
      else right = middle - 1;
    }
    return found;
  }
  const globals = new Map(), definitions = new Map(), byKey = new Map(), codeHashes = new Map();
  const addGlobal = (id) => {
    if (id?.type !== "Identifier") return;
    const module = owner(id.start), key = module + "::" + stem(id.name);
    const record = { name: id.name, module, key };
    globals.set(id.name, record);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(record);
  };
  const set = (name, value) => {
    if (!globals.has(name)) return;
    if (!definitions.has(name)) definitions.set(name, []);
    const record = globals.get(name);
    const relevant = instructionName.test(name) && instructionModule.test(record.module);
    if (!relevant && !potentialData(value)) return;
    // Relevant computed instructions need their complete tree to classify why
    // they cannot be inherited. A node-shaped stub caused TypeErrors for
    // computed templates, objects and regexes, obscuring actual compatibility.
    definitions.get(name).push(value);
  };
  // Esbuild puts some declarations in __esm initializers. Conservatively reject
  // an assignment whenever any enclosing function declares the same name.
  function locals(fn) {
    const names = new Set();
    function pattern(node) {
      if (!node) return;
      if (node.type === "Identifier") names.add(node.name);
      else for (const item of children(node)) pattern(item);
    }
    for (const param of fn.params) pattern(param);
    if (fn.id) pattern(fn.id);
    function scan(node) {
      if (node !== fn.body && /^(?:Function|ArrowFunction)/.test(node.type)) return;
      if (node.type === "VariableDeclarator") pattern(node.id);
      if (node.type === "CatchClause") pattern(node.param);
      for (const item of children(node)) scan(item);
    }
    scan(fn.body);
    return names;
  }
  function collect(node, shadows = new Set()) {
    if (["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"].includes(node.type)) {
      shadows = new Set([...shadows, ...locals(node)]);
    }
    if (node.type === "VariableDeclarator" && node.id.type === "Identifier" && node.init &&
        globals.has(node.id.name) && !shadows.has(node.id.name)) set(node.id.name, node.init);
    if (node.type === "AssignmentExpression" && node.operator === "=" && node.left.type === "Identifier" &&
        !shadows.has(node.left.name)) set(node.left.name, node.right);
    for (const item of children(node)) collect(item, shadows);
  }
  function codeShape(node, shadows = new Set()) {
    if (Array.isArray(node)) return node.map((item) => codeShape(item, shadows));
    if (!node || typeof node !== "object") return node;
    if (["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"].includes(node.type))
      shadows = new Set([...shadows, ...locals(node)]);
    const assigned = node.type === "VariableDeclarator" ? node.id :
      node.type === "AssignmentExpression" && node.operator === "=" ? node.left : undefined;
    const valueKey = node.type === "VariableDeclarator" ? "init" : "right";
    const record = assigned?.type === "Identifier" && globals.get(assigned.name);
    const mask = record && !shadows.has(assigned.name) && instructionName.test(assigned.name) &&
      instructionModule.test(record.module) && node[valueKey] && potentialData(node[valueKey]);
    const shape = {};
    for (const key of Object.keys(node).sort()) {
      if (["start", "end", "loc", "range", "raw"].includes(key)) continue;
      const global = node.type === "Identifier" && globals.get(node.name);
      const normalized = key === "name" && global && !shadows.has(node.name) && byKey.get(global.key)?.length === 1;
      shape[key] = mask && key === valueKey ? "<instruction-data>" : normalized ? global.key : codeShape(node[key], shadows);
    }
    return shape;
  }
  scanProgram(source, options, (node) => {
    if (node.type === "VariableDeclaration") for (const decl of node.declarations) addGlobal(decl.id);
    else if (["FunctionDeclaration", "ClassDeclaration"].includes(node.type)) addGlobal(node.id);
    collect(node);
    const module = owner(node.start);
    if (instructionModule.test(module)) {
      if (!codeHashes.has(module)) codeHashes.set(module, createHash("sha256"));
      codeHashes.get(module).update(JSON.stringify(codeShape(node)));
    }
  });
  const modules = new Map();
  for (let i = 0; i < markers.length; i++) {
    const item = markers[i], end = markers[i + 1]?.start ?? source.length;
    modules.set(item.module, (modules.get(item.module) || "") + source.slice(item.start, end));
  }
  return { source, globals, definitions, byKey, modules,
    moduleCode: new Map([...codeHashes].map(([key, hash]) => [key, hash.digest("hex")])) };
}

function counterpart(record, target) {
  const exact = target.globals.get(record.name);
  if (exact?.module === record.module) return exact;
  const rows = target.byKey.get(record.key) || [];
  return rows.length === 1 ? rows[0] : undefined;
}

function definition(name, target) {
  const entries = target.definitions.get(name) || [];
  return entries.length === 1 ? entries[0] : undefined;
}

function translate(expression, vendor, target) {
  const edits = [], references = new Set();
  function staticBinding(node, seen = new Set()) {
    if (!node) return false;
    if (node.type === "Identifier") {
      if (["undefined", "NaN", "Infinity"].includes(node.name)) return true;
      if (seen.has(node.name)) return false;
      return staticBinding(definition(node.name, target), new Set([...seen, node.name]));
    }
    if (node.type === "MemberExpression") {
      if (node.object.type !== "Identifier" || node.optional || (node.computed && node.property.type !== "Literal")) return false;
      const object = definition(node.object.name, target), key = node.computed ? node.property.value : node.property.name;
      const field = object?.type === "ObjectExpression" && object.properties.find((p) =>
        p.type === "Property" && !p.computed && !p.method && p.kind === "init" && (p.key.name ?? p.key.value) === key);
      return field && staticBinding(field.value, new Set([...seen, node.object.name]));
    }
    if (node.type === "Literal") return !node.regex && !node.bigint;
    if (node.type === "TemplateLiteral") return node.expressions.every((p) => staticBinding(p, seen));
    if (node.type === "ArrayExpression") return node.elements.every((p) => p && staticBinding(p, seen));
    if (node.type === "ObjectExpression") return node.properties.every((p) => p.type === "Property" &&
      !p.computed && !p.method && p.kind === "init" && staticBinding(p.value, seen));
    return false;
  }
  function ref(node) {
    if (["undefined", "NaN", "Infinity"].includes(node.name)) return;
    const record = vendor.globals.get(node.name), matched = record && counterpart(record, target);
    if (!matched) throw new Error("missing-binding");
    const existing = definition(matched.name, target);
    if (!existing || !staticBinding(existing)) throw new Error("non-static-binding");
    references.add(matched.name);
    if (matched.name !== node.name) edits.push({ start: node.start, end: node.end, text: matched.name });
  }
  function member(node) {
    if (node.object.type === "Identifier") {
      const record = vendor.globals.get(node.object.name), matched = record && counterpart(record, target);
      if (!matched) throw new Error("missing-binding");
      const value = definition(matched.name, target);
      const key = node.computed ? node.property.value : node.property.name;
      // A new tool name in a prompt must not advertise a tool this engine lacks.
      if (!value || value.type !== "ObjectExpression" ||
          !value.properties.some((p) => p.type === "Property" && !p.computed &&
            (p.key.name ?? p.key.value) === key)) throw new Error("missing-member");
      ref(node.object);
    } else throw new Error("unsupported-member");
  }
  function pure(node) {
    switch (node.type) {
      case "Literal":
        if (node.regex || node.bigint) throw new Error("non-instruction-literal");
        return;
      case "Identifier": return ref(node);
      case "TemplateLiteral": for (const part of node.expressions) pure(part); return;
      case "MemberExpression":
        if (node.optional || (node.computed && node.property.type !== "Literal")) throw new Error("dynamic-member");
        return member(node);
      case "ArrayExpression":
        for (const part of node.elements) { if (!part) throw new Error("sparse-array"); pure(part); } return;
      case "ObjectExpression":
        for (const part of node.properties) {
          if (part.type !== "Property" || part.computed || part.method || part.kind !== "init" || part.shorthand)
            throw new Error("dynamic-object");
          pure(part.value);
        } return;
      case "NewExpression":
        if (node.callee.type !== "Identifier" || !["Map", "Set"].includes(node.callee.name) || node.arguments.length !== 1 ||
            node.arguments[0].type !== "ArrayExpression") throw new Error("executable-instruction");
        pure(node.arguments[0]); return;
      default: throw new Error("executable-instruction");
    }
  }
  pure(expression);
  let result = vendor.source.slice(expression.start, expression.end);
  for (const edit of edits.sort((a, b) => b.start - a.start)) {
    result = result.slice(0, edit.start - expression.start) + edit.text + result.slice(edit.end - expression.start);
  }
  return { source: result, references: [...references] };
}

/** Never rewrite user profiles, reminders in requests, or unsupported code. */
export function backportInstructions(original, upstream) {
  const old = index(original), vendor = index(upstream), applied = [], pending = [], matched = [], edits = [];
  const possible = new Map();
  for (const record of vendor.globals.values()) {
    if (!instructionModule.test(record.module) || !instructionName.test(record.name)) continue;
    const next = definition(record.name, vendor);
    if (!next) { pending.push({ module: record.module, symbol: record.name, reason: "ambiguous-initializer" }); continue; }
    const current = counterpart(record, old), previous = current && definition(current.name, old);
    if (!previous) { pending.push({ module: record.module, symbol: record.name, reason: "new-instruction-or-feature" }); continue; }
    try {
      const translated = translate(next, vendor, old);
      const parsed = parse("(" + translated.source + ")", { ecmaVersion: "latest" }).body[0].expression;
      const equal = JSON.stringify(canonical(parsed)) === JSON.stringify(canonical(previous));
      if (equal) { matched.push(record.name); continue; }
      // A description can be syntactically pure while describing new arguments
      // or different tool behavior. Require the module's executable syntax to
      // match, rather than promoting text based on a familiar tool name alone.
      // Connector policy Maps are data selected by the retained local selector.
      const connectorMap = record.module === "src/shared/mcp/mcp-custom-instructions.ts" &&
        next.type === "NewExpression" && next.callee?.name === "Map";
      if (!connectorMap && old.moduleCode.get(record.module) !== vendor.moduleCode.get(record.module))
        throw new Error("module-code-changed");
      possible.set(current.name, { record, previous, translated, parsed });
    } catch (error) { pending.push({ module: record.module, symbol: record.name, reason: error.message }); }
  }
  // Do not backport new instructions that depend on another instruction which
  // could not be updated. Otherwise text can silently mix incompatible APIs.
  let removed;
  do {
    removed = false;
    for (const [name, candidate] of possible) {
      const failed = candidate.translated.references.some((reference) => {
        const oldRef = old.globals.get(reference), vendorRef = oldRef && counterpart(oldRef, vendor);
        if (!vendorRef || possible.has(reference)) return false;
        return pending.some((item) => item.module === vendorRef.module && item.symbol === vendorRef.name);
      });
      if (failed) { possible.delete(name); removed = true;
        pending.push({ module: candidate.record.module, symbol: candidate.record.name, reason: "pending-dependency" }); }
    }
  } while (removed);
  for (const [name, { record, previous, translated }] of possible) {
    edits.push({ start: previous.start, end: previous.end, text: translated.source });
    applied.push({ module: record.module, symbol: name,
      previousSha256: sha(original.slice(previous.start, previous.end)), newSha256: sha(translated.source) });
  }
  let source = original;
  for (const edit of edits.sort((a, b) => b.start - a.start)) source = source.slice(0, edit.start) + edit.text + source.slice(edit.end);
  scanProgram(source, { ecmaVersion: "latest", sourceType: "script", allowHashBang: true });
  const sections = [];
  for (const name of new Set([...old.modules.keys(), ...vendor.modules.keys()])) {
    const before = old.modules.get(name), after = vendor.modules.get(name);
    if (before !== after) sections.push({ module: name,
      kind: before === undefined ? "added" : after === undefined ? "removed" : "changed",
      previousSha256: before === undefined ? null : sha(before), upstreamSha256: after === undefined ? null : sha(after) });
  }
  return { source, report: { applied, pending, unchangedInstructions: matched.length,
    upstreamSections: sections, upstreamSha256: sha(upstream), originalSha256: sha(original), sourceSha256: sha(source),
    executableCodeTransplanted: false, featureParity: "partial" } };
}

function ownedSource(path) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || info.size > MAX_SOURCE) throw new Error("Untrusted source file");
    return readFileSync(fd, "utf8");
  } finally { closeSync(fd); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [oldPath, vendorPath, output] = process.argv.slice(2);
  if (!oldPath || !vendorPath || !output) throw new Error("Expected original, vendor and private output directory");
  const directory = resolve(output);
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const info = lstatSync(directory);
  if (!info.isDirectory() || info.uid !== process.getuid()) throw new Error("Untrusted output directory");
  const result = backportInstructions(ownedSource(oldPath), ownedSource(vendorPath));
  writeFileSync(resolve(directory, "candidate.cjs"), result.source, { mode: 0o600, flag: "wx" });
  writeFileSync(resolve(directory, "instructions.json"), JSON.stringify(result.report, null, 2) + "\n", { mode: 0o600, flag: "wx" });
  process.stdout.write(JSON.stringify({ applied: result.report.applied.length, pending: result.report.pending.length,
    upstreamSections: result.report.upstreamSections.length, sourceSha256: result.report.sourceSha256 }) + "\n");
}
