"""Browser-paired account connections, scoped capabilities and server-side renewal."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from socialctl.connection_service.providers import Providers
from socialctl.connection_service.settings import Settings
from socialctl.connection_service.store import Store

SESSION_SECONDS = 600
CONNECTION_SECONDS = 90 * 86400


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bearer(request: Request) -> str:
    value = request.headers.get('authorization', '')
    if not value.startswith('Bearer ') or not 16 <= len(value[7:]) <= 512:
        # Polling secrets from older clients are not supported; capabilities
        # are generated using token_urlsafe(32) by the official CLI.
        raise HTTPException(404, 'connection or authorization not found')
    return digest(value[7:])


def safe_account(account: dict) -> dict:
    return {key: account[key] for key in ('id', 'name', 'page_id') if key in account}


def browser_page(message: str, *, success=False):
    # The copy is fixed; no provider errors, tokens or account-controlled HTML.
    return HTMLResponse('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SocialCli connection</title><body><main><h1>SocialCli</h1><p>' + message + '</p><p>Return to SocialCli to ' + ('choose and confirm your account.' if success else 'try again or cancel.') + '</p><p><a href="/privacy.html">Privacy</a> · <a href="/terms.html">Terms</a></p></main></body></html>')


class StartAuthorization(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platform: str = Field(pattern='^(youtube|facebook|instagram|tiktok)$')
    poll_challenge: str = Field(pattern='^[0-9a-f]{64}$')
    management: bool = False
    analytics: bool = False


class CompleteAuthorization(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str = Field(min_length=1, max_length=256, pattern='^[A-Za-z0-9_-]+$')


def create_app(settings: Settings, *, store: Store | None = None,
               provider_client: httpx.Client | None = None, clock=time.time) -> FastAPI:
    vault = store or Store(settings.database, settings.encryption_keys)
    client = provider_client or httpx.Client(timeout=20, follow_redirects=False)
    providers = Providers(settings, client)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if provider_client is None:
            client.close()

    app = FastAPI(title='SocialCli connection service', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.public_url).hostname])

    @app.middleware('http')
    async def secure_headers(request: Request, call_next):
        try:
            if int(request.headers.get('content-length', '0')) > 16384:
                response = JSONResponse({'detail':'request is too large'}, status_code=413)
            else:
                response = await call_next(request)
        except Exception:
            response = JSONResponse({'detail':'connection service unavailable'}, status_code=503)
        response.headers.update({'Cache-Control':'no-store', 'Pragma':'no-cache',
                                 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff',
                                 'Content-Security-Policy':"default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    def checked(db, kind, record_id, request):
        record = vault.get(db, kind, record_id)
        proof = bearer(request)
        if record is None or record['expires'] <= clock() or not hmac.compare_digest(proof, record['credential_hash']):
            raise HTTPException(404, 'connection or authorization not found')
        return record

    @app.get('/healthz')
    def health():
        return {'service':'socialcli-connections', 'version':1,
                'configured_platforms': sorted(settings.providers), 'production_approval_verified':False}

    @app.post('/v1/authorizations', status_code=201)
    def start(body: StartAuthorization, request: Request):
        try:
            providers.scopes(body.platform, management=body.management, analytics=body.analytics)
        except ValueError:
            raise HTTPException(400, 'requested optional permissions are not supported for this platform') from None
        if body.platform not in settings.providers:
            raise HTTPException(503, 'this platform is not configured; use independent-app auth or contact the operator')
        record_id = secrets.token_urlsafe(24)
        data = {'platform':body.platform, 'status':'pending', 'management':body.management,
                'analytics':body.analytics}
        with vault.transaction() as db:
            vault.purge(db, now=clock())
            if not vault.admit(db, request.client.host if request.client else 'unknown', now=clock()):
                raise HTTPException(429, 'too many connection attempts; try again later', headers={'Retry-After':'60'})
            count = db.execute("SELECT count(*) FROM records WHERE kind='authorization'").fetchone()[0]
            if count >= 1000:
                raise HTTPException(503, 'connection capacity reached; try again later')
            vault.put(db, 'authorization', record_id, data, expires=clock()+SESSION_SECONDS,
                      credential_hash=body.poll_challenge)
        return {'version':1, 'authorization_id':record_id, 'browser_url':f'{settings.public_url}/connect/{record_id}',
                'expires_in':SESSION_SECONDS, 'poll_interval':2}

    @app.get('/connect/{authorization_id}')
    def browser_start(authorization_id: str):
        browser_secret = secrets.token_urlsafe(32)
        with vault.transaction() as db:
            record = vault.get(db, 'authorization', authorization_id)
            if record is None or record['expires'] <= clock():
                raise HTTPException(404, 'authorization not found')
            data = record['payload']
            if data['status'] != 'pending' or 'state' in data:
                raise HTTPException(409, 'this authorization has already started; restart from SocialCli')
            data.update(state=authorization_id+'.'+secrets.token_urlsafe(32),
                        browser_hash=digest(browser_secret), verifier=secrets.token_urlsafe(64))
            vault.put(db,'authorization',authorization_id,data,expires=record['expires'],credential_hash=record['credential_hash'])
            url = providers.authorization_url(data['platform'], data['state'], data['verifier'],
                                              management=data['management'], analytics=data['analytics'])
        response = RedirectResponse(url, status_code=303)
        response.set_cookie('socialcli-'+authorization_id,browser_secret,httponly=True,
                            secure=not settings.allow_http_local,samesite='lax',max_age=SESSION_SECONDS,path='/')
        return response

    @app.get('/oauth/{platform}/callback')
    def callback(platform: str, request: Request):
        query = request.query_params
        state = query.get('state', '')
        if not 16 <= len(state) <= 256 or '.' not in state:
            raise HTTPException(400, 'invalid authorization callback')
        authorization_id = state.split('.',1)[0]
        cookie = request.cookies.get('socialcli-'+authorization_id,'')
        with vault.transaction() as db:
            record = vault.get(db,'authorization',authorization_id)
            if record is None or record['expires'] <= clock():
                raise HTTPException(400, 'invalid authorization callback')
            data = record['payload']
            if (data['platform'] != platform or data['status'] != 'pending'
                    or not hmac.compare_digest(state,data.get('state',''))
                    or not cookie or not hmac.compare_digest(digest(cookie),data.get('browser_hash',''))):
                raise HTTPException(400, 'invalid authorization callback')
            # Consume state before contacting the provider. An uncertain exchange
            # is never retried with the same authorization code.
            data.pop('state',None)
            data.pop('browser_hash',None)
            data['status'] = 'denied' if 'error' in query else 'exchanging'
            vault.put(db,'authorization',authorization_id,data,expires=record['expires'],credential_hash=record['credential_hash'])
        if data['status'] == 'denied':
            response = browser_page('Authorization was cancelled. No account was connected.')
        else:
            code = query.get('code','')
            try:
                if not code or len(code)>4096:
                    raise ValueError()
                grant = providers.exchange(platform,code,data['verifier'],management=data['management'],analytics=data['analytics'])
                data['result'], data['status'] = grant, 'ready'
            except Exception:
                data['status'] = 'failed'
            data.pop('verifier',None)
            with vault.transaction() as db:
                current = vault.get(db,'authorization',authorization_id)
                if current is not None and current['expires'] > clock():
                    vault.put(db,'authorization',authorization_id,data,expires=current['expires'],credential_hash=current['credential_hash'])
            response = browser_page('Authorization received. No content was published.' if data['status']=='ready'
                                    else 'Authorization could not be completed. Check permissions and app configuration.', success=data['status']=='ready')
        response.delete_cookie('socialcli-'+authorization_id,path='/',secure=not settings.allow_http_local,httponly=True,samesite='lax')
        return response

    @app.post('/v1/authorizations/{authorization_id}/poll')
    def poll(authorization_id: str, request: Request):
        with vault.transaction() as db:
            data = checked(db,'authorization',authorization_id,request)['payload']
            reply = {'version':1,'status':data['status']}
            if data['status']=='ready':
                reply['accounts'] = [safe_account(a) for a in data['result']['accounts']]
            return reply

    @app.delete('/v1/authorizations/{authorization_id}')
    def cancel(authorization_id: str, request: Request):
        with vault.transaction() as db:
            checked(db,'authorization',authorization_id,request)
            vault.delete(db,'authorization',authorization_id)
        return {'version':1,'status':'cancelled'}

    @app.post('/v1/authorizations/{authorization_id}/complete', status_code=201)
    def finish(authorization_id: str, body: CompleteAuthorization, request: Request):
        with vault.transaction() as db:
            record = checked(db,'authorization',authorization_id,request)
            data = record['payload']
            if data['status'] != 'ready':
                raise HTTPException(409,'authorization is not ready')
            result = data['result']
            account = next((a for a in result['accounts'] if a['id']==body.account_id),None)
            if account is None:
                raise HTTPException(400,'account was not authorized')
            grant = result['grant']
            if account.get('access_token'):
                grant['access_token'] = account['access_token']
            connection_id, connection_secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
            payload = {'platform':data['platform'],'account':safe_account(account),'grant':grant,'activated':False}
            vault.put(db,'connection',connection_id,payload,expires=clock()+SESSION_SECONDS,credential_hash=digest(connection_secret))
            vault.delete(db,'authorization',authorization_id)
        return {'version':1,'connection_id':connection_id,'connection_secret':connection_secret,
                'platform':payload['platform'],'account':payload['account'],
                'granted_scopes':grant['granted_scopes'],'expires_at':grant['expires_at']}

    @app.get('/v1/connections/{connection_id}')
    def status(connection_id: str, request: Request):
        with vault.transaction() as db:
            record = checked(db,'connection',connection_id,request)
            data = record['payload']
            grant = data['grant']
        return {'version':1,'platform':data['platform'],'account':data['account'],
                'granted_scopes':grant['granted_scopes'],'expires_at':grant['expires_at'],
                'connection_expires_at':record['expires'],'needs_reconnect':grant['expires_at']<=clock() and data['platform'] not in {'youtube','tiktok'}}

    @app.post('/v1/connections/{connection_id}/token')
    def access_token(connection_id: str, request: Request, refresh: bool = True):
        with vault.transaction() as db:
            record = checked(db,'connection',connection_id,request)
            data = record['payload']
            grant = data['grant']
            if data['platform'] not in settings.providers:
                raise HTTPException(503,'platform temporarily disabled')
            if grant['expires_at'] <= clock()+60:
                if not refresh:
                    raise HTTPException(409,'provider token expired; run an explicitly authorized operation to renew, or reconnect')
                try:
                    grant = providers.refresh(data['platform'],grant)
                except ValueError:
                    raise HTTPException(409,'authorization could not be renewed; reconnect your account') from None
                data['grant'] = grant
                vault.put(db,'connection',connection_id,data,expires=record['expires'],credential_hash=record['credential_hash'])
            if not data.get('activated') and refresh:
                data['activated'] = True
                vault.put(db,'connection',connection_id,data,expires=clock()+CONNECTION_SECONDS,credential_hash=record['credential_hash'])
        return {'version':1,'access_token':grant['access_token'],'expires_at':grant['expires_at'],
                'granted_scopes':grant['granted_scopes'],'platform':data['platform'],'account':data['account']}

    @app.delete('/v1/connections/{connection_id}')
    def disconnect(connection_id: str, request: Request):
        with vault.transaction() as db:
            checked(db,'connection',connection_id,request)
            vault.delete(db,'connection',connection_id)
        return {'version':1,'status':'disconnected','provider_authorization_revoked':False}

    return app
