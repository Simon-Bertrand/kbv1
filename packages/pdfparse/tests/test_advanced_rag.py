"""Advanced preparation contracts with deterministic annotated evidence."""
from dataclasses import replace
import json

import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument, TableData

from pdfparse import PdfParseConfiguration, PdfParsePipeline, PdfParseResult, StructuralChunkingModel
from pdfparse.ingestion.contracts import ExtractionRegion
from pdfparse.ingestion.evaluation import EvidenceCase, evaluate_retrieval
from pdfparse.ingestion.indexing import migrate_index_record
from tests.test_prerag import result_for, technical_document
from tests.test_rag_enrichment import cell, location
from tests.test_pdfparse_pipeline import Extractor


def test_snapshot_is_detached_and_builds_canonical_text_once(tmp_path, monkeypatch):
    import pdfparse.ingestion.indexing as indexing
    result = result_for(tmp_path, technical_document()[0], StructuralChunkingModel(chunk_size=120))
    calls = []
    original = indexing.prepare_text
    monkeypatch.setattr(indexing, 'prepare_text', lambda doc: (calls.append(doc), original(doc))[1])
    snapshot = result.prepare_index(language='fr')
    assert len(calls) == 1 and snapshot.quality.coverage == 1
    records = list(snapshot.index_records())
    result.document.elements.clear()
    records[0]['text'] = 'external mutation'
    assert list(snapshot.index_records())[0]['text'] != 'external mutation'
    path = tmp_path / 'snapshot.jsonl'
    snapshot.export_jsonl(path)
    assert len(path.read_text().splitlines()) == len(records)


def test_manifest_stable_but_sensitive_to_chunk_configuration(tmp_path):
    path = tmp_path / 'input.pdf'
    path.write_bytes(b'fixture identity')
    source = technical_document()[0]
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=StructuralChunkingModel(chunk_size=120)), extractor=Extractor(source))
    first, second = pipeline.run(path), pipeline.run(path)
    assert first.manifest_json == second.manifest_json
    assert first.metrics.extraction_seconds >= 0
    assert first.metrics.enrichment_seconds >= 0
    assert first.metrics.chunking_seconds >= 0
    other = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=StructuralChunkingModel(chunk_size=150)), extractor=Extractor(source)).run(path)
    assert first.preparation_manifest()['manifest_hash'] != other.preparation_manifest()['manifest_hash']
    assert not first.quality_report().extraction_evidence_available
    assert all(record['preparation_hash'] == first.preparation_manifest()['manifest_hash'] for record in first.index_records())


def test_extraction_origins_and_losses_are_separate_from_chunk_coverage(tmp_path):
    source = DoclingDocument(name='origins')
    item = source.add_text(label=DocItemLabel.TEXT, text='Evidence native et OCR', prov=location(1))
    result = result_for(tmp_path, source, StructuralChunkingModel())
    result = replace(result, extraction_evidence_available=True, extraction=(
        ExtractionRegion(1, (0, 0, 10, 10), 'text', (item.self_ref,), 'ocr', 0.9),
        ExtractionRegion(1, (20, 0, 30, 10), 'text', (), 'unknown', 0.8),
    ))
    quality = result.quality_report()
    assert quality.coverage == 1 and quality.unmatched_extraction_regions == (1,)
    assert list(result.index_records())[0]['text_origins'][item.self_ref] == 'ocr'


def test_table_rows_retain_headers_and_candidate_links(tmp_path):
    source = DoclingDocument(name='table continuation')
    for page, value in ((1, '10'), (2, '20')):
        source.add_table(data=TableData(num_rows=2, num_cols=2, table_cells=[
            cell('Produit', 0, 0, column_header=True), cell('Masse', 0, 1, column_header=True),
            cell('A', 1, 0), cell(value, 1, 1)]), prov=location(page))
    result = result_for(tmp_path, source, StructuralChunkingModel(chunk_size=35))
    records = list(result.index_records())
    rows = [row for record in records for row in record['table_rows']]
    assert len(rows) == 2 and all(len(row['cells']) == 4 for row in rows)
    assert any(record['table_links'] for record in records)
    assert all(row['start'] < row['end'] for row in rows)
    # A different heading is a boundary, even when column labels match.
    result.document.elements['#/tables/1'].table_data['table_cells'][0]['text'] = 'Autre'
    assert not any(record['table_links'] for record in result.index_records())


def test_annotated_benchmark_reports_retention_and_external_rank_metrics(tmp_path):
    source, table, _ = technical_document()
    result = result_for(tmp_path, source, StructuralChunkingModel(chunk_size=120))
    case = EvidenceCase('mass-8', 'Quelle est la masse de Pièce-8 ?', '16 kg', (table.self_ref,), 'Produit: Pièce-8 ; Masse (kg): 16')
    report = evaluate_retrieval(result, [case])
    assert report['evidence_retention'] == 1 and report['hit_at_k'] is None
    relevant = next(c.chunk_id for c in result.chunks if case.evidence in c.text)
    other = next(c.chunk_id for c in result.chunks if c.chunk_id != relevant)
    report = evaluate_retrieval(result, [case], rankings={'mass-8': [other, relevant]}, retrieval_id='fixture-fixed-embeddings-v1', k=2)
    assert report['hit_at_k'] == 1 and report['mrr_at_k'] == 0.5
    with pytest.raises(ValueError):
        evaluate_retrieval(result, [case], rankings={'mass-8': ['unknown']}, retrieval_id='fixture')


def test_progressive_expansion_spends_budget_on_first_hit(tmp_path):
    from pdfparse.ingestion.text import prepare_text
    source = DoclingDocument(name='progressive')
    for text in ('alpha evidence extra detail', 'beta evidence ' * 30):
        source.add_text(label=DocItemLabel.TEXT, text=text)
    result = result_for(tmp_path, source, StructuralChunkingModel(chunk_size=15))
    prepared = prepare_text(result.document)
    first = prepared.chunk(0, 5, 'test')
    second_start = prepared.blocks[1].start
    second = prepared.chunk(second_start, second_start + 4, 'test')
    result = replace(result, chunks=(first, second))
    context = result.source_context([first.chunk_id, second.chunk_id], max_characters=40)
    assert context.expanded and context.text == 'alpha evidence extra detail\n\nbeta'
    assert len(context.text) <= 40


@pytest.mark.parametrize('version', [1, 2, 3])
def test_explicit_index_migration_preserves_embedding_hash(version):
    original = {'format': 'pdfparse.index_record', 'schema_version': version, 'content_hash': 'unchanged', 'record_hash': 'old'}
    if version >= 3:
        original.update(assets_dir=None, visual_assets=[], page_contexts=[])
    migrated = migrate_index_record(original)
    assert migrated['schema_version'] == 4 and migrated['content_hash'] == 'unchanged'
    assert migrated['visual_assets'] == migrated['page_contexts'] == [] and migrated['assets_dir'] is None
    assert migrated['record_hash'] != 'old' and original['schema_version'] == version
    assert migrate_index_record(migrated) == migrated


def test_multimodal_index_links_native_figure_text_and_generated_page_context(tmp_path):
    from PIL import Image
    from docling_core.types.doc import ImageRef, Size, RefItem
    from pdfparse.enrich import BasePageEnrichModel, PictureEnrichModel, FileSystemAssetStore
    from pdfparse.ingestion.text import prepare_text
    from tests.test_picture_routing import Classifier, Rule
    source = DoclingDocument(name='multimodal')
    for number in (1, 2):
        source.add_page(page_no=number, size=Size(width=100, height=200),
                        image=ImageRef.from_pil(Image.new('RGB', (100, 200)), dpi=72))
    figure = source.add_picture(image=ImageRef.from_pil(Image.new('RGB', (30, 40)), dpi=72), prov=location(1))
    caption = source.add_text(label=DocItemLabel.CAPTION, text='Figure 1 : régulation hydraulique', prov=location(1))
    figure.captions.append(RefItem(cref=caption.self_ref))
    label = source.add_text(label=DocItemLabel.TEXT, text='Pression : 12 kPa', parent=figure, prov=location(1))
    unrelated = source.add_text(label=DocItemLabel.TEXT, text='Autre sujet : mesures électriques.', prov=location(2))
    class Pages(BasePageEnrichModel):
        model_id, transformation_id = 'fixture.vlm', 'fixture.page.v1'
        def describe(self, image, *, markdown):
            return 'la régulation hydraulique.' if 'hydraulique' in markdown else 'mesures électriques.'
    def redundant_ocr(image):
        raise AssertionError('The native figure transcript already exists')
    rule = Rule(ocr=redundant_ocr, vlm=lambda image, text: 'Une vanne règle le débit de la pompe.')
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, 
        picture_enrich=PictureEnrichModel(classifier=Classifier(), routes={'chart': rule}),
        page_enrich=Pages(), asset_store=FileSystemAssetStore(tmp_path / 'assets')))
    path = tmp_path / 'hydraulique.pdf'
    path.write_bytes(b'multimodal evidence')
    document = pipeline.enrich_document(path, source)
    prepared = prepare_text(document)
    chunks = tuple(prepared.chunk(block.start, block.end, 'fixture.blocks.v1') for block in prepared.blocks)
    result = PdfParseResult(document, chunks, page_numbers=(1, 2))
    records = list(result.index_records(language='fr'))
    def record_for(ref):
        return next(record for record in records if record['body_refs'] == [ref])
    native = record_for(label.self_ref)
    assert native['visual_assets'][0]['ref'] == figure.self_ref
    assert native['visual_assets'][0]['role'] == 'body'
    asset = native['visual_assets'][0]
    assert asset['description'] == 'Une vanne règle le débit de la pompe.'
    assert asset['caption'] == caption.text and asset['ocr_text'] is None
    assert (tmp_path / 'assets' / asset['asset_path']).is_file()
    assert native['assets_dir'] == str(tmp_path / 'assets')
    assert [context['page_no'] for context in native['page_contexts']] == [1]
    assert native['page_contexts'][0]['model_id'] == 'fixture.vlm'
    summary = record_for('#/page_descriptions/1')
    assert summary['text_origins']['#/page_descriptions/1'] == 'generated'
    assert len(summary['visual_assets']) == 1 and summary['visual_assets'][0]['role'] == 'context'
    assert record_for(figure.self_ref)['text_origins'][figure.self_ref] == 'mixed'
    other = record_for(unrelated.self_ref)
    assert other['visual_assets'] == []
    assert [context['page_no'] for context in other['page_contexts']] == [2]
    assert all(record['schema_version'] == 4 for record in records)
    # Persisted wrappers carry everything required to reconstruct the same links.
    restored = pipeline.codec.from_dict(pipeline.codec.to_dict(document))
    assert records == list(PdfParseResult(restored, chunks, page_numbers=(1, 2)).index_records(language='fr'))
    old_hash, old_text_hash = native['record_hash'], native['content_hash']
    document.get(figure.self_ref).asset_path = 'other-image.png'
    changed = next(r for r in result.index_records(language='fr') if r['id'] == native['id'])
    assert changed['record_hash'] != old_hash and changed['content_hash'] == old_text_hash


def test_compare_chonkie_and_structural_on_same_annotations(tmp_path):
    from pdfparse import ChonkieChunkingModel
    from pdfparse.ingestion.evaluation import compare_preparations
    source, table, _ = technical_document()
    a = result_for(tmp_path, source, StructuralChunkingModel(chunk_size=120))
    b = replace(a, chunks=tuple(ChonkieChunkingModel().chunk(a.document)))
    cases = [EvidenceCase('mass', 'Quelle masse ?', '2 kg', (table.self_ref,), 'Produit: Pièce-1 ; Masse (kg): 2')]
    comparison = compare_preparations({'structural': a, 'chonkie': b}, cases)
    assert all(row['coverage'] == 1 and row['evidence_retention'] == 1 for row in comparison.values())


def test_visual_inventory_retains_unindexed_figures_and_snapshot_is_detached(tmp_path):
    from PIL import Image
    from docling_core.types.doc import ImageRef
    from pdfparse.enrich import MemoryAssetStore
    source = DoclingDocument(name='unindexed visual evidence')
    source.add_picture(image=ImageRef.from_pil(Image.new('RGB', (20, 30)), dpi=72), prov=location(1))
    path = tmp_path / 'visual.pdf'
    path.write_bytes(b'visual inventory')
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, asset_store=MemoryAssetStore()))
    document = pipeline.enrich_document(path, source)
    result = PdfParseResult(document, pipeline.chunk_document(document))
    assert not result.chunks
    snapshot = result.prepare_index()
    record = next(snapshot.visual_records())
    assert record['chunk_ids'] == [] and record['asset_path']
    assert record['document_path'] == str(path)
    document.elements.clear()
    assert next(snapshot.visual_records()) == record
    snapshot.export_visual_jsonl(tmp_path / 'images.jsonl')
    assert json.loads((tmp_path / 'images.jsonl').read_text()) == record


@pytest.mark.parametrize('limit_pages', [False, True])
def test_structural_page_boundary_policy_preserves_coverage_and_custom_token_budget(tmp_path, limit_pages):
    source = DoclingDocument(name='pages')
    group = source.add_list_group()
    for page in (1, 2):
        source.add_list_item(f'Mesure de la page {page}', parent=group, prov=location(page))
    model = StructuralChunkingModel(chunk_size=200, respect_page_boundaries=limit_pages,
        count_tokens=lambda text: len(text.encode('utf-8')), tokenizer_id='fixture.utf8')
    result = result_for(tmp_path, source, model)
    assert len(result.chunks) == (2 if limit_pages else 1)
    assert result.quality_report().coverage == 1
    assert all(chunk.token_count == len(chunk.contextualized_text.encode('utf-8')) for chunk in result.chunks)
    records = list(result.index_records())
    assert records[0]['previous_chunk_id'] is None and records[-1]['next_chunk_id'] is None
    if limit_pages:
        assert records[0]['next_chunk_id'] == records[1]['id']
        assert records[1]['previous_chunk_id'] == records[0]['id']


def test_structural_budget_checks_do_not_construct_candidate_chunks(tmp_path, monkeypatch):
    from pdfparse.ingestion.text import DocumentText
    source = DoclingDocument(name='long blocks')
    source.add_text(label=DocItemLabel.TEXT, text='Texte technique pertinent. ' * 120, prov=location(1))
    calls = []
    original = DocumentText.chunk
    def track(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(DocumentText, 'chunk', track)
    result = result_for(tmp_path, source, StructuralChunkingModel(chunk_size=100))
    assert len(calls) == len(result.chunks)
    assert result.quality_report().coverage == 1


def test_element_model_tags_use_saved_execution_evidence(tmp_path):
    from pdfparse.ingestion.preparation import element_model_provenance
    source = DoclingDocument(name='saved provenance')
    native = source.add_text(label=DocItemLabel.TEXT, text='Texte natif', prov=location(1))
    scanned = source.add_text(label=DocItemLabel.TEXT, text='Texte OCR', prov=location(1))
    result = result_for(tmp_path, source, StructuralChunkingModel())
    manifest = {'configuration': {'text_ocr': {
        'class': 'fixture.Ocr', 'model_identity': {'name': 'saved-ocr', 'weights': {'source': {'revision': 'sha'}}}, 'device': 'cuda'}},
        'extraction_options': {'options': {'layout_options': {'options': {'model_spec': {'options': {'name': 'saved-layout'}}}}}}}
    result = replace(result, manifest_json=json.dumps(manifest), extraction_evidence_available=True,
        extraction=(ExtractionRegion(1, (0, 0, 10, 10), 'text', (scanned.self_ref,), 'ocr', ocr_engine='fixture.Ocr'),))
    models = element_model_provenance(result)
    assert [tag['name'] for tag in models[native.self_ref]] == ['saved-layout']
    assert [tag['name'] for tag in models[scanned.self_ref]] == ['saved-layout', 'saved-ocr']
    assert models[scanned.self_ref][-1]['revision'] == 'sha'
    assert models[scanned.self_ref][-1]['device'] == 'cuda'
    assert not element_model_provenance(replace(result, extraction_evidence_available=False))[native.self_ref]
