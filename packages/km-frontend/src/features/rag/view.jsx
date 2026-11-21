import React, { memo } from "react";

const RagView = memo(function RagView() {
  return (
    <>
      <section id="rag" className="view">
        <h1>{"Recherche"}</h1>
        <form id="ragForm" className="search-bar">
          <label className="sr" htmlFor="ragQuery">
            {"Recherche"}
          </label>
          <input
            id="ragQuery"
            required={true}
            placeholder="Terme, référence ou question…"
          />
          <select aria-label="Mode de recherche" id="ragMode" defaultValue="hybrid">
            <option value="hybrid">{"Hybride"}</option>
            <option value="lexical">{"BM25"}</option>
            <option value="semantic">{"E5"}</option>
            <option value="visual" hidden={import.meta.env.VITE_KM_MODE === 'true'}>ColQwen2</option>
          </select>
          <select aria-label="Nombre de résultats" id="ragLimit" defaultValue="5">
            <option>{"5"}</option>
            <option>{"10"}</option>
            <option>{"20"}</option>
          </select>
          <button className="primary">{"Rechercher"}</button>
        </form>
        <details id="ragScope" className="rag-scope" hidden>
          <summary>Périmètre de recherche</summary>
          <label>Identifiant du document
            <input id="ragScopeDocument" form="ragForm" pattern="d[0-9a-f]{32}" />
          </label>
          <label>Colonne de métadonnées
            <select id="ragScopeColumn" disabled><option value="">Toutes les colonnes</option></select>
          </label>
          <label>Valeur à rechercher
            <input id="ragScopeValue" maxLength={1000} disabled />
          </label>
        </details>
        <div id="ragNotice" className="notice hidden" role="status"></div>
        <div className="retrieval-layout">
          <div id="ragResults" className="results">
            <div className="empty">{"Aucun résultat."}</div>
          </div>
          <article id="sourcePreview" className="source-preview">
            <div className="empty">{"Sélectionner une source."}</div>
          </article>
        </div>
      </section>
    </>
  );
});
export default RagView;
