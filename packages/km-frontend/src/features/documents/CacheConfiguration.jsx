import React,{useEffect,useState} from "react";
import Modal from "./Modal.jsx";

export default function CacheConfiguration({fileId,cacheKey,onClose}){
  const [data,setData]=useState(null),[error,setError]=useState("");
  useEffect(()=>{
    const controller=new AbortController();
    fetch("/api/index/catalog/cache?"+new URLSearchParams({file_id:fileId,cache_key:cacheKey}),{signal:controller.signal})
      .then(async response=>{const value=await response.json();if(!response.ok)throw new Error(value.detail||"Configuration indisponible.");return value;})
      .then(value=>{if(!controller.signal.aborted)setData(value);}).catch(e=>{if(e.name!=="AbortError")setError(e.message);});
    return()=>controller.abort();
  },[fileId,cacheKey]);
  return <Modal title={"Cache source "+cacheKey.slice(0,12)} onClose={onClose}>
    <p>Configuration de parsing conservée telle quelle. Elle est distincte du profil de l’encodeur.</p>
    {error&&<p role="alert">{error}</p>}{data?<pre>{JSON.stringify(data,null,2)}</pre>:!error&&<p>Chargement SQL…</p>}
  </Modal>;
}
