import assert from 'node:assert/strict';
import test from 'node:test';
import { HOST_MAIN } from '../domain/types.ts';
import { parseInstallCommand } from '../parse/argv.ts';
import { selectNativeHostMain } from './host-location.ts';
import { boxPathsFrom } from './paths.ts';

const modern = '/opt/sand/sand-host/host-main.cjs';
test('host discovery selects the actual native process before an unused copy', () => {
  assert.equal(selectNativeHostMain({ running:[modern], available:[HOST_MAIN,modern] }),modern);
  assert.equal(selectNativeHostMain({ running:[HOST_MAIN], available:[HOST_MAIN,modern] }),HOST_MAIN);
  assert.equal(selectNativeHostMain({ running:[modern,modern], available:[HOST_MAIN,modern] }),modern);
  assert.throws(()=>selectNativeHostMain({ running:[modern,HOST_MAIN], available:[HOST_MAIN,modern] }),/multiple/);
  assert.equal(selectNativeHostMain({explicit:'/custom/host.cjs',running:[modern,HOST_MAIN],available:[]}),'/custom/host.cjs');
  assert.equal(selectNativeHostMain({running:[],available:[modern]}),modern);
  assert.equal(selectNativeHostMain({running:[],available:[HOST_MAIN]}),HOST_MAIN);
});
test('CLI flags override environment paths and native default backup follows the selected host', () => {
  const parsed = parseInstallCommand({argv:['status'],repoRoot:'/tmp/source',env:{OPENBOT_HOST_MAIN:modern}});
  assert.equal(parsed.paths.hostMain,modern);
  assert.equal(parsed.paths.knownBackup,modern+'.pre-openbot');
  const explicit = parseInstallCommand({argv:['status','--host-main','/custom/host.cjs','--sand-data','/custom/data'],repoRoot:'/tmp/source',env:{OPENBOT_HOST_MAIN:modern,OPENBOT_SAND_DATA:'/other/data'}});
  assert.equal(explicit.paths.hostMain,'/custom/host.cjs');
  assert.equal(explicit.paths.sandData,'/custom/data');
  assert.equal(boxPathsFrom({hostMain:HOST_MAIN,repoRoot:'/tmp/source'}).knownBackup,HOST_MAIN+'.pre-openbot');
});
