"""Qwen3-VL-4B-Instruct with a bounded native image resolution."""
from pydantic import Field

from ..transformers import GenerationSettings
from .transformers import TransformersVlmModel


class Qwen3VlmSettings(GenerationSettings):
    batch_size: int = Field(default=1, ge=1)
    max_pixels: int = Field(default=1_048_576, ge=65_536)


class Qwen3VL4BVlm(TransformersVlmModel):
    settings_model = Qwen3VlmSettings
    model_type = "qwen3_vl"

    def _processor_kwargs(self) -> dict:
        return {"padding": True, "images_kwargs": {
            "size": {"shortest_edge": 65_536, "longest_edge": self.settings["max_pixels"]},
        }}
