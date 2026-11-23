import { locatorLabel, sourceName } from "../../shared/source-labels.js";
import { spansAt, traceBranches, traceSubquestion, traceTreeLayout } from "./trace-model.js";
import { createLifecycle } from "../../shared/lifecycle.js";
export function mountConsole() {
  const scope = createLifecycle();
  const { setTimeout, setInterval, requestAnimationFrame } = scope;
  ("use strict");
  const $ = (id) => document.getElementById(id),
    esc = (s) =>
      String(s ?? "").replace(
        /[&<>"']/g,
        (c) =>
          ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          })[c],
      );
  const names = {
    athena: "Agent",
    sources: "Documents",
    rag: "Recherche",
    traces: "Suivi",
    library: "Indexation",
    ingestion: "Ingestion",
    evaluation: "Évaluation",
    atlas: "Atlas des métadonnées",
    viewer: "Pages sources",
  };
  const labels = {
    decompose: "Décomposer la question",
    select_subquestion: "Choisir une sous-question",
    solve_subquestion: "Explorer les sources",
    synthesize: "Rédiger la réponse",
    verify_final: "Vérifier la réponse",
    search_text: "Recherche textuelle",
    search_visual: "Recherche visuelle",
    list_document_pages: "Parcourir les pages",
    document_outline: "Plan du document",
    read_selected_pages: "Lire les pages retenues",
    read_page: "Lire une page",
    read_document: "Lire un document",
    read_metadata: "Lire les métadonnées du fichier",
    metadata_select: "Filtrer les métadonnées",
  };
  let runs = [],
    current = null,
    events = [],
    traceAfter = 0,
    traceOffset = 0,
    tracePending = false,
    legacyTrace = false,
    selectedSpan = null,
    ioTab = "inputs",
    playing = null,
    polling = false,
    version = 0,
    view = "athena",
    activeRun = null;
  let detailKey = "", detailCursor = null, detailPath = null;
  const expandedGroups = new Map();
  const foldedBranches = new Set();
  let ragCapabilities = {}, loadingScopeColumns = false;
  let traceSteps = [];
  let zoom = 1,
    pan = { x: 30, y: 35 },
    layout = [],
    drag = null,
    pointerMoved = false;
  const svgNS = "http://www.w3.org/2000/svg";
  function toast(text) {
    $("toast").textContent = text;
    $("toast").style.display = "block";
    setTimeout(() => ($("toast").style.display = "none"), 3200);
  }
  function notice(id, text) {
    $(id).textContent = text;
    $(id).classList.toggle("hidden", !text);
  }
  function show(name, { updateUrl = true } = {}) {
    view = name;
    document
      .querySelectorAll(".view")
      .forEach((n) => n.classList.toggle("active", n.id === name));
    document
      .querySelectorAll(".nav")
      .forEach((n) =>
        n.classList.toggle(
          "active",
          n.dataset.view === (name === "viewer" ? "library" : name),
        ),
      );
    $("viewTitle").textContent = names[name];
    if (updateUrl && name !== "viewer") {
      history.replaceState(null, "", name === "athena" ? "/" : `/#${name}`);
    }
    window.dispatchEvent(new CustomEvent("console:view", { detail: name }));
    if (name === "traces") requestAnimationFrame(renderGraph);
  }
  document
    .querySelectorAll("[data-view]")
    .forEach((b) => (b.onclick = () => show(b.dataset.view)));
  document
    .querySelectorAll("[data-go]")
    .forEach((b) => (b.onclick = () => show(b.dataset.go)));
  async function api(path, options = {}) {
    const r = await fetch(path, {
      ...options,
      signal: options.signal || scope.signal,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
    let data;
    try {
      data = await r.json();
    } catch {
      throw Object.assign(Error("Réponse du service illisible"), { status: r.status });
    }
    if (!r.ok)
      throw Object.assign(Error(
        typeof data.detail === "string"
          ? data.detail
          : `Erreur HTTP ${r.status}`,
      ), { status: r.status });
    return data;
  }
  const date = (t) =>
    new Date(t * 1000).toLocaleString("fr-FR", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  function statusText(s) {
    return (
      {
        running: "EN COURS",
        complete: "TERMINÉE",
        failed: "ÉCHEC",
        interrupted: "INTERROMPUE",
        demo: "DÉMONSTRATION",
      }[s] || s.toUpperCase()
    );
  }
  async function refreshRuns() {
    try {
      runs = (await api("/api/athena/runs")).items;
      renderRuns();
    } catch (e) {
      $("runsList").innerHTML =
        '<div class="empty">Service de traces indisponible.</div>';
      notice("askNotice", e.message);
    }
  }
  function renderRuns() {
    const term = $("runSearch").value.toLocaleLowerCase();
    $("runsList").innerHTML = "";
    for (const r of runs.filter((r) =>
      r.question.toLocaleLowerCase().includes(term),
    )) {
      const b = document.createElement("button");
      b.className = "run-item" + (current?.id === r.id ? " active" : "");
      b.innerHTML = `<span class="badge ${esc(r.status)}">${esc(statusText(r.status))}</span><p>${esc(r.question.slice(0, 100))}</p><small>${esc(date(r.started))}</small>`;
      b.onclick = () => openRun(r.id);
      $("runsList").append(b);
    }
    if (!runs.length)
      $("runsList").innerHTML =
        '<div class="empty">Les exécutions apparaîtront ici.</div>';
    $("recentRuns").innerHTML = "";
    for (const r of runs.slice(0, 3)) {
      const b = document.createElement("button");
      b.className = "run-card";
      b.innerHTML = `<span class="badge ${esc(r.status)}">${esc(statusText(r.status))}</span><strong>${esc(r.question.slice(0, 130))}</strong><small>${esc(date(r.started))} <span>↗</span></small>`;
      b.onclick = () => {
        show("traces");
        openRun(r.id);
      };
      $("recentRuns").append(b);
    }
    if (!runs.length)
      $("recentRuns").innerHTML = '<div class="empty">Aucune requête.</div>';
  }
  $("runSearch").oninput = renderRuns;
  $("refreshRuns").onclick = refreshRuns;
  $("askForm").onsubmit = async (e) => {
    e.preventDefault();
    $("askButton").disabled = true;
    notice("askNotice", "Démarrage…");
    try {
      const result = await api("/api/athena/runs", {
        method: "POST",
        body: JSON.stringify({
          question: $("question").value,
          seconds: Number($("budget").value),
          search_mode: $("askMode").value,
        }),
      });
      activeRun = result.id;
      notice("askNotice", "En cours.");
      $("answerCard").classList.add("hidden");
      await refreshRuns();
      await openRun(result.id);
      show("traces");
    } catch (e) {
      notice("askNotice", e.message);
      $("askButton").disabled = false;
    }
  };
  $("answerTrace").onclick = () => {
    show("traces");
    if (activeRun) openRun(activeRun);
  };
  async function openRun(id) {
    cameraRevision++;
    stopPlay();
    initialGraphFit = true;
    version++;
    current = { id, status: "running", question: "Chargement…" };
    events = [];
    layout = [];
    traceSteps = [];
    expandedGroups.clear();
    foldedBranches.clear();
    traceAfter = 0;
    traceOffset = 0;
    tracePending = true;
    legacyTrace = false;
    selectedSpan = null;
    $("timeline").value = 1;
    $("followLive").checked = true;
    if (window.matchMedia("(max-width: 1100px)").matches) setRunsCollapsed(true);
    renderGraph();
    pan = { x: 25, y: 25 };
    zoom = 0.85;
    renderRuns();
    try {
      await updateRun(true);
    } catch (e) {
      toast(e.message);
    }
  }
  function traceEventsUrl(id, after, offset, structure = false) {
    return `/api/athena/runs/${id}/events?after=${after}&offset=${offset}${structure ? "&structure=true" : ""}`;
  }

  async function updateRun(initial = false) {
    if (!current || current.id === "demo" || polling === version) return;
    const token = version, id = current.id;
    polling = token;
    try {
      const oldStatus = current.status;
      const wasPending = tracePending;
      const info = current.ended ? current : await api(`/api/athena/runs/${id}`);
      if (token !== version) return;
      current = info;
      renderRuns();
      if (info.status !== "running" && info.status !== "queued" && activeRun === id) {
        $("askButton").disabled = false;
        notice(
          "askNotice",
          info.status === "complete"
            ? "Terminé."
            : `L’enquête s’est arrêtée : ${info.error || info.status}`,
        );
        if (info.result) {
          $("answerCard").classList.remove("hidden");
          const answer = info.result.answer || "Aucune réponse étayée disponible.";
          $("answerText").textContent = answer;
          import("../../shared/markdown.js").then(({ renderMarkdown }) => {
            if (token === version) return renderMarkdown($("answerText"), answer);
          }).catch(() => { if (token === version) $("answerText").textContent = answer; });
          $("answerWarnings").textContent = (info.result.warnings || []).join(" · ");
          try {
            renderAnswerSources(info.result.sources || []);
          } catch {
            $("answerSources").replaceChildren();
            $("answerWarnings").append(" Les liens vers les sources n’ont pas pu être affichés.");
          }
        }
      }
      const batch = await api(traceEventsUrl(id, traceAfter, traceOffset, true));
      if (token !== version) return;
      for (const event of batch.items) {
        if (event.detail_offset !== undefined) {
          events.push({ ...event, legacyOffset: traceOffset });
          continue;
        }
        // Older agents supply the full event page and ignore the structure flag.
        legacyTrace = true;
        const { inputs, outputs, error, ...header } = event;
        events.push({ ...header, legacyOffset: traceOffset, subquestion: traceSubquestion(event) });
      }
      traceAfter = batch.next_after ?? events.length;
      traceOffset = batch.next_offset ?? 0;
      tracePending = batch.has_more ?? (batch.items.length === 250);
      if (initial || batch.items.length || oldStatus !== info.status || wasPending !== tracePending) {
        renderGraph({ follow: $("followLive").checked && ["running", "queued"].includes(oldStatus) &&
          (!initial || ["running", "queued"].includes(info.status)) });
      }
      if (tracePending) setTimeout(() => {
        if (token === version) updateRun();
      }, 50);
    } catch (e) {
      if (token === version) toast(e.message);
    } finally {
      if (polling === token) polling = false;
    }
  }
  function renderAnswerSources(sources, target = $("answerSources")) {
    const links = document.createDocumentFragment();
    for (const source of sources) {
      if (!source) continue;
      const label = `${sourceName(source)} · ${locatorLabel(source.locator || {})}`;
      if (/^d[a-f0-9]{32}$/.test(source.document_id || "")) {
        const link = document.createElement("a");
        link.className = "source-link";
        const page = source.locator?.page || source.locator?.pages?.[0];
        link.href = "/view/documents/" + source.document_id + "/source" +
          (Number.isInteger(page) && page > 0 ? "#page=" + page : "");
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = label;
        links.append(link);
      } else if (source.page_id) {
        const button = document.createElement("button");
        button.className = "source-link";
        button.textContent = label;
        button.onclick = () => {
          if ($("traceInspectorDialog").open) $("traceInspectorDialog").close();
          show("rag");
          readSource(source);
        };
        links.append(button);
      }
    }
    target.replaceChildren(links);
  }
  setInterval(() => {
    if (
      current &&
      current.id !== "demo" &&
      (current.status === "running" || current.status === "queued" || tracePending)
    )
      updateRun();
  }, 1000);
  setInterval(refreshRuns, 10000);
  function svgEl(tag, attrs = {}, text) {
    const el = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    if (text !== undefined) el.textContent = text;
    return el;
  }
  const workspace = $("canvas").closest(".trace-workspace");
  function setRunsCollapsed(collapsed) {
    workspace.classList.toggle("runs-collapsed", collapsed);
    $("toggleRuns").setAttribute("aria-expanded", String(!collapsed));
  }
  $("toggleRuns").onclick = () => setRunsCollapsed(!workspace.classList.contains("runs-collapsed"));
  const inspectorDialog = $("traceInspectorDialog");
  $("expandInspector").onclick = () => {
    $("inspectorModalContent").append($("inspectorBody"));
    inspectorDialog.showModal();
    inspectorDialog.scrollTop = 0;
  };
  $("closeTraceInspector").onclick = () => inspectorDialog.close();
  inspectorDialog.onclose = () => $("inspectorDock").append($("inspectorBody"));
  $("followLive").onchange = () => renderGraph({ follow: $("followLive").checked });

  const nodeTypes = {
    question: ["?", "Question utilisateur"],
    node: ["◆", "Étape"],
    tool: ["⌘", "Outil"],
    llm: ["✦", "LLM"],
    search: ["◎", "Recherche"],
    subquestion: ["?", "Sous-question"],
  };

  function renderGraph({ follow = false, focus = false, anchor = null } = {}) {
    if (!current?.started) {
      renderInspector();
      $("canvasEmpty").classList.remove("hidden");
      return;
    }
    const traceFinished = !tracePending &&
      ["complete", "failed", "interrupted", "demo"].includes(current.status);
    const previous = selectedSpan;
    const anchorId = anchor || previous;
    const oldPositions = new Map(layout.map(s => [s.span_id, s]));
    const oldNode = oldPositions.get(anchorId);
    const animateLayout = !initialGraphFit && (anchor || focus) && !reducedMotion.matches;
    const root = {
      span_id: "user-question",
      category: "question",
      name: current.question,
      status: current.result ? "ok" : ({ failed: "error", interrupted: "incomplete" })[current.status] || current.status,
    };
    const tree = traceBranches(root, spansAt(events, events.length, traceFinished));
    traceSteps = tree.steps;
    let index = traceSteps.findIndex(s => s.span_id === selectedSpan);
    if (follow) index = traceSteps.length - 1;
    else if (index < 0) index = 0;
    selectedSpan = traceSteps[index].span_id;

    if (focus) {
      for (let node = traceSteps[index]; node.parent_id;) {
        const parent = tree.nodes.get(node.parent_id);
        if (parent.isCallGroup) expandedGroups.set(parent.span_id, node.groupPage);
        else foldedBranches.delete(parent.span_id);
        node = parent;
      }
    }
    layout = traceTreeLayout(tree, expandedGroups, foldedBranches);
    const positions = new Map(layout.map(s => [s.span_id, s]));
    let visibleSelection = traceSteps[index];
    while (!positions.has(visibleSelection.span_id)) {
      visibleSelection = tree.nodes.get(visibleSelection.parent_id);
    }
    const selectedNode = positions.get(visibleSelection.span_id);
    if (previous !== selectedSpan) {
      setInspectorTab(index === 0 && current.result ? "outputs" : "inputs");
    }
    const newAnchor = positions.get(anchorId);
    if (!initialGraphFit && oldNode && newAnchor && (anchor || previous === selectedSpan)) {
      pan.x += (oldNode.x - newAnchor.x) * zoom;
      pan.y += (oldNode.y - newAnchor.y) * zoom;
    }
    $("timeline").max = traceSteps.length;
    $("timeline").value = Math.max(1, index + 1);
    $("timeline").disabled = false;
    $("previousStep").disabled = index <= 0;
    $("nextStep").disabled = index >= traceSteps.length - 1;
    $("traceSummary").textContent = layout.some(s => s.isCallGroup)
      ? "Cliquez sur un groupe pour déplier ses appels" : "Les branches regroupent les appels par parent";
    $("foldAllGroups").hidden = !layout.some(s => s.isCallGroup && s.page !== undefined);
    $("canvasEmpty").classList.toggle("hidden", layout.length > 0);
    $("traceQuestion").textContent = current.question;
    $("traceStatus").textContent = statusText(current.status);
    $("traceStatus").className = "badge " + current.status;
    $("traceDuration").textContent = current.ended
      ? `${(current.ended - current.started).toFixed(1)} s`
      : current.status === "running"
        ? "EN DIRECT"
        : "RELECTURE";
    $("nodeCount").textContent = traceSteps.filter(
      (s) => s.category === "node",
    ).length;
    $("toolCount").textContent = traceSteps.filter(
      (s) => s.category === "tool",
    ).length;
    $("llmCount").textContent = traceSteps.filter(
      (s) => s.category === "llm",
    ).length;
    $("eventPosition").textContent = `${index + 1} / ${traceSteps.length}`;
    const focused = document.activeElement?.getAttribute("data-trace-span");
    const edges = document.createDocumentFragment();
    const nodes = document.createDocumentFragment();
    let focusTarget;
    const moving = [];
    const edgePath = (parent, node) => {
      const from = parent.x + 15;
      const toY = node.y + 42;
      return `M${from} ${parent.y + 82} V${toY} H${node.x}`;
    };
    for (const s of layout) {
      const parent = positions.get(s.parent_id);
      const old = oldPositions.get(s.span_id);
      if (parent) {
        const path = svgEl("path", {
          d: edgePath(parent, s),
          class: `trace-edge${s.status === "running" ? " live" : ""}`,
        });
        const oldParent = oldPositions.get(parent.span_id);
        if (animateLayout) {
          moving.push([path, old && oldParent
            ? [{ d: `path("${edgePath(oldParent, old)}")` }, { d: `path("${edgePath(parent, s)}")` }]
            : [{ opacity: 0 }, { opacity: 1 }]]);
        }
        edges.append(path);
      }
      const category = s.name?.startsWith("search_") ? "search" : s.category;
      const type = nodeTypes[category] || nodeTypes.node;
      const group = s.isCallGroup;
      const expanded = group && s.page !== undefined;
      const branch = !group && s.children.length > 0;
      const questionGroup = s.isQuestionGroup;
      const folded = branch && foldedBranches.has(s.span_id);
      const selected = selectedNode.span_id === s.span_id;
      const g = svgEl("g", {
        class: `trace-node ${category} ${s.status} ${group ? "call-group" : ""} ${selected ? "selected" : ""}`,
        transform: `translate(${s.x},${s.y})`,
        tabindex: selected || group || questionGroup ? 0 : -1,
        "data-trace-span": s.span_id,
        role: "button",
        "aria-label": s.category === "question" ? `Question utilisateur : ${s.name}` : `${s.name} · ${s.status}`,
      });
      const relative = Number.isFinite(s.ts) ? `+${Math.max(0, s.ts - current.started).toFixed(2)} s` : "";
      const status = questionGroup ? (folded ? "Déplier les étapes" : "Choix et exploration")
        : group ? expanded ? `Page ${s.page + 1} / ${s.pageCount}` + (s.failures ? ` · ${s.failures} en erreur` : "")
          : s.failures ? `${s.failures} en erreur` : s.status === "running" ? "Appels en cours" : "Déplier les appels"
        : s.category === "question" && current.result ? "Réponse finale dans Sorties"
        : ({ running: "En cours", queued: "En attente", error: "Erreur", incomplete: "Fin non enregistrée" })[s.status]
          || (s.duration_ms == null ? "Terminé" : `${s.duration_ms} ms`);
      const name = s.category === "question" ? s.name : labels[s.name] || s.name;
      if (group) {
        g.setAttribute("aria-expanded", String(expanded));
        g.setAttribute("aria-label", `${name} · ${s.children.length} appels${s.failures ? ` · ${s.failures} en erreur` : ""}`);
      } else if (questionGroup) {
        g.setAttribute("aria-expanded", String(!folded));
      } else {
        g.setAttribute("aria-pressed", String(selected));
      }
      g.append(
        svgEl("title", {}, name),
        svgEl("rect", { width: 270, height: 82, rx: 5 }),
        svgEl("text", { x: 14, y: 24, class: "node-type" },
          group ? `${expanded ? "−" : "+"}  ${s.children.length} appels` : `${type[0]}  ${type[1]}`),
        svgEl("text", { x: branch ? 210 : 256, y: 24, "text-anchor": "end", class: "node-caption" }, relative),
        svgEl("text", { x: 14, y: 49, class: "node-label" }, name.length > 34 ? `${name.slice(0, 33)}…` : name),
        svgEl("text", { x: 14, y: 69, class: "node-caption node-status" }, status),
      );
      const open = () => {
        if (questionGroup) {
          toggleBranch();
          return;
        }
        if (!group) {
          chooseStep(s.step, { focus: false });
          return;
        }
        if (expanded) expandedGroups.delete(s.span_id);
        else expandedGroups.set(s.span_id, 0);
        renderGraph({ anchor: s.span_id });
      };
      g.onclick = () => { if (!pointerMoved) open(); };
      g.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        } else if (!group && !questionGroup && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
          e.preventDefault();
          chooseStep(s.step + (e.key === "ArrowUp" ? -1 : 1));
        }
      };
      nodes.append(g);
      animateCard(g, s, old);
      if (focused && s.span_id === (anchor || selectedNode.span_id)) focusTarget = g;
      function toggleBranch() {
        if (folded) foldedBranches.delete(s.span_id);
        else foldedBranches.add(s.span_id);
        renderGraph({ anchor: s.span_id });
      }
      if (branch) {
        addControl(s, "branch", 222, 7, folded ? "+" : "−",
          `${folded ? "Déplier" : "Replier"} la branche : ${name}`, toggleBranch, !folded);
      }
      if (expanded) {
        for (const delta of [-1, 1]) {
          const page = s.page + delta;
          if (page < 0 || page >= s.pageCount) continue;
          addControl(s, String(delta), delta < 0 ? 181 : 222, 48, delta < 0 ? "←" : "→",
            `${name} : page ${delta < 0 ? "précédente" : "suivante"}`, () => {
              expandedGroups.set(s.span_id, page);
              renderGraph({ anchor: s.span_id });
            });
        }
      }
    }

    function addControl(node, suffix, x, y, icon, label, action, expanded) {
      const id = `${node.span_id}:${suffix}`;
      const button = svgEl("g", {
        class: "trace-group-page",
        transform: `translate(${node.x + x},${node.y + y})`,
        role: "button",
        tabindex: 0,
        "aria-label": label,
        "data-trace-span": id,
      });
      if (expanded !== undefined) button.setAttribute("aria-expanded", String(expanded));
      button.append(svgEl("rect", { width: 34, height: 28, rx: 4 }),
        svgEl("text", { x: 17, y: 19, "text-anchor": "middle" }, icon));
      button.onclick = () => { if (!pointerMoved) action(); };
      button.onkeydown = event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          action();
        }
      };
      nodes.append(button);
      animateCard(button, node, oldPositions.get(node.span_id), x, y);
      if (focused === id) focusTarget = button;
    }
    function animateCard(element, node, old, offsetX = 0, offsetY = 0) {
      if (!animateLayout) return;
      const startY = old?.y ?? node.y - 10;
      const startX = old?.x ?? node.x;
      if (old && startX === node.x && startY === node.y) return;
      moving.push([element, [
        { transform: `translate(${startX + offsetX}px,${startY + offsetY}px)`, opacity: old ? 1 : 0 },
        { transform: `translate(${node.x + offsetX}px,${node.y + offsetY}px)`, opacity: 1 },
      ]]);
    }
    $("edges").replaceChildren(edges);
    $("nodes").replaceChildren(nodes);
    for (const [element, frames] of moving) {
      element.animate(frames, { duration: 200, easing: "ease-out" });
    }
    focusTarget?.focus({ preventScroll: true });
    transform();
    renderInspector();
    if (initialGraphFit && layout.length && $("canvas").clientWidth > 0 && $("canvas").clientHeight > 0) {
      initialGraphFit = false;
      focusNode(selectedNode, index === 0, false);
    } else if (focus || (follow && previous !== selectedSpan)) {
      focusNode(selectedNode, index === 0);
    }
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let cameraRevision = 0;
  function focusNode(node, top = false, animate = true) {
    if (!node || !$("canvas").clientWidth) return;
    const endZoom = zoom;
    moveCamera({
      x: Math.max(24, ($("canvas").clientWidth - 270 * endZoom) / 2) - node.x * endZoom,
      y: (top ? 24 : Math.max(24, ($("canvas").clientHeight - 82 * endZoom) / 2)) - node.y * endZoom,
    }, endZoom, animate);
  }

  function moveCamera(target, targetZoom, animate = true) {
    const revision = ++cameraRevision;
    if (!animate || reducedMotion.matches) {
      pan = target;
      zoom = targetZoom;
      transform();
      return;
    }
    const start = performance.now();
    const initialPan = pan;
    const initialZoom = zoom;
    const duration = 220;
    function draw(now) {
      if (revision !== cameraRevision) return;
      const progress = Math.min(1, (now - start) / duration);
      const eased = progress * progress * (3 - 2 * progress);
      pan = {
        x: initialPan.x + (target.x - initialPan.x) * eased,
        y: initialPan.y + (target.y - initialPan.y) * eased,
      };
      zoom = initialZoom + (targetZoom - initialZoom) * eased;
      transform();
      if (progress < 1) requestAnimationFrame(draw);
    }
    requestAnimationFrame(draw);
  }
  let initialGraphFit = false;
  function transform() {
    $("world").setAttribute(
      "transform",
      `translate(${pan.x},${pan.y}) scale(${zoom})`,
    );
    $("zoomValue").textContent = Math.round(zoom * 100) + "%";
  }
  function fitGraph() {
    if (!layout.length) return;
    const maxX = Math.max(...layout.map((s) => s.x + 290)),
      maxY = Math.max(...layout.map((s) => s.y + 92));
    const fittedZoom = Math.min(
      1,
      ($("canvas").clientWidth - 35) / maxX,
      ($("canvas").clientHeight - 35) / maxY,
    );
    moveCamera({ x: 15, y: 15 }, Math.max(0.1, fittedZoom));
  }
  $("fitGraph").onclick = fitGraph;
  $("foldAllGroups").onclick = () => {
    expandedGroups.clear();
    renderGraph({ anchor: traceSteps[0]?.span_id });
  };
  $("zoomIn").onclick = () => {
    moveCamera(pan, Math.min(2, zoom * 1.2));
  };
  $("zoomOut").onclick = () => {
    moveCamera(pan, Math.max(0.1, zoom / 1.2));
  };
  $("traceSvg").addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      cameraRevision++;
      const rect = $("traceSvg").getBoundingClientRect(),
        x = e.clientX - rect.left,
        y = e.clientY - rect.top,
        old = zoom;
      zoom = Math.min(2, Math.max(0.1, zoom * (e.deltaY > 0 ? 0.9 : 1.1)));
      pan.x = x - ((x - pan.x) * zoom) / old;
      pan.y = y - ((y - pan.y) * zoom) / old;
      transform();
    },
    { passive: false },
  );
  $("traceSvg").onpointerdown = (e) => {
    if (e.button !== 0) return;
    cameraRevision++;
    drag = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
    pointerMoved = false;
  };
  scope.listen(window, "pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x,
      dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) pointerMoved = true;
    if (pointerMoved) {
      pan = { x: drag.px + dx, y: drag.py + dy };
      transform();
    }
  });
  scope.listen(window, "pointerup", () => {
    drag = null;
    setTimeout(() => (pointerMoved = false), 0);
  });
  function chooseStep(index, { pause = true, focus = true } = {}) {
    if (!traceSteps[index]) return;
    if (pause) stopPlay();
    $("followLive").checked = false;
    if (index === 0 && current.result) setInspectorTab("outputs");
    else if (selectedSpan === traceSteps[0].span_id && index > 0) setInspectorTab("inputs");
    selectedSpan = traceSteps[index].span_id;
    renderGraph({ focus });
    return true;
  }

  function renderInspector() {
    const s = traceSteps[Number($("timeline").value) - 1];
    $("expandInspector").disabled = !s;
    if (!s) {
      detailKey = "";
      $("inspectorEmpty").classList.remove("hidden");
      $("inspectorBody").classList.add("hidden");
      return;
    }
    $("inspectorEmpty").classList.add("hidden");
    $("inspectorBody").classList.remove("hidden");
    const [icon, type] = nodeTypes[s.name?.startsWith("search_") ? "search" : s.category] || nodeTypes.node;
    $("inspectorCategory").textContent = `${icon} ${type}`;
    $("inspectorType").textContent =
      s.status === "error"
        ? "ERREUR"
        : s.status === "running"
          ? "EN COURS"
          : s.status === "incomplete"
            ? "FIN NON ENREGISTRÉE"
          : "TERMINÉ";
    $("inspectorName").textContent = s.category === "question" ? "Question utilisateur" : labels[s.name] || s.name;
    $("traceInspectorTitle").textContent = $("inspectorName").textContent;
    $("inspectorSubquestion").textContent = s.subquestion?.label || "";
    $("inspectorSubquestion").hidden = !s.subquestion?.label;
    $("inspectorMeta").textContent =
      `${s.duration_ms === undefined ? (s.status === "running" ? "Durée en cours" : "Durée non enregistrée") : s.duration_ms + " ms"}${s.summary ? " · " + s.summary : ""}`;
    const detail = ioTab === "inputs" ? s : s.completion;
    const key = `${version}/${s.span_id}/${ioTab}/${detail?.seq ?? "-"}/${s.category === "question" ? current.status : ""}`;
    if (detailKey === key) return;
    detailKey = key;
    detailCursor = null;
    detailPath = null;
    $("inspectorData").replaceChildren();
    $("loadMoreDetail").classList.add("hidden");
    $("ioInfo").textContent = "";
    if (s.category === "question") {
      renderQuestionDetail(key);
    } else if (!detail) {
      $("inspectorData").textContent = "Sortie en attente.";
    } else if (current.id === "demo") {
      renderLegacyFields(detail);
    } else if (detail.detail_offset === undefined && detail.legacyOffset !== undefined) {
      loadLegacyDetail(detail);
    } else loadDetail(detail, 0);
  }

  async function renderQuestionDetail(key) {
    const target = $("inspectorData");
    if (ioTab === "inputs") {
      appendDetail({ path: ["question"], text: current.question });
      return;
    }
    const result = current.result;
    if (!result) {
      target.textContent = current.error || (["running", "queued"].includes(current.status)
        ? "Réponse en attente." : "Aucune réponse finale enregistrée.");
      return;
    }
    const answer = document.createElement("div");
    answer.className = "trace-response markdown";
    const text = result.answer || "Aucune réponse étayée disponible.";
    answer.textContent = text;
    target.append(answer);
    if (result.warnings?.length) {
      const warnings = document.createElement("p");
      warnings.textContent = result.warnings.join(" · ");
      target.append(warnings);
    }
    const links = document.createElement("div");
    links.className = "trace-response-sources";
    renderAnswerSources(result.sources || [], links);
    target.append(links);
    try {
      const { renderMarkdown } = await import("../../shared/markdown.js");
      if (key === detailKey) await renderMarkdown(answer, text);
    } catch {
      answer.textContent = text;
    }
  }

  const fieldLabels = {
    inputs: "Entrées", outputs: "Résultat", error: "Erreur", messages: "Messages",
    prompt: "Prompt", content: "Contenu", role: "Rôle", model: "Modèle",
    arguments: "Arguments", question: "Question", answer: "Réponse",
    text: "Texte", score: "Score", document_id: "Document", page: "Page",
  };

  function appendDetail(row) {
    const target = $("inspectorData");
    const path = JSON.stringify(row.path);
    if (row.continued && detailPath === path) {
      target.lastElementChild.querySelector(".detail-value").append(row.text);
      return;
    }
    detailPath = path;
    const field = document.createElement("section");
    field.className = "detail-field";
    const label = document.createElement("h4");
    label.textContent = row.path.map(part => typeof part === "number"
      ? `#${part + 1}` : Object.hasOwn(fieldLabels, part) ? fieldLabels[part] : part.replaceAll("_", " ")).join(" / ");
    const value = document.createElement("div");
    value.className = "detail-value";
    value.textContent = row.text || "—";
    field.append(label, value);
    if (row.source && ["document_id", "page_id"].includes(row.path.at(-1))) {
      const links = document.createElement("div");
      renderAnswerSources([{ ...row.source, locator: { page: row.source.page } }], links);
      field.append(links);
    }
    target.append(field);
  }

  function renderLegacyFields(event) {
    function walk(value, path, source = null) {
      if (value && typeof value === "object" && Object.keys(value).length) {
        if (value.document_id || value.page_id) {
          source = { ...value, page: value.locator?.page ?? value.page };
        }
        for (const [key, child] of Object.entries(value)) {
          walk(child, [...path, Array.isArray(value) ? Number(key) : key], source);
        }
      } else {
        appendDetail({ path, text: String(value ?? "—"), source });
      }
    }
    if (ioTab === "outputs" && event.error) walk(event.error, ["error"]);
    if (event[ioTab] === undefined) $("ioInfo").textContent = "Aucun champ enregistré.";
    else walk(event[ioTab], [ioTab]);
  }

  async function loadLegacyDetail(event) {
    const key = detailKey;
    $("ioInfo").textContent = "Chargement…";
    try {
      const batch = await api(traceEventsUrl(current.id, event.seq, event.legacyOffset));
      if (key !== detailKey) return;
      const full = batch.items.find(item => item.seq === event.seq);
      if (!full) throw Error("Événement introuvable.");
      $("ioInfo").textContent = "";
      renderLegacyFields(full);
    } catch (error) {
      if (key === detailKey) $("ioInfo").textContent = error.message;
    }
  }

  async function loadDetail(event, cursor) {
    const key = detailKey;
    $("loadMoreDetail").disabled = true;
    $("ioInfo").textContent = "Chargement…";
    try {
      const response = await api(`/api/athena/runs/${current.id}/event-detail?seq=${event.seq}&offset=${event.detail_offset}&section=${ioTab}&cursor=${cursor}`);
      if (key !== detailKey) return;
      for (const row of response.rows) appendDetail(row);
      detailCursor = response.next_cursor;
      $("ioInfo").textContent = response.rows.length ? "" : "Aucun champ enregistré.";
    } catch (error) {
      if (key !== detailKey) return;
      if (cursor === 0 && [404, 405].includes(error.status) && event.legacyOffset !== undefined) {
        await loadLegacyDetail(event);
        if (key === detailKey) $("loadMoreDetail").disabled = false;
        return;
      }
      detailCursor = cursor;
      $("ioInfo").textContent = error.message;
    }
    if (key !== detailKey) return;
    $("loadMoreDetail").disabled = false;
    $("loadMoreDetail").classList.toggle("hidden", detailCursor === null);
  }
  $("loadMoreDetail").onclick = () => {
    const s = traceSteps[Number($("timeline").value) - 1];
    const event = ioTab === "inputs" ? s : s?.completion;
    if (event && detailCursor !== null) loadDetail(event, detailCursor);
  };
  const detailTabs = [...document.querySelectorAll("[data-io]")];
  function setInspectorTab(tab) {
    ioTab = tab;
    detailTabs.forEach(button => {
      const active = ioTab === button.dataset.io;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  detailTabs.forEach(button => {
    button.onclick = () => {
      setInspectorTab(button.dataset.io);
      renderInspector();
    };
  });
  $("copyData").onclick = () =>
    navigator.clipboard
      .writeText($("inspectorData").textContent)
      .then(() => toast("Données copiées"))
      .catch(() => toast("La copie n’est pas disponible dans ce navigateur."));
  $("timeline").oninput = () => chooseStep(Number($("timeline").value) - 1);
  $("previousStep").onclick = () => chooseStep(Number($("timeline").value) - 2);
  $("nextStep").onclick = () => chooseStep(Number($("timeline").value));
  $("resetPlay").onclick = () => chooseStep(0);
  function stopPlay() {
    clearTimeout(playing);
    playing = null;
    $("play").textContent = "▶";
  }
  function tick(index) {
    if (!chooseStep(index, { pause: false })) {
      stopPlay();
      return;
    }
    playing = setTimeout(() => tick(index + 1), Number($("speed").value));
  }
  $("play").onclick = () => {
    if (playing) return stopPlay();
    const position = Number($("timeline").value);
    $("play").textContent = "Ⅱ";
    tick(position >= traceSteps.length ? 0 : position);
  };
  $("demoButton").onclick = () => {
    cameraRevision++;
    stopPlay();
    version++;
    events = [];
    selectedSpan = null;
    let seq = 0;
    const t = Date.now() / 1000;
    const add = (kind, extra) =>
      events.push({ kind, seq: seq++, run: "demo", ts: t + seq / 4, ...extra });
    const span = (id, name, category, parent, inputs, outputs) => {
      add("span_start", {
        span_id: id,
        name,
        category,
        parent_id: parent,
        inputs,
      });
      add("span_end", {
        span_id: id,
        name,
        category,
        parent_id: parent,
        status: "ok",
        outputs,
        duration_ms: category === "llm" ? 820 : category === "tool" ? 145 : 24,
      });
    };
    add("span_start", {
      span_id: "n1",
      name: "decompose",
      category: "node",
      inputs: {
        question: "Quelle pression maximale est indiquée dans la notice ?",
      },
    });
    span(
      "m1",
      "llama.cpp · completion",
      "llm",
      "n1",
      { question: "Décomposer la question", model: "Modèle de démonstration" },
      { sub_questions: [{ question: "Trouver la pression maximale" }] },
    );
    add("span_end", {
      span_id: "n1",
      name: "decompose",
      category: "node",
      status: "ok",
      outputs: { sub_questions: ["Trouver la pression maximale"] },
      duration_ms: 840,
    });
    add("span_start", {
      span_id: "n2",
      name: "solve_subquestion",
      category: "node",
      inputs: { question: "Trouver la pression maximale" },
    });
    span(
      "t1",
      "search_text",
      "tool",
      "n2",
      { question: "pression maximale", mode: "hybrid", limit: 8 },
      {
        results: [
          {
            document_id: "demo-notice",
            page_id: "demo-page-12",
            text: "Pression maximale admissible : 12 bar.",
          },
        ],
        synthetic: true,
      },
    );
    span(
      "t2",
      "read_page",
      "tool",
      "n2",
      { id: "demo-page-12" },
      {
        locator: { type: "page", page: 12 },
        blocks: [{ text: "Pression maximale admissible : 12 bar." }],
        synthetic: true,
      },
    );
    span(
      "m2",
      "llama.cpp · completion",
      "llm",
      "n2",
      { instruction: "Extraire le fait et les conditions" },
      { facts: ["Pression maximale : 12 bar."], verdict: "supports" },
    );
    add("span_end", {
      span_id: "n2",
      name: "solve_subquestion",
      category: "node",
      status: "ok",
      outputs: { status: "resolved", answer_value: "12 bar" },
      duration_ms: 1190,
    });
    span(
      "n3",
      "synthesize",
      "node",
      null,
      { facts: ["12 bar"] },
      {
        answer:
          "La pression maximale indiquée est de 12 bar. [Notice fictive, p. 12]",
      },
    );
    span(
      "n4",
      "verify_final",
      "node",
      null,
      { answer: "12 bar", evidence: "Notice fictive, p. 12" },
      { clean: true, gaps: [] },
    );
    current = {
      id: "demo",
      status: "demo",
      question: "Exemple fabriqué · Recherche d’une pression dans une notice",
      started: t,
      ended: t + 2.9,
    };
    layout = [];
    traceSteps = [];
    expandedGroups.clear();
    foldedBranches.clear();
    current.result = { answer: "La pression maximale indiquée est de **12 bar** (notice fictive, p. 12)." };
    if (window.matchMedia("(max-width: 1100px)").matches) setRunsCollapsed(true);
    tracePending = false;
    initialGraphFit = true;
    renderGraph();
    renderRuns();
    toast("Trace de démonstration.");
  };
  $("exportTrace").onclick = async () => {
    if (!current) return;
    const run = current;
    if (run.id !== "demo") {
      if (["running", "queued"].includes(run.status)) {
        toast("L’archive est disponible à la fin de l’exécution.");
        return;
      }
      if (!legacyTrace) {
        const url = `/api/athena/runs/${run.id}/archive`;
        try {
          const response = await fetch(url, { method: "HEAD", signal: scope.signal });
          if (current?.id !== run.id) return;
          if (response.ok) {
            const link = document.createElement("a");
            link.href = url;
            link.click();
            return;
          }
          if (![404, 405].includes(response.status)) throw Error(`Erreur HTTP ${response.status}`);
        } catch (error) {
          if (current?.id === run.id) toast(error.message);
          return;
        }
      }
    }
    const parts = [`{"run":${JSON.stringify(run)},"events":[`];
    try {
      if (run.id === "demo") {
        parts.push(events.map(event => JSON.stringify(event)).join(","));
      } else {
        let after = 0, offset = 0, separator = "";
        for (;;) {
          const page = await api(traceEventsUrl(run.id, after, offset));
          if (current !== run) return;
          for (const event of page.items) {
            parts.push(separator + JSON.stringify(event));
            separator = ",";
          }
          if (!page.has_more) break;
          after = page.next_after;
          offset = page.next_offset;
        }
      }
    } catch (error) {
      toast(error.message);
      return;
    }
    parts.push("]}");
    const blob = new Blob(parts, {
        type: "application/json",
      }),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `athena-${current.id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  $("deleteTrace").onclick = async () => {
    if (!current || current.id === "demo") return;
    if (current.status === "running") {
      toast("Une exécution active ne peut pas être supprimée.");
      return;
    }
    if (
      !confirm(
        "Supprimer définitivement cette trace et sa réponse ? Les sources du RAG sont conservées.",
      )
    )
      return;
    try {
      await api(`/api/athena/runs/${current.id}`, { method: "DELETE" });
      version++;
      current = null;
      events = [];
      layout = [];
      traceSteps = [];
      expandedGroups.clear();
      foldedBranches.clear();
      selectedSpan = null;
      detailKey = "";
      renderGraph();
      $("nodes").replaceChildren();
      $("edges").replaceChildren();
      $("canvasEmpty").classList.remove("hidden");
      $("inspectorBody").classList.add("hidden");
      $("inspectorEmpty").classList.remove("hidden");
      await refreshRuns();
      toast("Trace supprimée.");
    } catch (e) {
      toast(e.message);
    }
  };
  $("ragScope").ontoggle = async () => {
    const column = $("ragScopeColumn");
    if (!$("ragScope").open || !ragCapabilities.metadata || loadingScopeColumns || !column.disabled) return;
    loadingScopeColumns = true;
    try {
      const { columns } = await api("/api/rag/metadata/columns", { method: "POST" });
      for (const { name } of columns) column.add(new Option(name, name));
      column.disabled = false;
    } catch (error) {
      notice("ragNotice", error.message);
    } finally {
      loadingScopeColumns = false;
    }
  };
  $("ragScopeColumn").onchange = () => {
    $("ragScopeValue").disabled = !$("ragScopeColumn").value;
  };
  $("ragForm").onsubmit = async (e) => {
    e.preventDefault();
    notice("ragNotice", "Recherche en cours…");
    const visual = $("ragMode").value === "visual",
      body = {
        question: $("ragQuery").value,
        limit: Number($("ragLimit").value),
      };
    if (!visual) body.mode = $("ragMode").value;
    try {
      const documentId = $("ragScopeDocument").value.trim();
      const column = $("ragScopeColumn").value;
      const value = $("ragScopeValue").value.trim();
      const scoped = visual ? ragCapabilities.visual_search_scope : ragCapabilities.search_scope;
      if ((documentId || column) && !scoped) throw Error("Le mode choisi ne permet pas ce périmètre.");
      if (documentId) body.document_ids = [documentId];
      if (column) {
        if (!value) throw Error("Saisir une valeur pour filtrer les métadonnées.");
        body.metadata_filters = [{ column, op: "contains", value }];
      }
      const result = await api(
        "/api/rag/" + (visual ? "search/visual" : "search"),
        { method: "POST", body: JSON.stringify(body) },
      );
      if ((documentId || column) && result.scope_applied !== true) {
        $("ragResults").replaceChildren();
        $("sourcePreview").replaceChildren();
        throw Error("Le service n’a pas confirmé le périmètre de recherche.");
      }
      const ragDocumentNames = new Map();
      await Promise.all(
        [...new Set(result.results.map((h) => h.document_id))].map(
          async (id) => {
            try {
              const d = await api(
                "/api/rag/documents/" + encodeURIComponent(id),
              );
              ragDocumentNames.set(id, d.observed?.original_name || id);
            } catch {}
          },
        ),
      );
      $("ragResults").innerHTML = "";
      for (const [rank, hit] of result.results.entries()) {
        const b = document.createElement("button");
        b.className = "hit";
        const title =
          hit.observed?.original_name ||
          hit.original_name ||
          ragDocumentNames.get(hit.document_id) ||
          hit.document_id;
        b.innerHTML = `<div class="card-top">SOURCE ${String(rank + 1).padStart(2, "0")} <span>${esc(hit.score === undefined ? "" : Number(hit.score).toFixed(4))}</span></div><p><strong>${esc(title)}</strong></p><p>${esc((hit.text || "Page visuelle · ouvrir la source").slice(0, 280))}</p><div class="meta">${esc(locatorLabel(hit.locator || {}))}</div>`;
        b.onclick = () => {
          document
            .querySelectorAll(".hit")
            .forEach((n) => n.classList.toggle("active", n === b));
          readSource(hit, title);
        };
        $("ragResults").append(b);
      }
      if (!result.results.length)
        $("ragResults").innerHTML =
          '<div class="empty">Aucun résultat pour cette recherche.</div>';
      notice(
        "ragNotice",
        [
          `${result.results.length} résultat(s).`,
          result.retrieval?.effective === "lexical" && "Recherche lexicale utilisée pour ce profil.",
          result.metadata_not_evaluable?.length && "Certains documents ne permettent pas d’évaluer les filtres.",
        ].filter(Boolean).join(" "),
      );
    } catch (e) {
      notice("ragNotice", e.message);
    }
  };
  async function readSource(hit, fallbackName = sourceName(hit)) {
    $("sourcePreview").textContent = "Chargement de la page…";
    try {
      const p = await api("/api/rag/pages/" + encodeURIComponent(hit.page_id));
      $("sourcePreview").replaceChildren();
      const title = document.createElement("div");
      title.className = "card-top";
      title.textContent = p.original_name || p.observed?.original_name || fallbackName;
      $("sourcePreview").append(title);
      const locator = document.createElement("p");
      locator.className = "meta";
      const location = p.locator || hit.locator || {};
      locator.textContent = locatorLabel(location);
      $("sourcePreview").append(locator);
      const pages = p.record?.body_pages?.length
        ? p.record.body_pages
        : [location.page, ...(location.pages || [])];
      const physicalPages = [...new Set(pages.filter((page) => Number.isInteger(page) && page > 0))];
      const documentId = p.document_id || hit.document_id;
      if (ragCapabilities.search_scope && /^d[a-f0-9]{32}$/.test(documentId || "")) {
        const restrict = document.createElement("button");
        restrict.className = "secondary";
        restrict.textContent = "Limiter à ce document";
        restrict.onclick = () => {
          $("ragScope").open = true;
          $("ragScopeDocument").value = documentId;
          $("ragQuery").focus();
        };
        $("sourcePreview").append(restrict);
      }
      if (location.type === "chunk" && documentId && physicalPages.length) {
        const sourceUrl = "/view/documents/" + encodeURIComponent(documentId) + "/source";
        const preview = document.createElement("iframe");
        preview.width = "100%";
        preview.height = "520";
        preview.title = "Page source du PDF";
        preview.src = sourceUrl + "#page=" + physicalPages[0];
        const links = document.createElement("p");
        links.className = "meta";
        for (const page of physicalPages) {
          const link = document.createElement("a");
          link.href = sourceUrl + "#page=" + page;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "Ouvrir la page " + page;
          if (links.childNodes.length) links.append(" · ");
          links.append(link);
        }
        $("sourcePreview").append(links, preview);
      } else if (p.render_state && !p.render_state.available) {
        const reason = document.createElement("p");
        reason.className = p.render_state.code === "no_render" ? "meta" : "preview-error";
        reason.textContent = p.render_state.message;
        $("sourcePreview").append(reason);
      } else if (p.image) {
        const image = document.createElement("img");
        image.alt = "Rendu de la page source";
        image.src = "/api/rag/pages/" + encodeURIComponent(p.id) + "/image";
        image.onerror = () => {
          image.remove();
          const reason = document.createElement("p");
          reason.className = "preview-error";
          reason.textContent = "Le fichier image ne peut pas être affiché. Vérifier son format et l’accès au service RAG ; le texte extrait reste disponible.";
          $("sourcePreview").append(reason);
        };
        $("sourcePreview").append(image);
      }
      const text = document.createElement("pre");
      text.textContent =
        (p.blocks || []).map((b) => b.text || b.html || "").join("\n\n") ||
        "Aucun texte extrait pour cette page.";
      $("sourcePreview").append(text);
    } catch (e) {
      $("sourcePreview").textContent = e.message;
    }
  }
  async function status() {
    for (const [key, path, label] of [
      ["agent", "athena/ready", "Agent"],
      ["rag", "rag/health", "RAG"],
    ]) {
      try {
        const result = await api("/api/" + path);
        if (key === "rag") {
          ragCapabilities = result.capabilities || {};
          $("ragScope").hidden = !(ragCapabilities.search_scope || ragCapabilities.visual_search_scope
            || $("ragScopeDocument").value || $("ragScopeColumn").value);
          if (typeof ragCapabilities.visual === "boolean") {
            $("ragMode").querySelector('[value="visual"]').hidden = !ragCapabilities.visual;
          }
        }
        $(key + "Dot").className = "dot ok";
        $(key + "State").textContent = label + " disponible";
      } catch {
        $(key + "Dot").className = "dot error";
        $(key + "State").textContent =
          key === "agent"
            ? "Agent · dépendance indisponible"
            : "RAG indisponible";
      }
    }
  }
  setInterval(() => {
    $("clock").textContent = new Date().toLocaleTimeString("fr-FR", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }, 1000);
  setInterval(status, 30000);
  status();
  refreshRuns();

  return { api, toast, show, openRun, dispose: () => scope.dispose() };
}
