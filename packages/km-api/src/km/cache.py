"""Checked PdfParse cache exports, including chunks.json and document.md."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .inventory import cache_entries, confined

MAX_EXPORT=128*1024*1024

def content_hash(value):
    return hashlib.sha256(value).hexdigest()

def records(data,name):
    if name.endswith('.jsonl'):
        result=[json.loads(line) for line in data.splitlines() if line.strip()]
    else:
        result=json.loads(data)
        if isinstance(result,dict):
            result=result.get('index_records',result.get('records'))
    if not isinstance(result,list) or any(not isinstance(r,dict) for r in result):
        raise ValueError('Export de records PdfParse invalide')
    return result

def from_chunks(data,sha,path):
    payload=json.loads(data)
    if (not isinstance(payload,dict) or payload.get('format')!='pdfparse.chunks'
            or payload.get('schema_version')!=1 or not isinstance(payload.get('chunks'),list)):
        raise ValueError('Format chunks.json non pris en charge')
    result=[]
    for source in payload['chunks']:
        if not isinstance(source,dict) or source.get('document_id')!=sha:
            raise ValueError('Chunk associé à un autre contenu PDF')
        text,context=source.get('text'),source.get('context','')
        if not isinstance(text,str) or not isinstance(context,str) or not source.get('chunk_id'):
            raise ValueError('Chunk sans texte ou identité')
        embedding=context+'\n\n'+text if context else text
        locations=source.get('locations',[])
        pages=sorted({item['page_no'] for item in locations if isinstance(item,dict)
                      and type(item.get('page_no')) is int and item['page_no']>0})
        result.append({'format':'pdfparse.index_record','schema_version':4,
            'id':source['chunk_id'],'document_id':sha,'document_path':path,
            'text':text,'context':context,'embedding_text':embedding,'pages':pages,
            'locations':locations,'source_refs':source.get('source_refs',[]),
            'transformation_id':source.get('transformation_id'),
            'token_count':source.get('token_count'),'tokenizer_id':source.get('tokenizer_id'),
            'content_hash':content_hash(embedding.encode()),'record_hash':content_hash(json.dumps(source,sort_keys=True).encode()),
            'km_cache_projection':'chunks-json-v1','source_chunk':source})
    return result

class CacheReader:
    def __init__(self,root,upstream='/data'):
        self.root=Path(root)
        self.upstream=upstream

    def _sha(self,pdf):
        with pdf.open('rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest()

    def _entry(self,directory,relative,sha,expected_records=None,expected_manifest=None,*,external=False):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Dossier de cache lié ou invalide')
        paths=[directory/name for name in ('manifest.json','manisfest.json') if (directory/name).is_file()]
        if len(paths)!=1:raise ValueError('Manifeste absent ou deux manifestes concurrents')
        mp=paths[0]
        if mp.is_symlink() or mp.stat().st_size>8*1024*1024:raise ValueError('Manifeste invalide')
        manifest_raw=mp.read_bytes()
        manifest=json.loads(manifest_raw)
        if manifest.get('pdf_sha256')!=sha:return None
        if manifest.get('format')!='pdfparse.result_cache' or manifest.get('schema_version')!=2:
            raise ValueError('Version de cache non prise en charge')
        files=manifest.get('files')
        if not isinstance(files,dict):raise ValueError('Inventaire du cache absent')
        def read(name):
            target=directory/name
            if target.is_symlink() or not target.is_file() or target.stat().st_size>MAX_EXPORT:
                raise ValueError('Export manquant, lié ou trop volumineux')
            raw=target.read_bytes()
            if name not in files or content_hash(raw)!=files[name]:
                raise ValueError('PdfParse cache checksum mismatch')
            return raw
        def choose(names):
            present=[name for name in names if name in files]
            if len(present)>1:raise ValueError('Exports de cache concurrents')
            return present[0] if present else None
        prep=json.loads(read('preparation.json'))
        if prep.get('document_id')!=sha:raise ValueError('Préparation associée à un autre PDF')
        if expected_manifest and prep.get('manifest_hash')!=expected_manifest:return None
        upstream_path=self.upstream.rstrip('/')+'/'+relative
        index_name=choose(('index.jsonl','index.json'))
        chunk_data=read('chunks.json') if 'chunks.json' in files else None
        chunks=records(read(index_name),index_name) if index_name else from_chunks(chunk_data,sha,upstream_path) if chunk_data else None
        if chunks is None:raise ValueError('Aucun export de chunks signé')
        if expected_records is not None:
            signature=lambda values:[(r.get('id'),r.get('text'),r.get('embedding_text')) for r in values]
            if signature(chunks)!=signature(expected_records):return None
        if chunk_data and index_name:
            source=from_chunks(chunk_data,sha,upstream_path)
            if [(r['id'],r['text']) for r in source]!=[(r['id'],r['text']) for r in chunks]:
                raise ValueError('chunks.json et index ne correspondent pas')
        visual_name=choose(('visuals.jsonl','visual.jsonl'))
        visuals=records(read(visual_name),visual_name) if visual_name else []
        if not visual_name and manifest.get('assets'):raise ValueError('Export des figures absent')
        for kind,items in [('chunk',chunks),('visual',visuals)]:
            for item in items:
                if item.get('document_id')!=sha:raise ValueError('Record associé à un autre PDF')
                old_path=item.get('document_path')
                if old_path!=upstream_path:
                    item['upstream_document_path']=old_path
                    item['document_path']=upstream_path
                if kind=='visual' and item.get('asset_path'):
                    locator=item['asset_path']
                    if PurePosixPath(locator).is_absolute():raise ValueError('Locator de crop absolu refusé')
                    asset=confined(directory/'assets',locator)
                    with asset.open('rb') as stream:observed=hashlib.file_digest(stream,'sha256').hexdigest()
                    if observed!=manifest.get('assets',{}).get(locator):raise ValueError('Empreinte du crop invalide')
                    assets_dir=upstream_path+'.pdfparsecache/'+directory.name+'/assets'
                    if item.get('assets_dir')!=assets_dir:
                        item['upstream_assets_dir']=item.get('assets_dir')
                        item['assets_dir']=assets_dir
                    item.pop('pdfparse_external_asset', None)
                    if external:
                        item['pdfparse_external_asset'] = True
        return {'document_id':sha,'source_sha256':sha,'filepath':upstream_path,
            'chunks':chunks,'visuals':visuals,
            'metadata':{'pdfparse_manifest':prep,'pdfparse_cache_reused':True,
                'pdfparse_source_sha256':sha,'pdfparse_cache_key':directory.name,
                'pdfparse_cache_path':relative+'.pdfparsecache/'+directory.name,
                'pdfparse_cache_dir':str(directory.resolve()),
                'pdfparse_cache_external':bool(external),
                'pdfparse_cache_digest':content_hash(manifest_raw),
                'pdfparse_configuration_hash':manifest.get('configuration_hash'),
                'pdfparse_configuration':manifest.get('configuration'),
                'pdfparse_runtime_configuration':manifest.get('runtime_configuration'),
                'pdfparse_exports':files}}

    def _collect(self,relative,cache_key=None,expected_records=None,expected_manifest=None):
        if cache_key is not None and not re.fullmatch('[a-f0-9]{64}',cache_key):
            raise ValueError('Identifiant de cache invalide')
        pdf=confined(self.root,relative)
        sha=self._sha(pdf)
        entries=cache_entries(pdf, PurePosixPath(self.upstream) / relative)
        choices=[];matches=[]
        for directory, external in entries:
            if cache_key and directory.name!=cache_key:continue
            info={'key':directory.name,'path':relative+'.pdfparsecache/'+directory.name,'valid':False}
            try:
                bundle=self._entry(directory,relative,sha,expected_records,expected_manifest,external=external)
                if bundle is None:
                    info['error']='Cache associé à un autre PDF ou à un autre résultat de préparation.'
                else:
                    metadata=bundle['metadata']
                    info.update(chunks=len(bundle['chunks']),visuals=len(bundle['visuals']),
                        configuration_hash=metadata['pdfparse_configuration_hash'],
                        configuration=metadata['pdfparse_configuration'],
                        runtime_configuration=metadata['pdfparse_runtime_configuration'],source_sha256=sha)
                    if not bundle['chunks'] and not bundle['visuals']:
                        info['error']='Cache PdfParse vide : aucun chunk ni visuel exploitable.'
                    else:
                        info['valid']=True;matches.append(bundle)
            except PermissionError:
                info['error']='Cache illisible : vérifier les droits du lecteur KM.'
            except (OSError,ValueError,TypeError,KeyError,UnicodeError) as exc:
                info['error']=str(exc)[:300] if isinstance(exc,ValueError) else 'Cache incomplet ou invalide ('+type(exc).__name__+').'
            choices.append(info)
        if self._sha(pdf)!=sha:raise ValueError('PDF modifié pendant la lecture des caches')
        return choices,matches

    def inspect(self,relative):
        choices,_=self._collect(relative)
        return {'items':choices,'valid_count':sum(row['valid'] for row in choices)}

    def load(self,relative,*,expected_records=None,expected_manifest=None,cache_key=None):
        choices,matches=self._collect(relative,cache_key,expected_records,expected_manifest)
        if len(matches)>1:raise RuntimeError('Plusieurs caches PdfParse valides : choisir explicitement le cache à utiliser.')
        if not matches:
            if cache_key and not choices:raise ValueError('Cache sélectionné absent ; aucun autre cache utilisé.')
            if choices:raise ValueError('Aucun cache exploitable : '+' ; '.join(row.get('error','Cache invalide') for row in choices)[:450])
            return None
        result=matches[0]
        result['metadata']['pdfparse_cache_issues']=[{'key':row['key'],'error':row['error']} for row in choices if not row['valid']]
        return result
