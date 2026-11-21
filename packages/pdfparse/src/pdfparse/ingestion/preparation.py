"""Stable preparation manifests, without persisting inference runtimes."""
from collections.abc import Mapping
from dataclasses import fields as dataclass_fields, is_dataclass
from functools import lru_cache
import inspect
from enum import Enum
from pathlib import Path
from typing import Any
from pydantic import BaseModel
from importlib.metadata import version, PackageNotFoundError
import hashlib
import json
import os


@lru_cache(maxsize=4096)
def _native_file_hash(path: str, signature: tuple[int, ...]) -> str:
    with Path(path).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if _file_signature(Path(path)) != signature:
        raise RuntimeError('Native model file changed while computing its identity')
    return digest


def _file_signature(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def native_weights_identity(pipeline):
    """Fingerprint provisioned native artifacts without loading models or using HTTP."""
    converter = getattr(pipeline.extractor, '_converter', None)
    if converter is None:
        return None
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions
    from pdfparse.models.layout.model import native_layout_files
    from pdfparse.models.paths import models_root
    from .docling import tableformer_files

    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    sources = {}
    if isinstance(options.layout_options, LayoutObjectDetectionOptions):
        sources['layout'] = native_layout_files(options.layout_options)
    if options.do_table_structure:
        sources['tableformer'] = tableformer_files(options)
    if not sources:
        return None
    roots = {label: directory for label, (directory, _) in sources.items()}
    required = {label: files for label, (_, files) in sources.items()}
    complete = True
    files = {}
    for label, root in roots.items():
        found = False
        if root.is_dir():
            for directory, dirs, names in os.walk(root):
                dirs[:] = sorted(name for name in dirs if not name.startswith('.'))
                for name in sorted(names):
                    path = Path(directory) / name
                    if (not name.startswith('.') and path.is_file()
                            and path.resolve().is_relative_to(root)):
                        files[f'{label}/{path.relative_to(root).as_posix()}'] = _native_file_hash(str(path), _file_signature(path))
                        found = True
        complete = complete and found and all(
            (root / name).is_file() and (root / name).resolve().is_relative_to(root)
            and (root / name).stat().st_size > 0 for name in required[label])
    return {'complete': complete and bool(files),
            'root': str(models_root()),
            'snapshots': {name: str(path) for name, path in roots.items()}, 'files': files}

def describe(value: Any, depth: int = 0) -> Any:
    if isinstance(value, type):
        return f'{value.__module__}.{value.__qualname__}'
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (Enum, Path)):
        return str(value)
    if depth > 32:
        raise ValueError('Configuration description is cyclic or too deeply nested')
    if isinstance(value, Mapping):
        return {str(key): ('[masqué]' if str(key).lower() in (
            'api_key', 'password', 'secret', 'access_token', 'authorization',
        ) else describe(item, depth + 1)) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((describe(item, depth + 1) for item in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [describe(item, depth + 1) for item in value]
    if isinstance(value, BaseModel):
        return {'class': qualified(value), 'options': {
            key: describe(getattr(value, key), depth + 1) for key in type(value).model_fields
            if key not in ('api_key', 'password', 'secret', 'access_token', 'instance_key', 'text_ocr_key', 'table_ocr_key')
        }}
    if inspect.isfunction(value) or inspect.isbuiltin(value):
        return {'class': f'{value.__module__}.{value.__qualname__}'}
    if is_dataclass(value):
        return {'class': qualified(value), 'options': {
            field.name: describe(getattr(value, field.name), depth + 1)
            for field in dataclass_fields(value) if field.init
        }}
    fields = {key: describe(getattr(value, key), depth + 1) for key in (
        'model_identity', 'model_path', 'device', 'settings', 'transformation_id', 'prompt', 'unhandled',
        'ocr', 'vlm', 'classifier', 'routes', 'fallback', 'confidence_threshold', 'root',
        'backend', 'chunk_size', 'chunk_overlap', 'tokenizer', 'tokenizer_id', 'respect_page_boundaries',
        'min_sentences_per_chunk', 'min_characters_per_sentence', 'min_characters_per_chunk',
        'model_id', 'rules', 'levels', 'delimiters', 'whitespace', 'include_delim', 'pattern',
        'pattern_mode', 'sep', 'count_tokens', 'cache_identity',
    ) if hasattr(value, key)}
    if hasattr(value, 'handlers') and hasattr(value.handlers, '_dispatch'):
        fields['handlers'] = {f'{kind.__module__}.{kind.__qualname__}': {'class': f'{fn.__module__}.{getattr(fn, "__qualname__", type(fn).__name__)}'}
                              for kind, fn in value.handlers._dispatch.registry.items() if kind is not object}
    return {'class': qualified(value), **fields}


def qualified(value: Any) -> str:
    return f'{type(value).__module__}.{type(value).__qualname__}'



def preparation_manifest(pipeline, document_id):
    packages = {}
    for name in ('pdfparse', 'docling', 'docling-core', 'docling-ibm-models', 'chonkie',
                 'transformers', 'torch', 'torchvision', 'triton', 'fla-core', 'flash-linear-attention',
                 'causal-conv1d', 'bitsandbytes', 'accelerate', 'pillow', 'rapidocr', 'onnxruntime', 'paddleocr'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    config = pipeline.configuration
    converter = getattr(pipeline.extractor, '_converter', None)
    options = None
    if converter is not None:
        from docling.datamodel.base_models import InputFormat
        options = converter.format_to_options[InputFormat.PDF].pipeline_options
    manifest = {
        'format': 'pdfparse.preparation', 'schema_version': 1,
        'document_id': document_id, 'packages': packages,
        'configuration': {name: describe(getattr(config, name))
                          for name in type(config).model_fields if name != 'cache_dir'},
        'extractor': describe(pipeline.extractor), 'extraction_options': describe(options),
        'native_weights': native_weights_identity(pipeline),
        'codecs': {key: {'class': qualified(reg.codec), 'version': reg.version,
                         'wrapper': f'{reg.wrapper_type.__module__}.{reg.wrapper_type.__qualname__}'}
                   for key, reg in pipeline.codec.wrappers._by_id.items()},
    }
    encoded = json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False)
    manifest['manifest_hash'] = hashlib.sha256(encoded.encode()).hexdigest()
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False, allow_nan=False)


def element_model_provenance(result) -> dict[str, list[dict]]:
    """Attribute only executed stages using saved evidence, never the current UI selection."""
    from pdfparse.enrich import EnrichedPageDescriptionItem, EnrichedPictureItem, EnrichedTableItem
    manifest = result.preparation_manifest()
    config = manifest.get('configuration', {})
    extraction = manifest.get('extraction_options') or {}
    options = extraction.get('options', {})

    def tag(descriptor, role, fallback=None):
        descriptor = descriptor or {}
        identity = descriptor.get('model_identity', {})
        weights = identity.get('weights', {})
        source = weights.get('source', weights)
        implementation = descriptor.get('class')
        return {'role': role, 'name': identity.get('name') or fallback or implementation or role,
                'implementation': implementation, 'revision': source.get('revision'),
                'device': descriptor.get('device')}

    layout = None
    if result.extraction_evidence_available and options:
        native = options.get('layout_options') or {}
        spec = (native.get('options', {}).get('model_spec') or {}).get('options', {})
        layout = tag(config.get('layout') or native, 'layout', spec.get('name', 'Docling layout'))
        if not config.get('layout'):
            layout['revision'] = spec.get('revision')
            layout['device'] = (options.get('accelerator_options') or {}).get('options', {}).get('device')
    by_ref = {ref: [] for ref in result.document.elements}
    ocr_refs = {}
    for region in result.extraction:
        if region.text_origin in ('ocr', 'mixed'):
            for ref in region.source_refs:
                if region.ocr_engine:
                    ocr_refs[ref] = region.ocr_engine
    table_options = (options.get('table_structure_options') or {}).get('options', {})
    for ref, element in result.document.elements.items():
        tags = by_ref[ref]
        if isinstance(element, EnrichedPageDescriptionItem):
            tags.append(tag((config.get('page_enrich') or {}).get('vlm'), 'vlm', element.model_id))
            continue
        if layout and element.locations:
            tags.append(dict(layout))
        if ref in ocr_refs:
            tags.append(tag(config.get('text_ocr'), 'ocr', ocr_refs[ref]))
        if (layout and isinstance(element, EnrichedTableItem) and element.source_label == 'table'
                and options.get('do_table_structure') and not (ref in ocr_refs and table_options.get('table_ocr'))):
            tags.append(tag(None, 'table_structure', 'Docling TableFormer'))
        if isinstance(element, EnrichedPictureItem):
            classification = element.classification or {}
            saved = classification.get('models')
            if saved:
                tags.extend(dict(value) for value in saved)
            elif classification.get('model_id'):
                tags.append(tag(None, 'picture_classifier', classification['model_id']))
        # Keep one tag per executed service even if both region and figure report it.
        by_ref[ref] = list({(value['role'], value['name']): value for value in tags}.values())
    return by_ref


def cache_configuration(pipeline) -> tuple[str, dict]:
    """Hash pipeline components and options, independently of the runtime environment.

    Source code, installed package versions and discovered weight files belong
    to preparation provenance, not to the persistent cache identity.
    Never hash object reprs or memory addresses.

    Extension authors supply cache_identity with all their versioned settings,
    or disable caching. A class name alone cannot identify arbitrary injected state.
    """
    config = pipeline.configuration
    converter = getattr(pipeline.extractor, '_converter', None)
    options = None
    if converter is not None:
        from docling.datamodel.base_models import InputFormat
        options = converter.format_to_options[InputFormat.PDF].pipeline_options
    identity = {
        'configuration': {name: describe(getattr(config, name))
                          for name in type(config).model_fields if name != 'cache_dir'},
        'extractor': describe(pipeline.extractor),
        'extraction_options': describe(options),
        'codecs': {key: {'class': qualified(reg.codec), 'version': reg.version,
                         'wrapper': f'{reg.wrapper_type.__module__}.{reg.wrapper_type.__qualname__}'}
                   for key, reg in pipeline.codec.wrappers._by_id.items()},
    }
    for name in ('cache', 'asset_store', 'cache_max_entries', 'cache_max_bytes', 'cache_max_age_seconds'):
        identity['configuration'].pop(name, None)
    explicit = pipeline.configuration.cache_identity

    def check(value):
        if isinstance(value, dict):
            name = value.get('class')
            if name and not name.startswith(('pdfparse.', 'docling.', 'docling_core.', 'chonkie.', 'builtins.')):
                if explicit is None:
                    raise ValueError(f'Persistent cache requires cache_identity for extension {name}; '
                                     'declare its settings/versions or use cache=False.')
            if value.get('model_path') and not value.get('model_identity') and explicit is None:
                raise ValueError('Persistent cache requires model weights provenance: load through ModelRegistry, '
                                 'provide cache_identity with the weights version, or use cache=False.')
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(identity)
    encoded = json.dumps(identity, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest(), identity
