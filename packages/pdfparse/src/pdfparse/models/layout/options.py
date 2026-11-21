"""Layout strategies, validated Docling options and built-in model selection."""

import inspect
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from docling.datamodel.pipeline_options import BaseLayoutOptions, LayoutObjectDetectionOptions, TableStructureOptions
from pydantic import ConfigDict, Field, ImportString, JsonValue, TypeAdapter, field_validator, model_validator

from ..base import Device
from ..ocr import BaseOcrModel, BaseTableOcrModel
from .detector import BaseLayoutDetector, BaseLayoutModel
from .catalog import LayoutModel, MODELS


class NativeLayoutModel(BaseLayoutModel):
    """Select a native preset or snapshot explicit Docling options."""

    def __init__(self, options: BaseLayoutOptions | LayoutModel | str = LayoutModel.HERON) -> None:
        if isinstance(options, str):
            options = get_layout_options(options)
        if not isinstance(options, BaseLayoutOptions):
            raise TypeError("options must be BaseLayoutOptions")
        self._options = options.model_copy(deep=True)

    def get_options(self) -> BaseLayoutOptions:
        return self._options.model_copy(deep=True)


class SpatialTableOptions(TableStructureOptions):
    """Use explicit structured/spatial OCR on scans; retain TableFormer otherwise."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    kind: ClassVar[str] = 'pdfparse_spatial_tables'
    spatial_index_version: Literal['ocr-baselines.v1'] = 'ocr-baselines.v1'
    table_ocr: BaseTableOcrModel | None = Field(default=None, exclude=True, repr=False)
    table_ocr_key: str | None = None

    @model_validator(mode='after')
    def bind_table_ocr(self):
        self.table_ocr_key = f'{id(self.table_ocr):x}' if self.table_ocr is not None else None
        return self

    def __deepcopy__(self, memo=None):
        memo = {} if memo is None else memo
        if self.table_ocr is not None:
            memo[id(self.table_ocr)] = self.table_ocr
        return super().__deepcopy__(memo)


class DoclingLayoutOptions(BaseLayoutOptions):
    """Select a detector class directly or using ``package.module:Class``.

    Classes serialize as import paths, so Docling's pipeline cache distinguishes
    different backends and settings. Use module-level classes for round trips.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: ClassVar[str] = "pdfparse_custom_layout"
    model_path: Path
    device: Device = "cpu"
    detector_instance: BaseLayoutDetector | None = Field(default=None, exclude=True, repr=False)
    text_ocr: BaseOcrModel | None = Field(default=None, exclude=True, repr=False)
    text_ocr_key: str | None = None
    instance_key: str | None = None
    detector_class: ImportString[type[BaseLayoutDetector]]
    confidence_threshold: float = Field(default=0.35, ge=0, le=1)
    detector_settings: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bind_runtime(self):
        runtime = self.detector_instance
        if runtime is not None:
            if (type(runtime) is not self.detector_class or runtime.model_path != self.model_path.resolve()
                    or runtime.device != self.device or runtime.settings != self.detector_settings):
                raise ValueError("Injected layout runtime differs from its configured class/path/device/settings")
            self.instance_key = f"{id(runtime):x}"
        self.text_ocr_key = f"{id(self.text_ocr):x}" if self.text_ocr is not None else None
        return self

    def __deepcopy__(self, memo=None):
        memo = {} if memo is None else memo
        if self.detector_instance is not None:
            memo[id(self.detector_instance)] = self.detector_instance
        if self.text_ocr is not None:
            memo[id(self.text_ocr)] = self.text_ocr
        return super().__deepcopy__(memo)

    @field_validator("detector_class")
    @classmethod
    def concrete_detector(
        cls, detector: type[BaseLayoutDetector]
    ) -> type[BaseLayoutDetector]:
        if inspect.isabstract(detector):
            raise ValueError("detector_class must implement BaseLayoutDetector.predict")
        return detector


class NativeOcrLayoutOptions(LayoutObjectDetectionOptions):
    """Use local native Docling weights and optionally recognize empty crops."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    kind: ClassVar[str] = 'pdfparse_native_layout_ocr'
    text_ocr: BaseOcrModel | None = Field(default=None, exclude=True, repr=False)
    text_ocr_key: str = ''

    @model_validator(mode='after')
    def bind_ocr(self):
        self.text_ocr_key = f'{id(self.text_ocr):x}' if self.text_ocr is not None else ''
        return self

    def __deepcopy__(self, memo=None):
        memo = {} if memo is None else memo
        if self.text_ocr is not None:
            memo[id(self.text_ocr)] = self.text_ocr
        return super().__deepcopy__(memo)


def get_layout_options(
    model: LayoutModel | str = LayoutModel.HERON,
    *,
    model_path: Path | str | None = None,
    confidence_threshold: float | None = None,
) -> BaseLayoutOptions:
    """Select one model for ``PdfPipelineOptions.layout_options``.

    Heron and Heron-101 use Docling's native presets unless a local bundle is
    supplied. PP-DocLayout-S always requires its local Paddle model directory
    and ``allow_external_plugins=True`` on the PDF pipeline. Selecting options
    does not load runtimes or download weights.
    """
    selected = LayoutModel(model)
    if selected == LayoutModel.PP_DOCLAYOUT_S and model_path is None:
        raise ValueError("pp_doclayout_s requires model_path pointing to a local model directory")
    # Reuse the validated shared threshold field for native and custom engines.
    if confidence_threshold is not None:
        confidence_threshold = TypeAdapter(Annotated[float, Field(ge=0, le=1)]).validate_python(
            confidence_threshold
        )
    if model_path is not None:
        detector = MODELS[selected].implementation
        return DoclingLayoutOptions(
            model_path=Path(model_path), detector_class=detector,
            **({'confidence_threshold': confidence_threshold} if confidence_threshold is not None else {}),
        )
    preset = "layout_heron_default" if selected == LayoutModel.HERON else "layout_heron_101"
    options = LayoutObjectDetectionOptions.from_preset(preset)
    if confidence_threshold is not None:
        options.engine_options.score_threshold = confidence_threshold
    return options
