"""Supabase Auth REST adapter; browser sessions never reach agent installations."""
import hashlib
import secrets
from uuid import UUID
import httpx
from fastapi import HTTPException
from socialctl.saas.models import AuthenticatedUser


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class IdentityError(Exception):
    pass


class IdentityUnavailable(Exception):
    pass


class SupabaseAuth:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client

    def request(self, method, path, *, token=None, data=None):
        headers = {'apikey':self.settings.auth_key} if self.settings.auth_key else {}
        if token:
            headers['Authorization']='Bearer '+token
        try:
            result = self.client.request(method,self.settings.auth_url+path,json=data,headers=headers,
                                         timeout=15,follow_redirects=False)
            if result.status_code in {400,401,403,422}:
                raise IdentityError()
            result.raise_for_status()
            return result.json() if result.content else {}
        except (httpx.HTTPError,ValueError):
            raise IdentityUnavailable() from None

    def send_code(self,email):
        self.request('POST','/otp',data={'email':email,'create_user':True})

    def verify(self,email,code):
        data=self.request('POST','/verify',data={'email':email,'token':code,'type':'email'})
        user=self.parse_user(data.get('user',{}))
        if user.email.casefold()!=email.casefold():
            raise IdentityError()
        token=data.get('access_token')
        expiry=data.get('expires_in')
        if not isinstance(token,str) or not 16<=len(token)<=16384 or not isinstance(expiry,(int,float)) or not 0<expiry<=86400:
            raise IdentityError()
        return user,token,min(expiry,3600)

    def get_user(self,token):
        return self.parse_user(self.request('GET','/user',token=token))

    def logout(self,token):
        self.request('POST','/logout?scope=local',token=token)

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

    def __init__(self,metadata,identity,clock):
        self.metadata,self.vault,self.identity,self.clock=metadata,metadata.vault,identity,clock

    def create(self,db,user,token,lifetime):
        secret=secrets.token_urlsafe(32)
        row=self.metadata.user(db,user.id)
        if row['deleting']:
            raise IdentityError()
        self.vault.put(db,'saas-session',digest(secret),{'user_id':user.id,'email':user.email,
            'access_token':token,'version':row['session_version']},expires=self.clock()+lifetime)
        return secret

    def require_user(self,request):
        secret=request.cookies.get(self.cookie,'')
        if not 16<=len(secret)<=512:
            raise HTTPException(401,'Sign in to SocialCli to continue.')
        with self.vault.transaction() as db:
            record=self.vault.get(db,'saas-session',digest(secret))
            if record is None or record['expires']<=self.clock():
                raise HTTPException(401,'Your session expired. Sign in again.')
            data=record['payload']; row=self.metadata.user(db,data['user_id'])
            if row is None or row['deleting'] or row['session_version']!=data['version']:
                raise HTTPException(401,'Sign in to SocialCli to continue.')
        try:
            user=self.identity.get_user(data['access_token'])
            if user.id!=data['user_id'] or user.email!=data['email']:
                raise IdentityError()
        except IdentityError:
            with self.vault.transaction() as db:
                self.vault.delete(db,'saas-session',digest(secret))
            raise HTTPException(401,'Sign in to SocialCli to continue.') from None
        except IdentityUnavailable:
            raise HTTPException(503,'Sign-in is temporarily unavailable. Please try again.') from None
        # Recheck after network I/O: concurrent logout/deletion must win.
        with self.vault.transaction() as db:
            current=self.vault.get(db,'saas-session',digest(secret))
            row=self.metadata.user(db,user.id)
            if current is None or current['expires']<=self.clock() or row is None or row['deleting'] or row['session_version']!=data['version']:
                raise HTTPException(401,'Sign in to SocialCli to continue.')
        request.state.saas_session=data
        return user

    def revoke(self,request):
        with self.vault.transaction() as db:
            self.vault.delete(db,'saas-session',digest(request.cookies.get(self.cookie,'')))
        try:
            self.identity.logout(request.state.saas_session['access_token'])
        except (IdentityError,IdentityUnavailable):
            pass  # Local revocation is already durable, even if upstream is down.
