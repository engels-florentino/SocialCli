from pathlib import Path

import pytest

from socialctl.adapters.facebook import FacebookAdapter
from socialctl.adapters.instagram import InstagramAdapter
from socialctl.adapters.tiktok import TikTokAdapter
from socialctl.brands import Brand
from socialctl.models import Platform, PlatformPost


@pytest.mark.parametrize('adapter,platform', [
    (FacebookAdapter(), Platform.FACEBOOK),
    (InstagramAdapter(), Platform.INSTAGRAM),
    (TikTokAdapter(), Platform.TIKTOK),
])
def test_missing_account_validation_is_explained_in_english(adapter, platform):
    brand = Brand(nombre='Creator', raiz=Path('/fictional/workspace'), cuentas={})
    post = PlatformPost(platform=platform, body='Creator-provided content')
    errors = adapter.validate(post, brand)
    assert any('missing' in error.motivo.lower() for error in errors)
    assert all('falta ' not in error.motivo for error in errors)
