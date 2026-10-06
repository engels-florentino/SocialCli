import importlib.util
from pathlib import Path

import pytest


def settings_type():
    assert importlib.util.find_spec('socialctl.connection_service.settings') is not None, 'connection settings are missing'
    from socialctl.connection_service.settings import Settings
    return Settings


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com?token=secret', 'https://example.com#x'])
def test_service_origin_rejects_unsafe_urls(tmp_path, url):
    Settings = settings_type()
    with pytest.raises(ValueError):
        Settings(public_url=url, database=tmp_path / 'v.sqlite3', encryption_keys=[b'x'])


def test_settings_require_encryption_key_and_explicit_local_development(tmp_path):
    Settings = settings_type()
    with pytest.raises(ValueError):
        Settings(public_url='https://example.com', database=tmp_path / 'v.sqlite3', encryption_keys=[])
    with pytest.raises(ValueError):
        Settings(public_url='http://127.0.0.1:8787', database=tmp_path / 'v.sqlite3', encryption_keys=[b'x'])
    value = Settings(public_url='http://127.0.0.1:8787', database=tmp_path / 'v.sqlite3', encryption_keys=[b'x'], allow_http_local=True)
    assert value.callback('youtube') == 'http://127.0.0.1:8787/oauth/youtube/callback'
