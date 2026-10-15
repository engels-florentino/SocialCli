"""Thirty-minute human-approved pairing with retry-safe, scoped capabilities."""
import asyncio
import hmac
import re
import secrets
from html import escape
from urllib.parse import quote
from socialctl.connection_service.peer import client_ip
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from socialctl.saas.auth import digest
from socialctl.saas.routes import read_form
from socialctl.saas import pages, permissions

PAIR_SECONDS=1800
INSTALLATION_SECONDS=90*86400


class PairRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=80)
    poll_challenge:str=Field(pattern='^[0-9a-f]{64}$')


def proof(request):
    value=request.headers.get('authorization','')
    if not value.startswith('Bearer ') or not 16<=len(value[7:])<=512:
        return ''
    return digest(value[7:])


class Installations:
    def __init__(self,metadata,clock):
        self.metadata,self.vault,self.clock=metadata,metadata.vault,clock

    def require(self,db,installation_id,request):
        record=self.vault.get(db,'saas-installation',installation_id)
        if record is None or record['expires']<=self.clock() or not hmac.compare_digest(record['credential_hash'],proof(request)):
            raise HTTPException(401,'installation_expired: Sign in to SocialCli again.')
        user=self.metadata.user(db,record['payload']['user_id'])
        if user is None or user['deleting']:
            raise HTTPException(401,'installation_revoked: Sign in to SocialCli again.')
        return record


def install_pairing_routes(app,settings,metadata,sessions,clock):
    vault=metadata.vault
    installations=Installations(metadata,clock)
    app.state.saas_installations=installations
    csrf=app.state.saas_csrf
    valid_form=app.state.saas_valid_form

    def pairing(db,pair_id,request=None):
        record=vault.get(db,'saas-pair',pair_id)
        if record is None or record['expires']<=clock() or request is not None and not hmac.compare_digest(record['credential_hash'],proof(request)):
            raise HTTPException(404,'pairing_expired: Request a new SocialCli sign-in link.')
        return record

    @app.get('/api/saas/v1/capabilities')
    def capabilities():
        return {'version':1,'service':'socialcli-saas','minimum_client':'0.3.0',
                'pairing_seconds':PAIR_SECONDS,'oauth_seconds':600,'browser_account_selection':True,
                'currency':'usd','monthly_amount':1000,'trial_days':14,
                'platforms':sorted(settings.providers)}

    @app.post('/api/saas/v1/installations/pair',status_code=201)
    def start(body:PairRequest,request:Request):
        pair_id=secrets.token_urlsafe(24)
        with vault.transaction() as db:
            peer=client_ip(request,settings)
            if not metadata.admit(db,'pair-peer:'+peer,clock(),limit=60):
                raise HTTPException(429,'Too many sign-in links. Please wait a minute.')
            active=db.execute("SELECT count(*) FROM records WHERE kind='saas-pair' AND expires>?",(clock(),)).fetchone()[0]
            if active>=1000:
                raise HTTPException(503,'Sign-in is busy. Please try again.')
            vault.put(db,'saas-pair',pair_id,{'label':body.label,'status':'pending'},
                      expires=clock()+PAIR_SECONDS,credential_hash=body.poll_challenge)
        return {'version':1,'pairing_id':pair_id,'browser_url':settings.public_url+'/account/pair/'+pair_id,
                'expires_in':PAIR_SECONDS,'poll_interval':2}

    @app.get('/account/pair/{pair_id}',response_class=HTMLResponse)
    def approve_page(pair_id:str,request:Request):
        with vault.read_transaction() as db:
            record=pairing(db,pair_id)
            if record['payload']['status']!='pending':
                raise HTTPException(409,'This assistant has already been linked.')
        try:
            sessions.require_user(request)
        except HTTPException as exc:
            if exc.status_code!=401: raise
            return RedirectResponse('/account/login?next='+quote('/account/pair/'+pair_id,safe=''),status_code=303)
        label=escape(record['payload']['label'])
        content=f'''<p class="eyebrow">LINK AN ASSISTANT</p><h1 id="page-title">Allow {label}?</h1>
<p class="description">Choose which SocialCli connections this installation may access. Only approve an assistant you recognize. Provider credentials can include permissions for linked accounts and actions. This selection controls access to SocialCli connections; it does not narrow provider permissions.</p>
<div class="status-notice">You can revoke this installation from your account at any time. Previously issued provider tokens may remain usable until they expire or are revoked at the provider. Linking does not approve any publication.</div>
<form method="post" action="/account/pair/{escape(pair_id,quote=True)}" class="form-stack">{pages.hidden('csrf',csrf(request,'pair:'+pair_id))}{permissions.selection_html()}<button class="primary-button" type="submit">Allow {label}</button></form>
<a class="text-link" href="/account/">Cancel and return to account</a>'''
        return HTMLResponse(pages.page('Link an assistant',content))

    @app.post('/account/pair/{pair_id}',response_class=HTMLResponse)
    async def approve(pair_id:str,request:Request):
        data=await read_form(request,settings.public_url)
        valid_form(request,data,'pair:'+pair_id)
        platform_access=permissions.from_form(data)
        user=await asyncio.to_thread(sessions.require_user,request)
        def commit():
            with vault.transaction() as db:
                record=pairing(db,pair_id)
                if record['payload']['status']!='pending':
                    raise HTTPException(409,'This assistant has already been linked.')
                row=metadata.user(db,user.id)
                if row is None or row['deleting']:
                    raise HTTPException(401,'Sign in to SocialCli again.')
                installation_id,secret=secrets.token_urlsafe(24),secrets.token_urlsafe(32)
                vault.put(db,'saas-installation',installation_id,{'user_id':user.id,'label':record['payload']['label'],
                    'created_at':clock(),'platform_access':platform_access},expires=clock()+INSTALLATION_SECONDS,credential_hash=digest(secret))
                record['payload'].update(status='approved',user_id=user.id,installation_id=installation_id,installation_secret=secret)
                vault.put(db,'saas-pair',pair_id,record['payload'],expires=record['expires'],credential_hash=record['credential_hash'])
        await asyncio.to_thread(commit)
        return HTMLResponse(pages.page('Assistant linked','<p class="eyebrow">ALL SET</p><h1 id="page-title">Assistant linked</h1><p class="description">Return to your assistant to continue. No social account has been connected by this step.</p><a class="secondary-button" href="/account/">Your account</a>'))

    @app.post('/api/saas/v1/pairings/{pair_id}/poll')
    def poll(pair_id:str,request:Request):
        with vault.read_transaction() as db:
            payload=pairing(db,pair_id,request)['payload']
            return {'version':1,**{k:v for k,v in payload.items() if k in {'status','installation_id','installation_secret'}}}

    @app.post('/api/saas/v1/pairings/{pair_id}/ack')
    def ack(pair_id:str,request:Request):
        with vault.transaction() as db:
            record=pairing(db,pair_id,request)
            if record['payload']['status'] not in {'approved','consumed'}:
                raise HTTPException(409,'Pairing is not approved.')
            record['payload'].pop('installation_secret',None)
            record['payload']['status']='consumed'
            vault.put(db,'saas-pair',pair_id,record['payload'],expires=min(record['expires'],clock()+600),credential_hash=record['credential_hash'])
        return {'version':1,'status':'consumed'}

    @app.get('/api/saas/v1/installations/{installation_id}')
    def status(installation_id:str,request:Request):
        with vault.read_transaction() as db:
            record=installations.require(db,installation_id,request)
        return {'version':1,'installation_id':installation_id,'label':record['payload']['label'],'expires_at':record['expires'],
                'platform_access':permissions.describe(record['payload'])}

    @app.delete('/api/saas/v1/installations/{installation_id}')
    def revoke(installation_id:str,request:Request):
        with vault.transaction() as db:
            installations.require(db,installation_id,request)
            vault.delete(db,'saas-installation',installation_id)
        return {'version':1,'status':'revoked','provider_authorization_revoked':False}
