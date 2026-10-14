"""Server-side installation/platform boundaries, not provider action attenuation."""
from html import escape
from fastapi import HTTPException

PLATFORMS = {'youtube': 'YouTube', 'facebook': 'Facebook', 'instagram': 'Instagram', 'tiktok': 'TikTok'}


def policy(platforms, *, source='owner_selection'):
    return {'version': 1, 'platforms': sorted(platforms), 'source': source}


def approved(payload):
    value = payload.get('platform_access')
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('invalid platform policy')
    names = value.get('platforms')
    if (not isinstance(names, list) or any(not isinstance(p, str) or p not in PLATFORMS for p in names)
            or len(set(names)) != len(names)):
        raise ValueError('invalid platform policy')
    return sorted(names)


def require(payload, platform):
    try:
        names = approved(payload)
    except ValueError:
        raise HTTPException(403, 'installation_permissions_invalid: Ask the owner to revoke and link this assistant again.') from None
    if platform not in names:
        raise HTTPException(403, 'platform_not_allowed: This assistant was not approved for this social platform.')


def describe(payload):
    try:
        names = approved(payload)
    except ValueError:
        return {'version': 1, 'status': 'invalid', 'platforms': [], 'action_restrictions_enforced': False, 'provider_scope_restrictions_enforced': False, 'enforcement': 'connection_broker'}
    return {'version': 1, 'status': 'approved', 'platforms': names, 'action_restrictions_enforced': False, 'provider_scope_restrictions_enforced': False, 'enforcement': 'connection_broker'}


def from_form(data):
    if (data.get('platform_access_version') != '1'
            or set(data) - {'csrf', 'platform_access_version', *(f'allow_{p}' for p in PLATFORMS)}
            or any(value != '1' for key, value in data.items() if key.startswith('allow_'))):
        raise HTTPException(400, 'Review the social platforms on this page and submit again.')
    return policy([p for p in PLATFORMS if data.get('allow_' + p) == '1'])


def selection_html():
    choices = ''.join(f'<label for="allow-{p}"><input id="allow-{p}" type="checkbox" name="allow_{p}" value="1" checked> {escape(label)}</label>' for p, label in PLATFORMS.items())
    return '<fieldset class="permission-options"><legend>SocialCli connections this assistant may access</legend>' + choices + '</fieldset><input type="hidden" name="platform_access_version" value="1">'


def summary_html(payload):
    value = describe(payload)
    names = ', '.join(PLATFORMS[p] for p in value['platforms']) or 'None'
    return '<p>Approved social platforms: ' + escape(names) + '</p>' + (
        '<p class="status-notice">Permissions unavailable. Revoke and link this assistant again.</p>' if value['status'] == 'invalid' else '')
