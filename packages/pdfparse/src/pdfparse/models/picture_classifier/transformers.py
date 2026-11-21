"""Local Transformers image classification, reusable with other fine-tunes."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from pydantic import JsonValue

from PIL import Image

from .base import DocumentClassifierModel, CategoryScore, PictureClassification
from ..base import ModelSettings


class TransformersPictureClassifier(DocumentClassifierModel):
    """Load a local model and processor once, with no Hub access or remote code.

    Other image classifiers can use this implementation directly with their own
    local bundle and model_id, or specialize the preprocessing hooks.
    """

    settings_model = ModelSettings

    def __init__(
        self, *, model_path: Path | str, model_id: str, device: str = "cpu",
        settings: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(model_path=model_path, device=device, settings=settings)
        path = self.model_path
        if not path.is_dir():
            raise ValueError("model_path must be a local model directory")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id must be a nonempty string")
        from transformers import AutoModelForImageClassification

        self.model_id = model_id
        self.processor = self._load_processor()
        self.runtime = AutoModelForImageClassification.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False,
        ).to(device).eval()
        if getattr(self.runtime.config, "problem_type", None) == "multi_label_classification":
            raise ValueError("DFC requires mutually exclusive categories, not multi-label output")
        labels = {int(key): value for key, value in self.runtime.config.id2label.items()}
        if set(labels) != set(range(self.runtime.config.num_labels)):
            raise ValueError("id2label must cover each output index exactly once")
        self.labels = tuple(labels[index] for index in range(len(labels)))
        if not self.labels or any(not isinstance(label, str) or not label.strip()
                                  for label in self.labels):
            raise ValueError("Model category labels must be nonempty strings")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("Model category labels must be unique")

    def _load_processor(self) -> Any:
        from transformers import AutoImageProcessor

        return AutoImageProcessor.from_pretrained(
            str(self.model_path), local_files_only=True, trust_remote_code=False,
        )

    def _prepare_inputs(self, images: Sequence[Image.Image]) -> dict[str, Any]:
        return dict(self.processor(images=list(images), return_tensors="pt"))

    def _predict_batch(self, images: Sequence[Image.Image]) -> list[PictureClassification]:
        import torch

        inputs = {
            key: tensor.to(self.device)
            for key, tensor in self._prepare_inputs(
                [image.convert("RGB") for image in images]
            ).items()
        }
        with torch.inference_mode():
            logits = self.runtime(**inputs).logits
            if tuple(logits.shape) != (len(images), len(self.labels)):
                raise ValueError("Unexpected classification logits shape")
            probabilities = logits.float().softmax(dim=-1).cpu().tolist()
        return [
            PictureClassification(
                model_id=self.model_id,
                scores=tuple(sorted(
                    (CategoryScore(label, float(score))
                     for label, score in zip(self.labels, row, strict=True)),
                    key=lambda prediction: prediction.confidence, reverse=True,
                )),
            )
            for row in probabilities
        ]
