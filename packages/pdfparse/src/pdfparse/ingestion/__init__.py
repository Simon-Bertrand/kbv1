"""PDF extraction, chunking contracts and their implementations."""
from .contracts import BaseChunkingModel, BasePdfExtractor, Chunk, PdfParseResult, SourceContext
from .chonkie import ChonkieChunkingModel
from .structural import StructuralChunkingModel
from .indexing import QualityReport, TextOrigin, IndexSnapshot
from .evaluation import EvidenceCase, evaluate_retrieval
from .docling import DoclingPdfExtractor

__all__ = ["BaseChunkingModel", "BasePdfExtractor", "Chunk", "PdfParseResult", "SourceContext",
           "ChonkieChunkingModel", "StructuralChunkingModel", "DoclingPdfExtractor", "QualityReport", "TextOrigin", "IndexSnapshot", "EvidenceCase", "evaluate_retrieval"]
