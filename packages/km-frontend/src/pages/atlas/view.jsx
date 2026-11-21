import React, { memo } from "react";
const AtlasView = memo(function AtlasView() {
  return <>
    <header>
      <div><span className="brand">KM <b>atlas.</b></span><div className="sub">Identités / versions / fichiers · lecture seule</div></div>
      <div className="search"><input id="search" placeholder="Identifiant, fichier ou version…" aria-label="Rechercher dans l’atlas"/><div id="results" hidden /></div>
      <button id="import">Actualiser</button><button id="more" hidden>Charger les suivants</button>
    </header>
    <canvas aria-label="Hiérarchie des identités, versions et fichiers KM" />
    <section id="empty"><div className="sub">MÉTADONNÉES DU FILESYSTEM</div><h1>Chaque fichier,<br/>dans sa version.</h1><p id="emptyMessage">Lecture des métadonnées du corpus…</p><button id="start" className="primary">Lire le corpus</button></section>
    <div id="status" role="status" aria-live="polite" hidden />
    <div className="tools"><select id="grouping" aria-label="Regrouper les métadonnées" defaultValue="parent">
      <option value="parent">Par identité parent</option><option value="nature">Par nature brute</option><option value="etatId">Par étatId brut</option><option value="links">Par connexions</option>
    </select><button id="minus" aria-label="Dézoomer">−</button><button id="plus" aria-label="Zoomer">+</button><button id="fit">Vue d’ensemble</button></div>
    <div className="legend"><span className="identity-dot">● Identité</span> · ● Version / fichier<br/><span className="reference-dot">⇢ Référence déclarée / identité hors extrait</span><br/>Glisser · Molette · Double-clic pour la fiche</div>
    <div id="tip" hidden /><div id="popup" hidden><strong id="name"/><div id="count" className="sub"/><button id="open" className="primary">Ouvrir la fiche →</button></div>
    <aside hidden><button id="close" aria-label="Fermer la fiche">×</button><div className="sub">PROVENANCE FILESYSTEM</div><h2 id="title"/><p id="info"/>
      <a id="source" className="source-link" hidden target="_blank" rel="noreferrer">Ouvrir le fichier source ↗</a>
      <dl id="meta"/><h3>Connexions dans cet extrait</h3><div id="neighbors"/>
    </aside>
  </>;
});
export default AtlasView;
