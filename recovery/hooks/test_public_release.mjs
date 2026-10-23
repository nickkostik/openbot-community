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

test('public audit rejects dotted Token Plan credentials without echoing values', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'release-audit-')); t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  const secret = ['sk-sp-H', 'synthetic', 'x'.repeat(80)].join('.');
  fs.writeFileSync(path.join(root, 'source.py'), 'VALUE=' + JSON.stringify(secret));
  const report = auditTree(root);
  assert.equal(report.ok, false);
  assert.ok(report.findings.some(x => x.kind === 'token-plan-credential'));
  assert.equal(JSON.stringify(report).includes(secret), false);
});
test('public audit scans credentials in allowed binary assets without echoing values', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'release-audit-')); t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  const secret = ['sk-sp-H', 'synthetic', 'x'.repeat(80)].join('.');
  for (const ext of ['png', 'jpg', 'jpeg', 'ico', 'woff', 'woff2']) {
    fs.writeFileSync(path.join(root, 'asset.' + ext), Buffer.concat([
      Buffer.from([0x89, 0, 0xff]), Buffer.from('metadata=' + secret), Buffer.from([0]),
    ]));
  }
  const report = auditTree(root);
  assert.equal(report.ok, false);
  assert.equal(report.findings.filter(x => x.kind === 'token-plan-credential').length, 6);
  assert.equal(JSON.stringify(report).includes(secret), false);
});
test('public audit applies private denylist to binary metadata', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'release-audit-')); t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  const privateValue = 'private-installation.example';
  fs.writeFileSync(path.join(root, 'asset.png'), Buffer.concat([Buffer.from([0]), Buffer.from(privateValue)]));
  const report = auditTree(root, { deny: [privateValue] });
  assert.equal(report.ok, false);
  assert.ok(report.findings.some(x => x.kind === 'private-denylist'));
  assert.equal(JSON.stringify(report).includes(privateValue), false);
});
test('public audit keeps permitted product assets while refusing unreviewed binary source', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'release-audit-')); t.after(() => fs.rmSync(root,{recursive:true,force:true}));
  fs.writeFileSync(path.join(root, 'asset.png'), Buffer.from([0x89, 0, 0xff]));
  assert.equal(auditTree(root).ok, true);
  fs.writeFileSync(path.join(root, 'source.ts'), Buffer.from([0]));
  assert.deepEqual(auditTree(root).findings, [{ file: 'source.ts', kind: 'unreviewed-binary' }]);
});
test('desktop export includes reviewed repair tools and excludes account evidence', async () => {
  const { included } = await import('../../scripts/export-shareable.mjs');
  assert.equal(included('recovery/desktop-sidebar-authority/patch-sidebar-authority.py'), true);
  assert.equal(included('recovery/desktop-sidebar-authority/test_linux_0753.py'), true);
  assert.equal(included('recovery/desktop-sidebar-authority/evidence/client-verification.json'), false);
  assert.equal(included('recovery/desktop-sidebar-authority/private-runtime.json'), false);
  assert.equal(included('recovery/desktop-sidebar-authority/systemd/private-tunnel.service'), false);
});
