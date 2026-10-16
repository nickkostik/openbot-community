import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { backportInstructions } from "./upstream-instructions.mjs";

const MODULE = "// ../packages/grok-bot-harness/src/runner/system-prompt.ts\n";
const TOOLS = "// src/shared/agents/grok-bot-tool-names.ts\nvar GROK_BOT_TOOL_NAMES = { Read: 'Read', Shell: 'Shell' };\n";
const sample = (body) => TOOLS + MODULE + body;

test("new vendor instructions become actual runtime values and are idempotent", () => {
  const old = sample("var SAND_PROMPT = `Before reading, use ${GROK_BOT_TOOL_NAMES.Read}.`;\n") +
    "// src/host/runner/turn-runtime.ts\nfunction runAgentTurn(){ return 'KEEP BOX'; }\n";
  const upstream = sample("var SAND_PROMPT = `Before reading, page large files with ${GROK_BOT_TOOL_NAMES.Read}.`;\n") +
    "// src/host/runner/turn-runtime.ts\nfunction runAgentTurn(){throw new Error('retired');}\n";
  const result = backportInstructions(old, upstream), context = {};
  vm.runInNewContext(result.source, context);
  assert.equal(context.SAND_PROMPT, "Before reading, page large files with Read.");
  assert.equal(context.runAgentTurn(), "KEEP BOX");
  assert.equal(result.report.applied.length, 1);
  assert.equal(result.report.executableCodeTransplanted, false);
  assert.equal(backportInstructions(result.source, upstream).source, result.source);
});

test("compiler-renumbered global references are translated by source module", () => {
  const old = sample("var SYSTEM_HINT2 = 'original';\nvar SAND_PROMPT = `Read: ${SYSTEM_HINT2}`;\n");
  const upstream = sample("var SYSTEM_HINT3 = 'updated';\nvar SAND_PROMPT = `Files: ${SYSTEM_HINT3}`;\n");
  const result = backportInstructions(old, upstream), context = {};
  vm.runInNewContext(result.source, context);
  assert.equal(context.SAND_PROMPT, "Files: updated");
  assert.doesNotMatch(result.source, /SYSTEM_HINT3/);
});

test("an absent tool keeps its new instruction pending instead of advertising support", () => {
  const old = sample("var SECURE_GUIDANCE = 'Ask the user';\n");
  const upstream = TOOLS.replace("Shell: 'Shell'", "Shell: 'Shell', BrowserSecureFill: 'BrowserSecureFill'") + MODULE +
    "var SECURE_GUIDANCE = `Fill with ${GROK_BOT_TOOL_NAMES.BrowserSecureFill}`;\n";
  const result = backportInstructions(old, upstream);
  assert.equal(result.source, old);
  assert.equal(result.report.pending[0].reason, "missing-member");
});

test("vendor executable code is never run or transplanted as an instruction", () => {
  const old = sample("var SAND_PROMPT = 'safe';\nvar MODEL_URL = 'direct';\n");
  const upstream = sample("var SAND_PROMPT = process.env.AUTH_TOKEN;\nvar MODEL_URL = 'foreign';\n");
  const result = backportInstructions(old, upstream);
  assert.equal(result.source, old);
  assert.ok(result.report.pending.length);
  const call = backportInstructions(old, sample("var SAND_PROMPT = require('fs').readFileSync('/secret');\n"));
  assert.equal(call.source, old);
  assert.equal(call.report.pending[0].reason, "executable-instruction");
});

test("esbuild initializer assignments are inherited without running the initializer", () => {
  const old = sample("var SAND_PROMPT; var init_prompt = () => { SAND_PROMPT = 'old'; }; init_prompt();\n");
  const upstream = sample("var SAND_PROMPT; var init_prompt = () => { SAND_PROMPT = 'new'; }; init_prompt();\n");
  const result = backportInstructions(old, upstream), context = {};
  vm.runInNewContext(result.source, context);
  assert.equal(context.SAND_PROMPT, "new");
});

test("local shadow assignments cannot masquerade as the global prompt", () => {
  const old = sample("var SAND_PROMPT = 'original';\nfunction other(){var SAND_PROMPT; SAND_PROMPT = 'private';}\n");
  const result = backportInstructions(old, sample("var SAND_PROMPT = 'updated';\nfunction other(){var SAND_PROMPT; SAND_PROMPT = 'private';}\n"));
  const context = {}; vm.runInNewContext(result.source, context);
  assert.equal(context.SAND_PROMPT, "updated");
  assert.match(result.source, /SAND_PROMPT = 'private'/);
});

test("new features and all changed source modules remain in the coverage report", () => {
  const old = sample("var SAND_PROMPT = 'old';\n");
  const upstream = sample("var SAND_PROMPT = 'new';\nvar NEW_TOOL_DESCRIPTION = 'Requires a new tool';\n") +
    "// src/host/runner/new-feature.ts\nfunction newFeature(){}\n";
  const result = backportInstructions(old, upstream);
  assert.ok(result.report.pending.some((p) => p.symbol === "NEW_TOOL_DESCRIPTION"));
  assert.ok(result.report.upstreamSections.some((p) => p.module.endsWith("new-feature.ts") && p.kind === "added"));
  assert.equal(result.report.featureParity, "partial");
  assert.doesNotMatch(result.source, /function newFeature/);
});

test("static connector instruction Maps are updated while custom selection stays local", () => {
  const prefix = "// src/shared/mcp/mcp-custom-instructions.ts\n";
  const old = prefix + "var MCP_CUSTOM_INSTRUCTIONS = new Map([['finance','verify']]);\nfunction select(){return 'BOX';}\n";
  const upstream = prefix + "var MCP_CUSTOM_INSTRUCTIONS = new Map([['finance','verify'],['shopify','ask which store']]);\n";
  const result = backportInstructions(old, upstream), context = {};
  vm.runInNewContext(result.source, context);
  assert.equal(context.MCP_CUSTOM_INSTRUCTIONS.get("shopify"), "ask which store");
  assert.equal(context.select(), "BOX");
});

test("same-value formatting changes do not churn prompt prefixes", () => {
  const old = sample('var SAND_PROMPT = "stable";\n');
  const result = backportInstructions(old, sample("var SAND_PROMPT='stable';\n"));
  assert.equal(result.source, old);
  assert.equal(result.report.applied.length, 0);
});

test("multiple initializers and ambiguous compiler bindings fail closed", () => {
  const old = sample("var SAND_PROMPT = 'one'; SAND_PROMPT = 'two';\n");
  assert.equal(backportInstructions(old, sample("var SAND_PROMPT = 'new';\n")).source, old);
  const duplicate = sample("var SYSTEM_HINT1 = 'a', SYSTEM_HINT2 = 'b'; var SAND_PROMPT = 'old';\n");
  const vendor = sample("var SYSTEM_HINT3 = 'c'; var SAND_PROMPT = `new ${SYSTEM_HINT3}`;\n");
  assert.equal(backportInstructions(duplicate, vendor).source, duplicate);
});

test("descriptions of new behavior stay pending even when the tool name exists", () => {
  const old = sample("var WAIT_DESCRIPTION = 'wait a fixed delay';\nfunction wait(){return 'fixed';}\n");
  const vendor = sample("var WAIT_DESCRIPTION = 'return when the page becomes quiet';\nfunction wait(){return 'quiet';}\n");
  const result = backportInstructions(old, vendor);
  assert.equal(result.source, old);
  assert.equal(result.report.pending.find((p) => p.symbol === "WAIT_DESCRIPTION").reason, "module-code-changed");
});

test("strict program validation rejects malformed executable syntax", () => {
  assert.throws(() => backportInstructions('"use strict"; with (x) {}', sample("var SAND_PROMPT='new';")), SyntaxError);
  assert.throws(() => backportInstructions(sample("var SAND_PROMPT='old';"), 'const x = ;'), SyntaxError);
});

test("a prompt cannot interpolate a dynamic global value", () => {
  const prefix = "// src/shared/private-runtime.ts\nvar AUTH_SECRET = process.env.AUTH_TOKEN;\n";
  const old = prefix + sample("var SAND_PROMPT='old';\n");
  const vendor = prefix + sample("var SAND_PROMPT=`new ${AUTH_SECRET}`;\n");
  const result = backportInstructions(old, vendor);
  assert.equal(result.source, old);
  assert.equal(result.report.pending.find((p) => p.symbol === "SAND_PROMPT").reason, "non-static-binding");
});

test("computed templates, objects and patterns report compatibility reasons without parser crashes", () => {
  const variants = [
    "var SAND_PROMPT = `new ${render()}`;",
    "var SAND_PROMPT = {text: render()};",
    "var SAND_PROMPT = /new/;",
    "var SAND_PROMPT = [...parts];",
  ];
  for (const declaration of variants) {
    const result = backportInstructions(sample("var SAND_PROMPT='old';"), sample(declaration));
    assert.equal(result.source, sample("var SAND_PROMPT='old';"));
    assert.ok(result.report.pending.length);
    assert.doesNotMatch(result.report.pending[0].reason, /undefined|iterable|Cannot read/);
  }
});

test("an instruction cannot interpolate an executable member initializer", () => {
  const prefix = "// src/shared/values.ts\nvar VALUES = { text: render() };\n";
  const old = prefix + sample("var SAND_PROMPT='old';");
  const vendor = prefix + sample("var SAND_PROMPT=`new ${VALUES.text}`;");
  const result = backportInstructions(old, vendor);
  assert.equal(result.source, old);
  assert.ok(result.report.pending.length);
  assert.doesNotMatch(result.report.pending[0].reason, /Cannot read|iterable/);
});
