"""Readable independent oracle: repeated traversals, explicit probability distributions."""
from collections import Counter
import math
import statistics
import unicodedata as ud
import zstandard as zstd
from .conventions import COLUMNS, NAN, STOPWORDS, normalized, tokens
from .metrics import Features
from .repetitions import longest_repeat_reference


def extract_reference(original: str, pdf_key: str, chunk_id: str) -> Features:
    data = original.encode('utf-8')
    text = normalized(original)
    words = list(tokens(text, already_normalized=True))
    frequencies = Counter(words)
    c, n, v = len(text), len(words), len(frequencies)
    row = dict.fromkeys(COLUMNS, NAN)
    row.update(pdf_key=pdf_key, chunk_id=chunk_id, nearest_chunk_id=NAN, n_bytes=len(data),
               n_chars=c, n_words=n, n_distinct_chars=len(set(text)), n_distinct_words=v)
    def shannon(values):
        counts = Counter(values)
        total = sum(counts.values())
        return -sum((f / total) * math.log2(f / total) for f in counts.values()) if total else NAN
    def repeated(values, width):
        grams = [tuple(values[i:i + width]) for i in range(len(values) - width + 1)]
        return 1 - len(set(grams)) / len(grams) if grams else NAN
    row['entropy_chars'] = shannon(text)
    row['entropy_words'] = shannon(words)
    row['unicode_category_entropy'] = shannon([ud.category(ch)[0] for ch in text])
    tests = {'special_chars_prop': lambda ch: not ch.isalnum() and not ch.isspace(),
             'letter_ratio': lambda ch: ud.category(ch).startswith('L'),
             'digit_ratio': lambda ch: ud.category(ch) == 'Nd',
             'punctuation_ratio': lambda ch: ud.category(ch).startswith('P'),
             'symbol_ratio': lambda ch: ud.category(ch).startswith('S'),
             'control_ratio': lambda ch: ud.category(ch) == 'Cc',
             'replacement_char_ratio': lambda ch: ch == '\ufffd'}
    for name, predicate in tests.items():
        row[name] = sum(predicate(ch) for ch in text) / c if c else NAN
    for order in (1, 2):
        joints = Counter(tuple(text[i:i + order + 1]) for i in range(c - order))
        prefixes = Counter(tuple(text[i:i + order]) for i in range(c - order))
        total = sum(joints.values())
        row[f'conditional_entropy_{order}'] = (-sum(
            count / total * math.log2(count / prefixes[gram[:-1]])
            for gram, count in joints.items()) if total else NAN)
    if data:
        row['bytes_std'] = statistics.pstdev(data)
        row['compression_ratio'] = len(zstd.ZstdCompressor(level=3, threads=0,
            write_checksum=False, write_content_size=True, write_dict_id=False).compress(data)) / len(data)
    if c:
        row['max_repeated_substring_ratio'] = longest_repeat_reference(text) / c
    if n:
        spectrum = Counter(frequencies.values())
        row.update(root_type_token_ratio=v / math.sqrt(n),
            yules_k=10000 * (sum(r * r * vr for r, vr in spectrum.items()) / n ** 2 - 1 / n),
            hapax_ratio=spectrum[1] / v, mean_token_length=statistics.mean(map(len, words)),
            max_token_length=max(map(len, words)), stopword_ratio=sum(t in STOPWORDS for t in words) / n)
    if v >= 2:
        xs = [math.log(r) for r in range(1, v + 1)]
        ys = [math.log(f) for f in sorted(frequencies.values(), reverse=True)]
        fit = statistics.linear_regression(xs, ys)
        row['zipf_deviation'] = statistics.mean((y - fit.intercept - fit.slope * x) ** 2 for x, y in zip(xs, ys))
    row['repeated_word_trigram_ratio'] = repeated(words, 3)
    row['repeated_word_5gram_ratio'] = repeated(words, 5)
    row['repeated_char_trigram_ratio'] = repeated(text, 3)
    return Features(row, frequencies, {text[i:i + 5] for i in range(c - 4)})


def corpus_reference(records, *, candidates=None, pdf_keys=()):
    """Small-corpus oracle, exhaustive by default (never for production K).

    candidates[(pdf_key, chunk_id)] optionally restricts exact comparisons to
    (chunk_id, pdf_key) pairs from an optimized index for like-for-like checks.
    """
    features = [extract_reference(text, pdf, chunk) for pdf, chunk, text in records]
    documents = {pdf: set() for pdf in pdf_keys}
    for feature in features:
        documents.setdefault(feature.row['pdf_key'], set()).update(feature.words)
    df = Counter(word for document in documents.values() for word in document)
    d = len(documents)
    for feature in features:
        row, words = feature.row, feature.words
        row['average_idf'] = sum(math.log((d + 1) / (df[t] + 1)) for t in words) / len(words) if words and d else NAN
        eligible = None if candidates is None else set(candidates[(row['pdf_key'], row['chunk_id'])])
        comparisons = sorted(features, key=lambda f: (f.row['chunk_id'], f.row['pdf_key']))
        best, nearest = NAN, NAN
        if feature.shingles:
            for other in comparisons:
                identity = other.row['pdf_key'], other.row['chunk_id']
                if identity == (row['pdf_key'], row['chunk_id']):
                    continue
                if eligible is not None and (identity[1], identity[0]) not in eligible:
                    continue
                union = feature.shingles | other.shingles
                if not union:
                    continue
                similarity = len(feature.shingles & other.shingles) / len(union)
                if math.isnan(best) or similarity > best:
                    best, nearest = similarity, other.row['chunk_id']
        row['max_jaccard_shingle_similarity'], row['nearest_chunk_id'] = best, nearest
    return [f.row for f in features]
