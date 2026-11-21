"""The three supported models and reproducible official download sources."""

from enum import StrEnum

from ..catalog import ModelCategory, ModelSource, ModelSpec

from .heron import HeronLayoutDetector
from .heron_101 import Heron101LayoutDetector
from .pp_doclayout_s import PPDocLayoutSDetector


class LayoutModel(StrEnum):
    PP_DOCLAYOUT_S = "pp_doclayout_s"
    HERON = "heron"
    HERON_101 = "heron_101"


_TRANSFORMERS_FILES = ("config.json", "preprocessor_config.json", "model.safetensors")

MODELS = {
    LayoutModel.PP_DOCLAYOUT_S: ModelSpec(
        category=ModelCategory.LAYOUT,
        name=LayoutModel.PP_DOCLAYOUT_S.value,
        implementation=PPDocLayoutSDetector,
        source=ModelSource(
            repo_id="PaddlePaddle/PP-DocLayout-S",
            revision="8ac289e66575bb9bba6e15c53719d8b15cc9b3b2",
            files=("inference.json", "inference.yml", "inference.pdiparams"),
        ),
    ),
    LayoutModel.HERON: ModelSpec(
        category=ModelCategory.LAYOUT,
        name=LayoutModel.HERON.value,
        implementation=HeronLayoutDetector,
        source=ModelSource(
            repo_id="docling-project/docling-layout-heron",
            revision="8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
            files=_TRANSFORMERS_FILES,
        ),
    ),
    LayoutModel.HERON_101: ModelSpec(
        category=ModelCategory.LAYOUT,
        name=LayoutModel.HERON_101.value,
        implementation=Heron101LayoutDetector,
        source=ModelSource(
            repo_id="docling-project/docling-layout-heron-101",
            revision="2e4993cf6bb211112084a2f80938f26138008917",
            files=_TRANSFORMERS_FILES,
        ),
    ),
}
