"""Composition root for the replaceable PdfParse strategies."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions

from .enrich import AssetStore, WrapperRegistry
from .enrich.pages import BasePageEnrichModel
from .enrich.model import (
    BaseEnrichModel, BasePictureEnrichModel, HandlerEnrichModel, WrappingPictureEnrichModel,
)
from .ingestion.chonkie import ChonkieChunkingModel
from .ingestion.contracts import BaseChunkingModel, BasePdfExtractor
from .ingestion.docling import DoclingPdfExtractor
from .models.layout.options import BaseLayoutModel
from .models.layout.options import DoclingLayoutOptions, NativeOcrLayoutOptions
from .models.ocr import BaseOcrModel


class PdfParseConfiguration(BaseModel):
    """Runtime composition, not a serializable model or document.

    Handler models snapshot registrations at construction; injected services
    are shared by reference. Use one configuration per worker for stateful models.
    None layout preserves Docling's native Heron default.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True, frozen=True, extra="forbid", validate_default=True,
    )

    cache: bool = Field(default=True, strict=True)
    cache_dir: Path | None = None
    cache_identity: dict[str, JsonValue] | None = None
    cache_max_entries: int = Field(default=8, strict=True, ge=1)
    cache_max_bytes: int = Field(default=512 * 1024 * 1024, strict=True, ge=1)
    cache_max_age_seconds: int | None = Field(default=None, strict=True, ge=1)
    layout: BaseLayoutModel | None = None
    text_ocr: BaseOcrModel | None = None
    enrich: BaseEnrichModel = Field(default_factory=HandlerEnrichModel)
    picture_enrich: BasePictureEnrichModel = Field(default_factory=WrappingPictureEnrichModel)
    page_enrich: BasePageEnrichModel | None = None
    chunking: BaseChunkingModel = Field(default_factory=ChonkieChunkingModel)
    asset_store: AssetStore | None = None
    wrappers: WrapperRegistry | None = None

    @model_validator(mode='after')
    def text_ocr_layout(self):
        if self.text_ocr is not None and self.layout is not None and (
            not isinstance(self.layout.get_options(), (DoclingLayoutOptions, LayoutObjectDetectionOptions))
        ):
            raise ValueError('text_ocr requires native Docling or DoclingLayoutOptions')
        return self

    def create_extractor(self) -> BasePdfExtractor:
        layout = self.layout.get_options() if self.layout is not None else None
        if self.text_ocr is not None:
            if layout is None or isinstance(layout, LayoutObjectDetectionOptions):
                native = layout if layout is not None else LayoutObjectDetectionOptions()
                layout = NativeOcrLayoutOptions(
                    **{name: getattr(native, name) for name in LayoutObjectDetectionOptions.model_fields},
                    text_ocr=self.text_ocr,
                )
            else:
                layout = layout.model_copy(update={'text_ocr': self.text_ocr, 'text_ocr_key': f'{id(self.text_ocr):x}'})
        return DoclingPdfExtractor(layout=layout, page_images=self.page_enrich is not None)
