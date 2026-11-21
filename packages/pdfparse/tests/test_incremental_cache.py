"""Rechunk a validated legacy cache without changing its identity or inference."""
import hashlib
from pdfparse import StructuralChunkingModel
from pdfparse.ingestion.preparation import cache_configuration
from tests.test_result_cache import pipeline, cache_directory


def test_rechunk_reuses_enriched_document_and_preserves_existing_cache(tmp_path):
    pdf=tmp_path/'source.pdf';pdf.write_bytes(b'PDF fixture')
    original=pipeline(chunking=StructuralChunkingModel(chunk_size=128))
    key=cache_configuration(original)[0]
    first=original.run(pdf);directory=cache_directory(original,pdf)
    before={p.relative_to(directory):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file()}
    other=pipeline(chunking=StructuralChunkingModel(chunk_size=64))
    result=other.run(pdf)
    assert other.extractor.calls==0
    assert result.document.document_id==first.document.document_id
    assert result.preparation_manifest()['reuse']['inference_reexecuted'] is False
    assert {p.relative_to(directory):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file()}==before
    assert cache_configuration(original)[0]==key
    assert all('64' in chunk.transformation_id for chunk in result.chunks)


def test_incompatible_upstream_is_not_reused(tmp_path):
    from pdfparse import PdfParseConfiguration,PdfParsePipeline
    from tests.test_result_cache import FixtureExtractor
    pdf=tmp_path/'source.pdf';pdf.write_bytes(b'PDF fixture')
    pipeline().run(pdf)
    extractor=FixtureExtractor()
    changed=PdfParsePipeline(PdfParseConfiguration(cache_identity={'fixture':'different'},chunking=StructuralChunkingModel(chunk_size=64)),extractor=extractor)
    changed.run(pdf)
    assert extractor.calls==1


def test_frozen_legacy_cache_identity():
    # Captured with the existing cache_configuration implementation before new stage controls.
    assert cache_configuration(pipeline())[0]=='90b7a00cb49909636c719b8f064c91742f03b7455a32b4fe635c3f5674b16fbc'


def test_corrupted_extraction_checkpoint_recomputes_only_when_needed(tmp_path):
    import shutil
    pdf=tmp_path/'source.pdf';pdf.write_bytes(b'fixture')
    first=pipeline();first.run(pdf)
    parent=pdf.with_name(pdf.name+'.pdfparsecache')
    for path in parent.iterdir():
        if len(path.name)==64 and path.is_dir():shutil.rmtree(path)
    checkpoint=next((parent/'.stages').rglob('*.json'));checkpoint.write_text('{}')
    second=pipeline();second.run(pdf)
    assert second.extractor.calls==1


def test_external_image_uri_is_not_a_reusable_checkpoint():
    from pdfparse.ingestion.stages import StageStore
    assert not StageStore.embedded_images({'image':{'uri':'file:///tmp/temporary-image.png'}})
    assert not StageStore.embedded_images({'image':{'uri':'https://remote/image.png'}})
    assert StageStore.embedded_images({'image':{'uri':'data:image/png;base64,AAAA'}})


def test_individual_inference_cache_preserves_structured_ocr(tmp_path):
    from pdfparse.execution import Call
    from pdfparse.ingestion.stages import StageStore
    from pdfparse.models.ocr import BaseOcrModel, OcrRegion
    from docling_core.types.doc import TableData
    from types import SimpleNamespace
    from PIL import Image
    class Ocr(BaseOcrModel):
        def predict(self,image):return 'text'
    model=Ocr(model_path=tmp_path)
    entry=SimpleNamespace(parent=tmp_path,pdf_sha256='a'*64,pipeline=pipeline())
    store=StageStore(entry)
    key=store.call_key(Call(model.predict,(Image.new('RGB',(2,2),'blue'),)))
    assert key
    assert store.read_call(key)==(False,None)
    regions=[OcrRegion('hello',(0,0,2,2))]
    store.write_call(key,regions)
    assert store.read_call(key)==(True,regions)
    table=TableData(num_rows=0,num_cols=0,table_cells=[])
    store.write_call(key,table)
    assert store.read_call(key)==(True,table)
    changed=store.call_key(Call(model.predict,(Image.new('RGB',(2,2),'red'),)))
    assert changed!=key
    palette=Image.new('P',(2,2));palette.putpalette([0,0,0]*256)
    palette_key=store.call_key(Call(model.predict,(palette,)))
    palette.putpalette([255,0,0]*256)
    assert store.call_key(Call(model.predict,(palette,)))!=palette_key
    another=StageStore(SimpleNamespace(parent=tmp_path,pdf_sha256='b'*64,pipeline=pipeline()))
    assert another.call_key(Call(model.predict,(Image.new('RGB',(2,2),'blue'),)))==key
    assert another.read_call(key)==(True,table)
    path=store.path('inference',key);path.write_text('{bad json')
    assert store.read_call(key)==(False,None)


def test_fast_cache_matches_effective_tokenizer_not_only_the_studio_profile(tmp_path):
    from pdfparse.backend.defaults import default_runtime_configuration
    from pdfparse.backend.runtime import effective_runtime_configuration
    from pdfparse.ingestion.cache import load_cached_run, remember_runtime_configuration

    pdf = tmp_path / 'source.pdf'
    pdf.write_bytes(b'PDF fixture')
    selection = default_runtime_configuration()
    character = StructuralChunkingModel()
    tokenized = StructuralChunkingModel(count_tokens=len, tokenizer_id='e5.fixture')
    engine = pipeline(chunking=character)
    engine.run(pdf)
    profile = effective_runtime_configuration(selection, character)
    remember_runtime_configuration(pdf, engine, profile)
    assert load_cached_run(pdf, profile) is not None
    assert load_cached_run(pdf, effective_runtime_configuration(selection, tokenized)) is None
