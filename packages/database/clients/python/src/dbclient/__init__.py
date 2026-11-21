"""Typed calls to the RAG SQL functions, using an injected Psycopg connection."""
from collections.abc import Sequence
import json
import logging
import math
from typing import Any, Literal, TypedDict, cast

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_logger = logging.getLogger("dbclient")


class Chunk(TypedDict):
    chunk_id: str
    document_id: str
    filepath: str
    metadata: dict[str, Any]
    record: dict[str, Any]


class Visual(TypedDict):
    visual_id: str
    document_id: str
    filepath: str
    metadata: dict[str, Any]
    record: dict[str, Any]


class SearchHit(TypedDict):
    kind: Literal['chunk', 'visual']
    id: str
    score: float
    text_rank: int | None
    visual_rank: int | None
    document_id: str
    filepath: str
    metadata: dict[str, Any]
    evidence: dict[str, Any]


def _vector(value: Sequence[float] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        raise ValueError('Un vecteur doit être une séquence de nombres finis.')
    numbers = [float(component) for component in value]
    if not numbers or not all(math.isfinite(component) for component in numbers):
        raise ValueError('Un vecteur doit être non vide et contenir des nombres finis.')
    return json.dumps(numbers, allow_nan=False)


class RagClient:
    """Reuse a connection; the caller owns its lifetime and transaction policy.

    No commit, rollback, search_path change, or model/network initialization.
    SQL validation and Psycopg exceptions propagate to the application.
    """

    def __init__(self, connection: Connection, *, require_agent_user: bool = True):
        user = getattr(connection.info, "user", None)
        if require_agent_user and user is not None and user != "agent":
            raise PermissionError(
                f"RagClient requiert une connexion avec l'utilisateur PostgreSQL 'agent' (utilisateur actuel : '{user}'). "
                "L'agent RAG ne doit utiliser aucun autre utilisateur."
            )
        self.connection = connection

    def get_chunks(self, ids: Sequence[str]) -> list[Chunk]:
        """Read at most 100 IDs, in first-requested order, omitting missing IDs."""
        _logger.debug("get_chunks query", extra={"ids_count": len(ids)})
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('SELECT * FROM rag.rag_get_chunks(%s::text[])', (list(ids),))
            return cast(list[Chunk], cursor.fetchall())

    def get_visuals(self, ids: Sequence[str]) -> list[Visual]:
        """Read complete visual records, including their asset provenance."""
        _logger.debug("get_visuals query", extra={"ids_count": len(ids)})
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('SELECT * FROM rag.rag_get_visuals(%s::text[])', (list(ids),))
            return cast(list[Visual], cursor.fetchall())

    def search(
        self, *, text_query: Sequence[float] | None = None,
        visual_query: Sequence[float] | None = None,
        top_k: int = 10, candidates: int = 100,
        document_ids: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Search with already-encoded queries; None and an empty ID filter differ."""
        _logger.debug("rag_search query", extra={"top_k": top_k, "candidates": candidates,
                                                 "has_text": text_query is not None, "has_visual": visual_query is not None})
        parameters = (
            _vector(text_query), _vector(visual_query), top_k, candidates,
            None if document_ids is None else list(document_ids),
            None if metadata is None else Jsonb(metadata),
        )
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute('''SELECT * FROM rag.rag_search(
                p_text_query => %s::rag.vector,
                p_visual_query => %s::rag.vector,
                p_top_k => %s::integer,
                p_candidates => %s::integer,
                p_document_ids => %s::text[],
                p_metadata => %s::jsonb
            )''', parameters)
            return cast(list[SearchHit], cursor.fetchall())
