"""Text profile identities and profile-local publication snapshots."""
import hashlib
from psycopg.types.json import Jsonb
from . import sql_client

def active(conn):
    row=conn.execute(sql_client.ACTIVE_PROFILE).fetchone()
    return row['active_text_profile'] if row else None

def lock(conn):
    if not conn.execute(sql_client.LOCK_EMBEDDING_WRITER).fetchone()['ok']:
        raise RuntimeError('Une indexation est en cours ; réessayer après sa fin.')

def register(conn,metadata,fingerprint,requested=None):
    if metadata.get('dimension')!=1024 or not isinstance(fingerprint,str) or not fingerprint:
        raise ValueError('Profil texte 1024 et identité d’encodeur requis.')
    if requested:
        row=conn.execute(sql_client.TEXT_PROFILE_BY_NAME,(requested,)).fetchone()
        if not row or row['encoder_fingerprint']!=fingerprint or row['dimension']!=1024 or row['configuration'].get('legacy_unverified'):
            raise RuntimeError('Le profil choisi ne correspond pas à l’encodeur chargé. Choisir ou enregistrer son profil actuel.')
    else:
        row=conn.execute(sql_client.COMPATIBLE_TEXT_PROFILE,(fingerprint,)).fetchone()
        if row is None:
            name='text-'+hashlib.sha256(fingerprint.encode()).hexdigest()
            conn.execute(sql_client.INSERT_TEXT_PROFILE,(name,fingerprint,Jsonb(metadata)))
            row=conn.execute(sql_client.PROFILE_BY_NAME,(name,)).fetchone()
            if row['encoder_fingerprint']!=fingerprint or row['dimension']!=1024:
                raise RuntimeError('Identité de profil incohérente.')
    conn.execute(sql_client.INITIALIZE_ACTIVE_PROFILE,(row['profile'],))
    return row

def snapshot(conn,profile,bundle,vectors,hashes,*,original_name,type_document_id):
    doc=bundle['document_id']
    conn.execute(sql_client.UPSERT_PROFILE_DOCUMENT,
        (profile,doc,bundle['filepath'],Jsonb(bundle.get('metadata',{})),original_name,type_document_id))
    units=[('chunk',r['id'],r) for r in bundle['chunks']]+[('visual',r['id'],r) for r in bundle['visuals']]
    unit_ids = [ident for _,ident,_ in units]
    if conn.execute(sql_client.FOREIGN_PROFILE_UNIT,
        (profile,unit_ids,doc)).fetchone():
        raise ValueError('Unité du profil associée à un autre document.')
    with conn.cursor() as cursor:
        cursor.executemany(sql_client.UPSERT_PROFILE_UNIT,
            ((profile,ident,doc,kind,Jsonb(record)) for kind,ident,record in units))
        cursor.executemany(sql_client.UPSERT_PROFILE_EMBEDDING,
            ((profile,record['id'],hashes[record['id']],vectors[record['id']]) for record in bundle['chunks']))
    conn.execute(sql_client.DELETE_STALE_PROFILE_UNITS,
                 (profile,doc,unit_ids))