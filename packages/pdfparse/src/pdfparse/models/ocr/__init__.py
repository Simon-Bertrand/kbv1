"""Crop-local OCR contract; applications register their chosen implementations."""
from abc import abstractmethod
from dataclasses import dataclass
from importlib import import_module
import math
from typing import TYPE_CHECKING
from PIL import Image
from ..base import BaseInferenceModel


class BaseOcrModel(BaseInferenceModel[str]):
    @abstractmethod
    def predict(self, image: Image.Image) -> str:
        """Return recognized text in reading order; raise on inference failure."""


@dataclass(frozen=True, slots=True)
class OcrRegion:
    """Recognized text and its TOPLEFT box in crop pixels."""

    text: str
    xyxy: tuple[float, float, float, float]

    def __post_init__(self):
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError('OCR regions require nonempty text')
        if len(self.xyxy) != 4 or not all(math.isfinite(v) for v in self.xyxy):
            raise ValueError('OCR region coordinates must be finite')
        l, t, r, b = self.xyxy
        if not (0 <= l < r and 0 <= t < b):
            raise ValueError('OCR regions require a positive TOPLEFT box')


class BaseSpatialOcrModel(BaseOcrModel):
    """Optional OCR capability for matching scanned text to table cells."""

    @abstractmethod
    def predict_regions(self, image: Image.Image) -> list[OcrRegion]:
        """Return text with real crop coordinates, never inferred row spacing."""


class BaseTableOcrModel(BaseOcrModel):
    """Optional crop-local recognition of a table's text and structure."""

    @abstractmethod
    def predict_table(self, image: Image.Image) -> 'TableData':
        """Return native table cells; leave unpredicted cell boxes unset."""


if TYPE_CHECKING:
    from docling_core.types.doc import TableData
    from .paddleocr_vl_1_6 import PaddleOcrVl16Ocr
    from .catalog import OcrModel
    from .mineru2_5_pro import MinerU25ProOcr
    from .got_ocr2 import GotOcr2Ocr
    from .pp_ocrv6_medium_rec import PPOcrV6MediumRec
    from .rapidocr_v4 import RapidOcrV4

_EXPORTS = {"OcrModel": ".catalog", "MinerU25ProOcr": ".mineru2_5_pro", "GotOcr2Ocr": ".got_ocr2",
            "PaddleOcrVl16Ocr": ".paddleocr_vl_1_6",
            "PPOcrV6MediumRec": ".pp_ocrv6_medium_rec", "RapidOcrV4": ".rapidocr_v4"}
__all__ = ["BaseOcrModel", "BaseSpatialOcrModel", "BaseTableOcrModel", "OcrRegion", *_EXPORTS]


def __getattr__(name: str):
    if name in _EXPORTS:
        value = getattr(import_module(_EXPORTS[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(name)
