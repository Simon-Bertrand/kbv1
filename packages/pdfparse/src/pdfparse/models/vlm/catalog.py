"""Pinned VLM bundles, including every shard and local processor/tokenizer file."""
from enum import StrEnum

from ..catalog import ModelCategory, ModelSource, ModelSpec
from .granite_vision_4_1 import GraniteVision41Vlm
from .paddleocr_vl_1_6 import PaddleOcrVl16Vlm
from .qwen3_5_4b import Qwen35_4BVlm
from .qwen3_5_9b import Qwen35_9BVlm
from .qwen3_vl_4b import Qwen3VL4BVlm
from .mechvl_4b_rl import MechVL4BRLVlm


class VlmModel(StrEnum):
    QWEN3_VL_4B = "qwen3_vl_4b"
    MECHVL_4B_RL = "mechvl_4b_rl"
    QWEN3_5_4B = "qwen3_5_4b"
    QWEN3_5_9B = "qwen3_5_9b"
    GRANITE_VISION_4_1_4B = "granite_vision_4_1_4b"
    PADDLEOCR_VL_1_6 = "paddleocr_vl_1_6"


_QWEN_FILES = (
    "config.json", "model.safetensors.index.json", "preprocessor_config.json",
    "video_preprocessor_config.json", "chat_template.jinja", "tokenizer.json",
    "tokenizer_config.json", "vocab.json", "merges.txt",
)

MODELS = {
    VlmModel.QWEN3_VL_4B: ModelSpec(
        ModelCategory.VLM, VlmModel.QWEN3_VL_4B.value, Qwen3VL4BVlm,
        ModelSource(repo_id="Qwen/Qwen3-VL-4B-Instruct",
                    revision="ebb281ec70b05090aa6165b016eac8ec08e71b17",
                    files=("config.json", "generation_config.json", "model.safetensors.index.json",
                           "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
                           "preprocessor_config.json", "video_preprocessor_config.json", "chat_template.json",
                           "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt")),
    ),
    VlmModel.MECHVL_4B_RL: ModelSpec(
        ModelCategory.VLM, VlmModel.MECHVL_4B_RL.value, MechVL4BRLVlm,
        ModelSource(repo_id="XiaofengAlg/MechVL-4B-RL",
                    revision="aab86388368d86db23b93f2fdf6d9439a16cdbfe",
                    files=("config.json", "generation_config.json", "model.safetensors.index.json",
                           *(f"model-{i:05d}-of-00003.safetensors" for i in range(1, 4)),
                           "preprocessor_config.json", "video_preprocessor_config.json", "chat_template.jinja",
                           "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
                           "added_tokens.json", "special_tokens_map.json")),
    ),
    VlmModel.QWEN3_5_4B: ModelSpec(
        ModelCategory.VLM, VlmModel.QWEN3_5_4B.value, Qwen35_4BVlm,
        ModelSource(repo_id="Qwen/Qwen3.5-4B", revision="851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a",
                    files=(*_QWEN_FILES, *(f"model.safetensors-{i:05d}-of-00002.safetensors" for i in range(1, 3)))),
    ),
    VlmModel.QWEN3_5_9B: ModelSpec(
        ModelCategory.VLM, VlmModel.QWEN3_5_9B.value, Qwen35_9BVlm,
        ModelSource(repo_id="Qwen/Qwen3.5-9B", revision="c202236235762e1c871ad0ccb60c8ee5ba337b9a",
                    files=(*_QWEN_FILES, *(f"model.safetensors-{i:05d}-of-00004.safetensors" for i in range(1, 5)))),
    ),
    VlmModel.GRANITE_VISION_4_1_4B: ModelSpec(
        ModelCategory.VLM, VlmModel.GRANITE_VISION_4_1_4B.value, GraniteVision41Vlm,
        ModelSource(repo_id="ibm-granite/granite-vision-4.1-4b",
                    revision="37d591f06319e8f1638b5adcf58bdf50e0f84f7a",
                    files=("config.json", "model.safetensors.index.json",
                           "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors",
                           "generation_config.json", "processor_config.json", "preprocessor_config.json",
                           "chat_template.jinja", "tokenizer.json", "tokenizer_config.json",
                           "special_tokens_map.json", "vocab.json", "merges.txt")),
    ),
    VlmModel.PADDLEOCR_VL_1_6: ModelSpec(
        ModelCategory.VLM, VlmModel.PADDLEOCR_VL_1_6.value, PaddleOcrVl16Vlm,
        ModelSource(repo_id="PaddlePaddle/PaddleOCR-VL-1.6",
                    revision="c5630abae1d940eafe0697512a0325494b02ab42",
                    files=("config.json", "model.safetensors", "generation_config.json",
                           "processor_config.json", "preprocessor_config.json", "chat_template.jinja",
                           "tokenizer.json", "tokenizer.model", "tokenizer_config.json",
                           "special_tokens_map.json", "added_tokens.json")),
    ),
}
