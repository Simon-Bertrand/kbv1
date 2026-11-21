"""Pinned official bundles. Downloading is separate from inference."""

from enum import StrEnum


from ..catalog import ModelCategory, ModelSource, ModelSpec

from .dfc_v1 import DFCV1PictureClassifier
from .dfc_v25 import DFCV25PictureClassifier


class PictureClassifierModel(StrEnum):
    DFC_V1 = "dfc_v1"
    DFC_V25 = "dfc_v25"


MODELS = {
    PictureClassifierModel.DFC_V1: ModelSpec(
        category=ModelCategory.PICTURE_CLASSIFIER,
        name=PictureClassifierModel.DFC_V1.value,
        implementation=DFCV1PictureClassifier,
        source=ModelSource(
            repo_id="docling-project/DocumentFigureClassifier",
            revision="e4e565c82ed64ce315b9ffcfba4cdfa975fb4cd4",
            files=("config.json", "model.safetensors"),
        ),
    ),
    PictureClassifierModel.DFC_V25: ModelSpec(
        category=ModelCategory.PICTURE_CLASSIFIER,
        name=PictureClassifierModel.DFC_V25.value,
        implementation=DFCV25PictureClassifier,
        source=ModelSource(
            repo_id="docling-project/DocumentFigureClassifier-v2.5",
            revision="f859dfbff5c9916cd996942d4b0db7fa25808220",
            files=("config.json", "model.safetensors", "preprocessor_config.json"),
        ),
    ),
}
