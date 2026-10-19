import assert from "node:assert/strict";
import test from "node:test";
import { customActivationBlocked, customActivationModel, hostBlocked } from "../api/client.ts";
import type { BoxState } from "../api/types.ts";

const state = { snapshot: { wrap: { kind: "ambiguous-factory", functionDefs: 0, propertyDefs: 0 },
  uiListen: { kind: "ours", pid: 1 }, alignment: { desired: "official" } },
  activeModelId: "saved", models: [{ id: "first" }, { id: "saved" }] } as BoxState;

test("only explicit Custom activation can use independently verified pinned engine readiness", () => {
  assert.equal(customActivationBlocked(state)?.kind, "census-refused");
  const ready = { ...state, localEngine: { state: "ready", nativeDeliveryVerified: false } } as BoxState;
  assert.equal(customActivationBlocked(ready), null);
  assert.equal(hostBlocked(ready)?.kind, "census-refused");
  for (const status of ["unavailable", "not-pinned"] as const) {
    assert.equal(customActivationBlocked({ ...state, localEngine: { state: status, nativeDeliveryVerified: false } })?.kind, "census-refused");
  }
  assert.equal(customActivationBlocked({ ...ready, snapshot: { ...ready.snapshot, uiListen: { kind: "foreign", pid: 2, host: "127.0.0.1", port: 9280 } } })?.kind, "foreign-hop");
  assert.equal(customActivationBlocked({ ...ready, snapshot: { ...ready.snapshot, wrap: { kind: "foreign-opengrok" } } })?.kind, "foreign-opengrok");
});

test("Re-enable Custom preserves saved active model rather than first catalog entry", () => {
  assert.equal(customActivationModel(state)?.id, "saved");
  assert.equal(customActivationModel({ ...state, activeModelId: null })?.id, "first");
});
