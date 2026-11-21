"""PP-DocLayout-S implementation with deferred Paddle runtime imports."""

import os

from docling_core.types.doc import DocItemLabel
from PIL import Image
from pydantic import Field

from .detector import BaseLayoutDetector, Detection
from ..base import ModelSettings


class PaddleLayoutSettings(ModelSettings):
    cpu_threads: int = Field(default=4, ge=1)
    enable_mkldnn: bool = False


# PP-DocLayout-S's 23 labels do not have a one-to-one Docling equivalent.
# Keep the projection explicit; unknown classes must raise instead of disappearing.
PADDLE_LABELS = {
    "doc_title": DocItemLabel.TITLE,
    "paragraph_title": DocItemLabel.SECTION_HEADER,
    "text": DocItemLabel.TEXT,
    "page_number": DocItemLabel.PAGE_FOOTER,
    "number": DocItemLabel.PAGE_FOOTER,
    "abstract": DocItemLabel.TEXT,
    "content": DocItemLabel.DOCUMENT_INDEX,
    "reference": DocItemLabel.TEXT,
    "footnote": DocItemLabel.FOOTNOTE,
    "header": DocItemLabel.PAGE_HEADER,
    "footer": DocItemLabel.PAGE_FOOTER,
    "algorithm": DocItemLabel.CODE,
    "formula": DocItemLabel.FORMULA,
    "formula_number": DocItemLabel.TEXT,
    "image": DocItemLabel.PICTURE,
    "figure_caption": DocItemLabel.CAPTION,
    "table": DocItemLabel.TABLE,
    "table_caption": DocItemLabel.CAPTION,
    "table_title": DocItemLabel.CAPTION,
    "seal": DocItemLabel.PICTURE,
    "figure_title": DocItemLabel.CAPTION,
    "chart": DocItemLabel.PICTURE,
    "chart_title": DocItemLabel.CAPTION,
    "figure": DocItemLabel.PICTURE,
    "header_image": DocItemLabel.PICTURE,
    "footer_image": DocItemLabel.PICTURE,
    "aside_text": DocItemLabel.TEXT,
}


class PPDocLayoutSDetector(BaseLayoutDetector):
    """PP-DocLayout-S via the optional PaddleOCR/Paddle runtime."""

    settings_model = PaddleLayoutSettings

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        required = ("inference.json", "inference.pdiparams", "inference.yml")
        for filename in required:
            if not (self.model_path / filename).is_file():
                raise FileNotFoundError(self.model_path / filename)
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        try:
            from paddleocr import LayoutDetection
        except ImportError as exc:
            raise ImportError(
                "PP-DocLayout-S requires pdfparse[layout-lite] and a PaddlePaddle "
                "CPU or GPU runtime. See README.md."
            ) from exc
        if self.device not in ("cpu", "cuda") and not self.device.startswith("cuda:"):
            raise ValueError(f"PP-DocLayout-S does not support device {self.device!r}")
        device = self.device.replace("cuda", "gpu", 1)
        self.runtime = LayoutDetection(
            model_name="PP-DocLayout-S",
            model_dir=str(self.model_path),
            device=device,
            **self.settings,
        )

    def predict(self, image: Image.Image) -> list[Detection]:
        import numpy as np

        # Paddle's ndarray reader expects BGR, PIL provides RGB.
        bgr = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
        results = list(self.runtime.predict(bgr, batch_size=1, threshold=0.0))
        if len(results) != 1:
            raise ValueError(f"Expected one Paddle layout prediction, got {len(results)}")
        output = []
        for box in results[0]["boxes"]:
            name = box["label"]
            try:
                label = PADDLE_LABELS[name]
            except KeyError as exc:
                raise ValueError(f"Unmapped Paddle layout label: {name!r}") from exc
            output.append(Detection(
                label=label,
                confidence=float(box["score"]),
                xyxy=tuple(float(v) for v in box["coordinate"]),
            ))
        return output
