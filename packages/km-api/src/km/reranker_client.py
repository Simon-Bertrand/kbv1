"""Optional, bounded HTTP reranking; no inference dependencies in KM API."""

import json
import math
import os
import re
from urllib.parse import urlsplit

import httpx
from ._service_logging import upstream_headers


class RerankerUnavailable(RuntimeError):
    pass


class DirectSearchUnsupported(RerankerUnavailable):
    pass


def validate_model(data):
    if (
        not isinstance(data.get("model"), str)
        or not data["model"]
        or not re.fullmatch("[a-f0-9]{40}", data.get("revision", ""))
        or not re.fullmatch("[a-f0-9]{64}", data.get("fingerprint", ""))
        or data.get("score_kind") not in ("logit_difference", "classifier_logit", "yes_logit")
    ):
        raise ValueError("Incompatible reranker model identity")


class RerankerClient:
    def __init__(self, url=None, *, transport=None, token=None):
        origin = url or os.environ.get("RERANKER_URL", "")
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("RERANKER_URL must be an HTTP(S) origin")
        timeout = float(os.environ.get("RERANKER_TIMEOUT_SECONDS", "20"))
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("RERANKER_TIMEOUT_SECONDS must be in (0, 300]")
        self.token = token if token is not None else os.environ.get("KM_RERANKER_TOKEN", "")
        self.http = httpx.Client(
            base_url=origin.rstrip("/"),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(timeout, connect=3),
        )

    def _post(self, path, payload, max_bytes):
        headers = upstream_headers()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with self.http.stream("POST", path, headers=headers, json=payload) as response:
            response.raise_for_status()
            if response.headers.get("content-type", "").split(";")[0] != "application/json":
                raise ValueError("Expected JSON")
            body = bytearray()
            for block in response.iter_bytes():
                body.extend(block)
                if len(body) > max_bytes:
                    raise ValueError("Reranker response too large")
        return json.loads(body)

    def rank(self, query, documents):
        try:
            data = self._post("/rerank", {"query": query, "documents": documents, "top_n": len(documents)}, 256 * 1024)
            if data.get("contract") != "km-reranker-v1":
                raise ValueError("Incompatible reranker contract")
            validate_model(data)
            ranked = data["results"]
            if not isinstance(ranked, list) or len(ranked) != len(documents):
                raise ValueError("Reranker must return every original index")
            seen = set()
            previous = math.inf
            for item in ranked:
                index, score = item["index"], item["relevance_score"]
                if (
                    type(index) is not int
                    or not 0 <= index < len(documents)
                    or index in seen
                    or type(score) not in (float, int)
                    or not math.isfinite(score)
                    or score > previous
                ):
                    raise ValueError("Invalid indexes or scores")
                seen.add(index)
                previous = score
            return data
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RerankerUnavailable("Reranker unavailable or incompatible response") from exc

    def search(self, request):
        try:
            data = self._post("/search", request, 8 * 1024 * 1024)
            hits = data["results"]
            reranking = data["reranking"]
            if (
                data.pop("contract", None) != "km-reranker-search-v1"
                or data.get("backend") != "postgres"
                or data.get("mode") != request["mode"]
                or not isinstance(hits, list)
                or len(hits) > request["limit"]
                or ((request["document_ids"] or request["metadata_filters"]) and data.get("scope_applied") is not True)
                or type(reranking.get("applied")) is not bool
            ):
                raise ValueError("Incompatible direct search response")
            if reranking["applied"]:
                validate_model(reranking)
            elif request["failure_mode"] == "error" and reranking.get("reason") == "unavailable":
                raise ValueError("Reranking unavailable")
            for hit in hits:
                if (not isinstance(hit["page_id"], str) or not isinstance(hit["document_id"], str)
                    or not isinstance(hit["text"], str) or not isinstance(hit["locator"], dict)
                    or type(hit["score"]) not in (int, float) or not math.isfinite(hit["score"])):
                    raise ValueError("Invalid search result")
            return data
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 501):
                raise DirectSearchUnsupported("Direct search unavailable on this reranker") from exc
            if exc.response.status_code == 422:
                raise ValueError("Invalid direct RAG search") from exc
            raise RerankerUnavailable("Direct RAG search failed") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise RerankerUnavailable("Direct RAG search unavailable or incompatible") from exc

    def close(self):
        self.http.close()


def rerank_hits(result, query, limit, client, *, failure_mode="fallback"):
    return rank_search_hits(result, query, limit, lambda question, documents: client.rank(question, documents),
                            failure_mode=failure_mode)


def rank_search_hits(result, query, limit, rank, *, failure_mode="fallback"):
    hits = result["results"]
    eligible = [i for i, hit in enumerate(hits) if isinstance(hit.get("text"), str) and hit["text"].strip()]
    if not eligible:
        return {**result, "results": hits[:limit], "reranking": {"applied": False, "reason": "no_text"}}
    try:
        ranked = rank(query, [hits[i]["text"] for i in eligible])
    except RerankerUnavailable:
        if failure_mode == "error":
            raise
        return {**result, "results": hits[:limit], "reranking": {"applied": False, "reason": "unavailable"}}
    output = []
    for item in ranked["results"]:
        original = eligible[item["index"]]
        output.append(
            {**hits[original], "rerank_score": item["relevance_score"], "retrieval_rank": original + 1}
        )
    skipped = set(eligible)
    output.extend(hit for i, hit in enumerate(hits) if i not in skipped)
    return {
        **result,
        "results": output[:limit],
        "reranking": {
            "applied": True,
            "candidate_count": len(hits),
            "scored_count": len(eligible),
            **{key: ranked[key] for key in ("model", "revision", "fingerprint", "score_kind")},
        },
    }
