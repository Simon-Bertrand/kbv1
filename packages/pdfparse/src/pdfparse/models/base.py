"""Common construction and image inference API, without importing a runtime."""
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, ClassVar

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

Device = Annotated[str, StringConstraints(pattern=r"^(cpu|mps|cuda(?::[0-9]+)?|xpu(?::[0-9]+)?)$")]


def model_provenance(model, role: str) -> dict:
    """Compact identity of an executed service, without weights or runtime state."""
    identity = getattr(model, 'model_identity', {})
    weights = identity.get('weights', {})
    source = weights.get('source', weights)
    return {'role': role, 'name': identity.get('name', type(model).__name__),
            'implementation': f'{type(model).__module__}.{type(model).__qualname__}',
            'revision': source.get('revision'), 'device': getattr(model, 'device', None)}


class ModelConfiguration(BaseModel):
    """Runtime arguments shared by all categories; the backend checks availability."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    model_path: Path
    device: Device = "cpu"
    settings: dict[str, JsonValue] = Field(default_factory=dict)


class ModelSettings(BaseModel):
    """Strict options for a backend; subclass to declare supported parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, validate_default=True)


def validate_images(images: Sequence[Image.Image]) -> list[Image.Image]:
    batch = list(images)
    for image in batch:
        if not isinstance(image, Image.Image):
            raise TypeError("Expected PIL.Image.Image crops")
        if image.width <= 0 or image.height <= 0:
            raise ValueError("Image crops must have positive dimensions")
    return batch


class BaseInferenceModel[Prediction](ABC):
    """Implement category inference; reuse one loaded instance per worker/device.

    Constructors accept the same three keyword arguments. A custom constructor
    must forward them to super(); downloads never belong here.
    """
    settings_model: ClassVar[type[ModelSettings] | None] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from pdfparse.progress import trace_inference
        from .scheduling import queued_inference
        for name in ('predict', 'predict_batch', 'predict_regions', 'predict_table',
                     'predict_regions_batch', 'predict_table_batch'):
            if name in cls.__dict__:
                setattr(cls, name, queued_inference(trace_inference(cls.__dict__[name])))

    @classmethod
    def validate_settings(cls, settings: Mapping[str, JsonValue] | None) -> dict[str, JsonValue]:
        """Validate before loading weights; custom models may opt into a schema."""
        values = dict(settings or {})
        return cls.settings_model.model_validate(values).model_dump(mode="json") if cls.settings_model else values

    def __init__(self, *, model_path: Path | str, device: str = "cpu",
                 settings: Mapping[str, JsonValue] | None = None) -> None:
        config = ModelConfiguration(model_path=model_path, device=device, settings=self.validate_settings(settings))
        self.model_path = config.model_path.expanduser().resolve(strict=True)
        self.device = config.device
        self.settings = config.settings

    @abstractmethod
    def predict(self, image: Image.Image) -> Prediction: ...

    def predict_batch(self, images: Sequence[Image.Image]) -> list[Prediction]:
        return [self.predict(image) for image in validate_images(images)]
