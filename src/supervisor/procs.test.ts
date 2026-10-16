import assert from "node:assert/strict";
import test from "node:test";
import { isHostMainArgv, isHopServerArgv, isOpengrokHopArgv } from "./procs.ts";

const host = "/home/box/sand-host/host-main.cjs";

test("host pid matcher requires node plus the host path as argv", () => {
  assert.equal(isHostMainArgv("node /home/box/sand-host/host-main.cjs", host, 1, 88), true);
  assert.equal(
    isHostMainArgv("/usr/bin/node /home/box/sand-host/host-main.cjs --gateway-token x", host, 1, 88),
    true,
  );
});

test("host pid matcher ignores shells that only mention the path", () => {
  assert.equal(
    isHostMainArgv(
      "/bin/zsh -c node --experimental-strip-types src/ui/server.ts OPENBOT_HOST_MAIN=/home/box/sand-host/host-main.cjs",
      host,
      1,
      88,
    ),
    false,
  );
});

test("host pid matcher ignores the current process", () => {
  assert.equal(isHostMainArgv("node /home/box/sand-host/host-main.cjs", host, 88, 88), false);
});

test("host pid matcher excludes the guard and other scripts carrying --host-main", () => {
  assert.equal(isHostMainArgv("/home/box/sand-data/node22/bin/node --experimental-strip-types /home/box/sand-data/openbot/src/cli.ts guard --daemon --host-main " + host, host, 1, 88), false);
  assert.equal(isHostMainArgv("node /tmp/watcher.cjs " + host, host, 1, 88), false);
  assert.equal(isHostMainArgv("/usr/bin/echo node " + host, host, 1, 88), false);
  assert.equal(isHostMainArgv("node --check " + host, host, 1, 88), false);
  assert.equal(isHostMainArgv("node --eval " + host, host, 1, 88), false);
  assert.equal(isHostMainArgv("node -e1 " + host, host, 1, 88), false);
});

test("host pid matcher accepts the native launcher and Node preload options", () => {
  assert.equal(isHostMainArgv("/exec-daemon/node --disable-warning=ExperimentalWarning " + host, host, 1, 88), true);
  assert.equal(isHostMainArgv("node --require /tmp/preload.cjs " + host + " --gateway-token fixture", host, 1, 88), true);
  assert.equal(isHostMainArgv("node -- " + host, host, 1, 88), true);
});

test("opengrok hop matcher is python plus hop-server.py", () => {
  assert.equal(isOpengrokHopArgv("python3 /home/box/sand-data/hop-server.py", 1, 88), true);
  assert.equal(isOpengrokHopArgv("node /home/box/sand-data/openbot/payload/hop-server.cjs", 1, 88), false);
  assert.equal(isOpengrokHopArgv("/bin/zsh -c python3 /home/box/sand-data/hop-server.py", 1, 88), false);
});

test("OpenBot hop matcher requires node plus the exact hop-server path", () => {
  const hop = "/home/box/sand-data/openbot/payload/hop-server.cjs";
  assert.equal(isHopServerArgv("node " + hop, hop, 1, 88), true);
  assert.equal(isHopServerArgv("node /tmp/other/hop-server.cjs", hop, 1, 88), false);
  assert.equal(isHopServerArgv("/bin/sh -c node " + hop, hop, 1, 88), false);
});
