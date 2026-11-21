from types import SimpleNamespace

import pytest

from km_embeddings import indexing


def test_publication_rejects_a_different_e5_tokenizer_before_contacting_database(monkeypatch):
    monkeypatch.setattr(indexing, 'validate', lambda bundle: None)
    monkeypatch.setattr(indexing.psycopg, 'connect', lambda *args, **kwargs: pytest.fail('Index opened'))
    encoder = SimpleNamespace(metadata={'dimension': 1024, 'chunk_tokenizer_hash': 'encoder-assets'},
                              fingerprint='encoder')
    bundle = {'document_id': 'document', 'visuals': [], 'chunks': [
        {'tokenizer_id': 'multilingual-e5-large:passage:profile:files-v1:pdfparse-assets'},
    ]}
    with pytest.raises(ValueError, match='Tokenizer E5 différent'):
        indexing.Publisher('unused', encoder).publish(bundle)
