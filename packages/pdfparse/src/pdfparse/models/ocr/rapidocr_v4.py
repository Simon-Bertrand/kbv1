"""Multiline PP-OCRv4 detection and recognition using RapidOCR and local ONNX."""
from collections.abc import Mapping
from pathlib import Path

from PIL import Image
from pydantic import Field, JsonValue

from . import BaseOcrModel
from ..base import ModelSettings, validate_images


class RapidOcrSettings(ModelSettings):
    cpu_threads: int = Field(default=4, ge=1)
    rec_batch_size: int = Field(default=16, ge=1)
    text_score: float = Field(default=0.5, ge=0, le=1)


class RapidOcrV4(BaseOcrModel):
    """Reuse detector/recognizer sessions; no orientation model or download fallback."""

    settings_model = RapidOcrSettings

    def __init__(self, *, model_path: Path | str, device: str = "cpu",
                 settings: Mapping[str, JsonValue] | None = None) -> None:
        super().__init__(model_path=model_path, device=device, settings=settings)
        if self.device != "cpu" and self.device != "cuda" and not self.device.startswith("cuda:"):
            raise ValueError(f"RapidOCR does not support device {self.device!r}")
        paths = [self.model_path / "PP-OCRv4" / f"ch_PP-OCRv4_{task}_infer.onnx" for task in ("det", "rec")]
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(path)
        import onnxruntime as ort
        from omegaconf import flag_override
        from rapidocr import EngineType, ModelType, OCRVersion, RapidOCR

        provider = "CPUExecutionProvider" if self.device == "cpu" else "CUDAExecutionProvider"
        if provider not in ort.get_available_providers():
            raise ValueError(f"Requested ONNX provider is unavailable: {provider}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.settings["cpu_threads"]
        provider_options = {} if self.device == "cpu" else {
            "device_id": int(self.device.split(":")[1]) if ":" in self.device else 0,
        }
        providers = [(provider, provider_options)]
        sessions = [ort.InferenceSession(str(path), sess_options=options, providers=providers) for path in paths]
        for session in sessions:
            session.disable_fallback()
            if session.get_providers()[0] != provider:
                raise RuntimeError(f"ONNX did not initialize the requested provider: {provider}")
        if not sessions[1].get_modelmeta().custom_metadata_map.get("character"):
            raise ValueError("RapidOCR recognizer must contain its character dictionary")
        self.runtime = RapidOCR(params={
            "Global.use_det": True, "Global.use_cls": False, "Global.use_rec": True,
            "Global.text_score": self.settings["text_score"],
            "Det.engine_type": EngineType.ONNXRUNTIME, "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": OCRVersion.PPOCRV4, "Rec.ocr_version": OCRVersion.PPOCRV4,
            "Det.model_type": ModelType.MOBILE, "Rec.model_type": ModelType.MOBILE,
            "Det.model_path": str(paths[0]), "Rec.model_path": str(paths[1]),
            "Det.mean": [0.485, 0.456, 0.406], "Det.std": [0.229, 0.224, 0.225],
            "Rec.rec_batch_num": self.settings["rec_batch_size"],
        })
        # RapidOCR 3.10 loads lazily; bind our checked sessions before initialization.
        with flag_override(self.runtime.cfg, "allow_objects", True):
            self.runtime.cfg.Det.session, self.runtime.cfg.Rec.session = sessions
        self.runtime._load_det_model()
        self.runtime._load_rec_model()

    def predict(self, image: Image.Image) -> str:
        validate_images([image])
        result = self.runtime(image)
        if result.txts is None:
            return ""
        if any(not isinstance(text, str) for text in result.txts):
            raise TypeError("RapidOCR must return recognized strings")
        return "\n".join(result.txts)
