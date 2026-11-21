"""Typed ingestion ports and source-preserving chunk data."""
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from docling_core.types.doc import DoclingDocument
from pdfparse.enrich import ElementLocation, EnrichedDocument, EnrichedRef

if TYPE_CHECKING:
    from .indexing import QualityReport, IndexSnapshot


class BasePdfExtractor(ABC):
    """Extract a complete document or raise; document-wide OCR stays disabled."""

    @abstractmethod
    def extract(self, pdfpath: Path | str) -> DoclingDocument: ...

    def extract_with_evidence(self, pdfpath: Path | str) -> ExtractionResult:
        return ExtractionResult(self.extract(pdfpath))


@dataclass(frozen=True, slots=True)
class ExtractionRegion:
    page_no: int
    bbox: tuple[float, float, float, float]
    label: str
    source_refs: tuple[str, ...]
    text_origin: str
    layout_confidence: float | None = None
    ocr_engine: str | None = None
    recognition_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    document: DoclingDocument
    regions: tuple[ExtractionRegion, ...] = ()
    evidence_available: bool = False


def logical_pdf_path(pdfpath, source_path=None):
    path = Path(pdfpath).expanduser()
    if source_path is None:
        return path.resolve(strict=True)
    path = path.absolute()
    if path != path.resolve():
        raise ValueError('The logical PDF path must remain canonical')
    return path


def extract_source(extractor, pdfpath, source_path=None):
    """Read a snapshot while retaining the source name produced for a live PDF."""
    if source_path is None or source_path == pdfpath:
        return extractor.extract_with_evidence(pdfpath)
    result = extractor.extract_with_evidence(source_path)
    document = result.document
    if document.name == source_path.stem:
        document.name = Path(pdfpath).stem
    if document.origin is not None and document.origin.filename == source_path.name:
        document.origin.filename = Path(pdfpath).name
    return result


@dataclass(frozen=True, slots=True)
class StageMetrics:
    extraction_seconds: float
    enrichment_seconds: float
    chunking_seconds: float
    pages_per_second: float | None
    process_peak_rss_bytes: int | None = None
    inferences: list[dict] = field(default_factory=list)



@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    source_refs: tuple[EnrichedRef, ...]
    locations: tuple[ElementLocation, ...]
    transformation_id: str
    context: str = ""
    start_index: int | None = None
    end_index: int | None = None
    body_refs: tuple[EnrichedRef, ...] = ()
    section_refs: tuple[EnrichedRef, ...] = ()
    split_reason: str | None = None
    token_count: int | None = None
    tokenizer_id: str | None = None

    @property
    def contextualized_text(self) -> str:
        """Retrieval representation; text remains the exact Chonkie slice."""
        return f"{self.context}\n\n{self.text}" if self.context else self.text


class BaseChunkingModel(ABC):
    """Consume a valid document; return ordered chunks with resolvable sources.

    Empty documents yield []. Enrichment owns full graph validation; chunking
    must preserve the source graph. Each chunk has nonblank text, ID and
    transformation, unique source references and exactly their combined locations
    (including content_locations). Context is text and its sources are included.
    """

    @abstractmethod
    def chunk(self, document: EnrichedDocument) -> list[Chunk]: ...


@dataclass(frozen=True, slots=True)
class SourceContext:
    """Bounded source text for selected hits, with conservative PDF citations."""

    document_id: str
    text: str
    source_refs: tuple[EnrichedRef, ...]
    locations: tuple[ElementLocation, ...]
    chunk_ids: tuple[str, ...]
    expanded: bool


@dataclass(frozen=True, slots=True)
class ResultProjection:
    """Immutable projections of the validated result at the end of a run."""
    document_json: str
    markdown: str
    index: IndexSnapshot


@dataclass(frozen=True, slots=True)
class PdfParseResult:
    document: EnrichedDocument
    chunks: tuple[Chunk, ...]
    page_numbers: tuple[int, ...] | None = None
    extraction: tuple[ExtractionRegion, ...] = ()
    extraction_evidence_available: bool = False
    manifest_json: str | None = None
    metrics: StageMetrics | None = None
    projection: ResultProjection | None = field(default=None, repr=False, compare=False)

    def preparation_manifest(self) -> dict:
        import json
        return json.loads(self.manifest_json) if self.manifest_json else {}


    def source_context(self, chunk_ids: Iterable[str], *, max_characters: int = 12_000) -> SourceContext:
        """Merge exact overlaps, then expand source blocks within the budget.

        The hard budget includes separators and context, and counts characters.
        Raises if even the selected excerpts exceed it; never silently drops hits.
        """
        from .context import build_source_context
        return build_source_context(self, chunk_ids, max_characters=max_characters)

    def prepare_index(self, *, language: str | None = None, text_origins: Mapping[str, str] | None = None) -> IndexSnapshot:
        """Build quality and export records from one canonical text traversal."""
        from .indexing import prepare_index
        return prepare_index(self, language=language, text_origins=text_origins)

    def quality_report(self) -> QualityReport:
        """Measure coverage and flag extraction anomalies without model calls."""
        from .indexing import assess_quality
        return assess_quality(self)

    def index_records(self, *, language: str | None = None,
                      text_origins: Mapping[str, str] | None = None) -> Iterator[dict[str, Any]]:
        """Versioned records; language and per-element OCR origin are explicit."""
        from .indexing import index_records
        return index_records(self, language=language, text_origins=text_origins)

    def export_jsonl(self, path: Path | str, *, language: str | None = None,
                     text_origins: Mapping[str, str] | None = None) -> None:
        """Atomically export index records; never leave a partially written file."""
        from .indexing import export_jsonl
        export_jsonl(self, path, language=language, text_origins=text_origins)

    def visual_records(self) -> Iterator[dict[str, Any]]:
        """Every extracted figure, its asset locator, provenance and related chunks."""
        from .indexing import visual_records
        return visual_records(self)

    def export_visual_jsonl(self, path: Path | str) -> None:
        from .indexing import _write_records
        _write_records(self.visual_records(), path)
