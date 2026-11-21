"""MechVL-4B-RL specialization for mechanical drawings."""
from pydantic import Field

from .qwen3_vl_4b import Qwen3VL4BVlm, Qwen3VlmSettings


class MechVlmSettings(Qwen3VlmSettings):
    max_new_tokens: int = Field(default=2048, ge=1)


class MechVL4BRLVlm(Qwen3VL4BVlm):
    """Preserve native output, including any reasoning/answer tags."""

    settings_model = MechVlmSettings
    default_prompt = "Describe this mechanical drawing, its components, dimensions and annotations."
