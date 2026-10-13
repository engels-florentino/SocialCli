"""Public renewal state derived from encrypted grant metadata; no token replay."""
from fastapi import HTTPException

PENDING_SECONDS=120
RETRY_SECONDS=30
CONFIG_RETRY_SECONDS=300

MESSAGES={
    'renewal_required':'An explicitly authorized operation must renew this token.',
    'renewal_pending':'Another renewal is pending. Try again later.',
    'renewal_retryable':'The provider could not be reached. Keep this connection and try again later.',
    'renewal_uncertain':'Renewal could not be confirmed. Review this connection in your SocialCli account.',
    'provider_configuration':'Provider configuration needs attention. Keep this connection and try again after it is fixed.',
    'provider_authorization_expired':'Provider authorization expired or was revoked. Reconnect this account explicitly.',
    'provider_permissions_changed':'Provider permissions changed. Reconnect this account explicitly to review the required permissions.',
}


def state(payload,now):
    error=payload.get('refresh_error')
    if error in {'provider_authorization_expired','provider_permissions_changed'}: return 'reconnect_required'
    if error=='renewal_uncertain': return 'uncertain'
    if payload.get('refresh_lease'):
        started=payload.get('refresh_started_at')
        return 'pending' if started is not None and now<started+PENDING_SECONDS else 'uncertain'
    if error=='provider_configuration': return 'configuration_error'
    if error=='renewal_retryable': return 'retryable'
    grant=payload['grant']
    if grant['expires_at']<=now+60:
        if payload['platform'] not in {'youtube','tiktok'} or grant.get('refresh_expires_at',float('inf'))<=now:
            return 'reconnect_required'
        return 'required'
    return 'ready'


def reject(code):
    status=409 if code in {'renewal_required','provider_authorization_expired','provider_permissions_changed'} else 503
    raise HTTPException(status,code+': '+MESSAGES[code])


def check(payload,now,refresh):
    current=state(payload,now)
    fatal={'uncertain':'renewal_uncertain','pending':'renewal_pending',
           'reconnect_required':payload.get('refresh_error') or 'provider_authorization_expired'}
    if current in fatal: reject(fatal[current])
    if current in {'retryable','configuration_error'} and payload.get('refresh_retry_at',0)>now:
        reject(payload['refresh_error'])
    if current!='ready' and not refresh: reject('renewal_required')


def clear(payload):
    for key in ('refresh_lease','refresh_started_at','refresh_error','refresh_retry_at'):
        payload.pop(key,None)


def record_failure(payload,code,now):
    clear(payload)
    payload['refresh_error']=code
    if code in {'renewal_retryable','provider_configuration'}:
        payload['refresh_retry_at']=now+(RETRY_SECONDS if code=='renewal_retryable' else CONFIG_RETRY_SECONDS)


def notice(payload,now):
    current=state(payload,now)
    if payload.get('refresh_error')=='provider_permissions_changed':
        return 'Provider permissions changed. Ask your assistant to reconnect this account with your approval to review the required permissions.'
    return {
        'required':'Token renewal is due. An explicitly authorized operation can renew it.',
        'pending':'Token renewal is in progress. Try again shortly.',
        'retryable':'The provider could not be reached. Your connection is saved; try again later.',
        'configuration_error':'The connection service needs a configuration fix. Your connection is saved.',
        'uncertain':'Renewal could not be confirmed. Ask your assistant to review this connection. If access cannot be restored, approve a new connection.',
        'reconnect_required':'Provider authorization expired or was revoked. Ask your assistant to reconnect this account with your approval.',
    }.get(current,'')
