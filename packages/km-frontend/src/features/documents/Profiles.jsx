import React, { useEffect, useRef, useState } from "react";
import Modal from "./Modal.jsx";
import EmbeddingManagement from "./EmbeddingManagement.jsx";

async function request(path,body) {
  const response=await fetch("/api/index/profiles"+path,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{});
  const value=await response.json();
  if(!response.ok)throw new Error(value.detail||"Gestion des profils indisponible : vérifier les migrations KM.");
  return value;
}
export default function Profiles({selected,onChange,refreshKey,indexing,onEmbeddingsChanged,mode="indexation"}) {
  const version=useRef(0);
  const [configurationOpen,setConfigurationOpen]=useState(false);
  const [data,setData]=useState({items:[]});
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(true);
  const [saving,setSaving]=useState(false);
  const [label,setLabel]=useState("");
  async function load(preferred=selected) {
    const currentVersion=++version.current;
    setLoading(true);
    try {
      const value=await request("");
      if(currentVersion!==version.current)return;
      setData(value);setError("");
      const id=value.items.some(p=>p.profile===preferred)?preferred:value.current_profile||value.active_profile||value.items[0]?.profile||null;
      const row=value.items.find(p=>p.profile===id);
      setLabel(row?.label||"");onChange(id,Boolean(row?.compatible));
    } catch(e){if(currentVersion===version.current){setError(e.message);onChange(null,false);}}
    finally{if(currentVersion===version.current)setLoading(false);}
  }
  useEffect(()=>{let mounted=true;const currentVersion=++version.current;
    setLoading(true);
    request("").then(value=>{
      if(!mounted||currentVersion!==version.current)return;
      setData(value);setError("");
      const id=value.items.some(p=>p.profile===selected)?selected:value.current_profile||value.active_profile||value.items[0]?.profile||null;
      const row=value.items.find(p=>p.profile===id);
      setLabel(row?.label||"");onChange(id,Boolean(row?.compatible));
    }).catch(e=>{if(mounted&&currentVersion===version.current){setError(e.message);onChange(null,false);}})
      .finally(()=>{if(mounted&&currentVersion===version.current)setLoading(false);});
    return()=>{mounted=false;};
  },[refreshKey,onChange]);
  const current=data.items.find(p=>p.profile===selected);
  async function mutate(path,body,prefer) {
    setSaving(true);setError("");
    try {const value=await request(path,body);await load(prefer||value.profile||selected);}
    catch(e){setError(e.message);}
    finally{setSaving(false);}
  }
  return <section className={"embedding-profiles profiles-"+mode} aria-label="Gestion des profils d’embedding">
    <div className="profiles-heading"><div><h2>Profils d’embedding</h2><p>Chaque profil conserve ses vecteurs, ses sources et sa reprise. La recherche utilise un seul profil actif.</p></div>
      <button className="secondary" disabled={saving||loading} onClick={()=>load()}>Actualiser les profils</button></div>
    {error&&<p className="notice" role="alert">{error}</p>}
    {Boolean(data.unassigned_checkpoints)&&<p className="document-note">{data.unassigned_checkpoints} trace(s) historique(s) sans profil identifié : conservées, sans réutilisation automatique.</p>}
    {data.encoder_error&&<p className="document-note">{data.encoder_error}</p>}
    {data.encoder&&<p className="document-note">Encodeur chargé : {data.encoder.model||data.encoder.contract} · {data.encoder.dimension} dimensions · {data.encoder.max_tokens??"—"} tokens.</p>}
    <div className="document-toolbar">
      <label>Profil consulté <select aria-label="Profil d’embedding" value={selected||""} disabled={loading||saving} onChange={e=>{
        const row=data.items.find(p=>p.profile===e.target.value);setLabel(row?.label||"");onChange(row?.profile||null,Boolean(row?.compatible));
      }}><option value="" disabled>{loading?"Chargement des profils…":"Aucun profil enregistré"}</option>
        {data.items.map(row=><option key={row.profile} value={row.profile}>{row.label} · {row.configuration?.max_tokens??"?"} tokens · {row.encoder_fingerprint.slice(0,10)}{row.active?" · recherche active":""}</option>)}
      </select></label>
      <button className="secondary" disabled={saving||loading||!data.encoder||Boolean(data.current_profile)} onClick={()=>mutate("/register",{})}>Enregistrer l’encodeur actuel</button>
    </div>
    {current&&<>
      <div className="profile-facts"><span><strong>{current.documents}</strong> PDF</span><span><strong>{current.chunks}</strong> chunks publiés</span><span><strong>{current.saved}</strong> vecteurs à publier</span><span><strong>{current.failed}</strong> traces à reprendre</span>
        <span>{current.active?"Profil actif pour la recherche":"Profil conservé"}</span></div>
      <p className="document-note" role="status">{current.compatible?"Compatible avec l’encodeur chargé : indexation et recherche sémantique possibles.":current.verified?"Autre encodeur : consultation et recherche lexicale possibles. Charger l’encodeur correspondant pour la recherche sémantique ou pour compléter ce profil.":"Profil historique non vérifié : vecteurs conservés, sans réutilisation automatique avec l’encodeur actuel."}</p>
      {mode==="gestion"&&<div className="document-toolbar">
        <button className="secondary" disabled={saving||current.active} onClick={()=>mutate("/activate",{profile:current.profile})}>Utiliser pour la recherche</button>
        <form onSubmit={e=>{e.preventDefault();mutate("/rename",{profile:current.profile,label});}}>
          <label>Nom du profil <input aria-label="Nom du profil" value={label} maxLength={80} onChange={e=>setLabel(e.target.value)} /></label>
          <button className="secondary" disabled={saving||!label.trim()}>Renommer</button>
        </form>
      </div>}
      <button className="secondary profile-configuration" onClick={()=>setConfigurationOpen(true)}>Configuration du profil</button>
      {configurationOpen&&<Modal title="Configuration du profil" onClose={()=>setConfigurationOpen(false)}><pre>{JSON.stringify({profile:current.profile,encoder_fingerprint:current.encoder_fingerprint,...current.configuration},null,2)}</pre></Modal>}
    </>}
    <div id="panel-gestion" role="tabpanel" aria-labelledby="tab-gestion" hidden={mode!=="gestion"}>
      {mode==="gestion"&&current&&<EmbeddingManagement profile={selected} refreshKey={refreshKey} indexing={indexing} onChanged={onEmbeddingsChanged} />}
    </div>
  </section>;
}