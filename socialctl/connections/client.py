"""Bounded HTTPS connection-service client; secrets never appear in diagnostics."""
from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit

from socialctl.connections import keychain

DEFAULT_SERVICE = 'https://social.florentino.pro'


def service_origin(value: str, *, allow_http_local=False) -> str:
    try:
        p=urlsplit(value)
        local=allow_http_local and p.hostname in {'localhost','127.0.0.1','::1'}
        if (not p.hostname or p.scheme not in ({'http','https'} if local else {'https'})
                or p.username or p.password or p.query or p.fragment or p.path not in {'','/'}
                or p.port is not None and not 1<=p.port<=65535):
            raise ValueError()
    except Exception:
        raise ValueError('connection service must use a fixed HTTPS origin') from None
    return value.rstrip('/')


def opaque(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',value):
        raise ValueError('invalid connection response')
    return value


def visible(value):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:256]


def account_id(brand,platform):
    key={'youtube':'channel_id','facebook':'page_id','instagram':'ig_user_id','tiktok':'open_id'}[platform.value]
    return (brand.cuentas.get(platform.value) or {}).get(key)


class BrokerClient:
    def __init__(self,service,client,*,allow_http_local=False):
        self.origin=service_origin(service,allow_http_local=allow_http_local)
        self.client=client

    def request(self,method,path,*,secret=None,body=None,allow_missing=False):
        if not re.fullmatch(r'/v1/(?:authorizations|connections)(?:/[A-Za-z0-9_-]{1,256}(?:/(?:poll|complete|token))?)?(?:\?refresh=false)?',path):
            raise ValueError('invalid connection endpoint')
        headers={'Authorization':f'Bearer {secret}'} if secret else {}
        try:
            with self.client.stream(method,self.origin+path,headers=headers,json=body,
                                    timeout=30,follow_redirects=False) as response:
                if response.status_code==404 and allow_missing:
                    return {'version':1,'status':'disconnected'}
                if response.status_code in {404,409}:
                    raise ValueError('connection expired or unavailable; reconnect your account')
                if response.status_code==429:
                    raise ValueError('too many connection attempts; try again later')
                if response.status_code==503:
                    raise ValueError('connection service or platform is not configured yet')
                if not response.is_success:
                    raise ValueError('connection request was rejected')
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>65536:
                        raise ValueError('invalid connection response')
                result=json.loads(raw)
                if not isinstance(result,dict) or result.get('version')!=1:
                    raise ValueError('invalid connection response')
                return result
        except ValueError:
            raise
        except Exception:
            raise ValueError('could not reach the connection service securely') from None


def broker_access_token(brand,platform,client,*,refresh=True):
    metadata=brand.leer_secreto(platform)
    if metadata.get('auth_mode')!='broker' or metadata.get('version')!=1:
        raise ValueError('invalid connection metadata')
    broker=BrokerClient(metadata.get('service_url',''),client,allow_http_local=metadata.get('development_local') is True)
    connection_id=opaque(metadata.get('connection_id'))
    expected=opaque(metadata.get('account_id'))
    if account_id(brand,platform)!=expected:
        raise ValueError('brand account differs from its connection; reconnect explicitly')
    secret=keychain.get(brand,platform,metadata)
    if not isinstance(secret,str) or not 16<=len(secret)<=512:
        raise ValueError('connection credential unavailable in the selected secure credential store')
    result=broker.request('POST',f'/v1/connections/{connection_id}/token'+('' if refresh else '?refresh=false'),secret=secret)
    if result.get('platform')!=platform.value or result.get('account',{}).get('id')!=expected:
        raise ValueError('connection account mismatch; reconnect explicitly')
    expiry=result.get('expires_at')
    if type(expiry) not in {int,float} or not math.isfinite(expiry) or expiry<=0:
        raise ValueError('invalid connection expiration')
    token=result.get('access_token')
    if not isinstance(token,str) or not 1<=len(token)<=16384 or any(ord(c)<33 or ord(c)==127 for c in token):
        raise ValueError('invalid connection credentials')
    return token
