"""VLM prompt strategies on the shared image-to-text runtime."""
from collections.abc import Sequence

from PIL import Image
from pydantic import Field

from . import BaseVlmModel
from ..transformers import GenerationSettings, TransformersImageTextModel


class TransformersVlmModel(TransformersImageTextModel, BaseVlmModel):
    def predict(self, image: Image.Image, *, prompt: str | None = None) -> str:
        return self.predict_batch([image], prompt=prompt)[0]

    def predict_batch(self, images: Sequence[Image.Image], *, prompt: str | None = None,
                      prompts: Sequence[str | None] | None = None) -> list[str]:
        if prompts is not None:
            if prompt is not None:
                raise ValueError('Use prompt or prompts, not both')
            return self._predict_batch(images, [self.default_prompt if p is None else p for p in prompts])
        return self._predict_batch(images, self.default_prompt if prompt is None else prompt)


class QwenVlmSettings(GenerationSettings):
    enable_thinking: bool = False


class Qwen35VlmSettings(QwenVlmSettings):
    max_pixels: int = Field(default=262144, ge=65536)


class Qwen35Vlm(TransformersVlmModel):
    settings_model = Qwen35VlmSettings
    model_type = "qwen3_5"

    def _processor_kwargs(self) -> dict:
        return {'padding': True, 'images_kwargs': {
            'size': {'shortest_edge': 65536, 'longest_edge': self.settings['max_pixels']},
        }}

    def _template_kwargs(self) -> dict:
        return {"enable_thinking": self.settings["enable_thinking"]}
