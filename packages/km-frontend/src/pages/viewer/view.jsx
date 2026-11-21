import React, { memo } from "react";

const ViewerView = memo(function ViewerView() {
  return (
    <>
      <header>
        <div>
          <div className="eyebrow">
            {"CONSULTATION DOCUMENTAIRE / GÉOMÉTRIE SOURCE"}
          </div>
          <h1 id="name">{"Chargement du document…"}</h1>
          <p id="chain"></p>
        </div>
        <nav>
          <a href="/#library">{"← Gestion"}</a>
          {"   "}
          <a id="source" className="source">
            {"Télécharger l’original"}
          </a>
        </nav>
      </header>
      {"\n"}
      <div className="toolbar">
        <button id="previous" aria-label="Page précédente">
          {"←"}
        </button>
        <select id="pages" aria-label="Page ou unité source"></select>
        <button id="next" aria-label="Page suivante">
          {"→"}
        </button>
        <select id="type" aria-label="Type de bloc" defaultValue="">
          <option value="">{"Tous les types"}</option>
        </select>
        <label>
          <input type="checkbox" id="labels" defaultChecked={true} />
          {" Types sous les boîtes"}
        </label>
        <label>
          <input type="checkbox" id="nested" />
          {" Blocs internes MinerU"}
        </label>
        <label>
          {"Zoom "}
          <select id="zoom" aria-label="Zoom" defaultValue="fit">
            <option value="fit">{"Page entière"}</option>
            <option value="100">{"Largeur 100 %"}</option>
            <option value="150">{"150 %"}</option>
            <option value="200">{"200 %"}</option>
          </select>
        </label>
        <button id="fullscreen" aria-pressed="false">
          {"Plein écran"}
        </button>
        <button id="detailsToggle" aria-pressed="false">
          {"Détails du bloc"}
        </button>
        <span id="counts" className="counts"></span>
      </div>
      {"\n"}
      <div id="notice" role="status"></div>
      <main className="workspace">
        <section>
          <div className="reader" id="reader">
            <div className="sheet" id="sheet"></div>
          </div>
          <p id="method" className="note"></p>
          <div id="children" className="note"></div>
        </section>
        <aside className="side">
          <section>
            <h2>{"Blocs détectés"}</h2>
            <div id="blocks" className="blocks"></div>
            <p className="note">
              {
                "Cliquer sur une boîte ou un bloc pour consulter son crop, son type et le texte extrait. Les coordonnées sont celles conservées à l’indexation."
              }
            </p>
          </section>
          <section>
            <h3 id="cropTitle">{"Aperçu du bloc"}</h3>
            <div id="crop" className="crop">
              <p className="blank">{"Sélectionner un bloc."}</p>
            </div>
            <p id="cropNote" className="note"></p>
          </section>
          <section className="details">
            <h3>{"Données du bloc"}</h3>
            <pre id="details"></pre>
            <h3>{"Texte extrait"}</h3>
            <pre id="text"></pre>
          </section>
        </aside>
      </main>
    </>
  );
});
export default ViewerView;
