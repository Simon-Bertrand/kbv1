import React,{useCallback,useEffect,useRef,useState} from "react";
import "./indexation.css";
import Profiles from "./Profiles.jsx";
import ChunkWorkspace from "./ChunkWorkspace.jsx";
import ChunkJobErrors from "./ChunkJobErrors.jsx";

async function request(path,options={}){
  const response=await fetch("/api/index/"+path,options);
  const value=await response.json();
  if(!response.ok)throw new Error(value.detail||"Service KM indisponible.");
  return value;
}
const labels={queued:"En attente",running:"En cours",succeeded:"Terminé",partial:"Terminé avec erreurs",failed:"Échec",interrupted:"Interrompu",stopped:"Arrêté · reprise possible"};
const isActive=job=>["running","queued"].includes(job.status);

export default function Indexation(){
  const [tab,setTab]=useState("indexation"),[profile,setProfile]=useState(null),[compatible,setCompatible]=useState(false);
  const [jobs,setJobs]=useState([]),[sync,setSync]=useState({status:"never"}),[error,setError]=useState("");
  const [revision,setRevision]=useState(0),[profileRevision,setProfileRevision]=useState(0),[syncPending,setSyncPending]=useState(false),[stopPending,setStopPending]=useState(false);
  const [errorJob,setErrorJob]=useState(null);
  const previous=useRef({active:false,sync:"never"});
  const changeProfile=useCallback((id,canIndex)=>{setProfile(id);setCompatible(canIndex);},[]);
  useEffect(()=>{
    const controller=new AbortController();let timer;
    async function poll(){
      try{
        const results=await Promise.allSettled([request("jobs",{signal:controller.signal}),request("catalog/status",{signal:controller.signal})]);
        if(controller.signal.aborted)return;
        const old=previous.current;
        const work=results[0].status==="fulfilled"?results[0].value:null;
        const status=results[1].status==="fulfilled"?results[1].value:null;
        if(work)setJobs(work.items);if(status)setSync(status);
        const failed=results.find(r=>r.status==="rejected");if(failed)setError(failed.reason.message);else setError("");
        const active=work?work.items.some(isActive):old.active,newSync=status?.status||old.sync;
        if((old.active&&!active)||(old.sync==="running"&&newSync!=="running"))setRevision(v=>v+1);
        if(old.active&&!active)setProfileRevision(v=>v+1);
        previous.current={active,sync:newSync};
      }catch(e){if(e.name!=="AbortError")setError(e.message);}
      if(!controller.signal.aborted)timer=setTimeout(poll,3000);
    }
    poll();return()=>{controller.abort();clearTimeout(timer);};
  },[]);
  const active=jobs.some(isActive),syncing=sync.status==="running";
  const current=jobs.find(job=>isActive(job)&&job.unit==="chunk");
  async function synchronize(){
    setSyncPending(true);setError("");
    try{const status=await request("catalog/sync",{method:"POST"});setSync(status);previous.current.sync="running";}
    catch(e){setError(e.message);}finally{setSyncPending(false);}
  }
  async function stop(){
    setStopPending(true);setError("");
    try{await request("chunk-jobs/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({job_id:current.id})});setJobs(rows=>rows.map(row=>row.id===current.id?{...row,stop_requested:true}:row));}
    catch(e){setError(e.message);}finally{setStopPending(false);}
  }
  function submitted(job){setJobs(rows=>[job,...rows.filter(row=>row.id!==job.id)]);previous.current.active=true;setRevision(v=>v+1);}
  return <section id="library" className="view km-index" data-workspace={tab}>
    <div className="workspace-heading"><div><h1>Indexation</h1></div></div>
    {error&&<p className="notice" role="alert">{error}</p>}
    <div className="index-tabs" role="tablist" aria-label="Espace embedding">
      {[["indexation","Indexation"],["gestion","Gestion des embeddings"]].map(([id,label])=><button key={id} id={"tab-"+id} role="tab" aria-selected={tab===id} aria-controls={"panel-"+id} tabIndex={tab===id?0:-1} onClick={()=>setTab(id)} onKeyDown={e=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(e.key)){e.preventDefault();const next=e.key==="Home"?"indexation":e.key==="End"?"gestion":tab==="indexation"?"gestion":"indexation";setTab(next);document.getElementById("tab-"+next)?.focus();}}}>{label}{id==="indexation"&&active&&<span className="tab-activity">En cours</span>}</button>)}
    </div>
    <Profiles mode={tab} selected={profile} onChange={changeProfile} refreshKey={profileRevision} indexing={active} onEmbeddingsChanged={()=>{setRevision(v=>v+1);setProfileRevision(v=>v+1);}} />
    <div className="document-toolbar batch-toolbar"><button className="secondary" disabled={active||syncing||syncPending} onClick={synchronize}>{syncing?"Synchronisation en cours…":"Synchroniser PDF, caches et chunks"}</button>
      <span role="status">{sync.status==="never"?"Première synchronisation nécessaire.":syncing?`${sync.pdf_count||0} PDF · ${sync.cache_count||0} caches · ${sync.chunks_count||0} chunks parcourus`:sync.status==="succeeded"?`${sync.pdf_count} PDF · ${sync.valid_caches||0} caches vérifiés · ${sync.chunks_count||0} chunks · ${new Date(sync.finished_at).toLocaleString("fr-FR")}`:`Synchronisation ${sync.status} : ${sync.error||"relancer explicitement"}`}</span>
    </div>
    <div id="panel-indexation" role="tabpanel" aria-labelledby="tab-indexation" hidden={tab!=="indexation"}>
      {profile?<ChunkWorkspace key={profile} profile={profile} compatible={compatible} active={active} syncing={syncing} revision={revision} onSubmitted={submitted} visible={tab==="indexation"}/>:<p>Choisir ou enregistrer un profil pour consulter les chunks.</p>}
      <aside className="index-monitor" aria-label="Suivi des lots de chunks"><h2>Lots et reprise</h2>
        <details className="index-help"><summary>Fonctionnement des lots</summary><p>Les lots traitent les chunks manquants du corpus. Chaque PDF est publié pour le RAG une fois complet. Les vecteurs déjà calculés sont conservés.</p><p>L’arrêt attend la fin de l’appel E5 en cours. Les sauvegardes se font par groupes de 16. Relancer un lot reprend les manquants.</p></details>
        {current&&<button className="secondary" disabled={stopPending||current.stop_requested} onClick={stop}>{current.stop_requested?"Arrêt demandé…":"Arrêter après le lot en cours"}</button>}
        
        <div className="jobs-scroll" tabIndex="0" role="region" aria-label="Historique des lots">
          {!jobs.length&&<p>Aucun travail lancé.</p>}
          {jobs.filter(job=>job.profile===profile||isActive(job)).map(job=><article className="index-job" key={job.id}><strong>{labels[job.status]||job.status}</strong>
            <p>Profil {job.profile?.slice(0,16)} · {job.unit==="chunk"?job.all_chunks?"Tous les manquants":"Lot de chunks":"Ancien lot de PDF"}</p>
            <p>{job.processed??0} / {job.total} {job.unit==="chunk"?"chunks traités":"PDF traités"}{job.unit==="chunk"&&` · ${job.completed||0} nouveaux vecteurs · ${job.published_documents||0} PDF publiés`}</p>
            {job.chunk_progress&&isActive(job)&&<p>{job.chunk_progress.saved} / {job.chunk_progress.total} vecteurs disponibles pour le PDF en cours.</p>}
            {job.blocked_documents>0&&<p>{job.blocked_documents} PDF sans cache choisi ou exploitable, exclus de ce lot.</p>}
            {(job.failed_documents>0||job.failed_count>0)&&<p className="document-error">{job.failed_documents??job.failed_count} PDF en erreur. Les autres documents continuent.</p>}
            {job.unit==="chunk"&&job.failed_documents>0&&<button className="secondary" onClick={()=>setErrorJob(job.id)}>Tous les PDF en erreur de ce lot</button>}
            {(job.document_results||[]).filter(row=>row.status==="failed").map(row=><p key={row.document_id} className="document-error">{row.path} : {row.error}</p>)}
            {job.error&&<p role="alert">{job.error}</p>}
          </article>)}
        </div>
      </aside>
    </div>
    {errorJob&&<ChunkJobErrors jobId={errorJob} onClose={()=>setErrorJob(null)} />}
  </section>;
}
