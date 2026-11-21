"""PaddleOCR-VL 1.6 using its native Transformers implementation."""
import inspect

from .transformers import TransformersVlmModel


class PaddleOcrVl16Vlm(TransformersVlmModel):
    """Crop-local recognition; pass the native task prompt for tables/charts/formulae."""

    model_type = "paddleocr_vl"
    default_prompt = "OCR:"

    def _load_config(self, path: str):
        from transformers.models.paddleocr_vl.configuration_paddleocr_vl import PaddleOCRVLConfig, PaddleOCRTextConfig

        data, _ = PaddleOCRVLConfig.get_config_dict(path, local_files_only=True, trust_remote_code=False)
        # Preserve the flat Hub checkpoint while declaring the model's native MRoPE key.
        fields = set(inspect.signature(PaddleOCRTextConfig).parameters) | {'rope_scaling', 'rope_theta'}
        text = dict(data.get('text_config') or {key: value for key, value in data.items() if key in fields})
        text.setdefault('dtype', data.get('torch_dtype', data.get('dtype')))
        text['ignore_keys_at_rope_validation'] = {'mrope_section'}
        data['text_config'] = PaddleOCRTextConfig(**text)
        return PaddleOCRVLConfig.from_dict(data)

    def _load_processor(self, path: str, config):
        from transformers import AutoImageProcessor, AutoTokenizer, PaddleOCRVLProcessor

        options = {'local_files_only': True, 'trust_remote_code': False}
        data, kwargs = PaddleOCRVLProcessor.get_processor_dict(path, **options)
        components = [AutoImageProcessor.from_pretrained(path, config=config, **options),
                      AutoTokenizer.from_pretrained(path, config=config, **options)]
        return PaddleOCRVLProcessor.from_args_and_dict(components, data, **kwargs)
