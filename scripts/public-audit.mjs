#!/usr/bin/env node
// Fail closed on runtime artifacts and identity/credential material in a release.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const checks = [
  ['private-key', /-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----/],
  ['credential', /\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{40,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b/],
  ['saved-login', /\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b/],
  ['non-synthetic-identity', /\b(?!00000000-0000-4000-8000-)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i],
  ['personal-home', /\/home\/(?!box(?:\/|\b)|runner(?:\/|\b)|user(?:\/|\b)|example(?:\/|\b)|test(?:\/|\b))[A-Za-z][\w.-]+\//],
  ['installation-path', /\/(?:drive|root)\//],
];
const forbidden = /(?:^|\/)(?:\.env(?:\.[^/]+)?|sand-data|agents|agent-transcripts|recovery-snapshots|evidence|secrets|\.ssh|\.tailscale|\.cloudflared)(?:\/|$)|\.(?:pem|key|db|gpg|log|jsonl)(?:$|[-.])/i;

export function auditTree(root, { deny = [] } = {}) {
  const findings = []; let files = 0;
  function walk(dir) {
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      if (['.git', 'node_modules', 'dist', '__pycache__'].includes(item.name)) continue;
      const full = path.join(dir, item.name), name = path.relative(root, full).split(path.sep).join('/');
      if (item.isSymbolicLink()) { findings.push({ file: name, kind: 'symlink' }); continue; }
      if (forbidden.test(name)) { findings.push({ file: name, kind: 'runtime-artifact' }); continue; }
      if (item.isDirectory()) { walk(full); continue; }
      if (!item.isFile()) { findings.push({ file: name, kind: 'special-file' }); continue; }
      files++;
      const bytes = fs.readFileSync(full);
      if (bytes.includes(0)) {
        // Only reviewed product image/font assets are allowed as binary files.
        if (!/\.(?:png|jpg|jpeg|ico|woff2?)$/i.test(name)) findings.push({ file: name, kind: 'unreviewed-binary' });
        continue;
      }
      const text = bytes.toString('utf8');
      for (const [kind, pattern] of checks) if (pattern.test(text)) findings.push({ file: name, kind });
      if (deny.some(value => value && text.toLowerCase().includes(value.toLowerCase()))) findings.push({ file: name, kind: 'private-denylist' });
    }
  }
  walk(root);
  return { schemaVersion: 1, ok: findings.length === 0, files, findings };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(process.argv[2] || '.');
  const flag = process.argv.indexOf('--deny-file');
  const deny = flag < 0 ? [] : JSON.parse(fs.readFileSync(process.argv[flag + 1], 'utf8'));
  if (!Array.isArray(deny) || deny.some(value => typeof value !== 'string')) throw Error('Expected a private JSON array of denied strings.');
  const report = auditTree(root, { deny }); console.log(JSON.stringify(report, null, 2));
  if (!report.ok) process.exitCode = 1;
}
