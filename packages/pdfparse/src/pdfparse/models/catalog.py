"""Category and weight-source data; no SDK clients or runtime instances."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseInferenceModel


class ModelCategory(StrEnum):
    LAYOUT = "layout"
    OCR = "ocr"
    PICTURE_CLASSIFIER = "picture_classifier"
    VLM = "vlm"


@dataclass(frozen=True, slots=True)
class ModelSource:
    """Upstream provenance and required files for a provisioned local model."""
    repo_id: str | None = None
    revision: str | None = None
    files: tuple[str, ...] = ()
    local_path: Path | None = None

    def __post_init__(self) -> None:
        if isinstance(self.files, str):
            raise ValueError("files must be a sequence of filenames")
        object.__setattr__(self, "files", tuple(self.files))
        if self.local_path is None:
            if not self.repo_id or not isinstance(self.revision, str) or not re.fullmatch(r"[0-9a-f]{40}", self.revision):
                raise ValueError("Hub weights require repo_id and a pinned revision")
            if not re.fullmatch(r'[\w.-]+/[\w.-]+', self.repo_id):
                raise ValueError(f"Invalid upstream model identifier: {self.repo_id!r}")
        elif self.repo_id is not None or self.revision is not None:
            raise ValueError("Choose local_path or repo_id/revision, not both")
        else:
            object.__setattr__(self, "local_path", Path(self.local_path).expanduser().resolve())
        if not self.files or len(set(self.files)) != len(self.files):
            raise ValueError("Model files must be nonempty and unique")
        for filename in self.files:
            path = PurePosixPath(filename)
            if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in filename
                    or str(path) != filename or any(char in filename for char in "*?[]")):
                raise ValueError(f"Invalid model filename: {filename!r}")


@dataclass(frozen=True, slots=True)
class ModelSpec:
    category: ModelCategory
    name: str
    implementation: type[BaseInferenceModel]
    source: ModelSource

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", ModelCategory(self.category))
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", self.name):
            raise ValueError("Model name must be a lowercase directory-safe identifier")
        if not isinstance(self.source, ModelSource):
            raise TypeError("source must be ModelSource")
