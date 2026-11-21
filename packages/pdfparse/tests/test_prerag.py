"""Representative pre-RAG cases: structure, coverage, budgets and index exports."""
from dataclasses import replace
import json

import pytest
from docling_core.types.doc import DocItemLabel, DoclingDocument, RefItem, TableData

from pdfparse import ChonkieChunkingModel, PdfParseConfiguration, PdfParsePipeline, PdfParseResult, StructuralChunkingModel
from tests.test_rag_enrichment import cell, enriched, location


def result_for(tmp_path, doc, model, *, pages=None):
    document = enriched(tmp_path, doc)
    pipeline = PdfParsePipeline(PdfParseConfiguration(cache=False, chunking=model))
    return PdfParseResult(document, pipeline.chunk_document(document), pages)


def technical_document():
    doc = DoclingDocument(name='Maintenance')
    doc.add_text(label=DocItemLabel.TITLE, text='Maintenance', prov=location(1))
    doc.add_heading(text='Pièces', level=1, prov=location(1))
    cells = [cell('Produit', 0, 0, column_header=True), cell('Masse (kg)', 0, 1, column_header=True)]
    for row in range(1, 11):
        cells.extend([cell(f'Pièce-{row}', row, 0), cell(str(row * 2), row, 1)])
    table = doc.add_table(data=TableData(num_rows=11, num_cols=2, table_cells=cells), prov=location(2))
    caption = doc.add_text(label=DocItemLabel.CAPTION, text='Pièces homologuées', parent=table, prov=location(2))
    table.captions.append(RefItem(cref=caption.self_ref))
    doc.add_heading(text='Procédure', level=1, prov=location(3))
    group = doc.add_list_group()
    doc.add_list_item('Débrancher', parent=group, prov=location(3))
    doc.add_list_item('Vérifier', parent=group, prov=location(3))
    code = doc.add_code(text='pressure = 12\nassert pressure > 0', prov=location(3))
    return doc, table, code


def test_structure_keeps_rows_lists_code_sections_and_complete_coverage(tmp_path):
    doc, table, code = technical_document()
    result = result_for(tmp_path, doc, StructuralChunkingModel(chunk_size=120), pages=(1, 2, 3, 4))
    assert result.quality_report().coverage == 1.0
    assert result.quality_report().uncovered_spans == ()
    assert result.quality_report().pages_without_indexable_text == (4,)
    assert result.quality_report().page_inventory_complete
    table_chunks = [chunk for chunk in result.chunks if table.self_ref in {ref.cref for ref in chunk.body_refs}]
    assert len(table_chunks) > 1
    assert all('Pièces homologuées' in chunk.contextualized_text for chunk in table_chunks)
    for row in range(1, 11):
        expected = f'Produit: Pièce-{row} ; Masse (kg): {row * 2}'
        assert sum(expected in chunk.text for chunk in table_chunks) == 1
    assert any('Débrancher\n\nVérifier' in chunk.text for chunk in result.chunks)
    assert sum(code.text in chunk.text for chunk in result.chunks) == 1
    for chunk in result.chunks:
        assert len(chunk.contextualized_text) == chunk.token_count <= 120
        assert chunk.tokenizer_id == 'character'
        assert not ('Pièces' in chunk.context and 'Procédure' in chunk.text)
        assert all(ref.resolve(result.document) for ref in chunk.source_refs)
    assert StructuralChunkingModel(chunk_size=120).chunk(result.document) == list(result.chunks)


def test_oversized_table_row_repeats_first_field_and_marks_fallback(tmp_path):
    doc = DoclingDocument(name='large row')
    doc.add_heading(text='Catalogue', level=1)
    table = doc.add_table(data=TableData(num_rows=2, num_cols=2, table_cells=[
        cell('Produit', 0, 0, column_header=True), cell('Description', 0, 1, column_header=True),
        cell('A-12', 1, 0), cell('résiste à la chaleur ' * 40, 1, 1)]), prov=location(1))
    result = result_for(tmp_path, doc, StructuralChunkingModel(chunk_size=100))
    fragments = [chunk for chunk in result.chunks if chunk.split_reason == 'oversized_table_row']
    assert len(fragments) > 1
    assert all('Produit: A-12' in chunk.contextualized_text for chunk in fragments)
    assert all(table.self_ref in {ref.cref for ref in chunk.body_refs} for chunk in fragments)
    assert all(len(chunk.contextualized_text) <= 100 for chunk in fragments)
    assert result.quality_report().coverage == 1.0


def test_custom_token_budget_counts_full_context_and_no_tokenizer_is_loaded(tmp_path):
    doc = DoclingDocument(name='tokenized')
    doc.add_heading(text='Énergie', level=1)
    doc.add_text(label=DocItemLabel.TEXT, text='αβ 👩‍🔬 fonctionnement sûr. ' * 15, prov=location(1))
    # An explicit offline byte tokenizer: Unicode costs differ from character count.
    def counter(text):
        return len(text.encode('utf-8')) + 2
    result = result_for(tmp_path, doc, StructuralChunkingModel(
        chunk_size=90, count_tokens=counter, tokenizer_id='test.utf8-with-specials.v1'))
    assert len(result.chunks) > 2
    assert all(counter(chunk.contextualized_text) == chunk.token_count <= 90 for chunk in result.chunks)
    assert result.quality_report().coverage == 1.0
    assert any(chunk.split_reason for chunk in result.chunks)


def test_oversized_context_drops_only_the_heading_prefix(tmp_path):
    doc = DoclingDocument(name='too much context')
    title = 'Titre qui ne peut pas tenir dans le budget'
    heading = doc.add_heading(text=title, level=1)
    doc.add_text(label=DocItemLabel.TEXT, text='Corps')
    result = result_for(tmp_path, doc, StructuralChunkingModel(chunk_size=10))
    assert ''.join(chunk.text for chunk in result.chunks[:-1]) == title
    body = result.chunks[-1]
    assert body.text == 'Corps'
    assert body.context == ''
    assert body.section_refs[0].cref == heading.self_ref
    assert 'reduced_section_context' in body.split_reason
    assert result.quality_report().coverage == 1.0


def test_page_boundary_splits_a_block_with_aligned_provenance(tmp_path):
    doc = DoclingDocument(name='two pages')
    first, second = 'Première page. ', 'Seconde page.'
    item = doc.add_text(label=DocItemLabel.TEXT, text=first + second, prov=location(1))
    item.prov[0].charspan = (0, len(first))
    page2 = location(2)
    page2.charspan = (len(first), len(first + second))
    item.prov.append(page2)
    result = result_for(tmp_path, doc, StructuralChunkingModel(respect_page_boundaries=True))
    assert [chunk.text for chunk in result.chunks] == [first, second]
    assert [[loc.page_no for loc in chunk.locations] for chunk in result.chunks] == [[1], [2]]
    assert [record['body_pages'] for record in result.index_records()] == [[1], [2]]


def test_nested_list_continuations_retain_parent_and_large_code_is_traceable(tmp_path):
    doc = DoclingDocument(name='nested procedure')
    group = doc.add_list_group()
    parent = doc.add_list_item('Sécurité', parent=group, prov=location(1))
    nested = doc.add_list_group(parent=parent)
    children = [doc.add_list_item(f'Étape-{index}: vérifier le circuit', parent=nested, prov=location(1))
                for index in range(8)]
    code = doc.add_code(text='print("aucune perte")\n' * 20, prov=location(2))
    result = result_for(tmp_path, doc, StructuralChunkingModel(chunk_size=75))
    for child in children:
        chunks = [chunk for chunk in result.chunks if child.self_ref in {ref.cref for ref in chunk.body_refs}]
        assert chunks and all('Sécurité' in chunk.contextualized_text for chunk in chunks)
    code_chunks = [chunk for chunk in result.chunks if code.self_ref in {ref.cref for ref in chunk.body_refs}]
    assert all(chunk.split_reason == 'oversized_code' for chunk in code_chunks)
    assert result.quality_report().coverage == 1.0
    assert all(chunk.token_count <= 75 for chunk in result.chunks)


@pytest.mark.parametrize('options, error', [
    ({'chunk_size': True}, ValueError), ({'chunk_size': 0}, ValueError),
    ({'count_tokens': 3}, TypeError), ({'count_tokens': len}, ValueError),
    ({'tokenizer_id': 'pretend-tokenizer'}, ValueError),
])
def test_structural_configuration_rejects_ambiguous_budgets(options, error):
    with pytest.raises(error):
        StructuralChunkingModel(**options)


def test_quality_does_not_double_count_overlap_or_guess_repeated_offsets(tmp_path):
    from chonkie import TokenChunker
    doc = DoclingDocument(name='overlap')
    doc.add_text(label=DocItemLabel.TEXT, text='Répété répété répété ' * 20, prov=location(1))
    model = ChonkieChunkingModel(TokenChunker(tokenizer='character', chunk_size=40, chunk_overlap=10),
                                transformation_id='test.overlap.v1')
    result = result_for(tmp_path, doc, model)
    report = result.quality_report()
    assert report.covered_characters == report.searchable_characters
    assert report.coverage == 1.0
    assert not report.page_inventory_complete
    assert report.repetitive_text_refs
    incomplete = replace(result, chunks=result.chunks[:1]).quality_report()
    assert 0 < incomplete.coverage < 1 and incomplete.uncovered_spans
    unknown = replace(result, chunks=(replace(result.chunks[0], start_index=None, end_index=None),)).quality_report()
    assert unknown.coverage is None and unknown.unknown_offset_chunk_ids
    assert unknown.uncovered_spans == ()
    invalid = replace(result, chunks=(replace(result.chunks[0], start_index=2),))
    with pytest.raises(ValueError, match='Invalid canonical offsets'):
        invalid.quality_report()


def test_quality_flags_suspicious_text_and_unindexed_blocks_without_mutation(tmp_path):
    doc = DoclingDocument(name='poor extraction')
    broken = doc.add_text(label=DocItemLabel.TEXT, text='Défaut\ufffd\x00', prov=location(1))
    doc.add_picture(prov=location(2))
    result = result_for(tmp_path, doc, StructuralChunkingModel(), pages=(1, 2, 3))
    before = result.document.to_dict()
    report = replace(result, chunks=()).quality_report()
    assert report.coverage == 0.0
    assert report.suspicious_text_refs == (broken.self_ref,)
    assert report.unindexed_refs == (broken.self_ref,)
    assert report.pages_without_indexable_text == (2, 3)
    assert result.document.to_dict() == before
    empty = result_for(tmp_path, DoclingDocument(name='empty'), StructuralChunkingModel())
    assert empty.quality_report().coverage == 1.0
    assert empty.quality_report().searchable_characters == 0


def test_versioned_index_metadata_hashes_and_atomic_jsonl(tmp_path, monkeypatch):
    doc, table, _ = technical_document()
    result = result_for(tmp_path, doc, StructuralChunkingModel(chunk_size=120))
    records = list(result.index_records(language='fr', text_origins={table.self_ref: 'ocr'}))
    assert all(record['format'] == 'pdfparse.index_record' and record['schema_version'] == 4 for record in records)
    table_records = [record for record in records if table.self_ref in record['body_refs']]
    assert all(record['section_path'] == ['Maintenance', 'Pièces'] for record in table_records)
    assert all(2 in record['body_pages'] for record in table_records)
    assert any(record['body_refs'] == [table.self_ref] and record['body_pages'] == [2]
               for record in table_records)
    assert all(record['text_origins'][table.self_ref] == 'ocr' for record in table_records)
    assert all(record['language'] == 'fr' and record['pages'] for record in records)
    assert records == list(result.index_records(language='fr', text_origins={table.self_ref: 'ocr'}))
    renamed = list(result.index_records(language='en', text_origins={table.self_ref: 'ocr'}))
    assert [record['content_hash'] for record in records] == [record['content_hash'] for record in renamed]
    assert all(left['record_hash'] != right['record_hash'] for left, right in zip(records, renamed))
    destination = tmp_path / 'index.jsonl'
    result.export_jsonl(destination, language='fr', text_origins={table.self_ref: 'ocr'})
    assert [json.loads(line) for line in destination.read_text().splitlines()] == json.loads(json.dumps(records))
    before = destination.read_bytes()
    with pytest.raises(ValueError, match='unknown element'):
        result.export_jsonl(destination, text_origins={'#/missing': 'ocr'})
    assert destination.read_bytes() == before
    import pdfparse.ingestion.indexing as indexing
    def fail(*args):
        raise OSError('publication failed')
    monkeypatch.setattr(indexing.os, 'replace', fail)
    with pytest.raises(OSError, match='publication failed'):
        result.export_jsonl(destination)
    assert destination.read_bytes() == before
    assert not list(tmp_path.glob('.index.jsonl.*.tmp'))


def test_unproven_language_and_text_origin_remain_unknown(tmp_path):
    doc = DoclingDocument(name='no inferred metadata')
    doc.add_text(label=DocItemLabel.TEXT, text='Un texte français ne prouve pas une origine native.')
    result = result_for(tmp_path, doc, StructuralChunkingModel())
    record = next(result.index_records())
    assert record['language'] is None
    assert set(record['text_origins'].values()) == {'unknown'}
    with pytest.raises(ValueError):
        list(result.index_records(text_origins={'#/texts/0': 'guessed'}))


def test_large_table_nested_in_list_preserves_parent_context(tmp_path):
    doc = DoclingDocument(name='nested table')
    group = doc.add_list_group()
    parent = doc.add_list_item('Équipement X', parent=group, prov=location(1))
    table = doc.add_table(data=TableData(num_rows=12, num_cols=1, table_cells=[
        cell('Pièce', 0, 0, column_header=True),
        *[cell(f'Composant-{row}', row, 0) for row in range(1, 12)],
    ]), parent=parent, prov=location(2))
    result = result_for(tmp_path, doc, StructuralChunkingModel(chunk_size=70))
    fragments = [chunk for chunk in result.chunks if table.self_ref in {ref.cref for ref in chunk.body_refs}]
    assert len(fragments) > 1
    assert all('Équipement X' in chunk.contextualized_text for chunk in fragments)
    assert all(parent.self_ref in {ref.cref for ref in chunk.source_refs} for chunk in fragments)
    assert result.quality_report().coverage == 1.0


def test_small_query_corpus_preserves_expected_evidence(tmp_path):
    doc, table, code = technical_document()
    result = result_for(tmp_path, doc, StructuralChunkingModel(chunk_size=120))
    cases = [
        ('Quelle est la masse de Pièce-7 ?', ('Pièce-7', 'Masse (kg): 14'), table.self_ref),
        ('Quelle pression est vérifiée dans le code ?', ('pressure = 12', 'assert pressure > 0'), code.self_ref),
    ]
    for question, evidence, source in cases:
        assert any(all(term in chunk.contextualized_text for term in evidence)
                   and source in {ref.cref for ref in chunk.source_refs} for chunk in result.chunks), question


def test_real_two_column_pdf_retains_both_columns_and_reading_order(tmp_path):
    from reportlab.pdfgen import canvas
    from pdfparse import NativeLayoutModel
    from pdfparse.models.layout.options import DoclingLayoutOptions
    from tests.layout_detectors import TwoColumnDetector
    path = tmp_path / 'two-columns.pdf'
    pdf = canvas.Canvas(str(path), pagesize=(500, 400))
    for x, label in ((20, 'Gauche'), (280, 'Droite')):
        pdf.drawString(x, 350, f'{label} premier paragraphe')
        pdf.drawString(x, 320, f'{label} seconde ligne')
    pdf.save()
    options = DoclingLayoutOptions(model_path=tmp_path, detector_class=TwoColumnDetector)
    result = PdfParsePipeline(PdfParseConfiguration(cache=False, 
        layout=NativeLayoutModel(options), chunking=StructuralChunkingModel(chunk_size=160))).run(path)
    text = '\n'.join(chunk.text for chunk in result.chunks)
    assert 'Gauche premier paragraphe' in text and 'Droite premier paragraphe' in text
    assert text.index('Gauche seconde ligne') < text.index('Droite premier paragraphe')
    assert result.quality_report().coverage == 1.0
    assert result.quality_report().pages_without_indexable_text == ()


@pytest.mark.parametrize('count', [True, -1, 1.5])
def test_invalid_token_counter_outputs_fail_at_the_boundary(tmp_path, count):
    doc = DoclingDocument(name='invalid counter')
    doc.add_text(label=DocItemLabel.TEXT, text='texte')
    with pytest.raises(ValueError, match='nonnegative integer'):
        result_for(tmp_path, doc, StructuralChunkingModel(count_tokens=lambda text: count, tokenizer_id='broken'))
