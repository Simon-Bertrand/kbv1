"""Shared batch and Studio inference profile."""
from .schemas import EnrichmentSelection, ModelSelection, StructuralChunking, RuntimeConfiguration


def default_runtime_configuration():
    return RuntimeConfiguration(
        picture_classifier=ModelSelection(name='dfc_v25', device='cpu'),
        ocr=ModelSelection(name='mineru2_5_pro_2605', device='cuda:0', settings={
            'batch_size': 1, 'dtype': 'float16', 'max_new_tokens': 8096,
            'alphabet': 'latin', 'reject_repetition': True,
        }),
        vlm=ModelSelection(name='qwen3_5_4b', device='cuda:0', settings={
            'batch_size': 1, 'dtype': 'float16', 'max_pixels': 262144,
            'max_new_tokens': 1024, 'quantization': 'nf4',
        }),
        chunking=StructuralChunking(chunk_size=512, target_tokens=384, min_tokens=128, respect_page_boundaries=False),
        enrichment=EnrichmentSelection(figures='vlm', fallback='vlm',
                                       text_ocr=True, page_vlm=False, confidence_threshold=0.75),
    )
