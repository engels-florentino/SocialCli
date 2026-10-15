"""Conservative next-action hints from typed failures, never string guessing."""
from socialctl.connections.saas_client import MESSAGES, ServiceRejected, TransportFailure

_ACTIONS = {
    'privacy_acceptance_required': 'user_action',
    'assistant_consent_required': 'user_action',
    'platform_not_allowed': 'user_action',
    'installation_permissions_invalid': 'user_action',
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


# Only fixed internal codes acquire action semantics. Provider text is diagnostic,
# never a command, an expiry signal or evidence that a write was not accepted.
_CATALOG = {
    'approval_mismatch': ('user_action', 'The supplied approval does not match this exact preview. Obtain the owner approval before applying.'),
    'approval_required': ('user_action', 'Show the complete preview and obtain explicit approval of its exact fingerprint.'),
    'operation_cancelled': ('inspect', 'Approval was cancelled. No write was started by this command.'),
    'publication_pending': ('user_action', 'Complete or verify this existing submission in the platform; do not upload it again.'),
    'comment_incomplete': ('reconcile', 'Inspect the existing first-comment attempt and its ChangeSet. Do not upload the media again.'),
    'validation_failed': ('correct_input', 'Review every blocking validation item in the complete preview before approval.'),
    'reporting_incomplete': ('inspect', 'Some report observations could not be collected. Inspect the durable result and journal before further action; do not replay a write.'),
    'native_wait': ('wait', 'Keep this approved job and wait for its delivery window; do not create a duplicate.'),
    'native_dispatch': ('inspect', 'Inspect the approved route and delivery readiness before dispatching this existing job.'),
    'native_ui_required': ('user_action', 'Continue the exact approved handoff in the official platform UI. A submitted boundary permits readback only.'),
    'native_verify': ('reconcile', 'Collect fresh official evidence for this existing job. Scheduling alone does not prove publication.'),
    'operation_failed': ('inspect', 'Inspect the current state before taking another action.'),
    'service_rejected': ('inspect', 'Review account status and service availability.'),
    'local_configuration': ('user_action', 'Check local URL, proxy and CA configuration. No server rejection was received; keep credentials unchanged.'),
    'invalid_local_state': ('user_action', 'Review arguments, local account state and protected credential storage.'),
    'read_partial': ('inspect', 'Keep verified observations and inspect coverage before another read.'),
    'read_deadline': ('adjust_read', 'Keep verified observations; reduce read scope or increase the read budget.'),
    'response_too_large': ('adjust_read', 'Reduce read scope or page size; do not repeat the same oversized response.'),
    'read_interrupted': ('inspect', 'Keep verified observations. Resume only if the read is still requested.'),
    'not_attempted': ('inspect', 'This read was not attempted; inspect completed coverage before continuing.'),
    'transport_failure': ('retry_later', 'Wait before a bounded read retry. Keep credentials and all verified observations.'),
    'provider_unavailable': ('retry_later', 'Wait before a bounded read retry; the provider is unavailable.'),
    'rate_limited': ('retry_later', 'Wait for the provider rate limit before a bounded read retry.'),
    'authorization_unavailable': ('user_action', 'Review the account authorization; this failure alone does not prove token expiry.'),
    'permissions_required': ('user_action', 'Review granted permissions and account eligibility with the owner.'),
    'comments_disabled': ('inspect', 'Comments are disabled on this video. Reconnecting does not enable them; do not change video settings without owner approval.'),
    'target_unavailable': ('correct_input', 'Verify the requested target and account before another read.'),
    'request_rejected': ('correct_input', 'Review supported arguments and target before another request.'),
    'operation_rejected': ('inspect', 'Inspect the target, stored state and approval before another operation.'),
    'operation_uncertain': ('reconcile', 'Reconcile the existing operation. Do not replay its write.'),
    'operation_accepted_unverified': ('inspect', 'The provider accepted the operation. Independent verification is unavailable; this is not a rejection. Preserve the receipt and do not replay the write.'),
    'provider_response_invalid': ('inspect', 'Preserve verified observations and inspect the incomplete provider response.'),
    'storage_failure': ('repair_storage', 'Inspect local storage and preserve saved observations before recovery.'),
    'persistence_failed': ('repair_storage', 'Preserve verified observations and repair local persistence before collecting again.'),
    'unsupported_operation': ('unsupported', 'This integration does not support the requested operation.'),
    'account_not_configured': ('user_action', 'Ask the owner to select and authorize the intended account; never infer one.'),
    'invalid_input': ('correct_input', 'Review the supported arguments and explicit workspace.'),
    'installation_access_required': ('sign_in', 'Ask the owner to sign in to SocialCli again.'),
    'invalid_report_options': ('correct_input', 'Review the report date, window, item limit and platform.'),
    'report_workspace_unavailable': ('inspect_workspace', 'Inspect the explicitly selected report workspace.'),
    'snapshot_identity_mismatch': ('inspect_snapshot', 'Preserve the snapshot and inspect its brand/date binding.'),
    'snapshot_unreadable': ('inspect_snapshot', 'Preserve the unreadable snapshot before recovery.'),
    'snapshots_unavailable': ('collect_metrics', 'Collect authorized metrics before requesting a saved-snapshot report.'),
    'platform_read_unavailable': ('collect_metrics', 'Collect authorized metrics for this platform; retain other verified observations.'),
}


def error_for_code(code: str, *, read_only: bool = True) -> dict:
    if isinstance(code, str) and code in MESSAGES:
        action, message = _ACTIONS.get(code, 'inspect'), MESSAGES[code]
    else:
        code = code if isinstance(code, str) and code in _CATALOG else 'operation_failed'
        action, message = _CATALOG[code]
    if not read_only and (action == 'retry_later' or code in {'read_deadline', 'response_too_large'}):
        code = 'operation_uncertain'
        action, message = 'reconcile', 'Verify the existing operation before another write; its outcome may be uncertain.'
    return {'code':code, 'action':action,
            'retryable':bool(read_only and action == 'retry_later'),
            'requires_user_action':action in {'reconnect','sign_in','user_action','contact_support'},
            'message':message}


def error_document(exc: BaseException, *, read_only: bool) -> dict:
    import httpx
    from socialctl.read_budget import LocalConfigurationError
    from socialctl.auth import AuthError
    from socialctl.read_budget import ReadDeadlineExceeded, ReadResponseTooLarge
    from socialctl.management.changes import ChangeError
    from socialctl.management.youtube_resources import ResourceError, ResourceUncertain
    from socialctl.management.youtube import YouTubeManagementError, YouTubeUpdateUncertain
    from socialctl.management.meta_comments import CommentError, CommentUncertain
    from socialctl.management.meta_schema import MetaError, MetaUncertain
    from socialctl.metricas.base import SinPermiso

    # A read flow may refresh a token via POST. A lost response to that operation
    # cannot be classified as a harmless GET retry merely from the CLI command.
    if isinstance(exc, TransportFailure) and exc.request_method not in {None,'GET','HEAD','OPTIONS'}:
        read_only = False
    if isinstance(exc, httpx.RequestError):
        try: request = exc.request
        except RuntimeError: request = None
        if request is not None and request.method not in {'GET','HEAD','OPTIONS'}:
            read_only = False
    if isinstance(exc, ServiceRejected):
        code = exc.code if exc.code in MESSAGES else {
            401:'installation_access_required',402:'subscription_required',
            429:'rate_limited',503:'provider_unavailable'}.get(exc.status,'service_rejected')
    elif isinstance(exc, (ResourceUncertain, YouTubeUpdateUncertain, CommentUncertain, MetaUncertain)):
        code = 'operation_uncertain'
    elif isinstance(exc, (AuthError, YouTubeManagementError, ResourceError, CommentError, MetaError)) and getattr(exc, '_socialcli_error_code', None) is not None:
        code = exc._socialcli_error_code
    elif isinstance(exc, ReadDeadlineExceeded): code = 'read_deadline'
    elif isinstance(exc, ReadResponseTooLarge): code = 'response_too_large'
    elif isinstance(exc, KeyboardInterrupt): code = 'read_interrupted'
    elif isinstance(exc, AuthError): code = 'authorization_unavailable'
    elif isinstance(exc, SinPermiso): code = 'permissions_required'
    elif isinstance(exc, (LocalConfigurationError,httpx.InvalidURL,httpx.LocalProtocolError,httpx.UnsupportedProtocol)):
        code = 'local_configuration'
    elif isinstance(exc, (TransportFailure,httpx.TransportError)): code = 'transport_failure'
    elif isinstance(exc, ResourceError) and exc.http_status == 403 and exc.provider_reason in {'commentsDisabled', 'quotaExceeded'}:
        code = 'comments_disabled' if exc.provider_reason == 'commentsDisabled' else 'rate_limited'
    elif isinstance(exc, httpx.HTTPStatusError) or isinstance(exc, ResourceError) and exc.http_status:
        status = exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else exc.http_status
        if isinstance(exc,httpx.HTTPStatusError) and exc.request.method not in {'GET','HEAD','OPTIONS'}:
            read_only = False
        code = ({401:'authorization_unavailable',403:'permissions_required',404:'target_unavailable',
                429:'rate_limited'}.get(status,'provider_unavailable' if status>=500 else 'request_rejected')
                if type(status) is int else 'operation_rejected')
    elif isinstance(exc, httpx.RequestError): code = 'provider_response_invalid'
    elif isinstance(exc, ChangeError): code = 'operation_rejected'
    elif isinstance(exc, OSError): code = 'storage_failure'
    elif isinstance(exc, ValueError): code = 'invalid_local_state'
    else: code = 'operation_failed'
    return error_for_code(code,read_only=read_only)


def with_error_code(wrapper: Exception, cause: BaseException, *, read_only: bool,
                    fallback: str | None = None) -> Exception:
    """Keep only a fixed code across a sanitized wrapper, never raw exception data."""
    code = error_document(cause, read_only=read_only)['code']
    if code == 'operation_failed' and fallback is not None:
        code = error_for_code(fallback, read_only=read_only)['code']
    wrapper._socialcli_error_code = code
    return wrapper


def with_http_error_code(wrapper: Exception, response, *, read_only: bool) -> Exception:
    import httpx
    cause = httpx.HTTPStatusError('Provider request rejected', request=response.request,
                                  response=response)
    return with_error_code(wrapper, cause, read_only=read_only)


def emit_error(exc: Exception, *, read_only: bool) -> None:
    import json
    import typer
    typer.echo(json.dumps({'version': 1, 'status': 'error',
                           'error': error_document(exc, read_only=read_only)}))
