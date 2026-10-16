import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { auditTree } from '../../scripts/public-audit.mjs';

test('public audit refuses a credential hidden in otherwise permitted source without echoing it', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'release-audit-')); t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  const secret = 'ghp_' + 'a'.repeat(36);
  fs.writeFileSync(path.join(root,'source.ts'), 'const value="' + secret + '";');
  const report = auditTree(root); assert.equal(report.ok,false); assert.equal(report.findings[0].kind,'credential');
  assert.equal(JSON.stringify(report).includes(secret),false);
});
test('public audit rejects runtime state, symlinks and private denylist values', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'release-audit-')); t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  fs.writeFileSync(path.join(root,'source.ts'),'installation-specific.example');
  fs.mkdirSync(path.join(root,'sand-data')); fs.symlinkSync('/tmp',path.join(root,'linked'));
  assert.deepEqual(new Set(auditTree(root,{deny:['installation-specific.example']}).findings.map(x=>x.kind)),
    new Set(['private-denylist','runtime-artifact','symlink']));
});
test('public audit permits synthetic fixture identity and generic product source', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'release-audit-')); t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  fs.writeFileSync(path.join(root,'fixture.py'),'ID="00000000-0000-4000-8000-000000000001"');
  assert.equal(auditTree(root).ok,true);
});
