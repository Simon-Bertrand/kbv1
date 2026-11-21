"""HTTP adapter without model weights or torch dependency."""
import math
import os
import time
import uuid
from functools import lru_cache
import httpx
from ._service_logging import get_request_id, upstream_headers


class EncoderUnavailable(RuntimeError):
    """Temporary failure while contacting the encoder."""


class RemoteTokenizer:

    def __init__(self, client):
        self.client = client

    @lru_cache(maxsize=256)
    def encode(self, text):
        if not text.startswith('passage: '):
            raise ValueError('Expected passage prefix')
        data = self.client.request('POST', '/tokenize', {'texts': [text[9:]], 'role': 'passage'})
        self.client.check_metadata(data['metadata'])
        return data['tokens'][0]


class EncoderClient:
    validates_token_budget = True

    def __init__(self, url=None, transport=None, token=None):
        self.token = token if token is not None else os.environ.get('KM_ENCODER_TOKEN', '')
        self.http = httpx.Client(
            base_url=url
            or os.environ.get('ENCODER_URL', 'http://km-encoder:8001'),
            timeout=httpx.Timeout(180, connect=5),
            transport=transport,
            trust_env=False
        )
        self._metadata = None
        self.tokenizer = RemoteTokenizer(self)

    def request(self, method, path, body=None):
        headers = upstream_headers()
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        try:
            for attempt in range(3):
                response = self.http.request(method, path, json=body, headers=headers)
                if response.status_code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError('Expected JSON object')
                return result
        except httpx.TransportError as exc:
            raise EncoderUnavailable('E5 service unavailable') from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503, 504):
                raise EncoderUnavailable('E5 service unavailable') from exc
            if exc.response.status_code == 422 and path == '/encode':
                raise RuntimeError('E5 rejected embedding input; verify chunk size and token budget in Studio') from exc
            raise RuntimeError('E5 service rejected request') from exc
        except ValueError as exc:
            raise RuntimeError('E5 service unavailable or invalid response') from exc

    @property
    def metadata(self):
        if self._metadata is None:
            info = self.request('GET', '/health')
            metadata = {k: v for k, v in info.items() if k not in ('status', 'device')}
            if metadata.get('contract') != 'e5-v1' or not metadata.get('normalized'):
                raise RuntimeError('Incompatible encoder contract')
            self._metadata = metadata
        return self._metadata

    @property
    def fingerprint(self):
        return self.metadata['fingerprint']

    def check_metadata(self, metadata):
        if metadata != self.metadata:
            raise RuntimeError('Encoder version changed; restart client and reindex')

    def encode(self, texts, query=False):
        vectors = []
        for start in range(0, len(texts), 16):
            batch = texts[start:start + 16]
            data = self.request(
                'POST',
                '/encode',
                {
                    'texts': batch,
                    'role': 'query'
                    if query else 'passage',
                    'request_id': get_request_id() or uuid.uuid4().hex
                }
            )
            self.check_metadata(data['metadata'])
            received = data['vectors']
            if len(received) != len(batch):
                raise RuntimeError('Encoder returned wrong batch size')
            for vector in received:
                if len(vector) != self.metadata['dimension'] or not all((isinstance(x, (int, float)) and math.isfinite(x) for x in vector)):
                    raise RuntimeError('Invalid embedding shape or values')
                if abs(sum((x * x for x in vector)) - 1) > 0.01:
                    raise RuntimeError('Embedding is not L2 normalized')
            vectors.extend(received)
        return vectors

    def close(self):
        self.http.close()
