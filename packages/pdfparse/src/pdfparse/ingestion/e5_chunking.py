"""Strict local multilingual-e5-large tokenizer adapter for structural chunking."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .structural import StructuralChunkingModel


def chunk_tokenizer_hash(path: Path) -> str:
    # Order and framing are part of the PdfParse / encoder files-v1 contract.
    digest = hashlib.sha256()
    for name in ("tokenizer.json", "tokenizer.model", "sentencepiece.bpe.model",
                 "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json"):
        file = path / name
        if file.is_file():
            digest.update(name.encode() + b"\0" + hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def build_e5_chunker(
    snapshot: str | Path | None = None,
    max_tokens: int = 512,
    *,
    target_tokens: int = 384,
    min_tokens: int = 128,
    respect_page_boundaries: bool = False,
    table_header_getter=None,
    local_files_only: bool = True,
) -> StructuralChunkingModel:
    from transformers import AutoTokenizer

    if snapshot is not None:
        path = Path(snapshot)
    else:
        from pdfparse.models.paths import model_directory
        path = model_directory("embeddings", "multilingual_e5_large")

    if not path.is_dir():
        raise FileNotFoundError(f"Local multilingual-e5-large snapshot not found: {path}")
    if min(max_tokens, target_tokens, min_tokens) < 1:
        raise ValueError("E5 token budgets must be positive")

    tokenizer = AutoTokenizer.from_pretrained(
        str(path), use_fast=True, local_files_only=local_files_only
    )
    if not getattr(tokenizer, "is_fast", False):
        raise ValueError("A fast E5 tokenizer is required for stable identity")

    config_file = path / "config.json"
    cfg = json.loads(config_file.read_text(encoding="utf-8")) if config_file.is_file() else {}
    positions = cfg.get("max_position_embeddings", 514)
    capacity = min(512, positions - cfg.get("pad_token_id", 1) - 1, tokenizer.model_max_length)
    ceiling = int(os.environ.get("E5_MAX_TOKENS") or capacity)
    if not 0 < ceiling <= capacity:
        raise ValueError(f"E5_MAX_TOKENS doit être compris entre 1 et {capacity}")
    max_tokens = min(max_tokens, ceiling)
    target_tokens = min(target_tokens, max_tokens)
    min_tokens = min(min_tokens, target_tokens)

    revision = "local"
    # Hugging Face cache snapshots store weights under snapshots/<commit_hash>
    if len(path.name) == 40 and all(c in "0123456789abcdefABCDEF" for c in path.name):
        revision = path.name.lower()
    revision = cfg.get("_commit_hash") or (cfg.get("revision") if revision == "local" else None) or revision

    token_identity_payload = json.dumps({
        "backend": tokenizer.backend_tokenizer.to_str(),
        "prefix": "passage: ",
        "bos_token": getattr(tokenizer, "bos_token", "<s>"),
        "eos_token": getattr(tokenizer, "eos_token", "</s>"),
        "unk_token": getattr(tokenizer, "unk_token", "<unk>"),
        "pad_token": getattr(tokenizer, "pad_token", "<pad>"),
        "add_special_tokens": True,
        "truncation": False,
        "revision": revision,
    }, sort_keys=True)
    fingerprint = hashlib.sha256(
        token_identity_payload.encode("utf-8")
    ).hexdigest()[:20]
    tokenizer_id = f"multilingual-e5-large:passage:{fingerprint}:files-v1:{chunk_tokenizer_hash(path)}"

    def count(text: str) -> int:
        return len(
            tokenizer.encode(
                "passage: " + text,
                add_special_tokens=True,
                truncation=False,
                verbose=False,
            )
        )

    return StructuralChunkingModel(
        chunk_size=max_tokens,
        target_tokens=target_tokens,
        min_tokens=min_tokens,
        count_tokens=count,
        tokenizer_id=tokenizer_id,
        respect_page_boundaries=respect_page_boundaries,
        table_header_getter=table_header_getter,
    )
