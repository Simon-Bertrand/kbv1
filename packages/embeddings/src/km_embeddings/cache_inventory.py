"""Compare verified filesystem caches with the published text vectors, never trigger parsing."""
import hashlib
from km.inventory import read_metadata, bind_bundle

def describe_caches(parser, folder='', selections=None, rows=None):
    rows=parser.documents(folder) if rows is None else [dict(row) for row in rows]
    signatures=[]
    for row in rows:
        row.update(indexing_status='no_cache',cached_chunks=None,indexed_chunks=None,remaining_chunks=None)
        if not row.get('cache_count'):
            continue
        try:
            cache_key=(selections or {}).get(row.get('id'))
            bundle=parser.existing_bundle(row['path'],**({'cache_key':cache_key} if cache_key else {}))
            if bundle is None:
                row.update(indexing_status='blocked',comparison_error='Aucun cache compatible avec le PDF actuel.')
                continue
            bundle=bind_bundle(bundle,read_metadata(parser.root,row['path']))
            chunks={record['id']:hashlib.sha256((record.get('text') or record.get('embedding_text')).encode()).hexdigest() for record in bundle['chunks']}
            if len(chunks)!=len(bundle['chunks']) or not chunks:
                raise ValueError('Cache vide ou identités de chunks dupliquées.')
            row['cached_chunks']=len(chunks)
            row['selected_cache_key']=bundle.get('metadata',{}).get('pdfparse_cache_key')
            signatures.append({'row':row,'document_id':bundle['document_id'],'filepath':bundle['filepath'],'hashes':chunks,'cache_digest':bundle.get('metadata',{}).get('pdfparse_cache_digest')})
        except PermissionError:
            row.update(indexing_status='blocked',comparison_error='Cache illisible : sudo python3 -m scripts.ops.manage fix-permissions')
        except (ValueError,RuntimeError,OSError,KeyError,TypeError):
            row.update(indexing_status='blocked',comparison_error='Cache à vérifier : incomplet, altéré ou plusieurs profils présents.')
    return rows,signatures

def compare_publications(signatures, published):
    """A checkpoint alone is not an indexed chunk: require a published vector."""
    for item in signatures:
        row=item['row']
        publication=published.get((item['document_id'],item['filepath']),{})
        vectors=publication.get('inputs',{})
        matched=sum(vectors.get(chunk)==digest for chunk,digest in item['hashes'].items())
        remaining=len(item['hashes'])-matched
        same_cache=not item.get('cache_digest') or publication.get('cache_digest')==item['cache_digest']
        exact=set(vectors)==set(item['hashes']) and same_cache
        row.update(indexed_chunks=matched,remaining_chunks=remaining,
                   indexing_status=('indexed' if exact else 'republish') if not remaining else 'partial' if matched else 'new')

def cache_summary(rows):
    valid=[r for r in rows if r.get('cached_chunks') is not None and r.get('indexed_chunks') is not None]
    return {'cached_documents':len(valid),'cached_chunks':sum(r['cached_chunks'] for r in valid),
            'indexed_chunks':sum(r['indexed_chunks'] for r in valid),
            'remaining_chunks':sum(r['remaining_chunks'] for r in valid),
            'blocked_documents':sum(r.get('indexing_status')=='blocked' for r in rows)}