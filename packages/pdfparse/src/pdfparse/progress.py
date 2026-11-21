"""Optional execution events, scoped to the caller and never persisted in documents."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum


class Stage(StrEnum):
    EXTRACTION = 'extraction'
    ENRICHMENT = 'enrichment'
    CLASSIFICATION = 'classification'
    OCR = 'ocr'
    VLM = 'vlm'
    CHUNKING = 'chunking'
    RENDERING = 'rendering'
    UNLOADING = 'unloading'
    LOADING = 'loading'
    CONFIGURATION = 'configuration'


ProgressCallback = Callable[[Stage, str | None], None]
_observer: ContextVar[ProgressCallback | None] = ContextVar('pdfparse_progress', default=None)


def report_progress(stage: Stage, detail: str | None = None) -> None:
    if observer := _observer.get():
        observer(stage, detail)


@contextmanager
def observe_progress(callback: ProgressCallback) -> Iterator[None]:
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)

# Inference traces are scoped like progress events; no runtime or image is retained.
from functools import wraps
import sys
from time import perf_counter
from threading import Event, Thread

_inference_observers: ContextVar[tuple] = ContextVar('pdfparse_inference_observers', default=())
_active_inference: ContextVar[dict | None] = ContextVar('pdfparse_active_inference', default=None)


@contextmanager
def observe_inference(callback):
    token = _inference_observers.set((*_inference_observers.get(), callback))
    try:
        yield
    finally:
        _inference_observers.reset(token)


def inference_tokens(*, input_tokens: int, output_tokens: int):
    if record := _active_inference.get():
        record['input_tokens'] = record.get('input_tokens', 0) + input_tokens
        record['output_tokens'] = record.get('output_tokens', 0) + output_tokens


def trace_inference(function):
    """Measure one public model call; nested batch delegation is counted once."""
    @wraps(function)
    def measured(self, images, *args, **kwargs):
        observers = _inference_observers.get() or getattr(self, '_inference_observers', ())
        if not observers or _active_inference.get() is not None:
            return function(self, images, *args, **kwargs)
        batch = images if isinstance(images, (list, tuple)) else [images]
        record = {'model': getattr(self, 'model_identity', {}).get('name', type(self).__name__),
                  'implementation': type(self).__name__, 'device': getattr(self, 'device', 'cpu'),
                  'operation': function.__name__,
                  'image_sizes': [list(image.size) for image in batch if hasattr(image, 'size')],
                  'status': 'succeeded'}
        torch = sys.modules.get('torch')
        cuda = torch is not None and record['device'].startswith('cuda') and torch.cuda.is_available()
        if cuda and ':' in record['device']:
            cuda = int(record['device'].split(':', 1)[1]) < torch.cuda.device_count()
        stop, sampler = Event(), None
        def sample_memory():
            try:
                free, total = torch.cuda.mem_get_info(record['device'])
                record['cuda_total_bytes'] = total
                record['cuda_peak_used_bytes'] = max(record.get('cuda_peak_used_bytes',0),total-free)
                record['torch_peak_allocated_bytes'] = max(record.get('torch_peak_allocated_bytes',0),torch.cuda.memory_allocated(record['device']))
                record['torch_peak_reserved_bytes'] = max(record.get('torch_peak_reserved_bytes',0),torch.cuda.memory_reserved(record['device']))
                return total-free
            except RuntimeError:
                record['gpu_metrics_unavailable'] = True
                return None
        if cuda and torch.cuda.is_initialized():
            record['cuda_used_before_bytes'] = sample_memory()
            record['gpu_sampling_interval_seconds'] = .05
            def sample_loop():
                while not stop.wait(.05):
                    sample_memory()
            sampler = Thread(target=sample_loop,daemon=True,name='pdfparse-vram')
            sampler.start()
        else:
            cuda = False
        token = _active_inference.set(record)
        started = perf_counter()
        try:
            return function(self, images, *args, **kwargs)
        except Exception as exc:
            record.update(status='failed', error=str(exc))
            raise
        finally:
            try:
                if cuda:
                    try:
                        torch.cuda.synchronize(record['device'])
                        record['cuda_used_after_bytes'] = sample_memory()
                    except RuntimeError:
                        record['gpu_metrics_unavailable'] = True
                stop.set()
                if sampler is not None:
                    sampler.join(timeout=1)
                record['seconds'] = perf_counter() - started
            finally:
                _active_inference.reset(token)
            for observer in observers:
                observer(dict(record))
    return measured


@contextmanager
def inference_workers(services):
    """Carry this conversion's observers to explicitly injected Docling worker services."""
    callbacks = _inference_observers.get()
    sentinel = object()
    previous = {}
    try:
        if callbacks:
            for service in services:
                if service is not None and id(service) not in previous:
                    previous[id(service)] = (service,getattr(service,'_inference_observers',sentinel))
                    service._inference_observers = callbacks
        yield
    finally:
        for service, old in previous.values():
            if old is sentinel:
                del service._inference_observers
            else:
                service._inference_observers = old
