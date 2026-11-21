"""Vision-language contract; no provider is selected implicitly."""
from abc import abstractmethod
from collections.abc import Sequence
from importlib import import_module
from typing import TYPE_CHECKING
from PIL import Image
from ..base import BaseInferenceModel, validate_images


class BaseVlmModel(BaseInferenceModel[str]):
    @abstractmethod
    def predict(self, image: Image.Image, *, prompt: str | None = None) -> str:
        """Return generated text; None uses the model's default task prompt."""

    def predict_batch(self, images: Sequence[Image.Image], *, prompt: str | None = None) -> list[str]:
        return [self.predict(image) if prompt is None else self.predict(image, prompt=prompt)
                for image in validate_images(images)]


if TYPE_CHECKING:
    from .qwen3_vl_4b import Qwen3VL4BVlm
    from .mechvl_4b_rl import MechVL4BRLVlm
    from .catalog import VlmModel
    from .qwen3_5_4b import Qwen35_4BVlm
    from .qwen3_5_9b import Qwen35_9BVlm
    from .granite_vision_4_1 import GraniteVision41Vlm
    from .paddleocr_vl_1_6 import PaddleOcrVl16Vlm

_EXPORTS = {"VlmModel": ".catalog", "Qwen35_4BVlm": ".qwen3_5_4b",
            "Qwen3VL4BVlm": ".qwen3_vl_4b", "MechVL4BRLVlm": ".mechvl_4b_rl",
            "Qwen35_9BVlm": ".qwen3_5_9b", "GraniteVision41Vlm": ".granite_vision_4_1",
            "PaddleOcrVl16Vlm": ".paddleocr_vl_1_6"}
__all__ = ["BaseVlmModel", *_EXPORTS]


def __getattr__(name: str):
    if name in _EXPORTS:
        value = getattr(import_module(_EXPORTS[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(name)
