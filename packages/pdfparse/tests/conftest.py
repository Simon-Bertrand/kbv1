import base64
import pytest

@pytest.fixture(autouse=True)
def studio_credentials(monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setenv('PDFPARSE_ADMIN_USER', 'test-admin')
    monkeypatch.setenv('PDFPARSE_ADMIN_PASSWORD', 'test-password-long-enough')
    original = TestClient.__init__
    def initialize(self, *args, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        headers.setdefault('Authorization', 'Basic ' + base64.b64encode(b'test-admin:test-password-long-enough').decode())
        return original(self, *args, headers=headers, **kwargs)
    monkeypatch.setattr(TestClient, '__init__', initialize)
