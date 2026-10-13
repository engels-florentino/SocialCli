"""Owner-bound OAuth, browser account selection and recoverable local activation."""
import asyncio
import hmac
import json
import secrets
from html import escape
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from pydantic import BaseModel,ConfigDict,Field
from socialctl.saas import pages
from socialctl.saas.auth import digest
from socialctl.saas.pairing import proof,INSTALLATION_SECONDS
from socialctl.saas.routes import read_form
from socialctl.saas.access import AccessControl
from socialctl.saas import renewal
from socialctl.connection_service.providers import RefreshFailure

OAUTH_SECONDS=600
INACTIVITY_SECONDS=90*86400


class AttachRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    platform:str=Field(pattern='^(youtube|facebook|instagram|tiktok)$')
    brand:str=Field(min_length=1,max_length=80)


class OAuthRequest(AttachRequest):
    poll_challenge:str=Field(pattern='^[0-9a-f]{64}$')
    management:bool=False
    analytics:bool=False


def safe_account(account):
    return {k:account[k] for k in ('id','name','page_id') if k in account}


class Connections:
    def __init__(self,metadata,installations,providers,clock):
        self.metadata,self.vault,self.installations,self.providers,self.clock=metadata,metadata.vault,installations,providers,clock
        self.access=AccessControl(metadata)

    def owner(self,db,installation_id):
        installation=self.vault.get(db,'saas-installation',installation_id)
        if installation is None or installation['expires']<=self.clock():
            raise HTTPException(401,'installation_revoked: Sign in to SocialCli again.')
        user_id=installation['payload']['user_id']
        user=self.metadata.user(db,user_id)
        if user is None or user['deleting']:
            raise HTTPException(401,'account_unavailable: Sign in to SocialCli again.')
        return user_id

    def allowed(self,db,user_id,*,new=False):
        decision=self.access.decision(db,user_id,self.clock())
        if not decision.allowed and not (new and decision.reason=='trial_not_started'):
            raise HTTPException(402,'subscription_required: Open your SocialCli account to manage access.')
        return decision

    def brand(self,db,user_id,label):
        brand=db.execute('SELECT * FROM saas_brands WHERE user_id=?',(user_id,)).fetchone()
        if brand and brand['label']!=label:
            raise HTTPException(409,'brand_limit: This subscription already has a brand. Use its existing name.')
        return brand

    def authorization(self,db,auth_id,request=None,user=None):
        record=self.vault.get(db,'authorization',auth_id)
        if record is None or record['expires']<=self.clock() or 'saas' not in record['payload']:
            raise HTTPException(404,'authorization_expired: Request a new connection link.')
        if request is not None and not hmac.compare_digest(record['credential_hash'],proof(request)):
            raise HTTPException(404,'Authorization unavailable.')
        owner=self.owner(db,record['payload']['saas']['installation_id'])
        if owner!=record['payload']['saas']['user_id'] or user is not None and owner!=user.id:
            raise HTTPException(403,'This connection belongs to another account.')
        return record

    def candidate(self,db,installation_id,user_id,platform,label,account,*,grant=None,existing=None,expected=None):
        connection_id,secret=secrets.token_urlsafe(24),secrets.token_urlsafe(32)
        data={'installation_id':installation_id,'user_id':user_id,'platform':platform,'brand':label,
              'account':safe_account(account),'expected_grant':expected,'existing_grant':existing}
        if grant is not None: data['grant']=grant
        self.vault.put(db,'saas-candidate',connection_id,data,expires=self.clock()+OAUTH_SECONDS,credential_hash=digest(secret))
        return {'version':1,'connection_id':connection_id,'connection_secret':secret,'platform':platform,
                'account':data['account'],'granted_scopes':grant['granted_scopes'] if grant else [],'status':'selected'}

    def capability(self,db,connection_id,request,*,require_slot=True):
        record=self.vault.get(db,'saas-capability',connection_id)
        if record is None:
            candidate=self.vault.get(db,'saas-candidate',connection_id)
            if candidate and candidate['expires']>self.clock() and hmac.compare_digest(candidate['credential_hash'],proof(request)):
                raise HTTPException(409,'activation_required: Finish installing this connection.')
            raise HTTPException(404,'connection_unavailable: Check your SocialCli account.')
        if record['expires']<=self.clock() or not hmac.compare_digest(record['credential_hash'],proof(request)):
            raise HTTPException(404,'connection_unavailable: Check your SocialCli account.')
        data=record['payload']
        if self.owner(db,data['installation_id'])!=data['user_id']:
            raise HTTPException(403,'Connection ownership mismatch.')
        slot=db.execute('SELECT * FROM saas_slots WHERE user_id=? AND platform=?',(data['user_id'],data['platform'])).fetchone()
        if require_slot and (slot is None or slot['grant_id']!=data['grant_id']):
            raise HTTPException(409,'connection_replaced: Select the current connection in your SocialCli account.')
        return record

    def activate(self,connection_id,request):
        with self.vault.transaction() as db:
            active=self.vault.get(db,'saas-capability',connection_id)
            if active:
                self.capability(db,connection_id,request)
                return {'version':1,'status':'active'}
            candidate=self.vault.get(db,'saas-candidate',connection_id)
            if candidate is None or candidate['expires']<=self.clock() or not hmac.compare_digest(candidate['credential_hash'],proof(request)):
                raise HTTPException(404,'Connection activation expired.')
            data=candidate['payload'];user_id=self.owner(db,data['installation_id'])
            if user_id!=data['user_id']: raise HTTPException(403,'Connection ownership mismatch.')
            self.allowed(db,user_id,new=True)
            brand=self.brand(db,user_id,data['brand'])
            current=db.execute('SELECT * FROM saas_slots WHERE user_id=? AND platform=?',(user_id,data['platform'])).fetchone()
            current_id=current['grant_id'] if current else None
            if current_id!=data['expected_grant']:
                raise HTTPException(409,'connection_changed: Another connection changed. Review the current account before retrying.')
            if data['platform'] in {'facebook','instagram'}:
                other='instagram' if data['platform']=='facebook' else 'facebook'
                linked=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',(user_id,other)).fetchone()
                if linked:
                    other_grant=self.vault.get(db,'saas-grant',linked['grant_id'])
                    if other_grant is None or other_grant['expires']<=self.clock():
                        db.execute('DELETE FROM saas_slots WHERE user_id=? AND platform=?',(user_id,other))
                    else:
                        wanted=data['account'].get('page_id') if data['platform']=='instagram' else data['account']['id']
                        have=other_grant['payload']['account'].get('page_id') if other=='instagram' else other_grant['payload']['account']['id']
                        if wanted!=have: raise HTTPException(409,'page_mismatch: Facebook and Instagram must share the same Page.')
            if data.get('legacy_id'):
                from socialctl.saas.migration import legacy_record
                old=legacy_record(self,db,data['legacy_id'],data['legacy_hash'],data['platform'])
                if old['payload']['account']!=data['account']:
                    raise HTTPException(409,'connection_changed: Review the current connection.')
                data['grant']=old['payload']['grant']
            if data['existing_grant']:
                grant_id=data['existing_grant']
                existing=self.vault.get(db,'saas-grant',grant_id)
                if existing is None or existing['expires']<=self.clock():
                    raise HTTPException(409,'connection_inactive: This SocialCli connection expired after inactivity.')
            else:
                grant_id=secrets.token_urlsafe(24)
                self.vault.put(db,'saas-grant',grant_id,{'user_id':user_id,'platform':data['platform'],
                    'account':data['account'],'grant':data['grant']},expires=self.clock()+INACTIVITY_SECONDS)
            if brand is None:
                db.execute('INSERT INTO saas_brands VALUES(?,?,?)',(user_id,secrets.token_urlsafe(24),data['brand']))
            self.access.activate_trial(db,user_id,self.clock())
            self.allowed(db,user_id)
            db.execute('INSERT OR REPLACE INTO saas_slots VALUES(?,?,?)',(user_id,data['platform'],grant_id))
            self.vault.put(db,'saas-capability',connection_id,{'user_id':user_id,'installation_id':data['installation_id'],
                'platform':data['platform'],'grant_id':grant_id,'account':data['account']},
                expires=self.clock()+INSTALLATION_SECONDS,credential_hash=candidate['credential_hash'])
            self.vault.delete(db,'saas-candidate',connection_id)
            if data.get('legacy_id'):
                self.vault.delete(db,'connection',data['legacy_id'])
                db.execute("DELETE FROM legacy_allowance WHERE kind='connection' AND id=?",(data['legacy_id'],))
            if current_id and current_id!=grant_id:
                self.vault.delete(db,'saas-grant',current_id)
        return {'version':1,'status':'active'}


def install_connection_routes(app,settings,metadata,sessions,providers,clock):
    vault=metadata.vault
    connections=Connections(metadata,app.state.saas_installations,providers,clock)
    app.state.saas_connections=connections
    csrf,valid_form=app.state.saas_csrf,app.state.saas_valid_form

    @app.post('/api/saas/v1/installations/{installation_id}/authorizations',status_code=201)
    def start(installation_id:str,body:OAuthRequest,request:Request):
        if body.platform not in settings.providers:
            raise HTTPException(503,'platform_unavailable: This platform is not configured.')
        try:
            providers.scopes(body.platform,management=body.management,analytics=body.analytics)
        except ValueError:
            raise HTTPException(400,'These permissions are not available for this platform.') from None
        auth_id=secrets.token_urlsafe(24)
        with vault.transaction() as db:
            installation=connections.installations.require(db,installation_id,request)
            user_id=installation['payload']['user_id']
            connections.allowed(db,user_id,new=True);connections.brand(db,user_id,body.brand)
            if not metadata.admit(db,'oauth-installation:'+installation_id,clock(),limit=5):
                raise HTTPException(429,'Too many connection attempts. Wait a minute.')
            current=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',(user_id,body.platform)).fetchone()
            data={'platform':body.platform,'status':'pending','management':body.management,'analytics':body.analytics,
                  'saas':{'user_id':user_id,'installation_id':installation_id,'brand':body.brand,'expected_grant':current['grant_id'] if current else None}}
            vault.put(db,'authorization',auth_id,data,expires=clock()+OAUTH_SECONDS,credential_hash=body.poll_challenge)
        return {'version':1,'authorization_id':auth_id,'browser_url':settings.public_url+'/connect/'+auth_id,
                'expires_in':OAUTH_SECONDS,'poll_interval':2}

    @app.post('/api/saas/v1/installations/{installation_id}/attach',status_code=201)
    def attach(installation_id:str,body:AttachRequest,request:Request):
        with vault.transaction() as db:
            installation=connections.installations.require(db,installation_id,request)
            user_id=installation['payload']['user_id'];connections.allowed(db,user_id);connections.brand(db,user_id,body.brand)
            slot=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',(user_id,body.platform)).fetchone()
            grant=vault.get(db,'saas-grant',slot['grant_id']) if slot else None
            if grant is None or grant['expires']<=clock():
                raise HTTPException(404,'No current connection is available for this platform.')
            result=connections.candidate(db,installation_id,user_id,body.platform,body.brand,
                grant['payload']['account'],existing=slot['grant_id'],expected=slot['grant_id'])
            result['granted_scopes']=grant['payload']['grant']['granted_scopes']
            return result

    @app.get('/account/connect/{auth_id}',response_class=HTMLResponse)
    def choose(auth_id:str,request:Request):
        user=sessions.require_user(request)
        with vault.transaction() as db:
            record=connections.authorization(db,auth_id,user=user);data=record['payload']
            if data['status']!='ready':
                raise HTTPException(409,'This account selection is unavailable. Return to your assistant.')
        choices=''
        for index,account in enumerate(data['result']['accounts']):
            choices+=f'<label for="choice-{index}"><input id="choice-{index}" name="account_id" type="radio" value="{escape(account["id"],quote=True)}" required> {escape(account.get("name",account["id"]))} <small>({escape(account["id"])})</small></label>'
        replacement='<p class="status-notice">The existing connection stays active until your assistant finishes installing this replacement.</p>' if data['saas']['expected_grant'] else ''
        return HTMLResponse(pages.page('Choose your account',f'''<p class="eyebrow">REVIEW YOUR CONNECTION</p><h1 id="page-title">Choose your account</h1>
<p class="description">Connect one {escape(data['platform'])} account to {escape(data['saas']['brand'])}. Only choose an account you own or manage.</p>{replacement}
<form method="post" action="/account/connect/{escape(auth_id,quote=True)}" class="form-stack">{pages.hidden('csrf',csrf(request,'select:'+auth_id))}{choices}<button class="primary-button" type="submit">Confirm this account</button></form><p class="expiry-note">Confirming does not publish content.</p>'''))

    @app.post('/account/connect/{auth_id}',response_class=HTMLResponse)
    async def select(auth_id:str,request:Request):
        form=await read_form(request,settings.public_url);valid_form(request,form,'select:'+auth_id)
        user=await asyncio.to_thread(sessions.require_user,request)
        def commit():
            with vault.transaction() as db:
                record=connections.authorization(db,auth_id,user=user);data=record['payload']
                if data['status']!='ready': raise HTTPException(409,'This account selection has already been used.')
                connections.allowed(db,user.id,new=True)
                account=next((a for a in data['result']['accounts'] if a['id']==form.get('account_id')),None)
                if account is None: raise HTTPException(400,'Choose an account authorized by the provider.')
                grant=json.loads(json.dumps(data['result']['grant']))
                if account.get('access_token'): grant['access_token']=account['access_token']
                owner=data['saas']
                result=connections.candidate(db,owner['installation_id'],user.id,data['platform'],owner['brand'],account,
                    grant=grant,expected=owner['expected_grant'])
                data.pop('result',None);data['selection']=result;data['status']='selected'
                vault.put(db,'authorization',auth_id,data,expires=record['expires'],credential_hash=record['credential_hash'])
        await asyncio.to_thread(commit)
        return HTMLResponse(pages.page('Account confirmed','<p class="eyebrow">ACCOUNT CONFIRMED</p><h1 id="page-title">Return to your assistant</h1><p class="description">Keep the SocialCli command open while it finishes installing your connection. No content was published.</p><a class="secondary-button" href="/account/">Your account</a>'))

    @app.post('/api/saas/v1/authorizations/{auth_id}/poll')
    def poll(auth_id:str,request:Request):
        with vault.transaction() as db:
            data=connections.authorization(db,auth_id,request)['payload']
            return data['selection'] if data['status']=='selected' else {'version':1,'status':data['status']}

    @app.delete('/api/saas/v1/authorizations/{auth_id}')
    def cancel(auth_id:str,request:Request):
        with vault.transaction() as db:
            data=connections.authorization(db,auth_id,request)['payload']
            if data.get('selection'): vault.delete(db,'saas-candidate',data['selection']['connection_id'])
            vault.delete(db,'authorization',auth_id)
        return {'version':1,'status':'cancelled'}

    @app.post('/api/saas/v1/connections/{connection_id}/activate')
    def activate(connection_id:str,request:Request):
        return connections.activate(connection_id,request)

    @app.get('/api/saas/v1/connections/{connection_id}')
    def status(connection_id:str,request:Request):
        with vault.transaction() as db:
            cap=connections.capability(db,connection_id,request);data=cap['payload']
            grant=vault.get(db,'saas-grant',data['grant_id'])
            if grant is None or grant['expires']<=clock():
                raise HTTPException(409,'connection_inactive: This SocialCli connection expired after inactivity.')
            decision=connections.access.decision(db,data['user_id'],clock())
            renewal_state=renewal.state(grant['payload'],clock())
            return {'version':1,'platform':data['platform'],'account':data['account'],
                'expires_at':grant['payload']['grant']['expires_at'],'granted_scopes':grant['payload']['grant']['granted_scopes'],
                'connection_expires_at':cap['expires'],'needs_reconnect':renewal_state=='reconnect_required',
                'renewal_status':renewal_state,
                'access':{'allowed':decision.allowed,'reason':decision.reason,'access_until':decision.access_until}}

    @app.delete('/api/saas/v1/connections/{connection_id}')
    def disconnect(connection_id:str,request:Request):
        with vault.transaction() as db:
            record=connections.capability(db,connection_id,request,require_slot=False);data=record['payload']
            db.execute('DELETE FROM saas_slots WHERE user_id=? AND platform=? AND grant_id=?',(data['user_id'],data['platform'],data['grant_id']))
            vault.delete(db,'saas-grant',data['grant_id']);vault.delete(db,'saas-capability',connection_id)
        return {'version':1,'status':'disconnected','provider_authorization_revoked':False}

    @app.post('/api/saas/v1/connections/{connection_id}/token')
    def token(connection_id:str,request:Request,refresh:bool=True):
        lease=None
        with vault.transaction() as db:
            cap=connections.capability(db,connection_id,request);data=cap['payload']
            connections.allowed(db,data['user_id'])
            record=vault.get(db,'saas-grant',data['grant_id'])
            if record is None or record['expires']<=clock():
                raise HTTPException(409,'connection_inactive: This SocialCli connection expired after inactivity.')
            if data['platform'] not in settings.providers: raise HTTPException(503,'platform_unavailable: Try again later.')
            grant=record['payload']['grant']
            renewal.check(record['payload'],clock(),refresh)
            if grant['expires_at']<=clock()+60:
                lease=secrets.token_urlsafe(24)
                renewal.clear(record['payload'])
                record['payload']['refresh_lease']=lease
                record['payload']['refresh_started_at']=clock()
                vault.put(db,'saas-grant',data['grant_id'],record['payload'],expires=record['expires'])
        if lease:
            try:
                renewed=providers.refresh(data['platform'],grant)
            except Exception as error:
                code=error.code if isinstance(error,RefreshFailure) else 'renewal_uncertain'
                with vault.transaction() as db:
                    current=vault.get(db,'saas-grant',data['grant_id'])
                    if current is None or current['payload'].get('refresh_lease')!=lease:
                        raise HTTPException(409,'connection_changed: Review the current connection.') from None
                    renewal.record_failure(current['payload'],code,clock())
                    vault.put(db,'saas-grant',data['grant_id'],current['payload'],expires=current['expires'])
                renewal.reject(code)
            with vault.transaction() as db:
                current=vault.get(db,'saas-grant',data['grant_id'])
                if current is None or current['payload'].get('refresh_lease')!=lease:
                    raise HTTPException(409,'connection_changed: Review the current connection.')
                renewal.clear(current['payload']);current['payload']['grant']=renewed
                vault.put(db,'saas-grant',data['grant_id'],current['payload'],expires=current['expires'])
                grant=renewed
        with vault.transaction() as db:
            connections.capability(db,connection_id,request);connections.allowed(db,data['user_id'])
            current=vault.get(db,'saas-grant',data['grant_id'])
            if current is None: raise HTTPException(409,'connection_changed: Review the current connection.')
            vault.put(db,'saas-grant',data['grant_id'],current['payload'],expires=clock()+INACTIVITY_SECONDS)
        return {'version':1,'platform':data['platform'],'account':data['account'],'access_token':grant['access_token'],
                'expires_at':grant['expires_at'],'granted_scopes':grant['granted_scopes']}
