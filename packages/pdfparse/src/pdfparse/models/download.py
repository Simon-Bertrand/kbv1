"""Check local weights. Provision missing models with the scripts in models/."""
import argparse
import hashlib
from pathlib import Path
import sys

from .catalog import ModelCategory, ModelSource
from .paths import require_model_files


def file_digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def weight_identity(source: ModelSource, directory: Path) -> dict:
    require_model_files(directory, source.files)
    return {'repo_id': source.repo_id, 'revision': source.revision,
            'local_path': str(source.local_path) if source.local_path is not None else None,
            'files': [{'name': name, 'size_bytes': (directory / name).stat().st_size,
                       'sha256': file_digest(directory / name)} for name in source.files]}


def main(argv=None) -> int:
    from .registry import ModelRegistry
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--category', choices=list(ModelCategory), help='Default: all registered categories.')
    parser.add_argument('--model', help='One model; requires --category.')
    parser.add_argument('--offline', action='store_true', help='Compatibility flag. All checks are local.')
    args = parser.parse_args(argv)
    if args.model and args.category is None:
        parser.error('--model requires --category')
    registry = ModelRegistry()
    try:
        if args.model:
            paths = {f'{args.category}/{args.model}': registry.download(
                args.category, args.model, local_files_only=args.offline)}
        else:
            paths = registry.download_all(args.category, local_files_only=args.offline)
    except Exception as exc:
        print(f'Local model check failed: {exc}', file=sys.stderr)
        return 1
    for name, path in paths.items():
        print(f'{name}: {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
