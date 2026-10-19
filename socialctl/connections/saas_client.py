"""Bounded SaaS transport with fixed diagnostics and no legacy fallback."""
import json
import re
import httpx
from socialctl.connections.client import service_origin


class TransportFailure(ValueError):
    def __init__(self,message,*,request_method=None):
        super().__init__(message)
        self.request_method=request_method


class ServiceRejected(ValueError):
    def __init__(self,code,message,status):
        super().__init__(message)
        self.code,self.status=code,status


MESSAGES={
    'privacy_acceptance_required':'The account owner must review and accept the current Privacy Policy in their own SocialCli browser account. Never accept for the owner or reconnect automatically.',
    'assistant_consent_required':'The account owner must review assistant data access and provider settings in their own browser, then revoke and link this installation again. Never accept for the owner.',
    'platform_not_allowed':'The owner has not approved this assistant for that social platform. Run socialcli account permissions --brand BRAND --root WORKSPACE --no-browser and give its link to the owner to add access. Keep existing connections; do not log out or reconnect automatically.',
    'installation_permissions_invalid':'This installation has invalid permissions. Ask the owner to revoke it and link the assistant again.',
    'renewal_required':'Provider token needs renewal by an explicitly authorized operation.',
    'renewal_pending':'Provider renewal is pending. Try again later.',
    'renewal_uncertain':'Provider renewal could not be confirmed. Check your SocialCli account.',
    'renewal_retryable':'The provider could not be reached. Keep your connection and try again later.',
    'provider_configuration':'The connection service needs a configuration fix. Keep your connection and try again after it is fixed.',
    'provider_authorization_expired':'Provider authorization expired. Reconnect this account explicitly.',
    'provider_permissions_changed':'Provider permissions changed. Reconnect this account explicitly to review the required permissions.',
    'connection_inactive':'This SocialCli connection expired after inactivity. Review your account before connecting again.',
    'connection_unavailable':'This local connection is no longer available. Review your SocialCli account; use socialcli account attach PLATFORM --brand BRAND --root WORKSPACE to restore an existing owner-approved connection. Do not reconnect automatically.',
    'connection_replaced':'This connection was replaced. Review the current account before connecting again.',
    'connection_changed':'Another connection changed. Review your SocialCli account.',
    'activation_required':'Connection activation is pending. Run socialcli account recover.',
    'brand_limit':'This subscription already has a brand. Use its existing name.',
    'installation_revoked':'This installation was revoked. Run socialcli account login.',
    'installation_expired':'This installation expired. Run socialcli account login.',
    'subscription_required':'Your SocialCli subscription is required. Open your SocialCli account to manage access.',
    'user_authorization_required':'Instagram User authorization is unavailable. Ask the owner to reconnect Instagram with management consent; keep existing connections until replacement is confirmed.',
    'operation_uncertain':'The operation outcome is uncertain. Reconcile the saved operation; do not repeat its write.',
    'operation_conflict':'The approved account or resource changed. Review the saved operation before preparing another change.',
    'operation_rejected':'The provider operation was rejected or its ownership could not be verified. No automatic retry.',
}


class SaaSClient:
    def __init__(self,service,client,*,allow_http_local=False):
        self.origin=service_origin(service,allow_http_local=allow_http_local)
        self.client=client

    def request(self,method,path,*,secret=None,body=None,allow_missing=False,legacy_secret=None):
        from socialctl.read_budget import ReadDeadlineExceeded,ReadResponseTooLarge
        if path.startswith('/v1/connections'):
            path='/api/saas'+path
        if not re.fullmatch(r'/api/saas/v1/(?:capabilities|installations(?:/[A-Za-z0-9_-]{1,256}(?:/(?:authorizations|attach|migrations|rotations/[A-Za-z0-9_-]{16,64}))?)?|rotations/[A-Za-z0-9_-]{16,64}/commit|migrations/[A-Za-z0-9_-]{1,256}/poll|pairings/[A-Za-z0-9_-]{1,256}/(?:poll|ack)|authorizations/[A-Za-z0-9_-]{1,256}(?:/poll)?|connections/[A-Za-z0-9_-]{1,256}(?:/(?:activate|token|instagram-management|instagram-mention-inspect|media(?:/[A-Za-z0-9_-]{32})?))?)(?:\?refresh=false)?',path):
            raise ValueError('Invalid SocialCli account endpoint.')
        headers={'Authorization':'Bearer '+secret} if secret else {}
        if legacy_secret is not None:
            if not path.endswith('/migrations') or method!='POST':
                raise ValueError('Legacy proof is only accepted for explicit migration.')
            headers['X-SocialCli-Legacy-Secret']=legacy_secret
        try:
            with self.client.stream(method,self.origin+path,headers=headers,json=body,timeout=30,follow_redirects=False) as response:
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>65536: raise ValueError('Invalid SocialCli account response.')
                if response.status_code==404 and allow_missing:
                    return {'version':1,'status':'disconnected'}
                try: data=json.loads(raw)
                except (ValueError,RecursionError): data={}
                if not response.is_success:
                    detail=data.get('detail','') if isinstance(data,dict) else ''
                    code=detail.split(':',1)[0] if isinstance(detail,str) else ''
                    message=MESSAGES.get(code,{401:'SocialCli installation unavailable. Run socialcli account login.',
                        402:MESSAGES['subscription_required'],429:'Too many attempts. Wait a minute and try again.',
                        503:'SocialCli is temporarily unavailable. Try again later.'}.get(response.status_code,
                        'SocialCli rejected this request. Check your account and installed client version.'))
                    raise ServiceRejected(code,message,response.status_code)
                if not isinstance(data,dict) or type(data.get('version')) is not int or data['version']!=1:
                    raise ValueError('Unsupported SocialCli account response. Update the client.')
                return data
        except (ReadDeadlineExceeded,ReadResponseTooLarge):
            raise
        except (httpx.InvalidURL,httpx.LocalProtocolError,httpx.UnsupportedProtocol):
            from socialctl.read_budget import LocalConfigurationError
            raise LocalConfigurationError('Check local proxy and HTTP configuration; no server rejection was received.') from None
        except httpx.HTTPError:
            raise TransportFailure('Could not reach SocialCli securely. Keep your local credentials and check the connection before reviewing this attempt.',request_method=method) from None
