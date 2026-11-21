import React, { useDeferredValue, useEffect, useRef, useState } from "react";
import { fileSize, orderedEntries, fileLink } from "./sources.js";

function useResource(url, revision) {
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setState({ loading: true, data: null, error: "" });
    async function load() {
      try {
        const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw Error(data.detail || data.error || "Service indisponible.");
        if ((url.startsWith("/api/files/") && !Array.isArray(data.entries)) || (url.startsWith("/api/knowledge/") && (!Array.isArray(data.items) || !Number.isInteger(data.total)))) throw Error("Réponse documentaire invalide.");
        if (current) setState({ loading: false, data, error: "" });
      } catch (error) {
        if (current && error.name !== "AbortError")
          setState({ loading: false, data: null, error: error.message });
      }
    }
    load();
    return () => { current = false; controller.abort(); };
  }, [url, revision]);
  return state;
}

function StateMessage({ loading, error, retry, children }) {
  if (loading) return <div className="document-message" role="status">Chargement…</div>;
  if (error) return <div className="document-message document-error" role="alert"><strong>Consultation indisponible</strong><p>{error}</p><button onClick={retry}>Réessayer</button></div>;
  return children;
}

function FileBrowser() {
  const [path, setPath] = useState(() => new URLSearchParams(window.location.search).get("folder") || "");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [revision, setRevision] = useState(0);
  const [page, setPage] = useState(0);
  const deferredQuery = useDeferredValue(query);
  const state = useResource("/api/files/list?path=" + encodeURIComponent(path), revision);
  const entries = orderedEntries(state.data?.entries || [], deferredQuery);
  const parts = path.split("/").filter(Boolean);
  const pageSize = 50;
  function openFolder(next) {
    setPath(next); setSelected(null); setQuery(""); setPage(0);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("folder", next); else url.searchParams.delete("folder");
    url.hash = "sources";
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  const fullPath = selected ? [path, selected.name].filter(Boolean).join("/") : "";
  return <>
    <div className="document-toolbar">
      <nav className="document-breadcrumb" aria-label="Dossier courant">
        <button onClick={() => openFolder("")}>Racine</button>
        {parts.map((part, index) => <React.Fragment key={index}><span aria-hidden="true">/</span><button onClick={() => openFolder(parts.slice(0,index+1).join("/"))} aria-current={index===parts.length-1 ? "location" : undefined}>{part}</button></React.Fragment>)}
      </nav>
      <button className="subtle-button" onClick={() => { setSelected(null); setPage(0); setRevision(v=>v+1); }}>Actualiser</button>
      <a className="subtle-button" href={"/api/files/zip?path="+encodeURIComponent(path)} download>Tout télécharger en ZIP</a>
    </div>
    <div className="document-filter"><label htmlFor="documentFileFilter">Filtrer ce dossier</label><input id="documentFileFilter" type="search" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}} placeholder="Nom de fichier ou de dossier" autoComplete="off" /><span>{entries.length.toLocaleString("fr-FR")} élément{entries.length>1?"s":""}</span></div>
    <StateMessage {...state} retry={()=>setRevision(v=>v+1)}>
      <div className={"document-workspace"+(selected?" has-selection":"")}>
        <div className="document-table-wrap"><table className="document-table"><thead><tr><th scope="col">Nom</th><th scope="col">Type</th><th scope="col">Taille</th><th scope="col">Modifié le</th><th scope="col"><span className="sr">Téléchargement</span></th></tr></thead>
          <tbody>{entries.slice(page*pageSize,(page+1)*pageSize).map(entry=>{
            const folder=entry.type==="directory";
            const entryPath=[path,entry.name].filter(Boolean).join("/");
            return <tr key={entry.name} className={selected?.name===entry.name?"selected":""}>
              <td><button className="document-name" onClick={()=>folder?openFolder(entryPath):setSelected(entry)}><span className={"file-symbol "+(folder?"is-folder":"")} aria-hidden="true"></span>{entry.name}</button></td>
              <td>{folder?"Dossier":entry.name.includes(".")?entry.name.split(".").at(-1).toUpperCase():"Fichier"}</td><td>{folder?"—":fileSize(entry.size)}</td>
              <td>{entry.mtime && Number.isFinite(Date.parse(entry.mtime))?new Date(entry.mtime).toLocaleDateString("fr-FR"):"—"}</td>
              <td><a className="table-action" href={folder?"/api/files/zip?path="+encodeURIComponent(entryPath):fileLink(entryPath)} download aria-label={"Télécharger "+entry.name}>{folder?"ZIP":"Télécharger"}</a></td>
            </tr>;
          })}</tbody></table>
          {!entries.length && <div className="document-empty"><h2>{query?"Aucun résultat":"Ce dossier est vide"}</h2><p>{query?"Essayez un autre nom dans ce dossier.":"Les fichiers de la racine configurée apparaîtront ici."}</p></div>}
        </div>
        {selected && <aside className="document-details" aria-label="Détails du fichier"><div className="details-heading"><h2>Fichier</h2><button onClick={()=>setSelected(null)} aria-label="Fermer les détails">×</button></div><h3>{selected.name}</h3><dl><dt>Emplacement</dt><dd>{path || "Racine"}</dd><dt>Taille</dt><dd>{fileSize(selected.size)}</dd><dt>Dernière modification</dt><dd>{selected.mtime && Number.isFinite(Date.parse(selected.mtime))?new Date(selected.mtime).toLocaleString("fr-FR"):"Non renseignée"}</dd></dl><a className="primary document-download" href={fileLink(fullPath)} download>Télécharger le fichier</a><p className="document-note">Les originaux sont consultés en lecture seule.</p></aside>}
      </div>
      {entries.length>pageSize && <nav className="document-pagination" aria-label="Pages du dossier"><button disabled={page===0} onClick={()=>setPage(v=>v-1)}>Précédent</button><span>{page+1} / {Math.ceil(entries.length/pageSize)}</span><button disabled={(page+1)*pageSize>=entries.length} onClick={()=>setPage(v=>v+1)}>Suivant</button></nav>}
    </StateMessage>
  </>;
}

function IndexedCorpus() {
  const [offset,setOffset]=useState(0);
  const [revision,setRevision]=useState(0);
  const [selected,setSelected]=useState(null);
  const state=useResource("/api/knowledge/documents?limit=50&offset="+offset,revision);
  return <>
    <div className="document-toolbar"><p className="document-note">Documents publiés par la préparation documentaire.</p><button className="subtle-button" onClick={()=>{setSelected(null);setRevision(v=>v+1);}}>Actualiser</button></div>
    <StateMessage {...state} retry={()=>setRevision(v=>v+1)}>
      <div className={"document-workspace"+(selected?" has-selection":"")}>
        <div className="document-table-wrap"><table className="document-table"><thead><tr><th scope="col">Document</th><th scope="col">Emplacement</th><th scope="col">Consultation</th></tr></thead><tbody>{(state.data?.items||[]).map(doc=><tr key={doc.id} className={selected?.id===doc.id?"selected":""}><td><button className="document-name" onClick={()=>setSelected(doc)}><span className="file-symbol" aria-hidden="true"></span>{doc.observed?.original_name||"Sans nom"}</button></td><td className="document-path">{doc.filepath}</td><td><a className="table-action" href={"/viewer/"+encodeURIComponent(doc.id)}>Lire les passages</a></td></tr>)}</tbody></table>
        {!state.data?.items?.length && <div className="document-empty"><h2>Aucun document publié</h2><p>Les documents apparaîtront après leur préparation et leur publication dans le corpus.</p></div>}</div>
        {selected && <aside className="document-details" aria-label="Métadonnées du document"><div className="details-heading"><h2>Document</h2><button onClick={()=>setSelected(null)} aria-label="Fermer les détails">×</button></div><h3>{selected.observed?.original_name||"Sans nom"}</h3><dl>{Object.entries(selected.metadata||{}).map(([key,value])=><React.Fragment key={key}><dt>{key}</dt><dd>{typeof value==="object"?JSON.stringify(value):String(value)}</dd></React.Fragment>)}</dl>{!Object.keys(selected.metadata||{}).length && <p className="document-note">Aucune métadonnée renseignée.</p>}<a className="primary document-download" href={"/viewer/"+encodeURIComponent(selected.id)}>Consulter les passages</a><a className="subtle-button document-download" href={"/api/knowledge/documents/"+encodeURIComponent(selected.id)+"/source"} target="_blank" rel="noreferrer">Ouvrir l’original</a></aside>}
      </div>
      {(state.data?.total||0)>50 && <nav className="document-pagination" aria-label="Pages du corpus"><button disabled={!offset} onClick={()=>{setOffset(v=>v-50);setSelected(null);}}>Précédent</button><span>{offset+1}–{Math.min(offset+50,state.data.total)} sur {state.data.total}</span><button disabled={offset+50>=state.data.total} onClick={()=>{setOffset(v=>v+50);setSelected(null);}}>Suivant</button></nav>}
    </StateMessage>
  </>;
}

export default function Sources() {
  const [tab,setTab]=useState("files");
  const filesRef=useRef(null), corpusRef=useRef(null);
  function navigateTabs(event) {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const next=event.key==="Home"?"files":event.key==="End"?"corpus":tab==="files"?"corpus":"files";
    setTab(next);
    (next==="files"?filesRef:corpusRef).current?.focus();
  }
  return <div className="documents-page">
    <div className="page-heading"><div><h1>Documents</h1></div></div>
    <div className="document-tabs" role="tablist" aria-label="Documents" onKeyDown={navigateTabs}>
      <button ref={filesRef} tabIndex={tab==="files"?0:-1} role="tab" id="files-tab" aria-controls="document-files" aria-selected={tab==="files"} onClick={()=>setTab("files")}>Fichiers</button>
      <button ref={corpusRef} tabIndex={tab==="corpus"?0:-1} role="tab" id="corpus-tab" aria-controls="document-corpus" aria-selected={tab==="corpus"} onClick={()=>setTab("corpus")}>Corpus indexé</button>
    </div>
    {tab==="files" ? <div role="tabpanel" id="document-files" aria-labelledby="files-tab"><FileBrowser /></div>:<div role="tabpanel" id="document-corpus" aria-labelledby="corpus-tab"><IndexedCorpus /></div>}
  </div>;
}
