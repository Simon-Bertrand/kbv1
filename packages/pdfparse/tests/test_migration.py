"""Tests de validation du script de migration de cache pdfparse."""
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
# Importer les primitives de migration
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from migrate_cache import (
    plan_directory_migration,
    execute_filesystem_migration,
    execute_database_migration,
    update_mineru_max_new_tokens,
    file_sha256,
    compute_cache_identity,
    compute_configuration_hash,
    main,
    migrate_cache_corpus,
)


def dummy_pipeline(tokens):
    from docling_core.types.doc import DocItemLabel, DoclingDocument, ImageRef, ProvenanceItem, BoundingBox
    from PIL import Image
    from pdfparse import PdfParseConfiguration, PdfParsePipeline
    from pdfparse.ingestion import BasePdfExtractor

    class DummyExtractor(BasePdfExtractor):
        def __init__(self):
            self.calls = 0

        def extract(self, pdfpath):
            self.calls += 1
            doc = DoclingDocument(name='migration-fixture')
            doc.add_text(label=DocItemLabel.TEXT, text='Texte initial de test pour migration',
                         prov=ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=10, b=10), charspan=(0, 36)))
            doc.add_picture(image=ImageRef.from_pil(Image.new('RGB', (8, 8), 'red'), dpi=72))
            return doc

    identity = {'ocr': {'name': 'mineru2_5_pro_2605',
                        'settings': {'max_new_tokens': tokens, 'batch_size': 1}}}
    return PdfParsePipeline(PdfParseConfiguration(cache_identity=identity), extractor=DummyExtractor())


def test_migration_updates_filesystem_and_retains_cache_validity(tmp_path):
    # 1. Créer un cache réel avec un profil contenant max_new_tokens = 2048
    pdf_file = tmp_path / 'document.pdf'
    pdf_file.write_bytes(b'PDF sample for migration test')

    from pdfparse.ingestion.cache import ResultCache
    from pdfparse.ingestion.preparation import cache_configuration

    pipeline_v1 = dummy_pipeline(2048)
    assert pipeline_v1.run(pdf_file) is not None
    assert pipeline_v1.extractor.calls == 1

    parent_cache_dir = pdf_file.with_name(pdf_file.name + '.pdfparsecache')
    assert parent_cache_dir.is_dir()

    old_key, _ = cache_configuration(pipeline_v1)
    pdf_sha = hashlib.sha256(pdf_file.read_bytes()).hexdigest()
    expected_old_dir_name = hashlib.sha256(f'{pdf_sha}:{old_key}'.encode()).hexdigest()
    old_cache_dir = parent_cache_dir / expected_old_dir_name
    assert old_cache_dir.is_dir()
    original_preparation = json.loads((old_cache_dir/'preparation.json').read_text())

    # 2. Planifier la migration vers 4096 tokens
    def transform(config):
        return update_mineru_max_new_tokens(config, from_tokens=2048, to_tokens=4096)

    plan = plan_directory_migration(old_cache_dir, transform)
    assert plan is not None
    assert plan.old_key == expected_old_dir_name
    assert plan.old_key != plan.new_key
    assert plan.old_config_hash != plan.new_config_hash

    # 3. Exécuter la migration filesystem
    final_digest = execute_filesystem_migration(plan)
    assert final_digest is not None

    # L'ancien dossier n'existe plus et le nouveau est présent
    assert not old_cache_dir.exists()
    new_cache_dir = parent_cache_dir / plan.new_key
    assert new_cache_dir.is_dir()
    assert json.loads((new_cache_dir/'preparation.json').read_text()) == original_preparation
    assert json.loads((new_cache_dir/'manifest.json').read_text())['cache_migration']['inference_reexecuted'] is False

    # 4. Vérifier que tous les checksums du manifest sont parfaitement valides
    manifest_data = json.loads((new_cache_dir / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest_data['configuration_hash'] == plan.new_config_hash
    for filename, digest in manifest_data['files'].items():
        file_path = new_cache_dir / filename
        assert file_path.is_file(), f'Fichier manquant: {filename}'
        assert file_sha256(file_path) == digest, f'Checksum erroné pour {filename}'

    # Vérifier que document.json a bien son assets_dir mis à jour
    doc_json = json.loads((new_cache_dir / 'document.json').read_text(encoding='utf-8'))
    assert plan.new_key in doc_json['assets_dir']
    assert plan.old_key not in doc_json['assets_dir']
    for export in ('index.jsonl', 'visuals.jsonl'):
        for line in (new_cache_dir / export).read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            assert plan.new_key in record['assets_dir']
            assert record.pop('record_hash') == compute_configuration_hash(record)
            if export == 'index.jsonl':
                assert record['preparation_hash'] == original_preparation['manifest_hash']

    # 5. Charger le cache avec un pipeline configuré à 4096 tokens SANS appel à l'extracteur
    pipeline_v2 = dummy_pipeline(4096)

    # Vérifier que le hash de configuration de pipeline_v2 correspond au nouveau hash
    new_key_pipeline, _ = cache_configuration(pipeline_v2)
    assert new_key_pipeline == plan.new_config_hash

    # Charger le cache : il doit être accepté et valide !
    cached_entry = ResultCache(pipeline_v2, pdf_file)
    cached_entry.pdf_sha256 = pdf_sha
    cached_entry.directory = new_cache_dir
    loaded_result = cached_entry.load(verify_configuration=True)

    assert loaded_result is not None
    assert loaded_result.document.document_id == pdf_sha
    assert len(loaded_result.chunks) > 0
    # Aucune inférence n'a été réexécutée
    assert pipeline_v2.extractor.calls == 0


def test_database_migration_transaction_order():
    """Valide que la transaction SQL respecte les contraintes et clés étrangères."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.transaction.return_value.__enter__ = MagicMock()
    mock_conn.transaction.return_value.__exit__ = MagicMock()

    mock_cursor.fetchall.return_value = [
        ('doc-1', 'observed', 'valid', None, 'km-doc-1', 42, '2026-11-14T10:00:00Z', True, 'sync-1', 'digest_old', None)
    ]
    parent_rows = mock_cursor.fetchall.return_value
    mock_cursor.fetchall.side_effect = lambda: parent_rows if 'SELECT document_id, state' in mock_cursor.execute.call_args.args[0] else []

    mock_plan = MagicMock()
    mock_plan.old_key = 'old_key_123'
    mock_plan.new_key = 'new_key_456'
    mock_plan.old_cache_digest = 'digest_old'
    mock_plan.new_cache_digest = 'digest_new'
    mock_plan.new_config_hash = 'config_hash_new'
    mock_plan.new_manifest = {'configuration_hash': 'config_hash_new', 'configuration':{}, 'files':{}}

    execute_database_migration(mock_conn, mock_plan, corpus_root='/data', relative_path='doc.pdf.pdfparsecache/old_key_123')

    # Vérifier que les requêtes ont été passées dans le bon ordre :
    # 1. SELECT pour trouver les lignes parentes
    # 2. INSERT pour créer la nouvelle ligne parente km_corpus_caches
    # 3. UPDATE sur km_cache_chunks
    # 4. UPDATE sur km_profile_cache_choices
    # 5. UPDATE sur km_chunk_job_files
    # 6. UPDATE sur km_profile_documents (metadata JSONB)
    # 7. DELETE sur l'ancienne ligne parente km_corpus_caches
    calls = [call[0][0].strip() for call in mock_cursor.execute.call_args_list]

    assert any('SELECT' in c and 'FROM rag.km_corpus_caches' in c for c in calls)
    insert = next(i for i, sql in enumerate(calls) if 'INSERT INTO rag.km_corpus_caches' in sql)
    update = next(i for i, sql in enumerate(calls) if 'UPDATE rag.km_cache_chunks' in sql)
    delete = next(i for i, sql in enumerate(calls) if 'DELETE FROM rag.km_corpus_caches' in sql)
    assert insert < update < delete
    assert 'ON CONFLICT' not in calls[insert]
    assert any('UPDATE rag.km_cache_chunks' in c for c in calls)
    assert any('UPDATE rag.km_profile_cache_choices' in c for c in calls)
    assert any('UPDATE rag.km_chunk_job_files' in c for c in calls)
    assert any('UPDATE rag.km_profile_documents' in c for c in calls)
    assert any('DELETE FROM rag.km_corpus_caches' in c for c in calls)


def make_plan(tmp_path):
    pdf = tmp_path / 'a.PDF'
    pdf.write_bytes(b'PDF sample')
    config = {'ocr': {'name': 'mineru', 'settings': {'max_new_tokens': 2048}}}
    config_hash = compute_configuration_hash(config)
    pdf_hash = file_sha256(pdf)
    directory = tmp_path / 'a.PDF.pdfparsecache' / compute_cache_identity(pdf_hash, config_hash)
    directory.mkdir(parents=True)
    document = directory / 'document.json'
    document.write_text(json.dumps({'assets_dir': str(directory / 'assets')}), encoding='utf-8')
    manifest = {
        'format': 'pdfparse.result_cache', 'schema_version': 2,
        'configuration': config, 'configuration_hash': config_hash,
        'pdfpath': str(pdf), 'pdf_sha256': pdf_hash,
        'files': {'document.json': file_sha256(document)}, 'assets': {},
    }
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return plan_directory_migration(directory, update_mineru_max_new_tokens)


def test_migration_failure_keeps_original_bytes(tmp_path,monkeypatch):
    import migrate_cache as module
    plan=make_plan(tmp_path)
    original={p.relative_to(plan.directory):p.read_bytes() for p in plan.directory.rglob('*') if p.is_file()}
    real=module._write_json_atomic
    def fail(path,value):
        if path.name=='manifest.json':raise OSError('disk full')
        return real(path,value)
    monkeypatch.setattr(module,'_write_json_atomic',fail)
    with pytest.raises(OSError): execute_filesystem_migration(plan,retain_original=True)
    assert not plan.target_directory.exists()
    assert {p.relative_to(plan.directory):p.read_bytes() for p in plan.directory.rglob('*') if p.is_file()}==original
    assert not list(plan.parent.glob('.migration-stage-*'))


def test_prepared_migration_survives_restart_before_sql(tmp_path):
    import migrate_cache as module
    plan=make_plan(tmp_path)
    original=(plan.directory/'manifest.json').read_bytes()
    digest=execute_filesystem_migration(plan,retain_original=True)
    assert (plan.directory/'manifest.json').read_bytes()==original
    # Dry-run reconstructs the exact durable plan, without generating a second migration.
    resumed=module.migrate_cache_corpus(tmp_path,lambda c:update_mineru_max_new_tokens(c),dry_run=True)
    assert len(resumed)==1
    assert resumed[0].new_cache_digest==digest
    assert resumed[0].state == 'filesystem_ready'
    assert execute_filesystem_migration(resumed[0],retain_original=True)==digest
    with module.FileLock(plan.parent/'.lock'):module._archive_original(resumed[0])
    assert not plan.directory.exists()
    assert resumed[0].state == 'complete'
    assert (plan.parent/('.migration-original-'+plan.old_key)/'manifest.json').read_bytes()==original


def test_migration_rejects_changed_pdf(tmp_path):
    plan=make_plan(tmp_path)
    plan.pdf_path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='PDF'):execute_filesystem_migration(plan)
    assert plan.directory.exists() and not plan.target_directory.exists()


def test_selects_model_descriptors_and_keeps_other_ocr():
    value = {
        'ocr': {'name': 'paddleocr_vl_1_6', 'settings': {'max_new_tokens': 2048}},
        'table_ocr': {'model_id': 'opendatalab/MinerU2.5-Pro-2605-1.2B',
                      'settings': {'max_new_tokens': 1024}},
        'extractor': {'model_identity': {'name': 'mineru2_5_pro_2605'},
                      'settings': {'max_new_tokens': 2048}},
        'unknown': {'ocr': {'settings': {'max_new_tokens': 2048}}},
    }
    assert update_mineru_max_new_tokens(value, from_tokens=2048)
    assert value['extractor']['settings']['max_new_tokens'] == 8096
    assert value['table_ocr']['settings']['max_new_tokens'] == 1024
    assert update_mineru_max_new_tokens(value)
    assert value['table_ocr']['settings']['max_new_tokens'] == 8096
    assert value['ocr']['settings']['max_new_tokens'] == 2048
    assert value['unknown']['ocr']['settings']['max_new_tokens'] == 2048
    assert not update_mineru_max_new_tokens(value)


def test_cli_options_forward_scope_mode_and_target(tmp_path, monkeypatch):
    import migrate_cache as module
    migrate = MagicMock()
    monkeypatch.setattr(module, 'migrate_cache_corpus', migrate)
    assert main(['--root', str(tmp_path), '--corpus-root', '/corpus', '--model', 'opendatalab/MinerU2.5-Pro-2605-1.2B',
                 '--param', 'batch_size', '--from-tokens', '1', '--to-tokens', '2',
                 '--mode', 'recompute', '--no-db', '--dry-run']) == 0
    options = migrate.call_args.kwargs
    assert options['root'] == tmp_path
    assert options['corpus_root'] == '/corpus'
    assert options['mode'] == 'recompute'
    assert options['dsn'] is None
    assert options['dry_run']
    config = {'model_identity': {'name': 'mineru2_5_pro_2605'},
              'settings': {'max_new_tokens': 2048, 'batch_size': 1}}
    assert options['transform_fn'](config)
    assert config['settings'] == {'max_new_tokens': 2048, 'batch_size': 2}


def test_cli_default_profile_matches_current_runtime(tmp_path, monkeypatch):
    from pdfparse.backend.defaults import default_runtime_configuration
    import migrate_cache as module
    migrate = MagicMock()
    monkeypatch.setattr(module, 'migrate_cache_corpus', migrate)
    expected = default_runtime_configuration().model_dump(mode='json')
    assert expected['ocr']['settings']['max_new_tokens'] == 8096
    legacy = json.loads(json.dumps(expected))
    legacy['ocr']['settings']['max_new_tokens'] = 2048
    assert main(['--root', str(tmp_path), '--update-default-profile', '--to-tokens', '2000']) == 0
    options = migrate.call_args.kwargs
    assert options['mode'] == 'adopt'
    assert options['transform_runtime_fn'](legacy)
    assert legacy == expected


def test_sql_rejects_divergent_target_before_writing():
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.side_effect = [
        [('doc-1', 'observed', 'valid', None, 'km-doc-1', 42, None, True, 'sync-1', 'old-digest', None)],
        [('doc-1', 'doc.pdf.pdfparsecache/new', 'divergent')],
    ]
    plan = MagicMock(old_key='old', new_key='new', old_cache_digest='old-digest', new_cache_digest='new-digest')
    with pytest.raises(FileExistsError, match='cible'):
        execute_database_migration(conn, plan, '/corpus', 'doc.pdf.pdfparsecache/old')
    assert all(call.args[0].lstrip().startswith('SELECT') for call in cursor.execute.call_args_list)


@pytest.mark.parametrize('digest, expected_success', [('new-digest', True), ('divergent', False)])
def test_sql_replays_only_matching_destination(digest, expected_success):
    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchall.side_effect = [[], [(digest,)]]
    plan = MagicMock(old_key='old', new_key='new', new_cache_digest='new-digest')
    if expected_success:
        execute_database_migration(conn, plan, '/corpus', 'doc.pdf.pdfparsecache/old')
    else:
        with pytest.raises(RuntimeError, match='non vérifiable'):
            execute_database_migration(conn, plan, '/corpus', 'doc.pdf.pdfparsecache/old')
    assert all(call.args[0].lstrip().startswith('SELECT') for call in cursor.execute.call_args_list)


def test_prepared_journal_recovers_unpublished_directory(tmp_path, monkeypatch):
    import migrate_cache as module
    plan = make_plan(tmp_path)
    replace = module._durable_replace

    def fail_publish(source, destination):
        if destination == plan.target_directory:
            raise OSError('publication interrompue')
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(module, '_durable_replace', fail_publish)
        with pytest.raises(OSError, match='interrompue'):
            execute_filesystem_migration(plan, retain_original=True)
    assert plan.state == 'prepared'
    assert plan.directory.is_dir() and not plan.target_directory.exists()

    recovered, = module.migrate_cache_corpus(tmp_path, update_mineru_max_new_tokens)
    assert recovered.state == 'complete'
    assert recovered.new_cache_digest == plan.new_cache_digest
    assert recovered.target_directory.is_dir() and not recovered.directory.exists()


def test_resume_database_commit_only_archives_original(tmp_path):
    import migrate_cache as module
    plan = make_plan(tmp_path)
    plan.database_scope = ('/corpus', plan.target_directory.relative_to(tmp_path).as_posix())
    execute_filesystem_migration(plan, retain_original=True)
    with pytest.raises(RuntimeError, match='DSN'):
        module.migrate_cache_corpus(tmp_path, update_mineru_max_new_tokens)
    assert plan.directory.is_dir()

    module._journal(plan, 'database_committed')
    resumed, = module.migrate_cache_corpus(tmp_path, update_mineru_max_new_tokens)
    assert resumed.state == 'complete'
    assert not resumed.directory.exists()


def test_recompute_dispatches_without_relabelling_cache(tmp_path, monkeypatch):
    import migrate_cache as module
    plan = make_plan(tmp_path)
    recompute = MagicMock()
    monkeypatch.setattr(module, 'execute_recomputation', recompute)
    result, = module.migrate_cache_corpus(tmp_path, update_mineru_max_new_tokens, mode='recompute')
    recompute.assert_called_once_with(result)
    assert result.new_key == plan.new_key
    assert plan.directory.is_dir()
    assert not plan.target_directory.exists()
    assert not list(plan.parent.glob('.migration-*.json'))


def test_external_cache_discovery_skips_non_object_manifest(tmp_path, monkeypatch):
    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    pdf = corpus / 'test.pdf'
    pdf.write_bytes(b'%PDF-fixture')
    external = tmp_path / 'cache'
    upstream = '/data/test.pdf'
    parent = external / (hashlib.sha256(upstream.encode()).hexdigest() + '.pdfparsecache')
    configuration = {'value': 1}
    sha = file_sha256(pdf)
    key = compute_cache_identity(sha, compute_configuration_hash(configuration))
    source = parent / key
    source.mkdir(parents=True)
    (source / 'manifest.json').write_text(json.dumps({
        'format': 'pdfparse.result_cache', 'schema_version': 2, 'configuration': configuration,
        'pdfpath': upstream, 'pdf_sha256': sha, 'files': {},
    }))
    malformed = parent / ('0' * 64)
    malformed.mkdir()
    (malformed / 'manifest.json').write_text('[]')
    monkeypatch.setenv('PDFPARSE_CACHE_DIR', str(external))

    def update(value):
        value['value'] = 2
        return True

    plan, = migrate_cache_corpus(corpus, update)
    assert plan.pdf_path == pdf
    assert plan.state == 'complete'
    assert plan.target_directory.is_dir()
    assert not source.exists()
    assert malformed.is_dir()
