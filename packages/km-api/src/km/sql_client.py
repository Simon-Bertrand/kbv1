"""SQL statements and the read-only connection lifecycle for KM.

Write statements are executed by their callers inside the existing writer
transactions. Dynamic SQL fragments are built from internal selections and
validated metadata fields. Request values are bound separately.
"""

from contextlib import contextmanager

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row


class KmDbClient:
    """Manage read-only connections without coupling them to HTTP or ingestion."""
    def __init__(self, dsn, *, timeout_ms=10000):
        if not dsn or not 1 <= timeout_ms <= 120000:
            raise ValueError('Database configuration required')
        self.dsn, self.timeout_ms = dsn, timeout_ms
        self._pool = None

    @contextmanager
    def pooled(self):
        from psycopg_pool import ConnectionPool

        with ConnectionPool(self.dsn, kwargs={'connect_timeout': 5, 'row_factory': dict_row},
                            min_size=0, max_size=4, timeout=5, max_waiting=16,
                            open=False, check=ConnectionPool.check_connection) as pool:
            self._pool = pool
            try:
                yield self
            finally:
                self._pool = None

    @contextmanager
    def connection(self):
        if self._pool is None:
            checkout = psycopg.connect(self.dsn, connect_timeout=5, row_factory=dict_row)
        else:
            checkout = self._pool.connection()
        with checkout as conn:
            conn.read_only = True
            conn.isolation_level = IsolationLevel.REPEATABLE_READ
            with conn.transaction():
                conn.execute(SET_STATEMENT_TIMEOUT, (str(self.timeout_ms),))
                yield conn


# Retrieval

def document_identities(profiled):
    table = 'rag.km_profile_documents' if profiled else 'rag.documents'
    clause = ' AND d.profile = %s' if profiled else ''
    return (f"""SELECT d.document_id, d.original_name, d.type_document_id,
                   t.label AS type_document_label
            FROM {table} d
            LEFT JOIN rag.km_document_types t USING (type_document_id)
            WHERE d.document_id = ANY(%s){clause}""")

SET_STATEMENT_TIMEOUT = "SELECT set_config('statement_timeout', %s, true)"

CHECK_CHUNKS = 'SELECT 1 FROM rag.chunks LIMIT 1'

CHECK_KM_FILES = 'SELECT 1 FROM rag.km_files LIMIT 1'

CHECK_PROFILE_UNITS = 'SELECT 1 FROM rag.km_profile_units LIMIT 1'

CHECK_EMBEDDING_SETTINGS = 'SELECT 1 FROM rag.km_embedding_settings LIMIT 1'

READY_EXTENSIONS = "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_textsearch')"

PROFILE_BY_MODALITY = 'SELECT * FROM rag.embedding_profiles WHERE profile=%s AND modality=%s'

COUNT_DOCUMENTS = 'SELECT count(*) AS n FROM rag.documents'

LIST_DOCUMENTS = 'SELECT * FROM rag.documents ORDER BY document_id LIMIT %s OFFSET %s'

DOCUMENT_BY_DIGEST = 'SELECT * FROM rag.documents WHERE md5(document_id)=%s LIMIT 2'

DOCUMENT_ID_BY_DIGEST = 'SELECT document_id FROM rag.documents WHERE md5(document_id)=%s LIMIT 2'

def count_document_units(union):
    return ('SELECT count(*) AS n FROM ('+union+') u')

def list_document_units(union):
    return ("SELECT u.*,d.original_name,d.type_document_id,t.label AS type_document_label FROM ("+union+""") u
                JOIN rag.documents d USING(document_id)
                LEFT JOIN rag.km_document_types t USING(type_document_id)
                ORDER BY coalesce(u.record->'pages'->>0,''),u.kind,u.id LIMIT %s OFFSET %s""")

KM_UNIT_BY_DIGEST = ("""SELECT c.unit_id AS id,c.document_id,c.record,d.original_name,d.type_document_id,t.label AS type_document_label,c.kind
                    FROM rag.km_search_units c JOIN rag.documents d USING(document_id)
                    LEFT JOIN rag.km_document_types t USING(type_document_id)
                    WHERE md5(c.unit_id)=%s LIMIT 2""")

def legacy_unit_by_digest(key, table):
    return (f"""SELECT c.{key} AS id,c.document_id,c.record,d.original_name,d.type_document_id,t.label AS type_document_label,
                    %s::text AS kind FROM rag.{table} c JOIN rag.documents d USING(document_id)
                    LEFT JOIN rag.km_document_types t USING(type_document_id)
                    WHERE md5(c.{key})=%s LIMIT 2""")

COUNT_PROFILE_DOCUMENTS = 'SELECT count(*) AS n FROM rag.km_profile_documents WHERE profile=%s'

LIST_PROFILE_DOCUMENTS = 'SELECT * FROM rag.km_profile_documents WHERE profile=%s ORDER BY document_id LIMIT %s OFFSET %s'

PROFILE_DOCUMENT_BY_DIGEST = 'SELECT * FROM rag.km_profile_documents WHERE profile=%s AND md5(document_id)=%s LIMIT 2'

RECENT_PROFILE_DOCUMENT_BY_DIGEST = ('SELECT DISTINCT ON (document_id) * FROM rag.km_profile_documents '
                    'WHERE md5(document_id)=%s ORDER BY document_id, published_at DESC, profile LIMIT 2')

PROFILE_DOCUMENT_ID_BY_DIGEST = 'SELECT document_id FROM rag.km_profile_documents WHERE profile=%s AND md5(document_id)=%s LIMIT 2'

COUNT_PROFILE_UNITS = 'SELECT count(*) AS n FROM rag.km_profile_units WHERE profile=%s AND document_id=%s'

LIST_PROFILE_UNITS = ("""SELECT u.unit_id AS id,u.document_id,u.kind,u.record,d.original_name,d.type_document_id,t.label AS type_document_label,
                       d.metadata->>'pdfparse_source_sha256' AS source_sha256
                FROM rag.km_profile_units u JOIN rag.km_profile_documents d USING(profile,document_id)
                LEFT JOIN rag.km_document_types t USING(type_document_id)
                WHERE u.profile=%s AND u.document_id=%s ORDER BY u.kind,u.unit_id LIMIT %s OFFSET %s""")

PROFILE_UNIT_BY_DIGEST = ("""SELECT u.unit_id AS id,u.document_id,u.kind,u.record,d.original_name,d.type_document_id,t.label AS type_document_label,
                       d.metadata->>'pdfparse_source_sha256' AS source_sha256
                FROM rag.km_profile_units u JOIN rag.km_profile_documents d USING(profile,document_id)
                LEFT JOIN rag.km_document_types t USING(type_document_id)
                WHERE u.profile=%s AND md5(u.unit_id)=%s AND u.kind=ANY(%s) LIMIT 2""")

def physical_document(km):
    table = 'km_profile_documents' if km else 'documents'
    condition = 'profile=%s AND ' if km else ''
    return ("SELECT document_id,filepath,metadata->>'pdfparse_source_sha256' AS source_sha256 FROM rag."+table+
                ' WHERE '+condition+'md5(document_id)=%s LIMIT 2')

def physical_page_units(source):
    return ("SELECT * FROM ("+source+"""
                ) u WHERE (CASE WHEN jsonb_array_length(coalesce(record->'body_pages','[]')) > 0
                     THEN record->'body_pages' ELSE coalesce(record->'pages','[]') END @> %s::jsonb)
                OR (jsonb_array_length(coalesce(record->'body_pages','[]'))=0
                    AND jsonb_array_length(coalesce(record->'pages','[]'))=0
                    AND jsonb_path_exists(record,'$.locations[*] ? (@.page_no == $page || @.page == $page)',%s::jsonb))
                ORDER BY kind,id LIMIT %s""")

VECTOR_SHAPE = 'SELECT rag.vector_dims(%s::rag.vector) AS dimensions,rag.vector_norm(%s::rag.vector) AS norm'

RESOLVE_DOCUMENT_TYPES = 'SELECT rag.document_type_scope(%s,%s,%s) AS ids'

RESOLVE_DOCUMENT_DATES = 'SELECT rag.date_document_scope(%s) AS ids'

HNSW_STRICT_ORDER = "SELECT set_config('hnsw.iterative_scan','strict_order',true)"

HNSW_SEARCH_BUDGET = "SELECT set_config('hnsw.ef_search','100',true)"

def profile_visual_search(conditions):
    return ("""
            WITH nearest AS MATERIALIZED (
                SELECT v.unit_id AS visual_id,v.document_id,v.record,
                       e.embedding OPERATOR(rag.<=>) %s::rag.vector AS distance
                FROM rag.visual_embeddings e
                JOIN rag.km_profile_units v ON v.unit_id=e.visual_id AND v.kind='visual'
                JOIN rag.km_profile_documents pd ON pd.profile=v.profile AND pd.document_id=v.document_id
                WHERE """ + ' AND '.join(conditions) + """
                ORDER BY distance LIMIT 100
            ), ranked AS (
                SELECT *,row_number() OVER (ORDER BY distance,visual_id) AS visual_rank FROM nearest
            ), winners AS (
                SELECT coalesce(link.unit_id,r.visual_id) AS unit_id,
                       min(r.visual_rank) AS visual_rank,
                       array_agg(DISTINCT r.visual_rank) AS matches
                FROM ranked r
                LEFT JOIN LATERAL (
                    SELECT c.unit_id FROM rag.km_profile_units c
                    WHERE c.profile=%s AND c.document_id=r.document_id AND c.kind='chunk'
                      AND (r.record->'chunk_ids') ? c.unit_id
                ) link ON true
                GROUP BY coalesce(link.unit_id,r.visual_id)
                ORDER BY min(r.visual_rank),coalesce(link.unit_id,r.visual_id) LIMIT %s
            )
            SELECT u.kind,u.unit_id AS id,u.document_id,d.filepath,d.metadata,u.record,
                   1.0::double precision/(60+w.visual_rank) AS score,
                   ARRAY(SELECT r.visual_id FROM ranked r WHERE r.visual_rank=ANY(w.matches)
                         ORDER BY r.visual_rank) AS matched_visual_ids
            FROM winners w JOIN rag.km_profile_units u ON u.profile=%s AND u.unit_id=w.unit_id
            JOIN rag.km_profile_documents d USING (profile,document_id)
            ORDER BY w.visual_rank,u.kind,u.unit_id
        """)

def legacy_visual_search(conditions):
    scope = (
        ', p_document_ids => ARRAY(SELECT pd.document_id FROM rag.documents pd WHERE '
        + ' AND '.join(conditions) + ')'
    ) if conditions else ''
    return ("""WITH hits AS MATERIALIZED (
            SELECT * FROM rag.rag_search(
                p_visual_query => %s::rag.vector, p_top_k => %s, p_metadata => %s""" + scope + """)
            )
            SELECT h.*, coalesce(c.record, v.record) AS record FROM hits h
            LEFT JOIN rag.chunks c ON h.kind='chunk' AND c.chunk_id=h.id
            LEFT JOIN rag.visuals v ON h.kind='visual' AND v.visual_id=h.id
            ORDER BY h.score DESC, h.kind, h.id""")

DOCUMENT_TYPES = "SELECT * FROM rag.km_document_types ORDER BY type_document_id"

CHUNK_DOCUMENT_METADATA = "SELECT * FROM rag.chunk_document_metadata(%s, %s)"

PROFILE_FINGERPRINT = 'SELECT encoder_fingerprint FROM rag.embedding_profiles WHERE profile=%s'

PROFILE_SEARCH = ('''SELECT * FROM rag.rag_search_profile(
                    p_profile=>%s,p_query=>%s,p_text_query=>%s::rag.vector,p_mode=>%s,
                    p_top_k=>%s,p_candidates=>%s,p_metadata=>%s,
                    p_type_document_ids=>%s,p_perimeter_code=>%s,p_type_document_labels=>%s,p_date_filters=>%s)''')

LEGACY_TEXT_SEARCH = ("""SELECT * FROM rag.rag_search_hybrid(
                    p_query => %s, p_text_query => %s::rag.vector, p_mode => %s,
                    p_top_k => %s, p_candidates => %s, p_metadata => %s,
                    p_type_document_ids=>%s,p_perimeter_code=>%s,p_type_document_labels=>%s,p_date_filters=>%s)""")


def filtered_search(conditions):
    return """
            WITH allowed AS MATERIALIZED (
                SELECT pd.profile,pd.document_id,pd.filepath,pd.metadata
                FROM rag.km_profile_documents pd WHERE """ + ' AND '.join(conditions) + """
            ), lexical AS MATERIALIZED (
                SELECT u.unit_id,coalesce(u.record->>'text','') OPERATOR(rag.<@>) rag.to_bm25query(%s,'rag.km_profile_units_bm25_idx') AS distance
                FROM rag.km_profile_units u JOIN allowed d USING(profile,document_id)
                WHERE u.kind = 'chunk' AND %s<>'semantic'
                  AND rag.has_physical_page(u.record)
                ORDER BY distance LIMIT %s
            ), semantic AS MATERIALIZED (
                SELECT e.chunk_id,e.embedding OPERATOR(rag.<=>) %s::rag.vector AS distance
                FROM rag.km_text_embeddings e
                JOIN rag.km_profile_units u ON u.profile=e.profile AND u.unit_id=e.chunk_id
                JOIN allowed d ON d.profile=u.profile AND d.document_id=u.document_id
                WHERE %s<>'lexical' AND u.kind='chunk'
                ORDER BY e.embedding OPERATOR(rag.<=>) %s::rag.vector,e.chunk_id LIMIT %s
            ), ranks AS (
                SELECT l.unit_id,row_number() OVER(ORDER BY l.distance,l.unit_id) AS lr,
                       NULL::bigint AS sr FROM lexical l WHERE l.distance < 0
                UNION ALL
                SELECT s.chunk_id,NULL::bigint,row_number() OVER(ORDER BY s.distance,s.chunk_id) FROM semantic s
            ), fused AS (
                SELECT r.unit_id,min(r.lr) AS lr,min(r.sr) AS sr,
                    coalesce(1.0::double precision/(60+min(r.lr)),0)+
                    coalesce(1.0::double precision/(60+min(r.sr)),0) AS score
                FROM ranks r GROUP BY r.unit_id
            ), winners AS (SELECT * FROM fused ORDER BY score DESC,unit_id LIMIT %s)
            SELECT u.kind,u.unit_id AS id,w.score,w.lr AS lexical_rank,w.sr AS semantic_rank,
                   u.document_id,d.filepath,d.metadata,u.record
            FROM winners w JOIN rag.km_profile_units u ON u.profile=%s AND u.unit_id=w.unit_id
            JOIN allowed d ON d.profile=u.profile AND d.document_id=u.document_id
            ORDER BY w.score DESC,u.unit_id
        """


DOCUMENT_UNITS = """
    SELECT 'chunk' AS kind,chunk_id AS id,document_id,record FROM rag.chunks WHERE document_id=%s
    UNION ALL SELECT 'visual',visual_id,document_id,record FROM rag.visuals WHERE document_id=%s
"""


def metadata_scope(predicate):
    return 'EXISTS (SELECT 1' + METADATA_JOIN + 'WHERE f.document_id=pd.document_id AND ' + predicate + ')'

PROFILE_PAGE_SOURCE = "SELECT unit_id AS id,document_id,kind,record FROM rag.km_profile_units WHERE profile=%s AND document_id=%s AND kind IN ('chunk','visual')"

LEGACY_PAGE_SOURCE = "SELECT chunk_id AS id,document_id,'chunk' AS kind,record FROM rag.chunks WHERE document_id=%s UNION ALL SELECT visual_id,document_id,'visual',record FROM rag.visuals WHERE document_id=%s"

DOCUMENT_DIGEST_SCOPE = 'md5(pd.document_id)=ANY(%s)'

PROFILE_SCOPE = 'pd.profile=%s'

DOCUMENT_TYPE_SCOPE = 'pd.type_document_id = ANY(%s)'

DOCUMENT_DATE_SCOPE = 'pd.document_id=ANY(%s)'

DOCUMENT_METADATA_SCOPE = 'pd.metadata @> %s'


# Metadata

METADATA_JOIN=' FROM rag.km_files f JOIN rag.km_versions v USING(version_node_id) JOIN rag.documents d USING(document_id) '

INSERT_KM_NODE = 'INSERT INTO rag.km_nodes VALUES (%s) ON CONFLICT DO NOTHING'

KM_VERSION_IDENTITY = 'SELECT parent_node_id,version_folder FROM rag.km_versions WHERE version_node_id=%s'

UPSERT_KM_VERSION = ("""INSERT INTO rag.km_versions(version_node_id,parent_node_id,version_label,version_folder,
        info,version_record,linked_documents,warnings) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(version_node_id) DO UPDATE SET version_label=EXCLUDED.version_label,
        info=EXCLUDED.info,version_record=EXCLUDED.version_record,
        linked_documents=EXCLUDED.linked_documents,warnings=EXCLUDED.warnings,updated_at=now()
        WHERE (km_versions.version_label,km_versions.info,km_versions.version_record,
               km_versions.linked_documents,km_versions.warnings)
            IS DISTINCT FROM (EXCLUDED.version_label,EXCLUDED.info,EXCLUDED.version_record,
                              EXCLUDED.linked_documents,EXCLUDED.warnings)
        RETURNING version_node_id""")

UPSERT_KM_FILE = ("""INSERT INTO rag.km_files(file_id,version_node_id,document_id,relative_path,filename,original_path,csv_row)
        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(file_id) DO UPDATE SET
        document_id=EXCLUDED.document_id,original_path=EXCLUDED.original_path,csv_row=EXCLUDED.csv_row""")

DELETE_LINKED_DOCUMENTS = 'DELETE FROM rag.km_linked_documents WHERE version_node_id=%s'

INSERT_LINKED_DOCUMENT = 'INSERT INTO rag.km_linked_documents VALUES (%s,%s,%s,%s)'

DELETE_SEARCH_UNITS = 'DELETE FROM rag.km_search_units WHERE document_id=%s'

INSERT_SEARCH_UNIT = 'INSERT INTO rag.km_search_units VALUES (%s,%s,%s,%s,%s)'

READ_METADATA = ("""SELECT f.*,v.parent_node_id,v.version_label,v.version_folder,
                v.info,v.version_record,v.linked_documents,v.warnings,d.original_name,d.type_document_id,
                t.label AS type_document_label,d.metadata->'km'->'source_paths' AS source_paths """+METADATA_JOIN+
                'LEFT JOIN rag.km_document_types t ON t.type_document_id=d.type_document_id WHERE md5(f.document_id)=%s LIMIT 2')

def distinct_metadata(expression):
    return ('SELECT DISTINCT '+expression+' AS value'+METADATA_JOIN+
                              'ORDER BY value LIMIT %s')

def count_metadata(condition):
    where = ' WHERE ' + condition
    return ('SELECT count(*) AS n'+METADATA_JOIN+where)

def select_metadata(condition):
    where = ' WHERE ' + condition
    return ('SELECT f.document_id,f.filename,f.relative_path,f.original_path,v.parent_node_id,'
                "v.version_node_id,v.version_label,v.info->>'identifiant' AS identifiant,v.info->>'designation' AS designation,"
                "d.original_name,d.type_document_id,t.label AS type_document_label"+
                METADATA_JOIN+' LEFT JOIN rag.km_document_types t ON t.type_document_id=d.type_document_id '+where+' ORDER BY f.relative_path LIMIT %s')

BASE_COLUMNS={name:("to_jsonb("+expression+")","string") for name,expression in {
    'filename':'f.filename','document_id':"'d'||md5(f.document_id)",'parent_node_id':'v.parent_node_id',
    'version_node_id':'v.version_node_id','version_label':'v.version_label',
    'original_path':'f.original_path','relative_path':'f.relative_path'}.items()}


def metadata_columns(info_fields, version_fields):
    return {**BASE_COLUMNS,
    **{'info.'+name:("v.info->'"+name+"'","json") for name in info_fields},
    **{'version.'+name:("v.version_record->'"+name+"'","json") for name in version_fields}}

METADATA_DOCUMENT_DIGEST = 'md5(f.document_id)=%s'


def metadata_is_null(expression):
    return '('+expression+" IS NULL OR "+expression+" = 'null'::jsonb)"


def metadata_contains(expression):
    return "strpos(lower("+expression+" #>> '{}'),lower(%s))>0"


def metadata_in(expression):
    return expression+' IN (SELECT value FROM jsonb_array_elements(%s::jsonb))'


def metadata_comparison(expression, op):
    return '(jsonb_typeof('+expression+')=jsonb_typeof(%s::jsonb) AND '+expression+' '+op+' %s::jsonb)'


def conjunction(conditions):
    return ' AND '.join(conditions)



# Profiles

ACTIVE_PROFILE = 'SELECT active_text_profile FROM rag.km_embedding_settings WHERE singleton'

LOCK_EMBEDDING_WRITER = "SELECT pg_try_advisory_xact_lock(hashtextextended('km-embedding-writer',0)) AS ok"

TEXT_PROFILE_BY_NAME = "SELECT * FROM rag.embedding_profiles WHERE profile=%s AND modality='text'"

COMPATIBLE_TEXT_PROFILE = ("""SELECT * FROM rag.embedding_profiles WHERE modality='text'
            AND encoder_fingerprint=%s AND dimension=1024 AND NOT configuration @> '{"legacy_unverified":true}'
            ORDER BY CASE WHEN profile='text-v1' THEN 0 ELSE 1 END,profile LIMIT 1""")

INSERT_TEXT_PROFILE = ("""INSERT INTO rag.embedding_profiles(profile,modality,encoder_fingerprint,dimension,configuration)
                VALUES (%s,'text',%s,1024,%s) ON CONFLICT DO NOTHING""")

PROFILE_BY_NAME = 'SELECT * FROM rag.embedding_profiles WHERE profile=%s'

INITIALIZE_ACTIVE_PROFILE = ("""INSERT INTO rag.km_embedding_settings(singleton,active_text_profile) VALUES(true,%s)
        ON CONFLICT(singleton) DO UPDATE SET active_text_profile=coalesce(rag.km_embedding_settings.active_text_profile,EXCLUDED.active_text_profile)""")

UPSERT_PROFILE_DOCUMENT = ("""INSERT INTO rag.km_profile_documents(profile,document_id,filepath,metadata,original_name,type_document_id)
        VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(profile,document_id)
        DO UPDATE SET filepath=EXCLUDED.filepath,metadata=EXCLUDED.metadata,original_name=EXCLUDED.original_name,
                      type_document_id=EXCLUDED.type_document_id,published_at=now()""")


FOREIGN_PROFILE_UNIT = ("""SELECT 1 FROM rag.km_profile_units
        WHERE profile=%s AND unit_id=ANY(%s) AND document_id<>%s LIMIT 1""")

UPSERT_PROFILE_UNIT = ("""INSERT INTO rag.km_profile_units(profile,unit_id,document_id,kind,record)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(profile,unit_id)
            DO UPDATE SET kind=EXCLUDED.kind,record=EXCLUDED.record""")

UPSERT_PROFILE_EMBEDDING = ("""INSERT INTO rag.km_text_embeddings(profile,chunk_id,input_hash,embedding)
            VALUES(%s,%s,%s,%s::rag.vector) ON CONFLICT(profile,chunk_id)
            DO UPDATE SET input_hash=EXCLUDED.input_hash,embedding=EXCLUDED.embedding,indexed_at=now()""")

DELETE_STALE_PROFILE_UNITS = 'DELETE FROM rag.km_profile_units WHERE profile=%s AND document_id=%s AND NOT(unit_id=ANY(%s))'

# Catalog

CATALOG_READY = ("SELECT to_regclass('rag.km_corpus_files') IS NOT NULL AND "
                                "to_regclass('rag.km_corpus_caches') IS NOT NULL AND "
                                "to_regclass('rag.km_corpus_syncs') IS NOT NULL AND "
                                "to_regclass('rag.km_cache_chunks') IS NOT NULL AND "
                                "to_regclass('rag.km_profile_cache_choices') IS NOT NULL AND "
                                "to_regclass('rag.km_chunk_job_files') IS NOT NULL AS ok")

CORPUS_LOCK = 'SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok'

INTERRUPT_RECOVERED_SYNC = ("UPDATE rag.km_corpus_syncs SET status='interrupted',finished_at=now(),"
                             "error='Service redémarré ; relancer explicitement la synchronisation.' "
                             "WHERE corpus_root=%s AND status='running'")

LATEST_CORPUS_SYNC = ('SELECT * FROM rag.km_corpus_syncs WHERE corpus_root=%s '
                               'ORDER BY started_at DESC LIMIT 1')

CORPUS_SYNC_BY_ID = 'SELECT * FROM rag.km_corpus_syncs WHERE corpus_root=%s AND id=%s'

INTERRUPT_CORPUS_SYNC = ("UPDATE rag.km_corpus_syncs SET status='interrupted',finished_at=now() "
                         "WHERE corpus_root=%s AND status='running'")

INSERT_CORPUS_SYNC = ("INSERT INTO rag.km_corpus_syncs(id,corpus_root,status) "
                               "VALUES(%s,%s,'running') RETURNING *")

UPSERT_CORPUS_FILE = ('INSERT INTO rag.km_corpus_files(corpus_root,id,path,summary,metadata,last_seen_sync) '
                             'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(corpus_root,id) DO UPDATE SET '
                             'path=excluded.path,summary=excluded.summary,metadata=excluded.metadata,'
                             'present=true,last_seen_sync=excluded.last_seen_sync,observed_at=now()')

UPSERT_CORPUS_CACHE = ('INSERT INTO rag.km_corpus_caches(corpus_root,document_id,cache_key,path,state,manifest,error,last_seen_sync) '
                                 'VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(corpus_root,document_id,cache_key) DO UPDATE SET '
                                 'path=excluded.path,state=excluded.state,manifest=excluded.manifest,error=excluded.error,'
                                 'present=true,last_seen_sync=excluded.last_seen_sync,observed_at=now()')

COUNT_PRESENT_CORPUS_FILES = 'SELECT count(*) AS n FROM rag.km_corpus_files WHERE corpus_root=%s AND present'

def mark_corpus_absent(table):
    return ('UPDATE rag.'+table+' SET present=false WHERE corpus_root=%s AND last_seen_sync<>%s')

FINISH_CORPUS_SYNC = "UPDATE rag.km_corpus_syncs SET status='succeeded',finished_at=now() WHERE id=%s"

FAIL_CORPUS_SYNC = "UPDATE rag.km_corpus_syncs SET status='failed',finished_at=now(),error=%s WHERE id=%s"

SYNC_PROGRESS = 'UPDATE rag.km_corpus_syncs SET directories=%s,pdf_count=%s,cache_count=%s,chunks_count=%s,valid_caches=%s,invalid_caches=%s WHERE id=%s'

CORPUS_MATCH_SCOPE = 'corpus_root=%s AND present AND starts_with(path,%s) AND strpos(lower(path),lower(%s))>0'

COUNT_CORPUS_MATCHES = ('SELECT count(*) AS n FROM rag.km_corpus_files WHERE '+CORPUS_MATCH_SCOPE)

LIST_CORPUS_MATCHES = 'SELECT summary FROM rag.km_corpus_files WHERE '+CORPUS_MATCH_SCOPE+' ORDER BY lower(path),path LIMIT %s OFFSET %s'

COUNT_ABSENT_CORPUS_FILES = 'SELECT count(*) AS n FROM rag.km_corpus_files WHERE corpus_root=%s AND NOT present'

CORPUS_FILES_BY_IDS = 'SELECT summary FROM rag.km_corpus_files WHERE corpus_root=%s AND present AND id=ANY(%s)'

# Chunk catalog

CHUNK_CATALOG_BASE = """
WITH files AS (
 SELECT f.corpus_root,f.id,f.path,f.summary->>'version_label' AS version_label,choice.cache_key AS chosen_key, f.summary->>'metadata_digest' AS metadata_digest FROM rag.km_corpus_files f
 LEFT JOIN rag.km_profile_cache_choices choice ON choice.corpus_root=f.corpus_root AND choice.file_id=f.id AND choice.profile=%(profile)s
 WHERE f.corpus_root=%(root)s AND f.present AND strpos(lower(f.path),lower(%(query)s))>0
), candidates AS (
 SELECT c.corpus_root,c.document_id,c.cache_key,c.validation_state,c.chunk_count,c.cache_digest,c.verified_at,f.path AS file_path,f.chosen_key,f.metadata_digest,
 count(*) FILTER(WHERE c.validation_state='valid' AND c.chunk_count>0) OVER(PARTITION BY c.document_id) AS valid_count
 FROM rag.km_corpus_caches c JOIN files f ON f.corpus_root=c.corpus_root AND f.id=c.document_id
 WHERE c.corpus_root=%(root)s AND c.present
), selected AS (
 SELECT * FROM candidates WHERE validation_state='valid' AND chunk_count>0
 AND (chosen_key=cache_key OR (chosen_key IS NULL AND valid_count=1))
), publications AS MATERIALIZED (
 SELECT document_id,metadata->>'pdfparse_cache_digest' AS cache_digest,
 metadata->>'km_metadata_digest' AS metadata_digest FROM rag.km_profile_documents WHERE profile=%(profile)s
), states AS NOT MATERIALIZED (
 SELECT cs.file_id,cs.cache_key,cs.chunk_id,cs.document_id,cs.cache_digest,cs.input_hash,cs.ordinal,cs.pages,
 s.file_path,s.verified_at,substring(cs.record->>'text' for 240) AS preview,
 CASE WHEN e.chunk_id IS NOT NULL AND pd.cache_digest=cs.cache_digest
      AND pd.metadata_digest IS NOT DISTINCT FROM s.metadata_digest THEN 'published'
 WHEN e.chunk_id IS NOT NULL OR (ck.embedding IS NOT NULL AND ck.status IN ('encoded','indexed')) THEN 'computed'
 WHEN ck.status='encoding' THEN 'encoding'
 WHEN ck.status='failed' THEN 'failed' WHEN ck.status='interrupted' THEN 'interrupted' ELSE 'pending' END AS state
 FROM rag.km_cache_chunks cs JOIN selected s
 ON s.corpus_root=cs.corpus_root AND s.document_id=cs.file_id AND s.cache_key=cs.cache_key AND s.cache_digest=cs.cache_digest
 LEFT JOIN rag.km_profile_units u ON u.profile=%(profile)s AND u.unit_id=cs.chunk_id AND u.document_id=cs.document_id AND u.kind='chunk'
 LEFT JOIN rag.km_text_embeddings e ON e.profile=u.profile AND e.chunk_id=u.unit_id AND e.input_hash=cs.input_hash
 LEFT JOIN publications pd ON pd.document_id=cs.document_id
 LEFT JOIN rag.km_chunk_indexing ck ON ck.document_id=cs.document_id AND ck.chunk_id=cs.chunk_id AND ck.input_hash=cs.input_hash
 AND ck.encoder_fingerprint=%(fingerprint)s AND ck.profile=%(profile)s
), counts AS (
 SELECT file_id,count(*) AS total,count(*) FILTER(WHERE state='published') AS published,
 count(*) FILTER(WHERE state IN ('computed','published')) AS saved,
 count(*) FILTER(WHERE state IN ('pending','failed','interrupted')) AS missing
 FROM states GROUP BY file_id
), file_states AS (
 SELECT f.id AS file_id,f.path,f.version_label,f.chosen_key,
 coalesce(n.caches,0) AS cache_count,coalesce(n.valid,0) AS valid_caches,s.cache_key,
 coalesce(g.total,0) AS chunks,coalesce(g.published,0) AS published,coalesce(g.saved,0) AS saved,coalesce(g.missing,0) AS missing,
 CASE WHEN coalesce(n.caches,0)=0 THEN 'no_cache'
 WHEN s.cache_key IS NULL AND f.chosen_key IS NULL AND coalesce(n.valid,0)>1 THEN 'ambiguous'
 WHEN s.cache_key IS NULL THEN 'blocked'
 WHEN g.total=g.published THEN 'published' WHEN g.total=g.saved THEN 'computed'
 WHEN g.saved>0 THEN 'partial' ELSE 'pending' END AS state
 FROM files f
 LEFT JOIN (SELECT document_id,count(*) AS caches,count(*) FILTER(WHERE validation_state='valid' AND chunk_count>0) AS valid
   FROM candidates GROUP BY document_id) n ON n.document_id=f.id
 LEFT JOIN selected s ON s.document_id=f.id
 LEFT JOIN counts g ON g.file_id=f.id
)
"""

UPSERT_CACHED_CHUNK = ('INSERT INTO rag.km_cache_chunks(corpus_root,file_id,cache_key,chunk_id,document_id,source_chunk_id,cache_digest,input_hash,ordinal,pages,record) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(corpus_root,file_id,cache_key,chunk_id) DO UPDATE SET '
                    'document_id=excluded.document_id,source_chunk_id=excluded.source_chunk_id,cache_digest=excluded.cache_digest,'
                    'input_hash=excluded.input_hash,ordinal=excluded.ordinal,pages=excluded.pages,record=excluded.record')

MARK_CACHE_VALID = ("UPDATE rag.km_corpus_caches SET validation_state='valid',validation_error=NULL,cache_digest=%s,"
                         'km_document_id=%s,chunk_count=%s,verified_at=now() WHERE corpus_root=%s AND document_id=%s AND cache_key=%s')

MARK_CACHE_INVALID = ("UPDATE rag.km_corpus_caches SET validation_state='invalid',validation_error=%s,chunk_count=0,verified_at=now() "
                     'WHERE corpus_root=%s AND document_id=%s AND cache_key=%s')

READ_ONLY_SNAPSHOT = 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'

CHUNK_STATE_COUNTS = (CHUNK_CATALOG_BASE+' SELECT state,count(*) AS n FROM states GROUP BY state')

def list_chunk_states(state):
    filter_sql=" WHERE state IN ('pending','failed','interrupted')" if state=='missing' else ' WHERE state=%(state)s' if state else ''
    return (CHUNK_CATALOG_BASE+' SELECT * FROM states'+filter_sql+' ORDER BY file_path,file_id,ordinal,chunk_id LIMIT %(limit)s OFFSET %(offset)s')

COUNT_BLOCKED_FILES = (CHUNK_CATALOG_BASE+" SELECT count(*) AS total,count(*) FILTER(WHERE state IN ('blocked','ambiguous','no_cache')) AS blocked FROM file_states")

FILE_STATE_COUNTS = (CHUNK_CATALOG_BASE+' SELECT state,count(*) AS n FROM file_states GROUP BY state')

def list_file_states(state):
    filter_sql=" WHERE state IN ('pending','partial','computed')" if state=='missing' else ' WHERE state=%(state)s' if state else ''
    return (CHUNK_CATALOG_BASE+' SELECT * FROM file_states'+filter_sql+
                              ' ORDER BY path,file_id LIMIT %(limit)s OFFSET %(offset)s')

LIST_FILE_CACHES = ('SELECT cache_key,validation_state,validation_error,chunk_count,cache_digest,verified_at '
                    'FROM rag.km_corpus_caches WHERE corpus_root=%s AND document_id=%s AND present ORDER BY cache_key LIMIT 100')

VALID_CACHE_CHOICE = ("SELECT 1 FROM rag.km_corpus_caches c JOIN rag.km_corpus_files f ON f.corpus_root=c.corpus_root AND f.id=c.document_id "
                "WHERE c.corpus_root=%s AND c.document_id=%s AND c.cache_key=%s AND c.present AND f.present AND c.validation_state='valid' AND c.chunk_count>0")

UPSERT_CACHE_CHOICE = ('INSERT INTO rag.km_profile_cache_choices(corpus_root,file_id,profile,cache_key) VALUES(%s,%s,%s,%s) '
                'ON CONFLICT(corpus_root,file_id,profile) DO UPDATE SET cache_key=excluded.cache_key,selected_at=now()')

CACHE_INFO = ("SELECT cache_key,path,present,validation_state,validation_error,chunk_count,verified_at,"
                "manifest->'configuration' AS configuration,manifest->'runtime_configuration' AS runtime_configuration,"
                "manifest->>'configuration_hash' AS configuration_hash FROM rag.km_corpus_caches WHERE corpus_root=%s AND document_id=%s AND cache_key=%s")

PLAN_PENDING_CHUNKS = (CHUNK_CATALOG_BASE+' SELECT states.file_id,file_path,cache_key,chunk_id,cache_digest,input_hash,counts.missing AS file_missing '
                'FROM states JOIN counts USING(file_id) WHERE '+
                " NOT(file_id=ANY(%(excluded)s)) AND (%(cutoff)s::timestamptz IS NULL OR verified_at<=%(cutoff)s::timestamptz)"+
                " AND state IN ('pending','failed','interrupted') ORDER BY file_path,file_id,ordinal,chunk_id LIMIT %(limit)s")

PLAN_COMPUTED_FILES = (CHUNK_CATALOG_BASE+' SELECT f.file_id,f.path AS file_path,f.cache_key,s.cache_digest FROM file_states f '
                'JOIN selected s ON s.document_id=f.file_id WHERE f.state=\'computed\' AND NOT(f.file_id=ANY(%(excluded)s)) '
                'AND (%(cutoff)s::timestamptz IS NULL OR s.verified_at<=%(cutoff)s::timestamptz) ORDER BY f.path,f.file_id LIMIT %(limit)s')

ENQUEUE_ALL_JOB_FILES = (CHUNK_CATALOG_BASE+" INSERT INTO rag.km_chunk_job_files(job_id,corpus_root,profile,file_id,path,cache_key,cache_digest,missing_count) "
                "SELECT %(job)s,%(root)s,%(profile)s,f.file_id,f.path,f.cache_key,s.cache_digest,f.missing FROM file_states f "
                "JOIN selected s ON s.document_id=f.file_id WHERE f.state IN ('pending','partial','computed') AND s.verified_at<=%(cutoff)s::timestamptz")

COUNT_JOB_WORK = 'SELECT count(*) AS files,coalesce(sum(missing_count),0) AS chunks FROM rag.km_chunk_job_files WHERE job_id=%s'

ENQUEUE_SELECTED_JOB_FILE = ('INSERT INTO rag.km_chunk_job_files(job_id,corpus_root,profile,file_id,path,cache_key,cache_digest,missing_count,chunk_ids) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)')

NEXT_JOB_FILE = "SELECT *,path AS file_path FROM rag.km_chunk_job_files WHERE job_id=%s AND corpus_root=%s AND status='pending' ORDER BY path,file_id LIMIT 1"

START_JOB_FILE = "UPDATE rag.km_chunk_job_files SET status='running' WHERE job_id=%s AND file_id=%s"

FINISH_JOB_FILE = 'UPDATE rag.km_chunk_job_files SET status=%s,computed=%s,published=%s,error=%s WHERE job_id=%s AND file_id=%s AND corpus_root=%s'

COUNT_FAILED_JOB_FILES = "SELECT count(*) AS n FROM rag.km_chunk_job_files WHERE job_id=%s AND corpus_root=%s AND status='failed'"

LIST_FAILED_JOB_FILES = "SELECT file_id,path,cache_key,status,error FROM rag.km_chunk_job_files WHERE job_id=%s AND corpus_root=%s AND status='failed' ORDER BY path,file_id LIMIT %s OFFSET %s"

# Embedding management

DELETION_PROFILE = "SELECT profile,encoder_fingerprint,dimension FROM rag.embedding_profiles WHERE profile=%s AND modality='text'"

DELETION_LOCK_TIMEOUT = "SET LOCAL lock_timeout='5s'"

DELETION_STATEMENT_TIMEOUT = "SET LOCAL statement_timeout='60s'"

LOCK_DELETION_TABLES = 'LOCK TABLE rag.km_text_embeddings,rag.km_chunk_indexing,rag.text_embeddings IN SHARE ROW EXCLUSIVE MODE'

def delete_profile_vectors(queries):
    return ('DELETE FROM rag.km_text_embeddings WHERE profile=%s AND chunk_id IN (SELECT q.chunk_id FROM ('+queries[0][1]+') q)')

def delete_legacy_vectors(queries):
    return ('DELETE FROM rag.text_embeddings WHERE profile=%s AND chunk_id IN (SELECT q.chunk_id FROM ('+queries[2][1]+') q)')

def delete_checkpoints(scope):
    where = '(profile=%s OR (profile IS NULL AND encoder_fingerprint=%s))'
    if scope == 'document':
        where += ' AND document_id=%s'
    elif scope == 'chunk':
        where += ' AND chunk_id=%s'
    return ('DELETE FROM rag.km_chunk_indexing WHERE '+where)

INSERT_DELETION_AUDIT = 'INSERT INTO rag.km_embedding_deletions(id,profile,scope,target,encoder_fingerprint,counts) VALUES(%s,%s,%s,%s,%s,%s)'


def embedding_deletion_queries(profile,fingerprint,scope,document_id,chunk_id):
    extra='';params=[profile]
    ck_extra='';ck_params=[profile,fingerprint]
    legacy_extra='';legacy_params=[profile]
    if scope=='document':
        extra=' AND u.document_id=%s';params.append(document_id)
        ck_extra=' AND c.document_id=%s';ck_params.append(document_id)
        legacy_extra=' AND c.document_id=%s';legacy_params.append(document_id)
    elif scope=='chunk':
        extra=' AND e.chunk_id=%s';params.append(chunk_id)
        ck_extra=' AND c.chunk_id=%s';ck_params.append(chunk_id)
        legacy_extra=' AND e.chunk_id=%s';legacy_params.append(chunk_id)
    vectors="""SELECT e.chunk_id,e.input_hash,e.indexed_at,u.document_id
        FROM rag.km_text_embeddings e JOIN rag.km_profile_units u ON u.profile=e.profile AND u.unit_id=e.chunk_id
        WHERE e.profile=%s"""+extra+' ORDER BY e.chunk_id'
    checkpoints="""SELECT c.document_id,c.chunk_id,c.input_hash,c.encoder_fingerprint,c.status,
        c.updated_at,c.embedding IS NOT NULL AS has_vector
        FROM rag.km_chunk_indexing c WHERE (c.profile=%s OR (c.profile IS NULL AND c.encoder_fingerprint=%s))"""+ck_extra+' ORDER BY c.document_id,c.chunk_id,c.input_hash,c.encoder_fingerprint'
    legacy="""SELECT e.chunk_id,e.input_hash,c.document_id FROM rag.text_embeddings e
        JOIN rag.chunks c USING(chunk_id) WHERE e.profile=%s"""+legacy_extra+' ORDER BY e.chunk_id'
    return [('published_vectors',vectors,params),('checkpoint_rows',checkpoints,ck_params),('legacy_vectors',legacy,legacy_params)]
