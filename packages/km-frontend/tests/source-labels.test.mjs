import test from "node:test";
import assert from "node:assert/strict";
import { locatorLabel } from "../src/shared/source-labels.js";

test("fragments and figures keep physical page locators", () => {
  assert.equal(locatorLabel({ type: "chunk", pages: [2, 3] }), "Pages 2, 3");
  assert.equal(locatorLabel({ type: "chunk", page: 4, pages: [4] }), "Page 4");
  assert.equal(locatorLabel({ type: "chunk" }), "Fragment");
  assert.equal(locatorLabel({ type: "visual" }), "Figure");
  assert.equal(locatorLabel({ sheet: "Données", range: "A1:B2" }), "Feuille Données · A1:B2");
});
