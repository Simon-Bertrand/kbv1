"""Opt-in smoke tests for the selected official model weights."""

import os

import pytest
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from pdfparse.enrich import FileSystemAssetStore, wrap_document
from pdfparse.models import ModelRegistry
from tests.test_layout_pipeline import make_pdf


@pytest.mark.skipif(os.getenv("PDFPARSE_TEST_LAYOUT_MODELS") != "1",
                    reason="Requires local PP-DocLayout-S and Heron-101 bundles")
@pytest.mark.parametrize("model", ["pp_doclayout_s", "heron", "heron_101"])
def test_selected_real_model(tmp_path, model):
    source = tmp_path / "sample.pdf"
    make_pdf(source, pages=2)
    layout = ModelRegistry().load('layout', model).get_options()
    options = PdfPipelineOptions(
        layout_options=layout, allow_external_plugins=True,
        do_ocr=False, do_table_structure=False, generate_picture_images=True,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2),
    )
    converter = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=options),
    })
    result = converter.convert(source)
    assert result.status == ConversionStatus.SUCCESS
    assert not result.errors
    assert len(result.document.pages) == 2
    assert "Hello layout pipeline" in result.document.export_to_markdown()
    wrap_document(source, result.document, asset_store=FileSystemAssetStore(tmp_path / "assets")).validate()
