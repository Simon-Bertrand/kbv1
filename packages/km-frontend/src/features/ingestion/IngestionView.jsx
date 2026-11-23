import React, { useState, useEffect, useRef } from "react";
import "./ingestion.css";

const DAG_NODES = [
  {
    id: "watcher",
    num: "1",
    title: "Surveillance",
    tech: "watchfiles (DATA_DIR)",
    desc: "Détection continue 24/7 et contrôle de stabilité des fichiers (size & mtime_ns constants).",
    category: "source",
    icon: "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z",
  },
  {
    id: "archive",
    num: "2",
    title: "Copies _versions",
    tech: "source_versions.py",
    desc: "Archivage de la version PDF identifiée par SHA-256 avant son traitement.",
    category: "storage",
    icon: "M8 4H6a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-2m-4-1v8m0 0l3-3m-3 3L9 8m-5 5h2.586a1 1 0 01.707.293l2.414 2.414a1 1 0 00.707.293h3.172a1 1 0 00.707-.293l2.414-2.414a1 1 0 01.707-.293H20",
  },
  {
    id: "pdfparse_extract",
    num: "3",
    title: "Extraction Docling",
    tech: "PdfParse Docling + OCR",
    desc: "Analyse visuelle du layout, détection des titres, paragraphes, code et OCR des blocs textuels.",
    category: "extraction",
    icon: "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
  },
  {
    id: "pdfparse_enrich",
    num: "4",
    title: "Enrichissement",
    tech: "DFC Classif. & Tables MD",
    desc: "Détection des figures et classifieur DFC, conversion tabulaire Markdown, assets dans .pdfparsecache/assets.",
    category: "enrichment",
    icon: "M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z",
  },
  {
    id: "pdfparse_chunk",
    num: "5",
    title: "Chunking Token-Aware",
    tech: "Tokenisation E5 & Géométrie",
    desc: "Découpage conforme aux tokens d'attention E5, préservation des bounding boxes et pages physiques.",
    category: "chunking",
    icon: "M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10",
  },
  {
    id: "cache_publication",
    num: "6",
    title: "Publication Cache",
    tech: "Atomique .pdfparsecache",
    desc: "Écriture dans .cache-<uuid> temporaire, checksums SHA-256 puis publication atomique par os.replace().",
    category: "cache",
    icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4",
  },
  {
    id: "km_sync",
    num: "7",
    title: "KM Indexer Sync",
    tech: "POST /catalog/sync",
    desc: "Vérification fonctionnelle km-indexer-v1, réconciliation delta et profil compatible résolu.",
    category: "catalog",
    icon: "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15",
  },
  {
    id: "km_encode",
    num: "8",
    title: "KM Encoder E5",
    tech: "POST /chunk-jobs (1024-d)",
    desc: "Calcul vectoriel des passages denses E5 sur GPU/CPU avec suivi checkpoints SQLite /state.",
    category: "embedding",
    icon: "M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z",
  },
  {
    id: "rag_publication",
    num: "9",
    title: "PostgreSQL 17",
    tech: "rag.chunks & rag.documents",
    desc: "Vérification de la publication des documents et des chunks pour la recherche hybride.",
    category: "database",
    icon: "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4",
  },
];

function formatEvent(event) {
  const time = new Date(event.timestamp).toLocaleTimeString();
  const payload = event.payload || {};
  if (event.type === "log") {
    return `[${time}] ${payload.level} ${payload.logger} — ${payload.message}`;
  }
  if (event.type === "cycle_finished") {
    const outcome = payload.success ? "terminé" : payload.cancelled ? "interrompu" : "échoué ou incomplet";
    return `[${time}] Cycle ${payload.source} ${outcome}.`;
  }
  return `[${time}] ${event.type} — ${payload.stage || payload.message || ""}`;
}

export default function IngestionView() {
  const [status, setStatus] = useState(null);
  const [files, setFiles] = useState([]);
  const [selectedNode, setSelectedNode] = useState(DAG_NODES[0]);
  const [activeTab, setActiveTab] = useState("node");
  const [streamConnected, setStreamConnected] = useState(false);
  const [logs, setLogs] = useState([]);
  const [showRunModal, setShowRunModal] = useState(false);
  const [modalCacheOnly, setModalCacheOnly] = useState(false);
  const [modalForce, setModalForce] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [stopNotice, setStopNotice] = useState("");
  const [errorNotice, setErrorNotice] = useState("");
  const logContainerRef = useRef(null);

  // Poll status & connect SSE
  useEffect(() => {
    let isMounted = true;
    let loadingStatus = false;

    async function loadInitialStatus() {
      if (loadingStatus) return;
      loadingStatus = true;
      try {
        const res = await fetch("/api/ingestion/status");
        if (res.ok) {
          const data = await res.json();
          if (isMounted) setStatus(data);
        } else if (isMounted) {
          setStatus(null);
        }
      } catch {
        if (isMounted) setStatus(null);
      } finally {
        loadingStatus = false;
      }
    }

    async function loadFiles() {
      try {
        const res = await fetch("/api/ingestion/files");
        if (res.ok) {
          const data = await res.json();
          if (isMounted) setFiles(data.files || []);
        }
      } catch (e) {}
    }

    loadInitialStatus();
    loadFiles();
    const interval = setInterval(() => {
      loadInitialStatus();
      loadFiles();
    }, 4000);

    // Setup SSE stream
    let eventSource;
    try {
      eventSource = new EventSource("/api/ingestion/events");
      eventSource.onopen = () => setStreamConnected(true);
      eventSource.onerror = () => setStreamConnected(false);
      eventSource.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data);
          if (!isMounted) return;
          if (evt.type === "init") {
            setStatus(evt.payload);
            setLogs((evt.recent_events || []).map(formatEvent));
          } else {
            setLogs((prev) => [...prev.slice(-199), formatEvent(evt)]);
            if (evt.type !== "log") loadInitialStatus();
          }
        } catch (err) {}
      };
    } catch (err) {}

    return () => {
      isMounted = false;
      clearInterval(interval);
      if (eventSource) eventSource.close();
    };
  }, []);

  // Auto-scroll logs
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs, activeTab]);

  const lockInfo = status?.lock_info || {};
  const isLocked = lockInfo.locked === true;
  const isBusy = status?.is_busy === true;
  const scheduler = status?.scheduler || {};
  const isInWindow = scheduler.is_in_window === true && !scheduler.is_in_drain;
  const currentRun = status?.current_run;
  const lastRun = status?.history?.at(-1);
  const activeNodeId = status?.active_node;

  async function handleLaunchRun() {
    setIsSubmitting(true);
    setErrorNotice("");
    try {
      const res = await fetch("/api/ingestion/pipeline/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cache_only: modalCacheOnly,
          force: modalForce,
        }),
      });
      const data = await res.json();
      if (res.status === 409) {
        setErrorNotice(data.message || "Exécution bloquée : un verrou est actif.");
      } else if (!res.ok) {
        setErrorNotice(data.message || "Erreur lors du déclenchement de la pipeline.");
      } else {
        setShowRunModal(false);
        setActiveTab("logs");
        // Refresh status
        const sRes = await fetch("/api/ingestion/status");
        if (sRes.ok) setStatus(await sRes.json());
      }
    } catch (err) {
      setErrorNotice("Impossible de joindre le serveur d'ingestion.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleStop() {
    setStopNotice("");
    try {
      const response = await fetch("/api/ingestion/pipeline/stop", { method: "POST" });
      if (!response.ok) {
        setStopNotice((await response.json()).message || "Arrêt refusé.");
      } else {
        setActiveTab("logs");
      }
    } catch {
      setStopNotice("Impossible de joindre le serveur d’ingestion.");
    }
  }

  return (
    <div className="ingestion-view" id="ingestionWorkspace">
      {/* Top Header Bar */}
      <div className="ingestion-header">
        <div className="ingestion-title-group">
          <h2>Orchestrateur d'Ingestion RAG</h2>
          <span className="ingestion-subtitle">
            Surveillance continue, extraction PdfParse et vectorisation PostgreSQL
          </span>
        </div>

        <div className="ingestion-actions">
          {/* Main Manual Launch Button */}
          <button
            className="primary manual-launch-btn"
            disabled={!status || isBusy || isLocked || isSubmitting}
            onClick={() => {
              setErrorNotice("");
              setModalForce(false);
              setModalCacheOnly(status.config?.cache_only === true);
              setShowRunModal(true);
            }}
            title={
              isBusy || isLocked
                ? "Lancement manuel bloqué : tâche automatique en cours sur le GPU/corpus"
                : "Déclencher manuellement un cycle d'ingestion"
            }
          >
            {isBusy || isLocked ? (
              <>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                  <path d="M7 11V7a5 5 0 0110 0v4" />
                </svg>
                Lancement bloqué (Verrou actif)
              </>
            ) : (
              <>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
                Lancer la pipeline manuellement
              </>
            )}
          </button>

          {isBusy && (
            <button className="danger stop-btn" onClick={handleStop} title="Interrompre le cycle en cours">
              Arrêter
            </button>
          )}
        </div>
      </div>

      {stopNotice && <p role="alert">{stopNotice}</p>}
      {!status ? (
        <p role="status">Statut d’ingestion indisponible.</p>
      ) : isBusy || isLocked ? (
        <div className="lock-banner active-locked" role="alert">
          <div className="lock-banner-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0110 0v4" />
            </svg>
          </div>
          <div className="lock-banner-content">
            <strong>Exécution Manuelle Verrouillée</strong>
            <p>
              {lockInfo.message ||
                "Une tâche d'ingestion en arrière-plan tourne actuellement sur le corpus et détient le verrou GPU."}
              {lockInfo.held_by?.pid && (
                <span className="lock-held-meta">
                  (PID : <code>{lockInfo.held_by.pid}</code> sur <code>{lockInfo.held_by.host || "local"}</code>)
                </span>
              )}
            </p>
          </div>
          <span className="badge badge-locked">🔒 BAIL ACTIF</span>
        </div>
      ) : !isInWindow ? (
        <div className="lock-banner window-closed" role="status">
          <div className="lock-banner-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
          </div>
          <div className="lock-banner-content">
            <strong>{scheduler.is_in_drain ? "Drainage GPU en cours" : "Plage horaire GPU fermée"} ({scheduler.raw_window})</strong>
            <p>
              Le démon fonctionne en mode passif : les modifications sont mises en backlog pour libérer la VRAM de l'Agent.
              {!scheduler.is_in_drain && ` Réouverture dans ${Math.round(scheduler.seconds_until_next_open || 0)} s.`}
            </p>
          </div>
          <span className="badge badge-closed">⏳ HORS PLAGE</span>
        </div>
      ) : (
        <div className="lock-banner idle-ready" role="status">
          <div className="lock-banner-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
              <polyline points="22 4 12 14.01 9 11.01" />
            </svg>
          </div>
          <div className="lock-banner-content">
            <strong>Ressources Disponibles & Surveillance Active</strong>
            <p>
              Aucun verrou concurrent actif. Le démon surveille <code>{status?.config?.data_dir || "DATA_DIR"}</code>.
              Vous pouvez déclencher une exécution manuelle à tout moment.
            </p>
          </div>
          <span className="badge badge-ready">🟢 LIBRE</span>
        </div>
      )}

      {/* Directional Graph Canvas (DAG) */}
      <div className="dag-section">
        <div className="dag-section-header">
          <span className="dag-label">Graphe Directionnel d'Ingestion & Publication</span>
          <div className="dag-legend">
            <span className="legend-item"><i className="legend-dot dot-idle" /> En attente</span>
            <span className="legend-item"><i className="legend-dot dot-active" /> Étape active</span>
            
          </div>
        </div>

        <div className="dag-canvas">
          <div className="dag-nodes-track">
            {DAG_NODES.map((node, index) => {
              const isActive = activeNodeId === node.id;
              const isSelected = selectedNode?.id === node.id;
              return (
                <React.Fragment key={node.id}>
                  <button
                    type="button"
                    aria-pressed={isSelected}
                    className={`dag-card ${isActive ? "is-active" : ""} ${isSelected ? "is-selected" : ""}`}
                    onClick={() => {
                      setSelectedNode(node);
                      setActiveTab("node");
                    }}
                  >
                    <div className="dag-card-top">
                      <span className="dag-step-number">{node.num}</span>
                      <svg className="dag-card-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d={node.icon} />
                      </svg>
                    </div>
                    <div className="dag-card-body">
                      <h4>{node.title}</h4>
                      <small>{node.tech}</small>
                    </div>
                    {isActive && <span className="dag-pulse-indicator" />}
                  </button>

                  {index < DAG_NODES.length - 1 && (
                    <div className={`dag-edge ${isActive ? "edge-active" : ""}`}>
                      <svg width="28" height="20" viewBox="0 0 28 20" fill="none">
                        <path d="M0 10 H22" stroke="currentColor" strokeWidth="2" strokeDasharray={isActive ? "4 3" : "none"} />
                        <polygon points="20 6 28 10 20 14" fill="currentColor" />
                      </svg>
                    </div>
                  )}
                </React.Fragment>
              );
            })}
          </div>
        </div>
      </div>

      {/* Bottom Inspector Tabs & Panels */}
      <div className="ingestion-bottom-panel">
        <div className="panel-tab-bar">
          <button
            className={`panel-tab ${activeTab === "node" ? "active" : ""}`}
            onClick={() => setActiveTab("node")}
          >
            Détails de l'étape : {selectedNode?.title}
          </button>
          <button
            className={`panel-tab ${activeTab === "files" ? "active" : ""}`}
            onClick={() => setActiveTab("files")}
          >
            Corpus & Fichiers ({files.length})
          </button>
          <button
            className={`panel-tab ${activeTab === "logs" ? "active" : ""}`}
            onClick={() => setActiveTab("logs")}
          >
            Journal d’ingestion
          </button>
          <button
            className={`panel-tab ${activeTab === "diag" ? "active" : ""}`}
            onClick={() => setActiveTab("diag")}
          >
            Baux & Diagnostics
          </button>
        </div>

        <div className="panel-tab-body">
          {activeTab === "node" && (
            <div className="tab-pane node-pane">
              <div className="node-pane-header">
                <h3>
                  Étape {selectedNode.num} : {selectedNode.title}
                </h3>
                <span className="badge badge-tech">{selectedNode.tech}</span>
              </div>
              <p className="node-pane-desc">{selectedNode.desc}</p>
              <div className="node-metrics-grid">
                <div className="metric-box">
                  <span className="metric-label">Statut de l'étape</span>
                  <strong className="metric-val">
                    {activeNodeId === selectedNode.id ? "Traitement en cours…" : ["pdfparse_enrich", "pdfparse_chunk", "cache_publication"].includes(selectedNode.id) ? "Suivie avec PdfParse" : "Inactive"}
                  </strong>
                </div>
                <div className="metric-box">
                  <span className="metric-label">Ressource requise</span>
                  <strong className="metric-val">
                    {["pdfparse_extract", "km_encode"].includes(selectedNode.id) ? "GPU Accéléré" : "I/O Disque / CPU"}
                  </strong>
                </div>
                <div className="metric-box">
                  <span className="metric-label">Bail associé</span>
                  <strong className="metric-val">
                    {["pdfparse_extract", "km_encode"].includes(selectedNode.id) ? "gpu-all.lock" : "corpus.lock"}
                  </strong>
                </div>
              </div>
            </div>
          )}

          {activeTab === "files" && (
            <div className="tab-pane files-pane">
              <div className="files-table-container">
                <table className="files-table">
                  <thead>
                    <tr>
                      <th>Document PDF</th>
                      <th>Taille</th>
                      <th>Cache PdfParse</th>
                      <th>État d'ingestion</th>
                    </tr>
                  </thead>
                  <tbody>
                    {files.length === 0 ? (
                      <tr>
                        <td colSpan="4" style={{ textAlign: "center", color: "var(--muted)", padding: "20px" }}>
                          Aucun fichier PDF détecté dans le répertoire source.
                        </td>
                      </tr>
                    ) : (
                      files.map((file) => (
                        <tr key={file.path}>
                          <td className="file-name-cell">{file.path}</td>
                          <td>{(file.size_bytes / 1024).toFixed(1)} Ko</td>
                          <td>
                            {file.has_cache ? (
                              <span className="badge badge-ok">Présent</span>
                            ) : (
                              <span className="badge badge-muted">Absent</span>
                            )}
                          </td>
                          <td>
                            <span className={`badge file-state-${file.state}`}>
                              {file.state === "quarantined"
                                ? "Quarantaine"
                                : file.state === "pending"
                                ? "En attente"
                                : file.state === "cached"
                                ? "Prêt (en cache)"
                                : "Nouveau"}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {activeTab === "logs" && (
            <div className="tab-pane logs-pane">
              <p role="status">
                {streamConnected ? "En direct" : "Flux déconnecté, reconnexion en cours"}
                {currentRun && ` · Cycle ${currentRun.source}`}
                {!currentRun && lastRun && ` · Dernier cycle ${lastRun.success ? "terminé" : lastRun.cancelled ? "interrompu" : "échoué ou incomplet"}`}
              </p>
              <div className="log-terminal" ref={logContainerRef}>
                {logs.length === 0 ? (
                  <span className="log-empty">En attente d'événements du pipeline...</span>
                ) : (
                  logs.map((log, i) => (
                    <div key={i} className="log-line">
                      {log}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {activeTab === "diag" && (
            <div className="tab-pane diag-pane">
              <div className="diag-grid">
                <div className="diag-card">
                  <h4>Contrôle de Concurrence & Verrous</h4>
                  <ul>
                    <li>
                      <span>Bail GPU (<code>gpu-all.lock</code>) :</span>
                      <b>{isLocked ? "OCCUPÉ" : "DISPONIBLE"}</b>
                    </li>
                    <li>
                      <span>Processus détenteur :</span>
                      <b>{lockInfo.held_by?.pid || "Aucun"}</b>
                    </li>
                    <li>
                      <span>Fenêtre autorisée :</span>
                      <b>{scheduler.raw_window || "24/7 (Non restreint)"}</b>
                    </li>
                  </ul>
                </div>
                <div className="diag-card">
                  <h4>Variables d'Environnement Ingestion</h4>
                  <ul>
                    <li>
                      <span>Environnement (<code>KB_ENV</code>) :</span>
                      <b>{status?.config?.kb_env || "dev"}</b>
                    </li>
                    <li>
                      <span>Dossier Sources (<code>DATA_DIR</code>) :</span>
                      <b>{status?.config?.data_dir || "-"}</b>
                    </li>
                    <li>
                      <span>URL Indexeur KM :</span>
                      <b>{status?.config?.km_indexer_url || "-"}</b>
                    </li>
                    <li>
                      <span>Profil Embedding :</span>
                      <b>{status?.config?.embedding_profile || "default"}</b>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Manual Run Modal */}
      {showRunModal && (
        <div className="modal-backdrop">
          <div className="modal-dialog" role="dialog" aria-modal="true" aria-labelledby="ingestion-run-title">
            <div className="modal-header">
              <h3 id="ingestion-run-title">Lancer manuellement la pipeline</h3>
              <button className="subtle-button" aria-label="Fermer" onClick={() => setShowRunModal(false)}>
                ✕
              </button>
            </div>
            <div className="modal-body">
              {errorNotice && <div className="modal-error-box">{errorNotice}</div>}
              <p>
                Cette action engage le traitement séquentiel immédiat : préparation PdfParse puis synchronisation
                et vectorisation PostgreSQL.
              </p>

              <label className="modal-checkbox-row">
                <input
                  type="checkbox"
                  checked={modalCacheOnly}
                  onChange={(e) => setModalCacheOnly(e.target.checked)}
                />
                <div>
                  <strong>Mode Cache-Only (<code>--cache-only</code>)</strong>
                  <p>Republie les caches déjà générés sans ré-exécuter l'extraction PdfParse.</p>
                </div>
              </label>

              {!isInWindow && (
                <label className="modal-checkbox-row warning-row">
                  <input
                    type="checkbox"
                    checked={modalForce}
                    onChange={(e) => setModalForce(e.target.checked)}
                  />
                  <div>
                    <strong>Forcer l'exécution hors plage horaire</strong>
                    <p>Dérogation explicite aux restrictions de fenêtre GPU.</p>
                  </div>
                </label>
              )}
            </div>
            <div className="modal-footer">
              <button className="secondary" onClick={() => setShowRunModal(false)}>
                Annuler
              </button>
              <button className="primary" disabled={!status || isBusy || isLocked || isSubmitting || (!isInWindow && !modalForce)} onClick={handleLaunchRun}>
                {isSubmitting ? "Démarrage…" : "Confirmer et lancer"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
