"""Explicit, scoped vector deletion. Source snapshots and disk files are never deleted."""
import hashlib
import json
import uuid
from psycopg.types.json import Jsonb
from . import sql_client
from .profiles import lock

def _scope(profile,scope,document_id,chunk_id):
    if scope not in ('profile','document','chunk'):raise ValueError('Portée de suppression invalide.')
    if scope=='profile':
        if document_id or chunk_id:raise ValueError('Cible incompatible avec la portée profil.')
    elif scope=='document':
        if not document_id or chunk_id:raise ValueError('Document requis, sans chunk.')
    elif not chunk_id or document_id:raise ValueError('Chunk requis, sans document.')
    return document_id if scope=='document' else chunk_id if scope=='chunk' else profile

def plan(conn,profile,scope,document_id=None,chunk_id=None):
    target=_scope(profile,scope,document_id,chunk_id)
    header=conn.execute(sql_client.DELETION_PROFILE,(profile,)).fetchone()
    if not header:raise ValueError('Profil texte inconnu.')
    counts={'published_vectors':0,'checkpoint_rows':0,'saved_vectors':0,'legacy_vectors':0}
    digest=hashlib.sha256(json.dumps([dict(header),scope,target],sort_keys=True,default=str).encode())
    for name,query,params in sql_client.embedding_deletion_queries(profile,header['encoder_fingerprint'],scope,document_id,chunk_id):
        digest.update(name.encode()+b'\0')
        with conn.cursor(name='km_delete_'+uuid.uuid4().hex) as cursor:
            cursor.execute(query,params)
            for row in cursor:
                counts[name]+=1
                if name=='checkpoint_rows':counts['saved_vectors']+=bool(row['has_vector'])
                digest.update(json.dumps(dict(row),sort_keys=True,default=str,separators=(',',':')).encode()+b'\n')
    return {'profile':profile,'scope':scope,'target':target,'document_id':document_id,'chunk_id':chunk_id,
            'counts':counts,'expected':digest.hexdigest(),'encoder_fingerprint':header['encoder_fingerprint']}

def remove(conn,profile,scope,expected,confirmation,document_id=None,chunk_id=None):
    if confirmation!='SUPPRIMER':raise ValueError('La confirmation SUPPRIMER est requise.')
    lock(conn)
    conn.execute(sql_client.DELETION_LOCK_TIMEOUT)
    conn.execute(sql_client.DELETION_STATEMENT_TIMEOUT)
    conn.execute(sql_client.LOCK_DELETION_TABLES)
    current=plan(conn,profile,scope,document_id,chunk_id)
    if current['expected']!=expected:raise RuntimeError('Les embeddings ont changé depuis la prévisualisation. Vérifier à nouveau avant suppression.')
    counts=current['counts']
    if not sum(counts[k] for k in ('published_vectors','checkpoint_rows','legacy_vectors')):
        raise ValueError('Aucun vecteur ni trace de reprise à supprimer dans cette portée.')
    queries=sql_client.embedding_deletion_queries(profile,current['encoder_fingerprint'],scope,document_id,chunk_id)
    # Delete the exact selected sets, including saved vectors so they cannot silently reappear.
    conn.execute(sql_client.delete_profile_vectors(queries),[profile,*queries[0][2]])
    conn.execute(sql_client.delete_legacy_vectors(queries),[profile,*queries[2][2]])
    # The checkpoint query includes all historical input versions in the selected scope.
    params=[profile,current['encoder_fingerprint']]
    if scope=='document':params.append(document_id)
    elif scope=='chunk':params.append(chunk_id)
    conn.execute(sql_client.delete_checkpoints(scope),params)
    event=uuid.uuid4().hex
    conn.execute(sql_client.INSERT_DELETION_AUDIT,
                 (event,profile,scope,current['target'],current['encoder_fingerprint'],Jsonb(counts)))
    return {**current,'event_id':event,'deleted':True,'sources_preserved':True}