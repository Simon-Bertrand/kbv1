import test from "node:test";
import assert from "node:assert/strict";
import {JSDOM} from "jsdom";
import React,{act} from "react";
import {createRoot} from "react-dom/client";
import {createServer} from "vite";

const profiles={items:[{profile:"p",label:"E5",compatible:true,verified:true,active:true,configuration:{max_tokens:768},encoder_fingerprint:"current",documents:0,chunks:0,saved:0,failed:0}],current_profile:"p",active_profile:"p",encoder:{model:"fixture",dimension:1024,max_tokens:768}};
const count={pending:24,computed:16,published:0,failed:0,interrupted:0,encoding:0};
async function render(fetcher,component="Indexation",props={}){
  const previous={window:globalThis.window,document:globalThis.document,fetch:globalThis.fetch};
  const dom=new JSDOM("<div id='root'></div>",{url:"http://localhost/#library"});
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true});
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  dom.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
  const calls=[];
  globalThis.fetch=async(url,options={})=>{calls.push([url,options]);return {ok:true,json:async()=>fetcher(url,options)};};
  const vite=await createServer({server:{middlewareMode:true},appType:"custom"});
  const root=createRoot(document.getElementById("root"));
  const {default:View}=await vite.ssrLoadModule("/src/features/documents/"+component+".jsx");
  await act(async()=>root.render(React.createElement(View,props)));
  return {calls,dom,button:text=>[...document.querySelectorAll("button")].find(b=>b.textContent===text),async close(){
    await act(async()=>root.unmount());await vite.close();dom.window.close();
    for(const [key,value] of Object.entries(previous))if(value===undefined)delete globalThis[key];else globalThis[key]=value;
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  }};
}
function base(url){
  if(url.endsWith("/profiles"))return profiles;
  if(url.endsWith("/catalog/status"))return {status:"succeeded",pdf_count:1,valid_caches:1,chunks_count:40,finished_at:"2026-10-29T12:00:00Z"};
  if(url.includes("/catalog/chunks?"))return {items:[],total:24,summary:count,pdfs:{total:1,blocked:0}};
  return {items:[]};
}

test("chunk budget and all jobs require confirmation and never send PDF selections",async()=>{
  const ui=await render((url,options)=>url.endsWith("/chunk-jobs")?{id:"a".repeat(32),profile:"p",unit:"chunk",status:"queued",total:24,processed:0}:base(url));
  try{
    assert.equal(ui.calls.filter(([,o])=>o.method==="POST").length,0);
    assert.match(document.body.textContent,/Compteurs et filtres sur tout le corpus SQL/);
    assert.equal(document.querySelector("[aria-label='Nombre de chunks par lot']").value,"500");
    await act(async()=>ui.button("Tout lancer").click());
    assert.equal(ui.calls.filter(([,o])=>o.method==="POST").length,0);
    await act(async()=>ui.button("Confirmer le lancement").click());
    const [url,options]=ui.calls.find(([,o])=>o.method==="POST");
    assert.equal(url,"/api/index/chunk-jobs");
    assert.deepEqual(JSON.parse(options.body),{profile:"p",query:"",limit:500,all_chunks:true});
    assert.ok(ui.button("Tout lancer").disabled);
    assert.equal(ui.calls.some(([url])=>url.includes("candidates")),false);
  }finally{await ui.close();}
});

test("cooperative stop is requested once without restarting a service",async()=>{
  const active={id:"b".repeat(32),profile:"p",unit:"chunk",status:"running",total:40,processed:16,completed:16};
  const ui=await render(url=>url.endsWith("/jobs")?{items:[active]}:url.endsWith("/chunk-jobs/stop")?{stop_requested:true}:base(url));
  try{
    await act(async()=>ui.button("Arrêter après le lot en cours").click());
    const stop=ui.calls.find(([url])=>url.endsWith("/chunk-jobs/stop"));
    assert.deepEqual(JSON.parse(stop[1].body),{job_id:active.id});
    assert.ok(ui.button("Arrêt demandé…").disabled);
    assert.ok(ui.button("Synchroniser PDF, caches et chunks").disabled);
    assert.equal(ui.calls.filter(([,o])=>o.method==="POST").length,1);
  }finally{await ui.close();}
});

test("PDF cache choice and global status filter are server operations",async()=>{
  const key="a".repeat(64),other="b".repeat(64);let chosen=null;
  const ui=await render((url,options)=>{
    if(url.endsWith("cache-choice")){chosen=JSON.parse(options.body).cache_key;return {};}
    if(url.includes("/catalog/files?")){
      const published=new URL(url,"http://localhost").searchParams.get("state")==="published";
      return {items:[{file_id:"f".repeat(24),path:published?"beyond-first-page.pdf":"source.pdf",state:published?"published":chosen?"pending":"ambiguous",cache_count:2,chunks:3,saved:0,published:0,missing:3,chosen_key:chosen,
        caches:[{cache_key:key,validation_state:"valid",chunk_count:3},{cache_key:other,validation_state:"valid",chunk_count:4}]}],total:1};
    }
    return base(url);
  },"ChunkWorkspace",{profile:"p",compatible:true,active:false,syncing:false,revision:0,onSubmitted:()=>{}});
  try{
    await act(async()=>ui.button("PDF et caches").click());
    const select=document.querySelector("[aria-label='Cache source de source.pdf']");
    assert.equal(select.value,"");
    await act(async()=>{select.value=other;select.dispatchEvent(new ui.dom.window.Event("change",{bubbles:true}));});
    const post=ui.calls.find(([url])=>url.endsWith("cache-choice"));
    assert.deepEqual(JSON.parse(post[1].body),{profile:"p",file_id:"f".repeat(24),cache_key:other});
    await act(async()=>{const filter=document.querySelector("[aria-label='Filtrer le corpus SQL']");filter.value="published";filter.dispatchEvent(new ui.dom.window.Event("change",{bubbles:true}));});
    assert.match(document.body.textContent,/beyond-first-page.pdf/);
    assert.ok(ui.calls.some(([url])=>url.includes("catalog/files?")&&url.includes("state=published")));
    await act(async()=>ui.button("Chunks").click());
    assert.ok(document.querySelector("[aria-label='Chunks des caches']"));
  }finally{await ui.close();}
});

test("profile management remains available and keyboard tabs preserve routing class",async()=>{
  const ui=await render(base);
  try{
    document.querySelector(".km-index").classList.add("active");
    await act(async()=>ui.button("Configuration du profil").click());
    assert.equal(document.querySelector("dialog").open,true);
    await act(async()=>document.querySelector("dialog").dispatchEvent(new ui.dom.window.Event("cancel",{cancelable:true})));
    assert.equal(document.querySelector("dialog"),null);
    await act(async()=>document.getElementById("tab-indexation").dispatchEvent(new ui.dom.window.KeyboardEvent("keydown",{key:"ArrowRight",bubbles:true})));
    assert.equal(document.getElementById("panel-indexation").hidden,true);
    assert.equal(document.getElementById("panel-gestion").hidden,false);
    assert.ok(document.querySelector(".km-index").classList.contains("active"));
  }finally{await ui.close();}
});

test("synchronizing sources never starts embeddings automatically",async()=>{
  const ui=await render(url=>url.endsWith("/catalog/sync")?{status:"running",pdf_count:0}:base(url));
  try{
    await act(async()=>ui.button("Synchroniser PDF, caches et chunks").click());
    assert.equal(ui.calls.filter(([url,o])=>url.endsWith("/catalog/sync")&&o.method==="POST").length,1);
    assert.equal(ui.calls.filter(([url])=>url.endsWith("/chunk-jobs")).length,0);
    assert.ok(ui.button("Tout lancer").disabled);
  }finally{await ui.close();}
});
