"""Custom experiment types deliberately outside pdfparse's built-in catalogue."""

from dataclasses import dataclass, field

from pdfparse.enrich import EnrichedRef, EnrichedTextItem


@dataclass(frozen=True)
class SummarySettings:
    strategy: str
    source_refs: tuple[EnrichedRef, ...] = ()


@dataclass(kw_only=True, slots=True)
class SummaryTextItem(EnrichedTextItem):
    summary: str
    settings: SummarySettings = field(default_factory=lambda: SummarySettings("default"))


def enrichment_pipeline(*, handlers=None, wrappers=None, asset_store=None, picture_enrich=None):
    from pdfparse import PdfParseConfiguration, PdfParsePipeline, HandlerEnrichModel
    from pdfparse import WrappingPictureEnrichModel
    return PdfParsePipeline(PdfParseConfiguration(
        enrich=HandlerEnrichModel(handlers), wrappers=wrappers, asset_store=asset_store,
        picture_enrich=picture_enrich if picture_enrich is not None else WrappingPictureEnrichModel(),
    ))
