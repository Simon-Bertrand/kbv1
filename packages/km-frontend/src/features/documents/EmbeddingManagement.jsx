import React, { useEffect, useState } from "react";
import Modal from "./Modal.jsx";

async function api(path,body,signal) {
  const response=await fetch("/api/index/profiles/"+path,{signal,...(body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{})});
  const value=await response.json();
  if(!response.ok)throw new Error(value.detail||"Gestion des embeddings indisponible.");
  return value;
}
function DeleteConfirmation({target,indexing,onCancel,onDone}) {
  const [preview,setPreview]=useState(null),[word,setWord]=useState(""),[error,setError]=useState(""),[saving,setSaving]=useState(false),[revision,setRevision]=useState(0);
  useEffect(()=>{
    const controller=new AbortController();setPreview(null);setWord("");setError("");
    const {title,...body}=target;
    api("delete-preview",body,controller.signal).then(v=>{if(!controller.signal.aborted)setPreview(v);}).catch(e=>{if(e.name!=="AbortError")setError(e.message);});
    return()=>controller.abort();
  },[target,revision]);
  const count=preview?.counts;
  const empty=count&&!count.published_vectors&&!count.checkpoint_rows&&!count.legacy_vectors;
  async function remove(e) {
    e.preventDefault();
    if(!preview||word!=="SUPPRIMER"||empty||indexing||saving)return;
    setSaving(true);setError("");
    try {
      const {title,...body}=target;
      await api("delete-embeddings",{...body,expected:preview.expected,confirmation:word});
      onDone();
    } catch(e){setError(e.message);setWord("");}
    finally{setSaving(false);}
  }
  return <div className="delete-confirmation" role="region" aria-label="Confirmation de suppression des embeddings">
    <h3>Supprimer les embeddings : {target.title}</h3>
    <p className="document-note">Profil : <code>{target.profile}</code>. Les PDF, caches, configurations et textes sources sont conservés. Les textes peuvent rester accessibles en recherche lexicale. Les vecteurs de reprise concernés sont également retirés.</p>
    {error&&<p role="alert">{error}</p>}
    {!preview&&!error&&<p role="status">Calcul de la portée exacte…</p>}
    {count&&<p>Vecteurs publiés : <strong>{count.published_vectors}</strong> · Copies de reprise : <strong>{count.saved_vectors}</strong> · Traces techniques : {count.checkpoint_rows} · Copies historiques : {count.legacy_vectors}.</p>}
    {empty&&<p>Aucun vecteur ni trace à supprimer dans cette portée.</p>}
    <form onSubmit={remove}><label>Écrire SUPPRIMER pour confirmer <input aria-label="Confirmation SUPPRIMER" value={word} disabled={saving||!preview||empty||indexing} onChange={e=>setWord(e.target.value)} autoComplete="off" /></label>
      <button className="danger" disabled={saving||indexing||!preview||empty||word!=="SUPPRIMER"}>Confirmer la suppression</button>
      <button type="button" className="secondary" disabled={saving} onClick={onCancel}>Annuler</button>
      <button type="button" className="secondary" disabled={saving} onClick={()=>setRevision(n=>n+1)}>Actualiser la prévisualisation</button>
    </form>
    {indexing&&<p>Attendre la fin du travail KM avant de supprimer.</p>}
  </div>;
}

export default function EmbeddingManagement({profile,refreshKey,indexing,onChanged}) {
  const [docs,setDocs]=useState(null),[offset,setOffset]=useState(0),[error,setError]=useState("");
  const [revision,setRevision]=useState(0),[inspect,setInspect]=useState(null),[provenance,setProvenance]=useState(null),[chunkOffset,setChunkOffset]=useState(0);
  const [target,setTarget]=useState(null),[historyOpen,setHistoryOpen]=useState(false),[history,setHistory]=useState(null);
  useEffect(()=>{setOffset(0);setInspect(null);setTarget(null);setChunkOffset(0);setDocs(null);setError("");},[profile]);
  useEffect(()=>{
    if(!profile)return;
    const controller=new AbortController();setDocs(null);setError("");
    api("documents?"+new URLSearchParams({profile,limit:"100",offset:String(offset)}),null,controller.signal)
      .then(v=>{if(!controller.signal.aborted)setDocs(v);}).catch(e=>{if(e.name!=="AbortError")setError(e.message);});
    return()=>controller.abort();
  },[profile,offset,refreshKey,revision]);
  useEffect(()=>{
    setProvenance(null);
    if(!inspect||!profile)return;
    const controller=new AbortController();setError("");
    api("provenance?"+new URLSearchParams({profile,document_id:inspect.document_id,limit:"100",offset:String(chunkOffset)}),null,controller.signal)
      .then(v=>{if(!controller.signal.aborted)setProvenance(v);}).catch(e=>{if(e.name!=="AbortError")setError(e.message);});
    return()=>controller.abort();
  },[profile,inspect,chunkOffset,refreshKey,revision]);
  useEffect(()=>{
    if(!historyOpen||!profile)return;
    const controller=new AbortController();setHistory(null);
    api("deletions?"+new URLSearchParams({profile,limit:"50"}),null,controller.signal)
      .then(v=>{if(!controller.signal.aborted)setHistory(v);}).catch(e=>{if(e.name!=="AbortError")setError(e.message);});
    return()=>controller.abort();
  },[historyOpen,profile,refreshKey,revision]);
  function deleted(){setTarget(null);setRevision(n=>n+1);onChanged?.();}
  const metadata=provenance?.document.metadata||{};
  return <div className="embedding-management">
    {error&&<p role="alert">{error}</p>}
    <h2>Documents indexés</h2><p className="document-note">Consulter la provenance ou retirer les embeddings du profil sélectionné.</p>
      {!docs?<p role="status">Chargement des publications…</p>:<>
        <div className="document-table-wrap index-scroll" tabIndex="0" role="region" aria-label="Documents indexés"><table className="document-table"><thead><tr><th>PDF</th><th>Vecteurs</th><th>Cache publié</th><th>Actions</th></tr></thead><tbody>
          {docs.items.map(doc=><tr key={doc.document_id}><td>{doc.filepath}<small className="chunk-detail">{new Date(doc.published_at).toLocaleString("fr-FR")}</small></td>
            <td>{doc.chunks}</td><td>{doc.metadata?.pdfparse_cache_key?.slice(0,12)||"Non renseigné"}</td><td>
              <button className="secondary" onClick={()=>{setInspect(doc);setChunkOffset(0);}}>Provenance</button>{" "}
              <button className="danger" disabled={indexing} aria-label={"Supprimer les embeddings de "+doc.filepath} onClick={()=>setTarget({profile,scope:"document",document_id:doc.document_id,title:doc.filepath})}>Supprimer les embeddings</button>
            </td></tr>)}
          {!docs.items.length&&<tr><td colSpan="4">Aucune publication dans ce profil.</td></tr>}
        </tbody></table></div>
        {docs.total>100&&<nav className="document-pagination" aria-label="Documents du profil"><button disabled={!offset} onClick={()=>setOffset(n=>n-100)}>Précédent</button><span>{offset+1}–{Math.min(offset+100,docs.total)} / {docs.total}</span><button disabled={offset+100>=docs.total} onClick={()=>setOffset(n=>n+100)}>Suivant</button></nav>}
      </>}
    {inspect&&<Modal title={"Provenance · "+inspect.filepath} onClose={()=>setInspect(null)}><section className="embedding-provenance" aria-label="Provenance des embeddings">
      {error&&<p role="alert">{error}</p>}
      {!provenance?<p role="status">Chargement de la provenance…</p>:<>
        <dl><dt>Profil E5</dt><dd>{provenance.profile}</dd>
          <dt>Empreinte de l’encodeur</dt><dd>{provenance.encoder.encoder_fingerprint}</dd>
          <dt>PDF source · SHA-256</dt><dd>{metadata.pdfparse_source_sha256||metadata.content_sha256||metadata.pdfparse_manifest?.document_id||"Non renseigné"}</dd>
          <dt>Cache utilisé pour la publication</dt><dd>{metadata.pdfparse_cache_path||"Non renseigné dans cette ancienne publication"}</dd>
          <dt>Empreinte du manifeste</dt><dd>{metadata.pdfparse_cache_digest||"Non renseignée"}</dd>
          <dt>Configuration PdfParse</dt><dd>{metadata.pdfparse_configuration_hash||"Non renseignée"}</dd>
          <dt>Travail de publication KM</dt><dd>{metadata.km_indexing_job_id||"Non renseigné"}</dd></dl>
        <details><summary>Configurations et empreintes des exports</summary><pre>{JSON.stringify({encodeur:metadata.embedding_configuration||provenance.encoder.configuration,
          cache:metadata.pdfparse_configuration||null,exports:metadata.pdfparse_exports||null,preparation:metadata.pdfparse_manifest||null},null,2)}</pre></details>
        <p className="document-note">Ces informations décrivent la publication et son texte d’entrée, y compris lorsque des vecteurs identiques ont été réutilisés. Les champs absents des anciennes publications ne sont pas inventés.</p>
        <table className="document-table"><thead><tr><th>Chunk</th><th>Empreinte du texte encodé</th><th>Tokens</th><th>Publication</th><th>Action</th></tr></thead><tbody>
          {provenance.chunks.items.map(row=><tr key={row.chunk_id}><td>{row.chunk_id}</td><td>{row.input_hash}</td><td>{row.token_count??"—"}</td><td>{new Date(row.indexed_at).toLocaleString("fr-FR")}</td><td>
            <button className="danger" aria-label={"Supprimer le vecteur "+row.chunk_id} disabled={indexing} onClick={()=>setTarget({profile,scope:"chunk",chunk_id:row.chunk_id,title:"chunk "+row.chunk_id})}>Supprimer ce vecteur</button></td></tr>)}
          {!provenance.chunks.items.length&&<tr><td colSpan="5">Aucun vecteur publié ; les sources sont conservées.</td></tr>}
        </tbody></table>
        {provenance.chunks.total>100&&<nav className="document-pagination" aria-label="Chunks du document"><button disabled={!chunkOffset} onClick={()=>setChunkOffset(n=>n-100)}>Précédent</button><span>{chunkOffset+1}–{Math.min(chunkOffset+100,provenance.chunks.total)} / {provenance.chunks.total}</span><button disabled={chunkOffset+100>=provenance.chunks.total} onClick={()=>setChunkOffset(n=>n+100)}>Suivant</button></nav>}
      </>}
    </section></Modal>}
    <div className="document-toolbar"><button className="danger" disabled={indexing} onClick={()=>setTarget({profile,scope:"profile",title:"tous les vecteurs du profil "+profile})}>Supprimer tous les embeddings du profil</button></div>
    {target&&<Modal title="Supprimer des embeddings" onClose={()=>setTarget(null)}><DeleteConfirmation target={target} indexing={indexing} onCancel={()=>setTarget(null)} onDone={deleted} /></Modal>}
    <details open={historyOpen} onToggle={e=>setHistoryOpen(e.currentTarget.open)}><summary>Historique des suppressions du profil (50 dernières)</summary>
      {!history?<p role="status">Chargement de l’historique…</p>:history.items.length?<ul className="history-scroll" tabIndex="0">{history.items.map(row=><li key={row.id}>{new Date(row.created_at).toLocaleString("fr-FR")} · {row.scope} · {row.target} · {row.counts.published_vectors} vecteurs publiés retirés</li>)}</ul>:<p>Aucune suppression enregistrée.</p>}
    </details>
  </div>;
}