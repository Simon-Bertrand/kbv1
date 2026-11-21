"""Versioned conventions shared by the optimized extractor and its oracle."""
from collections import Counter
import math
import unicodedata as ud

VERSION = 'pdfparse.chunkmetrics.v1'
STOPWORDS_VERSION = 'fr-en-small-v1'
# Deliberately small, explicit and immutable; no language detection or downloads.
STOPWORDS = frozenset('''a about an and are as at be been being but by can could did do does doing for
from had has have he her hers herself him himself his how i if in into is it its
itself me more most my myself no nor not of off on once only or other our ours
ourselves out over own same she should so some such than that the their theirs
them themselves then there these they this those through to too under until up
very was we were what when where which while who whom why will with you your
yours yourself yourselves à ai ainsi alors au aucun aussi autre aux avec avoir
bon car ce ceci cela ces cet cette ceux chaque comme comment dans de des du
dedans dehors depuis deux devrait doit donc dos droite début elle elles en
encore entre es est et eu fait faites fois font force haut hors ici il ils je
juste la le les leur leurs lui là ma maintenant mais mes mien moins mon mot
même ni nom non nos notre nous nouveaux ou où par parce parole pas personnes
peu peut pièce plupart pour pourquoi quand que quel quelle quelles quels qui
sa sans se sera ses seulement si sien son sont sous soyez sujet sur ta tandis
tel tellement tels tes toi ton tous tout toute toutes trop très tu un une valeur
voie voient vont votre vous vu ça étaient état étions été être'''.split())

COLUMNS = tuple('''pdf_key chunk_id n_bytes n_chars n_words special_chars_prop bytes_std
n_distinct_chars letter_ratio digit_ratio punctuation_ratio symbol_ratio control_ratio
replacement_char_ratio unicode_category_entropy entropy_words entropy_chars
conditional_entropy_1 conditional_entropy_2 n_distinct_words root_type_token_ratio
yules_k hapax_ratio mean_token_length max_token_length stopword_ratio compression_ratio
repeated_word_trigram_ratio repeated_word_5gram_ratio repeated_char_trigram_ratio
max_repeated_substring_ratio average_idf zipf_deviation max_jaccard_shingle_similarity
nearest_chunk_id'''.split())
NAN = float('nan')


def normalized(text: str) -> str:
    return ud.normalize('NFC', text)


def tokens(text: str, *, already_normalized=False):
    """Maximal [L N][L M N]* runs in NFC(casefold(NFC(text))).

    Underscores, apostrophes, hyphens, symbols and whitespace split tokens.
    An isolated combining mark cannot start a token. N includes all number classes.
    """
    start = None
    lexical = normalized((text if already_normalized else normalized(text)).casefold())
    for i, char in enumerate(lexical):
        category = ud.category(char)[0]
        if category in 'LN' or (category == 'M' and start is not None):
            if start is None:
                start = i
        elif start is not None:
            yield lexical[start:i]
            start = None
    if start is not None:
        yield lexical[start:]


def entropy(counts, total=None):
    total = sum(counts) if total is None else total
    if not total:
        return NAN
    return math.fsum((n / total) * math.log2(total / n) for n in counts if n)


def repeat_ratio(distinct, total):
    return 1 - distinct / total if total else NAN


def shingles(text):
    return {text[i:i + 5] for i in range(max(0, len(text) - 4))}


def zipf_error(frequencies):
    ordered = sorted(frequencies, reverse=True)
    n = len(ordered)
    # An intercept and slope require at least two distinct ranks.
    if n < 2:
        return NAN
    xs = [math.log(rank) for rank in range(1, n + 1)]
    ys = [math.log(freq) for freq in ordered]
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    denominator = math.fsum((x - mx) ** 2 for x in xs)
    slope = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denominator
    return math.fsum((y - my - slope * (x - mx)) ** 2 for x, y in zip(xs, ys)) / n
