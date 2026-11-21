"""PaddleOCR-VL 1.6 exposed as crop-local text recognition."""
import re

from PIL import Image

from ..vlm.paddleocr_vl_1_6 import PaddleOcrVl16Vlm
from . import BaseSpatialOcrModel, OcrRegion
from .transformers import TransformersOcrModel


def _spotting_regions(text: str, size: tuple[int, int], *, eos_token: str) -> list[OcrRegion]:
    """Decode the native eight LOC_0..1000 tokens per quadrilateral."""
    pattern = re.compile(r'(.*?)((?:<\|LOC_\d+\|>){8})', re.DOTALL)
    regions = []
    end = 0
    width, height = size
    for match in pattern.finditer(text):
        content = match[1].strip()
        if '<|LOC_' in content:
            raise ValueError('Malformed PaddleOCR spotting coordinates')
        coords = [int(value) for value in re.findall(r'LOC_(\d+)', match[2])]
        if any(value > 1000 for value in coords):
            raise ValueError('PaddleOCR spotting coordinates must be in 0..1000')
        if content:
            xs, ys = coords[::2], coords[1::2]
            regions.append(OcrRegion(content, (min(xs) * width / 1000, min(ys) * height / 1000,
                                               max(xs) * width / 1000, max(ys) * height / 1000)))
        end = match.end()
    remaining = text[end:].strip()
    if eos_token:
        remaining = remaining.removesuffix(eos_token).strip()
    if remaining:
        raise ValueError('Incomplete PaddleOCR spotting output; increase max_new_tokens')
    return regions


class PaddleOcrVl16Ocr(TransformersOcrModel, BaseSpatialOcrModel):
    """Use the native OCR task on the shared local image-to-text runtime."""

    model_type = PaddleOcrVl16Vlm.model_type
    default_prompt = PaddleOcrVl16Vlm.default_prompt
    _load_config = PaddleOcrVl16Vlm._load_config
    _load_processor = PaddleOcrVl16Vlm._load_processor

    def _prepare_inputs(self, images, prompt):
        if prompt != 'Spotting:':
            return super()._prepare_inputs(images, prompt)
        conversations = [[{'role': 'user', 'content': [
            {'type': 'image', 'image': image}, {'type': 'text', 'text': prompt},
        ]}] for image in images]
        return self.processor.apply_chat_template(
            conversations, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors='pt', processor_kwargs={
                'padding': True,
                'images_kwargs': {'size': {
                    'shortest_edge': self.processor.image_processor.size['shortest_edge'],
                    'longest_edge': 2048 * 28 * 28,
                }},
            },
        )

    def _decode_kwargs(self, prompt):
        return {**super()._decode_kwargs(prompt), 'skip_special_tokens': prompt != 'Spotting:'}

    def predict_regions(self, image: Image.Image) -> list[OcrRegion]:
        return self.predict_regions_batch([image])[0]

    def predict_regions_batch(self, images):
        texts = self._predict_batch(images, 'Spotting:')
        return [_spotting_regions(text, image.size, eos_token=self.processor.tokenizer.eos_token or '')
                for image, text in zip(images, texts, strict=True)]
