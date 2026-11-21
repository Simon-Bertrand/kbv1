"""Reproducible local/oracle and end-to-end measurements; no unmeasured speed claims."""
import argparse
import cProfile
import gc
import hashlib
import io
import json
import math
import platform
import pstats
import random
import time
import tracemalloc
import unicodedata
from pathlib import Path
import zstandard

from .conventions import COLUMNS, STOPWORDS, STOPWORDS_VERSION, VERSION
from .metrics import ChunkMetrics
from .pipeline import MetricsPipeline
from .reference import extract_reference

SIZES = (50, 250, 1000, 5000, 20000)


def fixture(size, seed=42):
    rng = random.Random(seed)
    words = [''.join(rng.choices('abcdeéèfghijklmnopqrstuvwx', k=rng.randrange(3, 12))) for _ in range(256)]
    choices = words + ['the', 'de', 'et', 'information', 'PDF', '🙂', '123', 'répétition'] * 8
    result = []
    length = 0
    while length < size:
        token = rng.choice(choices) + rng.choice([' ', ' ', '\n', ', '])
        result.append(token)
        length += len(token)
    return ''.join(result)[:size]


def _measure(function, text, iterations, profile_path):
    function(text, 'pdf', 'chunk')  # Warm-up; excluded from timed samples.
    gc.collect()
    started_cpu, started_wall = time.process_time_ns(), time.perf_counter_ns()
    for _ in range(iterations):
        function(text, 'pdf', 'chunk')
    wall, cpu = time.perf_counter_ns() - started_wall, time.process_time_ns() - started_cpu
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    retained = function(text, 'pdf', 'chunk')
    current, peak = tracemalloc.get_traced_memory()
    changes = tracemalloc.take_snapshot().compare_to(before, 'lineno')
    retained_blocks = sum(max(0, stat.count_diff) for stat in changes)
    tracemalloc.stop()
    profiler = cProfile.Profile()
    profiler.runcall(function, text, 'pdf', 'chunk')
    profiler.dump_stats(str(profile_path))
    stats = pstats.Stats(profiler)
    names = {
        'lrs': ('longest_repeat', 'longest_repeat_reference'),
        'tokenization': ('tokens',),
        'zipf_fit': ('zipf_error', 'linear_regression'),
        'entropy': ('entropy', 'shannon'),
        'zstd': ('compress',),
        'reference_byte_std': ('pstdev',),
        'reference_ngrams': ('repeated',),
    }
    families = {}
    for family, functions in names.items():
        selected = [v for key, v in stats.stats.items() if any(
            key[2] == name or name == 'compress' and "'compress'" in key[2] for name in functions)]
        families[family] = {'cumulative_ns': round(sum(v[3] for v in selected) * 1e9),
                            'calls': sum(v[1] for v in selected)}
    shared = [v for key, v in stats.stats.items() if key[2] in ('extract', 'extract_reference')]
    families['shared_extraction'] = {'exclusive_ns': round(sum(v[2] for v in shared) * 1e9)}
    return dict(mean_wall_ns=wall / iterations, chunks_per_second=iterations * 1e9 / wall,
                mean_cpu_ns=cpu / iterations, python_peak_bytes=peak, python_retained_bytes=current,
                python_transient_peak_bytes=max(0, peak - current), retained_positive_blocks=retained_blocks,
                families_profiled=families)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=10)
    parser.add_argument('--corpus-chunks', type=int, default=12)
    args = parser.parse_args(argv)
    if args.iterations < 1 or args.corpus_chunks < 1:
        parser.error('iterations and corpus-chunks must be positive')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    extractor = ChunkMetrics()
    result = {'environment': dict(python=platform.python_version(), platform=platform.platform(),
        processor=platform.processor(), unicode=unicodedata.unidata_version,
        zstandard=zstandard.__version__, libzstd=zstandard.ZSTD_VERSION,
        convention=VERSION, stopwords=STOPWORDS_VERSION,
        stopwords_sha256=hashlib.sha256('\n'.join(sorted(STOPWORDS)).encode()).hexdigest(),
        iterations=args.iterations, corpus_chunks=args.corpus_chunks, seed=42), 'sizes': []}
    for size in SIZES:
        text = fixture(size)
        actual = extractor.extract(text, 'pdf', 'chunk').row
        expected = extract_reference(text, 'pdf', 'chunk').row
        for col in COLUMNS:
            a, b = actual[col], expected[col]
            if isinstance(a, float) and isinstance(b, float):
                assert math.isnan(a) and math.isnan(b) or math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10), col
            else:
                assert a == b, col
        reference = _measure(extract_reference, text, args.iterations, args.output_dir / f'reference-{size}.prof')
        optimized = _measure(extractor.extract, text, args.iterations, args.output_dir / f'optimized-{size}.prof')
        # Full pipeline includes disk spooling, MinHash/LSH, exact verification and CSV.
        # Timed separately from tracemalloc to avoid instrumentation distorting timing.
        class Discard(io.TextIOBase):
            def write(self, value):
                return len(value)
            def flush(self):
                pass
        records = [(f'pdf{i // 3}', f'chunk{i:06}', fixture(size, 42 + i)) for i in range(args.corpus_chunks)]
        pipeline = MetricsPipeline(batch_size=4, permutations=64, bands=16, max_candidates=128)
        started_cpu, started_wall = time.process_time_ns(), time.perf_counter_ns()
        pipeline.write_csv(iter(records), Discard())
        end_to_end_ns, end_to_end_cpu_ns = time.perf_counter_ns() - started_wall, time.process_time_ns() - started_cpu
        tracemalloc.start()
        pipeline.write_csv(iter(records), Discard())
        _, end_to_end_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        profiler = cProfile.Profile()
        profiler.runcall(pipeline.write_csv, iter(records), Discard())
        profiler.dump_stats(str(args.output_dir / f'corpus-{size}.prof'))
        profile_stats = pstats.Stats(profiler)
        global_families = {}
        for family, name in (('minhash', 'signature'), ('idf_queries', 'average_idf'),
                             ('index_finalization', 'finalize'), ('lsh_candidates', 'candidate_ids'),
                             ('jaccard_including_candidate_lookup', 'nearest')):
            global_families[family] = round(sum(v[3] for key, v in profile_stats.stats.items()
                                               if key[2] == name) * 1e9)
        result['sizes'].append(dict(chars=size, fixture_sha256=hashlib.sha256(text.encode()).hexdigest(),
            reference=reference, optimized=optimized, speedup=reference['mean_wall_ns'] / optimized['mean_wall_ns'],
            corpus=dict(wall_ns=end_to_end_ns, cpu_ns=end_to_end_cpu_ns,
                        chunks_per_second=args.corpus_chunks * 1e9 / end_to_end_ns,
                        python_peak_bytes=end_to_end_peak, families_profiled_ns=global_families)))
        print(f'{size}: reference={reference["mean_wall_ns"] / 1e6:.3f}ms; '
              f'optimized={optimized["mean_wall_ns"] / 1e6:.3f}ms; '
              f'ratio={result["sizes"][-1]["speedup"]:.2f}x', flush=True)
    (args.output_dir / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    lines = ['# Mesures cachecheck', '', 'Mesures locales sans traçage ; mémoire et profils mesurés lors de passages séparés.', '',
        '| Caractères | Référence ms/chunk | Optimisée ms/chunk | Réf./opt. | Opt. chunks/s | CPU opt. ms | Pic Python réf./opt. KiB | Corpus chunks/s |',
        '|---:|---:|---:|---:|---:|---:|---:|---:|']
    for entry in result['sizes']:
        r, o, c = entry['reference'], entry['optimized'], entry['corpus']
        lines.append(f'| {entry["chars"]} | {r["mean_wall_ns"] / 1e6:.3f} | {o["mean_wall_ns"] / 1e6:.3f} | '
            f'{entry["speedup"]:.2f}× | {o["chunks_per_second"]:.1f} | {o["mean_cpu_ns"] / 1e6:.3f} | '
            f'{r["python_peak_bytes"] / 1024:.1f} / {o["python_peak_bytes"] / 1024:.1f} | {c["chunks_per_second"]:.1f} |')
    lines += ['', 'Détail des familles optimisées (ms cumulées sous cProfile, passage séparé ; pas des temps sans instrumentation) :', '',
        '| Caractères | LRS | Tokenisation | Ajustement Zipf | Entropies | Zstd | Parcours mutualisés, temps propre | Blocs Python retenus réf./opt. |',
        '|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for entry in result['sizes']:
        families = entry['optimized']['families_profiled']
        values = ' | '.join(f'{families[family]["cumulative_ns"] / 1e6:.3f}' for family in ('lrs', 'tokenization', 'zipf_fit', 'entropy', 'zstd'))
        lines.append(f'| {entry["chars"]} | {values} | {families["shared_extraction"]["exclusive_ns"] / 1e6:.3f} | '
            f'{entry["reference"]["retained_positive_blocks"]} / {entry["optimized"]["retained_positive_blocks"]} |')
    lines += ['', 'Familles globales sous cProfile (ms pour le corpus complet, passage séparé) :', '',
        '| Caractères | MinHash | Requêtes IDF | Finalisation index | Candidats LSH | Jaccard avec recherche candidats |',
        '|---:|---:|---:|---:|---:|---:|---:|']
    for entry in result['sizes']:
        values = ' | '.join(f'{value / 1e6:.3f}' for value in entry['corpus']['families_profiled_ns'].values())
        lines.append(f'| {entry["chars"]} | {values} |')
    lines += ['', 'La famille Jaccard inclut la recherche LSH et le chargement des shingles ; ces temps cumulés ne sont pas additifs.',
        'Le rapport réf./opt. inférieur à 1 signifie un ralentissement, supérieur à 1 une accélération mesurée.',
        'Corpus : 1 worker, 64 permutations, 16 bandes, 128 candidats maximum, lots de 4 ; '
        f'{args.corpus_chunks} chunks synthétiques par taille. Ces petites mesures ne valident pas un débit sur des millions de chunks.',
        'tracemalloc mesure les allocations Python vivantes et le pic transitoire, pas le total des allocations '
        'effectuées ni les buffers natifs Zstd/SQLite, ni le RSS. Les profils .prof et results.json détaillent les familles.', '',
        'Environnement :', '', '```json', json.dumps(result['environment'], indent=2), '```']
    (args.output_dir / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
