"""Resource-aware local execution: one inference queue per CPU model and per accelerator.

Model handles have no threads. The dispatcher batches compatible requests,
prepares accelerator inputs on CPU and feeds a bounded accelerator pipeline.
"""
from contextlib import contextmanager
import asyncio
from collections import deque
from concurrent.futures import Future
from contextvars import ContextVar, copy_context
from copy import copy
from dataclasses import dataclass
from functools import wraps
import json
from math import isfinite, log2
import os
from threading import Condition, RLock, Thread, local
from time import monotonic
from uuid import uuid4

_execution_batch_size = ContextVar('pdfparse_execution_batch_size', default=None)
_prepared_inputs = ContextVar('pdfparse_prepared_inputs', default=None)


def available_cpu_count():
    """Respect the process/container CPU allowance where the OS exposes it."""
    if hasattr(os, 'process_cpu_count'):
        return os.process_cpu_count() or 1
    if hasattr(os, 'sched_getaffinity'):
        return len(os.sched_getaffinity(0)) or 1
    return os.cpu_count() or 1


@contextmanager
def cpu_thread_budget(threads):
    """Apply kernel thread limits before loading runtimes, then restore the caller."""
    import sys
    names = ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS')
    previous = {name: os.environ.get(name) for name in names}
    torch = sys.modules.get('torch')
    old_threads = torch.get_num_threads() if torch is not None else None
    try:
        for name in names:
            os.environ[name] = str(threads)
        if torch is not None:
            torch.set_num_threads(threads)
        yield
    finally:
        if torch is not None and old_threads is not None:
            torch.set_num_threads(old_threads)
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _pixel_limits(*sizes):
    """Read dicts and Transformers SizeDict without assuming mapping expansion.

    SizeDict exposes get()/attributes but is not a Mapping. Missing/None
    fields must not erase an earlier limit or reach the arithmetic below.
    """
    limits = {}
    for size in sizes:
        if size is None:
            continue
        for key, alias in (('shortest_edge', 'min_pixels'), ('longest_edge', 'max_pixels')):
            getter = getattr(size, 'get', None)
            value = getter(key) if getter is not None else getattr(size, key, None)
            if value is None:
                value = getter(alias) if getter is not None else getattr(size, alias, None)
            if value is not None:
                limits[key] = value
    return limits


def canonical_device(device):
    return 'cuda:0' if device == 'cuda' else str(device)


async def await_future(future):
    """Don't abandon running work while its document/cache is being cleaned up."""
    wrapped = asyncio.wrap_future(future)
    try:
        return await asyncio.shield(wrapped)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(wrapped)
        except Exception:
            pass
        raise


def queued_inference(function):
    @wraps(function)
    def dispatch(self, images, *args, **kwargs):
        queue = getattr(self, '_inference_queue', None)
        if queue is not None:
            if args:
                raise TypeError('Queued inference options must be keyword arguments')
            return queue.call(self, function.__name__, images, kwargs)
        return function(self, images, *args, **kwargs)
    return dispatch


@dataclass(eq=False)
class _Request:
    queue: object
    image: object
    operation: str
    options: dict
    future: Future
    observers: tuple
    pixels: int
    tokens: int
    memory: int
    bucket: tuple
    submitted: float


@dataclass(eq=False)
class _Job:
    function: object
    future: Future
    context: object
    key: object = None
    priority: bool = False


class ResourceScheduler:
    def __init__(self, *, cpu_workers=None, max_pending=64, max_memory_bytes=512 * 1024 * 1024,
                 prefetch=2, cpu_threads=None):
        self.cpu_workers = min(8, available_cpu_count()) if cpu_workers is None else cpu_workers
        if min(self.cpu_workers, max_pending, max_memory_bytes, prefetch) < 1:
            raise ValueError('Resource limits must be positive')
        self.cpu_threads_per_worker = max(1, available_cpu_count() // self.cpu_workers) if cpu_threads is None else cpu_threads
        if self.cpu_threads_per_worker < 1:
            raise ValueError('cpu_threads must be positive')
        self.running = 0
        self.max_pending, self.max_memory_bytes, self.prefetch = max_pending, max_memory_bytes, prefetch
        self.condition = Condition()
        self.cpu = deque()
        self.accelerators = {}
        self.pending = {}
        self.busy = set()
        self.shared = {}
        self.slots = {}
        self.memory = self.requests = 0
        self.accepting = True
        self.closing = False
        self.state = local()
        self.metrics = {'batches': 0, 'images': 0, 'peak_input_bytes': 0, 'peak_requests': 0}
        self.cpu_threads = [Thread(target=self._worker, args=('cpu',), name=f'pdfparse-cpu-{i}', daemon=True)
                            for i in range(self.cpu_workers)]
        self.gpu_threads = {}
        self.dispatcher = Thread(target=self._dispatch, name='pdfparse-dispatch', daemon=True)
        for thread in self.cpu_threads:
            thread.start()
        self.dispatcher.start()

    def _device(self, device):
        device = canonical_device(device)
        if device != 'cpu' and device not in self.accelerators:
            self.accelerators[device] = deque()
            thread = Thread(target=self._worker, args=(device,), name=f'pdfparse-{device}', daemon=True)
            self.gpu_threads[device] = thread
            thread.start()
        return device

    def submit(self, function, *, device='cpu', key=None, priority=False):
        with self.condition:
            if not self.accepting:
                raise RuntimeError('Resource scheduler is closed')
            return self._job(function, device, key, priority)

    def shared_resource(self, key, factory, *, device='cpu'):
        """Construct a heavyweight resource once per semantic configuration."""
        with self.condition:
            if not self.accepting:
                raise RuntimeError('Resource scheduler is closed')
            future = self.shared.get(key)
            if future is None:
                future = self._job(factory, device)
                self.shared[key] = future
        return self.wait(future)

    def _job(self, function, device='cpu', key=None, priority=False):
        device = self._device(device)
        future = Future()
        job = _Job(function, future, copy_context(), key, priority)
        queue = self.cpu if device == 'cpu' else self.accelerators[device]
        if priority:
            index = next((i for i, pending in enumerate(queue) if not pending.priority), len(queue))
            queue.insert(index, job)
        else:
            queue.append(job)
        self.condition.notify_all()
        return future

    async def run_cpu(self, function, *args, **kwargs):
        return await await_future(self.submit(lambda: function(*args, **kwargs)))

    async def run_cpu_serial(self, key, function, *args, **kwargs):
        return await await_future(self.submit(lambda: function(*args, **kwargs), key=key))

    def call(self, function, *, device='cpu', key=None):
        return self.wait(self.submit(function, device=device, key=key))

    def _pop(self, device):
        queue = self.cpu if device == 'cpu' else self.accelerators[device]
        for job in queue:
            if job.key is None or job.key not in self.busy:
                queue.remove(job)
                self.running += 1
                if job.key is not None:
                    self.busy.add(job.key)
                return job
        return None

    def configure_threads(self):
        import sys
        torch = sys.modules.get('torch')
        if torch is not None:
            torch.set_num_threads(self.cpu_threads_per_worker)

    def _execute(self, job):
        try:
            if job.future.set_running_or_notify_cancel():
                try:
                    import sys
                    torch = sys.modules.get('torch')
                    if torch is not None and getattr(self.state, 'torch', None) is not torch:
                        self.configure_threads()
                        self.state.torch = torch
                    result = job.context.run(job.function)
                except BaseException as error:
                    job.future.set_exception(error)
                else:
                    job.future.set_result(result)
        finally:
            with self.condition:
                self.running -= 1
                self.busy.discard(job.key)
                self.condition.notify_all()

    def wait(self, future):
        """Legacy synchronous callbacks help the CPU queue instead of occupying it."""
        if getattr(self.state, 'device', None) != 'cpu':
            return future.result()
        while not future.done():
            with self.condition:
                job = self._pop('cpu')
                if job is None:
                    self.condition.wait(.01)
            if job is not None:
                self._execute(job)
        return future.result()

    def _worker(self, device):
        self.state.device = device
        while True:
            with self.condition:
                job = self._pop(device)
                while job is None:
                    if (self.closing and not self.requests and not self.pending and not self.running
                            and not self.cpu and not any(self.accelerators.values())):
                        return
                    self.condition.wait()
                    job = self._pop(device)
            self._execute(job)

    def admit(self, request):
        while True:
            with self.condition:
                # A single oversize image is allowed so admission cannot deadlock.
                fits = self.memory + request.memory <= self.max_memory_bytes or self.requests == 0
                if not self.accepting:
                    raise RuntimeError('Resource scheduler is closed')
                if self.requests < self.max_pending and fits:
                    self.pending.setdefault(request.queue.resource_key, deque()).append(request)
                    self.requests += 1
                    self.memory += request.memory
                    self.metrics['peak_input_bytes'] = max(self.metrics['peak_input_bytes'], self.memory)
                    self.metrics['peak_requests'] = max(self.metrics['peak_requests'], self.requests)
                    self.condition.notify_all()
                    return
                job = self._pop('cpu') if getattr(self.state, 'device', None) == 'cpu' else None
                if job is None:
                    self.condition.wait(.01)
            if job is not None:
                self._execute(job)

    def _select(self):
        now = monotonic()
        seen = set()
        earliest = None
        # Rotate resources after dispatch so a busy model cannot monopolize
        # admission slots. CPU models each have one active batch; their actual
        # compute still shares the bounded CPU worker pool.
        for resource, pending in list(self.pending.items()):
            capacity = 1 if resource[0] == 'cpu' else self.prefetch
            if self.slots.get(resource, 0) >= capacity:
                continue
            for first in pending:
                queue = first.queue
                identity = (id(queue), first.operation, first.bucket)
                if identity in seen:
                    continue
                seen.add(identity)
                batch = [first]
                pixels, tokens = first.pixels, first.tokens
                for request in pending:
                    if request is first:
                        continue
                    if (len(batch) < queue.batch_size and pixels + request.pixels <= queue.max_pixels
                            and tokens + request.tokens <= queue.max_tokens and queue.compatible(first, request)):
                        batch.append(request)
                        pixels += request.pixels
                        tokens += request.tokens
                deadline = first.submitted + queue.wait_seconds
                full = len(batch) >= queue.batch_size or pixels >= queue.max_pixels or tokens >= queue.max_tokens
                if not full and now < deadline and not self.closing:
                    earliest = deadline if earliest is None else min(earliest, deadline)
                    continue
                for request in batch:
                    pending.remove(request)
                del self.pending[resource]
                if pending:
                    self.pending[resource] = pending
                self.slots[resource] = self.slots.get(resource, 0) + 1
                return batch, 0
        return None, None if earliest is None else max(0, earliest - now)

    def _dispatch(self):
        while True:
            with self.condition:
                batch, timeout = self._select()
                if batch is None:
                    if self.closing and not self.requests:
                        self.condition.notify_all()
                        return
                    self.condition.wait(timeout)
                    continue
                queue = batch[0].queue
                if queue.can_prepare(batch):
                    future = self._job(lambda q=queue, b=batch: q.prepare(b), key=('processor', id(queue.model)), priority=True)
                    future.add_done_callback(lambda f, q=queue, b=batch: self._prepared(q, b, f))
                else:
                    self._job(lambda q=queue, b=batch: q.execute(b), queue.device, ('model', id(queue.model)))

    def _prepared(self, queue, batch, future):
        try:
            prepared = future.result()
        except BaseException as error:
            queue.complete(batch, failure=error)
        else:
            with self.condition:
                self._job(lambda: queue.execute(batch, prepared), queue.device, ('model', id(queue.model)))

    def release(self, batch):
        with self.condition:
            self.requests -= len(batch)
            self.memory -= sum(request.memory for request in batch)
            self.slots[batch[0].queue.resource_key] -= 1
            self.metrics['batches'] += 1
            self.metrics['images'] += len(batch)
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.accepting = False
            self.closing = True
            self.condition.notify_all()
        self.dispatcher.join()
        for thread in [*self.cpu_threads, *self.gpu_threads.values()]:
            thread.join()


class ModelQueue:
    """A model handle into global resource queues; no dedicated model worker."""
    def __init__(self, model, *, batch_size=4, max_pending=64, wait_seconds=.01,
                 max_pixels=1048576, max_tokens=16384, scheduler=None, device_lock=None):
        if min(batch_size, max_pending, max_pixels, max_tokens) < 1 or not isfinite(wait_seconds) or wait_seconds < 0:
            raise ValueError('Queue limits must be positive and wait_seconds nonnegative')
        self.model = model
        self.device = canonical_device(model.device)
        self.batch_size, self.wait_seconds = batch_size, wait_seconds
        self.max_pixels, self.max_tokens = max_pixels, max_tokens
        self.owns_scheduler = scheduler is None
        self.scheduler = scheduler or ResourceScheduler(cpu_workers=1, max_pending=max_pending)
        self.closed = False
        self.processor_lock = RLock()
        self.model._processor_lock = self.processor_lock

    @property
    def resource_key(self):
        return ('cpu', id(self.model)) if self.device == 'cpu' else ('accelerator', self.device)

    @property
    def thread(self):
        return self.scheduler.cpu_threads[0]

    def submit(self, caller, operation, images, options):
        from pdfparse.progress import _inference_observers
        from .base import validate_images
        batch_call = operation.endswith('_batch')
        batch = validate_images(images if batch_call else [images])
        if self.closed:
            raise RuntimeError('Model queue is closed')
        operation = 'predict_batch' if operation in ('predict', 'predict_batch') else operation.removesuffix('_batch')
        observers = _inference_observers.get() or getattr(caller, '_inference_observers', ())
        prompts = options.get('prompts')
        if prompts is not None and (isinstance(prompts, str) or len(prompts) != len(batch)):
            raise ValueError('Expected one prompt per image')
        if prompts is not None and options.get('prompt') is not None:
            raise ValueError('Use prompt or prompts, not both')
        futures = []
        processor = getattr(getattr(self.model, 'processor', None), 'image_processor', None)
        patch = getattr(processor, 'patch_size', 14) * getattr(processor, 'merge_size', 2)
        limits = {}
        if str(getattr(self.model, 'model_type', '')).startswith('qwen'):
            limits = _pixel_limits(getattr(processor, 'size', None),
                self.model._processor_kwargs().get('images_kwargs', {}).get('size'))
        min_pixels = limits.get('shortest_edge', getattr(processor, 'min_pixels', None) or 1)
        max_pixels = self.model.settings.get('max_pixels', limits.get('longest_edge', getattr(processor, 'max_pixels', None)))
        for index, image in enumerate(batch):
            item_options = dict(options)
            if prompts is not None:
                item_options.pop('prompts')
                item_options['prompt'] = prompts[index]
            prompt = item_options.get('prompt') or getattr(self.model, 'default_prompt', '')
            if not isinstance(prompt, str):
                raise ValueError('Image-text prompt must be a string')
            pixels = max(image.width * image.height, min_pixels)
            if max_pixels is not None:
                pixels = min(pixels, max_pixels)
            tokens = max(1, pixels // (patch * patch)) + len(prompt) // 3 + self.model.settings.get('max_new_tokens', 0)
            # Account for the source and an estimated float image/tensor working copy.
            memory = image.width * image.height * (len(image.getbands()) + 3) + pixels * 16 + len(prompt.encode()) * 4
            request = _Request(self, image, operation, item_options, Future(), tuple(observers), pixels, tokens,
                               memory, (int(log2(max(1, pixels))) // 2, int(log2(max(1, len(prompt)))) // 2), monotonic())
            self.scheduler.admit(request)
            futures.append(request.future)
        return batch_call, futures

    def call(self, caller, operation, images, options):
        batch_call, futures = self.submit(caller, operation, images, options)
        results = [self.scheduler.wait(future) for future in futures]
        return results if batch_call else results[0]

    async def acall(self, caller, operation, images, options):
        # Admission may wait for byte credits; it must not block the event loop
        # or reserve a compute worker. Keep its context for per-document metrics.
        submission = asyncio.create_task(asyncio.to_thread(self.submit, caller, operation, images, options))
        try:
            batch_call, futures = await asyncio.shield(submission)
        except asyncio.CancelledError:
            _, futures = await submission
            await asyncio.gather(*(await_future(future) for future in futures), return_exceptions=True)
            raise
        results = await asyncio.gather(*(await_future(future) for future in futures), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results if batch_call else results[0]

    def compatible(self, first, request):
        from .vlm.transformers import TransformersVlmModel
        if first.queue is not request.queue or first.operation != request.operation or first.bucket != request.bucket:
            return False
        if first.operation != 'predict_batch' and not hasattr(self.model, first.operation + '_batch'):
            return False
        if first.operation == 'predict_batch' and isinstance(self.model, TransformersVlmModel):
            return {k: v for k, v in first.options.items() if k != 'prompt'} == {
                k: v for k, v in request.options.items() if k != 'prompt'}
        return first.options == request.options

    def prompts(self, batch):
        if batch[0].operation == 'predict_table':
            return [self.model.table_prompt] * len(batch)
        if batch[0].operation == 'predict_regions':
            return ['Spotting:'] * len(batch)
        return [request.options.get('prompt') or self.model.default_prompt for request in batch]

    def can_prepare(self, batch):
        from .transformers import TransformersImageTextModel
        return (self.device != 'cpu' and isinstance(self.model, TransformersImageTextModel)
                and type(self.model)._predict_batch is TransformersImageTextModel._predict_batch
                and batch[0].operation in ('predict_batch', 'predict_table', 'predict_regions'))

    def prepare(self, batch):
        from .transformers import PreparedInputs
        images = [request.image.convert('RGB') for request in batch]
        prompts = self.prompts(batch)
        prompt = prompts[0] if len(set(prompts)) == 1 else prompts
        with self.processor_lock:
            inputs = self.model._prepare_inputs(images, prompt)
        return PreparedInputs(inputs, prompts)

    def _execute(self, batch):
        from .vlm.transformers import TransformersVlmModel
        first = batch[0]
        images = [request.image for request in batch]
        if first.operation == 'predict_batch':
            if isinstance(self.model, TransformersVlmModel):
                return self.model.predict_batch(images, prompts=[request.options.get('prompt') for request in batch])
            return self.model.predict_batch(images, **first.options)
        batched = getattr(self.model, first.operation + '_batch', None)
        if batched is not None:
            return batched(images, **first.options)
        return [getattr(self.model, first.operation)(request.image, **request.options) for request in batch]

    def _retry(self, batch):
        token = _execution_batch_size.set(len(batch))
        try:
            return self._execute(batch)
        except Exception as error:
            import sys
            torch = sys.modules.get('torch')
            if torch is None or not isinstance(error, torch.OutOfMemoryError) or len(batch) == 1:
                raise
            error.__traceback__ = None
            middle = len(batch) // 2
            self.batch_size = min(self.batch_size, max(1, middle))
            token_inputs = _prepared_inputs.set(None)
            try:
                return self._retry(batch[:middle]) + self._retry(batch[middle:])
            finally:
                _prepared_inputs.reset(token_inputs)
        finally:
            _execution_batch_size.reset(token)

    def execute(self, batch, prepared=None, generated_records=None):
        # Accelerator work ends at CPU token IDs. Public model methods (OCR
        # filtering, table parsing, VLM decoding) then resume on the CPU pool.
        if prepared is not None and generated_records is None:
            return self.generate(batch, prepared)
        from pdfparse.progress import _active_inference
        active = _active_inference.set(generated_records[0]) if generated_records else None
        started = monotonic()

        from pdfparse.progress import observe_inference
        records = generated_records if generated_records is not None else []
        results, failure = None, None
        token = _prepared_inputs.set(prepared)
        try:
            with observe_inference(records.append):
                results = self._retry(batch)
            if len(results) != len(batch):
                raise ValueError('Model must return one result per queued image')
        except BaseException as error:
            failure = error
        finally:
            _prepared_inputs.reset(token)
        if active is not None:
            _active_inference.reset(active)
            records[0]['postprocess_seconds'] = monotonic() - started
            records[0]['seconds'] += records[0]['postprocess_seconds']
            if failure is not None:
                records[0].update(status='failed', error=str(failure))
        self.complete(batch, results, failure, records)

    def generate(self, batch, prepared):
        from pdfparse.progress import observe_inference
        records = []
        try:
            with observe_inference(records.append):
                prepared.generated = self.model._generate_prepared([request.image for request in batch], prepared)
        except BaseException as error:
            import sys
            torch = sys.modules.get('torch')
            if torch is not None and isinstance(error, torch.OutOfMemoryError):
                # Preserve the existing recursive split/retry behavior. Failed
                # prepared tensors must not retain GPU memory during retries.
                error.__traceback__ = None
                prepared.inputs = None
                return self.execute(batch)
            return self.complete(batch, failure=error, records=records)
        prepared.inputs = None
        for record in records:
            record['operation'] = batch[0].operation
            record['generation_seconds'] = record['seconds']
        with self.scheduler.condition:
            self.scheduler._job(lambda: self.execute(batch, prepared, records),
                key=('postprocess', id(self.model)), priority=True)

    def complete(self, batch, results=None, failure=None, records=()):
        batch_id = uuid4().hex
        observers = {id(callback): callback for request in batch for callback in request.observers}
        for index, record in enumerate(records):
            for callback in observers.values():
                try:
                    callback({**record, 'batch_id': f'{batch_id}:{index}', 'shared_batch': True,
                              'batch_size': len(record.get('image_sizes', batch)),
                              'queue_seconds': max(0., monotonic() - min(r.submitted for r in batch) - record['seconds'])})
                except Exception:
                    pass
        self.scheduler.release(batch)
        for index, request in enumerate(batch):
            if request.future.cancelled():
                continue
            if failure is not None:
                request.future.set_exception(failure)
            else:
                request.future.set_result(results[index])

    def close(self):
        self.closed = True
        if self.owns_scheduler:
            self.scheduler.close()


class QueuedModelRegistry:
    """One shared resource scheduler; concrete model facades preserve cache IDs."""
    def __init__(self, registry, *, cpu_workers=None, cpu_threads=None,
                 max_memory_bytes=512 * 1024 * 1024, prefetch=2, **queue_options):
        self.registry = registry
        self.options = queue_options
        self.lock = RLock()
        self.queues = {}
        self.loading = {}
        self.scheduler = ResourceScheduler(cpu_workers=cpu_workers, cpu_threads=cpu_threads,
            max_pending=queue_options.get('max_pending', 64), max_memory_bytes=max_memory_bytes, prefetch=prefetch)

    def __getattr__(self, name):
        return getattr(self.registry, name)

    def load(self, *args, **kwargs):
        from .registry import ModelRegistry
        if isinstance(self.registry, ModelRegistry):
            device = canonical_device(kwargs.get('device', 'cpu'))
            key = (args, json.dumps(kwargs, sort_keys=True))
            with self.lock:
                future = self.loading.get(key)
                if future is None:
                    future = self.scheduler.submit(lambda: self.registry.load(*args, **kwargs), device=device)
                    self.loading[key] = future
            model = self.scheduler.wait(future)
        else:
            # Lightweight injected registries still declare their device on the model.
            model = self.registry.load(*args, **kwargs)
        with self.lock:
            if id(model) not in self.queues:
                self.queues[id(model)] = ModelQueue(model, scheduler=self.scheduler, **self.options)
            facade = copy(model)
            facade._inference_queue = self.queues[id(model)]
            return facade

    def close(self):
        self.scheduler.close()
        for queue in self.queues.values():
            queue.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
