import test from "node:test";
import assert from "node:assert/strict";
import {JSDOM} from "jsdom";
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {createServer} from "vite";

test("embedding management shows provenance and previews deletion without deleting on click",async()=>{
  const dom=new JSDOM("<div id='root'></div>",{url:"http://localhost"});
  const previous={window:globalThis.window,document:globalThis.document,fetch:globalThis.fetch};
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true});
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
  const calls=[];
  globalThis.fetch=async(url,options={})=>{
    calls.push([url,options]);
    const data=url.includes("delete-preview")?{expected:"a".repeat(64),counts:{published_vectors:1,saved_vectors:1,checkpoint_rows:1,legacy_vectors:0}}
      :url.includes("provenance?")?{profile:"p",encoder:{encoder_fingerprint:"e5-fingerprint",configuration:{max_tokens:768}},
        document:{metadata:{pdfparse_cache_path:"a.pdf.pdfparsecache/cache-key",pdfparse_cache_digest:"manifest-proof",pdfparse_configuration_hash:"parse-proof",pdfparse_source_sha256:"pdf-proof",km_indexing_job_id:"job-proof"}},
        chunks:{items:[{chunk_id:"c",input_hash:"input-proof",indexed_at:"2026-10-28T10:00:00Z",token_count:"24"}],total:1}}
      :{items:[{document_id:"doc",filepath:"/data/a.pdf",chunks:1,published_at:"2026-10-28T10:00:00Z",metadata:{pdfparse_cache_key:"cache-key"}}],total:1};
    return {ok:true,json:async()=>data};
  };
  const vite=await createServer({server:{middlewareMode:true},appType:"custom"});
  const root=createRoot(document.getElementById("root"));
  try{
    const {default:Management}=await vite.ssrLoadModule("/src/features/documents/EmbeddingManagement.jsx");
    await act(async()=>root.render(React.createElement(Management,{profile:"p",refreshKey:0,indexing:false})));

    const opener=[...document.querySelectorAll("button")].find(b=>b.textContent==="Provenance");
    await act(async()=>{opener.focus();opener.click();});
    assert.equal(document.querySelector("dialog").open,true);
    assert.match(document.querySelector("dialog").textContent,/manifest-proof/);
    assert.match(document.body.textContent,/job-proof/);
    assert.match(document.body.textContent,/input-proof/);
    await act(async()=>document.querySelector("[aria-label='Supprimer le vecteur c']").click());
    assert.ok(document.querySelector("[aria-label='Confirmation de suppression des embeddings']"));
    const preview=calls.find(([url])=>url.includes("delete-preview"));
    assert.deepEqual(JSON.parse(preview[1].body),{profile:"p",scope:"chunk",chunk_id:"c"});
    assert.equal(calls.filter(([url])=>url.endsWith("delete-embeddings")).length,0);
    assert.equal([...document.querySelectorAll("button")].find(b=>b.textContent==="Confirmer la suppression").disabled,true);
    await act(async()=>[...document.querySelectorAll("button")].find(b=>b.textContent==="Annuler").click());
    assert.equal(document.querySelector("[aria-label='Confirmation de suppression des embeddings']"),null);
    assert.equal(document.querySelectorAll("dialog").length,1);
    await act(async()=>document.querySelector("dialog").dispatchEvent(new dom.window.Event("cancel",{cancelable:true})));
    assert.equal(document.querySelector("dialog"),null);
    assert.equal(document.activeElement,opener);
  }finally{
    await act(async()=>root.unmount());await vite.close();dom.window.close();
    for(const [key,value]of Object.entries(previous))if(value===undefined)delete globalThis[key];else globalThis[key]=value;
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  }
});

test("cache chooser exposes invalid caches and requires an explicit valid selection",async()=>{
  const dom=new JSDOM("<div id='root'></div>",{url:"http://localhost"});
  const previous={window:globalThis.window,document:globalThis.document,fetch:globalThis.fetch};
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true});
  const selected=[];
  globalThis.fetch=async()=>({ok:true,json:async()=>({items:[{key:"a".repeat(64),valid:false,chunks:0,error:"Cache vide"},{key:"b".repeat(64),valid:true,chunks:12,configuration:{chunk_size:768}}]})});
  const vite=await createServer({server:{middlewareMode:true},appType:"custom"});
  const root=createRoot(document.getElementById("root"));
  try{
    const {default:Choice}=await vite.ssrLoadModule("/src/features/documents/CacheChoice.jsx");
    await act(async()=>root.render(React.createElement(Choice,{doc:{id:"d",path:"a.pdf",cache_count:2},value:"",onChange:v=>selected.push(v),disabled:false,revision:0})));
    await act(async()=>document.querySelector("button").click());
    const select=document.querySelector("select");
    assert.equal(select.options[1].disabled,true);
    assert.match(document.body.textContent,/Cache vide/);
    assert.deepEqual(selected,[]);
    await act(async()=>{select.value="b".repeat(64);select.dispatchEvent(new dom.window.Event("change",{bubbles:true}));});
    assert.deepEqual(selected,["b".repeat(64)]);
  }finally{
    await act(async()=>root.unmount());await vite.close();dom.window.close();
    for(const[key,value]of Object.entries(previous))if(value===undefined)delete globalThis[key];else globalThis[key]=value;
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  }
});