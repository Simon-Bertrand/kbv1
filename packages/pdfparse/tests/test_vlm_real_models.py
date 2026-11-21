"""Opt-in inference on already provisioned VLM bundles; never downloads weights."""
import json
import os

from PIL import Image, ImageDraw
import pytest

from pdfparse.models import ModelRegistry
from pdfparse.models.vlm.catalog import MODELS


@pytest.mark.skipif(os.getenv("PDFPARSE_TEST_VLM_MODELS") != "1", reason="Requires locally provisioned VLM registry")
@pytest.mark.parametrize("name", MODELS)
def test_real_vlm(name):
    registry = ModelRegistry()
    overrides = json.loads(os.getenv("PDFPARSE_TEST_VLM_SETTINGS", "{}"))
    settings = {"max_new_tokens": 2048 if name == "mechvl_4b_rl" else 32, "batch_size": 1,
                **overrides.get(name, {})}
    model = registry.load("vlm", name, device=os.getenv("PDFPARSE_TEST_VLM_DEVICE", "cpu"), settings=settings)
    image = Image.new("RGB", (240, 120), "white")
    ImageDraw.Draw(image).text((20, 40), "TOTAL 123", fill="black", font_size=24)
    prompt = "OCR:" if name == "paddleocr_vl_1_6" else "Read the text in this image."
    results = model.predict_batch([image, image.convert("L")], prompt=prompt)
    assert len(results) == 2 and all("123" in text for text in results)
