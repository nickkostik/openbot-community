import assert from "node:assert/strict";
import test from "node:test";
import { appErrorMessage, createErrorLimiter, rejectionErrorMessage } from "./fatal-errors.ts";

test("opaque and injected wallet errors do not obscure the panel", () => {
  const origin = "https://openbot.example";
  assert.equal(appErrorMessage({ message: "Script error." }, origin), undefined);
  assert.equal(appErrorMessage({ message: "undefined is not an object (evaluating 'window.ethereum.selectedAddress = undefined')" }, origin), undefined);
  assert.equal(appErrorMessage({ message: "Request failed", filename: origin + "/assets/app.js", error: new Error("Request failed") }, origin), "Request failed");
  assert.equal(appErrorMessage({ message: "window.ethereum failure", filename: origin + "/assets/app.js" }, origin), "window.ethereum failure");
});

test("opaque script errors stay quiet even when browsers supply an Error object", () => {
  const origin = "https://openbot.example";
  assert.equal(appErrorMessage({ message: "Script error.", filename: origin + "/app.js", error: new Error("Script error.") }, origin), undefined);
  assert.equal(appErrorMessage({ message: "Script error.", error: { message: "Script error." } }, origin), undefined);
  assert.equal(appErrorMessage({ message: "Script error.", filename: origin + "/app.js", error: new Error("Actual parse failure") }, origin), "Actual parse failure");
});

test("a wallet injected into the document is not classified as an app script", () => {
  const origin = "https://openbot.example";
  const message = "undefined is not an object (evaluating 'window.ethereum.selectedAddress = undefined')";
  for (const filename of [origin, origin + "/", origin + "/#/bots", "chrome-extension://wallet/injected.js"]) {
    assert.equal(appErrorMessage({ message, filename }, origin), undefined);
  }
  assert.equal(appErrorMessage({ message, filename: origin + "/app.js" }, origin), message);
  assert.equal(appErrorMessage({ message, filename: origin + "/src/pages/Bots.tsx:20:4" }, origin), message);
  assert.equal(appErrorMessage({ message, filename: "https://elsewhere.example/app.js" }, origin), undefined);
});

test("promise rejection source evidence retains real app errors", () => {
  const origin = "https://openbot.example";
  const message = "window.ethereum failure";
  assert.equal(rejectionErrorMessage({ message, stack: `Error: ${message}\n at fn (${origin}/app.js:10:20)` }, origin), message);
  assert.equal(rejectionErrorMessage({ message, stack: `Error: ${message}\n at injected (${origin}/:10:20)` }, origin), undefined);
  assert.equal(rejectionErrorMessage(new Error("Script error."), origin), undefined);
  assert.equal(rejectionErrorMessage("Model save failed", origin), "Model save failed");
});

test("error handling tolerates injected objects with throwing getters", () => {
  const hostile = { get message(): never { throw new Error("getter"); }, toString(): never { throw new Error("string conversion"); } };
  assert.equal(appErrorMessage({ message: "Script error.", error: hostile }, "https://openbot.example"), undefined);
  assert.equal(rejectionErrorMessage(hostile, "https://openbot.example"), "Unhandled rejection");
});

test("repeated errors are limited, but new and later errors remain visible", () => {
  const show = createErrorLimiter();
  assert.equal(show("Save failed", 0), true);
  assert.equal(show("Save failed", 1), false);
  assert.equal(show("Network failed", 2), true);
  assert.equal(show("Save failed", 10000), true);
});
