"""Read-only KM metadata API and atomic writer helpers; never interprets business codes."""
import hashlib
import json
import bisect

from psycopg.types.json import Jsonb
from . import sql_client
from .inventory import identity
from .store import checked_handle,handle

INFO_FIELDS=('identiteNodeId','currentVersionNodeId','currentVersionLabel','nature','identifiant',
    'designation','typeDocumentId','commentaireIdentite','codePerimetre','codeSousPerimetre',
    'orgApprobateur','orgDiffusion','profilId','hasCdeEnCours','idPlanDiff','programmeDocumentaireNodeId')
VERSION_FIELDS=('nodeId','versionLabel','editionCodeId','editionDate','etatId','rangMaj','niveauMaj',
    'confidentialiteId','specialFranceId','entreeVigueurDate','emploiPur','imperatifNavId',
    'impactPea','impactDtu','impactIme','impactIdops','identificationRef','identificationDate',
    'approbationRef','approbationDate','abrogationRef','abrogationDate','editionGroup')

COLUMNS = sql_client.metadata_columns(INFO_FIELDS, VERSION_FIELDS)

def write_metadata(conn,bundle):
    item=bundle.get('km_metadata')
    if not item:
        return
    conn.execute(sql_client.INSERT_KM_NODE,(item['parent_node_id'],))
    existing=conn.execute(sql_client.KM_VERSION_IDENTITY,
                          (item['version_node_id'],)).fetchone()
    if existing and (existing['parent_node_id']!=item['parent_node_id'] or existing['version_folder']!=item['version_folder']):
        raise ValueError('Identité de version KM déjà attribuée à un autre dossier')
    updated = conn.execute(sql_client.UPSERT_KM_VERSION,
        (item['version_node_id'],item['parent_node_id'],item['version_label'],item['version_folder'],
         Jsonb(item['info']),Jsonb(item['version']),Jsonb(item['linked_documents']),Jsonb(item['warnings']))).fetchone()
    conn.execute(sql_client.UPSERT_KM_FILE,
        (item['file_id'],item['version_node_id'],bundle['document_id'],item['relative_path'],
         item['filename'],item['original_path'],Jsonb(item['csv_row'])))
    if updated:
        conn.execute(sql_client.DELETE_LINKED_DOCUMENTS,(item['version_node_id'],))
        with conn.cursor() as cursor:
            cursor.executemany(sql_client.INSERT_LINKED_DOCUMENT,
                ((item['version_node_id'],index,linked.get('uuid'),Jsonb(linked))
                 for index,linked in enumerate(item['linked_documents'] or [])))


def markdown_units(document,markdown):
    if not isinstance(markdown,str):
        return []
    starts=[0]
    starts.extend(index+1 for index,char in enumerate(markdown) if char=='\n')
    result=[]
    for pos in range(0,len(markdown),4000):
        text=markdown[pos:pos+4000]
        if not text.strip():
            continue
        ident=identity('markdown',document+':'+str(pos)+':'+hashlib.sha256(text.encode()).hexdigest())
        result.append((ident,text,{'id':ident,'document_id':document,'text':text,'context':'',
            'format':'km.markdown','pages':[],'start_line':bisect.bisect_right(starts,pos),
            'end_line':bisect.bisect_right(starts,pos+len(text)-1),
            'start_char':pos,'end_char':pos+len(text)}))
    return result

def write_search_units(conn,bundle):
    doc=bundle['document_id']
    conn.execute(sql_client.DELETE_SEARCH_UNITS,(doc,))
    with conn.cursor() as cursor:
        cursor.executemany(sql_client.INSERT_SEARCH_UNIT,
            ((record['id'],doc,'chunk',record['embedding_text'],Jsonb(record)) for record in bundle['chunks']))
    markdown=bundle.get('markdown')
    if markdown is not None:
        conn.execute(sql_client.UPSERT_DOCUMENT_TEXT,
            (doc,markdown,hashlib.sha256(markdown.encode()).hexdigest()))
    else:
        conn.execute(sql_client.DELETE_DOCUMENT_TEXT,(doc,))
    with conn.cursor() as cursor:
        cursor.executemany(sql_client.INSERT_SEARCH_UNIT,
            ((ident,doc,'markdown',text,Jsonb(record)) for ident,text,record in markdown_units(doc,markdown)))


def filter_conditions(filters):
    if not isinstance(filters, list) or not 1 <= len(filters) <= 16:
        raise ValueError('Filtres de métadonnées invalides')
    conditions=[]
    params=[]
    for item in filters:
        column = item.get('column') if isinstance(item, dict) else None
        if not isinstance(column, str) or column not in COLUMNS:
            raise ValueError('Colonne métier inconnue')
        op=item.get('op');value=item.get('value')
        expression=COLUMNS[column][0]
        # HTTP document handles must be resolved only through their fixed hash.
        if column=='document_id' and op=='=':
            _,digest=checked_handle(value,'d')
            conditions.append(sql_client.METADATA_DOCUMENT_DIGEST);params.append(digest);continue
        if op in ('is_null','is_not_null'):
            condition=sql_client.metadata_is_null(expression)
            conditions.append(('NOT ' if op=='is_not_null' else '')+condition)
        elif op=='contains':
            if not isinstance(value,str) or len(value)>1000:
                raise ValueError('contains attend une chaîne bornée')
            conditions.append(sql_client.metadata_contains(expression));params.append(value)
        elif op=='in':
            if not isinstance(value,list) or not 1<=len(value)<=100:
                raise ValueError('in attend une liste bornée')
            conditions.append(sql_client.metadata_in(expression));params.append(Jsonb(value))
        elif op in ('=','!=','<','<=','>','>='):
            if isinstance(value,(dict,list)) or (isinstance(value,str) and len(value)>1000):
                raise ValueError('Valeur de filtre invalide')
            conditions.append(sql_client.metadata_comparison(expression, op))
            params.extend([Jsonb(value),Jsonb(value)])
        else:
            raise ValueError('Opérateur métier invalide')
    return sql_client.conjunction(conditions), params

class Metadata:
    def __init__(self,store):
        self.store=store

    def columns(self):
        return [{'name':name,'type':kind} for name,(_,kind) in COLUMNS.items()]

    def read(self,ident):
        _,digest=checked_handle(ident,'d')
        with self.store.connection() as conn:
            rows=conn.execute(sql_client.READ_METADATA,(digest,)).fetchall()
        if len(rows)>1:
            raise ValueError('Identifiant documentaire ambigu')
        if not rows:
            return None
        row=rows[0]
        row['document_id']=handle('d',row['document_id'])
        row['display_name']=row['original_name']
        row['source_type']='km_metadata'
        row['semantics']='Champs sources non interprétés ; aucun statut courant ou lien documentaire déduit.'
        return row

    def distinct(self,column,limit):
        if not isinstance(column, str) or column not in COLUMNS or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Colonne ou limite de métadonnées invalide')
        expression=COLUMNS[column][0]
        with self.store.connection() as conn:
            rows=conn.execute(sql_client.distinct_metadata(expression),(limit+1,)).fetchall()
        return {'column':column,'values':[r['value'] for r in rows[:limit]],'truncated':len(rows)>limit}

    def select(self,filters,limit):
        if not isinstance(filters,list) or not 1<=len(filters)<=16 or type(limit) is not int or not 1<=limit<=200:
            raise ValueError('Filtres ou limite de métadonnées invalides')
        condition, params = filter_conditions(filters)
        with self.store.connection() as conn:
            total=conn.execute(sql_client.count_metadata(condition),params).fetchone()['n']
            rows=conn.execute(sql_client.select_metadata(condition),params+[limit]).fetchall()
        for row in rows:
            row['document_id']=handle('d',row['document_id'])
        return {'rows':rows,'total':total,'truncated':total>len(rows)}
