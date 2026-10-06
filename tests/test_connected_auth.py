"""Shared auth must feed real adapter token consumers without writing tokens to disk."""
import importlib.util

import httpx
import pytest

from socialctl.auth import AuthError, obtener_token
from socialctl.brands import crear_brand
from socialctl.models import Platform


def test_obtener_token_uses_connection_broker_without_local_client_secret(tmp_path,monkeypatch):
    brand=crear_brand(tmp_path,'Example')
    brand.cuentas['youtube']['channel_id']='UC-one'
    secret={'auth_mode':'broker','version':1,'service_url':'https://social.example','connection_id':'connection-one','account_id':'UC-one'}
    brand.guardar_secreto(Platform.YOUTUBE,secret)
    assert importlib.util.find_spec('socialctl.connections.keychain') is not None, 'secure connection keychain is missing'
    from socialctl.connections import keychain
    monkeypatch.setattr(keychain,'get',lambda *a:'connection-secret-123456789012345')
    def handler(r):
        assert r.url == 'https://social.example/v1/connections/connection-one/token'
        assert r.headers['authorization']=='Bearer connection-secret-123456789012345'
        return httpx.Response(200,json={'version':1,'access_token':'provider-access','expires_at':9999999999,'granted_scopes':['scope'],'platform':'youtube','account':{'id':'UC-one','name':'Example'}})
    assert obtener_token(brand,Platform.YOUTUBE,httpx.Client(transport=httpx.MockTransport(handler)))=='provider-access'
    assert brand.leer_secreto(Platform.YOUTUBE)==secret


def test_broker_rejects_account_mismatch_and_unsafe_origin(tmp_path,monkeypatch):
    assert importlib.util.find_spec('socialctl.connections.keychain') is not None, 'secure connection keychain is missing'
    from socialctl.connections import keychain
    monkeypatch.setattr(keychain,'get',lambda *a:'connection-secret-123456789012345')
    brand=crear_brand(tmp_path,'Example')
    brand.cuentas['youtube']['channel_id']='UC-one'
    secret={'auth_mode':'broker','version':1,'service_url':'http://social.example','connection_id':'connection-one','account_id':'UC-one'}
    brand.guardar_secreto(Platform.YOUTUBE,secret)
    requests=[]
    def handler(r):
        requests.append(r)
        return httpx.Response(200,json={'version':1,'access_token':'a','expires_at':9999999999,'granted_scopes':[],'platform':'youtube','account':{'id':'UC-other'}})
    with pytest.raises(AuthError):
        obtener_token(brand,Platform.YOUTUBE,httpx.Client(transport=httpx.MockTransport(handler)))
    assert not requests
    secret['service_url']='https://social.example'
    brand.guardar_secreto(Platform.YOUTUBE,secret)
    with pytest.raises(AuthError):
        obtener_token(brand,Platform.YOUTUBE,httpx.Client(transport=httpx.MockTransport(handler)))


def test_connection_missing_keyring_never_falls_back_to_legacy_token(tmp_path,monkeypatch):
    assert importlib.util.find_spec('socialctl.connections.keychain') is not None, 'secure connection keychain is missing'
    from socialctl.connections import keychain
    monkeypatch.setattr(keychain,'get',lambda *a:None)
    brand=crear_brand(tmp_path,'Example')
    brand.cuentas['youtube']['channel_id']='UC-one'
    brand.guardar_secreto(Platform.YOUTUBE,{'auth_mode':'broker','version':1,'service_url':'https://social.example','connection_id':'connection-one','account_id':'UC-one','access_token':'legacy-value'})
    with pytest.raises(AuthError):
        obtener_token(brand,Platform.YOUTUBE,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(500))))


@pytest.mark.parametrize('consumer',['identity','analytics','resources','meta'])
def test_all_credential_consumers_support_broker_connections(tmp_path,monkeypatch,consumer):
    from socialctl.connections import keychain
    monkeypatch.setattr(keychain,'get',lambda *a:'connection-secret-123456789012345')
    brand=crear_brand(tmp_path,'Example')
    platform=Platform.FACEBOOK if consumer=='meta' else Platform.YOUTUBE
    brand.cuentas['youtube']['channel_id']='UC-one'
    brand.cuentas['facebook']['page_id']='123'
    account='123' if consumer=='meta' else 'UC-one'
    brand.guardar_secreto(platform,{'auth_mode':'broker','version':1,'service_url':'https://social.example','connection_id':'connection-one','account_id':account})
    def handler(r):
        assert r.url.host=='social.example'
        return httpx.Response(200,json={'version':1,'access_token':'provider-access','expires_at':9999999999,'granted_scopes':[],'platform':platform.value,'account':{'id':account}})
    transport=httpx.Client(transport=httpx.MockTransport(handler))
    if consumer=='identity':
        from socialctl.identity import IdentityClient
        assert IdentityClient(brand,platform,transport)._token=='provider-access'
    elif consumer=='analytics':
        from socialctl.metricas.analytics_client import AnalyticsClient
        assert AnalyticsClient(brand,transport)._token()=='provider-access'
    elif consumer=='resources':
        from socialctl.management.youtube_resources import YouTubeResourcesClient
        assert YouTubeResourcesClient(brand,transport)._token()=='provider-access'
    else:
        from socialctl.management.meta_client import MetaClient
        assert MetaClient(brand,platform,transport)._token=='provider-access'
