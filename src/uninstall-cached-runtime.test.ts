import assert from 'node:assert/strict';
import { execFileSync, spawnSync, type SpawnSyncReturns } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const script = path.resolve('uninstall.sh');

function fixture(t: { skip: (reason: string) => void; after: (fn: () => void) => void }, invalidBackup = false) {
  if (process.platform === 'win32') { t.skip('POSIX process behavior is unavailable'); return undefined; }
  const utility = (name: string) => execFileSync('which', [name], { encoding: 'utf8' }).trim();
  let bash: string;
  try { bash = utility('bash'); utility('python3'); } catch { t.skip('bash and Python 3 are required'); return undefined; }
  const root = mkdtempSync(path.join(os.tmpdir(), 'openbot-uninstall-cached-node-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const data = path.join(root, 'sand-data'), bin = path.join(root, 'bin'), cachedBin = path.join(data, 'node22/bin');
  mkdirSync(bin); mkdirSync(cachedBin, { recursive: true }); mkdirSync(path.join(data, 'openbot'));
  for (const name of ['bash', 'python3', 'realpath', 'mkdir', 'dirname', 'date', 'tr', 'sed', 'rm', 'mv', 'sort', 'awk', 'sleep', 'ps', 'setsid']) {
    symlinkSync(utility(name), path.join(bin, name));
  }
  // Port inspection is isolated from the development machine's real services.
  for (const name of ['ss', 'lsof']) { const file = path.join(bin, name); writeFileSync(file, '#!/bin/sh\nexit 1\n'); chmodSync(file, 0o755); }
  symlinkSync(process.execPath, path.join(cachedBin, 'node'));
  const host = path.join(root, 'host-main.cjs');
  writeFileSync(host, 'wrapped host\n');
  writeFileSync(path.join(data, 'host-main.cjs.pre-openbot'), invalidBackup ? '' : 'stock host\n');
  writeFileSync(path.join(data, 'secrets.json'), '{"key":"retained-fixture-value"}\n');
  const env = { ...process.env, PATH: cachedBin + path.delimiter + bin, OPENBOT_SAND_DATA: data, OPENBOT_HOST_MAIN: host };
  return { bash, data, host, bin, env, result: path.join(data, 'openbot-uninstall-result.json') };
}

for (const invalidBackup of [false, true]) {
  test('uninstall publishes and reads ' + (invalidBackup ? 'failed' : 'successful') + ' completion after removing its only Node runtime', t => {
    const f = fixture(t, invalidBackup); if (!f) return;
    const run = spawnSync(f.bash, [script, '--bot-mode-worker'], { env: f.env, encoding: 'utf8', timeout: 10000 });
    assert.equal(run.status, invalidBackup ? 1 : 0, run.stderr);
    assert.equal(existsSync(path.join(f.data, 'node22')), false);
    assert.equal(existsSync(path.join(f.data, 'openbot')), false);
    assert.equal(readFileSync(f.host, 'utf8'), invalidBackup ? 'wrapped host\n' : 'stock host\n');
    assert.equal(readFileSync(path.join(f.data, 'secrets.json'), 'utf8'), '{"key":"retained-fixture-value"}\n');
    const receipt = JSON.parse(readFileSync(f.result, 'utf8'));
    assert.equal(receipt.status, invalidBackup ? 'failed' : 'success');
    assert.equal(typeof receipt.finishedAt, 'string');
    const status: SpawnSyncReturns<string> = spawnSync(f.bash, [script, '--bot-status'], { env: f.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(status.status, 0, status.stderr);
    assert.match(status.stdout, new RegExp('OPENBOT_STATUS=' + receipt.status));
    if (invalidBackup) assert.match(status.stdout, /OPENBOT_ERROR=The host backup failed validation/);
  });
}

test('a bot uninstall without Python refuses before changing the host or removing the runtime', t => {
  const f = fixture(t); if (!f) return;
  rmSync(path.join(f.bin, 'python3'));
  const run = spawnSync(f.bash, [script, '--bot-mode-worker'], { env: f.env, encoding: 'utf8', timeout: 5000 });
  assert.equal(run.status, 2);
  assert.match(run.stderr, /Python 3 is required.*no uninstall was started/);
  assert.equal(readFileSync(f.host, 'utf8'), 'wrapped host\n');
  assert.equal(existsSync(path.join(f.data, 'host-main.cjs.pre-openbot')), true);
  assert.equal(existsSync(path.join(f.data, 'openbot')), true);
  assert.equal(existsSync(path.join(f.data, 'node22')), true);
  assert.equal(existsSync(f.result), false);
});

test('status reads report malformed receipts and preserve multiline errors in the bot protocol', t => {
  const f = fixture(t); if (!f) return;
  rmSync(path.join(f.data, 'node22'), { recursive: true });
  for (const value of ['[]', '{bad json', '{"status":"failed","error":"First line\\nSecond line"}']) {
    writeFileSync(f.result, value);
    const status: SpawnSyncReturns<string> = spawnSync(f.bash, [script, '--bot-status'], { env: f.env, encoding: 'utf8', timeout: 5000 });
    assert.equal(status.status, 0, status.stderr);
    assert.match(status.stdout, /OPENBOT_STATUS=failed/);
    assert.match(status.stdout, /===== END OPENBOT BOT RESULT =====/);
    if (value.startsWith('{"status"')) assert.equal(status.stdout.includes('OPENBOT_ERROR=First line\\nSecond line\n'), true);
    else assert.match(status.stdout, /OPENBOT_ERROR=Invalid result JSON/);
    assert.equal(readFileSync(f.result, 'utf8'), value);
  }
});
