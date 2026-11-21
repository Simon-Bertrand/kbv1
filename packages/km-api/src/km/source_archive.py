"""Preserve verified PDF bytes in the shared content-addressed store."""

import hashlib
import os
from pathlib import Path
import tempfile

from .inventory import confined, source_relative


def archive_pdf(root: Path, upstream_root: str, versions: Path, path: str, digest: str):
    relative = source_relative(path, upstream_root)
    if relative.suffix.lower() != '.pdf' or any(
        part.startswith('.') or part == '_versions' or '.pdfparsecache' in part
        for part in relative.parts
    ):
        raise ValueError('Chemin PDF hors du corpus publiable.')
    source = confined(root, relative.as_posix())
    if not source.is_file():
        raise FileNotFoundError(source)

    name = digest + '.pdf'
    target = confined(versions, name, exists=False)
    if target.exists():
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Le PDF source ne correspond plus à son cache.')
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Version PDF immuable incohérente.')
        return

    with tempfile.NamedTemporaryFile(dir=versions, prefix='.source-', delete=False) as output:
        temporary = Path(output.name)
        try:
            sha = hashlib.sha256()
            with source.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    sha.update(block)
                    output.write(block)
            if sha.hexdigest() != digest:
                raise ValueError('Le PDF source ne correspond plus à son cache.')
            output.flush()
            os.fsync(output.fileno())
            temporary.chmod(0o444)
            try:
                os.link(temporary, target)
            except FileExistsError:
                with confined(versions, name).open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise ValueError('Version PDF immuable incohérente.')
        finally:
            temporary.unlink(missing_ok=True)
