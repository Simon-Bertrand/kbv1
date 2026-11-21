import test from 'node:test';
import assert from 'node:assert/strict';
import { filesystemGraph,rawValue } from '../src/pages/atlas/graph.js';
const versions=[{parent_node_id:'parent-a',version_node_id:'v1',version_label:'1.0',version_folder:'parent-a/V1_0@v1',info:{identifiant:'DOC',currentVersionNodeId:'v2'},version:{etatId:1}},
 {parent_node_id:'parent-a',version_node_id:'v2',version_label:'2.0',version_folder:'parent-a/V2_0@v2',info:{identifiant:'DOC'},version:{etatId:2}}];
const files=versions.map((v,i)=>({file_id:'file-'+i,parent_node_id:v.parent_node_id,version_node_id:v.version_node_id,filename:'notice.pdf',relative_path:v.version_folder+'/files/notice.pdf'}));
const data={versions,files,references:[{source_version_id:'v1',target_parent_id:'target-parent',raw:{estLie:false}}]};

test('filesystem atlas distinguishes identity, historical versions and same-named files',()=>{
 const g=filesystemGraph(data);
 assert.equal(g.nodes.filter(n=>n.kind==='parent').length,1);
 assert.equal(g.nodes.filter(n=>n.kind==='version').length,2);
 assert.equal(g.nodes.filter(n=>n.kind==='file').length,2);
 assert.equal(new Set(g.nodes.map(n=>n.key)).size,g.nodes.length);
 const v=g.nodes.find(n=>n.key==='version:v1');
 assert.equal(v.fields.version.etatId,1);
 assert.equal(v.fields.info.currentVersionNodeId,'v2');
});

test('declared references target the parent identity even for estLie false; never a guessed version',()=>{
 const g=filesystemGraph(data);
 const edge=g.edges[g.edgeKinds.indexOf('reference')];
 assert.equal(g.nodes[edge[0]].key,'version:v1');
 assert.equal(g.nodes[edge[1]].key,'parent:target-parent');
 assert.equal(g.nodes[edge[1]].kind,'reference');
 const targetVersion={...versions[0],parent_node_id:'target-parent',version_node_id:'target-v1',version_folder:'target/V1@target-v1'};
 const loaded=filesystemGraph({...data,versions:[...versions,targetVersion]});
 const ref=loaded.edges[loaded.edgeKinds.indexOf('reference')];
 assert.equal(loaded.nodes[ref[1]].kind,'parent');
});

test('duplicate version pages deduplicate, conflicting identities fail and missing uuid stays unresolved',()=>{
 const g=filesystemGraph({...data,versions:[...versions,...versions],files:[...files,...files],references:[...data.references,{source_version_id:'v1',target_parent_id:null}]});
 assert.equal(g.nodes.filter(n=>n.kind==='version').length,2);
 assert.equal(g.unresolved,1);
 assert.throws(()=>filesystemGraph({...data,versions:[...versions,{...versions[0],parent_node_id:'other'}]}),/Identité de version/);
 assert.ok(filesystemGraph(data,2).omitted>0);
});

test('raw scalar metadata distinguishes booleans, nulls, numeric codes and strings',()=>{
 assert.equal(rawValue(false),'false');assert.equal(rawValue(null),'null');
 assert.equal(rawValue(12345),'12345');assert.equal(rawValue('12345'),'"12345"');
});
