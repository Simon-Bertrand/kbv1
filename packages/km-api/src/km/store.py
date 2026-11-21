"""Bounded read-only SQL adapter. No catalogue, model or ingestion writes."""
from contextlib import contextmanager
import hashlib
import re

from psycopg.types.json import Jsonb
from . import sql_client

def handle(kind, ident):
    return kind + hashlib.md5(ident.encode(), usedforsecurity=False).hexdigest()

def checked_handle(ident, kinds):
    if not isinstance(ident, str) or not re.fullmatch('[' + kinds + '][0-9a-f]{32}', ident):
        raise ValueError('Invalid source handle')
    return ident[0], ident[1:]

def _document_identities(conn, ids, *, profile=None):
    if not ids:
        return {}
    args = (list(set(ids)),) if profile is None else (list(set(ids)), profile)
    rows = conn.execute(
        sql_client.document_identities(profile is not None), args,
    ).fetchall()
    return {row['document_id']: {key: row[key] for key in
            ('original_name', 'type_document_id', 'type_document_label')} for row in rows}


def document(row):
    name = row.get('original_name')
    version = row['metadata'].get('km', {}).get('version_label')
    return {'id': handle('d', row['document_id']), 'upstream_id': row['document_id'],
            'observed': {'original_name': name}, 'original_name': name,
            'type_document_id': row.get('type_document_id'),
            'type_document_label': row.get('type_document_label'),
            'metadata': row['metadata'], 'filepath': row['filepath'], 'status': 'published',
            'display_name': f'{name} (version {version})' if name and version else name}


def unit(row):
    record = row['record']
    pages = record.get('body_pages') or record.get('pages') or []
    if not pages:
        pages = sorted({loc.get('page_no', loc.get('page')) for loc in record.get('locations', [])
                        if isinstance(loc, dict) and type(loc.get('page_no', loc.get('page'))) is int
                        and loc.get('page_no', loc.get('page')) > 0})
    if not isinstance(pages, list) or any(type(p) is not int or p < 1 for p in pages):
        raise ValueError('Invalid physical page locators')
    kind = row.get('kind', 'chunk')
    ident = row.get('id') or row.get('chunk_id') or row.get('visual_id')
    if kind in {'chunk','markdown'}:
        # Keep generated context distinct from verbatim source in the response.
        text = record.get('text', '')
        context = record.get('context', '')
    else:
        text = record.get('ocr_text') or record.get('caption') or ''
        context = record.get('description') or ''
    if not isinstance(text, str) or not isinstance(context, str):
        raise ValueError('Invalid source text')
    locator = {'type': kind, 'pages': pages, 'record_id': ident}
    if kind == 'markdown':
        locator.update(start_line=record.get('start_line'),end_line=record.get('end_line'),
                       start_char=record.get('start_char'),end_char=record.get('end_char'))
    if len(pages) == 1:
        locator['page'] = pages[0]
    return {'id': handle('c' if kind in {'chunk','markdown'} else 'v', ident),
            'document_id': handle('d', row['document_id']), 'locator': locator,
            **({'source_sha256': row['source_sha256']} if row.get('source_sha256') else {}),
            'blocks': [{'type': 'text', 'text': text}],
            'context': context, 'record': record, 'image': kind == 'visual' and bool(record.get('asset_path')),
            'original_name': row.get('original_name'), 'type_document_id': row.get('type_document_id'),
            'type_document_label': row.get('type_document_label')}

class Knowledgebase:
    def __init__(self, dsn, *, timeout_ms=10000, km=False):
        self.client = sql_client.KmDbClient(dsn, timeout_ms=timeout_ms)
        self.dsn, self.timeout_ms, self.km = self.client.dsn, self.client.timeout_ms, km

    @contextmanager
    def pooled(self):
        with self.client.pooled():
            yield self

    def connection(self):
        return self.client.connection()

    def ready(self):
        with self.connection() as conn:
            conn.execute(sql_client.CHECK_CHUNKS).fetchall()
            if self.km:
                conn.execute(sql_client.CHECK_KM_FILES).fetchall()
                conn.execute(sql_client.CHECK_PROFILE_UNITS).fetchall()
                conn.execute(sql_client.CHECK_EMBEDDING_SETTINGS).fetchall()
            return conn.execute(sql_client.READY_EXTENSIONS).fetchall()

    def profile(self, modality):
        profile = 'text-v1' if modality == 'text' else 'visual-v1'
        with self.connection() as conn:
            if self.km and modality=='text':
                from .profiles import active
                profile=active(conn)
            return conn.execute(sql_client.PROFILE_BY_MODALITY,
                                (profile, modality)).fetchone()

    def documents(self, offset, limit):
        if self.km:return self.profile_documents(offset,limit)
        with self.connection() as conn:
            total = conn.execute(sql_client.COUNT_DOCUMENTS).fetchone()['n']
            rows = conn.execute(sql_client.LIST_DOCUMENTS,
                                (limit, offset)).fetchall()
        return {'items': [document(r) for r in rows], 'total': total, 'offset': offset, 'limit': limit}

    def document(self, ident):
        if self.km:return self.profile_document(ident)
        _, digest = checked_handle(ident, 'd')
        with self.connection() as conn:
            rows = conn.execute(sql_client.DOCUMENT_BY_DIGEST, (digest,)).fetchall()
            ids = _document_identities(conn, [row['document_id'] for row in rows])
        if len(rows) > 1:
            raise ValueError('Ambiguous document handle')
        return {**document(rows[0]), **ids[rows[0]['document_id']]} if rows else None

    def units(self, ident, offset, limit):
        if self.km:return self.profile_units(ident,offset,limit)
        _, digest = checked_handle(ident, 'd')
        with self.connection() as conn:
            docs = conn.execute(sql_client.DOCUMENT_ID_BY_DIGEST, (digest,)).fetchall()
            if not docs:
                return None
            if len(docs) != 1:
                raise ValueError('Ambiguous document handle')
            upstream = docs[0]['document_id']
            union = sql_client.document_units(self.km)
            params=(upstream,upstream,upstream) if self.km else (upstream,upstream)
            total=conn.execute(sql_client.count_document_units(union),params).fetchone()['n']
            rows=conn.execute(sql_client.list_document_units(union),
                (*params,limit,offset)).fetchall()
        return {'items': [dict(unit(r), index=offset+i) for i, r in enumerate(rows)],
                'total': total, 'offset': offset, 'limit': limit}

    def unit(self, ident):
        if self.km:return self.profile_unit(ident)
        kind, digest = checked_handle(ident, 'cv')
        # The only interpolated SQL identifiers come from this constant mapping.
        table, key, label = ('chunks', 'chunk_id', 'chunk') if kind == 'c' else ('visuals', 'visual_id', 'visual')
        with self.connection() as conn:
            if self.km and kind=='c':
                rows=conn.execute(sql_client.KM_UNIT_BY_DIGEST,(digest,)).fetchall()
            else:
                rows = conn.execute(sql_client.legacy_unit_by_digest(key, table),(label,digest)).fetchall()
        if len(rows) > 1:
            raise ValueError('Ambiguous record handle')
        return unit(rows[0]) if rows else None

    def profile_documents(self,offset,limit):
        from .profiles import active
        with self.connection() as conn:
            profile=active(conn)
            total=conn.execute(sql_client.COUNT_PROFILE_DOCUMENTS,(profile,)).fetchone()['n']
            rows=conn.execute(sql_client.LIST_PROFILE_DOCUMENTS,(profile,limit,offset)).fetchall()
        return {'items':[dict(document(r),profile=profile) for r in rows],'total':total,'offset':offset,'limit':limit,'profile':profile}

    def profile_document(self,ident):
        from .profiles import active
        _,digest=checked_handle(ident,'d')
        with self.connection() as conn:
            profile=active(conn)
            rows=conn.execute(sql_client.PROFILE_DOCUMENT_BY_DIGEST,(profile,digest)).fetchall()
            if not rows:
                rows = conn.execute(
                    sql_client.RECENT_PROFILE_DOCUMENT_BY_DIGEST,
                    (digest,),
                ).fetchall()
            ids = (_document_identities(conn, [rows[0]['document_id']], profile=rows[0]['profile'])
                   if len(rows) == 1 else {})
        if len(rows)>1:raise ValueError('Ambiguous document handle')
        return dict(document(rows[0]), **ids[rows[0]['document_id']], profile=rows[0]['profile']) if rows else None

    def profile_units(self,ident,offset,limit):
        from .profiles import active
        _,digest=checked_handle(ident,'d')
        with self.connection() as conn:
            profile=active(conn)
            docs=conn.execute(sql_client.PROFILE_DOCUMENT_ID_BY_DIGEST,(profile,digest)).fetchall()
            if not docs:return None
            if len(docs)>1:raise ValueError('Ambiguous document handle')
            doc=docs[0]['document_id']
            total=conn.execute(sql_client.COUNT_PROFILE_UNITS,(profile,doc)).fetchone()['n']
            rows=conn.execute(sql_client.LIST_PROFILE_UNITS,(profile,doc,limit,offset)).fetchall()
        return {'items':[dict(unit(r),index=offset+i,profile=profile) for i,r in enumerate(rows)],
                'total':total,'offset':offset,'limit':limit,'profile':profile}

    def profile_unit(self,ident):
        from .profiles import active
        kind,digest=checked_handle(ident,'cv')
        with self.connection() as conn:
            profile=active(conn)
            kinds=['chunk','markdown'] if kind=='c' else ['visual']
            rows=conn.execute(sql_client.PROFILE_UNIT_BY_DIGEST,(profile,digest,kinds)).fetchall()
        if len(rows)>1:raise ValueError('Ambiguous record handle')
        return dict(unit(rows[0]),profile=profile) if rows else None
    def physical_page(self, ident, number):
        from .physical_page import reconstruct, MAX_RECORDS
        from .profiles import active
        _, digest = checked_handle(ident, 'd')
        if type(number) is not int or not 1 <= number <= 100000:
            raise ValueError('Invalid physical page')
        with self.connection() as conn:
            profile = active(conn) if self.km else None
            params = (profile, digest) if self.km else (digest,)
            docs = conn.execute(sql_client.physical_document(self.km), params).fetchall()
            if not docs: return None
            if len(docs) > 1: raise ValueError('Ambiguous document handle')
            doc = docs[0]
            if self.km:
                source = sql_client.PROFILE_PAGE_SOURCE
                params = (profile, doc['document_id'])
            else:
                source = sql_client.LEGACY_PAGE_SOURCE
                params = (doc['document_id'], doc['document_id'])
            # body_pages excludes section-context pages; old records fall back to locations.
            rows = conn.execute(sql_client.physical_page_units(source), (*params, Jsonb([number]), Jsonb({'page':number}), MAX_RECORDS+1)).fetchall()
            if not rows: return None
            for row in rows: row['filepath'] = doc['filepath']
            result = reconstruct(ident, number, rows, profile)
            if source_sha := doc.get('source_sha256'):
                result['source_sha256'] = source_sha
            return result

    def _scope_conditions(self, metadata_filters, document_ids):
        from .metadata import filter_conditions
        conditions, params = [], []
        if document_ids:
            if not isinstance(document_ids, list) or len(document_ids) > 200:
                raise ValueError('Liste de documents invalide')
            digests = [checked_handle(ident, 'd')[1] for ident in document_ids]
            conditions.append(sql_client.DOCUMENT_DIGEST_SCOPE)
            params.append(digests)
        if metadata_filters:
            if not self.km:
                raise ValueError('Filtres métier disponibles uniquement dans KM')
            predicate, arguments = filter_conditions(metadata_filters)
            conditions.append(sql_client.metadata_scope(predicate))
            params.extend(arguments)
        return conditions, params

    def _filtered_search(self, conn, profile, query, mode, limit, filters, vector, metadata_filters, document_ids, type_scope=(None, None, None), date_filters=None):
        if (not isinstance(query, str) or not query.strip() or len(query) > 4000
                or mode not in {'lexical', 'semantic', 'hybrid'}
                or type(limit) is not int or not 1 <= limit <= 100
                or filters is not None and not isinstance(filters, dict)):
            raise ValueError('Paramètres de recherche filtrée invalides')
        if mode != 'lexical':
            if vector is None:
                raise ValueError('Vecteur textuel 1024 requis')
            shape = conn.execute(
                sql_client.VECTOR_SHAPE,
                (vector, vector)).fetchone()
            if shape['dimensions'] != 1024 or not shape['norm']:
                raise ValueError('Vecteur textuel 1024 non nul requis')
        scope, scope_params = self._scope_conditions(metadata_filters, document_ids)
        conditions = [sql_client.PROFILE_SCOPE, *scope]
        params = [profile, *scope_params]
        if any(value is not None for value in type_scope):
            type_ids = conn.execute(sql_client.RESOLVE_DOCUMENT_TYPES, type_scope).fetchone()['ids']
            conditions.append(sql_client.DOCUMENT_TYPE_SCOPE)
            params.append(type_ids)
        if date_filters:
            date_ids = conn.execute(sql_client.RESOLVE_DOCUMENT_DATES,
                                    (Jsonb(date_filters),)).fetchone()['ids']
            conditions.append(sql_client.DOCUMENT_DATE_SCOPE)
            params.append(date_ids)
        if filters:
            conditions.append(sql_client.DOCUMENT_METADATA_SCOPE)
            params.append(Jsonb(filters))
        candidates = max(100, limit)
        conn.execute(sql_client.HNSW_STRICT_ORDER)
        conn.execute(sql_client.HNSW_SEARCH_BUDGET)
        return conn.execute(sql_client.filtered_search(conditions), [*params, query, mode, query, candidates, vector, mode, vector,
                                  candidates, limit, profile]).fetchall()

    def _profile_visual_search(self, conn, vector, limit, filters, conditions, scope_params):
        from .profiles import active

        if type(limit) is not int or not 1 <= limit <= 100 or (filters is not None and not isinstance(filters, dict)):
            raise ValueError('Paramètres de recherche visuelle invalides')
        shape = conn.execute(
            sql_client.VECTOR_SHAPE,
            (vector, vector)).fetchone()
        if shape['dimensions'] != 768 or not shape['norm']:
            raise ValueError('Vecteur visuel 768 non nul requis')
        profile = active(conn)
        conditions = [sql_client.PROFILE_SCOPE, *conditions]
        params = [profile, *scope_params]
        if filters:
            conditions.append(sql_client.DOCUMENT_METADATA_SCOPE)
            params.append(Jsonb(filters))
        conn.execute(sql_client.HNSW_STRICT_ORDER)
        conn.execute(sql_client.HNSW_SEARCH_BUDGET)
        return conn.execute(sql_client.profile_visual_search(conditions), (vector, *params, profile, limit, profile)).fetchall()

    def _visual_search(self, conn, vector, limit, filters, metadata_filters, document_ids):
        conditions, scope_params = self._scope_conditions(metadata_filters, document_ids)
        if self.km:
            return self._profile_visual_search(conn, vector, limit, filters, conditions, scope_params)
        return conn.execute(sql_client.legacy_visual_search(conditions),
            (vector, limit, Jsonb(filters) if filters else None, *scope_params)).fetchall()

    def document_types(self):
        with self.connection() as conn:
            return conn.execute(sql_client.DOCUMENT_TYPES).fetchall()

    def chunk_document_metadata(self, chunk_ids):
        from .profiles import active
        with self.connection() as conn:
            return conn.execute(
                sql_client.CHUNK_DOCUMENT_METADATA,
                (chunk_ids, active(conn) if self.km else None),
            ).fetchall()

    def search(self, query, mode, limit, filters, vector=None, *, visual=False, fingerprint=None,
               metadata_filters=None, document_ids=None, type_document_ids=None,
               perimeter_code=None, type_document_labels=None, date_filters=None):
        with self.connection() as conn:
            from .profiles import active
            selected=active(conn) if self.km and not visual else ('visual-v1' if visual else 'text-v1')
            if fingerprint is not None:
                profile = conn.execute(sql_client.PROFILE_FINGERPRINT,
                    (selected,)).fetchone()
                if not profile or profile['encoder_fingerprint'] != fingerprint:
                    raise RuntimeError('Embedding profile changed during query preparation')
            type_scope = (type_document_ids, perimeter_code, type_document_labels)
            if visual:
                rows = self._visual_search(conn, vector, limit, filters, metadata_filters, document_ids)
            elif metadata_filters or document_ids:
                if not self.km:
                    raise ValueError('Recherche filtrée métier disponible pour les publications textuelles KM')
                rows = self._filtered_search(conn, selected, query, mode, limit, filters, vector,
                                             metadata_filters or [], document_ids or [], type_scope, date_filters)
            elif self.km:
                rows=conn.execute(sql_client.PROFILE_SEARCH,
                    (selected,query,vector,mode,limit,max(100,limit),Jsonb(filters) if filters else None,*type_scope, Jsonb(date_filters) if date_filters else None)).fetchall()
            else:
                rows = conn.execute(sql_client.LEGACY_TEXT_SEARCH,
                    (query, vector, mode, limit, max(100, limit), Jsonb(filters) if filters else None,*type_scope, Jsonb(date_filters) if date_filters else None)).fetchall()
            identity_profile = (active(conn) if visual else selected) if self.km else None
            identities = _document_identities(conn, [row['document_id'] for row in rows], profile=identity_profile)
        results = []
        for row in rows:
            item = unit(row)
            hit = {'id': item['id'], 'page_id': item['id'],
                   'document_id': item['document_id'], 'score': row['score'],
                   'locator': item['locator'], 'text': item['blocks'][0]['text'],
                   'lexical_rank': row.get('lexical_rank'), 'semantic_rank': row.get('semantic_rank'),
                   **identities[row['document_id']]}
            if visual and row['kind'] == 'chunk':
                matched = row.get('matched_visual_ids', (row.get('evidence') or {}).get('matched_visual_ids', []))
                if matched:
                    hit['visual_page_ids'] = [handle('v', ident) for ident in matched[:8]]
            results.append(hit)
        return {'results': results, 'backend': 'postgres', 'mode': 'visual' if visual else mode,'profile':selected}
