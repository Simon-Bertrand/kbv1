import React, { useEffect, useState } from "react";

export default function CacheChoice({doc,value,onChange,disabled,revision}) {
  const [open,setOpen]=useState(false),[data,setData]=useState(null),[error,setError]=useState("");
  useEffect(()=>{
    if(!open)return;
    const controller=new AbortController();setData(null);setError("");
    fetch("/api/index/caches?"+new URLSearchParams({document_id:doc.id}),{signal:controller.signal})
      .then(async r=>{const v=await r.json();if(!r.ok)throw new Error(v.detail||"Caches indisponibles");return v;})
      .then(v=>{if(!controller.signal.aborted)setData(v);})
      .catch(e=>{if(e.name!=="AbortError")setError(e.message);});
    return()=>controller.abort();
  },[open,doc.id,revision]);
  const selected=data?.items.find(row=>row.key===value);
  return <div className="cache-choice">
    <button className="secondary" disabled={disabled||!doc.cache_count} onClick={()=>setOpen(v=>!v)}>{value?"Cache choisi : "+value.slice(0,8):"Choisir / vérifier le cache"}</button>
    {open&&<div>
      {error&&<p role="alert">{error}</p>}
      {!data&&!error&&<p role="status">Vérification des caches…</p>}
      {data&&<>
        <select aria-label={"Cache de "+doc.path} disabled={disabled} value={value||""} onChange={e=>onChange(e.target.value)}>
          <option value="">Automatique si un seul cache valide</option>
          {data.items.map(row=><option key={row.key} value={row.key} disabled={!row.valid}>
            {row.key.slice(0,12)} · {row.chunks??"?"} chunks · {row.valid?"valide":row.error}
          </option>)}
        </select>
        {data.items.filter(row=>!row.valid).map(row=><p key={row.key} className="document-error">{row.key.slice(0,12)} : {row.error}</p>)}
        {selected&&<details><summary>Configuration du cache choisi</summary><pre>{JSON.stringify(selected.configuration,null,2)}</pre></details>}
      </>}
    </div>}
  </div>;
}