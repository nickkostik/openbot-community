import assert from "node:assert/strict";
import * as fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { execFileSync } from "node:child_process";

const source = fs.readFileSync(new URL("../uninstall.sh", import.meta.url), "utf8");
const statusWriter = source.match(/json_write\(\)[\s\S]*?<<'PYRESULT'\n([\s\S]*?)\nPYRESULT/);
assert.ok(statusWriter);

const harness = String.raw`
import json, os, sys
writer, target, fail = sys.argv[1:]
sys.argv = ['-', target, 'success', 'verify', 'Complete', '', '2026-10-01T00:01:00Z']
observed = []
original_fdopen, original_replace = os.fdopen, os.replace
class PausingFile:
    def __init__(self, handle): self.handle = handle
    def __enter__(self): self.handle.__enter__(); return self
    def __exit__(self, *args): return self.handle.__exit__(*args)
    def __getattr__(self, name): return getattr(self.handle, name)
    def write(self, body):
        self.handle.write(body[:8]); self.handle.flush()
        with open(target, encoding='utf8') as current: observed.append(json.load(current))
        return self.handle.write(body[8:]) + 8
def replace(before, after):
    if fail == 'true': raise RuntimeError('fixture rename failure')
    original_replace(before, after)
os.fdopen = lambda *args, **kwargs: PausingFile(original_fdopen(*args, **kwargs))
os.replace = replace
try:
    exec(writer)
    if fail == 'true': raise AssertionError('Expected a publication failure')
except RuntimeError as error:
    if fail != 'true' or str(error) != 'fixture rename failure': raise
print(json.dumps(observed))
`;

for (const failRename of [false, true]) {
  test(`uninstall status stays readable during ${failRename ? "failed" : "successful"} publication`, (t) => {
    try { execFileSync('python3', ['--version'], { stdio: 'ignore' }); }
    catch { t.skip('Python 3 is unavailable'); return; }
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-uninstall-status-"));
    t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
    const file = path.join(dir, "result.json");
    const before = { status: "running", startedAt: "2026-10-01T00:00:00Z" };
    fs.writeFileSync(file, JSON.stringify(before));
    const output = execFileSync('python3', ['-c', harness, statusWriter![1]!, file, String(failRename)], { encoding: 'utf8' });
    assert.deepEqual(JSON.parse(output), [before]);
    const after = JSON.parse(fs.readFileSync(file, "utf8"));
    assert.equal(after.status, failRename ? "running" : "success");
    assert.equal(after.startedAt, before.startedAt);
    assert.deepEqual(fs.readdirSync(dir), ["result.json"]);
  });
}
