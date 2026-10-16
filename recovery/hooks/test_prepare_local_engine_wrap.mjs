import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, symlinkSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import test from 'node:test';

const helper = resolve('recovery/hooks/prepare-local-engine-wrap.mjs');
const source = 'function createProtoSessionProvider() { return "stock"; }\n';

test('fresh-engine wrapping uses the canonical payload fingerprint and installed runtime', t => {
  const folder = mkdtempSync(join(tmpdir(), 'openbot-prepare-wrap-'));
  t.after(() => rmSync(folder, { recursive: true, force: true }));
  const stock = join(folder, 'stock.cjs'), output = join(folder, 'wrapped.cjs');
  writeFileSync(stock, source);
  const result = spawnSync(process.execPath, ['--experimental-strip-types', helper, process.cwd(), stock, output],
    { encoding: 'utf8', timeout: 10000 });
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.runtimePath, resolve('payload/runtime.cjs'));
  assert.match(report.payloadFingerprint, /^[0-9a-f]{16}$/);
  const wrapped = readFileSync(output, 'utf8');
  assert.ok(wrapped.startsWith('/* openbot-stock-wrap */\n/* openbot-payload ' + report.payloadFingerprint + ' */\n'));
  assert.ok(wrapped.includes('function createProtoSessionProvider_stock()'));
  // Repeating cannot overwrite a prepared immutable pair.
  const again = spawnSync(process.execPath, ['--experimental-strip-types', helper, process.cwd(), stock, output],
    { encoding: 'utf8', timeout: 10000 });
  assert.notEqual(again.status, 0);
  assert.equal(readFileSync(output, 'utf8'), wrapped);
});

test('fresh-engine wrapping refuses ambiguous factories and symlinked inputs', t => {
  const folder = mkdtempSync(join(tmpdir(), 'openbot-prepare-refusal-'));
  t.after(() => rmSync(folder, { recursive: true, force: true }));
  const real = join(folder, 'real.cjs');
  writeFileSync(real, source);
  for (const kind of ['ambiguous', 'symlink']) {
    const stock = join(folder, kind + '.cjs'), output = join(folder, kind + '-wrapped.cjs');
    if (kind === 'symlink') symlinkSync(real, stock);
    else writeFileSync(stock, source + source);
    const result = spawnSync(process.execPath, ['--experimental-strip-types', helper, process.cwd(), stock, output],
      { encoding: 'utf8', timeout: 10000 });
    assert.notEqual(result.status, 0);
    assert.throws(() => readFileSync(output), { code: 'ENOENT' });
  }
});
