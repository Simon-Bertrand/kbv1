"""Analytical fixtures, independent oracles, Unicode, streaming and determinism."""
import csv
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import random
import sqlite3
import subprocess
import sys

import pytest
import zstandard

from pdfparse.cachecheck import COLUMNS, ChunkMetrics, CorpusStatistics, MetricsPipeline, SimilarityIndex
from pdfparse.cachecheck.conventions import STOPWORDS, normalized, tokens
from pdfparse.cachecheck.corpus import connect
from pdfparse.cachecheck.pipeline import DocumentMarker
from pdfparse.cachecheck.reference import extract_reference, corpus_reference
from pdfparse.cachecheck.repetitions import longest_repeat, longest_repeat_reference


def assert_rows(actual, expected):
    assert actual.keys() == expected.keys()
    for key, value in expected.items():
        if isinstance(value, float) and math.isnan(value):
            assert math.isnan(actual[key]), key
        elif isinstance(value, (float, int)):
            assert actual[key] == pytest.approx(value, abs=2e-12), key
        else:
            assert actual[key] == value, key


@pytest.mark.parametrize('text', ['', 'a', 'ab', 'aba', 'abac', 'aaaaaa', 'é', 'e\u0301', 'ÉCOLE école',
    '👨\u200d👩\u200d👦🙂🙂', '中文１２٣ ½', '\x00\n\r\t\ufffd', 'a a a a a a',
    'word_foo l’eau aujourd’hui co-op', '\u0301a\u0301', 'ß İ ﬃ ᾲ', ' ' * 80])
def test_reference_agreement(text):
    assert_rows(ChunkMetrics().extract(text, 'pdf', 'chunk').row, extract_reference(text, 'pdf', 'chunk').row)


def test_random_reference_agreement():
    rng = random.Random(420)
    alphabet = 'abc éÉèßİ 12\t\n_–!🙂\u0301\ufffd'
    extractor = ChunkMetrics()
    for size in (0, 1, 2, 3, 5, 30, 100, 500):
        for _ in range(12):
            text = ''.join(rng.choices(alphabet, k=size))
            assert_rows(extractor.extract(text, 'pdf', 'chunk').row, extract_reference(text, 'pdf', 'chunk').row)


def test_empty_is_defined_only_for_counts():
    row = ChunkMetrics().extract('', 'p', 'c').row
    zero = {'n_bytes', 'n_chars', 'n_words', 'n_distinct_chars', 'n_distinct_words'}
    for name in COLUMNS[2:]:
        if name in zero:
            assert row[name] == 0
        else:
            assert math.isnan(row[name]), name
    assert len(COLUMNS) == 35


def test_analytical_characters_and_aligned_conditionals():
    row = ChunkMetrics().extract('abac', 'p', 'c').row
    assert row['entropy_chars'] == 1.5
    # Bigram contexts are a,b,a, not the full character marginal a,b,a,c.
    assert row['conditional_entropy_1'] == pytest.approx(2 / 3)
    assert row['conditional_entropy_2'] == 0
    assert row['max_repeated_substring_ratio'] == .25
    assert ChunkMetrics().extract('é🙂!2 \n\ufffd', 'p', 'c').row['unicode_category_entropy'] == pytest.approx(math.log2(7) - 2 / 7)


def test_unicode_counts_and_original_bytes():
    text = 'e\u0301🙂!2 \n\ufffd'
    row = ChunkMetrics().extract(text, 'p', 'c').row
    assert row['n_chars'] == 7
    assert row['n_bytes'] == len(text.encode())
    assert row['letter_ratio'] == row['digit_ratio'] == row['punctuation_ratio'] == pytest.approx(1 / 7)
    # U+FFFD is So; the emoji is So too.
    assert row['symbol_ratio'] == pytest.approx(2 / 7)
    assert row['control_ratio'] == row['replacement_char_ratio'] == pytest.approx(1 / 7)
    assert row['special_chars_prop'] == pytest.approx(3 / 7)
    assert list(tokens('ÉCOLE e\u0301cole STRAẞE l’eau_42 x-y')) == ['école', 'école', 'strasse', 'l', 'eau', '42', 'x', 'y']


def test_analytical_lexical_and_repetition_metrics():
    row = ChunkMetrics().extract('a a b c', 'p', 'c').row
    assert row['n_words'] == 4 and row['n_distinct_words'] == 3
    assert row['entropy_words'] == 1.5
    assert row['root_type_token_ratio'] == 1.5
    assert row['yules_k'] == 1250
    assert row['hapax_ratio'] == pytest.approx(2 / 3)
    assert row['stopword_ratio'] == .5
    row = ChunkMetrics().extract('a a a a a a', 'p', 'c').row
    assert row['repeated_word_trigram_ratio'] == .75
    assert row['repeated_word_5gram_ratio'] == .5
    assert row['repeated_char_trigram_ratio'] == pytest.approx(1 - 2 / 9)
    assert row['max_repeated_substring_ratio'] == pytest.approx(9 / 11)
    constant = ChunkMetrics().extract('aaaaaa', 'p', 'c').row
    assert constant['max_repeated_substring_ratio'] == 5 / 6
    assert constant['repeated_char_trigram_ratio'] == .75


def test_short_undefined_and_singleton_metrics():
    extractor = ChunkMetrics()
    for text in ('a', 'ab'):
        row = extractor.extract(text, 'p', 'c').row
        assert math.isnan(row['conditional_entropy_2'])
        assert math.isnan(row['repeated_char_trigram_ratio'])
        assert math.isnan(row['zipf_deviation'])
        assert row['max_repeated_substring_ratio'] == 0
        assert row['yules_k'] == 0
    assert math.isnan(extractor.extract('a', 'p', 'c').row['conditional_entropy_1'])
    assert extractor.extract('a b c', 'p', 'c').row['zipf_deviation'] == 0
    assert extractor.extract('a b', 'p', 'c').row['zipf_deviation'] == 0
    with pytest.raises(UnicodeEncodeError):
        extractor.extract('\ud800', 'p', 'c')


def test_lrs_exhaustive_overlapping_and_unicode():
    for size in range(9):
        for letters in itertools.product('aé', repeat=size):
            text = ''.join(letters)
            # Exhaustive oracle is restricted to tiny test strings.
            best = max((width for width in range(1, size) if
                len({text[i:i + width] for i in range(size - width + 1)}) < size - width + 1), default=0)
            assert longest_repeat(text) == longest_repeat_reference(text) == best
    assert longest_repeat('banana') == 3
    assert longest_repeat('🙂🙂🙂') == 2


def test_document_frequencies_deduplicate_pdf_and_merge_shards(tmp_path):
    db = connect(tmp_path / 'main.sqlite')
    stats = CorpusStatistics(db)
    stats.add('pdf1', ['shared', 'one'])
    stats.add('pdf1', ['shared', 'one'])
    other = connect(tmp_path / 'shard.sqlite')
    shard = CorpusStatistics(other)
    shard.add('pdf1', ['shared'])
    shard.add('pdf2', ['shared', 'two'])
    shard.add_pdf('empty')
    other.commit()
    other.close()
    stats.merge(tmp_path / 'shard.sqlite')
    stats.finalize()
    assert stats.documents == 3
    assert stats.average_idf(['shared', 'one']) == pytest.approx((math.log(4 / 3) + math.log(2)) / 2)
    assert stats.average_idf(['one', 'one']) == pytest.approx(math.log(2))
    assert math.isnan(stats.average_idf([]))
    with pytest.raises(ValueError):
        stats.average_idf(['unknown'])
    db.close()


def test_similarity_exact_on_candidates_stable_ties_and_empty(tmp_path):
    db = connect(tmp_path / 'similarity.sqlite')
    index = SimilarityIndex(db, permutations=8, bands=8)
    extractor = ChunkMetrics()
    records = [('p', 'z', 'abcdefghij'), ('q', 'a', 'abcdefghij'),
               ('p', 'b', 'abcdefghi'), ('p', 'short', 'abc'), ('p', 'none', '🙂' * 10)]
    features = [extractor.extract(text, pdf, chunk) for pdf, chunk, text in records]
    for f in features:
        index.add(f.row['pdf_key'], f.row['chunk_id'], f.shingles)
    index.finalize()
    assert index.nearest('p', 'z') == (1, 'a')
    assert index.nearest('p', 'b')[1] == 'a'
    assert math.isnan(index.nearest('p', 'short')[0])
    assert math.isnan(index.nearest('p', 'none')[0])
    candidates = {(p, c): index.candidate_ids(p, c) for p, c, _ in records}
    oracle = corpus_reference(records, candidates=candidates)
    for record, expected in zip(records, oracle):
        score, nearest = index.nearest(*record[:2])
        if math.isnan(expected['max_jaccard_shingle_similarity']):
            assert math.isnan(score)
        else:
            assert score == expected['max_jaccard_shingle_similarity']
        if isinstance(expected['nearest_chunk_id'], float):
            assert math.isnan(nearest)
        else:
            assert nearest == expected['nearest_chunk_id']
    db.close()


def test_lsh_candidate_work_is_bounded_even_for_identical_chunks(tmp_path):
    db = connect(tmp_path / 'duplicates.sqlite')
    index = SimilarityIndex(db, permutations=4, bands=4, max_candidates=3)
    for i in reversed(range(40)):
        index.add('p', f'{i:03}', {'abcde'})
    index.finalize()
    assert index.candidate_ids('p', '039') == [('000', 'p'), ('001', 'p'), ('002', 'p')]
    assert index.nearest('p', '039') == (1, '000')
    db.close()


def test_same_chunk_id_in_different_pdfs_is_not_self(tmp_path):
    db = connect(tmp_path / 'identities.sqlite')
    index = SimilarityIndex(db, permutations=4, bands=2)
    index.add('p1', 'same', {'abcde'})
    index.add('p2', 'same', {'abcde'})
    index.finalize()
    assert index.nearest('p1', 'same') == (1, 'same')
    with pytest.raises(sqlite3.IntegrityError):
        index.add('p1', 'same', {'abcde'})
    db.close()


@pytest.mark.parametrize('workers', [1, 2])
def test_pipeline_batches_csv_and_matches_reference(tmp_path, workers):
    records = [('pdf1', 'z', 'hello world hello world'), ('pdf2', 'a', 'hello world hello world'),
               ('pdf1', 'short', 'a'), ('pdf2', 'empty', '')]
    class Output(io.StringIO):
        flushes = 0
        def flush(self):
            self.flushes += 1
    output = Output()
    pipeline = MetricsPipeline(workers=workers, batch_size=2, work_dir=tmp_path, permutations=8, bands=4)
    assert pipeline.write_csv(iter(records), output, pdf_keys=['zero-chunk-pdf']) == 4
    assert output.flushes == 3  # Header then two batches.
    reader = csv.DictReader(io.StringIO(output.getvalue()))
    assert reader.fieldnames == list(COLUMNS)
    expected = corpus_reference(records, pdf_keys=['zero-chunk-pdf'])
    for row, oracle in zip(reader, expected):
        actual = {key: row[key] if key in ('pdf_key', 'chunk_id') or key == 'nearest_chunk_id' and row[key] != 'NaN' else float(row[key]) for key in COLUMNS}
        assert_rows(actual, oracle)
    assert not list(tmp_path.glob('pdfparse-cachecheck-*'))


def test_document_marker_counts_zero_chunk_pdfs(tmp_path):
    output = io.StringIO()
    MetricsPipeline(work_dir=tmp_path).write_csv([DocumentMarker('empty-pdf'), ('pdf', 'c', 'hello')], output)
    row = next(csv.DictReader(io.StringIO(output.getvalue())))
    assert float(row['average_idf']) == pytest.approx(math.log(3 / 2))


def test_reproducible_across_python_hash_seeds():
    code = "from pdfparse.cachecheck.corpus import SimilarityIndex,connect; print(SimilarityIndex(connect(':memory:')).signature({'école','abcde','🙂🙂🙂🙂🙂'}))"
    import os
    outputs = [subprocess.check_output([sys.executable, '-c', code], env={**os.environ, 'PYTHONHASHSEED': seed})
               for seed in ('1', '42')]
    assert outputs[0] == outputs[1]


def test_cli_jsonl_stdout_is_only_csv(tmp_path, capsys):
    from pdfparse.cachecheck.cli import main
    path = tmp_path / 'chunks.jsonl'
    path.write_text(json.dumps({'pdf_key': 'p,1', 'chunk_id': 'c\n1', 'text': 'école école'}) + '\n')
    assert main(['--input-jsonl', str(path), '--batch-size', '1', '--work-dir', str(tmp_path)]) == 0
    captured = capsys.readouterr()
    row = next(csv.DictReader(io.StringIO(captured.out)))
    assert row['pdf_key'] == 'p,1' and row['chunk_id'] == 'c\n1'
    assert 'Terminé' in captured.err and 'Terminé' not in captured.out


def write_fixture_cache(root, *, text='abcde abcde', profile=None):
    pdf = root / 'fixture.pdf'
    pdf.write_bytes(b'fixture pdf source')
    pdfsha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    config = {'test': 'cachecheck'}
    key = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    parent = pdf.with_name(pdf.name + '.pdfparsecache')
    directory = parent / hashlib.sha256(f'{pdfsha}:{key}'.encode()).hexdigest()
    directory.mkdir(parents=True)
    record = {'format': 'pdfparse.index_record', 'schema_version': 4, 'document_id': pdfsha,
              'id': 'fixture-chunk', 'text': text, 'embedding_text': 'Contexte\n\n' + text}
    index = (json.dumps(record) + '\n').encode()
    (directory / 'index.jsonl').write_bytes(index)
    manifest = {'format': 'pdfparse.result_cache', 'schema_version': 2, 'runtime_configuration': profile,
        'configuration': config, 'configuration_hash': key, 'pdfpath': str(pdf), 'pdf_sha256': pdfsha,
        'files': {'index.jsonl': hashlib.sha256(index).hexdigest()}}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    return directory


def test_cache_reader_streams_original_or_contextualized_chunks(tmp_path):
    from pdfparse.cachecheck.cli import cached_chunks
    write_fixture_cache(tmp_path, profile={'test': True})
    rows = list(cached_chunks(tmp_path, {'test': True}))
    assert isinstance(rows[0], DocumentMarker)
    assert rows[1] == ('fixture.pdf', 'fixture-chunk', 'abcde abcde')
    assert list(cached_chunks(tmp_path, {'test': True}, text_field='embedding_text'))[1][2].startswith('Contexte\n\n')
    assert list(cached_chunks(tmp_path, {'other': True})) == []


def test_corrupt_index_is_rejected_before_export(tmp_path):
    from pdfparse.cachecheck.cli import cached_chunks
    directory = write_fixture_cache(tmp_path, profile={})
    (directory / 'index.jsonl').write_text('{}')
    with pytest.raises(ValueError, match='checksum'):
        list(cached_chunks(tmp_path, {}))


def test_cli_reads_shared_compact_default_profile(tmp_path, capsys):
    from pdfparse.cachecheck.cli import main
    from pdfparse.backend.defaults import default_runtime_configuration
    write_fixture_cache(tmp_path, profile=default_runtime_configuration().model_dump(mode='json'))
    assert main(['--data-dir', str(tmp_path), '--strict']) == 0
    captured = capsys.readouterr()
    row = next(csv.DictReader(io.StringIO(captured.out)))
    assert row['pdf_key'] == 'fixture.pdf'
    assert float(row['n_words']) == 2


def test_bytes_std_and_two_rank_zipf_are_defined():
    extractor = ChunkMetrics()
    assert extractor.extract('a', 'p', 'c').row['bytes_std'] == 0
    assert extractor.extract('ab', 'p', 'c').row['bytes_std'] == .5
    assert extractor.extract('a a b', 'p', 'c').row['zipf_deviation'] == pytest.approx(0, abs=1e-30)


def test_failure_cleans_spool_and_does_not_write_partial_csv(tmp_path):
    output = io.StringIO()
    repeated_ids = [('p', 'same', 'hello'), ('p', 'same', 'world')]
    with pytest.raises(sqlite3.IntegrityError):
        MetricsPipeline(work_dir=tmp_path).write_csv(repeated_ids, output)
    assert output.getvalue() == ''
    assert not list(tmp_path.glob('pdfparse-cachecheck-*'))


def test_stale_pdf_cache_is_skipped_or_fails_strictly(tmp_path):
    from pdfparse.cachecheck.cli import cached_chunks
    write_fixture_cache(tmp_path, profile={})
    (tmp_path / 'fixture.pdf').write_bytes(b'new source')
    assert list(cached_chunks(tmp_path, {})) == []
    with pytest.raises(ValueError, match='aucun cache'):
        list(cached_chunks(tmp_path, {}, strict=True))


def test_local_api_does_not_import_model_runtimes():
    code = "from pdfparse.cachecheck import ChunkMetrics; import sys; ChunkMetrics().extract('école', 'p', 'c'); assert all(name not in sys.modules for name in ('torch', 'transformers', 'docling', 'fastapi'))"
    subprocess.run([sys.executable, '-c', code], check=True)
