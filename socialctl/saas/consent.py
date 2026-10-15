"""Versioned, browser-owner consent; agent credentials never create receipts.

Bump the respective version for a material policy or assistant-transfer change.
Previously issued provider credentials cannot be recalled by this broker gate.
"""
from fastapi import HTTPException

PRIVACY_VERSION = '2026-10-15'
ASSISTANT_VERSION = '2026-10-15'


def privacy_accepted(metadata, db, user_id):
    record = metadata.vault.get(db, 'saas-consent', user_id)
    return bool(record and record['payload'].get('user_id') == user_id
                and record['payload'].get('privacy_version') == PRIVACY_VERSION)


def require_privacy(metadata, db, user_id):
    if not privacy_accepted(metadata, db, user_id):
        raise HTTPException(403, 'privacy_acceptance_required: The account owner must review the current Privacy Policy in their SocialCli browser account. An assistant cannot accept for the owner.')


def privacy_choice(data, *, required=True):
    accepted = data.get('privacy_accept') == '1'
    if (required or accepted) and (not accepted or data.get('privacy_version') != PRIVACY_VERSION):
        raise HTTPException(400, 'Review the current Privacy Policy and explicitly accept it to enable features.')
    return accepted


def accept_privacy(metadata, db, user_id, now):
    metadata.vault.put(db, 'saas-consent', user_id,
                      {'user_id': user_id, 'privacy_version': PRIVACY_VERSION,
                       'accepted_at': now, 'source': 'owner_browser'}, expires=253402300799)


def assistant_choice(data, now):
    if data.get('assistant_accept') != '1' or data.get('assistant_version') != ASSISTANT_VERSION:
        raise HTTPException(400, 'Review and explicitly acknowledge the assistant data access and provider settings before linking.')
    return {'version': ASSISTANT_VERSION, 'accepted_at': now, 'source': 'owner_browser'}


def require_assistant(payload):
    receipt = payload.get('assistant_consent')
    if not isinstance(receipt, dict) or receipt.get('version') != ASSISTANT_VERSION:
        raise HTTPException(403, 'assistant_consent_required: The account owner must revoke and link this assistant again in their browser to review data access and provider settings.')


def require_legacy(payload):
    receipt=payload.get('privacy_consent')
    if not isinstance(receipt,dict) or receipt.get('version')!=PRIVACY_VERSION:
        raise HTTPException(403,'privacy_acceptance_required: The owner must sign in to the current SocialCli browser flow and review the Privacy Policy before using this connection.')
    require_assistant(payload)
