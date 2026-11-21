"""Crop-local OCR strategy on the shared image-to-text runtime."""
from collections.abc import Sequence
from typing import ClassVar, Literal
import unicodedata

from PIL import Image
from pydantic import Field

from . import BaseOcrModel
from ..transformers import GenerationSettings, TransformersImageTextModel
from pdfparse.progress import Stage, report_progress


class OcrGenerationSettings(GenerationSettings):
    max_new_tokens: int = Field(default=2048, ge=1)
    alphabet: Literal['any', 'latin'] = 'any'
    reject_repetition: bool = False


def _ocr_rejection(text: str, *, alphabet: str, reject_repetition: bool) -> str | None:
    if alphabet == 'latin' and any(char.isalpha() and not unicodedata.name(char, '').startswith('LATIN') for char in text):
        return 'alphabet non latin'
    if reject_repetition:
        tail = text.strip()[-128:]
        for size in range(1, min(16, len(tail) // 6) + 1):
            pattern = tail[-size:]
            if len(pattern) * 6 >= 24 and any(char.isalpha() for char in pattern) and tail.endswith(pattern * 6):
                return 'boucle de répétition'
    return None


class TransformersOcrModel(TransformersImageTextModel, BaseOcrModel):
    settings_model = OcrGenerationSettings
    # Execution tuning only: keep out of serialized model/profile settings so
    # caches produced before the scheduler optimization retain their identity.
    guard_check_interval: ClassVar[int] = 8

    def predict(self, image: Image.Image) -> str:
        return self.predict_batch([image])[0]

    def predict_batch(self, images: Sequence[Image.Image]) -> list[str]:
        texts = self._predict_batch(images, self.default_prompt)
        for index, text in enumerate(texts):
            reason = _ocr_rejection(text, alphabet=self.settings['alphabet'],
                                    reject_repetition=self.settings['reject_repetition'])
            if reason:
                report_progress(Stage.OCR, f'Sortie OCR écartée : {reason}')
                texts[index] = ''
        return texts

    def _generation_kwargs(self, prompt_length: int) -> dict:
        if self.settings['alphabet'] == 'any' and not self.settings['reject_repetition']:
            return {}
        import torch
        from transformers import StoppingCriteriaList

        interval = self.guard_check_interval
        stopped = None
        def stop_invalid_ocr(input_ids, scores, **kwargs):
            nonlocal stopped
            if stopped is None:
                stopped = torch.zeros(input_ids.shape[0], dtype=torch.bool, device=input_ids.device)
            # Inspect the first token, then only each interval. Reuse the mask
            # on intervening steps without decoding/copying CUDA IDs to CPU.
            generated = input_ids.shape[1] - prompt_length
            if (generated - 1) % interval:
                return stopped

            # A bounded generated suffix avoids decoding the prompt or a growing history.
            suffix = input_ids[:, max(prompt_length, input_ids.shape[1] - 64):]
            texts = self.processor.batch_decode(suffix, skip_special_tokens=True, clean_up_tokenization_spaces=False)
            rejected = [_ocr_rejection(text, alphabet=self.settings['alphabet'],
                                      reject_repetition=self.settings['reject_repetition']) is not None for text in texts]
            stopped = stopped | torch.tensor(rejected, dtype=torch.bool, device=input_ids.device)
            return stopped

        return {'stopping_criteria': StoppingCriteriaList([stop_invalid_ocr])}
