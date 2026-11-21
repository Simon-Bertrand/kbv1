"""Versioned document persistence and explicitly registered wrapper codecs."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from functools import cache
import json
import math
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from pydantic import TypeAdapter

from . import abstraction as data_models
from .abstraction import EnrichedDocument, EnrichedElement


T = TypeVar("T", bound=EnrichedElement)
Migration = Callable[[dict[str, Any]], dict[str, Any]]
DOCUMENT_FORMAT = "pdfparse.enriched_document"
DOCUMENT_SCHEMA_VERSION = 1


def _check_durable_data(value: Any, active: set[int] | None = None) -> None:
    """Reject live objects, cycles and NaN before serializers can coerce them."""
    if isinstance(value, Enum):
        _check_durable_data(value.value, active)
        return
    if value is None or isinstance(value, (str, bool, int, Path)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite numbers cannot be persisted")
        return
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError("Cyclic wrapper payload cannot be persisted")
    active.add(identity)
    try:
        if is_dataclass(value) and not isinstance(value, type):
            values = (getattr(value, item.name) for item in fields(value))
        elif isinstance(value, dict):
            values = (*value.keys(), *value.values())
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = value
        else:
            raise TypeError(f"Unsupported persistent value: {type(value).__name__}")
        for item in values:
            _check_durable_data(item, active)
    finally:
        active.remove(identity)


class WrapperCodec(ABC, Generic[T]):
    """Encode only durable data and restore its original Python field types."""

    @abstractmethod
    def encode(self, element: T) -> dict[str, Any]: ...

    @abstractmethod
    def decode(self, payload: dict[str, Any]) -> T: ...


@dataclass(frozen=True)
class DataclassWrapperCodec(WrapperCodec[T]):
    """Default codec for typed dataclass wrappers, including nested custom fields."""

    _adapter: TypeAdapter[T]
    _field_names: frozenset[str]

    def __init__(self, wrapper_type: type[T]) -> None:
        if not is_dataclass(wrapper_type):
            raise TypeError("Provide a custom WrapperCodec for a non-dataclass wrapper")
        object.__setattr__(self, "_adapter", TypeAdapter(wrapper_type))
        object.__setattr__(self, "_field_names", frozenset(field.name for field in fields(wrapper_type) if field.init))

    def encode(self, element: T) -> dict[str, Any]:
        _check_durable_data(element)
        return self._adapter.dump_python(element, mode="json", warnings="error")

    def decode(self, payload: dict[str, Any]) -> T:
        extra = payload.keys() - self._field_names
        if extra:
            raise ValueError(f"Unknown wrapper fields: {sorted(extra)}")
        return self._adapter.validate_python(payload)


@dataclass(frozen=True)
class _WrapperRegistration:
    type_id: str
    wrapper_type: type[EnrichedElement]
    version: int
    codec: WrapperCodec
    migrations: Mapping[int, Migration]


def _version(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"Schema version must be a positive integer: {value!r}")
    return value


class WrapperRegistry:
    """Map stable namespaced IDs to exact wrapper classes and versioned codecs.

    Registry membership authorizes deserialization; JSON never imports classes.
    A custom subclass needs its own registration to avoid dropping extra fields.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, _WrapperRegistration] = {}
        self._by_class: dict[type[EnrichedElement], str] = {}
        self._frozen = False

    def register(
        self,
        type_id: str,
        wrapper_type: type[T],
        *,
        version: int = 1,
        codec: WrapperCodec[T] | None = None,
        migrations: Mapping[int, Migration] | None = None,
    ) -> None:
        if self._frozen:
            raise RuntimeError("Wrapper registry is frozen; configure a copy before creating a pipeline")
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:[.][a-z][a-z0-9_]*)+", type_id):
            raise ValueError("Wrapper type IDs must be stable namespaced names, e.g. experiment.summary")
        if not isinstance(wrapper_type, type) or not issubclass(wrapper_type, EnrichedElement):
            raise TypeError("Wrapper class must inherit EnrichedElement")
        version = _version(version)
        if type_id in self._by_id or wrapper_type in self._by_class:
            raise ValueError(f"Duplicate wrapper type ID or class: {type_id}")
        steps = dict(migrations or {})
        if any(type(key) is not int or not 1 <= key < version or not callable(fn) for key, fn in steps.items()):
            raise ValueError("Migration keys must be versions below the current wrapper version")
        chosen_codec = codec if codec is not None else DataclassWrapperCodec(wrapper_type)
        if not isinstance(chosen_codec, WrapperCodec):
            raise TypeError("codec must implement WrapperCodec")
        registration = _WrapperRegistration(
            type_id, wrapper_type, version, chosen_codec, MappingProxyType(steps),
        )
        self._by_id[type_id] = registration
        self._by_class[wrapper_type] = type_id

    def copy(self) -> "WrapperRegistry":
        result = WrapperRegistry()
        result._by_id = dict(self._by_id)
        result._by_class = dict(self._by_class)
        return result

    def freeze(self) -> "WrapperRegistry":
        self._frozen = True
        return self

    def type_id_for(self, wrapper_type: type[EnrichedElement]) -> str:
        try:
            return self._by_class[wrapper_type]
        except KeyError as exc:
            raise ValueError(f"Unregistered wrapper class: {wrapper_type.__name__}") from exc

    def encode(self, element: EnrichedElement) -> dict[str, Any]:
        registration = self._by_id[self.type_id_for(type(element))]
        payload = registration.codec.encode(element)
        if not isinstance(payload, dict):
            raise TypeError("WrapperCodec.encode must return a dictionary")
        # Reject SDK objects and non-finite numbers before returning a durable payload.
        json.dumps(payload, allow_nan=False)
        return {"type": registration.type_id, "schema_version": registration.version, "data": payload}

    def decode(self, entry: dict[str, Any]) -> EnrichedElement:
        type_id = entry["type"]
        try:
            registration = self._by_id[type_id]
        except KeyError as exc:
            raise ValueError(f"Unknown wrapper type ID: {type_id!r}") from exc
        version = _version(entry["schema_version"])
        if version > registration.version:
            raise ValueError(f"Unsupported future wrapper version: {type_id} v{version}")
        if not isinstance(entry["data"], dict):
            raise TypeError("Wrapper data must be a dictionary")
        payload = deepcopy(entry["data"])
        while version < registration.version:
            if version not in registration.migrations:
                raise ValueError(f"Missing migration for {type_id} from v{version}")
            payload = registration.migrations[version](payload)
            if not isinstance(payload, dict):
                raise TypeError("Wrapper migration must return a dictionary")
            version += 1
        _check_durable_data(payload)
        result = registration.codec.decode(payload)
        if type(result) is not registration.wrapper_type:
            raise TypeError("WrapperCodec.decode returned a different wrapper class")
        _check_durable_data(result)
        return result


def create_default_wrapper_registry() -> WrapperRegistry:
    """Return an independent registry sharing immutable built-in codecs."""
    return _default_wrapper_registry().copy()


def _migrate_graph_text(payload: dict[str, Any]) -> dict[str, Any]:
    from .handlers import graph_text_fields

    payload.update(graph_text_fields(payload["graph_data"], payload["document_id"]))
    return payload


@cache
def _default_wrapper_registry() -> WrapperRegistry:
    registry = WrapperRegistry()
    for name, wrapper in (
        ("text", data_models.EnrichedTextItem),
        ("page_description", data_models.EnrichedPageDescriptionItem),
        ("title", data_models.EnrichedTitleItem),
        ("section_header", data_models.EnrichedSectionHeaderItem),
        ("list_item", data_models.EnrichedListItem),
        ("formula", data_models.EnrichedFormulaItem),
        ("code", data_models.EnrichedCodeItem),
        ("field_heading", data_models.EnrichedFieldHeadingItem),
        ("field_value", data_models.EnrichedFieldValueItem),
        ("table", data_models.EnrichedTableItem),
        ("picture", data_models.EnrichedPictureItem),
        ("key_value", data_models.EnrichedKeyValueItem),
        ("form", data_models.EnrichedFormItem),
        ("field_region", data_models.EnrichedFieldRegionItem),
        ("field_item", data_models.EnrichedFieldItem),
        ("group", data_models.EnrichedGroupItem),
        ("list_group", data_models.EnrichedListGroupItem),
        ("inline_group", data_models.EnrichedInlineGroupItem),
    ):
        if name in ("form", "key_value"):
            registry.register(f"pdfparse.{name}", wrapper, version=2, migrations={1: _migrate_graph_text})
        else:
            registry.register(f"pdfparse.{name}", wrapper)
    return registry.freeze()


class EnrichedDocumentCodec:
    """Versioned JSON boundary; owns a frozen snapshot of allowed wrapper codecs."""

    def __init__(self, wrappers: WrapperRegistry | None = None) -> None:
        source = wrappers if wrappers is not None else _default_wrapper_registry()
        self.wrappers = source.copy().freeze()

    def to_dict(self, document: EnrichedDocument, *, validate: bool = True) -> dict[str, Any]:
        if validate:
            document.validate()
        return {
            "format": DOCUMENT_FORMAT,
            "schema_version": DOCUMENT_SCHEMA_VERSION,
            "document_id": document.document_id,
            "pdfpath": str(document.pdfpath),
            "assets_dir": str(document.assets_dir) if document.assets_dir else None,
            "elements": [self.wrappers.encode(element) for element in document.elements.values()],
        }

    def from_dict(self, payload: dict[str, Any]) -> EnrichedDocument:
        version = _version(payload.get("schema_version"))
        if payload.get("format") != DOCUMENT_FORMAT or version != DOCUMENT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported document format/version: {payload.get('format')!r} v{version}")
        entries = payload["elements"]
        document = EnrichedDocument(
            document_id=payload["document_id"], pdfpath=Path(payload["pdfpath"]),
            assets_dir=Path(payload["assets_dir"]) if payload.get("assets_dir") else None,
        )
        for entry in entries:
            document.add(self.wrappers.decode(entry))
        document.validate()
        return document

    def save(self, document: EnrichedDocument, filename: Path | str) -> None:
        content = json.dumps(self.to_dict(document), ensure_ascii=False, indent=2, allow_nan=False)
        destination = Path(filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self, filename: Path | str) -> EnrichedDocument:
        return self.from_dict(json.loads(Path(filename).read_text(encoding="utf-8")))
