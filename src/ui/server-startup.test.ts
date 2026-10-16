import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { nodeProcs } from "../supervisor/procs.ts";
import { parseAbsPath } from "../supervisor/paths.ts";

test("a failed concurrent service exits and preserves the listener's PID", async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "openbot-startup-"));
  const server = http.createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const pidFile = path.join(folder, "openbot-ui.pid");
  const log = path.join(folder, "service.log");
  const owner = `${process.pid}\n`;
  fs.writeFileSync(pidFile, owner);
  const procs = nodeProcs();
  const pid = procs.start({
    argv: ["--experimental-strip-types", fileURLToPath(new URL("./server.ts", import.meta.url))],
    env: {
      ...process.env,
      OPENBOT_REPO: fileURLToPath(new URL("../../", import.meta.url)),
      OPENBOT_SAND_DATA: folder,
      OPENBOT_HOST_MAIN: path.join(folder, "host-main.cjs"),
      OPENBOT_UI_PORT: String(address.port),
      OPENBOT_UI_HOST: "127.0.0.1",
      OPENBOT_TURN_LEASE: path.join(folder, "turn-lease.json"),
      OPENBOT_GUARD_WATCH: "0",
      OPENBOT_TUNNEL_WATCH: "0",
    },
    pidFile: parseAbsPath(pidFile),
    log: parseAbsPath(log),
    writePidFile: false,
  });
  try {
    // Module loading can be slow on a remote filesystem. Measure exit after
    // the actual listen failure rather than treating import time as a hang.
    const startupDeadline = Date.now() + 60_000;
    let failureObserved = false;
    while (procs.pidAlive(pid) && Date.now() < startupDeadline) {
      if (fs.existsSync(log) && /service listen failed:.*EADDRINUSE/u.test(fs.readFileSync(log, "utf8"))) {
        failureObserved = true;
        break;
      }
      await new Promise<void>((resolve) => setTimeout(resolve, 50));
    }
    if (!failureObserved) assert.match(fs.readFileSync(log, "utf8"), /service listen failed:.*EADDRINUSE/u);
    const exitDeadline = Date.now() + 2_000;
    while (procs.pidAlive(pid) && Date.now() < exitDeadline) {
      await new Promise<void>((resolve) => setTimeout(resolve, 50));
    }
    assert.equal(procs.pidAlive(pid), false, "failed service remained alive after EADDRINUSE");
    assert.equal(fs.readFileSync(pidFile, "utf8"), owner);
    assert.match(fs.readFileSync(log, "utf8"), /service listen failed:.*EADDRINUSE/u);
    assert.equal(server.listening, true);
  } finally {
    if (procs.pidAlive(pid)) procs.stop(pid);
    await new Promise<void>((resolve) => server.close(() => resolve()));
    fs.rmSync(folder, { recursive: true, force: true });
  }
});
