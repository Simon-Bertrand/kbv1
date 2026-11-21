#!/usr/bin/env python3
"""Adopte les sorties PDFParse sous une nouvelle configuration ou recalcule le PDF.

Le mode adopt conserve la préparation et les sorties d’inférence d’origine. Il
convient aux changements de paramètres dont on accepte de conserver les sorties.
Le mode recompute produit un résultat normal à partir du profil runtime enregistré.
Synchronisez et réindexez ce nouveau résultat via les outils du catalogue.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Callable

from filelock import FileLock

_LOG = logging.getLogger('pdfparse.migration')


def _fsync_directory(path: Path) -> None:
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _durable_replace(source, destination):
    os.replace(source, destination)
    for parent in {Path(source).parent, Path(destination).parent}:
        _fsync_directory(parent)


def file_sha256(path: Path) -> str:
    """Calcule le SHA-256 d'un fichier."""
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_configuration_hash(configuration: dict[str, Any]) -> str:
    """Génère le hash déterministe de configuration identique à cache_configuration()."""
    encoded = json.dumps(configuration, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def compute_cache_identity(pdf_sha256: str, configuration_hash: str) -> str:
    """Génère le nom hexadécimal à 64 caractères du sous-dossier de cache."""
    return hashlib.sha256(f'{pdf_sha256}:{configuration_hash}'.encode('utf-8')).hexdigest()


def _matches_model(identifier: Any, model: str) -> bool:
    if not isinstance(identifier, str):
        return False
    name = identifier.rsplit('/', 1)[-1].casefold()
    name = re.sub(r'[^a-z0-9]', '', name)
    model = re.sub(r'[^a-z0-9]', '', model.rsplit('/', 1)[-1].casefold())
    if not model:
        return False
    if model == 'mineru':
        return bool(re.fullmatch(r'mineru(?:[0-9][a-z0-9]*)?', name))
    # The current MinerU bundle is recorded as a registry name, Hub ID or class.
    mineru_aliases = {'mineru25pro2605', 'mineru25pro260512b', 'mineru25proocr'}
    return name == model or (name in mineru_aliases and model in mineru_aliases)


def update_model_parameter(config: dict[str, Any], *, model: str, param: str,
                           from_tokens: int | None, to_tokens: int, set_missing: bool = False) -> bool:
    """Update a numeric parameter only in descriptors of the selected model."""
    modified = False

    def update(settings, *, allow_missing=False):
        nonlocal modified
        before = settings.get(param)
        if ((allow_missing and param not in settings)
                or (type(before) is int and before != to_tokens and (from_tokens is None or before == from_tokens))):
            settings[param] = to_tokens
            modified = True

    def walk(value, *, named_model=False):
        if isinstance(value, list):
            for item in value:
                walk(item)
        if not isinstance(value, dict):
            return
        identity = value.get('model_identity')
        identifiers = [value.get(key) for key in ('name', 'model_id', 'model')]
        if isinstance(identity, dict):
            identifiers.extend((identity.get('name'), identity.get('model_id')))
            weights = identity.get('weights')
            if isinstance(weights, dict):
                identifiers.append(weights.get('repo_id'))
        elif isinstance(identity, str):
            identifiers.append(identity)
        identifiers = [name for name in identifiers if isinstance(name, str) and name]
        if identifiers:
            selected = any(_matches_model(name, model) for name in identifiers)
        else:
            implementation = value.get('class')
            selected = named_model or (isinstance(implementation, str)
                and _matches_model(implementation.rsplit('.', 1)[-1], model))
        if selected:
            update(value)
            settings = value.get('settings')
            if isinstance(settings, dict):
                update(settings, allow_missing=set_missing)
        for key, item in value.items():
            if key != 'model_identity':
                walk(item, named_model=_matches_model(key, model) or (selected and key == 'options'))

    walk(config)
    return modified


def update_mineru_max_new_tokens(config: dict[str, Any], from_tokens: int | None = None,
                                to_tokens: int = 8096) -> bool:
    return update_model_parameter(config, model='mineru', param='max_new_tokens',
                                  from_tokens=from_tokens, to_tokens=to_tokens)


def _refresh_picture_routing(before, after, routing):
    if isinstance(before, list):
        for old, new in zip(before, after):
            _refresh_picture_routing(old, new, routing)
    if not isinstance(before, dict) or before == after:
        return
    transformation = before.get('transformation_id')
    if isinstance(transformation, str) and transformation.startswith(
        ('studio.table-routing.v2:', 'studio.figure-routing.v10:')
    ) and any(before.get(role) != after.get(role) for role in ('ocr', 'vlm')):
        from pdfparse.backend.runtime import picture_routing_identity
        if routing is None:
            from pdfparse.backend.defaults import default_runtime_configuration
            routing = default_runtime_configuration().enrichment.model_dump()
        modes = ('table',) if transformation.startswith('studio.table-routing.v2:') else ('ocr', 'vlm', 'ocr_vlm', 'keep')

        def identity(descriptor, mode):
            return picture_routing_identity(mode, routing, **{
                f'{role}_{field}': (descriptor.get(role) or {}).get(key)
                for role in ('ocr', 'vlm')
                for field, key in (('identity', 'model_identity'), ('settings', 'settings'))
            })

        for mode in modes:
            if identity(before, mode) == transformation:
                after['transformation_id'] = identity(after, mode)
                break
        else:
            raise ValueError('Impossible de reconstruire l’identité du routage des figures')
    for key, value in before.items():
        _refresh_picture_routing(value, after[key], routing)


class CacheMigrationPlan:
    """Détail de la migration calculée pour un dossier de cache spécifique."""

    def __init__(self, directory: Path, pdf_path: Path, manifest: dict[str, Any],
                 new_manifest: dict[str, Any], old_key: str, new_key: str,
                 old_config_hash: str, new_config_hash: str,
                 old_cache_digest: str, new_cache_digest: str):
        self.directory = directory
        self.pdf_path = pdf_path
        self.parent = directory.parent
        self.target_directory = self.parent / new_key
        self.manifest = manifest
        self.new_manifest = new_manifest
        self.old_key = old_key
        self.new_key = new_key
        self.old_config_hash = old_config_hash
        self.new_config_hash = new_config_hash
        self.old_cache_digest = old_cache_digest
        self.new_cache_digest = new_cache_digest
        self.lock = FileLock(self.parent / '.lock')
        self.state = None
        self.database_scope: tuple[str, str] | None = None


def plan_directory_migration(directory: Path,
                            transform_fn: Callable[[dict[str, Any]], bool],
                            transform_runtime_fn: Callable[[dict[str, Any]], bool] | None = None) -> CacheMigrationPlan | None:
    """Analyse un sous-dossier de cache et génère un plan de migration si nécessaire."""
    manifest_file = directory / 'manifest.json'
    if not manifest_file.is_file():
        return None

    try:
        manifest_raw = manifest_file.read_bytes()
        old_cache_digest = hashlib.sha256(manifest_raw).hexdigest()
        manifest = json.loads(manifest_raw.decode('utf-8'))
    except Exception as exc:
        _LOG.warning('Impossible de lire le manifeste de %s: %s', directory, exc)
        return None

    if not isinstance(manifest, dict):
        _LOG.warning('Le manifeste doit être un objet JSON : %s', manifest_file)
        return None
    if manifest.get('format') != 'pdfparse.result_cache' or manifest.get('schema_version') != 2:
        return None

    old_config = manifest.get('configuration')
    if not isinstance(old_config, dict):
        return None

    old_config_hash = compute_configuration_hash(old_config)
    if manifest.get('configuration_hash', old_config_hash) != old_config_hash:
        _LOG.warning('Empreinte de configuration invalide : %s', directory)
        return None
    pdf_sha256 = manifest.get('pdf_sha256')
    if not pdf_sha256:
        return None

    old_key = directory.name
    expected_old_key = compute_cache_identity(pdf_sha256, old_config_hash)
    if old_key != expected_old_key:
        _LOG.warning('Dossier de cache incoherent avec son manifest: %s (attendu: %s)', old_key, expected_old_key)
        return None

    new_config = deepcopy(old_config)
    if not transform_fn(new_config) or new_config == old_config:
        return None

    runtime = manifest.get('runtime_configuration')
    if runtime is not None and not isinstance(runtime, dict):
        _LOG.warning('Configuration runtime invalide : %s', manifest_file)
        return None
    _refresh_picture_routing(old_config, new_config, (runtime or {}).get('enrichment'))
    new_config_hash = compute_configuration_hash(new_config)
    new_key = compute_cache_identity(pdf_sha256, new_config_hash)

    new_manifest = deepcopy(manifest)
    new_manifest['configuration'] = new_config
    new_manifest['configuration_hash'] = new_config_hash
    if runtime is not None:
        new_runtime = deepcopy(runtime)
        (transform_runtime_fn or transform_fn)(new_runtime)
        new_manifest['runtime_configuration'] = new_runtime

    raw_path = manifest.get('pdfpath', str(directory.parent.with_suffix('')))
    if not isinstance(raw_path, str) or not raw_path:
        _LOG.warning('Chemin de PDF invalide : %s', manifest_file)
        return None
    pdf_path = Path(raw_path)

    # Digest provisoire pour le plan
    encoded_manifest = json.dumps(new_manifest, indent=2, ensure_ascii=False).encode('utf-8')
    provisional_digest = hashlib.sha256(encoded_manifest).hexdigest()

    return CacheMigrationPlan(
        directory=directory, pdf_path=pdf_path, manifest=manifest,
        new_manifest=new_manifest, old_key=old_key, new_key=new_key,
        old_config_hash=old_config_hash, new_config_hash=new_config_hash,
        old_cache_digest=old_cache_digest, new_cache_digest=provisional_digest,
    )


def _relocate(value: Any, old_key: str, new_key: str) -> Any:
    """Rewrite asset locators, never source text or the original preparation."""
    if isinstance(value, list):
        return [_relocate(item, old_key, new_key) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in ('assets_dir', 'upstream_assets_dir') and isinstance(item, str):
            parts = Path(item).parts
            result[key] = str(Path(*(new_key if part == old_key else part for part in parts))) if old_key in parts else item
        else:
            result[key] = _relocate(item, old_key, new_key)
    if result != value and 'record_hash' in result:
        result['record_hash'] = compute_configuration_hash({k: v for k, v in result.items() if k != 'record_hash'})
    return result


def _write_json_atomic(path: Path, value: Any) -> None:
    handle, temporary = tempfile.mkstemp(prefix='.migration-write-', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        _durable_replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _journal(plan: CacheMigrationPlan, state: str) -> None:
    _write_json_atomic(plan.parent / ('.migration-'+plan.old_key+'.json'), {
        'schema_version':1, 'state':state, 'old_key':plan.old_key, 'new_key':plan.new_key,
        'old_manifest':plan.manifest, 'new_manifest':plan.new_manifest,
        'old_cache_digest':plan.old_cache_digest, 'new_cache_digest':plan.new_cache_digest,
        'pdf_path':str(plan.pdf_path), 'database_scope':plan.database_scope,
        'database_reconciled':plan.database_scope is not None and state in ('database_committed', 'complete'),
    })
    plan.state = state


def _read_journal(path: Path) -> CacheMigrationPlan:
    if path.is_symlink():
        raise ValueError('Journal lié refusé')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('Version de journal inconnue')
    state = value['state']
    if state not in ('prepared', 'filesystem_ready', 'database_committed', 'complete'):
        raise ValueError(f'État de journal inconnu : {state}')
    old_key, new_key = value['old_key'], value['new_key']
    if (not all(re.fullmatch('[a-f0-9]{64}', key) for key in (old_key, new_key))
            or old_key == new_key or path.name != f'.migration-{old_key}.json'):
        raise ValueError('Clé de reprise invalide')
    manifest, new_manifest = value['old_manifest'], value['new_manifest']
    plan = CacheMigrationPlan(path.parent / old_key, Path(value['pdf_path']), manifest, new_manifest,
        old_key, new_key, manifest['configuration_hash'], new_manifest['configuration_hash'],
        value['old_cache_digest'], value['new_cache_digest'])
    plan.state = state
    if scope := value.get('database_scope'):
        plan.database_scope = tuple(scope)
    return plan


def _verify_files(directory: Path, manifest: dict) -> None:
    root = directory.resolve()
    for section in ('files', 'assets'):
        for name, expected in manifest.get(section, {}).items():
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Chemin de cache hors répertoire')
            path = directory / ('assets' if section == 'assets' else '') / relative
            if (path.is_symlink() or not path.resolve().is_relative_to(root)
                    or not path.is_file() or file_sha256(path) != expected):
                raise ValueError(f'Cache invalide : {name}')


def _verify_pdf(plan: CacheMigrationPlan) -> None:
    paths = (plan.pdf_path, plan.parent.with_suffix(''))
    if not any(not path.is_symlink() and path.is_file() and file_sha256(path) == plan.manifest['pdf_sha256']
               for path in dict.fromkeys(paths)):
        raise ValueError('Le PDF ne correspond plus au cache à migrer')


def execute_filesystem_migration(plan: CacheMigrationPlan, *, retain_original: bool = False) -> str:
    """Publish a validated copy; retain the original until SQL has committed."""
    with plan.lock:
        if plan.directory.is_symlink() or plan.target_directory.is_symlink():
            raise ValueError('Liens symboliques interdits dans le cache à migrer')
        _verify_pdf(plan)
        if plan.target_directory.exists():
            if file_sha256(plan.target_directory/'manifest.json') != plan.new_cache_digest:
                raise FileExistsError(f'Cache cible différent : {plan.target_directory}')
            _verify_files(plan.target_directory, plan.new_manifest)
            if plan.state == 'prepared':
                _journal(plan, 'filesystem_ready')
            if not retain_original and plan.state != 'complete':
                _archive_original(plan)
            return plan.new_cache_digest
        if plan.state in ('database_committed', 'complete'):
            raise FileNotFoundError(f'Cache cible déjà publié introuvable : {plan.target_directory}')
        if file_sha256(plan.directory/'manifest.json') != plan.old_cache_digest:
            raise ValueError('Le manifeste a changé depuis la planification')
        if any(p.is_symlink() for p in plan.directory.rglob('*')):
            raise ValueError('Liens symboliques interdits dans le cache à migrer')
        _verify_files(plan.directory, plan.manifest)
        required=sum(p.stat().st_size for p in plan.directory.rglob('*') if p.is_file())
        if shutil.disk_usage(plan.parent).free < required + 16*1024*1024:
            raise OSError('Espace insuffisant pour conserver original et préparation migrée')
        stage_prefix = f'.migration-stage-{plan.old_key}-'
        for unfinished in plan.parent.glob(f'{stage_prefix}*'):
            if not unfinished.is_symlink() and unfinished.is_dir():
                shutil.rmtree(unfinished)
        staged = Path(tempfile.mkdtemp(prefix=stage_prefix, dir=plan.parent))
        try:
            shutil.copytree(plan.directory, staged, dirs_exist_ok=True)
            # Original inference parameters and manifest_hash remain evidence
            # of the actual execution, rather than claiming a new inference.
            for name in plan.manifest.get('files', {}):
                path = staged/name
                if name != 'preparation.json' and path.suffix in ('.json', '.jsonl'):
                    if path.suffix == '.jsonl':
                        rows = [_relocate(json.loads(line), plan.old_key, plan.new_key)
                                for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
                        path.write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n' for row in rows),
                                        encoding='utf-8')
                    else:
                        data = json.loads(path.read_text(encoding='utf-8'))
                        path.write_text(json.dumps(_relocate(data, plan.old_key, plan.new_key),
                                                   ensure_ascii=False, allow_nan=False), encoding='utf-8')
                plan.new_manifest['files'][name] = file_sha256(path)
            plan.new_manifest['cache_migration'] = {
                'schema_version':1, 'from_key':plan.old_key,
                'from_manifest_digest':plan.old_cache_digest,
                'inference_reexecuted':False,
            }
            _write_json_atomic(staged/'manifest.json', plan.new_manifest)
            _verify_files(staged, plan.new_manifest)
            plan.new_cache_digest = file_sha256(staged/'manifest.json')
            if os.name == 'posix':
                for directory, _, files in os.walk(staged, topdown=False):
                    for name in files:
                        with (Path(directory) / name).open('rb') as stream:
                            os.fsync(stream.fileno())
                    _fsync_directory(Path(directory))
            # The durable journal lets a new process finish SQL after a crash.
            _journal(plan, 'prepared')
            _durable_replace(staged, plan.target_directory)
            _journal(plan, 'filesystem_ready')
            if not retain_original:
                _archive_original(plan)
            return plan.new_cache_digest
        finally:
            if staged.exists():
                shutil.rmtree(staged)


def _archive_original(plan: CacheMigrationPlan) -> None:
    if plan.database_scope and plan.state not in ('database_committed', 'complete'):
        raise RuntimeError('Archivage impossible avant la validation PostgreSQL')
    backup = plan.parent / ('.migration-original-'+plan.old_key)
    if plan.directory.exists():
        if file_sha256(plan.directory/'manifest.json') != plan.old_cache_digest:
            raise ValueError('Le cache original a changé ; sauvegarde et journal conservés')
        if backup.exists():
            raise FileExistsError(f'Sauvegarde déjà présente : {backup}')
        _durable_replace(plan.directory, backup)
    _journal(plan, 'complete')


def _lock_database(cursor, corpus_root: str) -> None:
    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('km-embedding-writer',0))")
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('km-corpus-sync:'+corpus_root,))


@contextmanager
def _publication_lock(connection, corpus_root: str):
    """Exclude catalogue scans and indexing until the old directory is retired."""
    with connection.cursor() as cursor:
        writer = 'km-embedding-writer'
        corpus = 'km-corpus-sync:' + corpus_root
        lock = 'SELECT pg_advisory_lock(hashtextextended(%s,0))'
        unlock = 'SELECT pg_advisory_unlock(hashtextextended(%s,0))'
        cursor.execute(lock, (writer,))
        try:
            cursor.execute(lock, (corpus,))
            try:
                yield
            finally:
                cursor.execute(unlock, (corpus,))
        finally:
            cursor.execute(unlock, (writer,))


def execute_database_migration(connection: Any, plan: CacheMigrationPlan, corpus_root: str, relative_path: str):
    """Migrate precisely one filesystem occurrence and only its publications."""
    old_path = str(Path(relative_path).with_name(plan.old_key))
    new_path = str(Path(relative_path).with_name(plan.new_key))
    with connection.transaction():
        cursor = connection.cursor()
        _lock_database(cursor, corpus_root)
        cursor.execute('''SELECT document_id, state, validation_state, validation_error,
                          km_document_id, chunk_count, verified_at, present, last_seen_sync, cache_digest, error
                          FROM rag.km_corpus_caches
                          WHERE corpus_root=%s AND cache_key=%s AND path=%s FOR UPDATE''',
                       (corpus_root,plan.old_key,old_path))
        rows = cursor.fetchall()
        if not rows:
            cursor.execute('''SELECT cache_digest FROM rag.km_corpus_caches
                              WHERE corpus_root=%s AND cache_key=%s AND path=%s FOR UPDATE''',
                           (corpus_root, plan.new_key, new_path))
            target = cursor.fetchall()
            if len(target) == 1 and (target[0]['cache_digest'] if isinstance(target[0], dict)
                                     else target[0][0]) == plan.new_cache_digest:
                return  # Replay after a committed SQL transaction.
            raise RuntimeError('Occurrence source absente et cible non vérifiable dans le catalogue')
        if len(rows) != 1:
            raise RuntimeError('Occurrence de cache ambiguë ; aucune migration SQL effectuée')
        row = rows[0]
        if isinstance(row, dict):
            row = tuple(row[k] for k in ('document_id','state','validation_state','validation_error',
                        'km_document_id','chunk_count','verified_at','present','last_seen_sync','cache_digest','error'))
        file_id, state, validation, validation_error, document_id, count, verified, present, sync, digest, error = row
        if digest is not None and digest != plan.old_cache_digest:
            raise RuntimeError('Le cache source a changé dans le catalogue')
        cursor.execute('''SELECT document_id, path, cache_digest FROM rag.km_corpus_caches
                          WHERE corpus_root=%s AND cache_key=%s AND (document_id=%s OR path=%s)
                          FOR UPDATE''', (corpus_root, plan.new_key, file_id, new_path))
        targets = cursor.fetchall()
        expected_target = (file_id, new_path, plan.new_cache_digest)
        targets = [(row['document_id'], row['path'], row['cache_digest']) if isinstance(row, dict) else row
                   for row in targets]
        if targets and targets != [expected_target]:
            raise FileExistsError(f'Cache cible déjà présent dans le catalogue : {new_path}')
        if not targets:
            cursor.execute('''INSERT INTO rag.km_corpus_caches(
                corpus_root,document_id,cache_key,path,state,manifest,validation_state,validation_error,error,
                cache_digest,km_document_id,chunk_count,verified_at,present,last_seen_sync,observed_at)
                VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())''',
                (corpus_root,file_id,plan.new_key,new_path,state,json.dumps(plan.new_manifest),validation,validation_error,error,
                 plan.new_cache_digest,document_id,count,verified,present,sync))
            cursor.execute('''UPDATE rag.km_cache_chunks SET cache_key=%s,cache_digest=%s
                              WHERE corpus_root=%s AND file_id=%s AND cache_key=%s AND cache_digest=%s''',
                           (plan.new_key, plan.new_cache_digest, corpus_root, file_id, plan.old_key, plan.old_cache_digest))
        cursor.execute('DELETE FROM rag.km_cache_chunks WHERE corpus_root=%s AND file_id=%s AND cache_key=%s',
                       (corpus_root, file_id, plan.old_key))
        for table in ('km_profile_cache_choices','km_chunk_job_files'):
            extra = ',cache_digest=%s' if table == 'km_chunk_job_files' else ''
            values = (plan.new_key,plan.new_cache_digest) if extra else (plan.new_key,)
            guard = ' AND cache_digest=%s' if extra else ''
            expected = (plan.old_cache_digest,) if extra else ()
            cursor.execute(f'UPDATE rag.{table} SET cache_key=%s{extra} WHERE corpus_root=%s AND file_id=%s AND cache_key=%s{guard}',
                           (*values, corpus_root, file_id, plan.old_key, *expected))
        # Relocate only records belonging to the selected file occurrence.
        if not targets:
            cursor.execute('SELECT chunk_id,record FROM rag.km_cache_chunks WHERE corpus_root=%s AND file_id=%s AND cache_key=%s',
                           (corpus_root,file_id,plan.new_key))
            for ident, record in cursor.fetchall():
                relocated = _relocate(record,plan.old_key,plan.new_key)
                if relocated != record:
                    cursor.execute('UPDATE rag.km_cache_chunks SET record=%s::jsonb WHERE corpus_root=%s AND file_id=%s AND cache_key=%s AND chunk_id=%s',
                                   (json.dumps(relocated),corpus_root,file_id,plan.new_key,ident))
        scope = (document_id, plan.old_key, old_path, plan.old_cache_digest)
        cursor.execute('''SELECT u.profile,u.unit_id,u.record FROM rag.km_profile_units u
                          JOIN rag.km_profile_documents d USING(profile,document_id)
                          WHERE d.document_id=%s AND d.metadata->>'pdfparse_cache_key'=%s
                            AND d.metadata->>'pdfparse_cache_path'=%s AND d.metadata->>'pdfparse_cache_digest'=%s''', scope)
        for profile,ident,record in cursor.fetchall():
            relocated = _relocate(record,plan.old_key,plan.new_key)
            if relocated != record:
                cursor.execute('UPDATE rag.km_profile_units SET record=%s::jsonb WHERE profile=%s AND unit_id=%s AND document_id=%s',
                               (json.dumps(relocated),profile,ident,document_id))
        for table,key in (('chunks','chunk_id'),('visuals','visual_id')):
            cursor.execute(f'''SELECT u.{key},u.record FROM rag.{table} u JOIN rag.documents d USING(document_id)
                WHERE d.document_id=%s AND d.metadata->>'pdfparse_cache_key'=%s
                  AND d.metadata->>'pdfparse_cache_path'=%s AND d.metadata->>'pdfparse_cache_digest'=%s''', scope)
            for ident,record in cursor.fetchall():
                relocated = _relocate(record,plan.old_key,plan.new_key)
                if relocated != record:
                    cursor.execute(f'UPDATE rag.{table} SET record=%s::jsonb WHERE {key}=%s AND document_id=%s',
                                   (json.dumps(relocated),ident,document_id))
        metadata = json.dumps({'pdfparse_cache_key':plan.new_key,'pdfparse_cache_path':new_path,
            'pdfparse_cache_digest':plan.new_cache_digest,'pdfparse_configuration_hash':plan.new_config_hash,
            'pdfparse_configuration':plan.new_manifest['configuration'],
            'pdfparse_runtime_configuration':plan.new_manifest.get('runtime_configuration'),
            'pdfparse_cache_migration':plan.new_manifest.get('cache_migration'),
            'pdfparse_exports':plan.new_manifest['files']})
        for table in ('km_profile_documents','documents'):
            cursor.execute(f'''UPDATE rag.{table} SET metadata=metadata || %s::jsonb ||
                CASE WHEN right(metadata->>'pdfparse_cache_dir',65) = '/' || %s
                    THEN jsonb_build_object('pdfparse_cache_dir', left(metadata->>'pdfparse_cache_dir',-64) || %s)
                    ELSE '{{}}'::jsonb END
                WHERE document_id=%s AND metadata->>'pdfparse_cache_key'=%s
                  AND metadata->>'pdfparse_cache_path'=%s AND metadata->>'pdfparse_cache_digest'=%s''',
                (metadata, plan.old_key, plan.new_key, *scope))
        cursor.execute('DELETE FROM rag.km_corpus_caches WHERE corpus_root=%s AND document_id=%s AND cache_key=%s AND path=%s',
                       (corpus_root,file_id,plan.old_key,old_path))


def execute_recomputation(plan: CacheMigrationPlan) -> None:
    """Run the configured pipeline and let the catalogue index the new output normally."""
    from pdfparse import ModelRegistry
    from pdfparse.backend.defaults import default_runtime_configuration
    from pdfparse.backend.runtime import build_pipeline, validate_selection
    from pdfparse.backend.schemas import RuntimeConfiguration
    from pdfparse.ingestion.cache import cache_parent
    from pdfparse.ingestion.preparation import cache_configuration

    if plan.pdf_path.is_symlink() or file_sha256(plan.pdf_path) != plan.manifest['pdf_sha256']:
        raise ValueError('Le PDF ne correspond plus au cache à recalculer')
    if cache_parent(plan.pdf_path) != plan.parent:
        raise ValueError('Configurer PDFPARSE_CACHE_DIR pour retrouver le cache source')
    runtime = deepcopy(plan.new_manifest.get('runtime_configuration'))
    if runtime is None:
        runtime = default_runtime_configuration().model_dump(mode='json')
    selection_data = deepcopy(runtime)
    tokenizer_id = selection_data.get('chunking', {}).pop('tokenizer_id', None)
    options = {}
    if tokenizer_id:
        from pdfparse.cli import e5_tokenizer_directory
        from pdfparse.ingestion.e5_chunking import build_e5_chunker
        tokenizer = e5_tokenizer_directory()
        if tokenizer is None:
            raise ValueError('Tokenizer E5 introuvable pour le recalcul')
        chunker = build_e5_chunker(tokenizer, selection_data['chunking']['chunk_size'])
        if chunker.tokenizer_id != tokenizer_id:
            raise ValueError('Le tokenizer E5 ne correspond pas au profil enregistré')
        options['chunking'] = chunker
    selection = RuntimeConfiguration.model_validate(selection_data)
    models = ModelRegistry()
    validate_selection(models, selection)
    pipeline = build_pipeline(models, selection, **options)
    if cache_configuration(pipeline)[1] != plan.new_manifest['configuration']:
        raise ValueError('Le pipeline local ne reproduit pas la nouvelle configuration prévue')
    if plan.target_directory.exists():
        manifest = json.loads((plan.target_directory / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('cache_migration', {}).get('inference_reexecuted') is False:
            raise FileExistsError(f'Cache cible déjà adopté : {plan.target_directory}')
    pipeline._runtime_configuration = runtime
    # Leave room for the new result alongside all existing entries.
    entries = [path for path in plan.parent.iterdir() if re.fullmatch('[a-f0-9]{64}', path.name)]
    size = sum(path.stat().st_size for entry in entries for path in entry.rglob('*') if path.is_file())
    pipeline.configuration = pipeline.configuration.model_copy(update={
        'cache_max_entries': len(entries) + pipeline.configuration.cache_max_entries,
        'cache_max_bytes': size + pipeline.configuration.cache_max_bytes,
    })
    pipeline.run(plan.pdf_path)
    _verify_files(plan.target_directory, json.loads((plan.target_directory / 'manifest.json').read_text(encoding='utf-8')))


def _logical_cache_path(plan, root, upstream_root, *, external):
    if not external:
        return plan.target_directory.relative_to(root).as_posix()
    original = plan.pdf_path
    if not original.is_absolute() or '..' in original.parts or original.suffix.lower() != '.pdf':
        raise ValueError(f'Chemin de PDF externe invalide : {original}')
    expected_parent = hashlib.sha256(original.as_posix().encode()).hexdigest() + '.pdfparsecache'
    if plan.parent.name != expected_parent:
        raise ValueError(f'Cache externe incohérent avec le PDF : {plan.parent}')
    relative = original.relative_to(root if original.is_relative_to(root) else upstream_root)
    if not relative.parts:
        raise ValueError(f'Chemin de PDF externe vide : {original}')
    plan.pdf_path = root / relative
    if plan.pdf_path.resolve() != plan.pdf_path:
        raise ValueError(f'Lien symbolique dans le chemin du PDF : {plan.pdf_path}')
    return (relative.parent / (relative.name + '.pdfparsecache') / plan.new_key).as_posix()


def migrate_cache_corpus(root: Path,
                         transform_fn: Callable[[dict[str, Any]], bool],
                         dsn: str | None = None,
                         dry_run: bool = False,
                         transform_runtime_fn: Callable[[dict[str, Any]], bool] | None = None,
                         *, corpus_root: str | None = None,
                         mode: str = 'adopt', upstream_root: str = '/data') -> list[CacheMigrationPlan]:
    """Planifie les caches par PDF, puis exécute l’opération choisie."""
    if mode not in ('adopt', 'recompute'):
        raise ValueError(f'Mode de migration inconnu : {mode}')

    plans: list[CacheMigrationPlan] = []
    root = root.resolve()
    _LOG.info('Recherche des dossiers .pdfparsecache dans %s...', root)
    configured_cache = os.environ.get('PDFPARSE_CACHE_DIR')
    external_root = Path(configured_cache).expanduser().resolve() if configured_cache else None
    external_parents = set(external_root.glob('*.pdfparsecache')) if external_root else set()
    parents = sorted(set(root.rglob('*.pdfparsecache')) | external_parents)
    for parent in parents:
        if not parent.is_dir() or parent.is_symlink():
            continue
        recovering = set()
        for journal in sorted(parent.glob('.migration-*.json')):
            plan = _read_journal(journal)
            if parent in external_parents:
                plan.pdf_path = Path(plan.manifest['pdfpath'])
            if plan.state != 'complete':
                recovering.update((plan.old_key, plan.new_key))
                if mode == 'adopt':
                    plans.append(plan)
        for child in sorted(parent.iterdir()):
            if child.is_dir() and not child.is_symlink() and len(child.name) == 64 and child.name not in recovering:
                plan = plan_directory_migration(child, transform_fn, transform_runtime_fn)
                if plan:
                    plans.append(plan)

    logical_paths = {
        (plan.parent, plan.old_key): _logical_cache_path(
            plan, root, Path(upstream_root), external=plan.parent in external_parents,
        )
        for plan in plans
    }
    _LOG.info('%d cache(s) identifié(s) pour le mode %s.', len(plans), mode)
    if dry_run:
        for plan in plans:
            _LOG.info('[DRY RUN] Migration : %s -> %s (nouveau hash : %s)',
                      plan.old_key[:12], plan.new_key[:12], plan.new_config_hash[:12])
        return plans

    if mode == 'recompute':
        if dsn:
            _LOG.info('Recalcul local. Synchronisez et réindexez les nouveaux résultats via le catalogue.')
        for plan in plans:
            execute_recomputation(plan)
        return plans

    db_conn = None
    if dsn and plans:
        import psycopg
        db_conn = psycopg.connect(dsn, autocommit=True)
        _LOG.info('Connexion PostgreSQL établie pour la synchronisation de rag.km_*.')

    try:
        for index, plan in enumerate(plans):
            with plan.lock:
                journal = plan.parent / f'.migration-{plan.old_key}.json'
                if journal.exists() or journal.is_symlink():
                    pending = _read_journal(journal)
                    if pending.new_key != plan.new_key:
                        raise RuntimeError('Une migration vers une autre cible est déjà engagée')
                    pending.lock = plan.lock
                    if plan.parent in external_parents:
                        pending.pdf_path = plan.pdf_path
                    plan = plans[index] = pending
                if plan.state == 'complete':
                    continue
                _LOG.info('Migration du cache : %s -> %s (%s)', plan.old_key[:12], plan.new_key[:12], plan.pdf_path.name)
                relative_path = logical_paths[plan.parent, plan.old_key]
                scope = (corpus_root or str(root), relative_path)
                if plan.database_scope is not None:
                    if corpus_root is not None and scope != plan.database_scope:
                        raise ValueError('Le corpus PostgreSQL diffère du journal de migration')
                    if not db_conn and plan.state != 'database_committed':
                        raise RuntimeError('Un DSN est requis pour reprendre cette migration PostgreSQL')
                    scope = plan.database_scope
                if db_conn:
                    plan.database_scope = scope
                    with _publication_lock(db_conn, scope[0]):
                        execute_filesystem_migration(plan, retain_original=True)
                        if plan.state != 'database_committed':
                            _journal(plan, 'filesystem_ready')
                        execute_database_migration(db_conn, plan, *scope)
                        if plan.state != 'database_committed':
                            _journal(plan, 'database_committed')
                        _archive_original(plan)
                else:
                    execute_filesystem_migration(plan, retain_original=True)
                    _archive_original(plan)
    finally:
        if db_conn:
            db_conn.close()
    return plans


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    parser = argparse.ArgumentParser(
        description='Adoption ou recalcul de caches PDFParse.',
        epilog='Le mode adopt conserve les sorties et leur provenance. Le mode recompute lance PDFParse '
               'avec le nouveau profil. Synchronisez puis réindexez ses résultats via le catalogue.',
    )
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('DATA_DIR', '.')),
                        help='Racine locale des caches (DATA_DIR ou . par défaut)')
    parser.add_argument('--upstream-root', default='/data', help='Racine logique des PDF inscrite dans les caches externes')
    parser.add_argument('--corpus-root', help='Identifiant corpus_root dans PostgreSQL (racine locale par défaut)')
    parser.add_argument('--model', default='mineru', help='Nom de registre ou alias du modèle (mineru par défaut)')
    parser.add_argument('--param', default='max_new_tokens', help='Paramètre numérique à modifier (max_new_tokens par défaut)')
    parser.add_argument('--from-tokens', type=int, help='Valeur actuelle à cibler (facultatif)')
    parser.add_argument('--to-tokens', type=int, default=8096, help='Nouvelle valeur (%(default)s par défaut)')
    parser.add_argument('--update-default-profile', action='store_true',
                        help='Utilise la valeur du paramètre dans le profil runtime par défaut')
    parser.add_argument('--mode', choices=('adopt', 'recompute'), default='adopt',
                        help='Conserver les sorties (adopt, par défaut) ou exécuter le pipeline (recompute)')
    parser.add_argument('--dsn', default=os.environ.get('DATABASE_URL'),
                        help='Connexion PostgreSQL pour adopt (DATABASE_URL par défaut)')
    parser.add_argument('--no-db', action='store_true', help='Adoption sans mise à jour PostgreSQL')
    parser.add_argument('--dry-run', action='store_true', help='Simule la migration sans modifier les caches')
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f'Répertoire racine inexistant : {args.root}')

    target = args.to_tokens
    if args.update_default_profile:
        from pdfparse.backend.defaults import default_runtime_configuration
        defaults = default_runtime_configuration()
        values = {selection.settings.get(args.param) for selection in (
            defaults.ocr, defaults.layout, defaults.picture_classifier, defaults.vlm,
        ) if selection and _matches_model(selection.name, args.model)}
        if len(values) != 1 or type(next(iter(values))) is not int:
            parser.error('Paramètre de modèle numérique absent ou ambigu dans le profil par défaut')
        target = values.pop()
    if args.param == 'max_new_tokens' and target < 1:
        parser.error('max_new_tokens doit être positif')

    def transform(config, *, set_missing=False):
        return update_model_parameter(config, model=args.model, param=args.param,
                                      from_tokens=args.from_tokens, to_tokens=target, set_missing=set_missing)

    migrate_cache_corpus(
        root=args.root, transform_fn=transform,
        transform_runtime_fn=lambda config: transform(config, set_missing=True),
        dsn=None if args.no_db else args.dsn, dry_run=args.dry_run,
        corpus_root=args.corpus_root, mode=args.mode, upstream_root=args.upstream_root,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
