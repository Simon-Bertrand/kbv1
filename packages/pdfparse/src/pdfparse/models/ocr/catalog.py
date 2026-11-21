"""Pinned recognition bundles; preparation uses the common model registry."""
from enum import StrEnum

from ..catalog import ModelCategory, ModelSource, ModelSpec
from ..vlm.catalog import MODELS as VLM_MODELS, VlmModel
from .got_ocr2 import GotOcr2Ocr
from .mineru2_5_pro import MinerU25ProOcr
from .pp_ocrv6_medium_rec import PPOcrV6MediumRec
from .rapidocr_v4 import RapidOcrV4
from .paddleocr_vl_1_6 import PaddleOcrVl16Ocr


class OcrModel(StrEnum):
    PADDLEOCR_VL_1_6 = "paddleocr_vl_1_6"
    MINERU2_5_PRO_2605 = "mineru2_5_pro_2605"
    GOT_OCR2 = "got_ocr2"
    PP_OCRV6_MEDIUM_REC = "pp_ocrv6_medium_rec"
    RAPIDOCR_V4 = "rapidocr_v4"


MODELS = {
    OcrModel.PADDLEOCR_VL_1_6: ModelSpec(
        ModelCategory.OCR, OcrModel.PADDLEOCR_VL_1_6.value, PaddleOcrVl16Ocr,
        VLM_MODELS[VlmModel.PADDLEOCR_VL_1_6].source,
    ),
    OcrModel.MINERU2_5_PRO_2605: ModelSpec(
        ModelCategory.OCR, OcrModel.MINERU2_5_PRO_2605.value, MinerU25ProOcr,
        ModelSource(repo_id="opendatalab/MinerU2.5-Pro-2605-1.2B",
                    revision="08aaea840498d49ce16247b6263196cf02814885",
                    files=("config.json", "model.safetensors", "generation_config.json",
                           "preprocessor_config.json", "chat_template.jinja", "tokenizer.json",
                           "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
                           "merges.txt", "vocab.json")),
    ),
    OcrModel.GOT_OCR2: ModelSpec(
        ModelCategory.OCR, OcrModel.GOT_OCR2.value, GotOcr2Ocr,
        ModelSource(repo_id="stepfun-ai/GOT-OCR-2.0-hf",
                    revision="d3017ef2c2c1395888c8d635c5e0508bcb0ac78d",
                    files=("config.json", "model.safetensors", "generation_config.json",
                           "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json",
                           "special_tokens_map.json")),
    ),
    OcrModel.PP_OCRV6_MEDIUM_REC: ModelSpec(
        ModelCategory.OCR, OcrModel.PP_OCRV6_MEDIUM_REC.value, PPOcrV6MediumRec,
        ModelSource(repo_id="PaddlePaddle/PP-OCRv6_medium_rec",
                    revision="e5a92bcbc5cc1b494628e458d267778f0704fd7c",
                    files=("inference.json", "inference.pdiparams", "inference.yml")),
    ),
    OcrModel.RAPIDOCR_V4: ModelSpec(
        ModelCategory.OCR, OcrModel.RAPIDOCR_V4.value, RapidOcrV4,
        ModelSource(repo_id="SWHL/RapidOCR", revision="1cfba2e90fc938db55889873735088de210cc173",
                    files=("PP-OCRv4/ch_PP-OCRv4_det_infer.onnx", "PP-OCRv4/ch_PP-OCRv4_rec_infer.onnx")),
    ),
}
