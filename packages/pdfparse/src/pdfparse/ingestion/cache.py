"""Versioned, disposable PDF results and crops, with bounded retention."""
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import time
from uuid import uuid4

from filelock import FileLock
from pydantic import TypeAdapter

from pdfparse.enrich import FileSystemAssetStore, EnrichedPictureItem
from .contracts import Chunk, ExtractionRegion, PdfParseResult, ResultProjection, StageMetrics, logical_pdf_path
from .preparation import cache_configuration

_LOG = logging.getLogger(__name__)
_CHUNKS = TypeAdapter(tuple[Chunk, ...])
_REQUIRED = {'document.json', 'chunks.json', 'result.json', 'preparation.json',
             'index.jsonl', 'visuals.jsonl', 'quality.json', 'document.md', 'models.json'}


@dataclass(frozen=True)
class _ResultData:
    page_numbers: tuple[int, ...] | None
    extraction: tuple[ExtractionRegion, ...]
    extraction_evidence_available: bool
    metrics: StageMetrics | None


_RESULT = TypeAdapter(_ResultData)


def _durable_replace(source, destination):
    os.replace(source,destination)
    if os.name == 'posix':
        for parent in {Path(source).parent,Path(destination).parent}:
            fd=os.open(parent,os.O_RDONLY | os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)


def _sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json(path: Path, value) -> None:
    with path.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def _inventory(parent):
    entries = []
    if parent.is_dir():
        for directory in parent.iterdir():
            if (directory.is_symlink() or not directory.is_dir() or len(directory.name) != 64
                    or any(c not in '0123456789abcdef' for c in directory.name)):
                continue
            size = sum(path.stat().st_size for path in directory.rglob('*')
                       if path.is_file() and not path.is_symlink())
            entries.append({'directory': directory, 'bytes': size,
                            'last_used': directory.stat().st_mtime})
    return entries


def _stats(entries):
    return {'entries': len(entries), 'bytes': sum(e['bytes'] for e in entries)}


def _stage_inventory(parent):
    root=parent/'.stages'
    if root.is_symlink() or not root.is_dir():return []
    return [p for p in root.rglob('*.json') if p.is_file() and not p.is_symlink()
            and not any(ancestor.is_symlink() for ancestor in p.parents if ancestor!=parent)]


def _stage_stats(parent):
    files=_stage_inventory(parent)
    return {'stage_entries':len(files),'stage_bytes':sum(p.stat().st_size for p in files)}


def cache_parent(path: Path, cache_dir=None) -> Path:
    configured = cache_dir if cache_dir is not None else os.getenv('PDFPARSE_CACHE_DIR')
    if not configured:
        return path.with_name(path.name + '.pdfparsecache')
    key = hashlib.sha256(path.as_posix().encode()).hexdigest()
    parent = Path(configured).expanduser().resolve() / (key + '.pdfparsecache')
    if parent.is_symlink():
        raise ValueError('Symbolic PDF cache refused')
    return parent


def cache_info(pdfpath: Path | str, *, cache_dir=None) -> dict:
    """Report bytes occupied by cached results and their crops."""
    path = Path(pdfpath).expanduser().resolve()
    parent = cache_parent(path, cache_dir)
    if not parent.exists():
        return _stats([])
    with FileLock(parent / '.lock'):
        complete=_stats(_inventory(parent))
        stages=_stage_stats(parent)
        return {**complete,**stages,'total_bytes':complete['bytes']+stages['stage_bytes']}


def _prune(parent, *, max_entries, max_bytes, max_age_seconds, keep=None):
    # The caller holds the common PDF lock: no live writer owns these remnants.
    for path in parent.iterdir():
        if (path.name.startswith(('.cache-', '.old-')) and path.is_dir()
                and not path.is_symlink()):
            shutil.rmtree(path)
    stages=parent/'.stages'
    if stages.is_dir() and not stages.is_symlink():
        for temporary in stages.rglob('.write-*'):
            if temporary.is_file() and not temporary.is_symlink():temporary.unlink(missing_ok=True)
    entries = _inventory(parent)
    managed = sorted(entries, key=lambda e: (e['directory'].name == keep, e['last_used']))
    count, size = len(managed), sum(e['bytes'] for e in managed)
    removed = []
    for entry in managed:
        if entry['directory'].name == keep:
            continue
        expired = max_age_seconds is not None and time.time() - entry['last_used'] > max_age_seconds
        if expired or (max_entries is not None and count > max_entries) or (max_bytes is not None and size > max_bytes):
            shutil.rmtree(entry['directory'])
            removed.append(entry['directory'].name)
            count -= 1
            size -= entry['bytes']
    complete=_stats([e for e in entries if e['directory'].name not in removed])
    files=sorted(_stage_inventory(parent),key=lambda p:p.stat().st_mtime)
    stage_size=sum(p.stat().st_size for p in files)
    for path in files:
        expired=max_age_seconds is not None and time.time()-path.stat().st_mtime>max_age_seconds
        if expired or (max_bytes is not None and complete['bytes']+stage_size>max_bytes):
            size=path.stat().st_size;path.unlink(missing_ok=True);stage_size-=size
    return {**complete,**_stage_stats(parent),'total_bytes':complete['bytes']+stage_size,'removed':removed}


def prune_cache(pdfpath: Path | str, *, max_entries: int | None = 8,
                max_bytes: int | None = 512 * 1024 * 1024,
                max_age_seconds: int | None = None, cache_dir=None) -> dict:
    """Evict complete cache entries, including crops. max_entries=0 purges them all."""
    for limit in (max_entries, max_bytes, max_age_seconds):
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError('Cache limits must be nonnegative integers or None')
    path = Path(pdfpath).expanduser().resolve()
    parent = cache_parent(path, cache_dir)
    if not parent.exists():
        return {**_stats([]), 'removed': []}
    with FileLock(parent / '.lock'):
        if max_entries == 0 and (parent / '.stages').is_dir() and not (parent / '.stages').is_symlink():
            shutil.rmtree(parent / '.stages')
        return _prune(parent, max_entries=max_entries, max_bytes=max_bytes, max_age_seconds=max_age_seconds)


class ResultCache:
    """One PDF/configuration, under the shared per-PDF read/write/eviction lock."""

    def __init__(self, pipeline, path: Path, source_path: Path | None = None):
        self.pipeline = pipeline
        self.pdfpath = path
        self.source_path = source_path if source_path is not None else path
        self.key, self.configuration = cache_configuration(pipeline)
        self.parent = cache_parent(path, pipeline.configuration.cache_dir)
        self.directory: Path | None = None
        self.pdf_sha256: str | None = None
        self.temporary: Path | None = None
        self.store = None
        self.used = False

    def load(self, *, verify_configuration=True) -> PdfParseResult | None:
        if not self.directory.is_dir():
            return None
        if sum(p.stat().st_size for p in self.directory.rglob('*') if p.is_file()) > self.pipeline.configuration.cache_max_bytes:
            return None
        self.store = FileSystemAssetStore(self.directory / 'assets')
        try:
            manifest = json.loads((self.directory / 'manifest.json').read_text(encoding='utf-8'))
            if (manifest['format'] != 'pdfparse.result_cache' or type(manifest['schema_version']) is not int
                    or manifest['schema_version'] != 2 or manifest['configuration_hash'] != self.key
                    or manifest['configuration'] != self.configuration
                    or manifest['pdf_sha256'] != self.pdf_sha256
                    or manifest['pdfpath'] != str(self.pdfpath)):
                return None
            files = manifest['files']
            if not isinstance(files, dict) or set(files) != _REQUIRED:
                raise ValueError('Incomplete cache file inventory')
            root, content = self.directory.resolve(), {}
            for name, digest in files.items():
                candidate = (root / name).resolve()
                if not candidate.is_relative_to(root) or candidate == root:
                    raise ValueError(f'Invalid cached file: {name}')
                data = candidate.read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f'Invalid cached file: {name}')
                content[name] = data
            document = self.pipeline.codec.from_dict(json.loads(content['document.json']))
            if (document.document_id != self.pdf_sha256 or document.pdfpath != self.pdfpath
                    or document.assets_dir != self.store.local_directory):
                raise ValueError('Invalid cached document identity')
            required_assets = {item.asset_path for item in document.elements.values()
                               if isinstance(item, EnrichedPictureItem) and item.asset_path is not None}
            if set(manifest['assets']) != required_assets:
                raise ValueError('Incomplete cached asset inventory')
            for locator, digest in manifest['assets'].items():
                if self._asset_sha(locator) != digest:
                    raise ValueError('Missing or altered cached asset')
            payload = json.loads(content['chunks.json'])
            if payload['format'] != 'pdfparse.chunks' or payload['schema_version'] != 1:
                raise ValueError('Unsupported chunks cache schema')
            chunks = _CHUNKS.validate_python(payload['chunks'])
            self.pipeline._validate_chunks(document, chunks)
            data = _RESULT.validate_json(content['result.json'])
            preparation = content['preparation.json'].decode('utf-8')
            if json.loads(preparation)['document_id'] != self.pdf_sha256:
                raise ValueError('Invalid preparation identity')
            from .indexing import IndexSnapshot, QualityReport
            index = IndexSnapshot(tuple(content['index.jsonl'].decode().splitlines()),
                TypeAdapter(QualityReport).validate_json(content['quality.json']), preparation,
                tuple(content['visuals.jsonl'].decode().splitlines()), content['models.json'].decode())
            projection = ResultProjection(content['document.json'].decode(), content['document.md'].decode(), index)
            if (_sha(self.source_path) != self.pdf_sha256
                    or (verify_configuration and cache_configuration(self.pipeline)[0] != self.key)):
                raise ValueError('PDF or model configuration changed while reading its cache')
            self.directory.touch()
            self.used = True
            return PdfParseResult(document, chunks, data.page_numbers, data.extraction,
                                  data.extraction_evidence_available, preparation, data.metrics, projection)
        except (OSError, ValueError, TypeError, KeyError) as error:
            _LOG.warning('Ignoring incomplete or invalid PdfParse cache %s: %s', self.directory, error)
            return None

    def extract(self):
        from .stages import StageStore
        return StageStore(self).extraction(self.pipeline.extractor)

    def reuse_enriched(self) -> PdfParseResult | None:
        """Reuse a validated enriched upstream from another preparation of this PDF.

        The legacy complete-result identity is unchanged. Only chunking differs;
        all producer/package/native-weight evidence must still match.
        """
        from copy import deepcopy
        from .preparation import preparation_manifest
        current = json.loads(preparation_manifest(self.pipeline, self.pdf_sha256))
        if current.get('native_weights') and not current['native_weights'].get('complete'):
            return None
        def upstream(value):
            value = deepcopy(value)
            value.pop('manifest_hash', None)
            value.pop('reuse',None)
            value.get('packages',{}).pop('chonkie',None)
            value.get('configuration', {}).pop('chunking', None)
            for name in ('cache', 'cache_max_entries', 'cache_max_bytes', 'cache_max_age_seconds', 'asset_store'):
                value.get('configuration', {}).pop(name, None)
            return value
        for directory in sorted(self.parent.iterdir()):
            if directory == self.directory or directory.is_symlink() or not directory.is_dir() or len(directory.name) != 64:
                continue
            try:
                manifest = json.loads((directory / 'manifest.json').read_text())
                if manifest.get('pdf_sha256') != self.pdf_sha256 or manifest.get('pdfpath') != str(self.pdfpath):
                    continue
                original = json.loads((directory / 'preparation.json').read_text())
                if upstream(original) != upstream(current):
                    continue
                other = ResultCache(self.pipeline, self.pdfpath, self.source_path)
                other.directory, other.pdf_sha256 = directory, self.pdf_sha256
                other.key, other.configuration = manifest['configuration_hash'], manifest['configuration']
                cached = other.load(verify_configuration=False)
                if cached is None:
                    continue
            except (OSError, ValueError, TypeError, KeyError):
                continue
            store = self.asset_store()
            for locator in manifest['assets']:
                store.put(locator, other.store.get(locator), media_type='application/octet-stream')
            # Rechunk in the same orchestrator and validators. Never transplant
            # prior chunks, context or published vectors into a new strategy.
            chunks = self.pipeline.chunk_document(cached.document)
            current['reuse'] = {'stage':'enriched_document', 'source_manifest_sha256':_sha(directory/'manifest.json'),
                                'source_preparation_hash':original.get('manifest_hash'), 'inference_reexecuted':False}
            current.pop('manifest_hash', None)
            current['manifest_hash'] = hashlib.sha256(json.dumps(current, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            result = replace(cached, chunks=chunks, manifest_json=json.dumps(current, sort_keys=True, ensure_ascii=False), metrics=None, projection=None)
            return self.save(result)
        return None

    def _asset_sha(self, locator):
        if isinstance(self.store, FileSystemAssetStore):
            return _sha(self.store._path(locator))
        return hashlib.sha256(self.store.get(locator)).hexdigest()

    def asset_store(self):
        if self.temporary is None:
            self.temporary = Path(tempfile.mkdtemp(prefix='.cache-', dir=self.parent))
            self.store = FileSystemAssetStore(self.temporary / 'assets')
            self.store.root.mkdir()
        return self.store

    def save(self, result: PdfParseResult) -> PdfParseResult:
        if self.temporary is None:
            raise RuntimeError('Cache must be initialized before enrichment')
        if (result.document.document_id != self.pdf_sha256 or _sha(self.source_path) != self.pdf_sha256
                or cache_configuration(self.pipeline)[0] != self.key):
            raise RuntimeError('PDF changed or model configuration changed during parsing; refusing to publish its cache')
        root = self.temporary
        result.document.assets_dir = self.directory / 'assets'
        document_data = self.pipeline.codec.to_dict(result.document, validate=False)
        _json(root / 'document.json', document_data)
        _json(root / 'chunks.json', {'format': 'pdfparse.chunks', 'schema_version': 1,
                                   'chunks': _CHUNKS.dump_python(result.chunks, mode='json', warnings='error')})
        data = _ResultData(result.page_numbers, result.extraction, result.extraction_evidence_available, result.metrics)
        _json(root / 'result.json', _RESULT.dump_python(data, mode='json', warnings='error'))
        _json(root / 'preparation.json', result.preparation_manifest())
        index = result.prepare_index()
        index.export_jsonl(root / 'index.jsonl')
        index.export_visual_jsonl(root / 'visuals.jsonl')
        _json(root / 'quality.json', asdict(index.quality))
        _json(root / 'models.json', json.loads(index.model_provenance_json))
        markdown = result.document.to_markdown()
        (root / 'document.md').write_text(markdown, encoding='utf-8')
        assets = {item.asset_path: self._asset_sha(item.asset_path) for item in result.document.elements.values()
                  if isinstance(item, EnrichedPictureItem) and item.asset_path is not None}
        files = {file.name: _sha(file) for file in root.iterdir() if file.is_file()}
        _json(root / 'manifest.json', {
            'format': 'pdfparse.result_cache', 'schema_version': 2,
            'configuration_hash': self.key, 'configuration': self.configuration,
            'runtime_configuration': getattr(self.pipeline, '_runtime_configuration', None),
            'pdfpath': str(self.pdfpath), 'pdf_sha256': self.pdf_sha256, 'files': files, 'assets': assets,
        })
        projection = ResultProjection((root / 'document.json').read_text(), markdown, index)
        if sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) > self.pipeline.configuration.cache_max_bytes:
            raise RuntimeError('PDF cache entry exceeds cache_max_bytes; increase the budget or process a smaller PDF')
        # Retain a previous entry until the complete replacement can be atomically published.
        backup = self.parent / f'.old-{uuid4().hex}'
        if self.directory.exists():
            _durable_replace(self.directory, backup)
        try:
            _durable_replace(root, self.directory)
        except BaseException:
            if backup.exists():
                _durable_replace(backup, self.directory)
            raise
        finally:
            if backup.exists() and self.directory.exists():
                if backup.is_dir():
                    shutil.rmtree(backup)
                else:
                    backup.unlink()
        self.temporary = None
        self.used = True
        return replace(result, projection=projection)


@contextmanager
def result_cache(pipeline, pdfpath, *, source_path=None):
    if not pipeline.configuration.cache:
        yield None
        return
    path = logical_pdf_path(pdfpath, source_path)
    parent = cache_parent(path, pipeline.configuration.cache_dir)
    parent.mkdir(parents=True, exist_ok=True)
    # One lock protects all configurations and retention decisions for this PDF.
    with FileLock(parent / '.lock'):
        entry = ResultCache(pipeline, path, source_path)
        entry.pdf_sha256 = _sha(entry.source_path)
        # Different PDF contents never overwrite another version's complete extraction.
        identity = hashlib.sha256(f'{entry.pdf_sha256}:{entry.key}'.encode()).hexdigest()
        entry.directory = entry.parent / identity
        try:
            from .stages import active_stages, StageStore
            token = active_stages.set(StageStore(entry))
            try:
                yield entry
            finally:
                active_stages.reset(token)
        finally:
            if entry.temporary is not None:
                shutil.rmtree(entry.temporary, ignore_errors=True)
            config = pipeline.configuration
            _prune(entry.parent, max_entries=config.cache_max_entries, max_bytes=config.cache_max_bytes,
                   max_age_seconds=config.cache_max_age_seconds, keep=entry.directory.name if entry.used and entry.directory.is_dir() else None)


def load_cached_run(pdfpath, runtime_configuration, *, source_path=None, cache_dir=None) -> PdfParseResult | None:
    """Validate a CLI result without constructing inference services.

    The saved runtime profile identifies CLI composition; the regular loader
    still verifies the PDF, inventory, assets, schemas and chunk references.
    """
    from types import SimpleNamespace
    from pdfparse.enrich import EnrichedDocumentCodec
    from pdfparse.pipeline import PdfParsePipeline

    path = logical_pdf_path(pdfpath, source_path)
    parent = cache_parent(path, cache_dir)
    if not parent.is_dir():
        return None
    with FileLock(parent / '.lock'):
        physical_path = source_path if source_path is not None else path
        pdf_sha = _sha(physical_path)
        for directory in sorted(parent.iterdir()):
            if directory.is_symlink() or not directory.is_dir() or len(directory.name) != 64:
                continue
            try:
                manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
                if manifest.get('runtime_configuration') != runtime_configuration:
                    continue
                configuration = manifest['configuration']
                encoded = json.dumps(configuration, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False)
                key = hashlib.sha256(encoded.encode()).hexdigest()
                if directory.name != hashlib.sha256(f'{pdf_sha}:{key}'.encode()).hexdigest():
                    continue
                entry = object.__new__(ResultCache)
                entry.pipeline = SimpleNamespace(
                    configuration=SimpleNamespace(cache_max_bytes=512 * 1024 * 1024),
                    codec=EnrichedDocumentCodec(), _validate_chunks=PdfParsePipeline._validate_chunks)
                entry.pdfpath, entry.pdf_sha256 = path, pdf_sha
                entry.source_path = physical_path
                entry.key, entry.configuration = key, configuration
                entry.directory, entry.used = directory, False
                result = entry.load(verify_configuration=False)
                if result is not None:
                    return result
            except (OSError, ValueError, TypeError, KeyError):
                continue
    return None


def cached_run_chunks(pdfpath, runtime_configuration, *, source_path=None, cache_dir=None) -> int | None:
    result = load_cached_run(pdfpath, runtime_configuration, source_path=source_path, cache_dir=cache_dir)
    return len(result.chunks) if result is not None else None


def remember_runtime_configuration(pdfpath, pipeline, runtime_configuration, *, source_path=None):
    """Upgrade a pre-existing CLI cache after a regular validated cache hit."""
    path = logical_pdf_path(pdfpath, source_path)
    parent = cache_parent(path, pipeline.configuration.cache_dir)
    with FileLock(parent / '.lock'):
        key, _ = cache_configuration(pipeline)
        physical_path = source_path if source_path is not None else path
        identity = hashlib.sha256(f'{_sha(physical_path)}:{key}'.encode()).hexdigest()
        manifest_path = parent / identity / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('runtime_configuration') != runtime_configuration:
            manifest['runtime_configuration'] = runtime_configuration
            _json(manifest_path, manifest)


@asynccontextmanager
async def async_result_cache(pipeline, pdfpath, scheduler, *, source_path=None):
    """Hold the per-PDF lock in the coroutine, never across unrelated threads."""
    if not pipeline.configuration.cache:
        yield None
        return
    from filelock import AsyncFileLock
    from pdfparse.execution import blocking
    path = await blocking(logical_pdf_path, pdfpath, source_path)
    parent = await blocking(cache_parent, path, pipeline.configuration.cache_dir)
    await blocking(lambda: parent.mkdir(parents=True, exist_ok=True))
    # Nonblocking OS lock attempts run on the event loop; polling suspends this
    # document rather than occupying a compute or I/O worker.
    async with AsyncFileLock(parent / '.lock', run_in_executor=False):
        def create():
            entry = ResultCache(pipeline, path, source_path)
            entry.pdf_sha256 = _sha(entry.source_path)
            identity = hashlib.sha256(f'{entry.pdf_sha256}:{entry.key}'.encode()).hexdigest()
            entry.directory = parent / identity
            return entry
        entry = await scheduler.run_cpu(create)
        from .stages import active_stages, StageStore
        token = active_stages.set(StageStore(entry))
        try:
            yield entry
        finally:
            active_stages.reset(token)
            def cleanup():
                if entry.temporary is not None:
                    shutil.rmtree(entry.temporary, ignore_errors=True)
                config = pipeline.configuration
                _prune(parent, max_entries=config.cache_max_entries, max_bytes=config.cache_max_bytes,
                       max_age_seconds=config.cache_max_age_seconds,
                       keep=entry.directory.name if entry.used and entry.directory.is_dir() else None)
            await blocking(cleanup)
