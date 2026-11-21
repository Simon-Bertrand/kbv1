"""One local model tree and worker-local runtime reuse."""
from __future__ import annotations
from collections import OrderedDict
from collections.abc import Mapping
from importlib import import_module
import inspect
import json
from pathlib import Path
from threading import RLock
from typing import Literal, TYPE_CHECKING, overload

from pydantic import JsonValue

from .base import BaseInferenceModel, ModelConfiguration
from .catalog import ModelCategory, ModelSource, ModelSpec
from .download import weight_identity
from .paths import model_directory, models_root, require_model_files

if TYPE_CHECKING:
    from .layout import BaseLayoutDetector
    from .ocr import BaseOcrModel
    from .picture_classifier import DocumentClassifierModel
    from .vlm import BaseVlmModel

_CONTRACTS = {
    ModelCategory.LAYOUT: (".layout.detector", "BaseLayoutDetector"),
    ModelCategory.OCR: (".ocr", "BaseOcrModel"),
    ModelCategory.PICTURE_CLASSIFIER: (".picture_classifier.base", "DocumentClassifierModel"),
    ModelCategory.VLM: (".vlm", "BaseVlmModel"),
}


class ModelRegistry:
    """Provision explicitly, then load local weights on an explicit device.

    Runtime instances are reused within this registry, never serialized. Use a
    registry per worker when inference services do not support concurrent calls.
    Models are provisioned externally in MODELS_DIR and opened in place.
    """
    def __init__(self, root: Path | str | None = None, *, builtins: bool = True,
                 max_instances: int = 4) -> None:
        self.root = models_root() if root is None else Path(root).expanduser().resolve()
        self._specs: dict[tuple[ModelCategory, str], ModelSpec] = {}
        if type(max_instances) is not int or max_instances < 1:
            raise ValueError('max_instances must be a positive integer')
        self.max_instances = max_instances
        self._instances: OrderedDict[tuple, BaseInferenceModel] = OrderedDict()
        self._lock = RLock()
        self._signatures = {}
        if builtins:
            for family in ("layout", "picture_classifier", "ocr", "vlm"):
                for spec in import_module(f".{family}.catalog", __package__).MODELS.values():
                    self.register(spec.category, spec.name, spec.implementation, spec.source)

    def register(self, category: ModelCategory | str, name: str,
                 implementation: type[BaseInferenceModel], source: ModelSource) -> None:
        spec = ModelSpec(ModelCategory(category), name, implementation, source)
        module, symbol = _CONTRACTS[spec.category]
        contract = getattr(import_module(module, __package__), symbol)
        if not isinstance(implementation, type) or not issubclass(implementation, contract):
            raise TypeError(f"{spec.category} models must implement {contract.__name__}")
        if inspect.isabstract(implementation):
            raise TypeError("Register a concrete model implementation")
        key = (spec.category, name)
        with self._lock:
            if key in self._specs:
                raise ValueError(f"Model already registered: {spec.category}/{name}")
            self._specs[key] = spec

    def get(self, category: ModelCategory | str, name: str) -> ModelSpec:
        try:
            return self._specs[(ModelCategory(category), name)]
        except KeyError as exc:
            raise KeyError(f"Unknown model: {category}/{name}") from exc

    def list(self, category: ModelCategory | str | None = None) -> tuple[ModelSpec, ...]:
        selected = ModelCategory(category) if category is not None else None
        return tuple(spec for spec in self._specs.values() if selected is None or spec.category == selected)

    def weights_path(self, category: ModelCategory | str, name: str) -> Path:
        spec = self.get(category, name)
        if spec.source.local_path is None:
            return model_directory(spec.category.value, spec.name, root=self.root)
        directory = spec.source.local_path.resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError(f'Model directory escapes MODELS_DIR: {directory}')
        return directory

    def download_all(self, category: ModelCategory | str | None = None, *,
                     local_files_only: bool = False) -> dict[str, Path]:
        """Check the local files for the selected models."""
        return self._check_local(self.list(category))

    def download(self, category: ModelCategory | str, name: str, *,
                 local_files_only: bool = False) -> Path:
        self._check_local((self.get(category, name),))
        return self.weights_path(category, name)

    def _check_local(self, specs: tuple[ModelSpec, ...]) -> dict[str, Path]:
        paths = {}
        with self._lock:
            for spec in specs:
                changed = True
                try:
                    path = self.weights_path(spec.category, spec.name)
                    require_model_files(path, spec.source.files)
                    changed = self._signatures.get((spec.category, spec.name)) != self._signature(spec)
                finally:
                    if changed:
                        self._instances = OrderedDict((key, instance) for key, instance in self._instances.items()
                                                      if key[:2] != (spec.category, spec.name))
                paths[f"{spec.category.value}/{spec.name}"] = path
        return paths

    def verify(self, category: ModelCategory | str, name: str) -> None:
        """Validate the local files and compute their content fingerprints."""
        spec = self.get(category, name)
        with self._lock:
            try:
                weight_identity(spec.source, self.weights_path(category, name))
            except (OSError, ValueError):
                self._instances = OrderedDict((key, value) for key, value in self._instances.items()
                                              if key[:2] != (spec.category, spec.name))
                raise

    def is_prepared(self, category: ModelCategory | str, name: str) -> bool:
        """Check local snapshot files, without downloading or loading."""
        spec = self.get(category, name)
        try:
            self._prepared(spec)
        except (OSError, ValueError):
            return False
        return True

    def unload(self) -> None:
        """Release cached runtime references; callers must release their own too.

        Call only between inference jobs. Weight files remain available locally.
        """
        with self._lock:
            self._instances.clear()

    def _signature(self, spec: ModelSpec) -> tuple:
        root = self.weights_path(spec.category, spec.name)
        return tuple((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                     for name in spec.source.files for stat in [(root / name).stat()])

    def _prepared(self, spec: ModelSpec) -> None:
        require_model_files(self.weights_path(spec.category, spec.name), spec.source.files)

    @overload
    def load(self, category: Literal[ModelCategory.LAYOUT, "layout"], name: str, *,
             device: str = "cpu", settings: Mapping[str, JsonValue] | None = None) -> BaseLayoutDetector: ...

    @overload
    def load(self, category: Literal[ModelCategory.OCR, "ocr"], name: str, *,
             device: str = "cpu", settings: Mapping[str, JsonValue] | None = None) -> BaseOcrModel: ...

    @overload
    def load(self, category: Literal[ModelCategory.PICTURE_CLASSIFIER, "picture_classifier"], name: str, *,
             device: str = "cpu", settings: Mapping[str, JsonValue] | None = None) -> DocumentClassifierModel: ...

    @overload
    def load(self, category: Literal[ModelCategory.VLM, "vlm"], name: str, *,
             device: str = "cpu", settings: Mapping[str, JsonValue] | None = None) -> BaseVlmModel: ...

    @overload
    def load(self, category: ModelCategory | str, name: str, *,
             device: str = "cpu", settings: Mapping[str, JsonValue] | None = None) -> BaseInferenceModel: ...

    def load(self, category: ModelCategory | str, name: str, *, device: str = "cpu",
             settings: Mapping[str, JsonValue] | None = None) -> BaseInferenceModel:
        """Validate local weights on first load; cached runtimes require no disk I/O.

        Use verify() to detect external weight changes; download() invalidates
        affected runtimes when the local weights have changed.
        """
        spec = self.get(category, name)
        config = ModelConfiguration(model_path=self.root,
                                    device=device, settings=spec.implementation.validate_settings(settings))
        key = (spec.category, spec.name, config.device,
               json.dumps(config.settings, sort_keys=True, separators=(",", ":")))
        with self._lock:
            if key not in self._instances:
                self._prepared(spec)
                path = self.weights_path(spec.category, spec.name)
                identity = weight_identity(spec.source, path)
                while len(self._instances) >= self.max_instances:
                    self._instances.popitem(last=False)
                instance = spec.implementation(model_path=path, device=config.device, settings=config.settings)
                instance.model_identity = {'category': spec.category.value, 'name': spec.name, 'weights': identity}
                self._signatures[(spec.category, spec.name)] = self._signature(spec)
                self._instances[key] = instance
            self._instances.move_to_end(key)
            return self._instances[key]

    def cache_info(self) -> dict[str, int]:
        """Count retained instances; callers may still own references to evicted models."""
        with self._lock:
            return {'instances': len(self._instances), 'max_instances': self.max_instances}
