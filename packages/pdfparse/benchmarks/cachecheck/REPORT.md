# Mesures cachecheck

Mesures locales sans traçage ; mémoire et profils mesurés lors de passages séparés.

| Caractères | Référence ms/chunk | Optimisée ms/chunk | Réf./opt. | Opt. chunks/s | CPU opt. ms | Pic Python réf./opt. KiB | Corpus chunks/s |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 0.164 | 0.087 | 1.88× | 11506.7 | 0.088 | 26.7 / 29.2 | 1302.1 |
| 250 | 0.575 | 0.317 | 1.82× | 3157.1 | 0.319 | 106.7 / 123.8 | 441.9 |
| 1000 | 2.607 | 1.345 | 1.94× | 743.6 | 1.352 | 355.1 / 518.4 | 131.7 |
| 5000 | 12.824 | 6.297 | 2.04× | 158.8 | 6.334 | 1038.4 / 2536.6 | 34.7 |
| 20000 | 58.139 | 25.317 | 2.30× | 39.5 | 25.084 | 3416.7 / 9213.4 | 11.8 |

Détail des familles optimisées (ms cumulées sous cProfile, passage séparé ; pas des temps sans instrumentation) :

| Caractères | LRS | Tokenisation | Ajustement Zipf | Entropies | Zstd | Parcours mutualisés, temps propre | Blocs Python retenus réf./opt. |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 0.103 | 0.014 | 0.015 | 0.016 | 0.003 | 0.109 | 334 / 263 |
| 250 | 0.389 | 0.048 | 0.033 | 0.026 | 0.010 | 0.258 | 1394 / 605 |
| 1000 | 1.862 | 0.185 | 0.082 | 0.046 | 0.017 | 0.850 | 4486 / 1534 |
| 5000 | 10.786 | 0.917 | 0.191 | 0.084 | 0.036 | 3.710 | 8433 / 5189 |
| 20000 | 42.415 | 3.780 | 0.222 | 0.097 | 0.105 | 13.867 | 15385 / 13504 |

Familles globales sous cProfile (ms pour le corpus complet, passage séparé) :

| Caractères | MinHash | Requêtes IDF | Finalisation index | Candidats LSH | Jaccard avec recherche candidats |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 8.179 | 0.222 | 0.297 | 1.274 | 1.481 |
| 250 | 39.363 | 0.452 | 0.376 | 1.220 | 1.628 |
| 1000 | 141.789 | 1.002 | 0.545 | 1.357 | 2.610 |
| 5000 | 542.830 | 2.336 | 0.945 | 1.565 | 5.074 |
| 20000 | 1434.084 | 2.476 | 1.110 | 1.600 | 10.274 |

La famille Jaccard inclut la recherche LSH et le chargement des shingles ; ces temps cumulés ne sont pas additifs.
Le rapport réf./opt. inférieur à 1 signifie un ralentissement, supérieur à 1 une accélération mesurée.
Corpus : 1 worker, 64 permutations, 16 bandes, 128 candidats maximum, lots de 4 ; 12 chunks synthétiques par taille. Ces petites mesures ne valident pas un débit sur des millions de chunks.
tracemalloc mesure les allocations Python vivantes et le pic transitoire, pas le total des allocations effectuées ni les buffers natifs Zstd/SQLite, ni le RSS. Les profils .prof et results.json détaillent les familles.

Environnement :

```json
{
  "python": "3.13.13",
  "platform": "Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39",
  "processor": "x86_64",
  "unicode": "15.1.0",
  "zstandard": "0.25.0",
  "libzstd": [
    1,
    5,
    7
  ],
  "convention": "pdfparse.chunkmetrics.v1",
  "stopwords": "fr-en-small-v1",
  "stopwords_sha256": "a89d5f4b16906317086ea51f4bfafed48d8a6803c80074ea3adbeeab0565b3e3",
  "iterations": 20,
  "corpus_chunks": 12,
  "seed": 42
}
```
