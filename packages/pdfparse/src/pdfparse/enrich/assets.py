"""Asset persistence independent of Docling and item enrichment."""

from abc import ABC, abstractmethod
import os
from pathlib import Path, PurePosixPath
import tempfile
from threading import RLock


def _validate_key(key: str) -> None:
    path = PurePosixPath(key)
    if not key or not path.parts or path.is_absolute() or ".." in path.parts or "\\" in key or str(path) != key:
        raise ValueError(f"Invalid asset key: {key!r}")


class AssetStore(ABC):
    """Store bytes and return an opaque locator usable with ``get``.

    Locators, not store instances, are persisted on wrappers. Implementations
    define how callers configure access to those locators after reloading JSON.
    ``put`` must not silently replace different content at the same key.
    """

    @property
    def local_directory(self) -> Path | None:
        return None

    @abstractmethod
    def put(self, key: str, data: bytes, *, media_type: str) -> str:
        """Persist an asset, returning its locator; repeated identical writes are allowed."""

    @abstractmethod
    def get(self, locator: str) -> bytes:
        """Read an asset or raise if unavailable."""


class FileSystemAssetStore(AssetStore):
    """Atomic, content-preserving writes beneath a local directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()

    @property
    def local_directory(self) -> Path:
        return self.root

    def _path(self, key: str) -> Path:
        _validate_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError(f"Asset key escapes the store: {key!r}")
        return path

    def put(self, key: str, data: bytes, *, media_type: str) -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".asset_", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Atomically publish without overwriting a concurrent writer.
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != data:
                    raise ValueError(f"Different content already exists for asset {key!r}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return key

    def get(self, locator: str) -> bytes:
        return self._path(locator).read_bytes()


class MemoryAssetStore(AssetStore):
    """In-memory store for experiments; keep the instance to read its locators."""

    def __init__(self) -> None:
        self._assets: dict[str, bytes] = {}
        self._lock = RLock()

    def put(self, key: str, data: bytes, *, media_type: str) -> str:
        _validate_key(key)
        with self._lock:
            if key in self._assets and self._assets[key] != data:
                raise ValueError(f"Different content already exists for asset {key!r}")
            self._assets[key] = bytes(data)
        return key

    def get(self, locator: str) -> bytes:
        with self._lock:
            return self._assets[locator]
