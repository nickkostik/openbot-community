import assert from 'node:assert/strict';
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { skipOnWindows } from './test-platform.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const installer = path.join(root, 'install.sh');
const oldCommit = 'a'.repeat(40), newCommit = 'b'.repeat(40);
type Run = { status: number | null; stdout: string; stderr: string };
function run(env: NodeJS.ProcessEnv, args: string[] = []): Promise<Run> {
  return new Promise((resolve, reject) => {
    const child = spawn('bash', [installer, ...args], { env: { ...process.env, ...env }, timeout: 20_000 });
    let stdout = '', stderr = '';
    child.stdout.on('data', c => { stdout += c; });
    child.stderr.on('data', c => { stderr += c; });
    child.once('error', reject);
    child.once('close', status => resolve({ status, stdout, stderr }));
  });
}
function fixture(t: { after: (fn: () => void) => void }) {
  const data = mkdtempSync(path.join(os.tmpdir(), 'openbot-portable-'));
  const src = path.join(data, 'checkout');
  mkdirSync(path.join(src, 'src'), { recursive: true });
  mkdirSync(path.join(src, 'payload'));
  writeFileSync(path.join(src, 'package.json'), JSON.stringify({ repository: { url: 'git+https://github.com/example/own-fork.git' } }));
  writeFileSync(path.join(src, 'src/cli.ts'), 'console.log("fixture activation");\n');
  writeFileSync(path.join(src, 'payload/runtime.cjs'), 'module.exports = {};\n');
  writeFileSync(path.join(src, 'payload/hop-server.cjs'), 'module.exports = {};\n');
  const host = path.join(data, 'host-main.cjs');
  writeFileSync(host, 'function createProtoSessionProvider() {}\n');
  t.after(() => rmSync(data, { recursive: true, force: true }));
  return { data, src, dest: path.join(data, 'openbot'), env: {
    OPENBOT_HOST_MAIN: host, OPENBOT_SAND_DATA: data, OPENBOT_SRC: src,
    OPENBOT_TUNNEL: 'off', OPENBOT_SKIP_NPM_INSTALL: '1', OPENBOT_COMMIT: newCommit,
    OPENBOT_BOT_RESULT: path.join(data, 'result.json'),
  } };
}
function saved(f: ReturnType<typeof fixture>) {
  const value = { schemaVersion: 1, repository: 'example/previous-fork', ref: 'stable', sourceType: 'github', commit: oldCommit };
  writeFileSync(path.join(f.data, 'openbot-install-source.json'), JSON.stringify(value));
  return value;
}
function makeArchive(f: ReturnType<typeof fixture>, directory = 'example-own-fork-cafed00d') {
  const base = path.join(f.data, 'archive-source'), release = path.join(base, directory);
  mkdirSync(release, { recursive: true });
  writeFileSync(path.join(release, 'package.json'), '{}\n');
  const tar = path.join(f.data, 'source.tar.gz');
  execFileSync('tar', ['-czf', tar, '-C', base, directory]);
  return tar;
}
function stubs(f: ReturnType<typeof fixture>, ghBody: string, curlBody: string) {
  const bin = path.join(f.data, 'bin'); mkdirSync(bin);
  for (const [name, body] of [['gh', ghBody], ['curl', curlBody]] as const) {
    const file = path.join(bin, name);
    writeFileSync(file, '#!/usr/bin/env node\n' + body); chmodSync(file, 0o755);
  }
  return { PATH: `${bin}:${process.env.PATH}`, STUB_LOG: path.join(f.data, 'fetches.jsonl') };
}

test('a copied fork is installed without private state and can update from its own installed directory', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t);
  mkdirSync(path.join(f.src, 'recovery/config'), { recursive: true });
  mkdirSync(path.join(f.src, 'recovery/hooks'));
  mkdirSync(path.join(f.src, '.git'));
  mkdirSync(path.join(f.src, 'node_modules'));
  writeFileSync(path.join(f.src, '.env'), 'PRIVATE_TOKEN=do-not-copy');
  writeFileSync(path.join(f.src, 'payload/auth.json'), '{"access_token":"do-not-copy"}');
  writeFileSync(path.join(f.src, 'recovery/config/fleet.json'), '{"private":"fleet"}');
  writeFileSync(path.join(f.src, 'recovery/hooks/helper.py'), 'print("portable helper")\n');
  const plan = '{"private":"existing model settings"}\n';
  writeFileSync(path.join(f.data, 'openbot-plan.json'), plan);
  const first = await run(f.env);
  assert.equal(first.status, 0, first.stderr);
  const source = JSON.parse(readFileSync(path.join(f.data, 'openbot-install-source.json'), 'utf8'));
  assert.equal(source.repository, 'example/own-fork');
  assert.equal(source.sourceType, 'local');
  for (const file of ['.git', 'node_modules', '.env', 'payload/auth.json', 'recovery/config']) assert.equal(existsSync(path.join(f.dest, file)), false, file);
  assert.equal(existsSync(path.join(f.dest, 'recovery/hooks/helper.py')), true);
  // This was destructive before staging: DEST was deleted before it was read.
  const second = await run({ ...f.env, OPENBOT_SRC: f.dest, OPENBOT_COMMIT: oldCommit });
  assert.equal(second.status, 0, second.stderr);
  assert.equal(JSON.parse(readFileSync(path.join(f.dest, 'payload/version.json'), 'utf8')).commit, oldCommit);
  assert.equal(JSON.parse(readFileSync(path.join(f.data, 'openbot-previous/payload/version.json'), 'utf8')).commit, newCommit);
  assert.equal(readFileSync(path.join(f.data, 'openbot-plan.json'), 'utf8'), plan);
});

test('copied source inside another repository does not inherit that repository\'s version', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t);
  const git = (args: string[]) => execFileSync('git', ['-C', f.data, ...args], { encoding: 'utf8' });
  git(['init', '-q']);
  git(['add', 'checkout']);
  git(['-c', 'user.name=Installer Test', '-c', 'user.email=installer@example.test', 'commit', '-qm', 'Parent checkout']);
  const parentCommit = git(['rev-parse', 'HEAD']).trim();
  const result = await run({ ...f.env, OPENBOT_COMMIT: '' });
  assert.equal(result.status, 0, result.stderr);
  const stamp = JSON.parse(readFileSync(path.join(f.dest, 'payload/version.json'), 'utf8'));
  assert.equal(stamp.commit, 'unknown');
  assert.notEqual(stamp.commit, parentCommit);
});

test('invalid staged source and activation refusal retain the old code, source metadata and settings', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), metadata = saved(f);
  mkdirSync(path.join(f.dest, 'payload'), { recursive: true });
  writeFileSync(path.join(f.dest, 'payload/version.json'), JSON.stringify({ commit: oldCommit }));
  writeFileSync(path.join(f.dest, 'keep.txt'), 'old-release');
  const settings = '{"bot":"selected effort"}\n';
  writeFileSync(path.join(f.data, 'openbot-bot-models.json'), settings);
  for (const body of ['invalid JavaScript !!!', 'throw new Error("activation refused");']) {
    writeFileSync(path.join(f.src, 'src/cli.ts'), body);
    const failed = await run(f.env);
    assert.notEqual(failed.status, 0);
    assert.equal(readFileSync(path.join(f.dest, 'keep.txt'), 'utf8'), 'old-release');
    assert.deepEqual(JSON.parse(readFileSync(path.join(f.data, 'openbot-install-source.json'), 'utf8')), metadata);
    assert.equal(readFileSync(path.join(f.data, 'openbot-bot-models.json'), 'utf8'), settings);
  }
});

test('failed downloads reuse the actual installed version without relabeling it as the requested commit', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), metadata = saved(f);
  mkdirSync(path.join(f.dest, 'payload'), { recursive: true });
  writeFileSync(path.join(f.dest, 'package.json'), '{}\n');
  const stamp = JSON.stringify({ commit: oldCommit });
  writeFileSync(path.join(f.dest, 'payload/version.json'), stamp);
  const mock = stubs(f, 'process.exit(1);', 'process.stdout.write("403");');
  const result = await run({ ...f.env, ...mock, OPENBOT_SRC: '', OPENBOT_COMMIT: newCommit,
    OPENBOT_TARBALL: 'http://127.0.0.1/not-available', OPENBOT_ARCHIVE_TARBALL: 'http://127.0.0.1/fallback', OPENBOT_TEST_DOWNLOAD_ONLY: '1' }, ['--bot-mode-worker']);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(readFileSync(path.join(f.dest, 'payload/version.json'), 'utf8'), stamp);
  assert.deepEqual(JSON.parse(readFileSync(path.join(f.data, 'openbot-install-source.json'), 'utf8')), metadata);
  const receipt = JSON.parse(readFileSync(path.join(f.data, 'result.json'), 'utf8'));
  assert.equal(receipt.commit, oldCommit);
  assert.equal(receipt.downloadSource, 'existing-install');
});

test('saved fork downloads and public fallback URLs all target the same exact commit', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t); saved(f);
  const archive = makeArchive(f);
  const mock = stubs(f,
    String.raw`const fs=require('fs');fs.appendFileSync(process.env.STUB_LOG,JSON.stringify(process.argv.slice(2))+'\n');if(process.argv.some(x=>x.includes('/commits/')))process.stdout.write('${newCommit}');else process.exit(1);`,
    String.raw`const fs=require('fs');const a=process.argv.slice(2);fs.appendFileSync(process.env.STUB_LOG,JSON.stringify(a)+'\n');if(a.some(x=>x.includes('codeload.github.com')))process.stdout.write('403');else{fs.copyFileSync(process.env.STUB_ARCHIVE,a[a.indexOf('-o')+1]);process.stdout.write('200');}`);
  const result = await run({ ...f.env, ...mock, STUB_ARCHIVE: archive, OPENBOT_SRC: '', OPENBOT_COMMIT: '', OPENBOT_TEST_DOWNLOAD_ONLY: '1' }, ['--bot-mode-worker']);
  assert.equal(result.status, 0, result.stderr);
  const lines: string[][] = readFileSync(mock.STUB_LOG, 'utf8').trim().split('\n').map(s => JSON.parse(s));
  assert.equal(lines[0]?.includes('repos/example/previous-fork/commits/stable'), true);
  assert.equal(lines.some(a => a.includes('repos/example/previous-fork/tarball/' + newCommit)), true);
  assert.equal(lines.some(a => a.includes('https://codeload.github.com/example/previous-fork/tar.gz/' + newCommit)), true);
  assert.equal(lines.some(a => a.includes('https://github.com/example/previous-fork/archive/' + newCommit + '.tar.gz')), true);
  assert.equal(lines.flat().some(s => s.includes('aaravarr')), false);
  assert.equal(JSON.parse(readFileSync(path.join(f.data, 'result.json'), 'utf8')).downloadSource, 'github-archive');
});

test('private GitHub API archive supports owner-specific archive roots without an anonymous fallback', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), archive = makeArchive(f, 'private-org-own-fork-1234abcd');
  const mock = stubs(f,
    String.raw`const fs=require('fs');fs.appendFileSync(process.env.STUB_LOG,JSON.stringify(process.argv.slice(2))+'\n');if(process.argv.some(x=>x.includes('/commits/')))process.stdout.write('${newCommit}');else process.stdout.write(fs.readFileSync(process.env.STUB_ARCHIVE));`,
    'throw Error("Anonymous download must not run");');
  const result = await run({ ...f.env, ...mock, STUB_ARCHIVE: archive, OPENBOT_SRC: '', OPENBOT_REPOSITORY: 'private-org/own-fork', OPENBOT_REF: 'release/local', OPENBOT_COMMIT: '', OPENBOT_TEST_DOWNLOAD_ONLY: '1' }, ['--bot-mode-worker']);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(readFileSync(path.join(f.data, 'result.json'), 'utf8')).downloadSource, 'github-api');
  assert.match(readFileSync(mock.STUB_LOG, 'utf8'), /repos\/private-org\/own-fork\/commits\/release\/local/);
});

test('source metadata corruption, credential URLs and dangerous destinations fail before replacement', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t);
  mkdirSync(f.dest); writeFileSync(path.join(f.dest, 'keep.txt'), 'old-release');
  const source = path.join(f.data, 'openbot-install-source.json');
  writeFileSync(source, '{bad json');
  assert.notEqual((await run(f.env)).status, 0);
  rmSync(source);
  for (const url of ['https://user:secret@example.test/archive', 'https://example.test/archive?token=secret']) {
    assert.notEqual((await run({ ...f.env, OPENBOT_SRC: '', OPENBOT_TARBALL: url })).status, 0);
  }
  assert.notEqual((await run({ ...f.env, OPENBOT_DEST: f.data })).status, 0);
  symlinkSync(path.join(f.data, 'openbot-plan.json'), path.join(f.src, 'payload/alias.json'));
  writeFileSync(path.join(f.data, 'openbot-plan.json'), '{"private":"no-copy"}');
  assert.notEqual((await run(f.env)).status, 0);
  assert.equal(readFileSync(path.join(f.dest, 'keep.txt'), 'utf8'), 'old-release');
});

test('symlink archives are refused before extraction', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), archive = makeArchive(f);
  const base = path.join(f.data, 'archive-source');
  symlinkSync('../../escaped', path.join(base, 'example-own-fork-cafed00d/escape'));
  execFileSync('tar', ['-czf', archive, '-C', base, 'example-own-fork-cafed00d']);
  const mock = stubs(f, 'process.exit(1);', `const fs=require('fs'),a=process.argv.slice(2);fs.copyFileSync(process.env.STUB_ARCHIVE,a[a.indexOf('-o')+1]);process.stdout.write('200');`);
  const result = await run({ ...f.env, ...mock, OPENBOT_SRC: '', STUB_ARCHIVE: archive, OPENBOT_TARBALL: 'http://127.0.0.1/archive', OPENBOT_TEST_DOWNLOAD_ONLY: '1' }, ['--bot-mode-worker']);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /invalid source archive/);
  assert.equal(existsSync(f.dest), false);
  assert.equal(existsSync(path.join(f.data, 'escaped')), false);
});

test('two installers cannot interleave a release switch and activation', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), marker = path.join(f.data, 'activating'), release = path.join(f.data, 'finish-activation');
  // Keep this synthetic entry point valid as JavaScript too: Node 24's syntax
  // check rejects its non-null assertions before activation.
  writeFileSync(path.join(f.src, 'src/cli.ts'), `import fs from 'node:fs';fs.writeFileSync(process.env.TEST_ACTIVATING, 'yes');const deadline=Date.now()+15000;while(!fs.existsSync(process.env.TEST_FINISH_ACTIVATION)){if(Date.now()>deadline)process.exit(99);await new Promise(r=>setTimeout(r,20));}\n`);
  let earlyResult: Run | undefined;
  const first = run({ ...f.env, TEST_ACTIVATING: marker, TEST_FINISH_ACTIVATION: release }).then(result => {
    earlyResult = result;
    return result;
  });
  let firstResult: Awaited<ReturnType<typeof run>>;
  try {
    const deadline = Date.now() + 10_000;
    while (!existsSync(marker) && !earlyResult && Date.now() < deadline) await new Promise(r => setTimeout(r, 20));
    assert.equal(existsSync(marker), true, 'first installer must hold the lock during activation' + (earlyResult ? ': ' + earlyResult.stderr : ''));
    const competing = await run({ ...f.env, OPENBOT_COMMIT: oldCommit });
    assert.equal(competing.status, 75, competing.stderr);
    assert.match(competing.stderr, /another installation/);
  } finally {
    writeFileSync(release, 'complete');
    firstResult = await first;
  }
  assert.equal(firstResult.status, 0, firstResult.stderr);
  assert.equal(JSON.parse(readFileSync(path.join(f.dest, 'payload/version.json'), 'utf8')).commit, newCommit);
});

test('runtime selection rejects early Node 22, Node 23, prereleases and missing capabilities', t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), candidate = path.join(f.data, 'candidate-node');
  writeFileSync(candidate, `#!${process.execPath}
const expression=process.argv[3];
if(process.argv[2]==='-p') {
 Object.defineProperty(process.versions,'node',{value:process.env.RUNTIME_TEST_VERSION});
 console.log(eval(expression));
} else if(process.argv[2]==='--experimental-strip-types') {
 if(process.env.RUNTIME_TEST_CAPABILITY==='missing-flag') process.exit(9);
 if(process.env.RUNTIME_TEST_CAPABILITY==='missing-websocket') globalThis.WebSocket=undefined;
 eval(process.argv[4]);
} else process.exit(10);
`); chmodSync(candidate, 0o755);
  const source = readFileSync(installer, 'utf8');
  const helper = source.slice(source.indexOf('node_runtime_supported() {'), source.indexOf('\nensure_node() {'));
  assert.ok(helper.length > 0);
  for (const [version, expected] of [
    ['20.19.0', false], ['22.0.0', false], ['22.6.0', false], ['22.12.0', false],
    ['22.13.0', true], ['22.14.0', true], ['22.23.3', true], ['23.11.0', false],
    ['24.0.0', true], ['24.21.0', true], ['25.0.0', true], ['24.0.0-rc.1', false], ['invalid', false],
  ] as const) {
    const result = spawnSync('bash', ['-c', helper + '\nnode_runtime_supported "$1"', 'runtime-test', candidate],
      { env: { ...process.env, RUNTIME_TEST_VERSION: version }, encoding: 'utf8' });
    assert.equal(result.status === 0, expected, version + ': ' + result.stderr);
  }
  for (const capability of ['missing-flag', 'missing-websocket']) {
    const result = spawnSync('bash', ['-c', helper + '\nnode_runtime_supported "$1"', 'runtime-test', candidate],
      { env: { ...process.env, RUNTIME_TEST_VERSION: '22.13.0', RUNTIME_TEST_CAPABILITY: capability }, encoding: 'utf8' });
    assert.notEqual(result.status, 0, capability);
  }
});

test('Node bootstrap verifies its archive before replacing an unsupported early Node 22 cache', async t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), bin = path.join(f.data, 'bin'); mkdirSync(bin);
  const realNode = process.execPath;
  const oldNode = `#!/usr/bin/env sh\nif [ "$1" = "-p" ]; then case "$2" in *">= 22"*) echo true ;; *) echo 22 ;; esac; else exec '${realNode}' "$@"; fi\n`;
  for (const file of [path.join(bin, 'node'), path.join(f.data, 'node22/bin/node')]) {
    mkdirSync(path.dirname(file), { recursive: true });writeFileSync(file, oldNode);chmodSync(file, 0o755);
  }
  const base = path.join(f.data, 'node-source'), release = path.join(base, 'node-v22.23.3-linux-x64');
  mkdirSync(path.join(release, 'bin'), { recursive: true });
  writeFileSync(path.join(release, 'bin/node'), `#!/usr/bin/env sh\nexec '${realNode}' "$@"\n`);chmodSync(path.join(release,'bin/node'),0o755);
  const archive = path.join(f.data,'node.tar.gz');
  execFileSync('tar',['-czf',archive,'-C',base,path.basename(release)]);
  const checksum = createHash('sha256').update(readFileSync(archive)).digest('hex');
  const curl = path.join(bin,'curl');
  writeFileSync(curl, `#!/usr/bin/env node\nconst fs=require('fs'),a=process.argv.slice(2),out=a[a.indexOf('-o')+1];if(a.at(-1).endsWith('SHASUMS256.txt'))fs.writeFileSync(out,process.env.STUB_SHA+'  node-v22.23.3-linux-x64.tar.gz\\n');else fs.copyFileSync(process.env.STUB_ARCHIVE,out);\n`);chmodSync(curl,0o755);
  const env = {...f.env,PATH:`${bin}:${process.env.PATH}`,STUB_ARCHIVE:archive,OPENBOT_NODE_DIST:'http://127.0.0.1/node-dist'};
  const rejected = await run({...env,STUB_SHA:'0'.repeat(64)});
  assert.notEqual(rejected.status,0);
  assert.match(rejected.stderr,/checksum mismatch/);
  assert.equal(readFileSync(path.join(f.data,'node22/bin/node'),'utf8'),oldNode);
  assert.equal(existsSync(f.dest),false);
  const accepted = await run({...env,STUB_SHA:checksum});
  assert.equal(accepted.status,0,accepted.stderr);
  assert.notEqual(readFileSync(path.join(f.data,'node22/bin/node'),'utf8'),oldNode);
  assert.equal(JSON.parse(readFileSync(path.join(f.data,'openbot-install-source.json'),'utf8')).repository,'example/own-fork');
});

test('shareable export contains exact committed product source and excludes private recovery artifacts and Git history', t => {
  if (skipOnWindows(t)) return;
  const f = fixture(t), repo = path.join(f.data, 'product'); mkdirSync(repo);
  const files: Record<string,string> = {
    'install.sh': 'DEFAULT_REPOSITORY="example/private-recovery"\n',
    'package.json': '{}\n', 'src/cli.ts': 'console.log("portable");\n',
    'recovery/hooks/generic.py': 'print("helper")\n',
    'recovery/config/fleet.json': 'private fleet',
    'recovery/evidence/server.json': 'private server identity',
    'recovery/backups/private.tar.gz.gpg': 'private encrypted backup',
    'recovery/README.md': 'private incident journal',
    'payload/auth.json': 'private login',
    'payload/version.json': '{"commit":"private-runtime"}',
    '.env': 'private key', 'README.md': 'private incident https://github.com/example/private-recovery',
    'skills/openbot-config/SKILL.md': 'private fleet history',
    'skills/openbot-config/reference.md': 'private node identities',
    'src/grok-skills.ts': 'export const GROK_SKILLS_GITHUB_REPO = "example/private-recovery";\n',
    'payload/openbot-local-engine/stock.cjs': 'private prepared engine',
    'payload/capture/host-main.cjs.pre-openbot': 'private vendor bundle',
    'docs/shareable-readme.md': 'git clone https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git',
    'docs/shareable-readme.zh-CN.md': '公开源码 YOUR_ACCOUNT/YOUR_REPOSITORY',
    'docs/shareable-config/SKILL.md': 'gh repo clone YOUR_ACCOUNT/YOUR_REPOSITORY',
    'docs/shareable-config/reference.md': 'gh repo clone YOUR_ACCOUNT/YOUR_REPOSITORY',
    'docs/updating.md': 'Official host and local engine need separate delivery proof.\n',
  };
  for (const [name, body] of Object.entries(files)) {mkdirSync(path.dirname(path.join(repo,name)),{recursive:true});writeFileSync(path.join(repo,name),body);}
  execFileSync('git',['init','-q',repo]);
  execFileSync('git',['-C',repo,'add','.']);
  execFileSync('git',['-C',repo,'-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','fixture']);
  writeFileSync(path.join(repo,'src/cli.ts'),'uncommitted change');
  const dest = path.join(f.data,'export');
  const args = [path.join(root,'scripts/export-shareable.mjs'),'--source',repo,'--destination',dest,'--repository','example/shareable'];
  const manifest = JSON.parse(execFileSync(process.execPath,args,{encoding:'utf8'}));
  assert.equal(manifest.sourceCommit,execFileSync('git',['-C',repo,'rev-parse','HEAD'],{encoding:'utf8'}).trim());
  assert.equal(readFileSync(path.join(dest,'src/cli.ts'),'utf8'),'console.log("portable");\n');
  for(const name of ['.git','.env','recovery/config','recovery/evidence','recovery/backups','recovery/README.md','payload/auth.json','payload/version.json','payload/openbot-local-engine','payload/capture/host-main.cjs.pre-openbot'])assert.equal(existsSync(path.join(dest,name)),false,name);
  assert.match(readFileSync(path.join(dest,'install.sh'),'utf8'),/example\/shareable/);
  assert.equal(JSON.parse(readFileSync(path.join(dest,'package.json'),'utf8')).repository.url,'git+https://github.com/example/shareable.git');
  assert.match(readFileSync(path.join(dest,'skills/openbot-config/SKILL.md'),'utf8'),/example\/shareable/);
  assert.equal(readFileSync(path.join(dest,'skills/openbot-config/reference.md'),'utf8').includes('private node'),false);
  assert.equal(readFileSync(path.join(dest,'README.md'),'utf8').includes('private incident'),false);
  assert.match(readFileSync(path.join(dest,'README.md'),'utf8'),/git clone https:\/\/github.com\/example\/shareable.git/);
  assert.equal(manifest.publicDocumentation,true);
  assert.equal(manifest.files['src/cli.ts'].bytes,readFileSync(path.join(dest,'src/cli.ts')).length);
  assert.equal(manifest.files['src/cli.ts'].sha256,createHash('sha256').update(readFileSync(path.join(dest,'src/cli.ts'))).digest('hex'));
  assert.equal(manifest.files['README.md'].sha256,createHash('sha256').update(readFileSync(path.join(dest,'README.md'))).digest('hex'));
  assert.throws(()=>execFileSync(process.execPath,args,{stdio:'pipe'}));
  assert.equal(readFileSync(path.join(dest,'src/cli.ts'),'utf8'),'console.log("portable");\n');
  // An exported fork must itself export another owner's project correctly.
  execFileSync('git', ['init', '-q', dest]);
  execFileSync('git', ['-C', dest, 'add', '.']);
  execFileSync('git', ['-C', dest, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'exported project']);
  const again = path.join(f.data, 're-export');
  execFileSync(process.execPath, [path.join(root, 'scripts/export-shareable.mjs'), '--source', dest, '--destination', again, '--repository', 'another/owner-project']);
  for (const file of ['README.md', 'skills/openbot-config/SKILL.md', 'skills/openbot-config/reference.md', 'src/grok-skills.ts']) {
    assert.match(readFileSync(path.join(again, file), 'utf8'), /another\/owner-project/);
    assert.equal(readFileSync(path.join(again, file), 'utf8').includes('example/shareable'), false);
  }
  // Missing public templates must fail before any private source is exported.
  execFileSync('git',['-C',repo,'rm','-q','docs/shareable-config/reference.md']);
  execFileSync('git',['-C',repo,'-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','missing public template']);
  const refused=path.join(f.data,'refused-export');
  assert.throws(()=>execFileSync(process.execPath,[path.join(root,'scripts/export-shareable.mjs'),'--source',repo,'--destination',refused,'--repository','example/public'],{stdio:'pipe'}));
  assert.equal(existsSync(refused),false);
});
