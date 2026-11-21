"""Backend contract independent of Docling's page processing lifecycle."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docling.datamodel.pipeline_options import BaseLayoutOptions
from docling_core.types.doc import DocItemLabel
from PIL import Image

from ..base import BaseInferenceModel

if TYPE_CHECKING:
    from .options import DoclingLayoutOptions


@dataclass(frozen=True, slots=True)
class Detection:
    """A region in image pixels, with an XYXY box and TOPLEFT origin."""

    label: DocItemLabel
    confidence: float
    xyxy: tuple[float, float, float, float]


class BaseLayoutModel(ABC):
    """Supply native or custom detector options to StandardPdfPipeline."""

    @abstractmethod
    def get_options(self) -> BaseLayoutOptions: ...


class BaseLayoutDetector(BaseInferenceModel[Sequence[Detection]], BaseLayoutModel):
    """Implement predict(image); construction is shared with OCR/classifier/VLM."""

    def get_options(self) -> DoclingLayoutOptions:
        from .options import DoclingLayoutOptions
        return DoclingLayoutOptions(
            model_path=self.model_path, device=self.device, detector_class=type(self),
            detector_settings=self.settings, detector_instance=self, instance_key=f"{id(self):x}",
        )

    @abstractmethod
    def predict(self, image: Image.Image) -> Sequence[Detection]:
        """Return Docling labels, finite [0, 1] scores and TOPLEFT pixel boxes."""
