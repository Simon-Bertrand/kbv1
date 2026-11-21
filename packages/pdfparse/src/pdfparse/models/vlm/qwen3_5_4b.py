"""Qwen3.5 4B vision-language model."""
from typing import Literal

from .transformers import Qwen35Vlm, Qwen35VlmSettings


class Qwen35_4BSettings(Qwen35VlmSettings):
    quantization: Literal["none", "nf4"] = "nf4"


class Qwen35_4BVlm(Qwen35Vlm):
    """Compact general-purpose VLM for document figure enrichment."""

    settings_model = Qwen35_4BSettings
