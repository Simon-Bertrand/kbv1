import React,{useEffect,useState} from "react";
import CacheConfiguration from "./CacheConfiguration.jsx";

const chunkNames={pending:"À calculer",computed:"Calculé · à publier",published:"Publié pour le RAG",encoding:"En cours",failed:"En erreur",interrupted:"À reprendre"};
const fileNames={pending:"À calculer",partial:"Calcul partiel",computed:"Prêt à publier",published:"Publié pour le RAG",ambiguous:"Choisir un cache",blocked:"Cache à vérifier",no_cache:"Sans cache"};
async function request(path,body,signal){
  const response=await fetch("/api/index/"+path,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal}:{signal});
  let value;try{value=await response.json();}catch{throw new Error("Réponse du service KM illisible (HTTP "+response.status+").");}
  if(!response.ok)throw new Error(value.detail||"Service KM indisponible.");
  return value;
}

export default function ChunkWorkspace({profile,compatible,active,syncing,revision,onSubmitted,visible=true}){
  const [mode,setMode]=useState("chunks"),[state,setState]=useState("missing");
  const [query,setQuery]=useState(""),[search,setSearch]=useState(""),[page,setPage]=useState(0);
  const [data,setData]=useState({items:[],total:0,summary:{}}),[summary,setSummary]=useState({});
  const [pdfs,setPdfs]=useState({total:0,blocked:0}),[loading,setLoading]=useState(false),[error,setError]=useState("");
  const [limit,setLimit]=useState(500),[confirmation,setConfirmation]=useState(null),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0);
  const [cacheInfo,setCacheInfo]=useState(null);
  useEffect(()=>{if(query===search)return;const timer=setTimeout(()=>{setSearch(query);setPage(0);setConfirmation(null);},300);return()=>clearTimeout(timer);},[query,search]);
  useEffect(()=>{
    if(!profile||!visible)return;
    const controller=new AbortController();let timer;
    async function load(){
    setLoading(true);setError("");
    const params=new URLSearchParams({profile,query:search,state,limit:"100",offset:String(page*100)});
    const counts=new URLSearchParams({profile,query:search,limit:"1"});
    try{const [rows,countsResult]=await Promise.all([request("catalog/"+(mode==="chunks"?"chunks":"files")+"?"+params,null,controller.signal),
      mode==="files"?request("catalog/chunks?"+counts,null,controller.signal):Promise.resolve(null)]);
      const global=countsResult||rows;
      if(!controller.signal.aborted){setData(rows);setSummary(global.summary||{});setPdfs(global.pdfs||{total:0,blocked:0});}
    }catch(e){if(e.name!=="AbortError")setError(e.message);}finally{if(!controller.signal.aborted)setLoading(false);}
    if(!controller.signal.aborted&&(active||syncing))timer=setTimeout(load,3000);
    }
    load();return()=>{controller.abort();clearTimeout(timer);};
  },[profile,mode,state,search,page,revision,refresh,active,syncing,visible]);
  const missing=(summary.pending||0)+(summary.failed||0)+(summary.interrupted||0);
  const blocked=active||syncing||busy||loading||query!==search||!compatible;
  function switchMode(value){setData({items:[],total:0,summary:{}});setMode(value);setState("");setPage(0);setConfirmation(null);}
  async function launch(){
    setBusy(true);setError("");
    try{
      const job=await request("chunk-jobs",{profile,query:search,limit:Number(limit),all_chunks:confirmation==="all"});
      onSubmitted(job);setConfirmation(null);setRefresh(v=>v+1);
    }catch(e){setError(e.message);}finally{setBusy(false);}
  }
  async function choose(file,key){
    setBusy(true);setError("");
    try{await request("catalog/cache-choice",{profile,file_id:file,cache_key:key});setRefresh(v=>v+1);}
    catch(e){setError(e.message);}finally{setBusy(false);}
  }
  return <div className="index-corpus source-workspace">
    {error&&<p className="notice" role="alert">{error}</p>}
    <div className="index-counters" aria-label="État global des chunks">
      <div><strong>{Object.values(summary).reduce((a,b)=>a+b,0)}</strong><span>Chunks des caches choisis</span></div>
      <div><strong>{missing}</strong><span>Manquants à calculer</span></div>
      <div><strong>{summary.computed||0}</strong><span>Calculés · à publier</span></div>
      <div><strong>{summary.published||0}</strong><span>Publiés pour le RAG</span></div>
    </div>
    <p className="document-note">Compteurs et filtres sur tout le corpus SQL{search?" correspondant à la recherche":""}, indépendamment de la page affichée. Les pages PDF sont des localisations ; le traitement se fait par chunks.</p>
    {pdfs.blocked>0&&<p className="notice">{pdfs.blocked} PDF sans cache exploitable ou sans choix de cache. Les consulter dans « PDF et caches » ; ils ne sont pas inclus dans les lots.</p>}
    <div className="document-toolbar batch-toolbar">
      <label>Chunks par lot <input aria-label="Nombre de chunks par lot" type="number" min="1" max="10000" value={limit} onChange={e=>{setLimit(e.target.value);setConfirmation(null);}} /></label>
      <button className="primary" disabled={blocked||(!missing&&!summary.computed)||!Number.isInteger(Number(limit))||Number(limit)<1||Number(limit)>10000} onClick={()=>setConfirmation("batch")}>Lancer le lot de chunks</button>
      <button className="secondary" disabled={blocked||(!missing&&!summary.computed)} onClick={()=>setConfirmation("all")}>Tout lancer</button>
    </div>
    {confirmation&&<div className="notice" role="region" aria-label="Confirmer le lot de chunks"><p>{confirmation==="all"?"Calculer tous les chunks manquants de cette recherche":"Calculer au plus "+limit+" chunks manquants de cette recherche"} pour ce profil ? Les vecteurs déjà disponibles sont réutilisés. Un PDF est publié lorsque tous les chunks de son cache sont prêts. Vous pourrez demander l’arrêt après le petit lot en cours.</p>
      <button className="primary" disabled={blocked} onClick={launch}>Confirmer le lancement</button>{" "}<button className="secondary" disabled={busy} onClick={()=>setConfirmation(null)}>Annuler</button></div>}
    <div className="document-toolbar batch-toolbar" aria-label="Niveaux du corpus">
      <button className="secondary" aria-pressed={mode==="chunks"} onClick={()=>switchMode("chunks")}>Chunks</button>
      <button className="secondary" aria-pressed={mode==="files"} onClick={()=>switchMode("files")}>PDF et caches</button>
      <button className="secondary" onClick={()=>setRefresh(v=>v+1)}>Actualiser SQL</button>
    </div>
    <div className="document-toolbar batch-toolbar">
      <label>Recherche dans tout le corpus <input aria-label="Rechercher dans le corpus" value={query} onChange={e=>{setQuery(e.target.value);setConfirmation(null);}} placeholder="Nom ou chemin du PDF…" /></label>
      <label>État global <select aria-label="Filtrer le corpus SQL" value={state} onChange={e=>{setState(e.target.value);setPage(0);}}>
        <option value="">Tous</option><option value="missing">À terminer</option>
        {Object.entries(mode==="chunks"?chunkNames:fileNames).map(([key,label])=><option key={key} value={key}>{label}</option>)}
      </select></label>
    </div>
    <p className="document-note" role="status">{loading?"Chargement SQL…":data.total+" "+(mode==="chunks"?"chunks":"PDF")+" correspondent au filtre global"}.</p>
    <div className="document-table-wrap index-scroll" tabIndex="0" role="region" aria-label={mode==="chunks"?"Chunks des caches":"PDF et caches sources"}>
      {mode==="chunks"?<table className="document-table"><thead><tr><th>PDF / cache</th><th>Chunk et pages PDF</th><th>État</th></tr></thead><tbody>
        {data.items.map(row=><tr key={row.file_id+row.cache_key+row.chunk_id}><td>{row.file_path}<small className="chunk-detail">Cache {row.cache_key.slice(0,12)}</small></td>
          <td><span title={row.chunk_id}>#{row.ordinal+1} · {row.chunk_id.slice(0,12)}</span><small className="chunk-detail">Pages physiques : {(row.pages||[]).join(", ")||"non renseignées"}</small><details><summary>Extrait du chunk</summary><p>{row.preview}</p></details></td>
          <td>{chunkNames[row.state]||row.state}</td></tr>)}
      </tbody></table>:<table className="document-table"><thead><tr><th>PDF</th><th>Cache source</th><th>Chunks</th><th>État du PDF</th></tr></thead><tbody>
        {data.items.map(row=><tr key={row.file_id}><td>{row.path}<small className="chunk-detail">Version {row.version_label||"—"}</small></td><td>
          <select aria-label={"Cache source de "+row.path} value={row.chosen_key||row.cache_key||""} disabled={active||syncing||busy} onChange={e=>choose(row.file_id,e.target.value)}>
            <option value="" disabled>Choisir un cache</option>
            {(row.caches||[]).map(c=><option key={c.cache_key} value={c.cache_key} disabled={c.validation_state!=="valid"||!c.chunk_count}>{c.cache_key.slice(0,12)} · {c.chunk_count} chunks · {c.validation_state==="valid"?"vérifié":"à vérifier"}</option>)}
          </select><small className="chunk-detail">{row.chosen_key?"Choix conservé pour ce profil":row.cache_key?"Un seul cache textuel valide":row.cache_count+" cache(s) observé(s)"}</small>
          <details><summary>Configurations des caches</summary>{(row.caches||[]).map(c=><button className="secondary" key={c.cache_key} onClick={()=>setCacheInfo({fileId:row.file_id,cacheKey:c.cache_key})}>Configuration {c.cache_key.slice(0,12)}</button>)}</details>
          {(row.caches||[]).filter(c=>c.validation_error).map(c=><p key={c.cache_key} className="document-error">{c.cache_key.slice(0,8)} : {c.validation_error}</p>)}</td>
          <td>{row.saved} / {row.chunks} calculés<small className="chunk-detail">{row.published} publiés · {row.missing} manquants</small></td><td>{fileNames[row.state]||row.state}</td></tr>)}
      </tbody></table>}
      {!loading&&!data.items.length&&<p className="document-note">Aucun élément pour ce filtre. Vérifier le profil, les caches choisis et la dernière synchronisation.</p>}
    </div>
    {data.total>100&&<nav className="document-pagination" aria-label="Pages du catalogue"><button disabled={!page||loading} onClick={()=>setPage(v=>v-1)}>Précédent</button><span>{page+1} / {Math.ceil(data.total/100)}</span><button disabled={loading||(page+1)*100>=data.total} onClick={()=>setPage(v=>v+1)}>Suivant</button></nav>}
    {cacheInfo&&<CacheConfiguration {...cacheInfo} onClose={()=>setCacheInfo(null)} />}
  </div>;
}
