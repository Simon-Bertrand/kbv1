"""Standalone DFC models: no enrichment handlers or Docling lifecycle dependencies."""

from pathlib import Path

from .base import DocumentClassifierModel, CategoryScore, PictureClassification
from .catalog import PictureClassifierModel, MODELS
from .dfc_v1 import DFCV1PictureClassifier
from .dfc_v25 import DFCV25PictureClassifier
from .transformers import TransformersPictureClassifier

__all__ = [
    "DocumentClassifierModel", "CategoryScore", "PictureClassification",
    "PictureClassifierModel", "DFCV1PictureClassifier", "DFCV25PictureClassifier",
    "TransformersPictureClassifier", "create_picture_classifier",
]


def create_picture_classifier(
    model: PictureClassifierModel | str = PictureClassifierModel.DFC_V25,
    *, model_path: Path | str, device: str = "cpu",
) -> DocumentClassifierModel:
    spec = MODELS[PictureClassifierModel(model)]
    return spec.implementation(model_path=model_path, device=device)
