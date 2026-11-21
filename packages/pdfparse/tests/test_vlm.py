"""VLM contracts and native Transformers boundaries, without downloading weights."""
from pathlib import Path
import json
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest
from pydantic import ValidationError
import torch

from pdfparse.models import ModelRegistry
from pdfparse.models.vlm import BaseVlmModel
from pdfparse.models.vlm.catalog import MODELS


@pytest.fixture
def engine(monkeypatch):
    import transformers

    state = SimpleNamespace(model_type="qwen3_5", loads=0, calls=0, templates=[], processed=[], devices=[],
                            next_id=0, output=None, decoded=None, failure=None)

    class Inputs(dict):
        def to(self, device, *, dtype):
            state.devices.append((device, dtype))
            self["pixel_values"] = self["pixel_values"].to(dtype=dtype)
            return self

    class Processor:
        tokenizer = SimpleNamespace(padding_side="right", convert_tokens_to_ids=lambda token: 151645)

        def __call__(self, *, images, **kwargs):
            state.processed.append((images, kwargs))
            return Inputs(input_ids=torch.full((len(images), 7), 999, dtype=torch.long),
                          pixel_values=torch.zeros(len(images), 3, 4, 4))

        def apply_chat_template(self, conversations, **kwargs):
            state.templates.append((conversations, kwargs))
            assert all(conv[0]["content"][0]["image"].mode == "RGB" for conv in conversations)
            assert kwargs["processor_kwargs"]["padding"] is True
            return Inputs(input_ids=torch.full((len(conversations), 7), 999, dtype=torch.long),
                          pixel_values=torch.zeros(len(conversations), 3, 4, 4))

        def batch_decode(self, sequences, **kwargs):
            assert kwargs == {"skip_special_tokens": True, "clean_up_tokenization_spaces": False}
            assert sequences.shape[1] == 2  # the padded input prefix was removed
            return state.decoded if state.decoded is not None else [f"figure-{row[1].item()}" for row in sequences]

    class Runtime:
        dtype = torch.bfloat16
        generation_config = SimpleNamespace()

        def to(self, device):
            state.model_device = device
            return self

        def eval(self):
            return self

        def generate(self, *, input_ids, pixel_values, **kwargs):
            state.stopping_criteria = kwargs.pop('stopping_criteria', None)
            assert not torch.is_grad_enabled()
            assert pixel_values.dtype == self.dtype
            assert kwargs == {"max_new_tokens": 16, "do_sample": False,
                              "use_cache": True,
                              "return_dict_in_generate": False}
            state.calls += 1
            if state.failure:
                raise state.failure
            if state.output is not None:
                return state.output
            tail = torch.tensor([[42, state.next_id + i] for i in range(len(input_ids))])
            state.next_id += len(input_ids)
            return torch.cat((input_ids, tail), dim=1)

    def load_config(path, **kwargs):
        assert kwargs == {"local_files_only": True, "trust_remote_code": False}
        return SimpleNamespace(model_type=state.model_type)

    def load_processor(path, **kwargs):
        assert kwargs == {"local_files_only": True, "trust_remote_code": False}
        return Processor()

    def load_model(path, **kwargs):
        assert kwargs["local_files_only"] and kwargs["trust_remote_code"] is False
        assert kwargs["dtype"] == getattr(state, "expected_dtype", "auto")
        assert kwargs["attn_implementation"] == ('eager' if state.model_type == 'got_ocr2' else 'sdpa')
        assert kwargs["config"].model_type == state.model_type
        state.loads += 1
        return Runtime()

    monkeypatch.setattr(transformers.AutoConfig, "from_pretrained", load_config)
    monkeypatch.setattr(transformers.AutoProcessor, "from_pretrained", load_processor)
    monkeypatch.setattr(transformers.AutoModelForImageTextToText, "from_pretrained", load_model)
    from pdfparse.models.vlm.paddleocr_vl_1_6 import PaddleOcrVl16Vlm
    from pdfparse.models.ocr.paddleocr_vl_1_6 import PaddleOcrVl16Ocr
    for implementation in (PaddleOcrVl16Vlm, PaddleOcrVl16Ocr):
        monkeypatch.setattr(implementation, '_load_config', lambda self, path: load_config(
            path, local_files_only=True, trust_remote_code=False))
        monkeypatch.setattr(implementation, '_load_processor', lambda self, path, config: load_processor(
            path, local_files_only=True, trust_remote_code=False))
    return state


@pytest.mark.parametrize("name", MODELS)
def test_registered_vlm_download_load_cache_and_bounded_batch(tmp_path, monkeypatch, engine, name):
    spec = MODELS[name]
    engine.model_type = spec.implementation.model_type
    registry = ModelRegistry(tmp_path)
    directory = registry.weights_path('vlm', name)
    for filename in spec.source.files:
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    registry.download("vlm", name)
    registry.verify("vlm", name)
    model = registry.load("vlm", name, device="cuda:2", settings={"quantization": "none", "max_new_tokens": 16, "batch_size": 2})
    assert isinstance(model, BaseVlmModel)
    assert model is registry.load("vlm", name, device="cuda:2", settings={"quantization": "none", "batch_size": 2, "max_new_tokens": 16})
    assert engine.loads == 1 and engine.model_device == "cuda:2"
    assert model.processor.tokenizer.padding_side == "left"
    images = [Image.new(mode, (20, 30)) for mode in ("L", "RGB", "RGBA")]
    assert model.predict_batch(images, prompt="Read this figure") == ["figure-0", "figure-1", "figure-2"]
    assert [len(conversations) for conversations, _ in engine.templates] == [2, 1]
    assert all(device == "cuda:2" for device, _ in engine.devices)
    assert images[0].mode == "L" and images[2].mode == "RGBA"
    assert model.predict_batch([]) == [] and engine.calls == 2
    assert model.predict(images[0]) == "figure-3"
    conversation, kwargs = engine.templates[-1]
    assert conversation[0][0]["content"][1]["text"] == model.default_prompt
    if name.startswith("qwen3_5"):
        assert kwargs["enable_thinking"] is False
    else:
        assert "enable_thinking" not in kwargs
    assert model.model_identity["weights"]["revision"] == spec.source.revision
    record = model.model_identity['weights']
    assert len(record["files"]) == len(spec.source.files)


@pytest.mark.parametrize("settings", [{"max_new_tokens": 0}, {"max_new_tokens": "16"},
                                     {"batch_size": False}, {"batch_size": 0},
                                     {"dtype": "int4"}, {"attn_implementation": "unknown"},
                                     {"unknown": True}])
def test_shared_generation_settings_fail_before_filesystem_or_backend(tmp_path, settings):
    name = "granite_vision_4_1_4b"
    with pytest.raises(ValidationError):
        MODELS[name].implementation(model_path=tmp_path / "missing", settings=settings)
    with pytest.raises(ValidationError):
        ModelRegistry(tmp_path).load("vlm", name, settings=settings)


def test_qwen_thinking_is_explicit_and_strict(tmp_path, engine):
    implementation = MODELS["qwen3_5_4b"].implementation
    with pytest.raises(ValidationError):
        implementation(model_path=tmp_path, settings={"enable_thinking": "false"})
    model = implementation(model_path=tmp_path, settings={"quantization": "none", "enable_thinking": True, "max_new_tokens": 16})
    model.predict(Image.new("RGB", (20, 20)))
    assert engine.templates[-1][1]["enable_thinking"] is True


@pytest.mark.parametrize("invalid", ["", "   ", 42])
def test_invalid_prompt_fails_before_inference(tmp_path, engine, invalid):
    model = MODELS["qwen3_5_4b"].implementation(model_path=tmp_path, settings={"quantization": "none", "max_new_tokens": 16})
    with pytest.raises(ValueError, match="prompt"):
        model.predict(Image.new("RGB", (20, 20)), prompt=invalid)
    assert engine.calls == 0


def test_invalid_images_wrong_weights_and_backend_errors(tmp_path, engine):
    implementation = MODELS["qwen3_5_4b"].implementation
    model = implementation(model_path=tmp_path, settings={"quantization": "none", "max_new_tokens": 16})
    with pytest.raises(TypeError, match="PIL"):
        model.predict_batch(["not an image"])
    with pytest.raises(ValueError, match="positive"):
        model.predict(Image.new("RGB", (0, 20)))
    engine.failure = RuntimeError("inference failed")
    with pytest.raises(RuntimeError, match="inference failed"):
        model.predict(Image.new("RGB", (20, 20)))
    engine.model_type = "wrong"
    with pytest.raises(ValueError, match="Expected qwen3_5"):
        implementation(model_path=tmp_path, settings={"quantization": "none"})
    assert engine.loads == 1


@pytest.mark.parametrize("output", [torch.zeros(1), torch.zeros((2, 9), dtype=torch.long)])
def test_partial_or_invalid_sequences_are_rejected(tmp_path, engine, output):
    engine.output = output
    model = MODELS["qwen3_5_4b"].implementation(model_path=tmp_path, settings={"quantization": "none", "max_new_tokens": 16})
    with pytest.raises(ValueError, match="one generated sequence"):
        model.predict(Image.new("RGB", (20, 20)))


@pytest.mark.parametrize("decoded", [[], [42]])
def test_invalid_decoded_results_are_rejected(tmp_path, engine, decoded):
    engine.decoded = decoded
    model = MODELS["qwen3_5_4b"].implementation(model_path=tmp_path, settings={"quantization": "none", "max_new_tokens": 16})
    with pytest.raises(ValueError, match="one string"):
        model.predict(Image.new("RGB", (20, 20)))


def test_vlm_exports_and_catalog_do_not_import_runtimes():
    subprocess.run([sys.executable, "-c", """
import sys
from pdfparse.models.vlm import Qwen3VL4BVlm, MechVL4BRLVlm, Qwen35_4BVlm, Qwen35_9BVlm, GraniteVision41Vlm, PaddleOcrVl16Vlm, VlmModel
from pdfparse.models.vlm.catalog import MODELS
assert len(MODELS) == 6
assert 'torch' not in sys.modules and 'transformers' not in sys.modules
assert 'docling.document_converter' not in sys.modules
"""], check=True)


def test_base_vlm_batch_forwards_prompt_to_custom_models(tmp_path):
    class CustomVlm(BaseVlmModel):
        def predict(self, image, *, prompt="custom default"):
            return f"{prompt}:{image.width}"
    model = CustomVlm(model_path=tmp_path)
    images = [Image.new("RGB", (20, 30)), Image.new("RGB", (40, 30))]
    assert model.predict_batch(images) == ["custom default:20", "custom default:40"]
    assert model.predict_batch(images, prompt="Read") == ["Read:20", "Read:40"]


@pytest.mark.parametrize("name", ["qwen3_vl_4b", "mechvl_4b_rl"])
def test_qwen3_vl_resolution_budget_is_strict_and_forwarded(tmp_path, engine, name):
    implementation = MODELS[name].implementation
    for invalid in [False, "1048576", 65535]:
        with pytest.raises(ValidationError):
            implementation(model_path=tmp_path / "missing", settings={"max_pixels": invalid})
    defaults = implementation.validate_settings(None)
    assert defaults["batch_size"] == 1 and defaults["max_pixels"] == 1_048_576
    engine.model_type = "qwen3_vl"
    model = implementation(model_path=tmp_path, settings={"max_pixels": 262144, "max_new_tokens": 16})
    model.predict(Image.new("RGB", (20, 20)))
    assert engine.templates[-1][1]["processor_kwargs"] == {
        "padding": True, "images_kwargs": {"size": {"shortest_edge": 65536, "longest_edge": 262144}},
    }


def test_mechvl_preserves_native_reasoning_and_answer(tmp_path, engine):
    engine.model_type = "qwen3_vl"
    engine.decoded = ["<think>analysis</think><answer>drawing</answer>"]
    model = MODELS["mechvl_4b_rl"].implementation(model_path=tmp_path, settings={"quantization": "none", "max_new_tokens": 16})
    assert model.predict(Image.new("RGB", (20, 20))) == engine.decoded[0]


@pytest.mark.parametrize('dtype,compute_dtype', [('auto', torch.float16), ('bfloat16', torch.bfloat16)])
@pytest.mark.parametrize('settings', [{}, {'quantization': 'nf4'}])
def test_nf4_uses_requested_device_map_without_moving_quantized_model(tmp_path, engine, monkeypatch, dtype, compute_dtype, settings):
    import transformers
    engine.expected_dtype = dtype

    captured = {}
    original = transformers.AutoModelForImageTextToText.from_pretrained
    def load(path, **kwargs):
        captured['device_map'] = kwargs.pop('device_map')
        captured['quantization'] = kwargs.pop('quantization_config')
        return original(path, **kwargs)
    monkeypatch.setattr(transformers, 'BitsAndBytesConfig', lambda **kwargs: kwargs)
    monkeypatch.setattr(transformers.AutoModelForImageTextToText, 'from_pretrained', load)
    model = MODELS['qwen3_5_4b'].implementation(model_path=tmp_path, device='cuda:2', settings={
        **settings, 'max_new_tokens': 16, 'dtype': dtype,
    })
    assert captured['device_map'] == {'': 'cuda:2'}
    assert captured['quantization'] == {
        'load_in_4bit': True, 'bnb_4bit_quant_type': 'nf4',
        'bnb_4bit_compute_dtype': compute_dtype, 'bnb_4bit_use_double_quant': True,
    }
    assert not hasattr(engine, 'model_device')  # device placement belongs to from_pretrained
    assert model.predict(Image.new('RGB', (20, 20))) == 'figure-0'


def test_nf4_is_strict_and_rejects_non_cuda_device(tmp_path, engine):
    implementation = MODELS['qwen3_5_4b'].implementation
    for invalid in [True, 'int4', 'auto']:
        with pytest.raises(ValidationError):
            implementation(model_path=tmp_path / 'missing', settings={'quantization': invalid})
    with pytest.raises(ValueError, match='CUDA'):
        implementation(model_path=tmp_path, device='cpu', settings={'quantization': 'nf4'})
    assert engine.loads == 0


@pytest.mark.parametrize('nested', [False, True])
def test_paddle_config_preserves_native_rope_and_checkpoint_without_warnings(tmp_path, caplog, nested):
    from transformers import PaddleOCRVLConfig
    from transformers.models.paddleocr_vl.configuration_paddleocr_vl import PaddleOCRTextConfig
    from pdfparse.models.vlm.paddleocr_vl_1_6 import PaddleOcrVl16Vlm
    text = {'hidden_size': 96, 'num_hidden_layers': 2, 'num_attention_heads': 6,
            'num_key_value_heads': 2, 'head_dim': 16, 'vocab_size': 512,
            'tie_word_embeddings': False, 'rope_theta': 12345,
            'rope_scaling': {'rope_type': 'default', 'mrope_section': [2, 3, 3]}}
    data = {'model_type': 'paddleocr_vl', 'tie_word_embeddings': False,
            **({'text_config': text} if nested else text)}
    source = json.dumps(data)
    (tmp_path / 'config.json').write_text(source)
    model = object.__new__(PaddleOcrVl16Vlm)
    config = model._load_config(str(tmp_path))
    assert type(config) is PaddleOCRVLConfig
    assert config.text_config.rope_parameters['mrope_section'] == [2, 3, 3]
    assert config.text_config.rope_parameters['rope_theta'] == 12345
    assert config.text_config.hidden_size == 96 and config.text_config.num_hidden_layers == 2
    assert not config.tie_word_embeddings and not config.text_config.tie_word_embeddings
    assert PaddleOCRTextConfig.ignore_keys_at_rope_validation == set()
    assert 'Unrecognized keys' not in caplog.text
    assert (tmp_path / 'config.json').read_text() == source
    json.loads(config.to_json_string())


def test_paddle_processor_loads_native_components_and_saved_template_offline(tmp_path, monkeypatch):
    import transformers
    from pdfparse.models.vlm.paddleocr_vl_1_6 import PaddleOcrVl16Vlm
    config, image_processor, tokenizer = object(), object(), object()
    calls = []
    def component(value):
        def load(path, **kwargs):
            assert path == str(tmp_path)
            assert kwargs == {'config': config, 'local_files_only': True, 'trust_remote_code': False}
            calls.append(value)
            return value
        return load
    monkeypatch.setattr(transformers.AutoImageProcessor, 'from_pretrained', component(image_processor))
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', component(tokenizer))
    def processor_dict(path, **kwargs):
        assert kwargs == {'local_files_only': True, 'trust_remote_code': False}
        return {'chat_template': 'native template'}, {}
    monkeypatch.setattr(transformers.PaddleOCRVLProcessor, 'get_processor_dict', processor_dict)
    def assemble(args, data, **kwargs):
        assert args == [image_processor, tokenizer]
        assert data['chat_template'] == 'native template'
        return 'native processor'
    monkeypatch.setattr(transformers.PaddleOCRVLProcessor, 'from_args_and_dict', assemble)
    model = object.__new__(PaddleOcrVl16Vlm)
    assert model._load_processor(str(tmp_path), config) == 'native processor'
    assert calls == [image_processor, tokenizer]


def test_qwen35_4b_defaults_to_nf4_with_explicit_unquantized_override():
    implementation = MODELS["qwen3_5_4b"].implementation
    assert implementation.validate_settings(None)["quantization"] == "nf4"
    assert implementation.validate_settings({"quantization": "none"})["quantization"] == "none"
    assert MODELS["qwen3_5_9b"].implementation.validate_settings(None)["quantization"] == "none"


def test_queued_vlm_batches_variable_images_and_individual_prompts(tmp_path, engine):
    from copy import copy
    from pdfparse.models.scheduling import ModelQueue
    model = MODELS['qwen3_5_4b'].implementation(
        model_path=tmp_path, settings={'quantization': 'none', 'max_new_tokens': 16, 'batch_size': 1})
    queue = ModelQueue(model, batch_size=4, wait_seconds=.01)
    proxy = copy(model)
    proxy._inference_queue = queue
    try:
        images = [Image.new('RGB', (20, 30)), Image.new('L', (40, 20))]
        prompts = ['Describe figure A', 'Describe figure B with OCR']
        assert proxy.predict_batch(images, prompts=prompts) == ['figure-0', 'figure-1']
        assert engine.calls == 1  # Runtime batch_size=1 does not split scheduled batches.
        conversations = engine.templates[0][0]
        assert [conv[0]['content'][1]['text'] for conv in conversations] == prompts
        assert [conv[0]['content'][0]['image'].size for conv in conversations] == [(20, 30), (40, 20)]
    finally:
        queue.close()


def test_gpu_batch_preprocessing_overlaps_previous_generation(tmp_path, engine):
    from copy import copy
    from threading import Event, current_thread
    from pdfparse.models.scheduling import ModelQueue, ResourceScheduler
    model = MODELS['qwen3_5_4b'].implementation(
        model_path=tmp_path, settings={'quantization': 'none', 'max_new_tokens': 16, 'batch_size': 1})
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1, prefetch=2)
    queue = ModelQueue(model, scheduler=scheduler, batch_size=1)
    queue.device = 'cuda:0'  # Exercise GPU scheduling while fixture tensors remain on CPU.
    proxy = copy(model)
    proxy._inference_queue = queue
    second_prepared = Event()
    prepare = model._prepare_inputs
    generate = model.runtime.generate
    prepared = []
    def prepare_inputs(images, prompt):
        prepared.append(current_thread().name)
        if len(prepared) == 2:
            second_prepared.set()
        return prepare(images, prompt)
    def generation(**kwargs):
        assert current_thread().name == 'pdfparse-cuda:0'
        assert second_prepared.wait(5), 'Next input batch was not prepared during generation'
        return generate(**kwargs)
    model._prepare_inputs = prepare_inputs
    model.runtime.generate = generation
    try:
        assert proxy.predict_batch([Image.new('RGB', (20, 30)) for _ in range(2)]) == ['figure-0', 'figure-1']
        assert len(prepared) == 2
        assert all(name.startswith('pdfparse-cpu-') for name in prepared)
    finally:
        scheduler.close()


def test_gpu_next_generation_overlaps_cpu_decoding(tmp_path, engine):
    from copy import copy
    from threading import Event, current_thread
    from pdfparse.models.scheduling import ModelQueue, ResourceScheduler
    model = MODELS['qwen3_5_4b'].implementation(
        model_path=tmp_path, settings={'quantization': 'none', 'max_new_tokens': 16})
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1, prefetch=2)
    queue = ModelQueue(model, scheduler=scheduler, batch_size=1, wait_seconds=0)
    queue.device = 'cuda:0'
    proxy = copy(model)
    proxy._inference_queue = queue
    prepared, second_generated = Event(), Event()
    prepare, generate, decode = model._prepare_inputs, model.runtime.generate, model.processor.batch_decode
    preparations = []
    def preparation(images, prompt):
        preparations.append(1)
        if len(preparations) == 2:
            prepared.set()
        return prepare(images, prompt)
    def generation(**kwargs):
        assert current_thread().name == 'pdfparse-cuda:0'
        assert prepared.wait(5)
        outputs = generate(**kwargs)
        if engine.calls == 2:
            second_generated.set()
        return outputs
    def decoding(rows, **kwargs):
        assert current_thread().name.startswith('pdfparse-cpu-')
        assert second_generated.wait(5), 'GPU waited for CPU decoding before starting next generation'
        return decode(rows, **kwargs)
    model._prepare_inputs = preparation
    model.runtime.generate = generation
    model.processor.batch_decode = decoding
    records = []
    from pdfparse.progress import observe_inference
    try:
        with observe_inference(records.append):
            assert proxy.predict_batch([Image.new('RGB', (20, 30))] * 2) == ['figure-0', 'figure-1']
        assert len(records) == 2
        assert all(record['generation_seconds'] >= 0 and record['postprocess_seconds'] >= 0 for record in records)
        assert sum(record['output_tokens'] for record in records) == 4
        assert scheduler.requests == scheduler.memory == 0
    finally:
        scheduler.close()


def test_gpu_staged_decode_failure_releases_credits_and_recovers(tmp_path, engine):
    from copy import copy
    from pdfparse.models.scheduling import ModelQueue, ResourceScheduler
    model = MODELS['qwen3_5_4b'].implementation(
        model_path=tmp_path, settings={'quantization': 'none', 'max_new_tokens': 16})
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    queue = ModelQueue(model, scheduler=scheduler, batch_size=1, wait_seconds=0)
    queue.device = 'cuda:0'
    proxy = copy(model)
    proxy._inference_queue = queue
    decode = model.processor.batch_decode
    def fail(*args, **kwargs):
        raise ValueError('decode failure')
    model.processor.batch_decode = fail
    try:
        with pytest.raises(ValueError, match='decode failure'):
            proxy.predict(Image.new('RGB', (20, 30)))
        assert scheduler.requests == scheduler.memory == 0
        model.processor.batch_decode = decode
        assert proxy.predict(Image.new('RGB', (20, 30))) == 'figure-1'
    finally:
        scheduler.close()


def test_gpu_staged_generation_oom_keeps_batch_split_retry(tmp_path, engine):
    from copy import copy
    from pdfparse.models.scheduling import ModelQueue, ResourceScheduler
    model = MODELS['qwen3_5_4b'].implementation(
        model_path=tmp_path, settings={'quantization': 'none', 'max_new_tokens': 16})
    scheduler = ResourceScheduler(cpu_workers=1, cpu_threads=1)
    queue = ModelQueue(model, scheduler=scheduler, batch_size=2, wait_seconds=.05)
    queue.device = 'cuda:0'
    proxy = copy(model)
    proxy._inference_queue = queue
    generate = model.runtime.generate
    def limited(**kwargs):
        if kwargs['input_ids'].shape[0] > 1:
            raise torch.OutOfMemoryError('fixture limit')
        return generate(**kwargs)
    model.runtime.generate = limited
    try:
        assert proxy.predict_batch([Image.new('RGB', (20, 30))] * 2) == ['figure-0', 'figure-1']
        assert queue.batch_size == 1
        assert scheduler.requests == scheduler.memory == 0
    finally:
        scheduler.close()
