#!/usr/bin/env node
import { constants, openSync, closeSync, fstatSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [packageRoot, stockPath, destination] = process.argv.slice(2);
if (![packageRoot, stockPath, destination].every(value => value && resolve(value) === value))
  throw new Error('Expected absolute package, stock and output paths');

function owned(path, maximum) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || info.size > maximum)
      throw new Error('Wrap input is not an owned bounded regular file');
    return readFileSync(fd, 'utf8');
  } finally { closeSync(fd); }
}

const runtime = join(packageRoot, 'payload/runtime.cjs');
owned(runtime, 4 * 1024 * 1024);
const { wrapHostSource } = await import(pathToFileURL(join(packageRoot, 'src/host/wrap.ts')).href);
const { payloadFingerprint } = await import(pathToFileURL(join(packageRoot, 'src/host/payload-fingerprint.ts')).href);
const fingerprint = payloadFingerprint({ payloadDir: join(packageRoot, 'payload'), read(path) {
  try { return owned(path, 4 * 1024 * 1024); }
  catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
} });
const proof = wrapHostSource({ source: owned(stockPath, 48 * 1024 * 1024), runtimePath: runtime,
  payloadFingerprint: fingerprint });
if (proof.kind !== 'wrapped') throw new Error('Prepared engine refused canonical wrapping');
writeFileSync(destination, proof.source, { mode: 0o600, flag: 'wx' });
process.stdout.write(JSON.stringify({ payloadFingerprint: fingerprint, runtimePath: runtime }) + '\n');
