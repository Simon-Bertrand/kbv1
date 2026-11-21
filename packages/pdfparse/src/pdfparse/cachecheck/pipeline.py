"""Bounded batch extraction, SQLite aggregation and incremental CSV emission."""
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
import csv
from dataclasses import dataclass
from itertools import islice
import json
import math
from pathlib import Path
import tempfile
from .conventions import COLUMNS
from .corpus import CorpusStatistics, SimilarityIndex, connect
from .metrics import ChunkMetrics

@dataclass(frozen=True, slots=True)
class DocumentMarker:
    """Register a PDF even if its valid cache has zero chunks."""
    pdf_key: str


_WORKER = None


def _extract(item):
    global _WORKER
    if _WORKER is None:
        _WORKER = ChunkMetrics()
    pdf_key, chunk_id, text = item
    return _WORKER.extract(text, pdf_key, chunk_id)


class MetricsPipeline:
    """Items are (pdf_key: str, chunk_id: str, text: str); order is preserved.

    The iterator is consumed once. Global statistics require its completion
    before the first CSV data batch can be emitted. Temporary state is disk-backed.
    """

    def __init__(self, *, batch_size=256, workers=1, work_dir=None,
                 permutations=64, bands=16, seed=0, max_candidates=128):
        if batch_size < 1 or workers < 1:
            raise ValueError('batch_size and workers must be positive')
        self.batch_size, self.workers, self.work_dir = batch_size, workers, work_dir
        self.similarity_options = dict(permutations=permutations, bands=bands, seed=seed,
                                       max_candidates=max_candidates)

    def write_csv(self, chunks, output, *, pdf_keys=()):
        with tempfile.TemporaryDirectory(prefix='pdfparse-cachecheck-', dir=self.work_dir) as temporary:
            db = connect(Path(temporary) / 'corpus.sqlite')
            db.execute('PRAGMA synchronous=OFF')  # Disposable spool; no crash recovery is promised.
            try:
                return self._write(chunks, output, pdf_keys, db)
            finally:
                db.close()

    def _write(self, chunks, output, pdf_keys, db):
        corpus = CorpusStatistics(db)
        similarity = SimilarityIndex(db, **self.similarity_options)
        db.execute('CREATE TABLE spool(seq INTEGER PRIMARY KEY, row TEXT, words TEXT)')
        for pdf_key in pdf_keys:
            corpus.add_pdf(pdf_key)
        source = iter(chunks)
        total = 0
        serial_extractor = ChunkMetrics()
        # map() sees only one bounded batch; no unbounded executor prefetch.
        with (ProcessPoolExecutor(max_workers=self.workers) if self.workers > 1 else nullcontext()) as pool:
            while batch := list(islice(source, self.batch_size)):
                records = []
                for item in batch:
                    if isinstance(item, DocumentMarker):
                        corpus.add_pdf(item.pdf_key)
                    else:
                        records.append(item)
                batch = records
                for pdf_key, chunk_id, text in batch:
                    if not isinstance(pdf_key, str) or not isinstance(chunk_id, str) or not isinstance(text, str):
                        raise TypeError('pdf_key, chunk_id and text must be str')
                extracted = pool.map(_extract, batch, chunksize=max(1, len(batch) // (self.workers * 4))) if pool else (serial_extractor.extract(text, pdf, chunk) for pdf, chunk, text in batch)
                for features in extracted:
                    row, words = features.row, features.words
                    corpus.add(row['pdf_key'], words)
                    # Duplicate composite identities fail rather than silently overwrite rows.
                    similarity.add(row['pdf_key'], row['chunk_id'], features.shingles)
                    # NaN stored as JSON null; exported as literal NaN.
                    safe = [None if isinstance(row[key], float) and math.isnan(row[key]) else row[key]
                            for key in COLUMNS]
                    db.execute('INSERT INTO spool(row, words) VALUES (?, ?)',
                               (json.dumps(safe, ensure_ascii=False, allow_nan=False), json.dumps(list(words), ensure_ascii=False)))
                    total += 1
                db.commit()
        corpus.finalize()
        similarity.finalize()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(COLUMNS)
        output.flush()
        cursor = db.execute('SELECT row, words FROM spool ORDER BY seq')
        while batch := cursor.fetchmany(self.batch_size):
            rows = []
            for encoded_row, encoded_words in batch:
                row = dict(zip(COLUMNS, json.loads(encoded_row)))
                row['average_idf'] = corpus.average_idf(json.loads(encoded_words))
                row['max_jaccard_shingle_similarity'], row['nearest_chunk_id'] = similarity.nearest(row['pdf_key'], row['chunk_id'])
                rows.append(['NaN' if row[col] is None or isinstance(row[col], float) and math.isnan(row[col])
                             else row[col] for col in COLUMNS])
            writer.writerows(rows)
            output.flush()
        return total
