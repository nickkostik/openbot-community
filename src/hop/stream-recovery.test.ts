import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const hop = require("../../payload/hop-handler.cjs") as {
  handleHopRequest: (req: http.IncomingMessage, res: http.ServerResponse) => Promise<boolean>;
  isRetryableUpstreamError: (error: unknown) => boolean;
};
const logs = require("../../payload/request-log.cjs") as {
  listRequests: () => { items: Array<{ status: number; ok: boolean; error?: string; attemptCount?: number; attempts?: unknown[] }> };
};

async function listen(handler: http.RequestListener) {
  const server = http.createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  return { server, port: address.port };
}

function close(server: http.Server) {
  server.close();
  server.closeAllConnections();
}

async function fixture(protocol: "responses" | "chat-completions", mode: "off" | "dry-run" | "enforce", handler: http.RequestListener) {
  const dir = mkdtempSync(path.join(os.tmpdir(), "openbot-stream-recovery-"));
  const upstream = await listen(handler);
  writeFileSync(path.join(dir, "openbot-plan.json"), JSON.stringify({
    kind: "custom",
    catalog: {
      providers: [{ id: "test", name: "Test", origin: `http://127.0.0.1:${upstream.port}/v1`, apiType: protocol }],
      models: [{ id: "test:model", providerId: "test", slug: "model", parameters: [] }],
      bindings: [],
    },
  }));
  writeFileSync(path.join(dir, "openbot-secrets.json"), JSON.stringify({ providers: { test: "test-key" } }));
  writeFileSync(path.join(dir, "openbot-logs.json"), JSON.stringify({ loggingEnabled: true, logBodies: false }));
  writeFileSync(path.join(dir, "openbot-injection.json"), JSON.stringify({ mode }));
  const env = {
    OPENBOT_SAND_DATA: dir,
    OPENBOT_PLAN: path.join(dir, "openbot-plan.json"),
    OPENBOT_SECRETS: path.join(dir, "openbot-secrets.json"),
    OPENBOT_LOGS: path.join(dir, "openbot-logs.json"),
    OPENBOT_INJECTION_MODE: mode,
  };
  const before = Object.fromEntries(Object.keys(env).map((k) => [k, process.env[k]]));
  Object.assign(process.env, env);
  const service = await listen((req, res) => { void hop.handleHopRequest(req, res); });
  return {
    port: service.port,
    cleanup() {
      close(service.server); close(upstream.server);
      for (const [key, value] of Object.entries(before)) {
        if (value === undefined) delete process.env[key]; else process.env[key] = value;
      }
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

function post(port: number, stream = true): Promise<{ status: number; text: string }> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ model: "test:model", stream, messages: [{ role: "user", content: "reply" }] });
    const req = http.request({ hostname: "127.0.0.1", port, path: "/v1/chat/completions", method: "POST", headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) } }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("error", reject);
      res.on("end", () => resolve({ status: res.statusCode ?? 0, text: Buffer.concat(chunks).toString() }));
    });
    req.setTimeout(4000, () => req.destroy(new Error("test client timed out waiting for stream")));
    req.on("error", reject);
    req.end(payload);
  });
}

function responses(text: string) {
  return [
    { type: "response.created", response: { id: "resp_test", model: "model" } },
    { type: "response.output_text.delta", delta: text },
    { type: "response.completed", response: { id: "resp_test", model: "model", status: "completed", output: [{ type: "message", content: [{ type: "output_text", text }] }], usage: { input_tokens: 1, output_tokens: 1 } } },
  ].map((event) => "data: " + JSON.stringify(event) + "\n\n").join("") + "data: [DONE]\n\n";
}

function failedResponse(code: string, text?: string) {
  const events: unknown[] = [{ type: "response.created", response: { id: "failed-attempt", model: "model" } }];
  if (text) events.push({ type: "response.output_text.delta", delta: text });
  events.push({ type: "response.failed", response: { status: "failed", error: { code, message: `provider failure: ${code}`, request_id: "req-failed" } } });
  return events.map((event) => "data: " + JSON.stringify(event) + "\n\n").join("");
}

for (const mode of ["off", "dry-run", "enforce"] as const) {
  test(`HTTP 200 Responses overload retries before output (${mode})`, async () => {
    let hits = 0;
    const requests: string[] = [];
    const f = await fixture("responses", mode, (req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (chunk: Buffer) => chunks.push(chunk));
      req.on("end", () => {
        requests.push(Buffer.concat(chunks).toString());
        hits++;
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.end(hits === 1 ? failedResponse("server_error") : responses("overload-recovered"));
      });
    });
    try {
      const result = await post(f.port);
      assert.equal(result.status, 200);
      assert.equal(hits, 2);
      assert.equal(requests[0], requests[1], "retry must preserve the selected route and request");
      assert.match(result.text, /overload-recovered/);
      assert.doesNotMatch(result.text, /failed-attempt|provider failure/);
      assert.equal(result.text.match(/data: \[DONE\]/g)?.length, 1);
      const row = logs.listRequests().items[0];
      assert.equal(row?.ok, true);
      assert.equal(row?.attemptCount, 2);
    } finally { f.cleanup(); }
  });

  test(`HTTP 200 persistent overload returns a real error after bounded retries (${mode})`, async () => {
    let hits = 0;
    const f = await fixture("responses", mode, (req, res) => {
      req.resume(); req.on("end", () => {
        hits++;
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.end(failedResponse("server_error"));
      });
    });
    try {
      const result = await post(f.port);
      assert.equal(hits, 3);
      assert.equal(result.status, 503);
      const error = JSON.parse(result.text).error;
      assert.equal(error.code, "server_error");
      assert.equal(error.request_id, "req-failed");
      assert.equal(error.upstream_status, 200);
      assert.equal(logs.listRequests().items[0]?.ok, false);
    } finally { f.cleanup(); }
  });

  test(`subscription quota errors are explicit and never retried (${mode})`, async () => {
    let hits = 0;
    const f = await fixture("responses", mode, (req, res) => {
      req.resume(); req.on("end", () => {
        hits++;
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.end(failedResponse("subscription_sharing_usage_limit_exceeded"));
      });
    });
    try {
      const result = await post(f.port);
      assert.equal(hits, 1);
      assert.equal(result.status, 502);
      assert.equal(JSON.parse(result.text).error.code, "subscription_sharing_usage_limit_exceeded");
      assert.equal(logs.listRequests().items[0]?.ok, false);
    } finally { f.cleanup(); }
  });
}

for (const mode of ["dry-run", "enforce"] as const) {
  test(`failed Responses after visible output does not replay it (${mode})`, async () => {
    let hits = 0;
    const f = await fixture("responses", mode, (req, res) => {
      req.resume(); req.on("end", () => {
        hits++;
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.write('data: {"type":"response.output_text.delta","delta":"visible-once"}\n\n');
        setTimeout(() => res.end(failedResponse("server_error")), 20);
      });
    });
    try {
      await assert.rejects(post(f.port), (error: NodeJS.ErrnoException) => error.code === "ECONNRESET");
      assert.equal(hits, 1, "a retry must never duplicate visible text or tools");
      assert.equal(logs.listRequests().items[0]?.ok, false);
    } finally { f.cleanup(); }
  });
}

test("non-streaming compaction retries an HTTP 200 failed Responses stream", async () => {
  let hits = 0;
  const f = await fixture("responses", "off", (req, res) => {
    req.resume(); req.on("end", () => {
      hits++;
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.end(hits === 1 ? failedResponse("server_error") : responses("summary-recovered"));
    });
  });
  try {
    const result = await post(f.port, false);
    assert.equal(hits, 2);
    assert.equal(result.status, 200);
    assert.equal(JSON.parse(result.text).choices[0].message.content, "summary-recovered");
  } finally { f.cleanup(); }
});

for (const mode of ["off", "dry-run"] as const) {
  test(`Responses retries a reset before client output (${mode})`, async () => {
    let hits = 0;
    const f = await fixture("responses", mode, (req, res) => {
      req.resume(); req.on("end", () => {
        hits++;
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        if (hits === 1) {
          res.write('data: {"type":"response.created","response":{"id":"discarded"}}\n\n');
          // The converter ignores response.created. No user or tool output
          // has been delivered, so this attempt is safe to replace.
          setTimeout(() => res.destroy(), 20);
        } else res.end(responses("recovered-output"));
      });
    });
    try {
      const result = await post(f.port);
      assert.equal(result.status, 200);
      assert.equal(hits, 2);
      assert.equal(result.text.match(/recovered-output/g)?.length, 1);
      assert.equal(result.text.match(/data: \[DONE\]/g)?.length, 1);
      const row = logs.listRequests().items[0];
      assert.equal(row?.ok, true);
      assert.equal(row?.error, undefined);
      assert.equal(row?.attemptCount, 2);
    } finally { f.cleanup(); }
  });

  test(`a partial streamed reply fails explicitly without replay (${mode})`, async () => {
    let hits = 0;
    const f = await fixture("chat-completions", mode, (req, res) => {
      req.resume(); req.on("end", () => {
        hits++;
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        res.write('data: {"choices":[{"delta":{"content":"partial-output"}}]}\n\n');
        setTimeout(() => res.destroy(), 20);
      });
    });
    try {
      await assert.rejects(post(f.port), (error: NodeJS.ErrnoException) => error.code === "ECONNRESET");
      assert.equal(hits, 1, "emitted content must not be replayed or duplicate actions");
      const row = logs.listRequests().items[0];
      assert.equal(row?.status, 502);
      assert.equal(row?.ok, false);
    } finally { f.cleanup(); }
  });
}

test("buffered Responses discards partial content before retrying", async () => {
  let hits = 0;
  const f = await fixture("responses", "off", (req, res) => {
    req.resume(); req.on("end", () => {
      hits++;
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      if (hits === 1) {
        res.write('data: {"type":"response.output_text.delta","delta":"discard-me"}\n\n');
        setTimeout(() => res.destroy(), 20);
      } else res.end(responses("complete-reply"));
    });
  });
  try {
    const result = await post(f.port);
    assert.equal(hits, 2);
    assert.match(result.text, /complete-reply/);
    assert.doesNotMatch(result.text, /discard-me/);
  } finally { f.cleanup(); }
});

test("successful JSON retries remain successful in the error log", async () => {
  let hits = 0;
  const f = await fixture("chat-completions", "off", (req, res) => {
    req.resume(); req.on("end", () => {
      hits++;
      res.writeHead(hits === 1 ? 503 : 200, { "Content-Type": "application/json" });
      res.end(hits === 1 ? '{"error":{"message":"temporary"}}' : '{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}');
    });
  });
  try {
    const result = await post(f.port, false);
    assert.equal(result.status, 200);
    const row = logs.listRequests().items[0];
    assert.equal(row?.ok, true);
    assert.equal(row?.error, undefined);
    assert.equal(row?.attemptCount, 2);
    assert.equal(row?.attempts?.length, 2);
  } finally { f.cleanup(); }
});

test("repeated pre-output resets stop at the retry limit and return an error", async () => {
  let hits = 0;
  const f = await fixture("responses", "off", (req, res) => {
    req.resume(); req.on("end", () => {
      hits++;
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.flushHeaders();
      setTimeout(() => res.destroy(), 20);
    });
  });
  try {
    const result = await post(f.port);
    assert.equal(hits, 3);
    assert.equal(result.status, 502);
    assert.match(result.text, /hop failed/);
  } finally { f.cleanup(); }
});

test("a disconnected client does not spend further upstream retries", async () => {
  let hits = 0;
  let upstreamReceived: () => void = () => {};
  const received = new Promise<void>((resolve) => { upstreamReceived = resolve; });
  const f = await fixture("responses", "off", (req, res) => {
    req.resume(); req.on("end", () => {
      hits++;
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.write('data: {"type":"response.created","response":{"id":"waiting"}}\n\n');
      upstreamReceived();
    });
  });
  try {
    const request = http.request({ hostname: "127.0.0.1", port: f.port, path: "/v1/chat/completions", method: "POST", headers: { "Content-Type": "application/json" } });
    const failed = new Promise<void>((resolve) => request.on("error", () => resolve()));
    request.end(JSON.stringify({ model: "test:model", stream: true, messages: [] }));
    await received;
    request.destroy();
    await failed;
    await new Promise<void>((resolve) => setTimeout(resolve, 650));
    assert.equal(hits, 1);
  } finally { f.cleanup(); }
});

test("record-layer TLS failures retry without weakening certificate checks", () => {
  assert.equal(hop.isRetryableUpstreamError(Object.assign(new Error("SSL routines:wrong version number"), { code: "EPROTO" })), true);
  assert.equal(hop.isRetryableUpstreamError(Object.assign(new Error("certificate has expired"), { code: "CERT_HAS_EXPIRED" })), false);
  assert.equal(hop.isRetryableUpstreamError(Object.assign(new Error("SSL certificate verify failed"), { code: "EPROTO" })), false);
});

test("cancelled non-streaming compaction closes its upstream request", async () => {
  let hits = 0;
  let markReceived: () => void = () => {};
  let markClosed: () => void = () => {};
  const received = new Promise<void>((resolve) => { markReceived = resolve; });
  const closed = new Promise<void>((resolve) => { markClosed = resolve; });
  const f = await fixture("responses", "off", (req, res) => {
    req.resume(); req.on("end", () => {
      hits++;
      res.on("close", markClosed);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.flushHeaders();
      markReceived();
    });
  });
  let deadline: ReturnType<typeof setTimeout> | undefined;
  try {
    const request = http.request({ hostname: "127.0.0.1", port: f.port, path: "/v1/chat/completions", method: "POST", headers: { "Content-Type": "application/json" } });
    const failed = new Promise<void>((resolve) => request.on("error", () => resolve()));
    request.end(JSON.stringify({ model: "test:model", stream: false, messages: [] }));
    await received;
    request.destroy();
    await failed;
    await Promise.race([closed, new Promise<void>((_resolve, reject) => {
      deadline = setTimeout(() => reject(new Error("upstream kept running after compactor cancellation")), 1000);
    })]);
    await new Promise<void>((resolve) => setTimeout(resolve, 650));
    assert.equal(hits, 1);
  } finally { clearTimeout(deadline); f.cleanup(); }
});
