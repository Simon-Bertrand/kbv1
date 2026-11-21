"""Test la validation des variables d'environnement au démarrage selon KB_ENV."""
import os
import pytest
from pathlib import Path

from pdfparse.backend.app import create_app, validate_startup_environment


def test_startup_validation_dev_mode(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv('KB_ENV', 'dev')
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    monkeypatch.delenv('PDFPARSE_SOURCE_ROOT', raising=False)
    monkeypatch.delenv('DATA_DIR', raising=False)
    monkeypatch.delenv('PDFPARSE_ASSETS_DIR', raising=False)

    import logging
    with caplog.at_level(logging.WARNING):
        validate_startup_environment(root=None, assets_dir=None)

    assert any("ALLOWED_HOSTS" in record.message for record in caplog.records)
    assert any("PDFPARSE_ASSETS_DIR" in record.message for record in caplog.records)


def test_startup_validation_prod_mode_missing_vars_fails(monkeypatch):
    monkeypatch.setenv('KB_ENV', 'production')
    monkeypatch.delenv('ALLOWED_HOSTS', raising=False)
    monkeypatch.delenv('PDFPARSE_SOURCE_ROOT', raising=False)
    monkeypatch.delenv('DATA_DIR', raising=False)

    with pytest.raises(RuntimeError, match="PDFPARSE_SOURCE_ROOT ou DATA_DIR est obligatoire en production"):
        validate_startup_environment(root=None, assets_dir=None)


def test_startup_validation_prod_mode_wildcard_hosts_fails(monkeypatch, tmp_path):
    monkeypatch.setenv('KB_ENV', 'production')
    monkeypatch.setenv('PDFPARSE_SOURCE_ROOT', str(tmp_path))
    monkeypatch.setenv('ALLOWED_HOSTS', '*')
    monkeypatch.setenv('PDFPARSE_ASSETS_DIR', str(tmp_path / 'assets'))

    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS doit être explicitement restreint"):
        validate_startup_environment(root=tmp_path, assets_dir=tmp_path / 'assets')


def test_startup_validation_prod_mode_success(monkeypatch, tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    monkeypatch.setenv('KB_ENV', 'production')
    monkeypatch.setenv('PDFPARSE_SOURCE_ROOT', str(tmp_path))
    monkeypatch.setenv('ALLOWED_HOSTS', 'pdfparse.internal,localhost')
    monkeypatch.setenv('PDFPARSE_ASSETS_DIR', str(assets))

    # Ne doit pas lever d'exception
    validate_startup_environment(root=tmp_path, assets_dir=assets)


def test_startup_validation_invalid_kb_env(monkeypatch):
    monkeypatch.setenv('KB_ENV', 'invalid_environment')
    with pytest.raises(ValueError, match="KB_ENV invalide"):
        validate_startup_environment(root=None, assets_dir=None)
