import assert from "node:assert/strict";
import fs from "node:fs";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { checkNativeDependencies } from "./native-dependencies.ts";

test("native preflight resolves direct eager imports against native host path without executing them", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-native-imports-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const host = path.join(root, "host-main.cjs");
  fs.mkdirSync(path.join(root, "node_modules", "fixture-native-dependency"), { recursive: true });
  fs.writeFileSync(path.join(root, "node_modules", "fixture-native-dependency", "index.js"), "throw Error('must not load');");
  const source = "const native = require('fixture-native-dependency'); const fs = require('node:fs'); throw Error('must not boot');";
  assert.deepEqual(checkNativeDependencies(source, host), { ok: true });
  const failed = checkNativeDependencies("var native = __toESM(require('fixture-missing-native'));", host);
  assert.equal(failed.ok, false);
  if (!failed.ok) assert.match(failed.reason, /fixture-missing-native/);
});

test("deferred and optional vendor imports are not falsely reported as eager boot dependencies", () => {
  const source = "function deferred() { require('missing-deferred'); } const later = () => require('missing-later'); try { require('missing-optional'); } catch {}";
  assert.deepEqual(checkNativeDependencies(source, "/tmp/host-main.cjs"), { ok: true });
});

test("dependency parser errors refuse instead of running an unparsed vendor", () => {
  assert.equal(checkNativeDependencies("invalid JavaScript {", "/tmp/host-main.cjs").ok, false);
});


test("portable CJS-only Acorn distribution loads even when its advertised ESM file is absent", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-cjs-acorn-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ type: "module" }));
  const module = path.join(root, "native-dependencies.ts");
  fs.copyFileSync(new URL("./native-dependencies.ts", import.meta.url), module);
  const acorn = path.join(root, "node_modules", "acorn");
  fs.mkdirSync(path.join(acorn, "dist"), { recursive: true });
  const require = createRequire(import.meta.url);
  // Match the Computer's portable dependency layout: real CJS parser bytes,
  // package exports still advertise an ESM artifact that was not packaged.
  fs.copyFileSync(require.resolve("acorn"), path.join(acorn, "dist", "acorn.js"));
  fs.writeFileSync(path.join(acorn, "package.json"), JSON.stringify({
    name: "acorn", main: "dist/acorn.js",
    exports: { ".": { import: "./dist/acorn.mjs", require: "./dist/acorn.js" } },
  }));
  assert.equal(fs.existsSync(path.join(acorn, "dist", "acorn.mjs")), false);
  const script = `import { checkNativeDependencies } from ${JSON.stringify(pathToFileURL(module).href)};
console.log(JSON.stringify(checkNativeDependencies("const fs = require('node:fs');", ${JSON.stringify(path.join(root, "host-main.cjs"))})));`;
  const result = spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", script], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout.trim()), { ok: true });
  fs.rmSync(acorn, { recursive: true });
  const missing = spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", script], { encoding: "utf8" });
  assert.equal(missing.status, 0, missing.stderr);
  const refused = JSON.parse(missing.stdout.trim()) as { ok: boolean; reason: string };
  assert.equal(refused.ok, false);
  assert.match(refused.reason, /Native dependency preflight failed/);
  assert.match(refused.reason, /acorn/);
});
