import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {createServer} from 'vite';

test('atlas loads and opens raw filesystem metadata before indexing, with no CSV upload',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/atlas',pretendToBeVisual:true});
 const keys=['window','document','getComputedStyle','ResizeObserver','Worker','devicePixelRatio','innerWidth','innerHeight','fetch','IS_REACT_ACT_ENVIRONMENT'];
 const previous=Object.fromEntries(keys.map(key=>[key,globalThis[key]]));
 const calls=[];
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,getComputedStyle:dom.window.getComputedStyle,
  devicePixelRatio:1,innerWidth:1200,innerHeight:800,IS_REACT_ACT_ENVIRONMENT:true,
  ResizeObserver:class{constructor(fn){this.fn=fn;}observe(){this.fn();}disconnect(){}},
  Worker:class{postMessage({n}){queueMicrotask(()=>this.onmessage?.({data:{p:new Float32Array(n*2),done:true}}));}terminate(){}},
  fetch:async(url)=>{
   calls.push(url);
   const data=url.includes('/metadata/atlas?')?{source:'filesystem',files:[{file_id:'f',parent_node_id:'p',version_node_id:'v',filename:'notice.pdf',relative_path:'p/V1_0@v/files/notice.pdf'}],
    versions:[{parent_node_id:'p',version_node_id:'v',version_folder:'p/V1_0@v',version_label:'1.0',sample_path:'p/V1_0@v/files/notice.pdf',info:{identifiant:'DOC'},version:{etatId:1}}],references:[],total:1,next_offset:null}
    :{info:{version:{etatId:99}},version:{etatId:1,emploiPur:false},linked_documents:[{uuid:'other',estLie:false}],csv_row:{},source_paths:{info:['p/info.json']},warnings:[]};
   return{ok:true,json:async()=>data};
  }});
 const vite=await createServer({server:{middlewareMode:true,hmr:false},appType:'custom'});
 const root=createRoot(document.getElementById('root'));
 let dispose;
 try{
  const {default:View}=await vite.ssrLoadModule('/src/pages/atlas/view.jsx');
  const {mountPage}=await vite.ssrLoadModule('/src/pages/atlas/controller.js');
  await act(async()=>root.render(React.createElement(View)));
  const canvas=document.querySelector('canvas');
  canvas.getContext=()=>new Proxy({},{get:(obj,key)=>obj[key]??(()=>{})});
  Object.defineProperty(canvas,'clientWidth',{value:1000});Object.defineProperty(canvas,'clientHeight',{value:700});
  dispose=mountPage(document);
  await new Promise(resolve=>setImmediate(resolve));
  const input=document.querySelector('#search');input.value='notice.pdf';input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  document.querySelector('#results button').click();
  await new Promise(resolve=>setImmediate(resolve));
  const fields=Object.fromEntries([...document.querySelectorAll('#meta dt')].map(dt=>[dt.textContent,dt.nextElementSibling.textContent]));
  assert.equal(JSON.parse(fields.version_json).etatId,1);
  assert.equal(JSON.parse(fields.info_json).version.etatId,99);
  assert.equal(JSON.parse(fields.documentsLiees_json)[0].estLie,false);
  assert.deepEqual(JSON.parse(fields.source_paths),{info:['p/info.json']});
  assert.ok(calls.some(url=>url.includes('/metadata/filesystem?path=')));
  assert.equal(document.querySelector('input[type=file]'),null);
  assert.equal(document.querySelector('#source').hidden,false);
 }finally{
  dispose?.();await act(async()=>root.unmount());await vite.close();dom.window.close();
  for(const [key,value]of Object.entries(previous)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}
 }
});
