"""One extraction of shared features; no corpus state or inference dependencies."""
from collections import Counter, deque
from dataclasses import dataclass
import math
import unicodedata as ud
import zstandard as zstd

from .conventions import COLUMNS, NAN, STOPWORDS, entropy, normalized, repeat_ratio, tokens, zipf_error
from .repetitions import longest_repeat


@dataclass(slots=True)
class Features:
    row: dict
    words: Counter
    shingles: set[str]


class ChunkMetrics:
    """One instance per worker; its reusable Zstd context must not be shared by threads."""

    def __init__(self):
        self.compressor = zstd.ZstdCompressor(level=3, threads=0, write_checksum=False,
                                               write_content_size=True, write_dict_id=False)

    def extract(self, text: str, pdf_key: str, chunk_id: str) -> Features:
        encoded = text.encode('utf-8', errors='strict')
        text = normalized(text)
        c, b = len(text), len(encoded)
        row = dict.fromkeys(COLUMNS, NAN)
        row.update(pdf_key=pdf_key, chunk_id=chunk_id, nearest_chunk_id=NAN, n_bytes=b, n_chars=c)
        characters, pairs, triples, categories = Counter(), Counter(), Counter(), Counter()
        shingle_set = set()
        for i, char in enumerate(text):
            characters[char] += 1
            if i >= 1:
                pairs[text[i - 1:i + 1]] += 1
            if i >= 2:
                triples[text[i - 2:i + 1]] += 1
            if i >= 4:
                shingle_set.add(text[i - 4:i + 1])
        special = letters = digits = punctuation = symbols = controls = 0
        for char, count in characters.items():
            category = ud.category(char)
            categories[category[0]] += count
            special += count * (not char.isalnum() and not char.isspace())
            letters += count * (category[0] == 'L')
            digits += count * (category == 'Nd')
            punctuation += count * (category[0] == 'P')
            symbols += count * (category[0] == 'S')
            controls += count * (category == 'Cc')
        row['n_distinct_chars'] = len(characters)
        if c:
            for name, count in (('special_chars_prop', special), ('letter_ratio', letters),
                                ('digit_ratio', digits), ('punctuation_ratio', punctuation),
                                ('symbol_ratio', symbols), ('control_ratio', controls),
                                ('replacement_char_ratio', characters['\ufffd'])):
                row[name] = count / c
        histogram = Counter(encoded)
        if b:
            # Integer moments before division avoid catastrophic cancellation.
            first = sum(value * count for value, count in histogram.items())
            second = sum(value * value * count for value, count in histogram.items())
            row['bytes_std'] = math.sqrt(second * b - first * first) / b
            row['compression_ratio'] = len(self.compressor.compress(encoded)) / b
        row['unicode_category_entropy'] = entropy(categories.values(), c)
        row['entropy_chars'] = entropy(characters.values(), c)
        if c >= 2:
            # All bigram prefixes: text[:-1], obtained without an extra traversal.
            prefix_chars = characters.copy()
            prefix_chars[text[-1]] -= 1
            row['conditional_entropy_1'] = math.fsum(
                count * math.log2(prefix_chars[gram[0]] / count) for gram, count in pairs.items()) / (c - 1)
        if c >= 3:
            prefix_pairs = pairs.copy()
            prefix_pairs[text[-2:]] -= 1
            row['conditional_entropy_2'] = math.fsum(
                count * math.log2(prefix_pairs[gram[:2]] / count) for gram, count in triples.items()) / (c - 2)
        row['repeated_char_trigram_ratio'] = repeat_ratio(len(triples), max(0, c - 2))
        words = Counter()
        window = deque(maxlen=5)
        word3, word5 = set(), set()
        n = length_sum = maximum = stops = 0
        for token in tokens(text, already_normalized=True):
            words[token] += 1
            n += 1
            size = len(token)
            length_sum += size
            maximum = max(maximum, size)
            stops += token in STOPWORDS
            window.append(token)
            if n >= 3:
                # One bounded tuple; reuse it for both fixed-window metrics.
                gram = tuple(window)
                word3.add(gram[-3:])
                if n >= 5:
                    word5.add(gram)
        v = len(words)
        row.update(n_words=n, n_distinct_words=v)
        row['entropy_words'] = entropy(words.values(), n)
        row['zipf_deviation'] = zipf_error(words.values())
        if n:
            row.update(root_type_token_ratio=v / math.sqrt(n),
                       yules_k=10000 * sum(f * (f - 1) for f in words.values()) / (n * n),
                       hapax_ratio=sum(f == 1 for f in words.values()) / v,
                       mean_token_length=length_sum / n, max_token_length=maximum,
                       stopword_ratio=stops / n)
        row['repeated_word_trigram_ratio'] = repeat_ratio(len(word3), max(0, n - 2))
        row['repeated_word_5gram_ratio'] = repeat_ratio(len(word5), max(0, n - 4))
        # Release transient counters before allocating the suffix automaton.
        del characters, pairs, triples, categories, histogram, word3, word5, window
        if c:
            row['max_repeated_substring_ratio'] = longest_repeat(text) / c
        return Features(row, words, shingle_set)
