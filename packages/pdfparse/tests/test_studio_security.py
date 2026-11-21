from fastapi.testclient import TestClient


def test_studio_requires_credentials_even_for_read_routes(tmp_path,monkeypatch):
    from pdfparse.backend.app import create_app
    app=create_app(root=tmp_path,load_models_on_startup=False)
    with TestClient(app) as client:
        client.headers.pop('authorization',None)
        assert client.get('/').status_code==401
        assert client.get('/api/settings').status_code==401
        assert client.get('/api-docs').status_code==401
        monkeypatch.delenv('PDFPARSE_ADMIN_PASSWORD')
        assert client.get('/').status_code==503
