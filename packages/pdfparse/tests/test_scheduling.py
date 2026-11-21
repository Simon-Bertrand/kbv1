"""Queue ownership, true batching, backpressure and document attribution."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from threading import Barrier, Lock
from time import sleep
from types import SimpleNamespace

from PIL import Image
import pytest

from pdfparse.models.base import BaseInferenceModel
from pdfparse.models.scheduling import ModelQueue, QueuedModelRegistry
from pdfparse.progress import observe_inference


class Model(BaseInferenceModel):
    def __init__(self):
        self.device = 'cpu'
        self.settings = {'batch_size': 1}
        self.calls = []
        self.fail = False

    def predict(self, image):
        return self.predict_batch([image])[0]

    def predict_batch(self, images):
        self.calls.append([image.size for image in images])
        if self.fail:
            raise ValueError('inference failed')
        return [image.getpixel((0, 0))[0] for image in images]


def facade(model, queue):
    proxy = copy(model)
    proxy._inference_queue = queue
    return proxy


def test_cross_document_batching_and_attribution():
    model = Model()
    queue = ModelQueue(model, batch_size=4, wait_seconds=.1)
    proxies = [facade(model, queue) for _ in range(4)]
    barrier = Barrier(4)
    records = [[] for _ in proxies]
    def run(index):
        image = Image.new('RGB', (20 + index, 30), (index, 0, 0))
        with observe_inference(records[index].append):
            barrier.wait(timeout=5)
            result = proxies[index].predict(image)
            assert records[index]  # Metrics arrive before the result.
            return result
    try:
        with ThreadPoolExecutor(4) as executor:
            assert list(executor.map(run, range(4))) == list(range(4))
        assert len(model.calls) == 1 and len(model.calls[0]) == 4
        assert len({record[0]['batch_id'] for record in records}) == 1
        assert all(record[0]['batch_size'] == 4 for record in records)
    finally:
        queue.close()
    assert not queue.thread.is_alive()


def test_large_batches_respect_backpressure_pixel_budget_and_result_order():
    model = Model()
    queue = ModelQueue(model, batch_size=4, max_pending=1, max_pixels=1200, wait_seconds=.001)
    try:
        images = [Image.new('RGB', (20, 30), (i, 0, 0)) for i in range(9)]
        assert facade(model, queue).predict_batch(images) == list(range(9))
        assert all(len(call) <= 2 for call in model.calls)
    finally:
        queue.close()


def test_errors_propagate_and_worker_remains_usable():
    model = Model()
    queue = ModelQueue(model, wait_seconds=0)
    proxy = facade(model, queue)
    try:
        model.fail = True
        with pytest.raises(ValueError, match='inference failed'):
            proxy.predict(Image.new('RGB', (10, 10)))
        model.fail = False
        assert proxy.predict(Image.new('RGB', (10, 10))) == 0
    finally:
        queue.close()
    with pytest.raises(RuntimeError, match='closed'):
        proxy.predict(Image.new('RGB', (10, 10)))


def test_models_on_same_gpu_never_enter_runtimes_simultaneously():
    active = 0
    lock = Lock()
    class GPUModel(Model):
        def predict_batch(self, images):
            nonlocal active
            with lock:
                active += 1
                assert active == 1
            sleep(.01)
            with lock:
                active -= 1
            return super().predict_batch(images)
    models = [GPUModel(), GPUModel()]
    for model in models:
        model.device = 'cuda:0'
    registry = SimpleNamespace(load=lambda index: models[index])
    with QueuedModelRegistry(registry, wait_seconds=0) as queued:
        proxies = [queued.load(i) for i in range(2)]
        assert queued.load(0) is not proxies[0]
        assert len(queued.queues) == 2
        # Avoid CUDA metric sampling on this CPU-only fixture.
        for model in models:
            model.device = 'cpu'
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda proxy: proxy.predict(Image.new('RGB', (10, 10))), proxies))
        assert results == [0, 0]


def test_oom_batches_are_split_without_losing_order():
    import torch
    class LimitedModel(Model):
        def predict_batch(self, images):
            if len(images) > 1:
                raise torch.OutOfMemoryError('fixture OOM')
            return super().predict_batch(images)
    model = LimitedModel()
    queue = ModelQueue(model, batch_size=4, wait_seconds=.05)
    try:
        images = [Image.new('RGB', (10, 10), (i, 0, 0)) for i in range(4)]
        assert facade(model, queue).predict_batch(images) == list(range(4))
    finally:
        queue.close()


def test_specialized_table_calls_use_the_model_batch_api():
    class TableModel(Model):
        def predict_table(self, image):
            return self.predict_table_batch([image])[0]
        def predict_table_batch(self, images):
            return self.predict_batch(images)
    model = TableModel()
    queue = ModelQueue(model, batch_size=4, wait_seconds=.02)
    proxy = facade(model, queue)
    try:
        images = [Image.new('RGB', (10, 10), (i, 0, 0)) for i in range(4)]
        assert proxy.predict_table_batch(images) == list(range(4))
        assert len(model.calls) == 1
    finally:
        queue.close()


def test_scheduled_facades_preserve_cache_descriptors():
    from pdfparse.ingestion.preparation import describe
    model = Model()
    queue = ModelQueue(model)
    try:
        assert describe(model) == describe(facade(model, queue))
    finally:
        queue.close()


def test_independent_docling_pipelines_share_queued_layout_and_keep_caches(tmp_path):
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from pdfparse import PdfParseConfiguration, PdfParsePipeline
    from pdfparse.ingestion.docling import DoclingPdfExtractor
    from pdfparse.ingestion.preparation import cache_configuration
    from tests.layout_detectors import FixtureDetector
    from tests.test_layout_pipeline import make_pdf

    model = FixtureDetector(model_path=tmp_path)
    queue = ModelQueue(model, batch_size=2, wait_seconds=.01, max_pixels=4000000)
    paths = [tmp_path / f'{index}.pdf' for index in range(2)]
    for path in paths:
        make_pdf(path)
    barrier = Barrier(2)
    def process(path):
        detector = facade(model, queue)
        configuration = PdfParseConfiguration(layout=detector, cache_identity={'fixture': 'queued-layout-v1'})
        extractor = DoclingPdfExtractor(layout=detector.get_options(), options=PdfPipelineOptions(
            do_ocr=False, do_table_structure=False))
        pipeline = PdfParsePipeline(configuration, extractor=extractor)
        key = cache_configuration(pipeline)[0]
        barrier.wait(timeout=5)
        result = pipeline.run(path)
        assert any('Hello layout pipeline' in chunk.text for chunk in result.chunks)
        assert result.metrics.inferences
        assert all(record['shared_batch'] for record in result.metrics.inferences)
        assert cache_configuration(pipeline)[0] == key
        cached = pipeline.run(path)
        assert cached.chunks == result.chunks
        return result.document.pdfpath
    try:
        with ThreadPoolExecutor(2) as executor:
            assert list(executor.map(process, paths)) == paths
    finally:
        queue.close()


def test_global_cpu_pool_parallelizes_models_and_serializes_each_instance():
    from pdfparse.models.scheduling import ResourceScheduler
    started = Barrier(2)
    class ParallelModel(Model):
        def predict_batch(self, images):
            started.wait(timeout=5)
            return super().predict_batch(images)
    scheduler = ResourceScheduler(cpu_workers=2, cpu_threads=1)
    models = [ParallelModel(), ParallelModel()]
    handles = [ModelQueue(model, scheduler=scheduler, batch_size=1) for model in models]
    try:
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(lambda pair: facade(*pair).predict(Image.new('RGB', (8, 8))),
                                 zip(models, handles))) == [0, 0]
        assert len(scheduler.cpu_threads) == 2
        assert not scheduler.gpu_threads
    finally:
        scheduler.close()


def test_cpu_and_gpu_tasks_overlap_with_one_cpu_worker():
    from threading import Event, current_thread
    from pdfparse.models.scheduling import ResourceScheduler
    cpu_started, gpu_started = Event(), Event()
    owners = {}
    class CPUModel(Model):
        def predict_batch(self, images):
            owners['cpu'] = current_thread().name
            cpu_started.set()
            assert gpu_started.wait(5)
            return super().predict_batch(images)
    class GPUModel(Model):
        def predict_batch(self, images):
            owners['gpu'] = current_thread().name
            gpu_started.set()
            assert cpu_started.wait(5)
            return super().predict_batch(images)
    cpu, gpu = CPUModel(), GPUModel()
    gpu.device = 'cuda:0'
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    handles = [ModelQueue(model, scheduler=scheduler, batch_size=1) for model in (cpu, gpu)]
    gpu.device = 'cpu'  # Fixture kernels run on CPU; routing retains the loaded device.
    try:
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(lambda pair: facade(*pair).predict(Image.new('RGB', (8, 8))),
                                 zip((cpu, gpu), handles))) == [0, 0]
        assert owners['cpu'].startswith('pdfparse-cpu-')
        assert owners['gpu'] == 'pdfparse-cuda:0'
        assert len(scheduler.gpu_threads) == 1
    finally:
        scheduler.close()


def test_input_byte_credits_and_oversized_single_image():
    from pdfparse.models.scheduling import ResourceScheduler
    model = Model()
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1, max_pending=8, max_memory_bytes=4000)
    queue = ModelQueue(model, scheduler=scheduler, batch_size=4, wait_seconds=.001)
    try:
        images = [Image.new('RGB', (10, 10), (i, 0, 0)) for i in range(8)]
        assert facade(model, queue).predict_batch(images) == list(range(8))
        assert scheduler.metrics['peak_input_bytes'] <= 4000
        assert scheduler.metrics['peak_requests'] <= 2
        assert facade(model, queue).predict(Image.new('RGB', (100, 100))) == 0
        assert scheduler.requests == scheduler.memory == 0
    finally:
        scheduler.close()


def test_cpu_worker_can_wait_for_inference_without_deadlocking_the_cpu_pool():
    from pdfparse.models.scheduling import ResourceScheduler
    model = Model()
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    queue = ModelQueue(model, scheduler=scheduler, batch_size=1)
    proxy = facade(model, queue)
    try:
        future = scheduler.submit(lambda: proxy.predict(Image.new('RGB', (8, 8))))
        assert future.result(timeout=5) == 0
    finally:
        scheduler.close()


@pytest.mark.parametrize('base_kind', ['dict', 'size_dict', 'native_processor'])
@pytest.mark.parametrize('override_kind', ['none', 'dict', 'size_dict', 'unset'])
def test_qwen_requests_accept_native_size_dict_and_optional_limits(base_kind, override_kind):
    from transformers import Qwen2VLImageProcessor
    from transformers.image_utils import SizeDict
    class QwenModel(Model):
        model_type = 'qwen2_vl'
        def _processor_kwargs(self):
            return {'images_kwargs': {'size': override}}
    base = {'shortest_edge': 1024, 'longest_edge': 4096}
    if base_kind == 'native_processor':
        processor = Qwen2VLImageProcessor(size=base)
        assert isinstance(processor.size, SizeDict)
    else:
        processor = SimpleNamespace(size=base if base_kind == 'dict' else SizeDict(**base))
    if override_kind == 'dict':
        override = {'shortest_edge': 2048, 'longest_edge': 8192}
    elif override_kind == 'size_dict':
        override = SizeDict(min_pixels=2048, max_pixels=8192)
    elif override_kind == 'unset':
        override = SizeDict()
    else:
        override = None
    model = QwenModel()
    model.processor = SimpleNamespace(image_processor=processor)
    queue = ModelQueue(model, batch_size=1, wait_seconds=0)
    try:
        assert facade(model, queue).predict(Image.new('RGB', (10, 10), (7, 0, 0))) == 7
        pixels = 2048 if override_kind in ('dict', 'size_dict') else 1024
        assert queue.scheduler.metrics['peak_input_bytes'] == 10 * 10 * 6 + pixels * 16
        assert queue.scheduler.requests == queue.scheduler.memory == 0
    finally:
        queue.close()


def test_busy_cpu_model_does_not_reserve_other_models_slots():
    from threading import Event
    from pdfparse.models.scheduling import ResourceScheduler
    entered, other_ran = Event(), Event()
    class BusyModel(Model):
        def predict_batch(self, images):
            entered.set()
            assert other_ran.wait(5), 'A queued batch blocked an independent CPU model'
            return super().predict_batch(images)
    class OtherModel(Model):
        def predict_batch(self, images):
            other_ran.set()
            return super().predict_batch(images)
    scheduler = ResourceScheduler(cpu_workers=2, cpu_threads=1)
    first, second = BusyModel(), OtherModel()
    first_queue = ModelQueue(first, scheduler=scheduler, batch_size=1, wait_seconds=0)
    second_queue = ModelQueue(second, scheduler=scheduler, batch_size=1, wait_seconds=0)
    image = Image.new('RGB', (8, 8))
    try:
        _, futures = first_queue.submit(first, 'predict_batch', [image] * 4, {})
        assert entered.wait(5)
        assert facade(second, second_queue).predict(image) == 0
        assert [future.result(timeout=5) for future in futures] == [0] * 4
    finally:
        other_ran.set()
        scheduler.close()


def test_pixel_saturated_batch_dispatches_without_waiting_for_more_images():
    model = Model()
    queue = ModelQueue(model, batch_size=4, max_pixels=64, wait_seconds=30)
    try:
        _, futures = queue.submit(model, 'predict_batch', [Image.new('RGB', (8, 8))], {})
        assert futures[0].result(timeout=5) == 0
    finally:
        queue.close()


def test_distinct_gpus_execute_concurrently():
    from pdfparse.models.scheduling import ResourceScheduler
    started = Barrier(2)
    class ParallelGPU(Model):
        def predict_batch(self, images):
            started.wait(timeout=5)
            return super().predict_batch(images)
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    models = [ParallelGPU(), ParallelGPU()]
    handles = []
    for index, model in enumerate(models):
        queue = ModelQueue(model, scheduler=scheduler, batch_size=1, wait_seconds=0)
        queue.device = f'cuda:{index}'
        handles.append(queue)
    try:
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(lambda pair: facade(*pair).predict(Image.new('RGB', (8, 8))),
                                 zip(models, handles))) == [0, 0]
        assert len(scheduler.gpu_threads) == 2
    finally:
        scheduler.close()
