// A bounded, drillable view of the complete event stream. No events are discarded.
const phaseNames = { prepare: "Préparer", research: "Explorer les documents", synthesize: "Rédiger", verify: "Vérifier" };

export function spansAt(events, position, traceFinished = false) {
  const spans = new Map();
  for (const event of events.slice(0, position)) {
    if (event.kind === "span_start") spans.set(event.span_id, { ...event, status: "running",
      subquestion: event.subquestion || traceSubquestion(event) });
    else if (event.kind === "span_end" && spans.has(event.span_id))
      Object.assign(spans.get(event.span_id), { status: event.status, outputs: event.outputs,
        error: event.error, duration_ms: event.duration_ms, end: event.ts, completion: event,
        subquestion: spans.get(event.span_id).subquestion || event.subquestion || traceSubquestion(event) });
  }
  if (traceFinished) {
    for (const span of spans.values()) {
      if (span.status === "running") span.status = "incomplete";
    }
  }
  return [...spans.values()];
}

export const traceGroupPageSize = 3;

export function traceSubquestion(event) {
  const state = event.name === "select_subquestion" && event.kind === "span_end" ? event.outputs
    : event.name === "solve_subquestion" && event.kind === "span_start" ? event.inputs : null;
  const id = state?.current_subquestion_id;
  if (typeof id !== "string") return undefined;
  const label = Array.isArray(state.sub_questions)
    ? state.sub_questions.find(question => question?.id === id)?.question : undefined;
  return { id, label: typeof label === "string" ? label : undefined };
}

export function traceBranches(root, spans) {
  const nodes = new Map();
  for (const [step, span] of [root, ...spans].entries()) {
    const parent = nodes.get(span.parent_id) || nodes.get(root.span_id);
    const node = { ...span, step, parent_id: parent?.span_id, children: [] };
    parent?.children.push(node);
    nodes.set(node.span_id, node);
  }
  const steps = [...nodes.values()];
  const rootNode = steps[0];
  const top = rootNode.children;
  const branches = [];
  let questionNumber = 0;
  for (let index = 0; index < top.length; index++) {
    const choice = top[index];
    const next = top[index + 1];
    const followsChoice = choice.name === "select_subquestion" && next?.name === "solve_subquestion"
      && (!choice.subquestion?.id || !next.subquestion?.id || choice.subquestion.id === next.subquestion.id);
    const explore = choice.name === "solve_subquestion" ? choice : followsChoice ? next : null;
    if (!explore) {
      branches.push(choice);
      continue;
    }
    const subquestion = { ...choice.subquestion, ...explore.subquestion,
      label: explore.subquestion?.label || choice.subquestion?.label };
    questionNumber++;
    const name = subquestion?.label || `Sous-question ${questionNumber}`;
    const children = choice === explore ? [explore] : [choice, explore];
    if (choice !== explore) index++;
    const groupNode = {
      span_id: `subquestion:${explore.span_id}`, parent_id: rootNode.span_id,
      category: "subquestion", isQuestionGroup: true, name, children,
      status: explore.status, subquestion: { ...subquestion, label: name },
    };
    for (const child of children) {
      child.parent_id = groupNode.span_id;
      child.subquestion = groupNode.subquestion;
    }
    nodes.set(groupNode.span_id, groupNode);
    branches.push(groupNode);
  }
  rootNode.children = branches;
  for (const node of steps) {
    const groups = [];
    for (const child of node.children) {
      child.subquestion ||= node.subquestion;
      const previous = groups.at(-1);
      if (child.category === "tool" && previous?.[0].category === "tool") previous.push(child);
      else groups.push([child]);
    }
    node.children = groups.flatMap(calls => {
      if (calls.length < 4) return calls;
      const first = calls[0];
      const sameName = calls.every(call => call.name === first.name);
      const groupNode = {
        span_id: `group:${first.span_id}`, parent_id: node.span_id, category: "tool",
        name: sameName ? first.name : "Appels d’outil", isCallGroup: true, children: calls,
        failures: calls.filter(call => call.status === "error").length,
        status: calls.some(call => call.status === "error") ? "error"
          : calls.some(call => call.status === "running") ? "running"
          : calls.some(call => call.status === "incomplete") ? "incomplete" : "ok",
      };
      nodes.set(groupNode.span_id, groupNode);
      calls.forEach((call, position) => {
        call.parent_id = groupNode.span_id;
        call.groupPage = Math.floor(position / traceGroupPageSize);
      });
      return [groupNode];
    });
  }
  return { root: steps[0], steps, nodes };
}

export function traceTreeLayout(tree, expandedGroups = new Map(), foldedBranches = new Set()) {
  const visible = [];
  const pending = [{ ...tree.root, depth: 0 }];
  while (pending.length) {
    const node = pending.pop();
    node.x = 24 + node.depth * 36;
    node.y = 24 + visible.length * 108;
    visible.push(node);
    let children = node.children;
    if (node.isCallGroup) {
      const page = expandedGroups.get(node.span_id);
      node.pageCount = Math.ceil(children.length / traceGroupPageSize);
      node.page = page === undefined ? undefined : Math.min(page, node.pageCount - 1);
      children = page === undefined ? [] : children.slice(node.page * traceGroupPageSize, (node.page + 1) * traceGroupPageSize);
    } else if (foldedBranches.has(node.span_id)) {
      children = [];
    }
    for (let i = children.length - 1; i >= 0; i--) {
      pending.push({ ...children[i], depth: node.depth + 1 });
    }
  }
  return visible;
}

function descendants(children) {
  const result = new Map();
  const stack = [...children];
  while (stack.length) {
    const item = stack.pop();
    if (item.category !== "group") result.set(item.span_id, item);
    stack.push(...(item.children || []));
  }
  return [...result.values()];
}

function group(id, name, children) {
  const members = descendants(children);
  const tools = members.filter((s) => s.category === "tool").length;
  const llms = members.filter((s) => s.category === "llm").length;
  const errors = members.filter((s) => s.status === "error").length;
  const incomplete = members.filter((s) => s.status === "incomplete").length;
  return { span_id: id, name, category: "group", children,
    status: errors ? "error" : members.some((s) => s.status === "running") ? "running" : incomplete ? "incomplete" : "ok",
    summary: `${tools} outils · ${llms} appels LLM${errors ? ` · ${errors} erreurs` : ""}`,
    inputs: { étapes: members.length, outils: tools, appels_llm: llms, erreurs: errors },
    outputs: { message: "Ouvrir les groupes pour inspecter chaque appel.", appels: members.map((s) => s.span_id) },
    duration_ms: Math.round(children.reduce((total, child) => total + (child.duration_ms || 0), 0)) };
}

function foldRepeated(children, parentId) {
  const groups = new Map();
  for (const item of children) {
    const key = item.category + ":" + item.name;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups].map(([key, list]) => list.length === 1 ? list[0]
    : group(parentId+"/calls/"+key, `${list[0].name} × ${list.length}`, list));
}

export function traceTree(spans) {
  const byId = new Map(spans.map((s) => [s.span_id, { ...s, children: [] }]));
  const roots = [];
  for (const item of byId.values()) {
    const parent = byId.get(item.parent_id);
    if (parent) parent.children.push(item);
    else roots.push(item);
  }
  for (const item of byId.values()) item.children = foldRepeated(item.children, item.span_id);
  const phases = new Map(Object.keys(phaseNames).map((key) => [key, []]));
  for (const item of roots) {
    const phase = item.name === "synthesize" ? "synthesize" : item.name === "verify_final" ? "verify"
      : ["select_subquestion", "solve_subquestion"].includes(item.name) ? "research" : "prepare";
    phases.get(phase).push(item);
  }
  const questions = new Map();
  for (const item of phases.get("research")) {
    const id = item.name === "solve_subquestion" ? item.inputs?.current_subquestion_id : null;
    const key = id || "routing";
    if (!questions.has(key)) questions.set(key, []);
    questions.get(key).push(item);
  }
  phases.set("research", [...questions].map(([id, items]) => {
    const question = items.flatMap((item) => item.inputs?.sub_questions || []).find((q) => q.id === id);
    return group("question:"+id, question?.question || (id === "routing" ? "Pilotage des sous-questions" : "Sous-question"), items);
  }));
  const root = { span_id: "overview", name: "Vue d’ensemble", category: "group",
    children: [...phases].filter(([, children]) => children.length).map(([key, children]) =>
      group("phase:"+key, phaseNames[key], children)) };
  const index = new Map();
  const visit = [root];
  while (visit.length) {
    const item = visit.pop();
    index.set(item.span_id, item);
    visit.push(...item.children);
  }
  return { root, index };
}

export function traceLevel(root, path) {
  let node = root;
  const crumbs = [root];
  for (const id of path) {
    const child = node.children.find((item) => item.span_id === id);
    if (!child?.children.length) break;
    node = child;
    crumbs.push(node);
  }
  return { node, crumbs };
}

export function traceLayout(children, width, page = 0, pageSize = null) {
  const columns = Math.max(1, Math.min(4, Math.floor((width - 30) / 260)));
  pageSize = pageSize || columns * 3;
  const totalPages = Math.max(1, Math.ceil(children.length / pageSize));
  page = Math.max(0, Math.min(page, totalPages - 1));
  return { page, totalPages, layout: children.slice(page * pageSize, (page + 1) * pageSize)
    .map((item, index) => ({ ...item, x: 24+(index % columns)*260,
      y: 30+Math.floor(index / columns)*112 })) };
}

// The execution position is independent from whichever call the user inspects.
export function executionFlow(spans, runStatus = "running") {
  const byId = new Map(spans.map(s => [s.span_id, s]));
  const active = [...spans].reverse().find(s => s.status === "running");
  const focus = active || [...spans].reverse().find(s => s.end) || spans.at(-1);
  const chain = [], seen = new Set();
  for (let node = focus; node && !seen.has(node.span_id); node = byId.get(node.parent_id)) {
    seen.add(node.span_id); chain.unshift(node);
  }
  const phaseOf = node => node?.name === "synthesize" ? "synthesize"
    : node?.name === "verify_final" ? "verify"
    : ["select_subquestion", "solve_subquestion"].includes(node?.name) ? "research" : "prepare";
  const currentPhase = focus ? phaseOf(chain[0]) : null;
  const roots = spans.filter(s => !byId.has(s.parent_id));
  const phases = Object.entries(phaseNames).map(([key, name]) => {
    const members = roots.filter(s => phaseOf(s) === key);
    const running = active && currentPhase === key && runStatus === "running";
    return { key, name, status: running ? "running" : members.length ?
      (members.some(s => s.status === "error") || ["failed","interrupted"].includes(runStatus) && currentPhase === key ? "error" : "ok") : "pending" };
  });
  const solver = chain.find(s => s.inputs?.current_subquestion_id);
  const question = solver?.inputs?.sub_questions?.find(q => q.id === solver.inputs.current_subquestion_id)?.question;
  const recent = spans.filter(s => s.category !== "node").slice(-12).reverse();
  return { phases, chain, focus, question, recent, currentPhase,
    live: runStatus === "running" && !!active, status: runStatus };
}
