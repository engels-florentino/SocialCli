"""Browser-paired account connections, scoped capabilities and server-side renewal."""
from __future__ import annotations

import hashlib
import asyncio
import hmac
import secrets
import re
import time
import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import httpx
from socialctl.connection_service.peer import client_ip
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
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


def browser_page(message: str, *, success=False, cancelled=False, platform=None):
    # The copy is fixed; no provider errors, tokens or account-controlled HTML.
    return HTMLResponse(result_page(message, success=success, cancelled=cancelled, platform=platform))


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
    # Enforce quarantine even if deployment configuration accidentally omits SaaS.
    # A raw legacy service must not release credentials from a mounted backup.
    with vault.read_transaction() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='saas_restore_guard'").fetchone():
            guard=db.execute('SELECT state FROM saas_restore_guard WHERE singleton=1').fetchone()
            if guard is None or guard['state']!='ready':
                raise ValueError('The restored database is quarantined; complete current-authority and identity cleanup before startup.')
    client = provider_client or httpx.Client(timeout=20, follow_redirects=False)
    providers = Providers(settings, client)
    from socialctl.connection_service.legacy import LegacyPolicy
    legacy = LegacyPolicy(vault,clock,close=settings.legacy_cutoff or bool(settings.saas and settings.saas.billing))
    landing_tokens = LandingTokens(settings.encryption_keys)
    auth_client = identity_client or (httpx.Client(timeout=15, follow_redirects=False) if settings.saas else None)

    stripe_client = billing_client or (httpx.Client(timeout=20, follow_redirects=False) if settings.saas and settings.saas.billing else None)

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup_media():
            while True:
                try:
                    await asyncio.to_thread(app.state.temporary_media.cleanup)
                except OSError:
                    logging.getLogger('socialcli.connection_service').error('temporary_media_cleanup_failed')
                await asyncio.sleep(1)
        janitor=asyncio.create_task(cleanup_media()) if hasattr(app.state,'temporary_media') else None
        try:
            yield
        finally:
            if janitor:
                janitor.cancel()
                from contextlib import suppress
                with suppress(asyncio.CancelledError):await janitor
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
            logging.getLogger('socialcli.connection_service').warning(
                'connection_browser_rejected status=%d', exc.status_code,
            )
            return HTMLResponse('' if request.method == 'HEAD' else error_page(exc.status_code),
                                status_code=exc.status_code, headers=exc.headers)
        return await http_exception_handler(request, exc)

    @app.middleware('http')
    async def secure_headers(request: Request, call_next):
        try:
            upload=(request.method=='PUT' and hasattr(app.state,'temporary_media') and re.fullmatch(r'/api/saas/v1/connections/[A-Za-z0-9_-]{1,256}/media/[A-Za-z0-9_-]{32}',request.url.path))
            limit=512*1024*1024 if upload else (1048576 if request.url.path in {'/api/saas/v1/billing/webhook','/api/saas/v1/meta/webhook'} else 16384)
            if int(request.headers.get('content-length', '0')) > limit:
                response = (HTMLResponse(error_page(413), status_code=413) if is_browser_path(request) else
                            JSONResponse({'detail':'request is too large'}, status_code=413))
            else:
                response = await call_next(request)
        except Exception as exc:
            # Never log exception messages, request URLs, headers or locals: they
            # may contain OAuth codes, single-use links or provider credentials.
            frames = traceback.extract_tb(exc.__traceback__)
            locations = ','.join(
                f'{Path(frame.filename).name}:{frame.lineno}'
                for frame in frames if '/socialctl/' in frame.filename
            )
            logging.getLogger('socialcli.connection_service').error(
                'connection_request_failed type=%s locations=%s',
                type(exc).__name__, locations or 'unavailable',
            )
            response = (HTMLResponse(error_page(503), status_code=503) if is_browser_path(request) else
                        JSONResponse({'detail':'connection service unavailable'}, status_code=503))
        response.headers.update({'Cache-Control':'no-store', 'Pragma':'no-cache',
                                 'X-Content-Type-Options':'nosniff'})
        # Native HTML form POSTs use Origin:null under no-referrer. Preserve
        # same-origin account submissions while withholding cross-origin referrers.
        response.headers.setdefault('Referrer-Policy',
            'same-origin' if settings.saas and request.url.path.startswith('/account/') else 'no-referrer')
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
        legacy.require(db,kind,record_id,record)
        return record

    @app.get('/agents', response_class=PlainTextResponse)
    def agent_instructions():
        return (Path(__file__).parent.parent / 'ui/assets/agents.md').read_text()

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
            legacy.allow_creation(db)
            vault.purge(db, now=clock())
            if not vault.admit(db, client_ip(request,settings), now=clock()):
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
        legacy.require(db,'authorization',authorization_id,record)
        if record['payload'].get('saas'):
            app.state.saas_connections.authorization(db,authorization_id)
        if record['payload']['status'] != 'pending' or 'state' in record['payload']:
            raise HTTPException(409, 'this authorization has already started; restart connect in SocialCli')
        return record

    @app.api_route('/connect/{authorization_id}', methods=['GET', 'HEAD'])
    def browser_landing(authorization_id: str, request: Request):
        with vault.transaction() as db:
            record = pending_authorization(db, authorization_id)
        if record['payload'].get('saas'):
            try:
                user = app.state.saas_sessions.require_user(request)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                from socialctl.saas.routes import connection_sign_in
                return connection_sign_in('/connect/' + authorization_id)
            if user.id != record['payload']['saas']['user_id']:
                raise HTTPException(403, 'This connection belongs to another account.')
        cookie_name = 'socialcli-form-' + authorization_id
        cookie = request.cookies.get(cookie_name, '')
        if not 16 <= len(cookie) <= 512:
            cookie = secrets.token_urlsafe(32)
        token = landing_tokens.issue(authorization_id, cookie, record['expires'])
        response = HTMLResponse('' if request.method == 'HEAD' else landing_page(
            authorization_id, token, platform=record['payload']['platform'],
            browser_selection=bool(record['payload'].get('saas'))))
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
        if request.headers.get('origin') != settings.public_url:
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
            values = parse_qs(body.decode('ascii'), strict_parsing=True, max_num_fields=5)
            if any(len(v)!=1 for v in values.values()):
                raise ValueError()
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
        from socialctl.saas import consent
        choice={k:v[0] for k,v in values.items()}
        consent.privacy_choice(choice)
        assistant_consent = None if owner_record['payload'].get('saas') else consent.assistant_choice(choice,clock())
        if owner_record['payload'].get('saas'):
            try:
                user = await asyncio.to_thread(app.state.saas_sessions.require_user, request)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                from socialctl.saas.routes import connection_sign_in
                return connection_sign_in('/connect/' + authorization_id)
            if user.id != owner_record['payload']['saas']['user_id']:
                raise HTTPException(403, 'This connection belongs to another account.')
        browser_secret = secrets.token_urlsafe(32)
        def consume_authorization():
            with vault.transaction() as db:
                record = pending_authorization(db, authorization_id)
                data = record['payload']
                data.update(privacy_consent={'version':consent.PRIVACY_VERSION,'accepted_at':clock(),'source':'owner_browser'},
                            assistant_consent=assistant_consent,
                            state=authorization_id+'.'+secrets.token_urlsafe(32),
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
            legacy.require(db,'authorization',authorization_id,record)
            data = record['payload']
            if (data['platform'] != platform or data['status'] != 'pending'
                    or not hmac.compare_digest(state,data.get('state',''))
                    or not cookie or not hmac.compare_digest(digest(cookie),data.get('browser_hash',''))):
                raise HTTPException(400, 'invalid authorization callback')
            if data.get('saas'):
                app.state.saas_connections.authorization(db,authorization_id)
            else:
                from socialctl.saas import consent
                consent.require_legacy(data)
            # Consume state before contacting the provider. An uncertain exchange
            # is never retried with the same authorization code.
            data.pop('state',None)
            data.pop('browser_hash',None)
            data['status'] = 'denied' if 'error' in query else 'exchanging'
            vault.put(db,'authorization',authorization_id,data,expires=record['expires'],credential_hash=record['credential_hash'])
        if data['status'] == 'denied':
            response = browser_page('Authorization was cancelled. No account was connected.', cancelled=True, platform=platform)
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
            revoked_during_exchange=False
            with vault.transaction() as db:
                current = vault.get(db,'authorization',authorization_id)
                if data.get('saas') and platform=='youtube' and data['status']=='ready':
                    owner_id=data['saas']['user_id']
                    revoked_during_exchange=bool(db.execute(
                        "SELECT 1 FROM saas_deletion_tombstones WHERE (kind='user' AND id=?) OR (kind='grant' AND id=?)",
                        (owner_id,authorization_id)).fetchone())
                    if revoked_during_exchange:
                        from socialctl.saas import revocation
                        revocation.enqueue(app.state.saas,db,authorization_id,
                            {'user_id':owner_id,'platform':platform,'grant':data['result']['grant']},clock())
                if not revoked_during_exchange and current is not None and current['expires'] > clock():
                    legacy.require(db,'authorization',authorization_id,current)
                    if current['payload'].get('saas'):
                        app.state.saas_connections.authorization(db,authorization_id)
                    vault.put(db,'authorization',authorization_id,data,expires=current['expires'],credential_hash=current['credential_hash'])
            if revoked_during_exchange:
                raise HTTPException(409,'connection_changed: This authorization was revoked while Google was responding. Cleanup is pending; no connection was restored.')
            response = browser_page('Authorization received. No content was published.' if data['status']=='ready'
                                    else 'Authorization could not be completed. Check permissions and app configuration.', success=data['status']=='ready', platform=platform)
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
                from socialctl.saas import consent
                consent.require_legacy(data)
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
            from socialctl.saas import consent
            consent.require_legacy(data)
            result = data['result']
            account = next((a for a in result['accounts'] if a['id']==body.account_id),None)
            if account is None:
                raise HTTPException(400,'account was not authorized')
            grant = result['grant']
            if account.get('access_token'):
                grant['access_token'] = account['access_token']
            connection_id, connection_secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
            payload = {'platform':data['platform'],'account':safe_account(account),'grant':grant,'activated':False,
                       'privacy_consent':data.get('privacy_consent'),'assistant_consent':data.get('assistant_consent')}
            expires=legacy.provisional(db,authorization_id,connection_id,clock()+SESSION_SECONDS)
            vault.put(db,'connection',connection_id,payload,expires=expires,credential_hash=digest(connection_secret))
            vault.delete(db,'authorization',authorization_id)
        return {'version':1,'connection_id':connection_id,'connection_secret':connection_secret,
                'platform':payload['platform'],'account':payload['account'],
                'granted_scopes':grant['granted_scopes'],'expires_at':grant['expires_at']}

    @app.get('/v1/connections/{connection_id}')
    def status(connection_id: str, request: Request):
        with vault.transaction() as db:
            record = checked(db,'connection',connection_id,request)
            data = record['payload']
            from socialctl.saas import consent
            consent.require_legacy(data)
            grant = data['grant']
        return {'version':1,'platform':data['platform'],'account':data['account'],
                'granted_scopes':grant['granted_scopes'],'expires_at':grant['expires_at'],
                'connection_expires_at':record['expires'],'needs_reconnect':grant['expires_at']<=clock() and data['platform'] not in {'youtube','tiktok'}}

    @app.post('/v1/connections/{connection_id}/token')
    def access_token(connection_id: str, request: Request, refresh: bool = True):
        with vault.transaction() as db:
            record = checked(db,'connection',connection_id,request)
            data = record['payload']
            from socialctl.saas import consent
            consent.require_legacy(data)
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
                legacy.activated(db,connection_id,clock()+CONNECTION_SECONDS)
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
        from socialctl.saas.meta_webhooks import install_meta_webhook_routes
        install_meta_webhook_routes(app,app.state.saas_connections,app.state.saas_sessions,settings,clock)
        from socialctl.saas.rotation import install_rotation_routes
        install_rotation_routes(app,app.state.saas,clock)
        app.state.saas_connections.legacy=legacy
        if settings.temporary_media_dir:
            from socialctl.saas.temporary_media import TemporaryMedia,install_media_routes
            install_media_routes(app,TemporaryMedia(app.state.saas,settings.temporary_media_dir,clock),app.state.saas_connections,settings.public_url)
        from socialctl.saas.migration import install_migration_routes
        install_migration_routes(app,settings,app.state.saas,app.state.saas_sessions,clock)
        if settings.saas.billing:
            from socialctl.saas.billing import BillingService
            from socialctl.saas.billing_routes import install_billing_routes
            from socialctl.saas.webhooks import install_webhook_route
            billing=BillingService(app.state.saas,settings.saas.billing,stripe_client,clock,settings.public_url)
            install_billing_routes(app,billing)
            install_webhook_route(app,billing)

    return app
