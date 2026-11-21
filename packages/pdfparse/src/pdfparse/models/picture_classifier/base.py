"""Document figure classification contract, independent of Docling and enrich."""

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from math import isclose, isfinite

from PIL import Image

from ..base import BaseInferenceModel, validate_images


@dataclass(frozen=True, slots=True)
class CategoryScore:
    label: str
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Category label must be a nonempty string")
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Category confidence must be finite and in [0, 1]")


@dataclass(frozen=True, slots=True)
class PictureClassification:
    """All native categories, sorted by decreasing probability.

    Scores sum to one. They are model probabilities, not calibrated confidence
    across models. No thresholding, renaming or fallback category is applied.
    """

    model_id: str
    scores: tuple[CategoryScore, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be a nonempty string")
        if not isinstance(self.scores, tuple) or not self.scores:
            raise ValueError("scores must be a nonempty tuple")
        if any(not isinstance(score, CategoryScore) for score in self.scores):
            raise TypeError("scores must contain CategoryScore instances")
        if len({score.label for score in self.scores}) != len(self.scores):
            raise ValueError("Duplicate category labels")
        probabilities = [score.confidence for score in self.scores]
        if probabilities != sorted(probabilities, reverse=True):
            raise ValueError("scores must be sorted by decreasing confidence")
        if not isclose(sum(probabilities), 1.0, abs_tol=1e-5):
            raise ValueError("Category probabilities must sum to one")

    @property
    def best(self) -> CategoryScore:
        return self.scores[0]


class DocumentClassifierModel(BaseInferenceModel[PictureClassification]):
    """A reusable DFC that accepts already extracted PIL crops.

    Implement only ``_predict_batch``. Return one full classification per image
    in input order; raise on failure rather than returning partial results.
    Instances load weights at construction and keep no per-document state.
    Concurrent use of the same instance is not guaranteed; use one per worker.
    """

    def predict(self, image: Image.Image) -> PictureClassification:
        return self.predict_batch([image])[0]

    def predict_batch(self, images: Sequence[Image.Image]) -> list[PictureClassification]:
        batch = validate_images(images)
        if not batch:
            return []
        results = list(self._predict_batch(batch))
        if len(results) != len(batch):
            raise ValueError("Classifier must return one result per input image")
        if any(not isinstance(result, PictureClassification) for result in results):
            raise TypeError("Classifier must return PictureClassification instances")
        return results

    @abstractmethod
    def _predict_batch(
        self, images: Sequence[Image.Image]
    ) -> Sequence[PictureClassification]:
        """Perform inference; the public methods handle common validation."""
