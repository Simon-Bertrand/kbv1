"""Opt-in crop recognition on already prepared OCR bundles; never downloads."""
import json
import os

from PIL import Image, ImageDraw
import pytest

from pdfparse.models import ModelRegistry
from pdfparse.models.ocr.catalog import MODELS


@pytest.mark.skipif(os.getenv("PDFPARSE_TEST_OCR_MODELS") != "1", reason="Requires locally provisioned OCR registry")
@pytest.mark.parametrize("name", MODELS)
def test_real_ocr(name):
    models = ModelRegistry()
    devices = json.loads(os.getenv("PDFPARSE_TEST_OCR_DEVICES", "{}"))
    settings = json.loads(os.getenv("PDFPARSE_TEST_OCR_SETTINGS", "{}"))
    model = models.load("ocr", name, device=devices.get(name, os.getenv("PDFPARSE_TEST_OCR_DEVICE", "cpu")),
                        settings=settings.get(name))
    image = Image.new("RGB", (320, 80), "white")
    ImageDraw.Draw(image).text((20, 20), "TOTAL 123", fill="black", font_size=28)
    outputs = model.predict_batch([image, image.convert("L")])
    assert len(outputs) == 2 and all("123" in text for text in outputs)
    assert model.predict_batch([]) == []
