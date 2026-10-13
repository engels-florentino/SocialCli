"""Browser-paired account connections, scoped capabilities and server-side renewal."""
from __future__ import annotations

import hashlib
import asyncio
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from socialctl.connection_service.landing import LandingTokens, landing_page, result_page, error_page
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


def browser_page(message: str, *, success=False, cancelled=False):
    # The copy is fixed; no provider errors, tokens or account-controlled HTML.
    return HTMLResponse(result_page(message, success=success, cancelled=cancelled))


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
               provider_client: httpx.Client | None = None, identity_client: httpx.Client | None = None, billing_client: httpx.Client | None = None, clock=time.time) -> FastAPI:
    vault = store or Store(settings.database, settings.encryption_keys)
    client = provider_client or httpx.Client(timeout=20, follow_redirects=False)
    providers = Providers(settings, client)
    landing_tokens = LandingTokens(settings.encryption_keys)
    auth_client = identity_client or (httpx.Client(timeout=15, follow_redirects=False) if settings.saas else None)

    stripe_client = billing_client or (httpx.Client(timeout=20, follow_redirects=False) if settings.saas and settings.saas.billing else None)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if identity_client is None and auth_client is not None:
            auth_client.close()
        if provider_client is None:
            client.close()
        if billing_client is None and stripe_client is not None:
            stripe_client.close()

    app = FastAPI(title='SocialCli connection service', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.public_url).hostname])
    app.mount('/connect/assets', StaticFiles(directory=Path(__file__).parent / 'assets'), name='connection-assets')

    def is_browser_path(request):
        return request.url.path.startswith(('/connect/','/oauth/','/account/'))

    @app.exception_handler(StarletteHTTPException)
    async def connection_error(request, exc):
        if settings.saas and request.url.path.startswith('/account/'):
            from socialctl.saas.pages import error
            return HTMLResponse('' if request.method == 'HEAD' else error(str(exc.detail)),status_code=exc.status_code,headers=exc.headers)
        if is_browser_path(request):
            return HTMLResponse('' if request.method == 'HEAD' else error_page(exc.status_code),
                                status_code=exc.status_code, headers=exc.headers)
        return await http_exception_handler(request, exc)

    @app.middleware('http')
    async def secure_headers(request: Request, call_next):
        try:
            if int(request.headers.get('content-length', '0')) > (1048576 if request.url.path=='/api/saas/v1/billing/webhook' else 16384):
                response = (HTMLResponse(error_page(413), status_code=413) if is_browser_path(request) else
                            JSONResponse({'detail':'request is too large'}, status_code=413))
            else:
                response = await call_next(request)
        except Exception:
            response = (HTMLResponse(error_page(503), status_code=503) if is_browser_path(request) else
                        JSONResponse({'detail':'connection service unavailable'}, status_code=503))
        response.headers.update({'Cache-Control':'no-store', 'Pragma':'no-cache',
                                 'X-Content-Type-Options':'nosniff'})
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        response.headers.setdefault('Content-Security-Policy',
            "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        return response

    def checked(db, kind, record_id, request):
        record = vault.get(db, kind, record_id)
        if record and record['payload'].get('saas'):
            raise HTTPException(404, 'connection or authorization not found')
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

    def pending_authorization(db, authorization_id):
        record = vault.get(db, 'authorization', authorization_id)
        if record is None or record['expires'] <= clock():
            raise HTTPException(404, 'authorization expired or unavailable; restart connect in SocialCli')
        if record['payload']['status'] != 'pending' or 'state' in record['payload']:
            raise HTTPException(409, 'this authorization has already started; restart connect in SocialCli')
        return record

    @app.api_route('/connect/{authorization_id}', methods=['GET', 'HEAD'])
    def browser_landing(authorization_id: str, request: Request):
        with vault.transaction() as db:
            record = pending_authorization(db, authorization_id)
        cookie_name = 'socialcli-form-' + authorization_id
        cookie = request.cookies.get(cookie_name, '')
        if not 16 <= len(cookie) <= 512:
            cookie = secrets.token_urlsafe(32)
        token = landing_tokens.issue(authorization_id, cookie, record['expires'])
        response = HTMLResponse('' if request.method == 'HEAD' else landing_page(authorization_id, token, platform=record['payload']['platform']))
        response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        response.headers['Referrer-Policy'] = 'same-origin'
        provider_origin = {'youtube':'https://accounts.google.com', 'facebook':'https://www.facebook.com',
                           'instagram':'https://www.facebook.com', 'tiktok':'https://www.tiktok.com'}[record['payload']['platform']]
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self' " + provider_origin
            + "; frame-ancestors 'none'; base-uri 'none'")
        response.set_cookie(cookie_name, cookie, httponly=True, secure=not settings.allow_http_local,
                            samesite='lax', max_age=SESSION_SECONDS, path='/connect/' + authorization_id)
        return response

    @app.post('/connect/{authorization_id}')
    async def browser_start(authorization_id: str, request: Request):
        if request.headers.get('origin', settings.public_url) != settings.public_url:
            raise HTTPException(403, 'invalid connection form; reload the login page')
        if request.headers.get('content-type', '').split(';')[0].strip() != 'application/x-www-form-urlencoded':
            raise HTTPException(403, 'invalid connection form; reload the login page')
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 8192:
                        raise HTTPException(413, 'connection form is too large')
            values = parse_qs(body.decode('ascii'), strict_parsing=True, max_num_fields=2)
            tokens = values.get('form_token', [])
        except (ValueError, UnicodeError, TimeoutError):
            raise HTTPException(403, 'invalid connection form; reload the login page') from None
        cookie_name = 'socialcli-form-' + authorization_id
        if (len(tokens) != 1 or not landing_tokens.valid(tokens[0], authorization_id,
                request.cookies.get(cookie_name, ''), clock())):
            raise HTTPException(403, 'invalid or expired connection form; restart connect in SocialCli')
        def read_owner_record():
            with vault.transaction() as db:
                return pending_authorization(db, authorization_id)
        owner_record = await asyncio.to_thread(read_owner_record)
        if owner_record['payload'].get('saas'):
            user = await asyncio.to_thread(app.state.saas_sessions.require_user, request)
            if user.id != owner_record['payload']['saas']['user_id']:
                raise HTTPException(403, 'This connection belongs to another account.')
        browser_secret = secrets.token_urlsafe(32)
        def consume_authorization():
            with vault.transaction() as db:
                record = pending_authorization(db, authorization_id)
                data = record['payload']
                data.update(state=authorization_id+'.'+secrets.token_urlsafe(32),
                            browser_hash=digest(browser_secret), verifier=secrets.token_urlsafe(64))
                vault.put(db,'authorization',authorization_id,data,expires=record['expires'],credential_hash=record['credential_hash'])
                url = providers.authorization_url(data['platform'], data['state'], data['verifier'],
                                                  management=data['management'], analytics=data['analytics'])
            return url
        url = await asyncio.to_thread(consume_authorization)
        response = RedirectResponse(url, status_code=303)
        response.delete_cookie(cookie_name, path='/connect/' + authorization_id,
                               secure=not settings.allow_http_local, httponly=True, samesite='lax')
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
            response = browser_page('Authorization was cancelled. No account was connected.', cancelled=True)
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
        if data.get('saas') and data['status']=='ready':
            response = RedirectResponse('/account/connect/'+authorization_id,status_code=303)
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

    if settings.saas:
        from socialctl.saas.routes import install_account_routes
        install_account_routes(app, settings, vault, auth_client, clock)
        from socialctl.saas.connections import install_connection_routes
        install_connection_routes(app,settings,app.state.saas,app.state.saas_sessions,providers,clock)
        if settings.saas.billing:
            from socialctl.saas.billing import BillingService
            from socialctl.saas.billing_routes import install_billing_routes
            from socialctl.saas.webhooks import install_webhook_route
            billing=BillingService(app.state.saas,settings.saas.billing,stripe_client,clock,settings.public_url)
            install_billing_routes(app,billing)
            install_webhook_route(app,billing)

    return app
