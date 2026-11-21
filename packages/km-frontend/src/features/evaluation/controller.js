import { createLifecycle } from "../../shared/lifecycle.js";
export function mountEvaluation({ api, toast, show, openRun }) {
  const scope = createLifecycle();
  const { setTimeout, setInterval, requestAnimationFrame } = scope;
  ("use strict");
  const E = (id) => document.getElementById(id),
    h = (s) =>
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
  const base = "/api/athena/eval",
    pct = (v) => (v == null ? "N/E" : `${(100 * v).toFixed(1)} %`),
    num = (v) => (v == null ? "N/E" : Number(v).toFixed(1)),
    state = {
      datasets: [],
      campaigns: [],
      report: null,
      detail: null,
      busy: false,
    };
  const status = (s) =>
    ({
      pending: "En attente",
      running: "En cours",
      complete: "Terminée",
      failed: "Échec",
      interrupted: "Interrompue",
      review: "Revue",
      stopped: "Arrêtée",
      evaluation_error: "Erreur de mesure",
    })[s] || s;
  const outcome = (v) =>
    v === true ? "Réussi" : v === false ? "Échec" : "Non évalué";
  const post = (path, body) =>
    api(base + path, { method: "POST", body: JSON.stringify(body) });
  function message(s) {
    E("evNotice").textContent = s;
    E("evNotice").classList.toggle("hidden", !s);
  }
  async function attempt(fn) {
    try {
      await fn();
      message("");
    } catch (e) {
      message(e.message);
    }
  }
  function download(data, name) {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function refresh() {
    if (state.busy) return;
    state.busy = true;
    try {
      const [d, c] = await Promise.all([
        api(base + "/datasets"),
        api(base + "/campaigns"),
      ]);
      state.datasets = d.items;
      state.campaigns = c.items;
      const prior = E("evDataset").value;
      E("evDataset").innerHTML = state.datasets
        .map(
          (d) =>
            `<option value="${h(d.id)}">${h(d.name)} · ${d.case_count} questions</option>`,
        )
        .join("");
      if (state.datasets.some((d) => d.id === prior))
        E("evDataset").value = prior;
      E("evCampaigns").innerHTML =
        state.campaigns
          .map(
            (c) =>
              `<button data-campaign="${h(c.id)}" class="${state.report?.id === c.id ? "active" : ""}">${h(c.label)}<small>${h(status(c.status))} · ${new Date(c.created * 1000).toLocaleDateString("fr-FR")} · ${c.repetitions}×</small></button>`,
          )
          .join("") || '<div class="empty">Aucune campagne.</div>';
      E("evCampaigns")
        .querySelectorAll("button")
        .forEach(
          (b) => (b.onclick = () => attempt(() => load(b.dataset.campaign))),
        );
      if (state.report) {
        state.report = await api(base + "/campaigns/" + state.report.id);
        renderReport();
      }
    } finally {
      state.busy = false;
    }
  }
  async function load(id) {
    state.detail = null;
    state.report = await api(base + "/campaigns/" + id);
    renderReport();
    await refresh();
  }
  function stat(label, value, detail) {
    return `<div class="eval-stat"><span>${h(label)}</span><strong>${h(value)}</strong><small>${h(detail)}</small></div>`;
  }
  function renderReport() {
    const r = state.report,
      s = r.summary,
      t = s.task_success;
    E("evReport").hidden = false;
    E("evReportTitle").textContent = r.label;
    E("evVersion").textContent =
      `${r.dataset.name} · Corpus déclaré : ${r.configuration.corpus_version} · ${status(r.status)} · Code : ${r.configuration.agent_identity?.source_sha256 || r.configuration.agent_fingerprint}`;
    E("evStop").hidden = r.status !== "running";
    E("evStop").disabled = r.stop_requested;
    E("evStop").textContent = r.stop_requested
      ? "Arrêt demandé"
      : "Arrêter après la question";
    E("evStats").innerHTML =
      stat(
        "Réussite · première répétition",
        pct(t.rate),
        `${t.successes}/${t.assessed} évaluées · couverture ${t.assessed}/${t.total}`,
      ) +
      stat(
        "Recherche · rappel des sources connues",
        pct(s.first_search_known_recall.value),
        `Première recherche · ${s.first_search_known_recall.n} questions annotées`,
      ) +
      stat(
        "Parcours d’outils · jalons satisfaits",
        pct(s.tool_rule_adherence.value),
        `${s.tool_rule_adherence.n} questions avec règles mesurables`,
      ) +
      stat(
        "Latence p50 / p95",
        `${num(s.latency.p50)} / ${num(s.latency.p95)} s`,
        `${s.latency.n} exécutions · échecs inclus`,
      ) +
      stat(
        "Réussite sur toutes les répétitions",
        pct(s.all_repetitions_success.rate),
        `${s.all_repetitions_success.n}/${s.questions} questions entièrement évaluées · ${r.repetitions}×`,
      ) +
      stat(
        "Exécutions terminées",
        `${s.finished_runs}/${s.planned_runs}`,
        `${s.failed_runs} échec(s) · ${s.trace_coverage} traces complètes`,
      );
    const automatic = s.automatic;
    if (automatic) E("evStats").innerHTML +=
      stat("Juge LLM · première appréciation /4", num(automatic.judge_score.value), `${automatic.judge_score.n} jugées · ${automatic.judge_errors} erreurs`) +
      stat("BLEU / ROUGE-L · similarité", `${pct(automatic.bleu.value)} / ${pct(automatic.rougeL_f1.value)}`, "Références partielles · aucune garantie de justesse");
    E("evUncertainty").textContent = t.wilson_95
      ? `IC Wilson 95 % indicatif : ${pct(t.wilson_95[0])} – ${pct(t.wilson_95[1])}. Bornes sur toutes les questions, selon les non-évaluées : ${pct(t.all_question_bounds[0])} – ${pct(t.all_question_bounds[1])}.`
      : "Qualité métier non évaluée. Les critères nécessitent une revue des réponses et des sources.";
    E("evDimensions").innerHTML =
      '<h3>Critères métier · premier essai</h3><table class="eval-table"><thead><tr><th>Dimension</th><th>Satisfaits / évalués</th><th>Partiels</th><th>Échecs</th><th>Inconnus</th></tr></thead><tbody>' +
      Object.entries(s.rubric_dimensions)
        .map(
          ([name, d]) =>
            `<tr><td>${h({ answer: "Réponse", grounding: "Support documentaire", tools: "Outils", retrieval: "Recherche" }[name] || name)}</td><td>${d.pass}/${d.assessed}</td><td>${d.partial}</td><td>${d.fail}</td><td>${d.unknown}</td></tr>`,
        )
        .join("") +
      "</tbody></table>";
    E("evRows").innerHTML = r.rows
      .map(
        (row) =>
          `<tr data-row="${h(row.id)}"><td>${h(row.case_id)} · ${row.repetition}</td><td>${h(row.question)}<small>${h(row.category)} · ${h(row.type_question || "")}</small></td><td>${h(status(row.status))}</td><td class="${row.metrics.outcome === true ? "eval-success" : row.metrics.outcome === false ? "eval-fail" : ""}">${outcome(row.metrics.outcome)}</td><td>${num(row.seconds)}</td><td>${h(row.judge?.score ?? "N/E")}/4 · ${h(row.judge?.status || "non demandé")}</td><td>${pct(row.metrics.bleu)} / ${pct(row.metrics.rougeL_f1)}</td></tr>`,
      )
      .join("");
    E("evRows")
      .querySelectorAll("tr")
      .forEach(
        (tr) => (tr.onclick = () => attempt(() => detail(tr.dataset.row))),
      );
    E("evCompare").innerHTML =
      '<option value="">Comparer à…</option>' +
      state.campaigns
        .filter(
          (c) =>
            c.id !== r.id && c.dataset_fingerprint === r.dataset_fingerprint,
        )
        .map((c) => `<option value="${h(c.id)}">${h(c.label)}</option>`)
        .join("");
    links();
    if (!state.detail) E("evDetail").hidden = true;
  }
  function links() {
    if (!state.report) return;
    for (const format of ["html", "csv", "json"])
      E("evExport" + format).href =
        `${base}/campaigns/${state.report.id}/export?format=${format}&include_content=${E("evInclude").checked}`;
  }
  function selectOptions(values, current) {
    return values
      .map(
        ([v, l]) =>
          `<option value="${v}" ${v === current ? "selected" : ""}>${l}</option>`,
      )
      .join("");
  }
  async function detail(id) {
    const data = await api(`${base}/campaigns/${state.report.id}/rows/${id}`);
    state.detail = data;
    const { row, case: c } = data,
      o = row.observation || {},
      review = row.review || {};
    E("evDetail").hidden = false;
    E("evDetailTitle").textContent = `${c.id} · Répétition ${row.repetition}`;
    E("evQuestion").textContent = `${c.category} · ${c.type_question || "Type non renseigné"} — ${c.question}`;
    const j = row.judge;
    E("evAutomatic").textContent = [
      `Similarité lexicale : BLEU ${pct(row.metrics.bleu)} · ROUGE-1 ${pct(row.metrics.rouge1_f1)} · ROUGE-2 ${pct(row.metrics.rouge2_f1)} · ROUGE-L ${pct(row.metrics.rougeL_f1)}`,
      j ? `Juge LLM : ${j.status} · ${j.model || "modèle inconnu"} · note ${j.score ?? "N/E"}/4 · ${j.verdict || ""}` : "Juge LLM : non demandé",
      j?.same_model_as_agent ? "Même modèle que l’agent. Sources documentaires non vérifiées par le juge." : "",
      j?.rationale || j?.reason || j?.error || "",
      j?.verdict_consistent === false ? "Le verdict textuel du modèle contredisait sa note ; le verdict affiché suit le barème numérique." : "",
      j?.missing_expected?.length ? `Éléments attendus manquants : ${j.missing_expected.join(" ; ")}` : "",
      j?.unsupported_claims?.length ? `Affirmations à vérifier : ${j.unsupported_claims.join(" ; ")}` : "",
    ].filter(Boolean).join("\n\n");
    E("evAnswer").textContent = row.answer || "Aucune réponse disponible.";
    E("evReference").textContent = c.reference_answer
      ? `Référence ${c.reference_status === "partial" ? "partielle" : "complète"} : ${c.reference_answer}`
      : "Aucune réponse de référence : BLEU/ROUGE et jugement comparatif non évaluables.";
    if (c.commentaire) E("evReference").textContent += `\nCommentaire métier : ${c.commentaire}`;
    E("evExecutionError").textContent = row.execution_error || "";
    E("evTrace").hidden = !row.run_id;
    E("evCriteria").innerHTML = c.criteria
      .map(
        (cr) =>
          `<div class="eval-criterion"><label for="criterion-${h(cr.id)}">${h(cr.label)}${cr.required ? " *" : ""}<small class="eval-muted"> · ${h(cr.dimension)}</small></label><select id="criterion-${h(cr.id)}" data-criterion="${h(cr.id)}">${selectOptions(
            [
              ["unknown", "Non évalué"],
              ["pass", "Satisfait"],
              ["partial", "Partiel"],
              ["fail", "Échec"],
            ],
            review.criteria?.[cr.id] || "unknown",
          )}</select></div>`,
      )
      .join("");
    E("evReviewer").value =
      review.reviewer || localStorage.getItem("athena-reviewer") || "";
    E("evReviewNotes").value = review.notes || "";
    E("evTools").innerHTML =
      (o.tools || [])
        .map(
          (t) =>
            `<details><summary>${h(t.tool)} · ${h(t.status)} · ${num(t.duration_ms)} ms</summary><pre>${h(JSON.stringify(t.arguments, null, 2))}</pre><label>Pertinence de cet appel<select data-tool-review="${h(t.span_id)}">${selectOptions(
              [
                ["unknown", "Non évalué"],
                ["appropriate", "Adapté"],
                ["inappropriate", "Inadapté"],
              ],
              review.tools?.[t.span_id] || "unknown",
            )}</select></label></details>`,
        )
        .join("") ||
      '<p class="eval-muted">Aucun appel d’outil documentaire observé.</p>';
    E("evDiagnostic").textContent = JSON.stringify(
      {
        trace_complete: o.trace_complete,
        metrics: row.metrics,
        retrieved: o.searches,
        consulted: o.reads,
        llm_calls: o.llm_calls,
        observed_tokens: o.observed_tokens,
      },
      null,
      2,
    );
    E("evRelevance").value = JSON.stringify(
      review.relevance ?? c.relevance,
      null,
      2,
    );
    E("evExhaustive").checked =
      review.relevance_complete ?? c.relevance_complete;
    E("evRelevanceUnit").textContent =
      `Identifiants ${c.retrieval_unit} : 0 = non pertinent, 1 = pertinent, 2 = central. Top k = ${c.top_k}.`;
    E("evReviewSubmit").disabled = ![
      "complete",
      "failed",
      "interrupted",
    ].includes(row.status);
    E("evAttachArea").hidden =
      !!row.run_id || state.report.status === "running";
    E("evAttachRuns").innerHTML =
      '<option value="">Choisir une trace de la même question</option>';
    if (!row.run_id) {
      const archived = await api("/api/athena/runs");
      const norm = (q) => q.toLocaleLowerCase().trim().replace(/\s+/g, " ");
      E("evAttachRuns").innerHTML += archived.items
        .filter(
          (r) =>
            ["complete", "failed", "interrupted"].includes(r.status) &&
            norm(r.question) === norm(c.question),
        )
        .map(
          (r) =>
            `<option value="${h(r.id)}">${new Date(r.started * 1000).toLocaleString("fr-FR")} · ${h(status(r.status))}</option>`,
        )
        .join("");
    }
    E("evDetail").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  E("evImport").onsubmit = (e) => {
    e.preventDefault();
    attempt(async () => {
      let content = E("evContent").value,
        format = E("evFormat").value;
      const file = E("evFile").files[0];
      if (file) {
        content = await file.text();
        if (file.name.endsWith(".csv")) format = "csv";
        else if (file.name.endsWith(".jsonl")) format = "jsonl";
        else if (file.name.endsWith(".txt")) format = "text";
        else format = "json";
      }
      const data = await post("/datasets", {
        name: E("evDatasetName").value,
        content,
        format,
      });
      await refresh();
      E("evDataset").value = data.id;
      E("evContent").value = "";
      E("evFile").value = "";
      toast(`${data.cases.length} questions importées`);
    });
  };
  E("evTemplate").onclick = () =>
    attempt(async () =>
      download(
        await api(base + "/template"),
        "athena-evaluation-exemple-fabrique.json",
      ),
    );
  E("evDatasetExport").onclick = () =>
    attempt(async () => {
      const d = await api(base + "/datasets/" + E("evDataset").value);
      download(
        { schema_version: d.schema_version, name: d.name, cases: d.cases },
        "athena-questions.json",
      );
    });
  E("evCreate").onsubmit = (e) => {
    e.preventDefault();
    const execute = e.submitter?.value === "execute";
    attempt(async () => {
      const r = await post("/campaigns", {
        dataset_id: E("evDataset").value,
        label: E("evLabel").value,
        corpus_version: E("evCorpus").value,
        repetitions: Number(E("evRepetitions").value),
        seconds: Number(E("evBudget").value),
        search_mode: E("evMode").value,
        execute,
        judge: E("evJudge").checked,
      });
      await refresh();
      await load(r.id);
    });
  };
  E("evRefresh").onclick = () => attempt(refresh);
  E("evInclude").onchange = links;
  E("evStop").onclick = () =>
    attempt(async () => {
      await post(`/campaigns/${state.report.id}/stop`, {});
      await refresh();
    });
  E("evCompare").onchange = () =>
    attempt(async () => {
      if (!E("evCompare").value) {
        E("evCompareResult").textContent = "";
        return;
      }
      const c = await api(
        `${base}/compare?left=${state.report.id}&right=${E("evCompare").value}`,
      );
      E("evCompareResult").textContent =
        `Campagne comparée par rapport à celle affichée : ${c.wins} gains, ${c.losses} pertes, ${c.ties} égalités · ${c.n}/${c.total} questions évaluées des deux côtés. Écart de réussite : ${c.mean_delta == null ? "N/E" : (c.mean_delta * 100).toFixed(1) + " points"}.${c.same_corpus_tag ? "" : " Tags corpus différents."} Juge LLM : écart ${num(c.automatic?.judge_score.mean_delta)}/4 (${c.automatic?.judge_score.n ?? 0} paires). ROUGE-L : écart ${pct(c.automatic?.rougeL_f1.mean_delta)}.`;
    });
  E("evTrace").onclick = () => {
    if (state.detail?.row.run_id) {
      show("traces");
      openRun(state.detail.row.run_id);
    }
  };
  E("evAttach").onclick = () =>
    attempt(async () => {
      if (!E("evAttachRuns").value) throw Error("Sélectionner une trace.");
      const id = state.detail.row.id;
      await post(`/campaigns/${state.report.id}/attach`, {
        row_id: id,
        run_id: E("evAttachRuns").value,
      });
      await refresh();
      await detail(id);
    });
  E("evReview").onsubmit = (e) => {
    e.preventDefault();
    attempt(async () => {
      const row = state.detail.row;
      const criteria = Object.fromEntries(
        [...E("evCriteria").querySelectorAll("select")].map((s) => [
          s.dataset.criterion,
          s.value,
        ]),
      );
      const tools = Object.fromEntries(
        [...E("evTools").querySelectorAll("select")].map((s) => [
          s.dataset.toolReview,
          s.value,
        ]),
      );
      await post(`/campaigns/${state.report.id}/rows/${row.id}/review`, {
        revision: row.review_revision,
        reviewer: E("evReviewer").value,
        notes: E("evReviewNotes").value,
        criteria,
        tools,
        relevance: JSON.parse(E("evRelevance").value),
        relevance_complete: E("evExhaustive").checked,
      });
      localStorage.setItem("athena-reviewer", E("evReviewer").value);
      await refresh();
      await detail(row.id);
      toast("Revue enregistrée");
    });
  };
  scope.listen(window, "console:view", (e) => {
    if (e.detail === "evaluation") attempt(refresh);
  });
  setInterval(() => {
    if (
      E("evaluation").classList.contains("active") &&
      state.report?.status === "running"
    )
      attempt(refresh);
  }, 4000);

  return () => scope.dispose();
}
