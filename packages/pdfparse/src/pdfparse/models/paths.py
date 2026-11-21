"""Resolve the model tree provisioned by the repository downloaders."""

import os
from pathlib import Path


def models_root() -> Path:
    return Path(os.getenv('MODELS_DIR') or './models').expanduser().resolve()


def model_directory(category: str, name: str, *, root: Path | None = None) -> Path:
    root = models_root() if root is None else root
    directory = (root / category / name).resolve()
    if not directory.is_relative_to(root):
        raise ValueError(f'Model directory escapes MODELS_DIR: {directory}')
    return directory


def require_model_files(directory: Path, filenames: tuple[str, ...]) -> None:
    for filename in filenames:
        path = directory / filename
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f'Missing or empty model file: {path}. Run the appropriate models/download_*.py script.')
        if not path.resolve().is_relative_to(directory):
            raise ValueError(f'Model file escapes its weights directory: {path}')
