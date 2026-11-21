import React,{useEffect,useState} from "react";
import Modal from "./Modal.jsx";

export default function ChunkJobErrors({jobId,onClose}){
  const [page,setPage]=useState(0),[data,setData]=useState({items:[],total:0}),[error,setError]=useState("");
  useEffect(()=>{
    const controller=new AbortController();setError("");
    fetch("/api/index/chunk-jobs/files?"+new URLSearchParams({job_id:jobId,limit:"100",offset:String(page*100)}),{signal:controller.signal})
      .then(async response=>{const value=await response.json();if(!response.ok)throw new Error(value.detail||"Échecs indisponibles.");return value;})
      .then(value=>{if(!controller.signal.aborted)setData(value);}).catch(e=>{if(e.name!=="AbortError")setError(e.message);});
    return()=>controller.abort();
  },[jobId,page]);
  return <Modal title="PDF en erreur dans ce lot" onClose={onClose}>
    {error&&<p role="alert">{error}</p>}<p>{data.total} PDF en erreur conservés dans SQL.</p>
    <table className="document-table"><thead><tr><th>PDF</th><th>Cache</th><th>Erreur</th></tr></thead><tbody>
      {data.items.map(row=><tr key={row.file_id}><td>{row.path}</td><td>{row.cache_key.slice(0,12)}</td><td>{row.error}</td></tr>)}
    </tbody></table>
    {data.total>100&&<nav className="document-pagination" aria-label="Pages des échecs"><button disabled={!page} onClick={()=>setPage(v=>v-1)}>Précédent</button><span>{page+1} / {Math.ceil(data.total/100)}</span><button disabled={(page+1)*100>=data.total} onClick={()=>setPage(v=>v+1)}>Suivant</button></nav>}
  </Modal>;
}
