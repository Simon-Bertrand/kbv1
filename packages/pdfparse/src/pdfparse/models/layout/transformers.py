"""Shared local Transformers inference for the Heron model family."""

from collections.abc import Sequence

from docling_core.types.doc import DocItemLabel
from PIL import Image

from .detector import BaseLayoutDetector, Detection
from ..base import ModelSettings, validate_images

class TransformersLayoutDetector(BaseLayoutDetector):
    """Load a local Heron model bundle without contacting the Hub."""

    settings_model = ModelSettings

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from transformers import AutoImageProcessor, AutoModelForObjectDetection

        self.processor = AutoImageProcessor.from_pretrained(
            str(self.model_path), local_files_only=True, trust_remote_code=False,
        )
        self.runtime = AutoModelForObjectDetection.from_pretrained(
            str(self.model_path), local_files_only=True, trust_remote_code=False,
        ).to(self.device).eval()
        self.labels = {
            int(key): DocItemLabel(value.strip().lower().replace("-", "_").replace(" ", "_"))
            for key, value in self.runtime.config.id2label.items()
        }

    def predict(self, image: Image.Image) -> list[Detection]:
        return self.predict_batch([image])[0]

    def predict_batch(self, images: Sequence[Image.Image]) -> list[list[Detection]]:
        import torch

        batch = validate_images(images)
        if not batch:
            return []
        inputs = self.processor(images=[image.convert("RGB") for image in batch],
                                return_tensors="pt").to(self.device)
        with torch.inference_mode():
            predictions = self.runtime(**inputs)
        results = self.processor.post_process_object_detection(
            predictions,
            target_sizes=[(image.height, image.width) for image in batch],
            threshold=0.0,
        )
        if len(results) != len(batch):
            raise ValueError("Layout model must return one result per input image")
        return [[
            Detection(
                label=self.labels[int(label)], confidence=float(score),
                xyxy=tuple(float(v) for v in box),
            )
            for label, score, box in zip(
                result["labels"].tolist(), result["scores"].tolist(), result["boxes"].tolist(),
                strict=True,
            )
        ] for result in results]
