"""Disk-backed document frequencies and bounded MinHash/LSH candidate search."""
import hashlib
import json
import math
import sqlite3
import struct
import zstandard as zstd
from .conventions import NAN


def connect(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA cache_size=-32768')  # 32 MiB SQLite page cache.
    db.execute('PRAGMA temp_store=FILE')
    return db


class CorpusStatistics:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS pdfs(pdf_key TEXT PRIMARY KEY) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS pdf_terms(
                pdf_key TEXT, token TEXT, PRIMARY KEY(pdf_key, token)) WITHOUT ROWID;
        ''')
        self.documents = None

    def add_pdf(self, pdf_key):
        self.db.execute('INSERT OR IGNORE INTO pdfs VALUES (?)', (pdf_key,))
        self.documents = None

    def add(self, pdf_key, words):
        self.add_pdf(pdf_key)
        self.db.executemany('INSERT OR IGNORE INTO pdf_terms VALUES (?, ?)', ((pdf_key, t) for t in words))

    def merge(self, shard):
        """Union PDF/term pairs from a finalized or incremental SQLite shard.

        PDF keys must use the same global namespace on every shard. Documents
        split across shards are deduplicated correctly. No chunk data is merged.
        """
        self.db.commit()
        self.db.execute('ATTACH DATABASE ? AS shard', (str(shard),))
        try:
            self.db.execute('INSERT OR IGNORE INTO pdfs SELECT pdf_key FROM shard.pdfs')
            self.db.execute('INSERT OR IGNORE INTO pdf_terms SELECT pdf_key, token FROM shard.pdf_terms')
            self.db.commit()
        finally:
            self.db.execute('DETACH DATABASE shard')
        self.documents = None

    def finalize(self):
        self.db.execute('DROP TABLE IF EXISTS term_df')
        self.db.execute('CREATE TABLE term_df(token TEXT PRIMARY KEY, df INTEGER) WITHOUT ROWID')
        self.db.execute('INSERT INTO term_df SELECT token, count(*) FROM pdf_terms GROUP BY token')
        self.documents = self.db.execute('SELECT count(*) FROM pdfs').fetchone()[0]
        self.db.commit()

    def average_idf(self, words):
        if self.documents is None:
            raise RuntimeError('Finalize document frequencies before querying IDF')
        words = words if isinstance(words, (dict, set, frozenset)) else set(words)
        if not words or not self.documents:
            return NAN
        # SQLite JSON table avoids V SQL round trips and SQLite's parameter limit.
        dfs = self.db.execute('SELECT df FROM term_df WHERE token IN (SELECT value FROM json_each(?))',
                              (json.dumps(list(words), ensure_ascii=False),))
        values = [math.log((self.documents + 1) / (df + 1)) for (df,) in dfs]
        if len(values) != len(words):
            raise ValueError('IDF requested for tokens outside the aggregated corpus')
        return math.fsum(values) / len(values)


class SimilarityIndex:
    PRIME = (1 << 61) - 1

    def __init__(self, db: sqlite3.Connection, *, permutations=64, bands=16, seed=0, max_candidates=128):
        if permutations < 1 or bands < 1 or permutations % bands or max_candidates < 1:
            raise ValueError('Positive permutations divisible by bands and positive max_candidates are required')
        self.db, self.permutations, self.bands = db, permutations, bands
        self.rows, self.max_candidates = permutations // bands, max_candidates
        self.coefficients = []
        for i in range(permutations):
            digest = hashlib.sha256(f'pdfparse.minhash.v1:{seed}:{i}'.encode()).digest()
            self.coefficients.append((int.from_bytes(digest[:8], 'little') % (self.PRIME - 1) + 1,
                                      int.from_bytes(digest[8:16], 'little') % self.PRIME))
        db.executescript('''
            CREATE TABLE IF NOT EXISTS similarity_chunks(
                pdf_key TEXT, chunk_id TEXT, shingles BLOB NOT NULL,
                PRIMARY KEY(pdf_key, chunk_id)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS lsh(
                band INTEGER, bucket BLOB, chunk_id TEXT COLLATE BINARY, pdf_key TEXT COLLATE BINARY,
                PRIMARY KEY(band, bucket, chunk_id, pdf_key)) WITHOUT ROWID;
        ''')
        config = json.dumps([permutations, bands, seed, max_candidates])
        db.execute('CREATE TABLE IF NOT EXISTS similarity_config(config TEXT)')
        saved = db.execute('SELECT config FROM similarity_config').fetchone()
        if saved is not None and saved[0] != config:
            raise ValueError('An existing similarity index uses different MinHash/LSH parameters')
        if saved is None:
            db.execute('INSERT INTO similarity_config VALUES (?)', (config,))
        self.compressor = zstd.ZstdCompressor(level=3)
        self.decompressor = zstd.ZstdDecompressor()

    def signature(self, shingles):
        if not shingles:
            return ()
        hashed = [int.from_bytes(hashlib.blake2b(s.encode('utf-8'), digest_size=8,
                  person=b'pdfparse-sh-v1').digest(), 'little') % self.PRIME for s in shingles]
        return tuple(min((a * value + b) % self.PRIME for value in hashed) for a, b in self.coefficients)

    def buckets(self, signature):
        for band in range(self.bands):
            start = band * self.rows
            yield band, struct.pack(f'<{self.rows}Q', *signature[start:start + self.rows])

    def add(self, pdf_key, chunk_id, shingles):
        # Exact strings are retained for verification; hashes only select candidates.
        data = json.dumps(sorted(shingles), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.db.execute('INSERT INTO similarity_chunks VALUES (?, ?, ?)',
                        (pdf_key, chunk_id, self.compressor.compress(data)))
        signature = self.signature(shingles)
        if signature:
            self.db.executemany('INSERT INTO lsh VALUES (?, ?, ?, ?)',
                                ((band, bucket, chunk_id, pdf_key) for band, bucket in self.buckets(signature)))

    def get_shingles(self, pdf_key, chunk_id):
        data = self.db.execute('SELECT shingles FROM similarity_chunks WHERE pdf_key=? AND chunk_id=?',
                               (pdf_key, chunk_id)).fetchone()
        if data is None:
            raise KeyError((pdf_key, chunk_id))
        return set(json.loads(self.decompressor.decompress(data[0])))

    def candidate_ids(self, pdf_key, chunk_id):
        # Reuse persisted band keys rather than recomputing the signature.
        buckets = self.db.execute('SELECT band, bucket FROM lsh_by_chunk WHERE pdf_key=? AND chunk_id=?',
                                  (pdf_key, chunk_id)).fetchall()
        candidates = set()
        for band, bucket in buckets:
            candidates.update(self.db.execute('''SELECT chunk_id, pdf_key FROM lsh
                WHERE band=? AND bucket=? AND NOT (pdf_key=? AND chunk_id=?)
                ORDER BY chunk_id COLLATE BINARY, pdf_key COLLATE BINARY LIMIT ?''',
                (band, bucket, pdf_key, chunk_id, self.max_candidates)))
        return sorted(candidates)[:self.max_candidates]

    def nearest(self, pdf_key, chunk_id):
        shingles = self.get_shingles(pdf_key, chunk_id)
        if not shingles:
            return NAN, NAN
        best, nearest = NAN, NAN
        for other_id, other_pdf in self.candidate_ids(pdf_key, chunk_id):
            other = self.get_shingles(other_pdf, other_id)
            intersection = len(shingles.intersection(other))
            union = len(shingles) + len(other) - intersection
            if not union:
                continue
            score = intersection / union
            # Candidates sorted by UTF-8/binary order (chunk_id, pdf_key); first wins ties.
            if math.isnan(best) or score > best:
                best, nearest = score, other_id
                if score == 1:
                    break  # Sorted candidates: neither the score nor tie-break can improve.
        return best, nearest

    def finalize(self):
        self.db.execute('CREATE INDEX IF NOT EXISTS lsh_chunk ON lsh(pdf_key, chunk_id)')
        self.db.execute('CREATE VIEW IF NOT EXISTS lsh_by_chunk AS SELECT pdf_key, chunk_id, band, bucket FROM lsh')
        self.db.commit()
