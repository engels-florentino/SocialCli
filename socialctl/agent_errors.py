"""Conservative next-action hints from typed failures, never string guessing."""
from socialctl.connections.saas_client import MESSAGES, ServiceRejected, TransportFailure

_ACTIONS = {
    'provider_authorization_expired': 'reconnect',
    'provider_permissions_changed': 'reconnect',
    'renewal_uncertain': 'reconcile',
    'renewal_pending': 'retry_later',
    'renewal_retryable': 'retry_later',
    'renewal_required': 'user_action',
    'provider_configuration': 'contact_support',
    'installation_revoked': 'sign_in',
    'installation_expired': 'sign_in',
    'activation_required': 'recover',
    'subscription_required': 'user_action',
    'brand_limit': 'user_action',
    'connection_inactive': 'user_action',
    'connection_replaced': 'user_action',
    'connection_changed': 'user_action',
}


def error_document(exc: Exception, *, read_only: bool) -> dict:
    code, action = 'operation_failed', 'inspect'
    message = 'Operation failed. Inspect the current state before taking another action.'
    if isinstance(exc, ServiceRejected):
        code = exc.code if exc.code in MESSAGES else 'service_rejected'
        action = _ACTIONS.get(code, {401: 'sign_in', 402: 'user_action',
                                     429: 'retry_later', 503: 'retry_later'}.get(exc.status, 'inspect'))
        message = MESSAGES.get(code, 'SocialCli rejected the request. Review account status and service availability.')
    elif isinstance(exc, TransportFailure):
        code = 'transport_failure'
        action = 'retry_later' if read_only else 'reconcile'
        message = 'Connection failed. Keep credentials; verify any pending operation before sending it again.'
    elif isinstance(exc, ValueError):
        code, action = 'invalid_local_state', 'user_action'
        message = 'Review the command arguments, local account state and protected credential storage.'
    return {'code': code, 'action': action,
            'retryable': bool(read_only and action == 'retry_later'),
            'requires_user_action': action in {'reconnect', 'sign_in', 'user_action', 'contact_support'},
            'message': message}


def emit_error(exc: Exception, *, read_only: bool) -> None:
    import json
    import typer
    typer.echo(json.dumps({'version': 1, 'status': 'error',
                           'error': error_document(exc, read_only=read_only)}))
