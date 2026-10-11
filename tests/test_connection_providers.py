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


def meta_empty_pages_handler(platform='facebook', debug=None, page=None):
    permissions = ['pages_show_list', 'pages_read_engagement'] + (
        ['pages_manage_posts'] if platform == 'facebook' else ['instagram_basic', 'instagram_content_publish'])
    metadata = {'is_valid': True, 'app_id': 'fictional-client', 'type': 'USER',
                'scopes': permissions, 'granular_scopes': [
                    {'scope': scope, 'target_ids': ['123']} for scope in permissions if scope.startswith('pages_')]}
    if debug is not None:
        metadata.update(debug)
    row = page or {'id': '123', 'name': 'Example Page', 'access_token': 'page-a',
                   'instagram_business_account': {'id': '456', 'username': 'example'}}

    def handler(request):
        path = request.url.path
        if path.endswith('/oauth/access_token'):
            return httpx.Response(200, json={'access_token': 'user-a', 'expires_in': 5184000})
        if path.endswith('/me/permissions'):
            return httpx.Response(200, json={'data': [{'permission': scope, 'status': 'granted'} for scope in permissions]})
        if path.endswith('/me/accounts'):
            return httpx.Response(200, json={'data': []})
        if path.endswith('/debug_token'):
            assert request.url.params['input_token'] == 'user-a'
            assert request.headers['authorization'] == 'Bearer fictional-client|fictional-app-secret'
            return httpx.Response(200, json={'data': metadata})
        assert path == '/v26.0/123', 'must only retrieve the explicitly authorized Page'
        assert request.headers['authorization'] == 'Bearer user-a'
        if platform == 'facebook':
            assert request.url.params['fields'] == 'id,name,access_token'
        return httpx.Response(200, json=row)

    return handler


@pytest.mark.parametrize('platform, expected_id', [('facebook', '123'), ('instagram', '456')])
def test_meta_discovers_explicitly_authorized_page_when_accounts_edge_is_empty(tmp_path, platform, expected_id):
    grant = setup(tmp_path, meta_empty_pages_handler(platform)).exchange(platform, 'code', 'verifier')
    assert grant['accounts'][0]['id'] == expected_id
    assert grant['accounts'][0]['page_id'] == '123'
    assert grant['accounts'][0]['access_token'] == 'page-a'


@pytest.mark.parametrize('debug', [
    {'is_valid': False}, {'app_id': 'another-app'}, {'type': 'APP'},
    {'granular_scopes': []},
    {'granular_scopes': [{'scope': 'pages_show_list', 'target_ids': ['123']}]},
    {'granular_scopes': [
        {'scope': 'pages_show_list', 'target_ids': ['123']},
        {'scope': 'pages_read_engagement', 'target_ids': ['999']},
        {'scope': 'pages_manage_posts', 'target_ids': ['123']}]},
    {'granular_scopes': [
        {'scope': scope, 'target_ids': ['../me']} for scope in ['pages_show_list', 'pages_read_engagement', 'pages_manage_posts']]},
    {'granular_scopes': [
        {'scope': scope, 'target_ids': [str(i) for i in range(101)]} for scope in ['pages_show_list', 'pages_read_engagement', 'pages_manage_posts']]},
])
def test_meta_empty_accounts_cannot_fall_back_to_unverified_or_unbounded_targets(tmp_path, debug):
    with pytest.raises(ValueError):
        setup(tmp_path, meta_empty_pages_handler(debug=debug)).exchange('facebook', 'code', 'verifier')


def test_meta_rejects_different_page_returned_for_authorized_target(tmp_path):
    with pytest.raises(ValueError):
        setup(tmp_path, meta_empty_pages_handler(page={'id': '999', 'name': 'Wrong Page', 'access_token': 'wrong-a'})).exchange('facebook', 'code', 'verifier')


@pytest.mark.parametrize('first_empty', [True, False])
def test_meta_pagination_never_uses_empty_edge_fallback(tmp_path, first_empty):
    permissions = ['pages_show_list', 'pages_read_engagement', 'pages_manage_posts']
    row = {'id': '123', 'name': 'Example Page', 'access_token': 'page-a'}

    def handler(request):
        if request.url.path.endswith('/oauth/access_token'):
            return httpx.Response(200, json={'access_token': 'user-a', 'expires_in': 5184000})
        if request.url.path.endswith('/me/permissions'):
            return httpx.Response(200, json={'data': [{'permission': scope, 'status': 'granted'} for scope in permissions]})
        assert request.url.path.endswith('/me/accounts'), 'paginated discovery must not use debug-token fallback'
        if 'after' not in request.url.params:
            return httpx.Response(200, json={'data': [] if first_empty else [row],
                'paging': {'next': 'https://graph.facebook.com/next', 'cursors': {'after': 'next-page'}}})
        return httpx.Response(200, json={'data': [row] if first_empty else []})

    accounts = setup(tmp_path, handler).exchange('facebook', 'code', 'verifier')['accounts']
    assert accounts == [{'id': '123', 'name': 'Example Page', 'page_id': '123', 'access_token': 'page-a'}]

@pytest.mark.parametrize('platform,base,extra', [
    ('facebook', {'pages_show_list','pages_read_engagement','pages_manage_posts'}, 'read_insights'),
    ('instagram', {'pages_show_list','pages_read_engagement','instagram_basic','instagram_content_publish'}, 'instagram_manage_insights'),
])
def test_meta_analytics_consent_requests_only_its_read_permission(tmp_path, platform, base, extra):
    p = setup(tmp_path, lambda r: httpx.Response(500))
    ordinary = parse_qs(urlsplit(p.authorization_url(platform, 'state', 'verifier')).query)
    analytics = parse_qs(urlsplit(p.authorization_url(platform, 'state', 'verifier', analytics=True)).query)
    assert set(ordinary['scope'][0].split(',')) == base
    assert set(analytics['scope'][0].split(',')) == base | {extra}


def test_tiktok_rejects_analytics_and_management(tmp_path):
    p = setup(tmp_path, lambda r: httpx.Response(500))
    for platform, options in [('tiktok', {'analytics':True}), ('tiktok', {'management':True})]:
        with pytest.raises(ValueError):
            p.authorization_url(platform, 'state', 'verifier', **options)


@pytest.mark.parametrize('platform,management,analytics', [
    ('facebook', {'pages_read_user_content', 'pages_manage_engagement', 'pages_manage_metadata'}, 'read_insights'),
    ('instagram', {'instagram_manage_comments', 'pages_manage_metadata'}, 'instagram_manage_insights'),
])
def test_meta_management_consent_is_optional_and_combines_with_analytics(tmp_path, platform, management, analytics):
    provider = setup(tmp_path, lambda r: httpx.Response(500))
    basic = set(provider.scopes(platform))
    url = provider.authorization_url(platform, 'state', 'verifier', management=True, analytics=True)
    query = parse_qs(urlsplit(url).query)
    assert set(query['scope'][0].split(',')) == basic | management | {analytics}
    assert basic.isdisjoint(management)
    assert query['auth_type'] == ['rerequest']


@pytest.mark.parametrize('platform', ['facebook', 'instagram'])
@pytest.mark.parametrize('missing', [False, True])
def test_meta_management_exchange_validates_requested_permissions(tmp_path, platform, missing):
    provider = setup(tmp_path, lambda r: httpx.Response(500))
    required = provider.scopes(platform, management=True, analytics=True)
    missing_scope = 'pages_manage_engagement' if platform == 'facebook' else 'instagram_manage_comments'
    granted = [scope for scope in required if not missing or scope != missing_scope]
    def handler(request):
        if request.url.path.endswith('/oauth/access_token'):
            return httpx.Response(200, json={'access_token':'fictional-user', 'expires_in':3600})
        if request.url.path.endswith('/me/permissions'):
            return httpx.Response(200, json={'data':[{'permission':s, 'status':'granted'} for s in granted]})
        assert not missing, 'incomplete grants must be rejected before account discovery'
        return httpx.Response(200, json={'data':[{'id':'123', 'name':'Example', 'access_token':'fictional-page',
            'instagram_business_account':{'id':'456', 'username':'example'}}]})
    provider = setup(tmp_path, handler)
    if missing:
        with pytest.raises(ValueError, match='required permissions were not granted'):
            provider.exchange(platform, 'code', 'verifier', management=True, analytics=True)
    else:
        result = provider.exchange(platform, 'code', 'verifier', management=True, analytics=True)
        assert set(result['grant']['granted_scopes']) == set(required)
        assert result['accounts'][0]['id'] == ('123' if platform == 'facebook' else '456')
