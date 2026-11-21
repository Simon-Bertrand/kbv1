import os
from pathlib import Path

import psycopg
import pytest

from dbclient import RagClient, _vector


@pytest.fixture
def client():
    dsn = os.environ.get('DBCLIENT_TEST_DSN')
    if not dsn:
        pytest.skip('Définir DBCLIENT_TEST_DSN sur une base de test initialisée dans rag.')
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute('BEGIN')
        try:
            # Use the same fixtures as the SQL suite; every test rolls them back.
            fixture = Path(__file__).resolve().parents[3] / 'sql/rag/tests/retrieval.sql'
            setup = fixture.read_text().split('DO $$', 1)[0].replace('BEGIN;', '', 1)
            connection.execute(setup)
            yield RagClient(connection, require_agent_user=False)
        finally:
            connection.execute('ROLLBACK')


def test_rag_client_enforces_agent_user_and_no_raw_sql():
    class DummyInfo:
        def __init__(self, user):
            self.user = user

    class DummyConnection:
        def __init__(self, user):
            self.info = DummyInfo(user)

    # Rejette tout utilisateur autre que 'agent'
    for forbidden_user in ('postgres', 'root', 'admin', 'anonymous'):
        with pytest.raises(PermissionError) as exc_info:
            RagClient(DummyConnection(forbidden_user))
        assert "RagClient requiert une connexion avec l'utilisateur PostgreSQL 'agent'" in str(exc_info.value)
        assert forbidden_user in str(exc_info.value)

    # Accepte l'utilisateur 'agent'
    agent_client = RagClient(DummyConnection('agent'))
    assert agent_client.connection.info.user == 'agent'

    # require_agent_user=False permet de bypasser explicitement pour les opérations de test/fixtures admin
    admin_client = RagClient(DummyConnection('postgres'), require_agent_user=False)
    assert admin_client.connection.info.user == 'postgres'

    # S'assurer que RagClient n'expose aucune méthode d'exécution de raw SQL
    assert not hasattr(agent_client, 'execute')
    assert not hasattr(agent_client, 'raw_sql')
    assert not hasattr(agent_client, 'query')


def test_vector_encoding():
    assert _vector(None) is None
    assert _vector((1, 0.5)) == '[1.0, 0.5]'
    for value in ([], [float('nan')], [float('inf')], '1,2'):
        with pytest.raises(ValueError):
            _vector(value)


def test_reads_preserve_json_unicode_order_and_empty_batches(client):
    rows = client.get_chunks(['test-rag-c2', 'absent', 'test-rag-c1', 'test-rag-c2'])
    assert [row['chunk_id'] for row in rows] == ['test-rag-c2', 'test-rag-c1']
    assert rows[0]['record']['text'] == 'Texte Unicode : été 中文'
    assert client.get_chunks([]) == [] and client.get_visuals([]) == []
    assert client.get_chunks(["x'); DROP SCHEMA rag CASCADE; --"]) == []
    assert client.get_visuals(['test-rag-v2'])[0]['record']['caption'] == 'Figure témoin'
    # The client doesn't mutate the injected connection's row factory or search path.
    assert client.connection.execute('SHOW search_path').fetchone() == ('pg_catalog',)
    assert isinstance(client.connection.execute('SELECT 1').fetchone(), tuple)


def test_text_visual_and_fused_search(client):
    text = [1.0] + [0.0] * 1023
    visual = [1.0] + [0.0] * 767
    kwargs = {'document_ids': ['test-rag-a'], 'metadata': {'category': 'manual'}}
    hit = client.search(text_query=text, top_k=1, **kwargs)[0]
    assert hit['id'] == 'test-rag-c1' and hit['evidence']['pages'] == [1, 2]
    hits = client.search(visual_query=visual, **kwargs)
    assert {hit['id'] for hit in hits} == {'test-rag-c1', 'test-rag-c2', 'test-rag-v2'}
    hit = client.search(text_query=text, visual_query=visual, **kwargs)[0]
    assert hit['text_rank'] == hit['visual_rank'] == 1
    assert hit['score'] == pytest.approx(2 / 61)
    assert client.search(text_query=text, document_ids=[]) == []
    assert client.search(text_query=text, document_ids=['test-rag-a'],
                         metadata={'category': 'other'}) == []


def test_sql_errors_propagate_and_transactions_stay_owned_by_caller(client):
    for call in (
        lambda: client.get_chunks(['x'] * 101),
        lambda: client.search(),
        lambda: client.search(text_query=[1.0, 2.0]),
        lambda: client.search(text_query=[0.0] * 1024),
    ):
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            with client.connection.transaction():  # savepoint in the test transaction
                call()
    assert client.get_chunks(['test-rag-c1'])


def test_schema_contents_and_shadowing(client):
    connection = client.connection
    assert connection.execute('''SELECT count(*) FROM pg_catalog.pg_extension e
        JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace
        WHERE e.extname IN ('vector','vectorscale','pg_textsearch') AND n.nspname='rag'
    ''').fetchone()[0] == 3
    assert connection.execute('''SELECT count(*) FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN (
            'documents','chunks','visuals','chunk_visuals','text_embeddings','visual_embeddings')
    ''').fetchone()[0] == 0
    connection.execute('CREATE TEMP TABLE documents (invalid_column integer)')
    connection.execute('SET LOCAL search_path = pg_temp, public, pg_catalog')
    assert client.get_chunks(['test-rag-c1'])[0]['document_id'] == 'test-rag-a'
    assert client.search(text_query=[1.0] + [0.0] * 1023)
