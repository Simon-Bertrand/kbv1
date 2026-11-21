// Filesystem identities are authoritative; filenames never resolve references.
export const atlasHeaders = ["parent", "nature", "etatId"];
export function rawValue(value) {
  return value === undefined ? "[champ absent]" : typeof value === "string" ? JSON.stringify(value) : JSON.stringify(value, null, 2);
}

export function filesystemGraph(data, maxNodes = 10000) {
  const nodes=[],edges=[],edgeKinds=[],lookup=new Map(),seen=new Set(),versions=new Map();
  let omitted=0,unresolved=0;
  const add=(node)=>{
    if(lookup.has(node.key)) return lookup.get(node.key);
    if(nodes.length>=maxNodes){omitted++;return -1;}
    lookup.set(node.key,nodes.length);nodes.push(node);return nodes.length-1;
  };
  const edge=(a,b,kind)=>{
    if(a<0||b<0||a===b)return;
    const key=JSON.stringify([a,b,kind]);
    if(!seen.has(key)){seen.add(key);edges.push([a,b]);edgeKinds.push(kind);}
  };
  for(const v of data.versions || []) {
    const previous=versions.get(v.version_node_id);
    if(previous && (previous.parent_node_id!==v.parent_node_id || previous.version_folder!==v.version_folder))
      throw Error("Identité de version associée à plusieurs dossiers. Actualiser et vérifier les métadonnées.");
    if(previous)continue;
    versions.set(v.version_node_id,v);
    const row=[v.parent_node_id,rawValue(v.info?.nature),rawValue(v.version?.etatId)];
    const parent=add({key:'parent:'+v.parent_node_id,kind:'parent',name:'Identité · '+v.parent_node_id,
      row,fields:{parent_node_id:v.parent_node_id}});
    const version=add({key:'version:'+v.version_node_id,kind:'version',
      name:'Version '+v.version_label+' · '+(v.info?.identifiant || v.version_node_id), row,
      path:v.sample_path, fields:{parent_node_id:v.parent_node_id,version_node_id:v.version_node_id,
        version_label:v.version_label,version_folder:v.version_folder,info:v.info,version:v.version,
        warnings:v.warnings,source_paths:v.source_paths,metadata_error:v.metadata_error}});
    edge(parent,version,'version');
  }
  for(const f of data.files || []) {
    const v=versions.get(f.version_node_id);
    if(!v || v.parent_node_id!==f.parent_node_id)throw Error('Fichier sans version filesystem vérifiée.');
    const file=add({key:'file:'+f.file_id,kind:'file',name:f.filename+' · version '+v.version_label+' · '+(v.info?.identifiant || f.parent_node_id),
      row:[f.parent_node_id,rawValue(v.info?.nature),rawValue(v.version?.etatId)],path:f.relative_path,
      fields:{file_id:f.file_id,relative_path:f.relative_path,filename:f.filename,parent_node_id:f.parent_node_id,
        version_node_id:f.version_node_id,version_label:v.version_label}});
    edge(lookup.get('version:'+f.version_node_id)??-1,file,'file');
  }
  for(const ref of data.references || []) {
    const from=lookup.get('version:'+ref.source_version_id);
    if(from===undefined)continue;
    const target=ref.target_parent_id;
    if(typeof target!=='string'||!target.trim()){unresolved++;continue;}
    const to=add({key:'parent:'+target,kind:'reference',name:target+' · identité hors extrait',row:null,
      fields:{parent_node_id:target,scope:'Identité référencée, non décrite dans cet extrait. Aucune version cible déduite.'}});
    // estLie is evidence, never a boolean gate or a version selector.
    edge(from,to,'reference');
  }
  return {nodes,edges,edgeKinds,omitted,unresolved};
}
