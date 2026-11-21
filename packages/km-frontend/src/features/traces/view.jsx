import React, { memo } from "react";

const TracesView = memo(function TracesView() {
  return (
    <>
      <section id="traces" className="view trace-view">
        <div className="trace-top">
          <div>
            <h1>{"Suivi"}</h1>
          </div>
          <div className="trace-actions">
            <button id="toggleRuns" className="secondary" aria-expanded="true">Exécutions</button>
            <button id="demoButton" className="secondary">Démonstration</button>
          </div>
        </div>
        <div className="trace-workspace">
          <aside id="traceRuns" className="run-list">
            <div className="panel-title">
              {"Exécutions "}
              <button id="refreshRuns" title="Actualiser">
                {"↻"}
              </button>
            </div>
            <input
              id="runSearch"
              aria-label="Filtrer les traces"
              placeholder="Filtrer les questions…"
            />
            <div id="runsList"></div>
          </aside>
          <div className="graph-area">
            <div className="graph-top">
              <div>
                <span id="traceStatus" className="badge">
                  {"AUCUNE SÉLECTION"}
                </span>
                <strong id="traceQuestion">
                  {"Sélectionnez une exécution"}
                </strong>
              </div>
              <div className="graph-actions">
                <button id="exportTrace" title="Télécharger la trace archivée">Exporter</button>
                <button
                  id="deleteTrace"
                  title="Supprimer la trace sélectionnée"
                >
                  {"Supprimer"}
                </button>
              </div>
            </div>
            <div className="graph-stats">
              <span>
                <b id="nodeCount">{"0"}</b>
                {" étapes"}
              </span>
              <span>
                <b id="toolCount">{"0"}</b>
                {" outils"}
              </span>
              <span>
                <b id="llmCount">{"0"}</b>
                {" appels LLM"}
              </span>
              <span id="traceDuration">{"—"}</span>
              <div className="legend">
                <i className="node-key"></i>
                {"Nœud"}
                <i className="tool-key"></i>
                {"Outil"}
                <i className="llm-key"></i>
                {"LLM"}
              </div>
            </div>
            <div className="trace-navigation">
              <p id="traceSummary" role="status"></p>
            </div>
            <div id="canvas" className="canvas">
              <svg
                id="traceSvg"
                aria-label="Graphe interactif des événements"
                role="group"
              >
                <defs>
                  <pattern
                    id="dots"
                    width="24"
                    height="24"
                    patternUnits="userSpaceOnUse"
                  >
                    <circle cx="1" cy="1" r="1" fill="var(--line)"></circle>
                  </pattern>
                </defs>
                <rect width="100%" height="100%" fill="url(#dots)"></rect>
                <g id="world">
                  <g id="edges"></g>
                  <g id="nodes"></g>
                </g>
              </svg>
              <div id="canvasEmpty" className="canvas-empty">
                
                <h3>{"Aucune trace sélectionnée."}</h3>
                <p></p>
              </div>
              <div className="zoom-controls">
                <button id="zoomOut" aria-label="Dézoomer">
                  {"−"}
                </button>
                <span id="zoomValue">{"100%"}</span>
                <button id="zoomIn" aria-label="Zoomer">
                  {"+"}
                </button>
                <button id="foldAllGroups" title="Replier tous les groupes" aria-label="Replier tous les groupes" hidden>⊟</button>
                <button id="fitGraph" title="Cadrer le graphe">
                  {"⊡"}
                </button>
              </div>
            </div>
            <div className="playback">
              <button
                id="play"
                className="play-button"
                aria-label="Lire l’animation"
              >
                {"▶"}
              </button>
              <button id="resetPlay" title="Revenir au début">
                {"↤"}
              </button>
              <button id="previousStep" aria-label="Étape précédente" title="Étape précédente">←</button>
              <button id="nextStep" aria-label="Étape suivante" title="Étape suivante">→</button>
              <input
                id="timeline"
                type="range"
                min="1"
                max="1"
                defaultValue="1"
                aria-label="Position dans la trace"
              />
              <span id="eventPosition" className="mono">
                {"0 / 0"}
              </span>
              <select
                id="speed"
                aria-label="Vitesse de lecture"
                defaultValue="1000"
              >
                <option value="1000">{"1×"}</option>
                <option value="500">{"2×"}</option>
                <option value="200">{"5×"}</option>
              </select>
              <label className="follow">
                <input type="checkbox" id="followLive" defaultChecked={true} />
                {" Suivre en direct"}
              </label>
            </div>
          </div>
          <aside className="inspector" aria-label="Détails du nœud">
            <div className="panel-title">
              {"Détails "}
              <span id="inspectorCategory">{"—"}</span>
              <button id="expandInspector" type="button" aria-haspopup="dialog" aria-controls="traceInspectorDialog" disabled>Agrandir</button>
            </div>
            <div id="inspectorEmpty" className="empty">
              {"Cliquez sur une activité pour consulter ses détails."}
            </div>
            <div id="inspectorDock">
              <div id="inspectorBody" className="hidden">
                <div id="inspectorType" className="node-type-label"></div>
                <h3 id="inspectorName"></h3>
                <p id="inspectorSubquestion" className="trace-subquestion-context" hidden></p>
                <div id="inspectorMeta" className="meta"></div>
                <div className="io-tabs">
                  <button className="active" data-io="inputs" aria-pressed="true">Entrées</button>
                  <button data-io="outputs" aria-pressed="false">Sorties</button>
                </div>
                <div className="io-actions">
                  <span id="ioInfo"></span>
                  <button id="copyData">Copier</button>
                </div>
                <div id="inspectorData"></div>
                <button id="loadMoreDetail" className="secondary hidden">Afficher la suite</button>
              </div>
            </div>
          </aside>
        </div>
        <dialog id="traceInspectorDialog" className="trace-inspector-dialog" aria-labelledby="traceInspectorTitle">
          <header>
            <h2 id="traceInspectorTitle">Détails du nœud</h2>
            <button id="closeTraceInspector" className="secondary" type="button">Fermer</button>
          </header>
          <div id="inspectorModalContent"></div>
        </dialog>
      </section>
    </>
  );
});
export default TracesView;
