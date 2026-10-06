"""Official HTTP boundaries: permissions, identity discovery and refresh rotation."""
import importlib.util
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from socialctl.connection_service.settings import Settings, AppCredentials

BASIC = 'https://www.googleapis.com/auth/youtube.readonly'
UPLOAD = 'https://www.googleapis.com/auth/youtube.upload'


def providers_type():
    assert importlib.util.find_spec('socialctl.connection_service.providers') is not None, 'provider connectors are missing'
    from socialctl.connection_service.providers import Providers
    return Providers


def setup(tmp_path, handler):
    settings = Settings('https://social.example', tmp_path / 'vault.sqlite3', [b'x'],
        providers={p: AppCredentials('fictional-client', 'fictional-app-secret') for p in ['youtube', 'tiktok', 'facebook', 'instagram']})
    return providers_type()(settings, httpx.Client(transport=httpx.MockTransport(handler)))


def test_google_consent_uses_fixed_callback_and_optional_permissions(tmp_path):
    p = setup(tmp_path, lambda r: httpx.Response(500))
    params = parse_qs(urlsplit(p.authorization_url('youtube', 'state', 'verifier')).query)
    assert params['redirect_uri'] == ['https://social.example/oauth/youtube/callback']
    assert set(params['scope'][0].split()) == {BASIC, UPLOAD}
    assert params['access_type'] == ['offline']
    assert params['code_challenge_method'] == ['S256']
    assert 'fictional-app-secret' not in str(params)
    extra = parse_qs(urlsplit(p.authorization_url('youtube', 'state', 'verifier', management=True, analytics=True)).query)
    assert 'https://www.googleapis.com/auth/youtube.force-ssl' in extra['scope'][0]
    assert 'https://www.googleapis.com/auth/yt-analytics.readonly' in extra['scope'][0]


def test_google_exchange_discovers_actual_channel_and_keeps_refresh_token_server_side(tmp_path):
    def handler(r):
        if r.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token':'fictional-access', 'refresh_token':'fictional-refresh', 'expires_in':3600, 'scope':f'{BASIC} {UPLOAD}'})
        assert r.url.path == '/youtube/v3/channels'
        assert r.headers['authorization'] == 'Bearer fictional-access'
        return httpx.Response(200, json={'items':[{'id':'UC-fictional', 'snippet':{'title':'Example creator'}}]})
    grant = setup(tmp_path, handler).exchange('youtube', 'code', 'verifier')
    assert grant['accounts'] == [{'id':'UC-fictional', 'name':'Example creator'}]
    assert grant['grant']['refresh_token'] == 'fictional-refresh'
    assert 'client_secret' not in grant['grant']


@pytest.mark.parametrize('response', [
    {'access_token':'a', 'refresh_token':'r', 'expires_in':3600, 'scope':UPLOAD},
    {'access_token':'a', 'expires_in':3600, 'scope':f'{BASIC} {UPLOAD}'},
    {'access_token':'a', 'refresh_token':'r', 'expires_in':float('inf'), 'scope':f'{BASIC} {UPLOAD}'},
])
def test_incomplete_google_grants_are_rejected(tmp_path, response):
    p = setup(tmp_path, lambda r: httpx.Response(200, json=response) if response.get('expires_in') != float('inf') else httpx.Response(200, content=b'{"access_token":"a","refresh_token":"r","expires_in":1e999,"scope":"x"}'))
    with pytest.raises(ValueError):
        p.exchange('youtube', 'code', 'verifier')


def test_google_rejects_ambiguous_channel_identity(tmp_path):
    def handler(r):
        if r.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token':'a','refresh_token':'r','expires_in':3600,'scope':f'{BASIC} {UPLOAD}'})
        return httpx.Response(200, json={'items':[{'id':'one'}, {'id':'two'}]})
    with pytest.raises(ValueError, match='account discovery failed'):
        setup(tmp_path, handler).exchange('youtube', 'code', 'verifier')


def test_tiktok_inbox_permissions_and_profile_identity(tmp_path):
    def handler(r):
        if r.url.path == '/v2/oauth/token/':
            return httpx.Response(200, json={'access_token':'a', 'refresh_token':'r', 'expires_in':86400, 'refresh_expires_in':31536000, 'scope':'user.info.basic,video.upload', 'open_id':'creator-one'})
        assert r.url.path == '/v2/user/info/'
        return httpx.Response(200, json={'data':{'user':{'open_id':'creator-one','display_name':'Example'}}, 'error':{'code':'ok'}})
    p = setup(tmp_path, handler)
    params = parse_qs(urlsplit(p.authorization_url('tiktok', 'state', 'verifier')).query)
    assert params['scope'] == ['user.info.basic,video.upload']
    grant = p.exchange('tiktok', 'code', 'verifier')
    assert grant['accounts'] == [{'id':'creator-one','name':'Example'}]


def test_tiktok_refresh_persists_rotated_refresh_token_and_identity(tmp_path):
    p = setup(tmp_path, lambda r: httpx.Response(200, json={'access_token':'new-a', 'refresh_token':'new-r', 'expires_in':86400,'scope':'user.info.basic,video.upload','open_id':'creator-one'}))
    new = p.refresh('tiktok', {'access_token':'old-a','refresh_token':'old-r','expires_at':1,'granted_scopes':['user.info.basic','video.upload'],'open_id':'creator-one'})
    assert new['refresh_token'] == 'new-r'
    assert new['access_token'] == 'new-a'
    with pytest.raises(ValueError):
        p.refresh('tiktok', {'refresh_token':'old-r','open_id':'another-creator', 'granted_scopes':['user.info.basic','video.upload']})


def test_meta_lists_only_authorized_pages_and_connected_instagram(tmp_path):
    permissions = ['pages_show_list','pages_read_engagement','instagram_basic','instagram_content_publish']
    def handler(r):
        if r.url.path.endswith('/oauth/access_token'):
            return httpx.Response(200, json={'access_token':'user-a', 'expires_in':5184000})
        if r.url.path.endswith('/me/permissions'):
            return httpx.Response(200, json={'data':[{'permission':s,'status':'granted'} for s in permissions]})
        assert r.url.path.endswith('/me/accounts')
        return httpx.Response(200, json={'data':[{'id':'123','name':'Example Page','access_token':'page-a','instagram_business_account':{'id':'456','username':'example'}}]})
    grant = setup(tmp_path, handler).exchange('instagram', 'code', 'verifier')
    assert grant['accounts'][0]['id'] == '456'
    assert grant['accounts'][0]['page_id'] == '123'
    assert grant['accounts'][0]['access_token'] == 'page-a'


def test_provider_errors_never_expose_response_or_secret(tmp_path):
    p = setup(tmp_path, lambda r: httpx.Response(400, json={'error_description':'fictional-app-secret fictional-refresh'}))
    with pytest.raises(ValueError) as exc:
        p.exchange('youtube','code','verifier')
    assert 'fictional-' not in str(exc.value)
