"""GOT-OCR 2.0 recognition with native Transformers preprocessing."""
from collections.abc import Sequence
from typing import Literal

from PIL import Image

from .transformers import OcrGenerationSettings, TransformersOcrModel


class GotOcr2Settings(OcrGenerationSettings):
    # Transformers' GOT implementation has no SDPA/FlashAttention backend.
    attn_implementation: Literal['eager'] = 'eager'
    format: bool = False


class GotOcr2Ocr(TransformersOcrModel):
    settings_model = GotOcr2Settings
    model_type = "got_ocr2"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # The bundle's generation_config omits EOS; stop at the native message end.
        self.runtime.generation_config.eos_token_id = self.processor.tokenizer.convert_tokens_to_ids("<|im_end|>")

    def _prepare_inputs(self, images: Sequence[Image.Image], prompt: str):
        return self.processor(images=list(images), return_tensors="pt", padding=True,
                              format=self.settings["format"])
