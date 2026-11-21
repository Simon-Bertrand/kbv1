"""OCR lifecycle, preprocessing, native outputs and failure propagation without weights."""
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest
from pydantic import ValidationError

from pdfparse.models import ModelRegistry
from pdfparse.models.ocr import BaseOcrModel
from pdfparse.models.ocr.catalog import MODELS
from tests.test_model_registry import provision
from tests.test_vlm import engine


@pytest.mark.parametrize('text,rejected', [
    ('Éléonore écrit à Œuvre : 12,50 € — café.\nDear Sir, 1914.', False),
    ('It can be procured of any first class� drug-\nstore.', False),
    ('んっしゃい' * 10, True), ('مرحبا', True), ('Dear Sir, مرحبا', True),
    ('word ' * 30, True), ('12.50 12.50 12.50', False),
])
def test_latin_ocr_discards_foreign_alphabets_and_loops_without_rewriting(tmp_path, engine, text, rejected):
    from pdfparse.progress import observe_progress
    spec = MODELS['paddleocr_vl_1_6']
    engine.model_type = spec.implementation.model_type
    engine.decoded = [text]
    model = spec.implementation(model_path=tmp_path, settings={'max_new_tokens':16,'alphabet':'latin','reject_repetition':True})
    events = []
    with observe_progress(lambda stage, detail: events.append(detail)):
        output = model.predict(Image.new('RGB',(20,30)))
    assert output == ('' if rejected else text)
    assert bool(events) == rejected
    assert engine.stopping_criteria is not None


def test_multilingual_ocr_remains_available_without_guard(tmp_path, engine):
    spec = MODELS['paddleocr_vl_1_6']
    engine.model_type = spec.implementation.model_type
    engine.decoded = ['العربية 日本語 中文']
    model = spec.implementation(model_path=tmp_path, settings={'max_new_tokens':16})
    assert model.predict(Image.new('RGB',(20,30))) == engine.decoded[0]
    assert engine.stopping_criteria is None


def test_ocr_early_stop_is_per_image_and_keeps_utf8_fragments(tmp_path):
    import torch
    from pdfparse.models.ocr.paddleocr_vl_1_6 import PaddleOcrVl16Ocr
    model = object.__new__(PaddleOcrVl16Ocr)
    model.settings = {'alphabet':'latin', 'reject_repetition':True}
    class Decoder:
        def batch_decode(self, ids, **kwargs):
            assert ids.shape[1] <= 64
            return ['んっしゃい', 'Éléonore', '\ufffd']
    model.processor = Decoder()
    criteria = model._generation_kwargs(200)['stopping_criteria']
    stopped = criteria(torch.zeros((3, 201),dtype=torch.long), None)
    assert stopped.tolist() == [True, False, False]


@pytest.mark.parametrize("name", ["mineru2_5_pro_2605", "got_ocr2", "paddleocr_vl_1_6"])
def test_generative_ocr_registry_batch_cache_and_preprocessing(tmp_path, engine, name):
    spec = MODELS[name]
    engine.model_type = spec.implementation.model_type
    models = ModelRegistry(tmp_path)
    provision(models, "ocr", name)
    model = models.load("ocr", name, device="cuda:2", settings={"max_new_tokens": 16, "batch_size": 2})
    assert isinstance(model, BaseOcrModel)
    assert model is models.load("ocr", name, device="cuda:2", settings={"batch_size": 2, "max_new_tokens": 16})
    images = [Image.new(mode, (20, 30)) for mode in ("L", "RGB", "RGBA")]
    assert model.predict_batch(images) == ["figure-0", "figure-1", "figure-2"]
    assert model.predict(images[0]) == "figure-3"
    assert model.predict_batch([]) == []
    assert engine.loads == 1 and engine.calls == 3 and engine.model_device == "cuda:2"
    assert all(device == "cuda:2" for device, _ in engine.devices)
    if name != "got_ocr2":
        assert not engine.processed
        assert engine.templates[0][0][0][0]["content"][1]["text"] == (
            "OCR:" if name == "paddleocr_vl_1_6" else "\nText Recognition:")
    else:
        assert model.settings['attn_implementation'] == 'eager'
        assert model.runtime.generation_config.eos_token_id == 151645
        assert not engine.templates
        assert [len(images) for images, _ in engine.processed] == [2, 1, 1]
        assert all(kwargs == {"return_tensors": "pt", "padding": True, "format": False}
                   for _, kwargs in engine.processed)
        assert all(image.mode == "RGB" for images, _ in engine.processed for image in images)
    assert images[0].mode == "L"
    models.verify("ocr", name)
    record = model.model_identity['weights']
    assert record["revision"] == spec.source.revision


@pytest.fixture
def paddle(monkeypatch):
    state = SimpleNamespace(calls=[], output=None, failure=None)
    class Runtime:
        def predict(self, images, **kwargs):
            state.calls.append((images, kwargs))
            if state.failure:
                raise state.failure
            return state.output if state.output is not None else [{"rec_text": f"line-{image.shape[1]}"} for image in images]
    state.constructor = Mock(return_value=Runtime())
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(TextRecognition=state.constructor))
    return state


def test_paddle_local_recognition_order_bgr_batch_and_device(tmp_path, paddle):
    models = ModelRegistry(tmp_path)
    provision(models, "ocr", "pp_ocrv6_medium_rec")
    model = models.load("ocr", "pp_ocrv6_medium_rec", device="cuda:2", settings={"batch_size": 2})
    images = [Image.new("RGB", (width, 30), "red") for width in (20, 40, 60)]
    assert model.predict_batch(images) == ["line-20", "line-40", "line-60"]
    assert len(paddle.calls) == 2
    assert paddle.calls[0][0][0][0, 0].tolist() == [0, 0, 255]
    assert model.predict_batch([]) == [] and len(paddle.calls) == 2
    paddle.constructor.assert_called_once_with(
        model_name="PP-OCRv6_medium_rec", model_dir=str(models.weights_path("ocr", "pp_ocrv6_medium_rec")),
        device="gpu:2", cpu_threads=4, enable_mkldnn=False,
    )
    assert model is models.load("ocr", "pp_ocrv6_medium_rec", device="cuda:2", settings={"batch_size": 2})
    paddle.failure = RuntimeError("recognition failed")
    with pytest.raises(RuntimeError, match="recognition failed"):
        model.predict(images[0])


@pytest.mark.parametrize("output,error", [([], ValueError), ([{"rec_text": 42}], TypeError)])
def test_paddle_rejects_partial_or_invalid_results(tmp_path, paddle, output, error):
    models = ModelRegistry(tmp_path)
    provision(models, "ocr", "pp_ocrv6_medium_rec")
    model = models.load("ocr", "pp_ocrv6_medium_rec")
    paddle.output = output
    with pytest.raises(error):
        model.predict(Image.new("RGB", (20, 30)))


@pytest.fixture
def rapid(monkeypatch):
    from omegaconf import OmegaConf

    state = SimpleNamespace(sessions=[], available=["CPUExecutionProvider", "CUDAExecutionProvider"],
                            fallback=False, dictionary="a\nb\nc", texts=["first", "second"], failure=None)
    class Session:
        def __init__(self, path, *, sess_options, providers):
            self.path, self.providers = path, providers
            self.fallback_disabled = False
            state.sessions.append(self)
        def get_providers(self):
            return ["CPUExecutionProvider"] if state.fallback else [self.providers[0][0]]
        def disable_fallback(self):
            self.fallback_disabled = True
        def get_modelmeta(self):
            return SimpleNamespace(custom_metadata_map={"character": state.dictionary})
    class Runtime:
        def __init__(self, *, params):
            state.params = params
            self.cfg = OmegaConf.create({"Det": {}, "Rec": {}})
        def _load_det_model(self):
            assert self.cfg.Det.session is state.sessions[0]
        def _load_rec_model(self):
            assert self.cfg.Rec.session is state.sessions[1]
        def __call__(self, image):
            if state.failure:
                raise state.failure
            return SimpleNamespace(txts=state.texts)
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=SimpleNamespace, InferenceSession=Session, get_available_providers=lambda: state.available))
    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(
        RapidOCR=Runtime, EngineType=SimpleNamespace(ONNXRUNTIME="ort"),
        ModelType=SimpleNamespace(MOBILE="mobile"), OCRVersion=SimpleNamespace(PPOCRV4="v4")))
    return state


def test_rapidocr_local_sessions_reading_order_empty_and_errors(tmp_path, rapid):
    models = ModelRegistry(tmp_path)
    provision(models, "ocr", "rapidocr_v4")
    model = models.load("ocr", "rapidocr_v4", device="cuda:2")
    assert len(rapid.sessions) == 2
    assert all(session.providers == [("CUDAExecutionProvider", {"device_id": 2})]
               and session.fallback_disabled for session in rapid.sessions)
    assert rapid.params["Global.use_cls"] is False
    image = Image.new("RGB", (120, 60))
    assert model.predict(image) == "first\nsecond"
    assert model.predict_batch([image, image]) == ["first\nsecond"] * 2
    assert len(rapid.sessions) == 2
    rapid.texts = None
    assert model.predict(image) == ""
    rapid.texts = [42]
    with pytest.raises(TypeError, match="strings"):
        model.predict(image)
    rapid.failure = RuntimeError("OCR failed")
    with pytest.raises(RuntimeError, match="OCR failed"):
        model.predict(image)


@pytest.mark.parametrize("failure", ["unavailable", "fallback", "dictionary"])
def test_rapidocr_rejects_backend_fallback_and_missing_dictionary(tmp_path, rapid, failure):
    models = ModelRegistry(tmp_path)
    provision(models, "ocr", "rapidocr_v4")
    if failure == "unavailable":
        rapid.available = ["CPUExecutionProvider"]
    elif failure == "fallback":
        rapid.fallback = True
    else:
        rapid.dictionary = ""
    with pytest.raises((ValueError, RuntimeError)):
        models.load("ocr", "rapidocr_v4", device="cuda:2")


@pytest.mark.parametrize("name,settings", [
    ("got_ocr2", {"format": "false"}),
    ("paddleocr_vl_1_6", {"max_new_tokens": 0}),
    ("pp_ocrv6_medium_rec", {"batch_size": 0}),
    ("pp_ocrv6_medium_rec", {"cpu_threads": "4"}),
    ("rapidocr_v4", {"text_score": 2.0}),
    ("rapidocr_v4", {"rec_batch_size": False}),
    *[(name, {"unknown": True}) for name in ("paddleocr_vl_1_6", "pp_ocrv6_medium_rec", "rapidocr_v4")],
])
def test_settings_validate_before_path_or_runtime(tmp_path, name, settings):
    with pytest.raises(ValidationError):
        MODELS[name].implementation(model_path=tmp_path / "missing", settings=settings)
    with pytest.raises(ValidationError):
        ModelRegistry(tmp_path).load("ocr", name, settings=settings)


@pytest.mark.parametrize("name", ["pp_ocrv6_medium_rec", "rapidocr_v4"])
def test_native_ocr_rejects_unsupported_device_and_missing_weights(tmp_path, name):
    implementation = MODELS[name].implementation
    with pytest.raises(ValueError, match="device"):
        implementation(model_path=tmp_path, device="mps")
    with pytest.raises(FileNotFoundError):
        implementation(model_path=tmp_path)


def test_ocr_exports_do_not_import_runtime_or_docling():
    subprocess.run([sys.executable, "-c", """
import sys
from pdfparse.models.ocr import OcrModel, MinerU25ProOcr, GotOcr2Ocr, PPOcrV6MediumRec, RapidOcrV4, PaddleOcrVl16Ocr
from pdfparse.models.ocr.catalog import MODELS
assert len(MODELS) == 5
assert not {'torch', 'transformers', 'paddleocr', 'rapidocr', 'onnxruntime', 'docling.document_converter'} & sys.modules.keys()
"""], check=True)


def test_paddleocr_ocr_reuses_vlm_source_and_task():
    from pdfparse.models.vlm.catalog import MODELS as vlm_models

    ocr, vlm = MODELS["paddleocr_vl_1_6"], vlm_models["paddleocr_vl_1_6"]
    assert ocr.source is vlm.source
    assert ocr.implementation.default_prompt == vlm.implementation.default_prompt == "OCR:"
    assert ocr.implementation.validate_settings(None)["max_new_tokens"] == 2048


def test_paddle_spotting_preserves_text_and_maps_native_coordinates():
    from pdfparse.models.ocr.paddleocr_vl_1_6 import _spotting_regions
    locs = ''.join(f'<|LOC_{n}|>' for n in (100, 200, 800, 200, 800, 400, 100, 400))
    regions = _spotting_regions('Loan to State\n500,000.00' + locs + '</s>', (200, 100), eos_token='</s>')
    assert len(regions) == 1
    assert regions[0].text == 'Loan to State\n500,000.00'
    assert regions[0].xyxy == (20, 20, 160, 40)


def test_paddle_spotting_uses_native_processor_size_and_keeps_location_tokens():
    from pdfparse.models.ocr.paddleocr_vl_1_6 import PaddleOcrVl16Ocr
    model = object.__new__(PaddleOcrVl16Ocr)
    model.processor = SimpleNamespace(
        image_processor=SimpleNamespace(size={'shortest_edge': 112896}),
        tokenizer=SimpleNamespace(eos_token='</s>'), apply_chat_template=Mock())
    image = Image.new('RGB', (100, 50))
    model._prepare_inputs([image], 'Spotting:')
    kwargs = model.processor.apply_chat_template.call_args.kwargs
    assert kwargs['processor_kwargs']['images_kwargs']['size'] == {
        'shortest_edge': 112896, 'longest_edge': 1605632,
    }
    assert model._decode_kwargs('Spotting:')['skip_special_tokens'] is False
    assert model._decode_kwargs('OCR:')['skip_special_tokens'] is True
    model._predict_batch = Mock(return_value=['Value' + ''.join(
        f'<|LOC_{value}|>' for value in (0, 0, 500, 0, 500, 1000, 0, 1000)) + '</s>'])
    assert model.predict_regions(image)[0].xyxy == (0, 0, 50, 50)
    model._predict_batch.assert_called_once_with([image], 'Spotting:')


def test_mineru_table_keeps_native_structure_and_numeric_strings():
    from pdfparse.models.ocr.mineru2_5_pro import MinerU25ProOcr
    model = object.__new__(MinerU25ProOcr)
    model.processor = SimpleNamespace(tokenizer=SimpleNamespace(eos_token='<|im_end|>'))
    model._predict_batch = Mock(return_value=[
        '<fcel>Loan<fcel>500,000.00<nl>\n<fcel>Capital<fcel>3,700,000.00<nl><|im_end|>'])
    data = model.predict_table(Image.new('RGB', (100, 100)))
    assert (data.num_rows, data.num_cols) == (2, 2)
    assert [cell.text for cell in data.table_cells] == ['Loan', '500,000.00', 'Capital', '3,700,000.00']
    assert all(cell.bbox is None for cell in data.table_cells)
    assert model._decode_kwargs(model.table_prompt)['skip_special_tokens'] is False
    assert model._decode_kwargs(model.default_prompt)['skip_special_tokens'] is True
    model._predict_batch.return_value = ['<fcel>Loan<fcel>500,00']
    with pytest.raises(ValueError, match='Incomplete'):
        model.predict_table(Image.new('RGB', (100, 100)))


@pytest.mark.parametrize('output', [
    'partial text',
    'value<|LOC_1|>',
    'value' + '<|LOC_1001|>' * 8,
    'value' + '<|LOC_100|>' * 8,
])
def test_paddle_spotting_rejects_truncation_and_invalid_boxes(output):
    from pdfparse.models.ocr.paddleocr_vl_1_6 import _spotting_regions
    with pytest.raises(ValueError):
        _spotting_regions(output, (100, 100), eos_token='</s>')



def test_got_rejects_unsupported_attention_before_loading_weights():
    from pdfparse.models.ocr.got_ocr2 import GotOcr2Ocr
    assert GotOcr2Ocr.validate_settings(None)['attn_implementation'] == 'eager'
    for backend in ('sdpa', 'flash_attention_2'):
        with pytest.raises(ValidationError, match='attn_implementation'):
            GotOcr2Ocr.validate_settings({'attn_implementation': backend})


def test_ocr_guard_interval_skips_cpu_decode_and_preserves_stopped_rows(tmp_path):
    import torch
    from pdfparse.models.ocr.paddleocr_vl_1_6 import PaddleOcrVl16Ocr
    model = object.__new__(PaddleOcrVl16Ocr)
    model.settings = {'alphabet': 'latin', 'reject_repetition': True}
    calls = []
    def decode(ids, **kwargs):
        calls.append(ids.shape[1])
        return ['مرحبا', 'Éléonore'] if len(calls) == 1 else ['Éléonore', 'مرحبا']
    model.processor = SimpleNamespace(batch_decode=decode)
    criterion = model._generation_kwargs(200)['stopping_criteria']
    for length in range(201, 209):
        assert criterion(torch.zeros((2, length), dtype=torch.long), None).tolist() == [True, False]
    assert calls == [1]
    assert criterion(torch.zeros((2, 209), dtype=torch.long), None).tolist() == [True, True]
    assert calls == [1, 9]


def test_staged_gpu_ocr_keeps_final_rejection_on_cpu(tmp_path, engine):
    from copy import copy
    from threading import current_thread
    from pdfparse.models.scheduling import ModelQueue, ResourceScheduler
    from pdfparse.progress import observe_inference
    spec = MODELS['mineru2_5_pro_2605']
    engine.model_type = spec.implementation.model_type
    model = spec.implementation(model_path=tmp_path,
        settings={'max_new_tokens': 16, 'alphabet': 'latin', 'reject_repetition': True})
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    queue = ModelQueue(model, scheduler=scheduler, batch_size=1, wait_seconds=0)
    queue.device = 'cuda:0'
    proxy = copy(model)
    proxy._inference_queue = queue
    def decode(*args, **kwargs):
        assert current_thread().name.startswith('pdfparse-cpu-')
        return ['مرحبا']
    model.processor.batch_decode = decode
    records = []
    try:
        with observe_inference(records.append):
            assert proxy.predict(Image.new('RGB', (20, 30))) == ''
        assert len(records) == 1 and records[0]['status'] == 'succeeded'
        assert records[0]['postprocess_seconds'] >= 0
    finally:
        scheduler.close()
