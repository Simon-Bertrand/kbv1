import { filesystemGraph, atlasHeaders, rawValue } from "./graph.js";
import { createLifecycle } from "../../shared/lifecycle.js";
export function mountPage(root = document) {
  const scope = createLifecycle();
  const { requestAnimationFrame } = scope;

  ("use strict");
  const $ = (s) => root.querySelector(s),
    c = $("canvas"),
    ctx = c.getContext("2d");
  const styles = getComputedStyle(root.host || document.documentElement);
  const color = (name, fallback) =>
    styles.getPropertyValue(name).trim() || fallback;
  const palette = {
    label: color("--canvas-label", "#526b7d"),
    edge: color("--canvas-edge", "#aebbc565"),
    mutedEdge: color("--canvas-edge-muted", "#aebbc526"),
    activeEdge: color("--canvas-active-edge", "#1675ebbb"),
    active: color("--cyan", "#1675eb"),
    node: color("--canvas-node", "#7b919f"),
    missing: color("--canvas-missing", "#cc9956"),
  };
  let ns = [],
    es = [],
    adj = [],
    p = [],
    headers = atlasHeaders,
    edgeKinds = [],
    currentGraph,
    data = {files:[],versions:[],references:[]},
    nextOffset = 0,
    total = 0,
    loading,
    detailVersion = 0,
    worker,
    selected = -1,
    near = new Set(),
    hover = -1,
    W = 0,
    H = 0,
    view = { x: 0, y: 0, k: 1 },
    anim,
    dirty = true,
    drag,
    moved = false;
  let clusterAnchors = [];

  // Les regroupements utilisent des valeurs brutes, sans décoder les codes métier.
  function buildClusters(nodes, columns, mode) {
    if (mode === "links") return { anchors: [], targets: [] };
    const column = columns.findIndex(
      (name) => name === mode,
    );
    const memberships = nodes.map((node) => {
      if (!node.row) return ["Identité hors extrait"];
      const value = column < 0 ? "" : (node.row[column] || "").trim();
      if (!value) return ["Non renseigné"];
      return [value];
    });
    const names = [...new Set(memberships.flat())].sort((a, b) =>
      a.localeCompare(b, "fr"),
    );
    const counts = new Map(names.map((name) => [name, 0]));
    memberships.forEach((list) =>
      list.forEach((name) => counts.set(name, counts.get(name) + 1)),
    );
    const radius = Math.max(
      450,
      Math.sqrt(nodes.length) * 20,
      names.length * 130,
    );
    const anchors = names.map((name, i) => ({
      name,
      x: Math.cos((i * Math.PI * 2) / names.length) * radius,
      y: Math.sin((i * Math.PI * 2) / names.length) * radius,
      count: counts.get(name),
    }));
    const lookup = new Map(anchors.map((anchor) => [anchor.name, anchor]));
    const targets = memberships.map((list) => {
      const points = list.map((name) => lookup.get(name));
      return [
        points.reduce((sum, point) => sum + point.x, 0) / points.length,
        points.reduce((sum, point) => sum + point.y, 0) / points.length,
      ];
    });
    return { anchors, targets };
  }

  function layout() {
    onmessage = ({ data: { n, edges, targets = [] } }) => {
      let p = new Float32Array(n * 2),
        v = new Float32Array(n * 2),
        a = Array.from({ length: n }, () => []),
        visited = new Uint8Array(n),
        groups = [];
      for (let [i, j] of edges) {
        a[i].push(j);
        a[j].push(i);
      }
      for (let i = 0; i < n; i++)
        if (!visited[i]) {
          let q = [i];
          visited[i] = 1;
          for (let k = 0; k < q.length; k++)
            for (let j of a[q[k]])
              if (!visited[j]) {
                visited[j] = 1;
                q.push(j);
              }
          groups.push(q);
        }
      groups.sort((a, b) => b.length - a.length);
      let side =
          Math.sqrt(
            groups.reduce(
              (s, g) =>
                s + (Math.max(22, Math.sqrt(g.length) * 9) * 2 + 18) ** 2,
              0,
            ),
          ) * 1.2,
        x = 0,
        y = 0,
        rh = 0;
      for (let g of groups) {
        let r = Math.max(22, Math.sqrt(g.length) * 9),
          box = 2 * r + 18;
        if (x + box > side) {
          x = 0;
          y += rh;
          rh = 0;
        }
        g.forEach((i, k) => {
          let angle = k * 2.399963,
            rad = r * Math.sqrt((k + 0.5) / g.length);
          p[i * 2] = x + r + Math.cos(angle) * rad;
          p[i * 2 + 1] = y + r + Math.sin(angle) * rad;
        });
        x += box;
        rh = Math.max(rh, box);
      }
      if (targets.length) {
        const occurrences = new Map();
        for (let i = 0; i < n; i++) {
          const key = targets[i].join(":");
          const rank = occurrences.get(key) || 0;
          occurrences.set(key, rank + 1);
          const angle = rank * 2.399963;
          const radius = Math.sqrt(rank + 1) * 8;
          p[2 * i] = targets[i][0] + Math.cos(angle) * radius;
          p[2 * i + 1] = targets[i][1] + Math.sin(angle) * radius;
        }
      }
      postMessage({ p: p.slice() });
      for (let step = 0; step < 240; step++) {
        let grid = new Map();
        for (let i = 0; i < n; i++) {
          let key =
            Math.floor(p[2 * i] / 32) + "," + Math.floor(p[2 * i + 1] / 32);
          if (!grid.has(key)) grid.set(key, []);
          grid.get(key).push(i);
        }
        for (let i = 0; i < n; i++) {
          let gx = Math.floor(p[2 * i] / 32),
            gy = Math.floor(p[2 * i + 1] / 32),
            budget = 100;
          for (let dx = -1; dx <= 1; dx++)
            for (let dy = -1; dy <= 1; dy++)
              for (let j of grid.get(gx + dx + "," + (gy + dy)) || []) {
                if (i === j || budget-- <= 0) continue;
                let x = p[2 * i] - p[2 * j],
                  y = p[2 * i + 1] - p[2 * j + 1],
                  f = Math.min(2, 35 / (x * x + y * y + 0.1));
                v[2 * i] += x * f;
                v[2 * i + 1] += y * f;
              }
        }
        for (let [i, j] of edges) {
          let x = p[2 * j] - p[2 * i],
            y = p[2 * j + 1] - p[2 * i + 1],
            d = Math.hypot(x, y) || 1,
            f = (d - 24) * (targets.length ? 0.00015 : 0.008);
          v[2 * i] += (x / d) * f;
          v[2 * i + 1] += (y / d) * f;
          v[2 * j] -= (x / d) * f;
          v[2 * j + 1] -= (y / d) * f;
        }
        if (targets.length) {
          for (let i = 0; i < n; i++) {
            v[2 * i] += (targets[i][0] - p[2 * i]) * 0.025;
            v[2 * i + 1] += (targets[i][1] - p[2 * i + 1]) * 0.025;
          }
        }
        for (let k = 0; k < p.length; k++) {
          v[k] *= 0.65;
          p[k] += Math.max(-4, Math.min(4, v[k])) * (1 - step / 280);
        }
        if (step % 24 === 23) postMessage({ p: p.slice(), done: step === 239 });
      }
    };
  }
  function load(g) {
    worker?.terminate();
    ns = g.nodes;
    es = g.edges;
    edgeKinds = g.edgeKinds;
    currentGraph = g;
    detailVersion++;
    adj = ns.map(() => []);
    for (let [a, b] of es) {
      adj[a].push(b);
      adj[b].push(a);
    }
    adj = adj.map(list => [...new Set(list)]);
    p = new Float32Array(ns.length * 2);
    selected = hover = -1;
    near.clear();
    for (let s of ["aside", "#popup", "#tip", "#results"]) $(s).hidden = true;
    $("#search").value = "";
    $("#empty").hidden = !!ns.length;
    $("#status").hidden = false;
    let stats = `${ns.filter(n=>n.kind==='file').length.toLocaleString("fr")} fichiers affichés / ${total.toLocaleString("fr")} dans le corpus · ${ns.filter(n=>n.kind==='version').length} versions · ${ns.filter(n=>n.kind==='parent').length} identités · ${edgeKinds.filter(k=>k==='reference').length} références déclarées`;
    if (g.omitted) stats += " · limite atteinte : extrait partiel";
    if (g.unresolved) stats += ` · ${g.unresolved} références sans uuid exploitable`;
    if (data.references_limited) stats += " · références partiellement chargées (limite API)";
    $("#status").textContent = stats + " · Placement…";
    if (!ns.length) {
      $("#status").textContent = "Aucun fichier sous parent/V…@…/files/. Aucune indexation requise.";
      $("#emptyMessage").textContent = "Aucun fichier détecté dans la structure KM. Vérifier DATA_DIR. Les métadonnées sont accessibles avant l’indexation.";
      dirty = true;
      return;
    }
    let url = URL.createObjectURL(
      new Blob(["(" + layout.toString() + ")()"], { type: "text/javascript" }),
    );
    worker = new Worker(url);
    URL.revokeObjectURL(url);
    let first = true;
    worker.onmessage = (e) => {
      p = e.data.p;
      if (first) {
        fit(false);
        first = false;
      }
      if (e.data.done) {
        $("#status").textContent = stats;
        worker.terminate();
        worker = null;
      }
      dirty = true;
    };
    worker.onerror = () =>
      ($("#status").textContent =
        "Placement indisponible : vérifier le support des Web Workers.");
    const clusters = buildClusters(ns, headers, $("#grouping").value);
    clusterAnchors = clusters.anchors;
    worker.postMessage({ n: ns.length, edges: es, targets: clusters.targets });
  }
  function target(to, smooth = true) {
    anim = smooth ? { from: { ...view }, to, start: performance.now() } : null;
    if (!smooth) view = to;
    dirty = true;
  }
  function fit(smooth = true) {
    if (!ns.length) return;
    let x0 = Infinity,
      x1 = -Infinity,
      y0 = Infinity,
      y1 = -Infinity;
    for (let i = 0; i < ns.length; i++) {
      x0 = Math.min(x0, p[i * 2]);
      x1 = Math.max(x1, p[i * 2]);
      y0 = Math.min(y0, p[i * 2 + 1]);
      y1 = Math.max(y1, p[i * 2 + 1]);
    }
    let k = Math.max(
      0.005,
      Math.min((W - 80) / (x1 - x0 + 30), (H - 80) / (y1 - y0 + 30), 3),
    );
    target(
      { k, x: W / 2 - ((x0 + x1) / 2) * k, y: H / 2 - ((y0 + y1) / 2) * k },
      smooth,
    );
  }
  function pt(i) {
    return [p[2 * i] * view.k + view.x, p[2 * i + 1] * view.k + view.y];
  }
  function hit(x, y) {
    let best = -1,
      dist = 100;
    for (let i = 0; i < ns.length; i++) {
      let [a, b] = pt(i),
        d = (a - x) ** 2 + (b - y) ** 2;
      if (d < dist) {
        best = i;
        dist = d;
      }
    }
    return best;
  }
  const resizeObserver = new ResizeObserver(() => {
    W = c.clientWidth;
    H = c.clientHeight;
    let d = Math.min(devicePixelRatio || 1, 2);
    c.width = W * d;
    c.height = H * d;
    ctx.setTransform(d, 0, 0, d, 0, 0);
    dirty = true;
  });
  resizeObserver.observe(c);
  function draw(now) {
    requestAnimationFrame(draw);
    if (anim) {
      let t = Math.min(1, (now - anim.start) / 500),
        e = 1 - (1 - t) ** 3;
      for (let k of ["x", "y", "k"])
        view[k] = anim.from[k] + (anim.to[k] - anim.from[k]) * e;
      if (t === 1) anim = null;
      dirty = true;
    }
    if (!dirty) return;
    dirty = false;
    ctx.clearRect(0, 0, W, H);
    ctx.font = "13px Segoe UI, sans-serif";
    ctx.textAlign = "center";
    ctx.fillStyle = palette.label;
    for (const anchor of clusterAnchors) {
      const x = anchor.x * view.k + view.x;
      const y = anchor.y * view.k + view.y - 30;
      ctx.fillText(anchor.name + " · " + anchor.count, x, y);
    }
    ctx.textAlign = "left";
    ctx.strokeStyle = selected < 0 ? palette.edge : palette.mutedEdge;
    ctx.lineWidth = 0.7;
    ctx.beginPath();
    for (let index=0;index<es.length;index++) {
      if(edgeKinds[index] === "reference") continue;
      const [a,b]=es[index];
      let [x, y] = pt(a),
        [u, v] = pt(b);
      if (
        Math.max(x, u) < 0 ||
        Math.min(x, u) > W ||
        Math.max(y, v) < 0 ||
        Math.min(y, v) > H
      )
        continue;
      ctx.moveTo(x, y);
      ctx.lineTo(u, v);
    }
    ctx.stroke();
    ctx.strokeStyle=palette.activeEdge;
    ctx.setLineDash([3,4]);
    for(let k=0;k<es.length;k++) {
      if(edgeKinds[k]!=="reference")continue;
      const [a,b]=es[k],[x,y]=pt(a),[u,v]=pt(b),angle=Math.atan2(v-y,u-x);
      ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(u,v);ctx.stroke();
      ctx.beginPath();ctx.moveTo(u-5*Math.cos(angle),v-5*Math.sin(angle));
      ctx.lineTo(u-12*Math.cos(angle-.4),v-12*Math.sin(angle-.4));
      ctx.moveTo(u-5*Math.cos(angle),v-5*Math.sin(angle));ctx.lineTo(u-12*Math.cos(angle+.4),v-12*Math.sin(angle+.4));ctx.stroke();
    }
    ctx.setLineDash([]);
    if (selected >= 0) {
      ctx.strokeStyle = palette.activeEdge;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      let [x, y] = pt(selected);
      for (let j of adj[selected]) {
        let [u, v] = pt(j);
        ctx.moveTo(x, y);
        ctx.lineTo(u, v);
      }
      ctx.stroke();
    }
    for (let i = 0; i < ns.length; i++) {
      let [x, y] = pt(i);
      if (x < -10 || x > W + 10 || y < -10 || y > H + 10) continue;
      let active = i === selected || near.has(i);
      ctx.globalAlpha = selected < 0 || active ? 1 : 0.23;
      ctx.fillStyle = active
        ? palette.active
        : ns[i].row
          ? (ns[i].kind === "parent" ? palette.active : palette.node)
          : palette.missing;
      ctx.beginPath();
      ctx.arc(
        x,
        y,
        i === selected
          ? 6
          : i === hover
            ? 5
            : Math.max(1.7, Math.min(3.5, 2 * view.k)),
        0,
        Math.PI * 2,
      );
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  requestAnimationFrame(draw);
  function choose(i, x, y) {
    selected = i;
    near = new Set(adj[i]);
    dirty = true;
    $("#name").textContent = ns[i].name;
    $("#count").textContent =
      `${adj[i].length} connexions${ns[i].row ? "" : " · identité hors extrait"}`;
    $("#popup").hidden = false;
    $("#popup").style.left = Math.max(8, Math.min(x, innerWidth - 330)) + "px";
    $("#popup").style.top = Math.max(8, Math.min(y, innerHeight - 150)) + "px";
  }
  function open(i) {
    selected = i;
    near = new Set(adj[i]);
    $("#popup").hidden = $("#tip").hidden = true;
    $("aside").hidden = false;
    $("#title").textContent = ns[i].name;
    $("#info").textContent = "Hiérarchie filesystem et références déclarées. Les codes, dates numériques et estLie restent bruts ; aucune version cible n’est déduite.";
    const node=ns[i];
    renderFields(node.fields);
    $("#source").hidden = node.kind !== "file";
    if(node.kind === "file") $("#source").href="/api/files/raw?path="+encodeURIComponent(node.path);
    if(node.path) showRawDetails(node,i);
    $("#neighbors").replaceChildren();
    for (let j of adj[i]) {
      let b = document.createElement("button");
      b.textContent = ns[j].name + (ns[j].row ? "" : " · hors extrait");
      b.onclick = () => open(j);
      $("#neighbors").append(b);
    }
    if (!adj[i].length) $("#neighbors").textContent = "Aucune connexion.";
    let k = Math.max(view.k, 2.5),
      available = W - Math.min(430, W * 0.5);
    target({ k, x: available / 2 - p[2 * i] * k, y: H / 2 - p[2 * i + 1] * k });
  }
  function clear() {
    selected = -1;
    near.clear();
    $("#popup").hidden = true;
    dirty = true;
  }
  c.onpointerdown = (e) => {
    anim = null;
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    moved = false;
    c.setPointerCapture(e.pointerId);
    $("#popup").hidden = true;
  };
  c.onpointermove = (e) => {
    if (drag) {
      let dx = e.clientX - drag.x,
        dy = e.clientY - drag.y;
      if (Math.hypot(dx, dy) > 4) moved = true;
      if (moved) {
        view.x = drag.vx + dx;
        view.y = drag.vy + dy;
        dirty = true;
        $("#tip").hidden = true;
      }
      return;
    }
    let r = c.getBoundingClientRect();
    hover = hit(e.clientX - r.left, e.clientY - r.top);
    c.style.cursor = hover < 0 ? "grab" : "pointer";
    $("#tip").hidden = hover < 0;
    if (hover >= 0) {
      $("#tip").textContent = ns[hover].name;
      $("#tip").style.left =
        Math.max(8, Math.min(e.clientX + 14, innerWidth - 330)) + "px";
      $("#tip").style.top = Math.min(e.clientY + 14, innerHeight - 70) + "px";
    }
    dirty = true;
  };
  c.onpointerup = c.onpointercancel = () => (drag = null);
  c.onpointerleave = () => {
    $("#tip").hidden = true;
    hover = -1;
    dirty = true;
  };
  c.onclick = (e) => {
    if (moved) return;
    let r = c.getBoundingClientRect(),
      i = hit(e.clientX - r.left, e.clientY - r.top);
    if (i >= 0) choose(i, e.clientX + 12, e.clientY + 12);
    else clear();
  };
  c.ondblclick = (e) => {
    if (moved) return;
    let r = c.getBoundingClientRect(),
      i = hit(e.clientX - r.left, e.clientY - r.top);
    if (i >= 0) open(i);
  };
  function zoom(f, x = W / 2, y = H / 2) {
    anim = null;
    let k = Math.max(0.005, Math.min(25, view.k * f)),
      ratio = k / view.k;
    view = { k, x: x - (x - view.x) * ratio, y: y - (y - view.y) * ratio };
    dirty = true;
    $("#popup").hidden = true;
  }
  scope.listen(
    c,
    "wheel",
    (e) => {
      e.preventDefault();
      let r = c.getBoundingClientRect();
      zoom(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    },
    { passive: false },
  );
  $("#plus").onclick = () => zoom(1.4);
  $("#minus").onclick = () => zoom(1 / 1.4);
  $("#fit").onclick = () => {
    $("aside").hidden = true;
    $("#popup").hidden = true;
    fit();
  };
  $("#open").onclick = () => {
    if (selected >= 0) open(selected);
  };
  $("#close").onclick = () => ($("aside").hidden = true);
  scope.listen(root, "keydown", (e) => {
    if (e.key === "Escape") {
      $("aside").hidden = $("#results").hidden = true;
      clear();
    }
  });
  $("#search").oninput = () => {
    let q = $("#search").value.trim().toLocaleLowerCase();
    $("#results").replaceChildren();
    $("#results").hidden = !q;
    if (!q) return;
    let matches = ns
      .map((n, i) => ({ n, i }))
      .filter(({ n }) => n.name.toLocaleLowerCase().includes(q))
      .slice(0, 30);
    for (let { n, i } of matches) {
      let b = document.createElement("button");
      b.textContent = n.name;
      b.onclick = () => {
        open(i);
        $("#results").hidden = true;
      };
      $("#results").append(b);
    }
    if (!matches.length) $("#results").textContent = "Aucun fichier trouvé.";
  };
  function renderFields(fields) {
    const fragment=document.createDocumentFragment();
    for(const [key,value] of Object.entries(fields || {})) {
      const dt=document.createElement("dt"),dd=document.createElement("dd");
      dt.textContent=key;dd.textContent=rawValue(value);fragment.append(dt,dd);
    }
    $("#meta").replaceChildren(fragment);
  }
  async function showRawDetails(node,index) {
    const version=++detailVersion;
    try {
      const response=await fetch("/api/knowledge/metadata/filesystem?path="+encodeURIComponent(node.path),{signal:scope.signal});
      if(!response.ok)throw Error("Fiche brute indisponible ; vérifier les métadonnées de cette version.");
      const value=await response.json();
      if(scope.disposed || detailVersion!==version || selected!==index)return;
      const {info:summaryInfo,version:summaryVersion,...identityFields}=node.fields;
      renderFields({...identityFields,info_json:value.info,version_json:value.version,
        documentsLiees_json:value.linked_documents,metadata_csv_brut:value.csv_row,source_paths:value.source_paths,warnings:value.warnings});
    } catch(error) {
      if(!scope.disposed && detailVersion===version && selected===index && error.name!=="AbortError") $("#info").textContent=error.message;
    }
  }
  async function refresh(reset=false) {
    loading?.abort();
    const controller=new AbortController();loading=controller;
    const offset=reset?0:nextOffset;
    if(offset===null)return;
    $("#import").disabled=$("#more").disabled=$("#start").disabled=true;
    $("#status").hidden=false;$("#status").textContent="Lecture des métadonnées filesystem…";
    try {
      const response=await fetch("/api/knowledge/metadata/atlas?limit=200&offset="+offset,{signal:controller.signal});
      if(!response.ok)throw Error("Atlas indisponible : vérifier le montage du corpus et la version de l’API KM.");
      const page=await response.json();
      if(scope.disposed || controller!==loading)return;
      const previous=reset?{files:[],versions:[],references:[],references_limited:false}:data;
      const files=new Map([...previous.files,...page.files].map(file=>[file.file_id,file]));
      const refs=new Map([...previous.references,...page.references].map(ref=>[JSON.stringify([ref.source_version_id,ref.source,ref.ordinal]),ref]));
      const next={files:[...files.values()],versions:[...previous.versions,...page.versions],references:[...refs.values()],
        references_limited:previous.references_limited || page.references_omitted>0};
      const graph=filesystemGraph(next);
      data=next;total=page.total;nextOffset=page.next_offset;
      $("#more").hidden=nextOffset===null || graph.omitted>0 || data.files.length>=5000;
      if(data.files.length>=5000)graph.omitted++;
      load(graph);
    } catch(error) {
      if(error.name!=="AbortError" && !scope.disposed && controller===loading) {
        $("#status").textContent=error.message;$("#emptyMessage").textContent=error.message;
      }
    } finally {
      if(!scope.disposed && controller===loading) $("#import").disabled=$("#more").disabled=$("#start").disabled=false;
    }
  }
  $("#import").onclick=$("#start").onclick=()=>refresh(true);
  $("#more").onclick=()=>refresh();
  $("#grouping").onchange=()=>{if(currentGraph)load(currentGraph);};
  refresh(true);

  return () => {
    loading?.abort();
    scope.dispose();
    resizeObserver.disconnect();
    worker?.terminate();
  };
}
