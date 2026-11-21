"""Global chunk-budget jobs; incomplete PDFs keep checkpoints, never partial RAG publications."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import uuid
import httpx
import psycopg
from km.chunk_catalog import ChunkCatalog
from km.diagnostics import database_diagnostic
from km.inventory import confined, read_bytes
from km._service_logging import submit_with_context


def archive_source(bundle):
    url = os.environ.get('KM_SOURCE_ARCHIVE_URL')
    if not url:
        return
    token = (os.environ.get('KM_INDEXER_TOKEN') or '').strip()
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    try:
        response = httpx.post(
            url, headers=headers, timeout=60, trust_env=False,
            json={'path': bundle['filepath'], 'sha256': bundle.get('source_sha256', bundle['document_id'])},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 409):
            raise ValueError(exc.response.json()['detail']) from exc
        raise RuntimeError(f'Archivage PDF refusé par km-api (HTTP {exc.response.status_code}).') from exc
    except httpx.RequestError as exc:
        raise RuntimeError('km-api indisponible pour archiver le PDF.') from exc


def now(): return datetime.now(timezone.utc).isoformat()


def submit(jobs, profile, limit=500, all_chunks=False, query='', idempotency_key=None):
    catalog = getattr(jobs, 'catalog', None)
    if catalog is None: raise RuntimeError('Catalogue SQL indisponible.')
    params_hash = hashlib.sha256(json.dumps({'profile': profile, 'limit': limit, 'all_chunks': all_chunks, 'query': query}, sort_keys=True).encode()).hexdigest()
    with jobs.mutex:
        existing = jobs.find_by_submission_key(idempotency_key) if idempotency_key else None
        if existing is not None:
            if existing.get('idempotency_params_hash') != params_hash:
                raise ValueError('Clé d’idempotence déjà utilisée avec des paramètres différents.')
            return existing
        if catalog.status()['status'] != 'succeeded':
            raise RuntimeError('Synchroniser complètement le corpus avant de lancer les chunks.')
        if any(row['status'] in ('running', 'queued') for row in jobs.summaries()):
            raise RuntimeError('Un travail KM est déjà actif.')
        assigned = jobs.publisher.ensure_current_profile(profile)
        sources = ChunkCatalog(catalog)
        snapshot = sources.chunks(assigned['profile'], query=query, limit=1)
        missing = sum(snapshot['summary'][s] for s in ('pending', 'failed', 'interrupted'))
        rows, ready = ([], []) if all_chunks else sources.plan(assigned['profile'], limit, query)
        has_work = bool(missing or snapshot['summary']['computed']) if all_chunks else bool(rows or ready)
        if not has_work:
            raise ValueError('Aucun chunk manquant ni PDF prêt à publier. Vérifier les caches bloqués ou ambigus.')
        job = {'id': uuid.uuid4().hex, 'unit': 'chunk', 'all_chunks': all_chunks, 'profile': assigned['profile'],
             'encoder_fingerprint': assigned['encoder_fingerprint'], 'status': 'queued', 'stage': 'queued', 'created_at': now(),
             'finished_at': None, 'total': missing if all_chunks else len(rows), 'processed': 0, 'completed': 0, 'failed_count': 0,
             'failed_documents': 0, 'published_documents': 0, 'document_results': [], 'results': [],
             'query': query, 'idempotency_key': idempotency_key, 'idempotency_params_hash': params_hash,
             'scope_cutoff': now(), 'blocked_documents': snapshot['pdfs']['blocked']}
        jobs.stop_event.clear()
        if not all_chunks:sources.enqueue_plan(job['id'],job['profile'],rows,ready)
        jobs.save(job)
        submit_with_context(jobs.executor, execute, jobs, job)
        return dict(job)


def execute(jobs,job):
    job.update(status='running',stage='cache',started_at=now())
    jobs.save(job)
    source=ChunkCatalog(jobs.catalog)
    try:
        if job['all_chunks']:
            queue=source.enqueue_all(job['id'],job['profile'],job['query'],job['scope_cutoff'])
            job['total']=int(queue['chunks']);jobs.save(job)
        while not jobs.stop_event.is_set():
            if jobs.publisher.encoder.fingerprint!=job['encoder_fingerprint']:raise RuntimeError('Encodeur changé ; relancer avec le profil correspondant.')
            base=source.next_file(job['id'])
            if not base:break
            file_id=base['file_id']
            outcome={'document_id':file_id,'path':base['file_path'],'cache_key':base['cache_key']}
            expected=base['missing_count']
            before_processed,before_completed=job['processed'],job['completed']
            def progress(stage):
                if isinstance(stage,dict):
                    job['stage']=stage['stage'];job['chunk_progress']=stage
                    job['completed']=before_completed+stage.get('encoded',0)
                    job['processed']=before_processed+min(expected,stage.get('encoded',0))
                else:job['stage']=stage
                jobs.save(job)
            try:
                if jobs.publisher.encoder.fingerprint!=job['encoder_fingerprint']:raise RuntimeError('Encodeur changé ; relancer avec le profil correspondant.')
                files=jobs.catalog.lookup([file_id])
                if not files:raise ValueError('PDF absent ; synchroniser le corpus.')
                jobs.parser.entries.update({row['id']:row for row in files})
                bundle=jobs.parser.prepare(file_id,progress,cache_key=base['cache_key'])
                if bundle['metadata'].get('pdfparse_cache_digest')!=base['cache_digest']:
                    raise ValueError('Cache modifié depuis la synchronisation ; synchroniser avant de relancer.')
                bundle={**bundle,'metadata':{**bundle.get('metadata',{}),'km_indexing_job_id':job['id']}}
                archive_source(bundle)
                def verify():
                    jobs.parser.verify_source(bundle)
                    cache_dir = bundle['metadata'].get('pdfparse_cache_dir')
                    if cache_dir and Path(cache_dir).is_dir():
                        directory = Path(cache_dir)
                        if any(p.is_symlink() for p in (directory, *directory.parents)) or directory.name != base['cache_key']:
                            raise ValueError('Répertoire de cache externe non conforme.')
                    else:
                        directory = confined(jobs.parser.root, bundle['metadata']['pdfparse_cache_path'])
                    manifests = [directory / name for name in ('manifest.json', 'manisfest.json') if (directory / name).exists()]
                    if len(manifests) != 1 or hashlib.sha256(read_bytes(manifests[0])).hexdigest() != base['cache_digest']:
                        raise ValueError('Cache modifié pendant le calcul ; publication refusée. Synchroniser le corpus.')
                result=jobs.publisher.publish(bundle,progress,profile=job['profile'],
                    chunk_ids=None if base['chunk_ids'] is None else set(base['chunk_ids']),
                    verify_source=verify,stop=jobs.stop_event.is_set)
                job['completed']=before_completed+result['encoded']
                job['processed']=before_processed+(min(expected,result['encoded']) if result.get('stopped') else expected)
                if result.get('published'):job['published_documents']+=1
                outcome.update(status='succeeded',published=result.get('published',False),encoded=result['encoded'])
                source.finish_file(job['id'],file_id,'stopped' if result.get('stopped') else 'succeeded',result['encoded'],result.get('published',False))
            except Exception as exc:
                job['failed_documents']+=1
                job['failed_count']+=max(0,expected-(job['completed']-before_completed))
                job['processed']=before_processed+expected
                message=database_diagnostic(exc)['detail'] if isinstance(exc,psycopg.Error) else str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
                outcome.update(status='failed',error=message[:700])
                source.finish_file(job['id'],file_id,'failed',job['completed']-before_completed,False,message[:700])
            outcome['finished_at']=now()
            if outcome['status']=='failed' and len(job['document_results'])<100:job['document_results'].append(outcome)
            jobs.save(job)
        job['status']='stopped' if jobs.stop_event.is_set() else 'partial' if job['failed_documents'] and job['completed'] else 'failed' if job['failed_documents'] else 'succeeded'
        job['stage']='complete'
    except Exception as exc:
        job.update(status='failed',error=database_diagnostic(exc)['detail'] if isinstance(exc,psycopg.Error) else str(exc)[:700] if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__)
    finally:
        job['finished_at']=now();job['stop_requested']=jobs.stop_event.is_set()
        jobs.save(job)
