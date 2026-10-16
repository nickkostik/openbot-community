import assert from "node:assert/strict";
import test from "node:test";
import { createStateOrder } from "./state-order.ts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

test("an old poll delivered after a successful model save cannot revert the selection", async () => {
  const order = createStateOrder();
  const poll = deferred<string>();
  let selected = "old-model";
  const ticket = order.beginRead();
  const reading = poll.promise.then(value => { if (order.accepts(ticket)) selected = value; });
  order.beginWrite();
  selected = "new-model";
  order.finishWrite();
  poll.resolve("old-model");
  await reading;
  assert.equal(selected, "new-model");
  assert.equal(order.accepts(order.beginRead()), true);
});

test("polls during rapid queued saves remain stale after every save completes", () => {
  const order = createStateOrder();
  order.beginWrite(); order.beginWrite();
  const duringFirst = order.beginRead();
  order.finishWrite();
  assert.equal(order.accepts(duringFirst), false);
  const duringSecond = order.beginRead();
  assert.equal(order.accepts(duringSecond), false);
  order.finishWrite();
  assert.equal(order.accepts(duringFirst), false);
  assert.equal(order.accepts(duringSecond), false);
  assert.equal(order.accepts(order.beginRead()), true);
});

test("late poll errors and out-of-order reads cannot replace the latest state", async () => {
  const order = createStateOrder();
  const error = deferred<string>();
  let visible = "loading";
  const beforeSave = order.beginRead();
  const waiting = error.promise.then(value => { if (order.accepts(beforeSave)) visible = value; });
  order.beginWrite(); visible = "saved"; order.finishWrite();
  error.resolve("stale error"); await waiting;
  assert.equal(visible, "saved");
  const first = order.beginRead(), second = order.beginRead();
  assert.equal(order.accepts(first), false);
  assert.equal(order.accepts(second), true);
});
