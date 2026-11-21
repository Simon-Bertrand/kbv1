"""PaddleOCR PP-OCRv6 recognition of individual text-line crops."""
from collections.abc import Mapping, Sequence
import os
from pathlib import Path

from PIL import Image
from pydantic import Field, JsonValue

from . import BaseOcrModel
from ..base import ModelSettings, validate_images


class PaddleRecognitionSettings(ModelSettings):
    batch_size: int = Field(default=16, ge=1)
    cpu_threads: int = Field(default=4, ge=1)
    enable_mkldnn: bool = False


class PPOcrV6MediumRec(BaseOcrModel):
    """Recognize one line per input; use a detector-backed OCR for multiline figures."""

    settings_model = PaddleRecognitionSettings

    def __init__(self, *, model_path: Path | str, device: str = "cpu",
                 settings: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(model_path=model_path, device=device, settings=settings)
        if self.device != "cpu" and self.device != "cuda" and not self.device.startswith("cuda:"):
            raise ValueError(f"PaddleOCR recognition does not support device {self.device!r}")
        for name in ("inference.json", "inference.pdiparams", "inference.yml"):
            if not (self.model_path / name).is_file():
                raise FileNotFoundError(self.model_path / name)
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        from paddleocr import TextRecognition

        self.runtime = TextRecognition(
            model_name="PP-OCRv6_medium_rec", model_dir=str(self.model_path),
            device=self.device.replace("cuda", "gpu", 1),
            cpu_threads=self.settings["cpu_threads"], enable_mkldnn=self.settings["enable_mkldnn"],
        )

    def predict(self, image: Image.Image) -> str:
        return self.predict_batch([image])[0]

    def predict_batch(self, images: Sequence[Image.Image]) -> list[str]:
        batch = validate_images(images)
        if not batch:
            return []
        import numpy as np

        texts: list[str] = []
        size = self.settings["batch_size"]
        for start in range(0, len(batch), size):
            crops = [np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
                     for image in batch[start:start + size]]
            predictions = list(self.runtime.predict(crops, batch_size=size))
            if len(predictions) != len(crops):
                raise ValueError("PaddleOCR must return one result per input line")
            for prediction in predictions:
                text = prediction["rec_text"]
                if not isinstance(text, str):
                    raise TypeError("PaddleOCR must return recognized strings")
                texts.append(text)
        return texts
