"""Supabase Auth REST adapter; browser sessions never reach agent installations."""
import hashlib
import secrets
from uuid import UUID
import httpx
from fastapi import HTTPException
from socialctl.saas.models import AuthenticatedUser
from socialctl.saas.budget import BudgetExpired


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class IdentityError(Exception):
    pass


class IdentityUnavailable(Exception):
    pass


class SupabaseAuth:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        self.budget = None

    def request(self, method, path, *, token=None, data=None, identity_errors=(400,401,403,422)):
        headers = {'apikey':self.settings.auth_key} if self.settings.auth_key else {}
        if token:
            headers['Authorization']='Bearer '+token
        try:
            result = self.client.request(method,self.settings.auth_url+path,json=data,headers=headers,
                                         timeout=self.budget.timeout(15) if self.budget else 15,follow_redirects=False)
            if result.status_code in identity_errors:
                raise IdentityError()
            result.raise_for_status()
            return result.json() if result.content else {}
        except (httpx.HTTPError,ValueError,BudgetExpired):
            raise IdentityUnavailable() from None

    def send_code(self,email):
        # OTP has no user bearer token: 401/403 concern service authorization.
        self.request('POST','/otp',data={'email':email,'create_user':True},identity_errors=(400,422))

    def verify(self,email,code):
        data=self.request('POST','/verify',data={'email':email,'token':code,'type':'email'})
        user=self.parse_user(data.get('user',{}))
        if user.email.casefold()!=email.casefold():
            raise IdentityError()
        token,expiry,refresh=self.tokens(data)
        return user,token,expiry,refresh

    @staticmethod
    def tokens(data):
        token=data.get('access_token');expiry=data.get('expires_in');refresh=data.get('refresh_token')
        if (not isinstance(token,str) or not 16<=len(token)<=16384
                or type(expiry) not in (int,float) or not 0<expiry<=86400
                or not isinstance(refresh,str) or not 1<=len(refresh)<=16384 or any(ord(c)<33 or ord(c)==127 for c in refresh)):
            raise IdentityError()
        return token,expiry,refresh

    def refresh(self,refresh_token):
        data=self.request('POST','/token?grant_type=refresh_token',data={'refresh_token':refresh_token})
        user=self.parse_user(data.get('user',{}))
        return user,*self.tokens(data)

    def get_user(self,token):
        return self.parse_user(self.request('GET','/user',token=token))

    def logout(self,token):
        self.request('POST','/logout?scope=local',token=token)

    def delete_user(self,user_id):
        user_id=str(UUID(user_id))
        if not self.settings.auth_admin_key:raise IdentityUnavailable()
        try:
            result=self.client.request('DELETE',self.settings.auth_url+'/admin/users/'+user_id,
                headers={'Authorization':'Bearer '+self.settings.auth_admin_key,'apikey':self.settings.auth_admin_key},
                timeout=self.budget.timeout(15) if self.budget else 15,follow_redirects=False)
            if result.status_code in {200,204}:return
            if result.status_code==404 and result.json().get('error_code')=='user_not_found':return
            raise IdentityUnavailable()
        except (httpx.HTTPError,ValueError,AttributeError,BudgetExpired):
            raise IdentityUnavailable() from None

    @staticmethod
    def parse_user(data):
        try:
            user_id=str(UUID(data['id']))
            email=data['email']
            if not data.get('email_confirmed_at') or not isinstance(email,str) or not 3<=len(email)<=254 or '@' not in email:
                raise ValueError()
            return AuthenticatedUser(user_id,email)
        except (KeyError,TypeError,ValueError,AttributeError):
            raise IdentityError() from None


class WebSessions:
    cookie='socialcli-session'
    max_lifetime=12*3600
    verification_seconds=45

    def __init__(self,metadata,identity,clock):
        self.metadata,self.vault,self.identity,self.clock=metadata,metadata.vault,identity,clock

    def create(self,db,user,token,lifetime,refresh_token=None):
        secret=secrets.token_urlsafe(32)
        row=self.metadata.user(db,user.id)
        if row['deleting']:
            raise IdentityError()
        self.vault.put(db,'saas-session',digest(secret),{'user_id':user.id,'email':user.email,
            'access_token':token,'version':row['session_version'],'refresh_token':refresh_token,
            'access_expires':self.clock()+lifetime,'verified_until':0},
            expires=self.clock()+(self.max_lifetime if refresh_token else lifetime))
        return secret

    def checked(self,db,key):
        record=self.vault.get(db,'saas-session',key)
        if record is None or record['expires']<=self.clock():
            raise HTTPException(401,'Your session expired. Sign in again.')
        data=record['payload'];row=self.metadata.user(db,data['user_id'])
        if row is None or row['deleting'] or row['session_version']!=data['version']:
            raise HTTPException(401,'Sign in to SocialCli to continue.')
        return record

    def require_user(self,request,*,privacy=True):
        user=self.authenticated_user(request)
        if privacy:
            from socialctl.saas import consent
            with self.vault.read_transaction() as db:
                accepted=consent.privacy_accepted(self.metadata,db,user.id)
            if not accepted:
                if request.method in {'GET','HEAD'}:
                    from urllib.parse import urlencode
                    from socialctl.saas.routes import safe_next
                    raise HTTPException(303,'Review the Privacy Policy.',headers={
                        'Location':'/account/privacy?'+urlencode({'next':safe_next(request.url.path)})})
                raise HTTPException(403,'privacy_acceptance_required: The account owner must review the current Privacy Policy in their browser.')
        return user

    def authenticated_user(self,request):
        secret=request.cookies.get(self.cookie,'')
        if not 16<=len(secret)<=512:
            raise HTTPException(401,'Sign in to SocialCli to continue.')
        key=digest(secret);lease=None
        with self.vault.read_transaction() as db:
            record=self.checked(db,key)
        data=record['payload']
        refresh=bool(data.get('refresh_token') and data.get('access_expires',record['expires'])<=self.clock()+60)
        if not refresh and data.get('verified_until',0)>self.clock():
            request.state.saas_session=data
            return AuthenticatedUser(data['user_id'],data['email'])
        if refresh:
            with self.vault.transaction() as db:
                record=self.checked(db,key);data=record['payload']
                if data.get('access_expires',0)>self.clock()+60:
                    request.state.saas_session=data
                    return AuthenticatedUser(data['user_id'],data['email'])
                if data.get('refresh_lease'):
                    # An ambiguous rotation must never replay an old refresh token.
                    if data['refresh_until']<=self.clock():
                        raise HTTPException(401,'Your session needs a fresh sign-in.')
                    if request.method not in {'GET','HEAD'} or data.get('access_expires',0)<=self.clock():
                        raise HTTPException(503,'Your session is refreshing. Please try again shortly.')
                    # Verify the still-live access token without replaying refresh.
                    # The final checked transaction preserves logout/account fences.
                else:
                    lease=secrets.token_urlsafe(24)
                    data.update(refresh_lease=lease,refresh_until=self.clock()+30)
                    self.vault.put(db,'saas-session',key,data,expires=record['expires'])
        try:
            if lease:
                user,access,expiry,refresh_token=self.identity.refresh(data['refresh_token'])
            else:
                user=self.identity.get_user(data['access_token'])
            if user.id!=data['user_id'] or user.email!=data['email']:
                raise IdentityError()
        except IdentityError:
            with self.vault.transaction() as db:
                current=self.vault.get(db,'saas-session',key)
                if current and current['payload'].get('access_token')==data['access_token']:
                    self.vault.delete(db,'saas-session',key)
            raise HTTPException(401,'Sign in to SocialCli to continue.') from None
        except IdentityUnavailable:
            raise HTTPException(503,'Sign-in is temporarily unavailable. Please try again.') from None
        # A concurrent logout, deletion or credential rotation must win.
        with self.vault.transaction() as db:
            current=self.checked(db,key);latest=current['payload']
            if lease:
                if latest.get('refresh_lease')!=lease:
                    raise HTTPException(401,'Sign in to SocialCli to continue.')
                latest.update(access_token=access,refresh_token=refresh_token,access_expires=self.clock()+expiry)
                latest.pop('refresh_lease',None);latest.pop('refresh_until',None)
            else:
                if latest.get('refresh_lease') and latest['refresh_until']<=self.clock():
                    raise HTTPException(401,'Your session needs a fresh sign-in.')
                if latest.get('access_expires',current['expires'])<=self.clock():
                    raise HTTPException(503,'Your session is refreshing. Please try again shortly.')
                if latest['access_token']!=data['access_token'] and latest.get('verified_until',0)<=self.clock():
                    raise HTTPException(503,'Your session changed. Please try again.')
            latest['verified_until']=min(self.clock()+self.verification_seconds,latest.get('access_expires',current['expires']))
            self.vault.put(db,'saas-session',key,latest,expires=current['expires'])
        request.state.saas_session=latest
        return user

    def revoke(self,request):
        with self.vault.transaction() as db:
            self.vault.delete(db,'saas-session',digest(request.cookies.get(self.cookie,'')))
        try:
            self.identity.logout(request.state.saas_session['access_token'])
        except (IdentityError,IdentityUnavailable):
            pass  # Local revocation is already durable, even if upstream is down.
