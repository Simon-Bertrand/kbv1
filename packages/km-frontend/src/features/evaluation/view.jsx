import React, { memo } from "react";

const EvaluationView = memo(function EvaluationView() {
  return (
    <>
      <section id="evaluation" className="view">
        {"\n"}
        <div className="eval-heading">
          <h1>{"Évaluation"}</h1>
          <button className="secondary" id="evRefresh">
            {"Actualiser"}
          </button>
        </div>
        {"\n"}
        <div id="evNotice" className="notice hidden" role="status"></div>
        <div className="eval-grid">
          {"\n"}
          <aside>
            <div className="eval-panel">
              <h2>{"Jeux de questions"}</h2>
              <details>
                <summary>{"Importer un jeu"}</summary><p className="eval-muted">JSON métier : partie, question, response obligatoire, type_question (texte libre) ; commentaire facultatif.</p>
                <form id="evImport">
                  <label>
                    {"Nom"}
                    <input id="evDatasetName" required={true} maxLength="150" />
                  </label>
                  <label>
                    {"Fichier JSON, JSONL, CSV ou TXT"}
                    <input
                      id="evFile"
                      type="file"
                      accept=".json,.jsonl,.csv,.txt"
                    />
                  </label>
                  <label>
                    {"Ou contenu"}
                    <textarea
                      id="evContent"
                      placeholder="Une question par ligne"
                      defaultValue=""
                    ></textarea>
                  </label>
                  <label>
                    {"Format du contenu"}
                    <select id="evFormat" defaultValue="text">
                      <option value="text">
                        {"Texte · une question par ligne"}
                      </option>
                      <option value="json">{"JSON"}</option>
                      <option value="jsonl">{"JSONL"}</option>
                      <option value="csv">{"CSV"}</option>
                    </select>
                  </label>
                  <button className="primary">{"Importer"}</button>
                </form>
                <button id="evTemplate" className="text-button">
                  {"Exemple JSON fabriqué ↓"}
                </button>
              </details>
              <form id="evCreate">
                <label>
                  {"Jeu"}
                  <select id="evDataset" required={true}></select>
                </label>
                <button
                  type="button"
                  id="evDatasetExport"
                  className="text-button"
                >
                  {"Exporter les questions ↓"}
                </button>
                <label>
                  {"Campagne"}
                  <input id="evLabel" required={true} maxLength="150" />
                </label>
                <label>
                  {"Version du corpus · déclarative"}
                  <input
                    id="evCorpus"
                    required={true}
                    maxLength="150"
                    placeholder="Ex. catalogue-2026-10-16"
                  />
                </label>
                <div className="eval-two">
                  <label>
                    {"Répétitions"}
                    <select id="evRepetitions" defaultValue="1">
                      <option>{"1"}</option>
                      <option>{"2"}</option>
                      <option>{"3"}</option>
                      <option>{"5"}</option>
                    </select>
                  </label>
                  <label>
                    {"Budget / question"}
                    <select id="evBudget" defaultValue="0">
                      <option value="0">Sans limite de temps</option>
                      <option value="240">{"4 min"}</option>
                      <option value="600">{"10 min"}</option>
                      <option value="900">{"15 min"}</option>
                    </select>
                  </label>
                </div>
                <label>
                  {"Recherche"}
                  <select id="evMode" defaultValue="hybrid">
                    <option value="hybrid">{"Hybride"}</option>
                    <option value="lexical">{"BM25"}</option>
                    <option value="semantic">{"E5"}</option>
                  </select>
                </label>
                <label><input id="evJudge" type="checkbox" defaultChecked={true} />
                  {" Évaluation LLM · référence partielle"}
                </label>
                
                <button className="secondary" value="review">
                  {"Créer une revue"}
                </button>
                <button className="primary" value="execute">
                  {"Exécuter la campagne"}
                </button>
              </form>
            </div>
            <div className="eval-panel" style={{ marginTop: "16px" }}>
              <h2>{"Campagnes"}</h2>
              <div id="evCampaigns" className="eval-campaigns">
                <div className="empty">{"Aucune campagne."}</div>
              </div>
            </div>
          </aside>
          {"\n"}
          <div>
            <div id="evReport" className="eval-panel" hidden={true}>
              <div className="eval-toolbar">
                <h2 id="evReportTitle"></h2>
                <button id="evStop" className="secondary" hidden={true}>
                  {"Arrêter après la question"}
                </button>
              </div>
              <p id="evVersion" className="eval-muted"></p>
              <div id="evStats" className="eval-stats"></div>
              <p id="evUncertainty" className="eval-muted"></p>
              <div className="eval-export">
                <a id="evExporthtml">{"Rapport HTML ↓"}</a>
                <a id="evExportcsv">{"CSV ↓"}</a>
                <a id="evExportjson">{"JSON ↓"}</a>
                <label>
                  <input id="evInclude" type="checkbox" />
                  {" Inclure questions et réponses"}
                </label>
              </div>
              <label>
                {"Comparaison · même jeu et annotations initiales"}
                <select id="evCompare"></select>
              </label>
              <p id="evCompareResult"></p>
              <div id="evDimensions" className="eval-table-wrap"></div>
              <div className="eval-table-wrap">
                <table className="eval-table">
                  <thead>
                    <tr>
                      <th>{"Cas · essai"}</th>
                      <th>{"Question"}</th>
                      <th>{"Exécution"}</th>
                      <th>{"Qualité métier"}</th>
                      <th>{"Secondes"}</th>
                      <th>{"Juge LLM"}</th>
                      <th>{"BLEU / ROUGE-L"}</th>
                    </tr>
                  </thead>
                  <tbody id="evRows"></tbody>
                </table>
              </div>
              {"\n"}
              <div id="evDetail" className="eval-review" hidden={true}>
                <div className="eval-toolbar">
                  <h2 id="evDetailTitle"></h2>
                  <button id="evTrace" className="text-button">
                    {"Ouvrir la trace ↗"}
                  </button>
                </div>
                <h3>{"Question"}</h3>
                <pre id="evQuestion"></pre>
                <h3>{"Réponse"}</h3>
                <pre id="evAnswer"></pre>
                <pre id="evAutomatic" className="eval-muted"></pre>
              <p id="evExecutionError" className="eval-fail"></p>
                <p id="evReference" className="eval-muted"></p>
                <div id="evAttachArea">
                  <label>
                    {"Trace archivée"}
                    <select id="evAttachRuns"></select>
                  </label>
                  <button id="evAttach" className="secondary">
                    {"Rattacher"}
                  </button>
                </div>
                <form id="evReview">
                  <h3>{"Critères métier"}</h3>
                  <div id="evCriteria"></div>
                  <h3>{"Appels d’outils"}</h3>
                  <div id="evTools"></div>
                  <details>
                    <summary>{"Sources pertinentes"}</summary>
                    <p id="evRelevanceUnit" className="eval-muted"></p>
                    <label>
                      {"Jugements JSON"}
                      <textarea
                        id="evRelevance"
                        spellCheck="false"
                        defaultValue=""
                      ></textarea>
                    </label>
                    <label>
                      <input id="evExhaustive" type="checkbox" />
                      {
                        " Jugements exhaustifs : toute source non listée est non pertinente"
                      }
                    </label>
                  </details>
                  <details>
                    <summary>{"Mesures et sources observées"}</summary>
                    <pre id="evDiagnostic"></pre>
                  </details>
                  <label>
                    {"Évaluateur"}
                    <input id="evReviewer" required={true} maxLength="120" />
                  </label>
                  <label>
                    {"Notes / justification"}
                    <textarea
                      id="evReviewNotes"
                      maxLength="4000"
                      defaultValue=""
                    ></textarea>
                  </label>
                  <button id="evReviewSubmit" className="primary">
                    {"Enregistrer la revue"}
                  </button>
                </form>
              </div>
            </div>
            <div className="eval-panel eval-muted">
              {
                "N/E : non évaluable avec les annotations disponibles. La réussite métier est distincte de la fin d’exécution et des contrôles automatiques."
              }
            </div>
          </div>
        </div>
      </section>
    </>
  );
});
export default EvaluationView;
