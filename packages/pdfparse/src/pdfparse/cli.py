#!/usr/bin/env python3
"""Populate PdfParse caches (including chunks) for a recursive PDF corpus."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from contextvars import copy_context
import argparse
import os
from pathlib import Path
import sys
from time import monotonic
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm


def _root_env_file():
    """Locate the workspace root, including editable installs run from elsewhere."""
    projects = []
    visited = set()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for directory in (start, *start.parents):
            if directory in visited:
                continue
            visited.add(directory)
            project = directory / 'pyproject.toml'
            if (directory / '.git').exists():
                return directory / '.env'
            if project.is_file():
                if '[tool.uv.workspace]' in project.read_text(encoding='utf-8'):
                    return directory / '.env'
                projects.append(directory)
    if projects:
        return projects[0] / '.env'
    return None


def default_data_dir():
    """CLI flag > exported DATA_DIR > root .env, with root-relative dotenv paths."""
    if 'DATA_DIR' in os.environ:
        return os.environ['DATA_DIR']
    env_file = _root_env_file()
    if env_file is None or not env_file.is_file():
        return None
    from dotenv import dotenv_values
    value = dotenv_values(env_file).get('DATA_DIR')
    if not value:
        return value
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else env_file.parent / path)


def find_pdfs(root: Path):
    versions = root.resolve() / '_versions'
    # Do not traverse generated caches, archived copies or directory symlinks.
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(name for name in folders if not name.endswith('.pdfparsecache')
                            and (Path(directory) != root or name != '_versions'))
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() == '.pdf' and path.is_file() and not path.resolve().is_relative_to(versions):
                yield path


def main(argv=None):
    from pdfparse.logging import setup_logging
    setup_logging("pdfparse")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',
                        help='Dossier source (DATA_DIR exporté ou .env racine par défaut).')
    parser.add_argument('--files-manifest', type=Path, help='Sélection JSON des PDF à traiter sous data-dir.')
    parser.add_argument('--configuration', type=Path, help='Profil RuntimeConfiguration au format JSON, devices inclus.')
    parser.add_argument('--cpu-workers', type=int, help='Calculs CPU simultanés (automatique selon les CPU disponibles).')
    parser.add_argument('--cpu-threads', type=int, help='Threads de calcul par worker CPU (automatique).')
    parser.add_argument('--inflight-mib', type=int, default=512, help='Budget estimé des images en vol, en Mio (défaut : 512).')
    parser.add_argument('--batch-tokens', type=int, default=16384, help='Budget estimé de tokens par lot (défaut : 16384).')
    parser.add_argument('--workers', type=int, default=4, help='PDF simultanés (défaut : 4).')
    parser.add_argument('--batch-size', type=int, default=4, help='Images maximum par lot de modèle (défaut : 4).')
    parser.add_argument('--batch-pixels', type=int, default=1048576,
                        help='Budget de pixels estimés par lot (défaut : 1048576).')
    parser.add_argument('--batch-wait-ms', type=float, default=10,
                        help="Attente maximale pour remplir un lot, en ms (défaut : 10).")
    parser.add_argument('--prefetch', type=int, default=2, help='Lots en préparation/exécution par GPU (défaut : 2).')
    parser.add_argument('--queue-size', type=int, default=64, help='Images en vol, tous modèles confondus (défaut : 64).')
    args = parser.parse_args(argv)
    if min(args.workers, args.batch_size, args.batch_pixels, args.queue_size, args.inflight_mib,
           args.batch_tokens, args.prefetch, args.cpu_workers or 1, args.cpu_threads or 1) < 1 or args.cpu_workers == 0 or args.cpu_threads == 0:
        parser.error('workers, batch-size, batch-pixels et queue-size doivent être positifs.')
    import math
    import signal
    if not math.isfinite(args.batch_wait_ms) or args.batch_wait_ms < 0:
        parser.error('batch-wait-ms doit être un nombre fini positif ou nul.')
    if args.data_dir is None:
        args.data_dir = default_data_dir()
    if not args.data_dir:
        parser.error('Définir DATA_DIR ou fournir --data-dir.')
    root = Path(args.data_dir).expanduser().resolve()
    if not root.is_dir():
        parser.error(f'Dossier inexistant : {root}')

    # Capture SIGTERM pour déclencher l'arrêt gracieux comme SIGINT (KeyboardInterrupt)
    def _sigterm_handler(signum, frame):
        raise KeyboardInterrupt()

    original_sigterm = None
    try:
        original_sigterm = signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, AttributeError):
        pass

    try:
        return _run_cli(args, root)
    except ValueError as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        print("\nInterruption reçue (SIGINT/SIGTERM), arrêt gracieux de pdfparse-run.", file=sys.stderr, flush=True)
        return 130
    finally:
        if original_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, original_sigterm)
            except (ValueError, AttributeError):
                pass


def selected_pdfs(root, manifest=None):
    """Map logical corpus paths to optional immutable snapshots."""
    if manifest is None:
        return dict.fromkeys(find_pdfs(root))
    import json
    import re

    values = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(values, (list, dict)):
        raise ValueError('Invalid PDF selection')
    if isinstance(values, list):
        if len(values) > 100000 or any(not isinstance(value, str) for value in values):
            raise ValueError('Invalid PDF selection')
        requested = {Path(value).absolute() for value in values}
        if not requested.issubset(set(find_pdfs(root))):
            raise ValueError('Selected PDF outside allowed corpus')
        return dict.fromkeys(requested)

    legacy_versions = Path(os.environ.get('SOURCE_VERSIONS_DIR') or root / '_versions').absolute()
    snapshots = {}
    for logical_path, digest in values.items():
        path = Path(logical_path)
        if (not path.is_relative_to(root) or path != path.resolve()
                or path.is_relative_to(root / '_versions')
                or any(part.endswith('.pdfparsecache') for part in path.relative_to(root).parts[:-1])
                or path.suffix.lower() != '.pdf'
                or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Invalid archived PDF selection')
        snapshot = path.with_name(path.name + '.pdfparsecache') / 'versions' / (digest + '.pdf')
        if snapshot.resolve() != snapshot:
            raise ValueError('Symbolic PDF snapshot refused')
        if not snapshot.exists():
            snapshot = legacy_versions / (digest + '.pdf')
        if snapshot.resolve() != snapshot or not snapshot.is_file():
            raise ValueError('Immutable PDF snapshot is missing or invalid')
        snapshots[path] = snapshot
    return snapshots


def e5_tokenizer_directory() -> Path | None:
    from pdfparse.models.paths import model_directory

    directory = model_directory('embeddings', 'multilingual_e5_large')
    return directory if directory.is_dir() else None


def _run_cli(args, root: Path) -> int:
    sources = selected_pdfs(root, args.files_manifest)
    # Queue the smallest files first across the entire recursive corpus.
    pdfs = sorted(sources, key=lambda path: ((sources[path] or path).stat().st_size, path))
    print(f'{len(pdfs)} PDF trouvés dans {root}', flush=True)
    if not pdfs:
        return 0

    from pdfparse import ModelRegistry
    from pdfparse.backend.defaults import default_runtime_configuration
    from pdfparse.backend.runtime import build_chunking, build_pipeline, effective_runtime_configuration, validate_selection
    from pdfparse.progress import observe_progress
    from pdfparse.ingestion.cache import cached_run_chunks, remember_runtime_configuration

    def cached(path, runtime):
        return cached_run_chunks(path, runtime, **source_options(path))

    def source_options(path):
        return {'source_path': sources[path]} if sources[path] is not None else {}

    models = ModelRegistry()
    selection = default_runtime_configuration()
    if args.configuration:
        from pdfparse.backend.schemas import RuntimeConfiguration
        try:
            selection = RuntimeConfiguration.model_validate_json(args.configuration.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise ValueError(f'Configuration invalide : {error}') from error
    chunker = build_chunking(selection.chunking)
    pipeline_options = {'chunking': chunker}
    runtime_configuration = effective_runtime_configuration(selection, chunker)
    from pdfparse.models.scheduling import QueuedModelRegistry, available_cpu_count, cpu_thread_budget, await_future
    from pdfparse.execution import blocking
    cpu_workers = args.cpu_workers or min(8, available_cpu_count())
    cpu_threads = args.cpu_threads or max(1, available_cpu_count() // cpu_workers)
    missing = []
    failures = 0
    with logging_redirect_tqdm(), tqdm(total=len(pdfs), unit='PDF', dynamic_ncols=True,
                                      desc='PdfParse') as progress:
        last_stage_refresh = 0.
        def on_stage(stage, detail):
            nonlocal last_stage_refresh
            # Stage callbacks may originate on several model/Docling threads.
            # Avoid a terminal redraw and global tqdm lock for every crop/token.
            now = monotonic()
            if now - last_stage_refresh < .2:
                return
            with progress.get_lock():
                if now - last_stage_refresh < .2:
                    return
                last_stage_refresh = now
                progress.set_postfix_str(f'{stage.value} · {detail or ""}')

        def failed(path, error):
            nonlocal failures
            failures += 1
            def message(error):
                if isinstance(error, BaseExceptionGroup):
                    return '; '.join(message(item) for item in error.exceptions)
                return str(error)
            tqdm.write(f'ÉCHEC : {path} : {message(error)}', file=sys.stderr)

        remaining_pdfs = iter(pdfs)
        for path in remaining_pdfs:
            progress.set_description(f'Cache · {path.relative_to(root)}')
            try:
                cached_chunks = cached(path, runtime_configuration)
                if cached_chunks is None:
                    missing.append(path)
                    break
                tqdm.write(f'Cache réutilisé : {path.relative_to(root)} · {cached_chunks} chunks')
            except Exception as exc:
                failed(path, exc)
            progress.set_postfix(erreurs=failures)
            progress.update(1)

        if missing:
            with cpu_thread_budget(cpu_threads):
                validate_selection(models, selection)
                tqdm.write(f'Traitement du corpus · {args.workers} PDF simultanés · '
                           f'{cpu_workers} workers CPU × {cpu_threads} threads · lots ≤ {args.batch_size}')
                with QueuedModelRegistry(models, cpu_workers=cpu_workers, cpu_threads=cpu_threads,
                        batch_size=args.batch_size, max_pending=args.queue_size, max_tokens=args.batch_tokens,
                        max_memory_bytes=args.inflight_mib * 1024 * 1024, prefetch=args.prefetch,
                        wait_seconds=args.batch_wait_ms / 1000, max_pixels=args.batch_pixels) as queued:
                    scheduler = queued.scheduler
                    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix='pdfparse-docling') as bridge:
                        async def run_corpus():
                            # Continue validating caches while the first missing
                            # PDFs already execute. Only paths occupy this queue.
                            pending = asyncio.Queue(maxsize=args.workers)
                            worker_count = min(args.workers, len(pdfs))
                            async def produce():
                                await pending.put(missing[0])
                                for path in remaining_pdfs:
                                    try:
                                        cached_chunks = await blocking(cached, path, runtime_configuration)
                                        if cached_chunks is None:
                                            await pending.put(path)
                                            continue
                                        tqdm.write(f'Cache réutilisé : {path.relative_to(root)} · {cached_chunks} chunks')
                                    except Exception as exc:
                                        failed(path, exc)
                                    progress.set_postfix(erreurs=failures)
                                    progress.update(1)
                                for _ in range(worker_count):
                                    await pending.put(None)

                            async def consume():
                                pipeline = None
                                while True:
                                    path = await pending.get()
                                    if path is None:
                                        return
                                    try:
                                        if pipeline is None:
                                            pipeline = await scheduler.run_cpu(build_pipeline, queued, selection, **pipeline_options)
                                            pipeline._runtime_configuration = runtime_configuration
                                        with observe_progress(on_stage):
                                            if hasattr(pipeline, 'run_async'):
                                                result = await pipeline.run_async(path, scheduler=scheduler,
                                                    bridge_executor=bridge, item_concurrency=args.batch_size * 2,
                                                    **source_options(path))
                                            else:
                                                result = await await_future(bridge.submit(
                                                    copy_context().run, pipeline.run, path, **source_options(path)))
                                        await blocking(lambda: remember_runtime_configuration(
                                            path, pipeline, runtime_configuration, **source_options(path)))
                                        tqdm.write(f'Cache prêt : {path.relative_to(root)} · {len(result.chunks)} chunks')
                                    except Exception as exc:
                                        failed(path, exc)
                                    finally:
                                        progress.set_description(f'PdfParse · {path.relative_to(root)}')
                                        progress.set_postfix(erreurs=failures)
                                        progress.update(1)
                            async with asyncio.TaskGroup() as group:
                                group.create_task(produce())
                                for _ in range(worker_count):
                                    group.create_task(consume())
                        asyncio.run(run_corpus())
                    metrics = scheduler.metrics
                    tqdm.write(f"Inférences : {metrics['images']} images · {metrics['batches']} lots · "
                               f"pic d’entrées estimé : {metrics['peak_input_bytes'] / 1024 ** 2:.1f} Mio")
    print(f'Terminé : {len(pdfs) - failures} réussis, {failures} échecs.', flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
