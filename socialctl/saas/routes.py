"""Browser-bound email sign-in with local session revocation and bounded forms."""
import asyncio
import hmac
import re
import secrets
from urllib.parse import parse_qs
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from socialctl.connection_service.landing import LandingTokens
from socialctl.saas.auth import SupabaseAuth, WebSessions, IdentityError, IdentityUnavailable, digest
from socialctl.saas.store import SaaSStore
from socialctl.saas import pages

PREAUTH='socialcli-browser'


async def read_form(request,origin):
    if request.headers.get('origin')!=origin:
        raise HTTPException(403,'Reload the page and try again.')
    if request.headers.get('content-type','').split(';')[0]!='application/x-www-form-urlencoded':
        raise HTTPException(403,'Reload the page and try again.')
    content=bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content)>8192:
                    raise HTTPException(413,'This form is too large.')
        values=parse_qs(content.decode('utf-8'),max_num_fields=12,strict_parsing=True,keep_blank_values=True)
        if any(len(v)!=1 for v in values.values()):
            raise ValueError()
        return {k:v[0] for k,v in values.items()}
    except (UnicodeError,ValueError,TimeoutError):
        raise HTTPException(403,'Reload the page and try again.') from None


def safe_next(value):
    # Installation approval paths are added by the pairing phase, never arbitrary URLs.
    return value if value=='/account/' or re.fullmatch(r'/account/(?:pair|migrate)/[A-Za-z0-9_-]{16,64}',value or '') else '/account/'


def install_account_routes(app,settings,vault,identity_client,clock):
    metadata=SaaSStore(vault,settings.encryption_keys)
    identity=SupabaseAuth(settings.saas,identity_client)
    sessions=WebSessions(metadata,identity,clock)
    tokens=LandingTokens(settings.encryption_keys)
    app.state.saas=metadata
    app.state.saas_sessions=sessions

    def csrf(request,purpose):
        return tokens.issue(purpose,request.cookies.get(PREAUTH,''),clock()+900)

    def valid_form(request,data,purpose):
        if not tokens.valid(data.get('csrf',''),purpose,request.cookies.get(PREAUTH,''),clock()):
            raise HTTPException(403,'This form expired. Reload the page and try again.')

    def response_cookie(response,secret,name=PREAUTH,lifetime=1800):
        response.set_cookie(name,secret,secure=not settings.allow_http_local,httponly=True,
                            samesite='lax',max_age=int(lifetime),path='/')
        return response

    @app.get('/account/login',response_class=HTMLResponse)
    def login_page(request:Request):
        secret=request.cookies.get(PREAUTH,'')
        if not 16<=len(secret)<=512:
            secret=secrets.token_urlsafe(32)
        token=tokens.issue('saas-login',secret,clock()+900)
        return response_cookie(HTMLResponse(pages.login(token,safe_next(request.query_params.get('next','')))),secret)

    @app.post('/account/login',response_class=HTMLResponse)
    async def send_code(request:Request):
        data=await read_form(request,settings.public_url)
        valid_form(request,data,'saas-login')
        email=data.get('email','').strip().casefold()
        if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
            return HTMLResponse(pages.login(csrf(request,'saas-login'),safe_next(data.get('next','')),'Enter a valid email address.'),status_code=400)
        def prepare():
            with vault.transaction() as db:
                limits=[('email-minute:'+email,3,60),('email-hour:'+email,10,3600),
                        ('browser:'+request.cookies[PREAUTH],5,60),
                        ('login-peer:'+(request.client.host if request.client else 'unknown'),120,60)]
                if not all(metadata.admit(db,key,clock(),limit=limit,seconds=period) for key,limit,period in limits):
                    return None
                attempt=secrets.token_urlsafe(24)
                vault.put(db,'saas-login',attempt,{'email':email,'browser':digest(request.cookies[PREAUTH]),
                    'next':safe_next(data.get('next','')),'tries':0,'status':'pending'},expires=clock()+600)
                return attempt
        attempt=await asyncio.to_thread(prepare)
        if attempt is None:
            raise HTTPException(429,'Too many codes requested. Wait a minute and try again.')
        try:
            await asyncio.to_thread(identity.send_code,email)
        except (IdentityError,IdentityUnavailable):
            def cancel():
                with vault.transaction() as db:
                    vault.delete(db,'saas-login',attempt)
            await asyncio.to_thread(cancel)
            return HTMLResponse(pages.error('Email delivery is temporarily unavailable. Please try again.'),status_code=503)
        return HTMLResponse(pages.verify(csrf(request,attempt),attempt,email))

    @app.post('/account/verify',response_class=HTMLResponse)
    async def verify_code(request:Request):
        data=await read_form(request,settings.public_url)
        attempt=data.get('attempt','')
        valid_form(request,data,attempt)
        def reserve():
            with vault.transaction() as db:
                record=vault.get(db,'saas-login',attempt)
                if record is None or record['expires']<=clock() or not hmac.compare_digest(record['payload']['browser'],digest(request.cookies.get(PREAUTH,''))):
                    raise HTTPException(403,'This code expired. Request a new sign-in code.')
                payload=record['payload']
                if payload['status']!='pending':
                    raise HTTPException(409,'This code has already been used.')
                if payload['tries']>=5:
                    return None
                payload['tries']+=1
                vault.put(db,'saas-login',attempt,payload,expires=record['expires'])
                return payload
        payload=await asyncio.to_thread(reserve)
        if payload is None:
            raise HTTPException(429,'Too many attempts. Request a new sign-in code.')
        code=data.get('code','')
        try:
            if not re.fullmatch(r'[0-9]{6,8}',code):
                raise IdentityError()
            user,access,lifetime=await asyncio.to_thread(identity.verify,payload['email'],code)
        except IdentityError:
            return HTMLResponse(pages.verify(csrf(request,attempt),attempt,payload['email'],'That code is invalid or expired. Please try again.'),status_code=400)
        except IdentityUnavailable:
            return HTMLResponse(pages.verify(csrf(request,attempt),attempt,payload['email'],'Sign-in is temporarily unavailable. Please try again.'),status_code=503)
        def commit():
            with vault.transaction() as db:
                current=vault.get(db,'saas-login',attempt)
                if current is None or current['expires']<=clock() or current['payload']['status']!='pending':
                    raise HTTPException(409,'This code has already been used or expired.')
                current['payload']['status']='consumed'
                vault.put(db,'saas-login',attempt,current['payload'],expires=current['expires'])
                metadata.ensure_user(db,user,clock())
                previous=request.cookies.get(sessions.cookie,'')
                if previous:
                    vault.delete(db,'saas-session',digest(previous))
                return sessions.create(db,user,access,lifetime)
        secret=await asyncio.to_thread(commit)
        return response_cookie(RedirectResponse(payload['next'],status_code=303),secret,sessions.cookie,lifetime)

    @app.get('/account/',response_class=HTMLResponse)
    def account_page(request:Request):
        try:
            user=sessions.require_user(request)
        except HTTPException as exc:
            if exc.status_code!=401:
                raise
            return RedirectResponse('/account/login',status_code=303)
        from socialctl.saas.access import AccessControl
        with vault.transaction() as db:
            decision=AccessControl(metadata).decision(db,user.id,clock())
            entitlement=db.execute('SELECT * FROM saas_entitlements WHERE user_id=?',(user.id,)).fetchone()
            billing=db.execute('SELECT * FROM saas_billing WHERE user_id=?',(user.id,)).fetchone()
        token=csrf(request,'saas-account')
        details=pages.subscription(decision,entitlement,billing,token,
            enabled=hasattr(app.state,'saas_billing'),processing=request.query_params.get('payment')=='processing' and not decision.allowed)
        details+=app.state.saas_lifecycle.panel(user.id,token)
        response=HTMLResponse(pages.account(user,token,details))
        response.headers['Content-Security-Policy']="default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self' https://checkout.stripe.com https://billing.stripe.com; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.post('/account/logout')
    async def logout(request:Request):
        data=await read_form(request,settings.public_url)
        valid_form(request,data,'saas-account')
        await asyncio.to_thread(sessions.require_user,request)
        await asyncio.to_thread(sessions.revoke,request)
        response=RedirectResponse('/account/login',status_code=303)
        response.delete_cookie(sessions.cookie,path='/',secure=not settings.allow_http_local,httponly=True,samesite='lax')
        return response

    app.state.saas_csrf=csrf
    app.state.saas_valid_form=valid_form
    from socialctl.saas.pairing import install_pairing_routes
    install_pairing_routes(app,settings,metadata,sessions,clock)
    from socialctl.saas.lifecycle import install_lifecycle_routes
    install_lifecycle_routes(app,settings,metadata,sessions,clock)
    return metadata,sessions
