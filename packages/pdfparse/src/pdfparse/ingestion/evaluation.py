"""Offline evidence evaluation; rankings are supplied by the application."""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .contracts import PdfParseResult


@dataclass(frozen=True, slots=True)
class EvidenceCase:
    query_id: str
    question: str
    answer: str
    source_refs: tuple[str, ...]
    evidence: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.query_id, self.question, self.answer, self.evidence)):
            raise ValueError('Evidence cases require nonblank IDs, question, answer and evidence')
        if not self.source_refs or any(not isinstance(v, str) or not v.strip() for v in self.source_refs):
            raise ValueError('Evidence cases require source references')


def evaluate_retrieval(result: PdfParseResult, cases: Sequence[EvidenceCase], *,
                       rankings: Mapping[str, Sequence[str]] | None = None, k: int = 5,
                       retrieval_id: str | None = None) -> dict:
    """Measure evidence retention and, with external ranks, evidence hit@k/MRR.

    Relevance requires BOTH the annotated source refs and an exact evidence
    quote. No lexical surrogate is presented as an embedding evaluation.
    retrieval_id should identify embedding model/revision and search settings.
    """
    if type(k) is not int or k < 1:
        raise ValueError('k must be a positive integer')
    if not cases or len({case.query_id for case in cases}) != len(cases):
        raise ValueError('Provide nonempty cases with unique query IDs')
    if rankings is not None and (not retrieval_id or set(rankings) != {case.query_id for case in cases}):
        raise ValueError('Rankings require a retrieval_id and exactly the annotated query IDs')
    available = {chunk.chunk_id: chunk for chunk in result.chunks}
    observations = []
    for case in cases:
        if not set(case.source_refs).issubset(result.document.elements):
            raise ValueError(f'Unknown evidence source for {case.query_id}')
        relevant = {chunk.chunk_id for chunk in result.chunks
                    if set(case.source_refs).issubset(ref.cref for ref in chunk.source_refs)
                    and case.evidence in chunk.contextualized_text}
        rank = None
        if rankings is not None:
            hits = rankings[case.query_id]
            if isinstance(hits, str) or len(set(hits)) != len(hits) or any(hit not in available for hit in hits):
                raise ValueError('Rankings must contain unique known chunk IDs')
            rank = next((i for i, hit in enumerate(hits[:k], 1) if hit in relevant), None)
        observations.append({'query_id': case.query_id, 'evidence_retained': bool(relevant), 'rank': rank})
    return {'format': 'pdfparse.evaluation', 'schema_version': 1, 'document_id': result.document.document_id,
            'preparation_hash': result.preparation_manifest().get('manifest_hash'),
            'retrieval_id': retrieval_id, 'k': k, 'queries': observations,
            'evidence_retention': sum(row['evidence_retained'] for row in observations) / len(cases),
            'hit_at_k': sum(row['rank'] is not None for row in observations) / len(cases) if rankings is not None else None,
            'mrr_at_k': sum(1 / row['rank'] if row['rank'] else 0 for row in observations) / len(cases) if rankings is not None else None}


def compare_preparations(results: Mapping[str, PdfParseResult], cases: Sequence[EvidenceCase]) -> dict:
    """Compare chunkers on the same source PDF and annotations, without inference."""
    if not results or len({result.document.document_id for result in results.values()}) != 1:
        raise ValueError('Compare preparations of the same source document')
    return {name: {
        **evaluate_retrieval(result, cases),
        'chunk_count': len(result.chunks),
        'embedding_characters': sum(len(chunk.contextualized_text) for chunk in result.chunks),
        'coverage': result.quality_report().coverage,
    } for name, result in results.items()}
