"""Internal bounded E5 inference service with weight hash & dimension verification."""
import hashlib
import hmac
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import Request, FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from km._service_logging import RequestIdMiddleware, accept_upstream

DEFAULT_MAX_TOKENS = 512
MAX_TOKENS = DEFAULT_MAX_TOKENS


class EncodeRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=16)
    role: Literal['query', 'passage']
    request_id: str = Field(default='', max_length=128)


def verify_bearer_token(authorization: str | None, expected_token: str | None, kb_env: str = "dev") -> bool:
    """Constant-time bearer token check."""
    normalized_env = (kb_env or 'dev').strip().lower()
    is_prod = normalized_env in ("production", "prod")
    if not expected_token:
        if is_prod:
            return False
        return True
    if not authorization or not authorization.startswith("Bearer "):
        return False
    provided = authorization[7:].strip()
    return hmac.compare_digest(provided, expected_token)


def compute_weights_hash(root: Path) -> str:
    """Compute deterministic sha256 hash covering all weight shards, configs, and tokenizer files.
    Validates that sharded weights are complete according to index.json if present."""
    if not root.is_dir():
        raise RuntimeError("Local E5 snapshot missing")

    root_resolved = root.resolve()

    # If an index file is present, ensure all referenced shards exist, are non-empty, and inside root
    for index_name in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        index_file = root / index_name
        if index_file.is_file():
            try:
                raw_text = index_file.read_text(encoding="utf-8")
                index_data = json.loads(raw_text)
                if not isinstance(index_data, dict):
                    raise RuntimeError(f"Invalid weight index file {index_name}: not a JSON object")
                weight_map = index_data.get("weight_map")
                if not weight_map or not isinstance(weight_map, dict):
                    raise RuntimeError(f"Invalid weight index file {index_name}: empty or missing weight_map")
                shards = set(weight_map.values())
                if not shards:
                    raise RuntimeError(f"Invalid weight index file {index_name}: no shards referenced")
                for shard in sorted(shards):
                    if not isinstance(shard, str) or not shard.strip():
                        raise RuntimeError(f"Invalid shard reference in {index_name}")
                    shard_path = (root / shard).resolve()
                    if not shard_path.is_relative_to(root_resolved):
                        raise RuntimeError(f"Security error: shard '{shard}' in {index_name} points outside snapshot")
                    if not shard_path.is_file() or shard_path.stat().st_size == 0:
                        raise RuntimeError(f"E5 local weights incomplete: shard '{shard}' referenced in {index_name} is missing or empty")
            except (json.JSONDecodeError, KeyError) as e:
                raise RuntimeError(f"Invalid weight index file {index_name}: {e}") from e

    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix in {".safetensors", ".bin", ".json", ".model", ".txt", ".pt"}
    )
    # Must contain real weights (.safetensors, .bin, .pt) - tokenizer.model alone does NOT count as weights!
    has_real_weights = any(
        p.suffix in {".safetensors", ".bin", ".pt"} and p.name != "tokenizer.model"
        for p in files
    )
    if not has_real_weights:
        raise RuntimeError("E5 local weights missing: no .safetensors, .bin or .pt weight files found")

    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        with path.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())

    return digest.hexdigest()


def derive_model_capacity(root: Path, tokenizer, model) -> int:
    """Derive maximum sequence length capacity from config and tokenizer without exceeding XLM-R positions."""
    capacity = DEFAULT_MAX_TOKENS
    config_path = root / "config.json"
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                raise RuntimeError("config.json must be a JSON object")
            max_pos = cfg.get("max_position_embeddings")
            pad_id = cfg.get("pad_token_id", 1)
            if max_pos is not None:
                if not isinstance(max_pos, int) or max_pos <= 0:
                    raise RuntimeError(f"Invalid max_position_embeddings: {max_pos}")
                capacity = max(1, max_pos - (pad_id + 1))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed config.json: {exc}") from exc

    tok_max = getattr(tokenizer, "model_max_length", None)
    if isinstance(tok_max, int) and 0 < tok_max < capacity:
        capacity = tok_max

    capacity = min(capacity, DEFAULT_MAX_TOKENS)
    env_max = os.environ.get("E5_MAX_TOKENS")
    if env_max:
        try:
            val = int(env_max)
            if val > capacity:
                raise RuntimeError(f"E5_MAX_TOKENS={val} exceeds model capacity {capacity}")
            if val <= 0:
                raise RuntimeError(f"Invalid E5_MAX_TOKENS={val}")
            return val
        except ValueError:
            raise RuntimeError(f"Invalid E5_MAX_TOKENS value: {env_max}")

    return min(capacity, DEFAULT_MAX_TOKENS)


def chunk_tokenizer_hash(path: Path) -> str:
    # Order and framing are part of the PdfParse / encoder files-v1 contract.
    digest = hashlib.sha256()
    for name in ("tokenizer.json", "tokenizer.model", "sentencepiece.bpe.model",
                 "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json"):
        file = path / name
        if file.is_file():
            digest.update(name.encode() + b"\0" + hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


class KMModel:
    def __init__(self):
        root = Path(os.environ.get('MODELS_DIR') or './models').expanduser().resolve()
        path = (root / 'embeddings' / 'multilingual_e5_large').resolve()
        if not path.is_relative_to(root):
            raise ValueError(f'E5 model directory escapes MODELS_DIR: {path}')
        if not path.is_dir():
            raise RuntimeError(f'Local E5 model missing: {path}. Run models/download_embeddings.py.')
        for asset in path.rglob('*'):
            if asset.is_symlink() and not asset.resolve().is_relative_to(path):
                raise ValueError(f'E5 file escapes its model directory: {asset}')

        from sentence_transformers import SentenceTransformer

        device = os.environ.get('E5_DEVICE', 'cpu')
        self.model = SentenceTransformer(str(path), device=device, local_files_only=True)
        self.tokenizer = self.model.tokenizer

        weights_hash = compute_weights_hash(path)
        max_tokens = derive_model_capacity(path, self.tokenizer, self.model)
        self.model.max_seq_length = max_tokens

        tok_digest = hashlib.sha256()
        for name in ('config.json', 'tokenizer.json', 'modules.json', '1_Pooling/config.json', 'tokenizer.model'):
            f = path / name
            if f.is_file():
                tok_digest.update(f.read_bytes())

        dimension = self.model.get_sentence_embedding_dimension()
        if dimension != 1024:
            raise RuntimeError(f'KM text schema requires E5 large, 1024 dimensions; got {dimension}')

        self.metadata = {
            'contract': 'e5-v1',
            'model': 'intfloat/multilingual-e5-large',
            'revision': os.environ.get('E5_REVISION', 'local-weights-sha256'),
            'preprocessing': 'prefix-mean-l2-v1',
            'tokenizer_hash': tok_digest.hexdigest(),
            'weights_hash': weights_hash,
            'dimension': dimension,
            'dtype': 'float32',
            'normalized': True,
            'max_tokens': max_tokens,
        }
        self.metadata['fingerprint'] = hashlib.sha256(
            json.dumps(self.metadata, sort_keys=True).encode()
        ).hexdigest()
        self.metadata['chunk_tokenizer_hash'] = chunk_tokenizer_hash(path)

    def encode(self, texts, role):
        return self.model.encode(
            [role + ': ' + t for t in texts],
            normalize_embeddings=True,
            batch_size=int(os.environ.get('E5_BATCH_SIZE', '4')),
        ).tolist()


E5Model = KMModel


def check_encoder_auth(authorization: str | None = Header(None)):
    token = (os.environ.get('KM_ENCODER_TOKEN') or '').strip()
    env = (os.environ.get('KB_ENV') or os.environ.get('ENVIRONMENT') or 'dev').strip().lower()
    is_prod = env in ('production', 'prod')
    is_dev = env in ('dev', 'development', 'test', 'testing')
    if not is_prod and not is_dev:
        raise HTTPException(500, f'Invalid environment configuration: {env}')
    if is_prod:
        if not token or len(token) < 16:
            raise HTTPException(500, 'KM_ENCODER_TOKEN must be configured with >= 16 characters in production')
        if not authorization or not verify_bearer_token(authorization, token, env):
            raise HTTPException(401, 'Unauthorized: invalid or missing encoder bearer token')
    else:
        if token and not verify_bearer_token(authorization, token, env):
            raise HTTPException(401, 'Unauthorized: invalid encoder bearer token')


def create_app(model=None):
    @asynccontextmanager
    async def lifespan(app_inst):
        if model is not None:
            app_inst.state.model = model
        else:
            app_inst.state.model = KMModel()
        yield
        if hasattr(app_inst.state, 'model'):
            del app_inst.state.model

    application = FastAPI(title='Internal E5 encoder', version='1.4.0', lifespan=lifespan)
    slot = threading.BoundedSemaphore(1)

    @application.get('/health')
    def health():
        if not hasattr(application.state, 'model'):
            raise HTTPException(503, 'Model not ready')
        return {
            'status': 'ready',
            **application.state.model.metadata,
            'device': os.environ.get('E5_DEVICE', 'cpu')
        }

    @application.post('/tokenize')
    def tokenize(request: EncodeRequest, http_request: Request, authorization: str | None = Header(None)):
        check_encoder_auth(authorization)
        if os.environ.get('KM_ENCODER_TOKEN', '').strip():
            accept_upstream(http_request.headers)
        if any((len(t) > 32000 for t in request.texts)):
            raise HTTPException(422, 'Text exceeds character limit')
        m = application.state.model
        return {
            'metadata': m.metadata,
            'tokens': [m.tokenizer.encode(request.role + ': ' + t) for t in request.texts]
        }

    @application.post('/encode')
    def encode(request: EncodeRequest, http_request: Request, authorization: str | None = Header(None)):
        check_encoder_auth(authorization)
        if os.environ.get('KM_ENCODER_TOKEN', '').strip():
            accept_upstream(http_request.headers)
        if any((len(t) > 32000 for t in request.texts)):
            raise HTTPException(422, 'Text exceeds character limit')
        m = application.state.model
        max_tokens = m.metadata.get('max_tokens', DEFAULT_MAX_TOKENS)
        if any(
            (
                len(m.tokenizer.encode(request.role + ': ' + t)) > max_tokens
                for t in request.texts
            )
        ):
            raise HTTPException(422, f'Input exceeds {max_tokens} tokens; no implicit truncation')
        if not slot.acquire(blocking=False):
            raise HTTPException(429, 'Encoder busy; retry later')
        try:
            return {
                'request_id': request.request_id,
                'metadata': m.metadata,
                'vectors': m.encode(request.texts, request.role)
            }
        finally:
            slot.release()

    application.add_middleware(RequestIdMiddleware, service='km-encoder')
    return application


app = create_app()
