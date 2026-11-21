"""Deterministic local detectors for exercising the real Docling pipeline."""

from docling_core.types.doc import DocItemLabel

from pdfparse.models.layout import BaseLayoutDetector, Detection


class FixtureDetector(BaseLayoutDetector):
    def predict(self, image):
        w, h = image.size
        return [
            Detection(DocItemLabel.TEXT, 0.95, (0, 0, w, h * 0.4)),
            Detection(DocItemLabel.PICTURE, 0.9, (w * 0.1, h * 0.5, w * 0.9, h * 0.9)),
        ]


class AlternativeDetector(FixtureDetector):
    """Second backend used to verify pipeline cache isolation."""


class ScannedIndexDetector(BaseLayoutDetector):
    def predict(self, image):
        return [Detection(DocItemLabel.DOCUMENT_INDEX, .99, (0, 0, *image.size))]


class TwoColumnDetector(BaseLayoutDetector):
    """Two deterministic text columns for pre-RAG reading-order integration."""

    def predict(self, image):
        w, h = image.size
        return [Detection(DocItemLabel.TEXT, .99, (0, 0, w * .49, h)),
                Detection(DocItemLabel.TEXT, .99, (w * .51, 0, w, h))]


class DuplicateTextDetector(FixtureDetector):
    """Overlapping body and line proposals, to exercise pre-OCR deduplication."""
    def predict(self, image):
        text, picture = super().predict(image)
        w, h = image.size
        return [Detection(text.label, 0.6, text.xyxy), Detection(text.label, 0.55, text.xyxy),
                Detection(text.label, 0.95, (0, 0, w, h * 0.1)),
                Detection(text.label, 0.95, (0, h * 0.3, w, h * 0.4)),
                Detection(picture.label, 0.35, picture.xyxy),
                Detection(DocItemLabel.CAPTION, 0.9, (w * 0.2, h * 0.6, w * 0.8, h * 0.7))]
