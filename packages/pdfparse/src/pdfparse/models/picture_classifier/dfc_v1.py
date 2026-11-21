"""Original 16-category EfficientNet-B0 document figure classifier."""

from collections.abc import Sequence
from pathlib import Path
from collections.abc import Mapping
from pydantic import JsonValue
from typing import Any

from PIL import Image

from .transformers import TransformersPictureClassifier


class DFCV1PictureClassifier(TransformersPictureClassifier):
    def __init__(self, *, model_path: Path | str, device: str = "cpu",
                 settings: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(
            model_path=model_path, device=device, settings=settings,
            model_id="docling-project/DocumentFigureClassifier",
        )

    def _load_processor(self) -> Any:
        # The original bundle has no preprocessor_config.json. Reproduce the
        # upstream docling-ibm-models v3.4.0 predictor, including its unusual std.
        from torchvision import transforms

        return transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.47853944, 0.4732864, 0.47434163],
            ),
        ])

    def _prepare_inputs(self, images: Sequence[Image.Image]) -> dict[str, Any]:
        import torch

        return {"pixel_values": torch.stack([self.processor(image) for image in images])}
