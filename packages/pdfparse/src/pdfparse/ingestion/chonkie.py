"""Chonkie adapter preserving source references across element boundaries."""
from __future__ import annotations
from typing import TYPE_CHECKING

from pdfparse.enrich import EnrichedDocument
from .text import prepare_text
from .contracts import BaseChunkingModel, Chunk

if TYPE_CHECKING:
    from chonkie.chunker.base import BaseChunker as ChonkieChunker


class ChonkieChunkingModel(BaseChunkingModel):
    """Chunk embedding representations in traversal order, joined by two LF.

    The default character tokenizer needs no download. Its budget counts
    characters, not LLM tokens. Inject any Chonkie chunker that returns exact
    character offsets into its input, with an explicit transformation version.
    Geometry conservatively includes every contributing source element.
    """

    def __init__(self, backend: ChonkieChunker | None = None, *,
                 chunk_size: int = 512, tokenizer: str = "character",
                 transformation_id: str | None = None) -> None:
        if backend is None:
            from chonkie import RecursiveChunker, __version__
            backend = RecursiveChunker(tokenizer=tokenizer, chunk_size=chunk_size)
            if transformation_id is None:
                transformation_id = f"pdfparse.chonkie.context.v7:{__version__}:recursive:{tokenizer}:{chunk_size}"
        if not isinstance(transformation_id, str) or not transformation_id.strip():
            raise ValueError("An injected Chonkie backend requires a transformation_id")
        self.backend = backend
        self.transformation_id = transformation_id

    def chunk(self, document: EnrichedDocument) -> list[Chunk]:
        prepared = prepare_text(document)
        if not prepared.blocks:
            return []
        chunks = []
        for part in self.backend.chunk(prepared.text):
            start, end = part.start_index, part.end_index
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(prepared.text)
                    or prepared.text[start:end] != part.text):
                raise ValueError("Chonkie must return exact character offsets and unchanged text")
            if part.text.strip():
                chunks.append(prepared.chunk(start, end, self.transformation_id))
        return chunks
