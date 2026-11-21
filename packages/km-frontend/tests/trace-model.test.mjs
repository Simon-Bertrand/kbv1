import { test } from "node:test";
import assert from "node:assert/strict";
import { traceBranches, traceTreeLayout, spansAt, traceTree, traceLevel, traceLayout, executionFlow } from "../src/features/console/trace-model.js";
import { locatorLabel, sourceName } from "../src/shared/source-labels.js";

test("vertical layout draws each subtree before moving to the next sibling", () => {
  const root = { span_id: "question" };
  const nodes = traceTreeLayout(traceBranches(root, [
    { span_id: "first", name: "read_page" },
    { span_id: "nested", parent_id: "first" },
    { span_id: "second", name: "read_page" },
    { span_id: "last", parent_id: "missing" },
  ]));
  const [question, first, nested, second, last] = nodes;
  assert.deepEqual(nodes.map(node => node.step), [0, 1, 2, 3, 4]);
  assert.equal(first.x, second.x);
  assert.equal(second.x, last.x);
  assert.ok(question.x < first.x && first.x < nested.x);
  assert.ok(first.y < nested.y && nested.y < second.y);
  assert.ok(second.y < last.y);
  assert.equal(last.parent_id, question.span_id);
});

test("fifty tool calls fit in one collapsed branch and open three at a time", () => {
  const root = { span_id: "question" };
  const calls = Array.from({ length: 50 }, (_, index) => ({
    span_id: `tool${index}`, name: "read_page", category: "tool",
    status: index === 49 ? "error" : "ok",
  }));
  const tree = traceBranches(root, calls);
  const [question, group] = traceTreeLayout(tree);
  assert.equal(group.depth, question.depth + 1);
  assert.equal(group.children.length, 50);
  assert.equal(group.failures, 1);
  assert.equal(group.status, "error");
  assert.equal(tree.steps[50].span_id, "tool49");
  assert.equal(tree.steps[50].groupPage, 16);
  const expanded = traceTreeLayout(tree, new Map([[group.span_id, 0]]));
  assert.equal(expanded.length, 5);
  assert.equal(new Set(expanded.slice(2).map(call => call.x)).size, 1);
  const last = traceTreeLayout(tree, new Map([[group.span_id, 16]]));
  assert.deepEqual(last.slice(2).map(call => call.span_id), ["tool48", "tool49"]);
  assert.equal(traceTreeLayout(tree, new Map(), new Set([root.span_id])).length, 1);
});

test("an interrupted group shows its unfinished calls", () => {
  const events = Array.from({ length: 4 }, (_, i) => ({
    kind: "span_start", span_id: `call${i}`, category: "tool", name: "read_page",
  }));
  const { root } = traceBranches({ span_id: "question" }, spansAt(events, events.length, true));
  assert.equal(root.children[0].status, "incomplete");
});

test("tool batches preserve order inside the selected subquestion", () => {
  const root = { span_id: "question" };
  const input = { current_subquestion_id: "q1", sub_questions: [{ id: "q1", question: "Quelle pression ?" }] };
  const choice = { span_id: "choice", name: "select_subquestion", category: "node", subquestion: { id: "q1" } };
  const solve = { span_id: "solve", kind: "span_start", name: "solve_subquestion", category: "node", inputs: input };
  const calls = ["read_page", "read_document", "read_page", "search_text"].map((name, i) => ({
    span_id: `call${i}`, name, category: "tool", parent_id: "solve",
  }));
  const llm = { span_id: "llm", category: "llm", parent_id: "solve" };
  const last = { span_id: "later", name: "read_page", category: "tool", parent_id: "solve" };
  const tree = traceBranches(root, [choice, ...spansAt([solve], 1), ...calls, llm, last]);
  const subquestion = tree.nodes.get("subquestion:solve");
  assert.deepEqual(subquestion.children.map(node => node.span_id), ["choice", "solve"]);
  assert.equal(subquestion.name, "Quelle pression ?");
  const [batch, model, later] = tree.nodes.get("solve").children;
  assert.deepEqual(batch.children.map(node => node.name), calls.map(call => call.name));
  assert.deepEqual([model.span_id, later.span_id], ["llm", "later"]);
  assert.equal(later.subquestion.label, subquestion.name);
  assert.deepEqual(tree.steps.slice(3).map(node => node.span_id), ["call0", "call1", "call2", "call3", "llm", "later"]);
});

test("a long trace stays compact and all calls remain reachable", () => {
  const spans = [{ span_id: "plan", category: "node", name: "decompose", status: "ok" },
    { span_id: "solve", category: "node", name: "solve_subquestion", status: "ok",
      inputs: { current_subquestion_id: "q1", sub_questions: [{ id: "q1", question: "Quels documents ?" }] } }];
  for (let i = 0; i < 500; i++) spans.push({ span_id: "tool"+i, parent_id: "solve", category: "tool",
    name: "read_page", status: i === 42 ? "error" : "ok", duration_ms: 1 });
  const { root, index } = traceTree(spans);
  assert.equal(root.children.length, 2);
  const research = root.children.find((node) => node.span_id === "phase:research");
  assert.equal(research.status, "error");
  assert.equal(research.inputs.outils, 500);
  assert.equal(research.children[0].name, "Quels documents ?");
  assert.ok(index.has("tool499"));
  const selected = traceLevel(root, ["phase:research", "question:q1", "solve"]);
  assert.equal(selected.node.children.length, 1);
  assert.equal(selected.node.children[0].children.length, 500);
  const page = traceLayout(selected.node.children[0].children, 720);
  assert.ok(page.layout.length <= 9);
  assert.ok(Math.max(...page.layout.map((node) => node.y)) < 300);
  assert.ok(page.totalPages > 1);
  assert.equal(traceLayout(selected.node.children[0].children, 720, 9999).page, page.totalPages-1);
});

test("timeline replay never exposes future results and resets an unavailable drill path", () => {
  const events = [{ kind: "span_start", span_id: "s1", category: "tool", name: "read_page" },
    { kind: "span_end", span_id: "s1", status: "ok", outputs: { content: "future" } }];
  assert.equal(spansAt(events, 1)[0].status, "running");
  assert.equal(spansAt(events, 1)[0].outputs, undefined);
  assert.deepEqual(spansAt(events, 2)[0].outputs, { content: "future" });
  const { root } = traceTree(spansAt(events, 0));
  assert.equal(traceLevel(root, ["phase:research", "old-question"]).crumbs.length, 1);
});

test("source labels show document names and physical locators without JSON or IDs", () => {
  assert.equal(sourceName({ original_name: "Notice A.pdf", document_id: "uuid" }), "Notice A.pdf");
  assert.equal(locatorLabel({ type: "page", page: 12 }), "Page 12");
  assert.equal(locatorLabel({ type: "sheet", sheet: "Équipements", range: "A1:C8" }), "Feuille Équipements · A1:C8");
  assert.equal(locatorLabel({ type: "message" }), "Message");
});

test("a finished trace keeps real outcomes and stops animating groups with missing span endings", () => {
  const events = [
    { kind: "span_start", span_id: "solve", category: "node", name: "solve_subquestion" },
    { kind: "span_start", span_id: "first", parent_id: "solve", category: "tool", name: "read_page" },
    { kind: "span_end", span_id: "first", status: "ok", outputs: { text: "Source" } },
    { kind: "span_start", span_id: "second", parent_id: "solve", category: "tool", name: "read_page" },
    { kind: "span_end", span_id: "solve", status: "ok" },
  ];
  const live = traceTree(spansAt(events, events.length)).index;
  const finished = traceTree(spansAt(events, events.length, true)).index;
  for (const id of ["phase:research", "solve/calls/tool:read_page", "second"]) {
    assert.equal(live.get(id).status, "running");
    assert.equal(finished.get(id).status, "incomplete");
  }
  assert.equal(finished.get("first").status, "ok");
  assert.deepEqual(finished.get("first").outputs, { text: "Source" });
});

test("execution position keeps its parents and all four phases visible", () => {
 const spans=[{span_id:"plan",name:"decompose",category:"node",status:"ok"},
 {span_id:"solve",name:"solve_subquestion",category:"node",status:"running",inputs:{current_subquestion_id:"q",sub_questions:[{id:"q",question:"Quelle pression ?"}]}},
 {span_id:"read",parent_id:"solve",name:"read_page",category:"tool",status:"running"}];
 const flow=executionFlow(spans);
 assert.equal(flow.phases.length,4);
 assert.deepEqual(flow.phases.map(p=>p.status),["ok","running","pending","pending"]);
 assert.deepEqual(flow.chain.map(s=>s.span_id),["solve","read"]);
 assert.equal(flow.question,"Quelle pression ?");
 assert.equal(flow.focus.span_id,"read");
 assert.equal(flow.recent[0].span_id,"read");
 assert.equal(flow.live,true);
});

test("position follows a later phase, bounds history and retains stopped errors", () => {
 const spans=[{span_id:"solve",name:"solve_subquestion",category:"node",status:"ok"}];
 for(let i=0;i<500;i++)spans.push({span_id:"r"+i,parent_id:"solve",name:"read_page",category:"tool",status:"ok",end:i+1});
 spans.push({span_id:"verify",name:"verify_final",category:"node",status:"running"});
 spans.push({span_id:"llm",parent_id:"verify",name:"llama.cpp · verify",category:"llm",status:"error",end:1000});
 const live=executionFlow(spans);
 assert.equal(live.currentPhase,"verify");
 assert.equal(live.recent.length,12);
 assert.equal(live.phases[3].status,"running");
 const stopped=executionFlow(spans,"failed");
 assert.equal(stopped.live,false);
 assert.equal(stopped.phases[3].status,"error");
 assert.equal(executionFlow([]).focus,undefined);
});
