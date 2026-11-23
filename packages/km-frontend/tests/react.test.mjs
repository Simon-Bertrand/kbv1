import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { createLifecycle } from "../src/shared/lifecycle.js";
import { initialDestination } from "../src/shell/navigation.js";

test("KM shell contains current workspaces",async()=>{
  const vite=await createServer({server:{middlewareMode:true},appType:"custom"});
  try {
    const {default:Layout}=await vite.ssrLoadModule("/src/shell/view.jsx");
    const markup=renderToStaticMarkup(React.createElement(Layout));
    for(const id of ["question","askForm","ragForm","traceSvg","evCreate","atlas","viewer","library","ingestion"])
      assert.ok(markup.includes('id="'+id+'"'),id);
    assert.ok(markup.includes('id="library"'));
    assert.ok(!markup.includes('id="mgFiles"'));
    assert.ok(!markup.includes("Passer en indexation…"));
  } finally {await vite.close();}
});

test("legacy links open a workspace within the one application", () => {
  const route = (pathname, hash = "") => initialDestination({ pathname, hash });
  assert.deepEqual(route("/atlas"), { view: "atlas" });
  assert.deepEqual(route("/ingestion"), { view: "ingestion" });
  assert.deepEqual(route("/metadata"), { view: "library", tab: "metadata" });
  assert.deepEqual(route("/management"), { view: "library" });
  assert.deepEqual(route("/kanban"), { view: "library" });
  assert.deepEqual(route("/sandbox"), { view: "rag" });
  assert.deepEqual(route("/viewer/document-id"), { view: "viewer" });
  assert.deepEqual(route("/", "#atlas"), { view: "atlas" });
  assert.deepEqual(route("/", "#unknown"), { view: "athena" });
});

test("React integration releases timers, global listeners and pending requests", () => {
  const removed = [],
    cancelled = [];
  globalThis.window = {
    setTimeout: () => 1,
    setInterval: () => 2,
    requestAnimationFrame: () => 3,
    clearTimeout: (id) => cancelled.push(id),
    clearInterval: (id) => cancelled.push(id),
    cancelAnimationFrame: (id) => cancelled.push(id),
  };
  const target = {
    addEventListener() {},
    removeEventListener: (...args) => removed.push(args),
  };
  const scope = createLifecycle(),
    listener = () => {};
  scope.setTimeout(() => {}, 10);
  scope.setInterval(() => {}, 10);
  scope.requestAnimationFrame(() => {});
  scope.listen(target, "resize", listener);
  scope.dispose();
  assert.equal(scope.signal.aborted, true);
  assert.equal(scope.disposed, true);
  assert.deepEqual(cancelled, [1, 2, 3]);
  assert.equal(removed[0][1], listener);
  delete globalThis.window;
});
