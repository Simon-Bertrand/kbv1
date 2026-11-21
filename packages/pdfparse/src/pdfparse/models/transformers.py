"""Native Transformers image-to-text inference shared by OCR and VLM models."""
from collections.abc import Mapping, Sequence
from pathlib import Path
from dataclasses import dataclass
from contextlib import nullcontext
from typing import ClassVar, Literal

from PIL import Image
from pydantic import Field, JsonValue

from .base import BaseInferenceModel, ModelSettings, validate_images
from pdfparse.progress import trace_inference


@dataclass
class PreparedInputs:
    inputs: object
    prompts: list[str]
    used: bool = False
    generated: object = None


class GenerationSettings(ModelSettings):
    max_new_tokens: int = Field(default=512, ge=1)
    batch_size: int = Field(default=4, ge=1)
    dtype: Literal["auto", "float32", "float16", "bfloat16"] = "auto"
    attn_implementation: Literal["eager", "sdpa", "flash_attention_2"] = "sdpa"
    quantization: Literal["none", "nf4"] = "none"
    use_cache: bool = True


class TransformersImageTextModel(BaseInferenceModel[str]):
    """Reuse one native Transformers runtime, without Hub access or remote code."""

    settings_model = GenerationSettings
    model_type: ClassVar[str]
    default_prompt: ClassVar[str] = "Describe this image."

    def __init__(self, *, model_path: Path | str, device: str = "cpu",
                 settings: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(model_path=model_path, device=device, settings=settings)
        if self.settings["quantization"] == "nf4" and not self.device.startswith("cuda"):
            raise ValueError("NF4 loading requires an explicitly selected CUDA device")
        from transformers import AutoModelForImageTextToText

        path = str(self.model_path)
        config = self._load_config(path)
        if config.model_type != self.model_type:
            raise ValueError(f"Expected {self.model_type} weights, got {config.model_type}")
        self.processor = self._load_processor(path, config)
        self.processor.tokenizer.padding_side = "left"
        options = {}
        if self.settings["quantization"] == "nf4":
            import torch
            from transformers import BitsAndBytesConfig

            compute_dtype = torch.float16 if self.settings["dtype"] == "auto" else getattr(torch, self.settings["dtype"])
            options = {
                "device_map": {"": self.device},
                "quantization_config": BitsAndBytesConfig(
                    load_in_4bit=True, bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=compute_dtype, bnb_4bit_use_double_quant=True,
                ),
            }
        runtime = AutoModelForImageTextToText.from_pretrained(
            path, config=config, local_files_only=True, trust_remote_code=False,
            dtype=self.settings["dtype"], attn_implementation=self.settings["attn_implementation"],
            **options,
        )
        self.runtime = (runtime if options else runtime.to(self.device)).eval()

    def _load_config(self, path: str):
        from transformers import AutoConfig
        return AutoConfig.from_pretrained(path, local_files_only=True, trust_remote_code=False)

    def _load_processor(self, path: str, config):
        from transformers import AutoProcessor
        return AutoProcessor.from_pretrained(path, local_files_only=True, trust_remote_code=False)

    def _template_kwargs(self) -> dict:
        return {}

    def _processor_kwargs(self) -> dict:
        return {"padding": True}

    def _generation_kwargs(self, prompt_length: int) -> dict:
        return {}

    def _decode_kwargs(self, prompt: str) -> dict:
        return {"skip_special_tokens": True, "clean_up_tokenization_spaces": False}

    def _prepare_inputs(self, images: Sequence[Image.Image], prompt: str | Sequence[str]):
        prompts = [prompt] * len(images) if isinstance(prompt, str) else prompt
        conversations = [[{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": prompt},
        ]}] for image, prompt in zip(images, prompts, strict=True)]
        return self.processor.apply_chat_template(
            conversations, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt", processor_kwargs=self._processor_kwargs(),
            **self._template_kwargs(),
        )

    def _predict_batch(self, images: Sequence[Image.Image], prompt: str | Sequence[str]) -> list[str]:
        batch = validate_images(images)
        if not isinstance(prompt, (str, list, tuple)) or (isinstance(prompt, str) and not prompt.strip()):
            raise ValueError("Image-text prompt must be a nonempty string or a sequence of prompts")
        prompts = [prompt] * len(batch) if isinstance(prompt, str) else list(prompt)
        if len(prompts) != len(batch) or any(not isinstance(p, str) or not p.strip() for p in prompts):
            raise ValueError("Image-text prompt must be a nonempty string")
        if not batch:
            return []
        results: list[str] = []
        from .scheduling import _execution_batch_size, _prepared_inputs
        size = _execution_batch_size.get() or self.settings["batch_size"]
        for start in range(0, len(batch), size):
            images = batch[start:start + size]
            current_prompts = prompts[start:start + size]
            batch_prompt = current_prompts[0] if len(set(current_prompts)) == 1 else current_prompts
            prepared = _prepared_inputs.get()
            generated = None
            if prepared is not None and not prepared.used and prepared.prompts == current_prompts:
                inputs = prepared.inputs
                generated = prepared.generated
                prepared.used = True
            else:
                with getattr(self, '_processor_lock', nullcontext()):
                    inputs = self._prepare_inputs([image.convert("RGB") for image in images], batch_prompt)
            if generated is None:
                generated = self._generate_inputs(inputs, len(images))
            tail, input_tokens, output_tokens = generated
            from pdfparse.progress import inference_tokens
            inference_tokens(input_tokens=input_tokens, output_tokens=output_tokens)
            decode_options = [self._decode_kwargs(item_prompt) for item_prompt in current_prompts]
            with getattr(self, '_processor_lock', nullcontext()):
                if all(options == decode_options[0] for options in decode_options):
                    texts = self.processor.batch_decode(tail, **decode_options[0])
                else:
                    texts = [self.processor.batch_decode(tail[index:index + 1], **options)[0]
                             for index, options in enumerate(decode_options)]
            if len(texts) != len(images) or any(not isinstance(text, str) for text in texts):
                raise ValueError("Image-text model must decode one string per input image")
            results.extend(texts)
        return results

    def _generate_inputs(self, inputs, count):
        """Return CPU token IDs so decoding never needs the accelerator worker."""
        import torch
        prompt_length = inputs['input_ids'].shape[1]
        # Count the CPU mask before moving inputs, avoiding a GPU scalar sync.
        input_tokens = int(inputs['attention_mask'].sum().item()) if 'attention_mask' in inputs else inputs['input_ids'].numel()
        inputs = inputs.to(self.device, dtype=self.runtime.dtype)
        with torch.inference_mode():
            outputs = self.runtime.generate(
                **inputs, max_new_tokens=self.settings['max_new_tokens'],
                use_cache=self.settings['use_cache'], do_sample=False,
                return_dict_in_generate=False,
                **self._generation_kwargs(prompt_length),
            )
        if outputs.ndim != 2 or outputs.shape[0] != count:
            raise ValueError('Image-text model must return one generated sequence per input image')
        tail = outputs[:, prompt_length:].cpu()
        padding = getattr(self.processor.tokenizer, 'pad_token_id', None)
        output_tokens = int((tail != padding).sum().item()) if padding is not None else tail.numel()
        return tail, input_tokens, output_tokens

    @trace_inference
    def _generate_prepared(self, images, prepared):
        return self._generate_inputs(prepared.inputs, len(images))
