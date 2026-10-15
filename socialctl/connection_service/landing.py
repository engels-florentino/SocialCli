"""Read-only login landing and browser-bound, authenticated form tokens."""
from __future__ import annotations

import hashlib
import hmac
import json
from html import escape

from cryptography.fernet import Fernet, MultiFernet


ASSETS = '/connect/assets'

PROVIDER_LOGOS = {'youtube': 'youtube.svg', 'facebook': 'facebook.webp',
                  'instagram': 'instagram.svg', 'tiktok': 'tiktok.png'}

PROVIDERS = {
    'youtube': ('YouTube', 'Google', 'Choose your channel and review access with Google.'),
    'facebook': ('Facebook', 'Facebook', 'Choose the Page you want to connect with SocialCli.'),
    'instagram': ('Instagram', 'Facebook', 'Connect your professional account through Facebook Login.'),
    'tiktok': ('TikTok', 'TikTok', 'Sign in to TikTok and choose the access you want to share.'),
}


def _icon(name: str) -> str:
    paths = {
        'arrow': '<path d="M5 12h14m-6-6 6 6-6 6"/>',
        'lock': '<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
        'terminal': '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m6 0h4"/>',
        'check': '<path d="m5 12 4 4L19 6"/>',
        'refresh': '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1"/>',
        'close': '<path d="m6 6 12 12M6 18 18 6"/>',
    }
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>'


def _page(title: str, content: str, *, platform=None) -> str:
    provider = PROVIDERS.get(platform)
    logo = PROVIDER_LOGOS[platform] if provider else 'socialcli-logo.png'
    mime = {'svg': 'image/svg+xml', 'png': 'image/png', 'webp': 'image/webp'}[logo.rsplit('.', 1)[1]]
    label = provider[0].upper() + ' CONNECTION' if provider else 'ACCOUNT CONNECTION'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="light">
<title>{escape(title)} · SocialCli</title>
<link rel="icon" type="{mime}" href="{ASSETS}/{logo}">
<link rel="stylesheet" href="{ASSETS}/connection.css">
</head><body>
<header class="site-header"><a class="wordmark" href="/" aria-label="SocialCli home">
<img src="{ASSETS}/socialcli-logo.png" alt="SocialCli" width="42" height="42"><span>SocialCli</span></a>
<span class="header-label">{label}</span></header>
<main id="main-content"><section class="connection-card" aria-labelledby="page-title">{content}</section>
<p class="trust-note">{_icon('lock')} Your account. Your permissions. Your choice.</p></main>
<footer class="site-footer"><span>Social tools. Human control.</span>
<nav aria-label="Legal and support"><a href="/privacy.html">Privacy</a><a href="/terms.html">Terms</a><a href="/connections.html">Help</a></nav>
</footer></body></html>'''


def connection_symbols(platform):
    return f'''<div class="connection-symbols" aria-hidden="true"><img src="{ASSETS}/socialcli-logo.png" alt="" width="64" height="64">
<span class="connection-line"></span><span class="provider-mark provider-{platform}{" provider-app-icon" if platform != "youtube" else ""}"><img class="provider-logo" src="{ASSETS}/{PROVIDER_LOGOS[platform]}" alt="" width="32" height="32"></span></div>'''


def landing_page(authorization_id: str, form_token: str, *, platform='youtube', browser_selection=False) -> str:
    from socialctl.saas import consent
    from socialctl.saas.pages import assistant_choice_html
    privacy = f'<input type="hidden" name="privacy_version" value="{escape(consent.PRIVACY_VERSION,quote=True)}"><label for="privacy-accept"><input id="privacy-accept" type="checkbox" name="privacy_accept" value="1" required> I am the account owner and have read and agree to the <a href="https://socialcli.app/privacy.html" target="_blank" rel="noopener">Privacy Policy</a> (version {escape(consent.PRIVACY_VERSION)}).</label>'
    assistant = '' if browser_selection else assistant_choice_html()
    action = escape('/connect/' + authorization_id, quote=True)
    token = escape(form_token, quote=True)
    name, login, description = PROVIDERS[platform]
    confirmation = ('After signing in, choose and confirm your account in this browser.'
                    if browser_selection else 'Return there to choose and confirm your account.')
    content = f'''
{connection_symbols(platform)}
<p class="eyebrow">LET'S GET CONNECTED</p>
<h1 id="page-title">Connect to {name}</h1>
<p class="description">{description}</p>
<ol class="steps" aria-label="Connection steps"><li class="current"><span>1</span>Sign in</li><li><span>2</span>Review access</li><li><span>3</span>Confirm</li></ol>
<form method="post" action="{action}">
<input type="hidden" name="form_token" value="{token}">
{privacy}{assistant}
<button class="primary-button" type="submit"><span>Continue with {login}</span>{_icon('arrow')}</button></form>
<p class="button-note">You'll continue to {login} to sign in.</p>
<div class="connection-details"><div class="detail">{_icon('lock')}<div><h2>You're in control</h2><p>Review the requested access before you approve.</p></div></div>
<div class="detail">{_icon('terminal')}<div><h2>Keep SocialCli open</h2><p>{confirmation}</p></div></div></div>
<p class="quiet-notice">Connecting does not publish content.</p>
<p class="expiry-note">This link expires 10 minutes after it was created.</p>'''
    return _page('Connect to ' + name, content, platform=platform)


def result_page(message: str, *, success=False, cancelled=False, platform=None) -> str:
    title = 'Authorization received' if success else 'Authorization cancelled' if cancelled else "We couldn't connect"
    if platform in PROVIDERS:
        title += ' · ' + PROVIDERS[platform][0]
    description = ('One more step: return to SocialCli to choose and confirm your account.' if success else
                   'Nothing was connected. You can start again whenever you are ready.' if cancelled else
                   'Return to SocialCli and request a new connection link to try again.')
    icon = 'check' if success else 'close' if cancelled else 'refresh'
    state = 'success' if success else 'neutral' if cancelled else 'warning'
    content = f'''{connection_symbols(platform) if platform in PROVIDERS else ''}<div class="result-symbol {state}">{_icon(icon)}</div>
<p class="eyebrow">{'READY FOR THE NEXT STEP' if success else 'ACCOUNT CONNECTION'}</p>
<h1 id="page-title">{escape(title)}</h1><p class="description">{description}</p>
<div class="return-panel">{_icon('terminal')}<div><h2>Return to SocialCli</h2><p>{'Choose and confirm your account to finish.' if success else 'Your assistant or terminal can help you start again.'}</p></div></div>
<p class="quiet-notice">{escape(message)}</p>
<a class="text-link" href="/connections.html">Connection help <span aria-hidden="true">↗</span></a>'''
    return _page(title, content, platform=platform)


def error_page(status_code: int) -> str:
    title = ('This link is no longer available' if status_code == 404 else
             'This connection has already started' if status_code == 409 else
             "We couldn't verify this connection" if status_code == 403 else
             "Let's try that again")
    description = ('For your security, connection links expire and can only be used once.'
                   if status_code in {404,409} else 'This connection could not be completed. Your account has not been connected by this request.')
    content = f'''<div class="result-symbol warning">{_icon('refresh')}</div>
<p class="eyebrow">ACCOUNT CONNECTION</p><h1 id="page-title">{escape(title)}</h1>
<p class="description">{description}</p>
<div class="return-panel">{_icon('terminal')}<div><h2>Return to SocialCli</h2><p>Ask your assistant or use your terminal to get a new connection link.</p></div></div>
<a class="secondary-button" href="/connections.html">Connection help {_icon('arrow')}</a>
<p class="expiry-note">No need to share your password or credentials.</p>'''
    return _page(title, content)


class LandingTokens:
    def __init__(self, keys: list[bytes]):
        self.cipher = MultiFernet([Fernet(key) for key in keys])

    def issue(self, authorization_id: str, cookie: str, expires: float) -> str:
        payload = {'purpose': 'socialcli-connect-form-v1', 'id': authorization_id,
                   'browser': hashlib.sha256(cookie.encode()).hexdigest(), 'expires': expires}
        return self.cipher.encrypt(json.dumps(payload).encode()).decode()

    def valid(self, token: str, authorization_id: str, cookie: str, now: float) -> bool:
        try:
            if not 16 <= len(cookie) <= 512 or not 1 <= len(token) <= 4096:
                return False
            data = json.loads(self.cipher.decrypt(token.encode()))
            return (data['purpose'] == 'socialcli-connect-form-v1' and data['id'] == authorization_id
                    and data['expires'] > now
                    and hmac.compare_digest(data['browser'], hashlib.sha256(cookie.encode()).hexdigest()))
        except Exception:
            return False
