"""Opt-in inference on the two pinned, locally downloaded DFC models."""

import os

import pytest
from PIL import Image

from pdfparse.models import ModelRegistry


@pytest.mark.skipif(os.getenv("PDFPARSE_TEST_PICTURE_MODELS") != "1",
                    reason="Requires local DFC bundles")
@pytest.mark.parametrize("model,count", [("dfc_v1", 16), ("dfc_v25", 26)])
def test_real_dfc(model, count):
    classifier = ModelRegistry().load('picture_classifier', model)
    images = [Image.new("RGB", (240, 120), "white"), Image.new("L", (100, 160), 90)]
    batch = classifier.predict_batch(images)
    for image, result in zip(images, batch, strict=True):
        assert len(result.scores) == count
        assert sum(score.confidence for score in result.scores) == pytest.approx(1., abs=1e-5)
        single = classifier.predict(image)
        assert single.best.label == result.best.label
        assert [s.confidence for s in single.scores] == pytest.approx(
            [s.confidence for s in result.scores], abs=1e-5,
        )
    if model == "dfc_v25":
        assert "photograph" in {score.label for score in batch[0].scores}
