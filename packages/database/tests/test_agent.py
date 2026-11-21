"""Real PostgreSQL/SCRAM/ACL checks in an isolated, disposable Docker container.

RAG_SECURITY_DOCKER_TESTS=1 pytest database/tests/test_agent.py -q
Build knowledgebase/postgres:local first. No existing volume is used.
"""
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip('psycopg')
pytestmark = pytest.mark.skipif(os.getenv('RAG_SECURITY_DOCKER_TESTS') != '1',
                                reason='Explicit Docker integration opt-in required')


@pytest.fixture(scope='module')
def server():
    name = 'knowledgebase-agent-test-' + uuid.uuid4().hex[:10]
    admin_password = secrets.token_urlsafe(24)
    # Quotes, backslashes and dollars must remain data, never SQL/shell syntax.
    agent_password = secrets.token_urlsafe(24) + "'\\$;"
    passwords = {
        'POSTGRES_PASSWORD': admin_password,
        'POSTGRES_AGENT_PASSWORD': agent_password,
        'POSTGRES_KM_READER_PASSWORD': secrets.token_urlsafe(24),
        'POSTGRES_KM_RERANKER_PASSWORD': secrets.token_urlsafe(24),
        'POSTGRES_KM_WRITER_PASSWORD': secrets.token_urlsafe(24),
        'POSTGRES_WEBUI_PASSWORD': secrets.token_urlsafe(24),
    }
    environment = {**os.environ, **passwords}
    image = os.getenv('KB_TEST_POSTGRES_IMAGE', 'knowledgebase/postgres:local')
    subprocess.run(['docker', 'run', '--pull=never', '-d', '--name', name,
                    '--tmpfs', '/var/lib/postgresql/data:rw', '-p', '127.0.0.1::5432',
                    '-e', 'POSTGRES_DB=knowledgebase', '-e', 'POSTGRES_USER=postgres',
                    *[arg for key in passwords for arg in ('-e', key)],
                    image], env=environment, check=True,
                   stdout=subprocess.DEVNULL)
    try:
        info = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
        port = info['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
        common = {'host': '127.0.0.1', 'port': port, 'dbname': 'knowledgebase', 'connect_timeout': 2}
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(**common, user='postgres', password=admin_password):
                    break
            except psycopg.OperationalError:
                state = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]['State']
                if not state['Running']:
                    pytest.fail(subprocess.check_output(['docker', 'logs', name], stderr=subprocess.STDOUT, text=True))
                time.sleep(.2)
        else:
            pytest.fail(subprocess.check_output(['docker', 'logs', name], stderr=subprocess.STDOUT, text=True))
        yield {'name': name, 'common': common, 'admin_password': admin_password,
               'agent_password': agent_password, 'passwords': passwords}
    finally:
        subprocess.run(['docker', 'rm', '-f', '-v', name], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_fresh_schema_serves_km_and_webui_roles(server):
    common = server['common']
    passwords = server['passwords']
    with psycopg.connect(**common, user='km_reader', password=passwords['POSTGRES_KM_READER_PASSWORD']) as reader:
        assert reader.execute("SELECT active_text_profile FROM rag.km_embedding_settings").fetchone() == (None,)
        assert reader.execute("SELECT * FROM rag.rag_search_profile('missing', 'test', p_mode => 'lexical')").fetchall() == []
    with psycopg.connect(**common, user='km_writer', password=passwords['POSTGRES_KM_WRITER_PASSWORD']) as writer:
        assert writer.execute("SELECT count(*) FROM rag.km_cache_chunks").fetchone() == (0,)
        assert writer.execute("SELECT to_regclass('rag.km_migrations') IS NULL").fetchone() == (True,)
    with psycopg.connect(**common, user='webui-user', password=passwords['POSTGRES_WEBUI_PASSWORD']) as webui:
        assert webui.execute('SELECT storage_bytes, revision, deleted_at FROM webui.conversations').fetchall() == []
        assert webui.execute('SELECT * FROM webui.request_buckets').fetchall() == []
        options = webui.execute("SELECT reloptions FROM pg_class WHERE oid='webui.request_buckets'::regclass").fetchone()[0]
        assert set(options) >= {'autovacuum_vacuum_scale_factor=0.05', 'autovacuum_vacuum_threshold=50',
                                'autovacuum_vacuum_cost_limit=1000'}


def test_sql_permission_suite_and_real_agent_session(server):
    with psycopg.connect(**server['common'], user='postgres', password=server['admin_password'], autocommit=True) as admin:
        admin.execute((Path(__file__).parents[1] / 'sql/rag/tests/agent_permissions.sql').read_text())
    with psycopg.connect(**server['common'], user='agent', password=server['agent_password'], autocommit=True) as agent:
        assert agent.execute('SELECT * FROM rag.rag_get_chunks(%s)', ([],)).fetchall() == []
        assert agent.execute('SELECT * FROM rag.rag_get_visuals(%s)', ([],)).fetchall() == []
        assert agent.execute('SELECT * FROM rag.rag_search(p_text_query => %s::rag.vector)',
                             ('[' + ','.join(['1'] + ['0'] * 1023) + ']',)).fetchall() == []
        # Read-only is defense in depth, not the security boundary: ACLs still deny writes.
        agent.execute('SET default_transaction_read_only = off')
        for sql in ('SELECT * FROM rag.documents', 'CREATE TEMP TABLE forbidden(id integer)',
                    'CREATE TABLE public.forbidden(id integer)', 'DELETE FROM rag.documents',
                    'SET ROLE rag_executor', 'SET ROLE postgres', 'SELECT rag.vector_dims(\'[1]\'::rag.vector)'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                agent.execute(sql)


def test_authentication_and_other_databases_are_rejected(server):
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(**server['common'], user='agent', password='wrong-password')
    for database in ('postgres', 'template1'):
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(**(server['common'] | {'dbname': database}), user='agent', password=server['agent_password'])
    # The trusted administrative Unix socket must not bypass the agent password.
    denied = subprocess.run(['docker', 'exec', '-e', 'PGPASSWORD=wrong-password', server['name'],
                             'psql', '-U', 'agent', '-d', 'knowledgebase', '-c', 'SELECT 1'],
                            capture_output=True)
    assert denied.returncode != 0


def test_password_rotation_and_bootstrap_are_idempotent(server):
    rotated = secrets.token_urlsafe(24) + "'\\$;"
    environment = dict(os.environ, POSTGRES_AGENT_PASSWORD=rotated)
    subprocess.run(['docker', 'exec', '-e', 'POSTGRES_AGENT_PASSWORD', server['name'], 'init-rag-agent'],
                   env=environment, check=True, stdout=subprocess.DEVNULL)
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(**server['common'], user='agent', password=server['agent_password'])
    with psycopg.connect(**server['common'], user='agent', password=rotated, autocommit=True) as agent:
        assert agent.execute('SELECT * FROM rag.rag_get_chunks(%s)', ([],)).fetchall() == []
    hba = subprocess.check_output(['docker', 'exec', server['name'], 'cat', '/var/lib/postgresql/data/pg_hba.conf'], text=True)
    assert hba.count('# BEGIN RAG AGENT') == 1


def test_reranker_search_role_executes_profile_search_without_write_access(server):
    from km.store import Knowledgebase, handle
    from psycopg.conninfo import make_conninfo

    profile = 'direct-reranker-test'
    vector = '[' + ','.join(['1'] + ['0'] * 1023) + ']'
    with psycopg.connect(**server['common'], user='postgres', password=server['admin_password']) as admin:
        admin.execute("INSERT INTO rag.embedding_profiles(profile,modality,encoder_fingerprint,dimension,configuration) VALUES (%s,'text','fixture',1024,'{}')", (profile,))
        admin.execute("INSERT INTO rag.km_profile_documents(profile,document_id,filepath,metadata) VALUES (%s,'test-doc','sample.pdf','{}')", (profile,))
        admin.execute("INSERT INTO rag.km_profile_units(profile,unit_id,document_id,kind,record) VALUES (%s,'test-unit','test-doc','chunk','{\"text\":\"contrat\",\"pages\":[1]}')", (profile,))
        admin.execute("INSERT INTO rag.km_text_embeddings(profile,chunk_id,input_hash,embedding) VALUES (%s,'test-unit','hash',%s::rag.vector)", (profile, vector))
        admin.execute('UPDATE rag.km_embedding_settings SET active_text_profile=%s', (profile,))

    dsn = make_conninfo(**server['common'], user='km_reranker',
                        password=server['passwords']['POSTGRES_KM_RERANKER_PASSWORD'])
    store = Knowledgebase(dsn, km=True)
    with store.pooled():
        with store.connection() as conn:
            pid = conn.info.backend_pid
            assert conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
            assert conn.execute('SHOW transaction_isolation').fetchone()['transaction_isolation'] == 'repeatable read'
        for scope in ({}, {'document_ids': [handle('d', 'test-doc')]}):
            result = store.search('contrat', 'hybrid', 1, {}, vector, fingerprint='fixture', **scope)
            assert result['results'][0]['page_id'] == handle('c', 'test-unit')
            assert result['profile'] == profile
        assert store.search('contrat', 'hybrid', 1, {}, vector, fingerprint='fixture',
                            document_ids=[handle('d', 'outside')])['results'] == []
        with store.connection() as conn:
            assert conn.info.backend_pid == pid

    with psycopg.connect(dsn, autocommit=True) as reranker:
        assert reranker.execute("SHOW default_transaction_read_only").fetchone() == ('on',)
        assert reranker.execute("SELECT id FROM rag.rag_search_profile(%s,'contrat',%s::rag.vector)",
                                (profile, vector)).fetchall() == [('test-unit',)]
        reranker.execute('SET default_transaction_read_only = off')
        for statement in ("DELETE FROM rag.km_profile_units", "SELECT * FROM webui.conversations",
                          "SELECT * FROM rag.km_chunk_indexing", "SET ROLE km_reader"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                reranker.execute(statement)

    if image := os.getenv('KB_TEST_RERANKER_IMAGE'):
        token = secrets.token_urlsafe(24)
        container_dsn = make_conninfo(dsn, host='127.0.0.1', port=5432)
        subprocess.run(
            ['docker', 'run', '--rm', '--pull=never', '--network', f"container:{server['name']}",
             '--entrypoint', 'python', '-e', 'RERANKER_DATABASE_URL', '-e', 'KM_RERANKER_TOKEN',
             image, '-c', RERANKER_HTTP_CHECK],
            env={**os.environ, 'RERANKER_DATABASE_URL': container_dsn, 'KM_RERANKER_TOKEN': token},
            check=True, timeout=60,
        )


RERANKER_HTTP_CHECK = '''
import asyncio
import os
import httpx
from km.store import handle
from km_reranker.api import create_app
from km_reranker.contracts import Scores


class Backend:
    metadata = {'model': 'fixture', 'revision': 'fixture', 'fingerprint': 'fixture', 'score_kind': 'logit'}

    def score(self, query, documents):
        assert query == 'contrat'
        assert documents == ['contrat']
        return Scores([1.0], 1, (), False)


async def check():
    app = create_app(backend=Backend())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://reranker') as client:
            body = {'question': 'contrat', 'mode': 'hybrid', 'limit': 1, 'candidates': 1,
                    'vector': [1.0] + [0.0] * 1023, 'fingerprint': 'fixture'}
            headers = {'Authorization': 'Bearer ' + os.environ['KM_RERANKER_TOKEN']}
            assert (await client.post('/search', json=body)).status_code == 401
            response = await client.post('/search', json=body, headers=headers)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result['profile'] == 'direct-reranker-test'
            assert result['reranking']['applied']
            assert result['results'][0]['locator']['pages'] == [1]
            body['document_ids'] = [handle('d', 'outside')]
            response = await client.post('/search', json=body, headers=headers)
            assert response.status_code == 200 and response.json()['results'] == []


asyncio.run(check())
'''
