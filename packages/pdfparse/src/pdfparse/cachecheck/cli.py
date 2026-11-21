"""Stream CSV metrics for the cached chunks of a recursive PDF corpus."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unicodedata
import zstandard
from filelock import FileLock

from .conventions import VERSION, STOPWORDS_VERSION
from .pipeline import DocumentMarker, MetricsPipeline


def _sha(stream):
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def cached_chunks(root, profile, *, text_field='text', strict=False):
    """Stream verified index.jsonl rows, holding the existing per-PDF cache lock.

    Verify the source PDF, profile, configuration identity and index file digest.
    This is a text-index audit; it intentionally does not decode images or models.
    """
    from pdfparse.cli import find_pdfs
    for pdf in find_pdfs(root):
        if pdf.is_symlink():
            continue
        parent = pdf.with_name(pdf.name + '.pdfparsecache')
        relative = pdf.relative_to(root).as_posix()
        found = False
        if parent.is_dir():
            with FileLock(parent / '.lock'):
                pdf_sha = None
                for directory in sorted(parent.iterdir()):
                    if directory.is_symlink() or not directory.is_dir() or len(directory.name) != 64:
                        continue
                    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
                    if manifest.get('runtime_configuration') != profile:
                        continue
                    if manifest.get('format') != 'pdfparse.result_cache' or manifest.get('schema_version') != 2:
                        raise ValueError(f'{relative}: unsupported cache manifest')
                    if pdf_sha is None:
                        source_stat = pdf.stat()
                        with pdf.open('rb') as stream:
                            pdf_sha = _sha(stream)
                    if manifest['pdf_sha256'] != pdf_sha:
                        continue  # Historical cache for an older source version.
                    configuration = json.dumps(manifest['configuration'], sort_keys=True,
                        separators=(',', ':'), ensure_ascii=False, allow_nan=False)
                    key = hashlib.sha256(configuration.encode('utf-8')).hexdigest()
                    if (manifest['configuration_hash'] != key or manifest['pdfpath'] != str(pdf)
                            or directory.name != hashlib.sha256(f'{pdf_sha}:{key}'.encode()).hexdigest()):
                        raise ValueError(f'{relative}: invalid cache identity')
                    index = directory / 'index.jsonl'
                    if index.is_symlink():
                        raise ValueError(f'{relative}: index must not be a symlink')
                    with index.open('rb') as stream:
                        if _sha(stream) != manifest['files']['index.jsonl']:
                            raise ValueError(f'{relative}: invalid index.jsonl checksum')
                        stream.seek(0)
                        yield DocumentMarker(relative)
                        for line in stream:
                            record = json.loads(line)
                            if (record.get('format') != 'pdfparse.index_record' or record.get('schema_version') != 4
                                    or record['document_id'] != pdf_sha):
                                raise ValueError(f'{relative}: invalid chunk index record')
                            yield relative, record['id'], record[text_field]
                    after = pdf.stat()
                    if ((source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns, source_stat.st_ctime_ns)
                            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                        raise ValueError(f'{relative}: source PDF changed during cache reading')
                    found = True
                    break  # Exactly one cache per PDF for the selected runtime profile.
        if not found:
            message = f'{relative}: aucun cache valide pour le profil choisi'
            if strict:
                raise ValueError(message)
            print(message, file=sys.stderr)


def jsonl_chunks(stream):
    for number, line in enumerate(stream, 1):
        if line.strip():
            try:
                record = json.loads(line)
                yield record['pdf_key'], record['chunk_id'], record['text']
            except (ValueError, KeyError, TypeError) as error:
                raise ValueError(f'JSONL ligne {number}: {error}') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', help='DATA_DIR exporté ou .env racine par défaut, comme pdfparse-run')
    parser.add_argument('--configuration', type=Path, help='Même profil RuntimeConfiguration JSON que pdfparse-run')
    parser.add_argument('--input-jsonl', metavar='PATH', help='Alternative : lignes {pdf_key, chunk_id, text}; - pour stdin')
    parser.add_argument('--text-field', choices=('text', 'embedding_text'), default='text')
    parser.add_argument('--strict', action='store_true', help='Échouer si un PDF ne possède pas de cache correspondant')
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--work-dir', type=Path, help='Disque temporaire pour les agrégations SQLite')
    parser.add_argument('--permutations', type=int, default=64)
    parser.add_argument('--bands', type=int, default=16)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--max-candidates', type=int, default=128)
    args = parser.parse_args(argv)
    if args.input_jsonl and (args.data_dir or args.configuration):
        parser.error('--input-jsonl ne se combine pas avec --data-dir/--configuration')
    try:
        pipeline = MetricsPipeline(batch_size=args.batch_size, workers=args.workers, work_dir=args.work_dir,
            permutations=args.permutations, bands=args.bands, seed=args.seed, max_candidates=args.max_candidates)
        # Fail invalid LSH parameters before reading inputs.
        if args.permutations < 1 or args.bands < 1 or args.permutations % args.bands or args.max_candidates < 1:
            parser.error('permutations/bands positifs et divisibles, max-candidates positif requis')
        print(f'{VERSION}; stopwords={STOPWORDS_VERSION}; Unicode={unicodedata.unidata_version}; '
              f'zstandard={zstandard.__version__}; libzstd={zstandard.ZSTD_VERSION}; '
              f'MinHash={args.permutations}; bandes={args.bands}; seed={args.seed}; '
              f'candidats_max={args.max_candidates}; texte={args.text_field}; '
              'phase 1: agrégation sur disque', file=sys.stderr)
        if args.input_jsonl:
            if args.input_jsonl == '-':
                count = pipeline.write_csv(jsonl_chunks(sys.stdin), sys.stdout)
            else:
                with Path(args.input_jsonl).open(encoding='utf-8') as stream:
                    count = pipeline.write_csv(jsonl_chunks(stream), sys.stdout)
        else:
            from pdfparse.cli import default_data_dir
            from pdfparse.backend.defaults import default_runtime_configuration
            from pdfparse.backend.schemas import RuntimeConfiguration
            root_value = args.data_dir if args.data_dir is not None else default_data_dir()
            if not root_value:
                parser.error('Définir DATA_DIR ou fournir --data-dir')
            root = Path(root_value).expanduser().resolve(strict=True)
            if not root.is_dir():
                parser.error('data-dir doit être un dossier')
            profile = (RuntimeConfiguration.model_validate_json(args.configuration.read_text(encoding='utf-8'))
                       if args.configuration else default_runtime_configuration()).model_dump(mode='json')
            count = pipeline.write_csv(cached_chunks(root, profile, text_field=args.text_field, strict=args.strict), sys.stdout)
        print(f'Terminé : {count} chunks exportés', file=sys.stderr)
        return 0
    except BrokenPipeError:
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        print(f'pdfparse-cachecheck: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
