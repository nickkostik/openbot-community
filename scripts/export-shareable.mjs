#!/usr/bin/env node
// Export reviewed product source from one Git commit, without fleet state/history.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { auditTree } from './public-audit.mjs';

const rootFiles = new Set([
  'AGENTS.md', 'DESIGN.md', 'LICENSE', 'README.md', 'README.zh-CN.md',
  'SECURITY.md', 'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md', '.gitignore',
  'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts',
  'install.sh', 'uninstall.sh', 'quickstart.sh',
]);
const roots = ['src/', 'web/', 'ui/', 'payload/', 'skills/', '.github/', 'scripts/', 'docs/', 'agent-box/', 'recovery/hooks/'];
const publicDocs = new Set(['agent-install.md', 'models-and-tools.md', 'portable-installation.md',
  'portable-recovery.md', 'public-release.md', 'troubleshooting.md', 'updating.md', 'same-id-live-restoration.md',
  'shareable-readme.md', 'shareable-readme.zh-CN.md']);
const publicDocuments = new Map([
  ['README.md', 'docs/shareable-readme.md'],
  ['README.zh-CN.md', 'docs/shareable-readme.zh-CN.md'],
  ['skills/openbot-config/SKILL.md', 'docs/shareable-config/SKILL.md'],
  ['skills/openbot-config/reference.md', 'docs/shareable-config/reference.md'],
  ['docs/updating.md', 'docs/updating.md'],
]);
export function included(file) {
  if (!(rootFiles.has(file) || roots.some(root => file.startsWith(root)))) return false;
  if (file.startsWith('docs/') && !publicDocs.has(file.slice(5)) && !file.startsWith('docs/shareable-config/')) return false;
  if (['update-fleet-instructions.py', 'build-acp-opencode-direct.py', 'patch-acp-fleet.py',
    'test_build_acp_opencode_direct.py', 'test_patch_acp_fleet.py'].some(name => file === 'recovery/hooks/' + name)) return false;
  const parts = file.split('/');
  return !parts.some(part => ['.git', '.agents', '.codex', '.ssh', '.cloudflared', '.tailscale', 'node_modules', 'secrets', 'sand-data', 'recovery-snapshots', 'openbot-local-engine', '__pycache__'].includes(part)
    || /^\.env(?:\.|$)/.test(part) || /(?:\.(?:key|pem|gpg|db)|\.db-[^/]+|\.pyc|\.log)$/.test(part)
    || /^(?:auth|gateway|.*secrets.*)\.json$/.test(part)
    || /^(?:host-main\.cjs(?:\..*)?|stock\.cjs|wrapped\.cjs)$/.test(part))
    && file !== 'payload/version.json';
}

export function exportSource({ source, destination, repository, revision = 'HEAD' }) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) || repository.includes('..')) throw Error('Expected owner/repository.');
  if (!/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(revision) || revision.includes('..')) throw Error('Expected a Git commit or ref.');
  const run = args => execFileSync('git', ['-C', source, ...args], { maxBuffer: 32 * 1024 * 1024 });
  const commit = run(['rev-parse', '--verify', revision + '^{commit}']).toString().trim();
  const entries = run(['ls-tree', '-r', '-z', commit]).toString().split('\0').filter(Boolean).map(row => {
    const [metadata, file] = row.split('\t');
    const [mode, type, hash] = metadata.split(' ');
    return { mode, type, hash, file };
  }).filter(entry => included(entry.file));
  if (!entries.some(entry => entry.file === 'install.sh') || !entries.some(entry => entry.file === 'package.json')) throw Error('Commit does not contain the OpenBot product.');
  if (entries.some(entry => !['100644', '100755'].includes(entry.mode) || entry.type !== 'blob')) throw Error('Product export contains a symlink or unsupported Git entry.');
  for (const template of publicDocuments.values()) {
    if (!entries.some(entry => entry.file === template)) throw Error(`Missing public documentation template: ${template}`);
  }
  // A fresh destination makes accidental inclusion of an old private export impossible.
  fs.mkdirSync(destination, { mode: 0o700 });
  try {
    for (const entry of entries) {
      const target = path.join(destination, entry.file);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, run(['cat-file', 'blob', entry.hash]), { mode: entry.mode === '100755' ? 0o755 : 0o644, flag: 'wx' });
    }
    const packageFile = path.join(destination, 'package.json');
    const pkg = JSON.parse(fs.readFileSync(packageFile, 'utf8'));
    const previousRepository = typeof pkg.repository === 'string' ? pkg.repository : pkg.repository?.url;
    pkg.repository = { type: 'git', url: `git+https://github.com/${repository}.git` };
    pkg.bugs = { url: `https://github.com/${repository}/issues` };
    pkg.homepage = `https://github.com/${repository}#readme`;
    fs.writeFileSync(packageFile, JSON.stringify(pkg, null, 2) + '\n');
    const installer = path.join(destination, 'install.sh');
    const text = fs.readFileSync(installer, 'utf8');
    if ((text.match(/^DEFAULT_REPOSITORY=/gm) || []).length !== 1) throw Error('Installer has no unique source default.');
    const sourceRepository = /^(?:git\+)?https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?)(?:\.git)?$/.exec(previousRepository || '')?.[1]
      || /^git@github\.com:([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?)(?:\.git)?$/.exec(previousRepository || '')?.[1]
      || /^DEFAULT_REPOSITORY="([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)"$/m.exec(text)?.[1];
    if (!sourceRepository) throw Error('Cannot establish the committed source repository.');
    fs.writeFileSync(installer, text.replace(/^DEFAULT_REPOSITORY=.*$/m, `DEFAULT_REPOSITORY="${repository}"`));
    // Public instructions are product contracts, never copied incident journals.
    for (const [file, template] of publicDocuments) {
      const target = path.join(destination, file);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, fs.readFileSync(path.join(destination, template), 'utf8')
        .replaceAll('YOUR_ACCOUNT/YOUR_REPOSITORY', repository));
    }
    // Recovery's old incident journal belongs to the private recovery repository.
    const guide = path.join(destination, 'docs', 'portable-recovery.md');
    if (fs.existsSync(guide)) {
      fs.mkdirSync(path.join(destination, 'recovery'), { recursive: true });
      fs.copyFileSync(guide, path.join(destination, 'recovery', 'README.md'));
    }
    const documents = entries.filter(entry => entry.file.endsWith('.md')).map(entry => entry.file);
    for (const file of [...documents, 'recovery/README.md', 'src/grok-skills.ts']) {
      const target = path.join(destination, file);
      if (fs.existsSync(target)) fs.writeFileSync(target, fs.readFileSync(target, 'utf8').replaceAll(sourceRepository, repository));
    }
    // Fixtures must use reserved synthetic identities, never captured account IDs.
    const replacements = new Map();
    for (const entry of entries.filter(entry => /(?:\.test\.[tm]s|test_[^/]+\.(?:py|mjs))$/.test(entry.file)
      || entry.file === 'recovery/hooks/standalone-proxy-check.py')) {
      const target = path.join(destination, entry.file);
      const text = fs.readFileSync(target, 'utf8').replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, value => {
        if (/^00000000-0000-4000-8000-/.test(value)) return value;
        if (!replacements.has(value)) replacements.set(value, '00000000-0000-4000-8000-' +
          createHash('sha256').update(value).digest('hex').slice(0, 12));
        return replacements.get(value);
      });
      fs.writeFileSync(target, text);
    }
    const privacy = auditTree(destination);
    if (!privacy.ok) throw Error('Public export refused: ' + JSON.stringify(privacy.findings));
    const files = {};
    function inventory(directory) {
      for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        const full = path.join(directory, entry.name);
        if (entry.isDirectory()) inventory(full);
        else {
          const bytes = fs.readFileSync(full);
          files[path.relative(destination, full).split(path.sep).join('/')] = { bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') };
        }
      }
    }
    inventory(destination);
    const manifest = { schemaVersion: 1, repository, sourceCommit: commit, sourceFileCount: entries.length,
      publicDocumentation: true, privacyAudit: { passed: true, files: privacy.files }, files,
      excluded: ['Git history', 'fleet configs', 'evidence', 'encrypted backups', 'credentials', 'runtime logs/transcripts', 'vendor host bundles'] };
    fs.writeFileSync(path.join(destination, '.openbot-export.json'), JSON.stringify(manifest, null, 2) + '\n', { mode: 0o644, flag: 'wx' });
    return manifest;
  } catch (error) {
    fs.rmSync(destination, { recursive: true, force: true });
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2), options = {};
    for (let i = 0; i < args.length; i += 2) {
      if (!['--source', '--destination', '--repository', '--revision'].includes(args[i]) || !args[i + 1]) throw Error('Usage: export-shareable.mjs --destination NEW_DIRECTORY --repository OWNER/REPO [--source CHECKOUT] [--revision COMMIT]');
      options[args[i].slice(2)] = args[i + 1];
    }
    if (!options.destination || !options.repository) throw Error('A fresh destination and repository are required.');
    options.source ||= path.resolve(fileURLToPath(new URL('..', import.meta.url)));
    console.log(JSON.stringify(exportSource(options)));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
