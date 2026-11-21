import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { createServer } from "vite";

test("Documents uses the real React lifecycle for folders, filtering and corpus consultation", async () => {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", { url:"http://localhost/#sources" });
  const previous={window:globalThis.window,document:globalThis.document,history:globalThis.history,fetch:globalThis.fetch};
  Object.assign(globalThis,{window:dom.window,document:dom.window.document,history:dom.window.history,IS_REACT_ACT_ENVIRONMENT:true});
  const calls=[];
  globalThis.fetch=async (url,options)=>{
    calls.push([url,options.signal]);
    const data=url.startsWith("/api/knowledge")?{items:[{id:"d"+"a".repeat(32),observed:{original_name:"Notice.pdf"},filepath:"/data/Notice.pdf",metadata:{référence:"ZX-12"}}],total:1}
      :url.includes("path=Plans")?{entries:[{name:"schéma #1.pdf",type:"file",size:2048,mtime:"2026-10-26T08:00:00Z"}]}
      :{entries:[{name:"Plans",type:"directory"},{name:"note.txt",type:"file",size:0}]};
    return {ok:true,json:async()=>data};
  };
  const vite=await createServer({server:{middlewareMode:true},appType:"custom"});
  const root=createRoot(document.getElementById("root"));
  try {
    const {default:Sources}=await vite.ssrLoadModule("/src/features/documents/Sources.jsx");
    await act(async()=>{root.render(React.createElement(Sources));});
    assert.ok(document.body.textContent.includes("Documents"));
    const folder=[...document.querySelectorAll(".document-name")].find(b=>b.textContent==="Plans");
    await act(async()=>folder.click());
    assert.ok(calls.some(([url])=>url==="/api/files/list?path=Plans"));
    assert.equal(document.querySelector(".document-breadcrumb [aria-current]").textContent,"Plans");
    await act(async()=>document.querySelector(".document-name").click());
    assert.ok(document.querySelector("[aria-label='Détails du fichier']").textContent.includes("schéma #1.pdf"));
    const download=document.querySelector(".document-download");
    assert.equal(new URL(download.href).searchParams.get("path"),"Plans/schéma #1.pdf");
    await act(async()=>document.getElementById("corpus-tab").click());
    assert.ok(document.querySelector(".document-table").textContent.includes("Notice.pdf"));
    await act(async()=>document.querySelector(".document-name").click());
    assert.ok(document.querySelector("[aria-label='Métadonnées du document']").textContent.includes("ZX-12"));
    assert.match(document.querySelector(".document-download").getAttribute("href"),/^\/viewer\/d[a-f0-9]{32}$/);
    assert.equal(document.querySelectorAll("iframe").length,0);
    assert.ok(calls.slice(0,-1).some(([,signal])=>signal.aborted));
  } finally {
    await act(async()=>root.unmount());
    await vite.close();
    dom.window.close();
    for(const [key,value] of Object.entries(previous)) if(value===undefined)delete globalThis[key];else globalThis[key]=value;
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  }
});
