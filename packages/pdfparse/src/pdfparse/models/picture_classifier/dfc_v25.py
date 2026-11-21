"""26-category EfficientNet-B0 DFC, including photograph and table."""

from pathlib import Path
from collections.abc import Mapping
from pydantic import JsonValue

from .transformers import TransformersPictureClassifier


class DFCV25PictureClassifier(TransformersPictureClassifier):
    def __init__(self, *, model_path: Path | str, device: str = "cpu",
                 settings: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(
            model_path=model_path, device=device, settings=settings,
            model_id="docling-project/DocumentFigureClassifier-v2.5",
        )
