"""Categorized model library; runtimes remain unloaded until explicitly built."""
from importlib import import_module
from typing import TYPE_CHECKING

from .base import BaseInferenceModel, ModelConfiguration, ModelSettings
from .catalog import ModelCategory, ModelSource, ModelSpec

if TYPE_CHECKING:
    from .registry import ModelRegistry
    from .ocr import BaseOcrModel, BaseSpatialOcrModel, BaseTableOcrModel, OcrRegion
    from .vlm import BaseVlmModel
    from .layout import BaseLayoutDetector
    from .picture_classifier import DocumentClassifierModel

__all__ = ["BaseInferenceModel", "ModelConfiguration", "ModelSettings", "ModelCategory", "ModelSource",
           "ModelSpec", "ModelRegistry", "BaseLayoutDetector", "BaseOcrModel", "BaseSpatialOcrModel", "BaseTableOcrModel", "OcrRegion",
           "DocumentClassifierModel", "BaseVlmModel"]


def __getattr__(name: str):
    modules = {"ModelRegistry": ".registry", "BaseOcrModel": ".ocr", "BaseSpatialOcrModel": ".ocr",
               "BaseTableOcrModel": ".ocr", "OcrRegion": ".ocr", "BaseVlmModel": ".vlm",
               "BaseLayoutDetector": ".layout", "DocumentClassifierModel": ".picture_classifier"}
    if name in modules:
        value = getattr(import_module(modules[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(name)
