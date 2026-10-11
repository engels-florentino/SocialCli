"""Read-only login landing and browser-bound, authenticated form tokens."""
from __future__ import annotations

import hashlib
import hmac
import json
from html import escape

from cryptography.fernet import Fernet, MultiFernet


def landing_page(authorization_id: str, form_token: str) -> str:
    action = escape('/connect/' + authorization_id, quote=True)
    token = escape(form_token, quote=True)
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex,nofollow"><title>Connect · SocialCli</title>'
            '<body><main><h1>Connect your account</h1>'
            '<p>Continue to your provider to sign in and review permissions. '
            'Connecting does not publish content. Keep SocialCli running.</p>'
            f'<form method="post" action="{action}">'
            f'<input type="hidden" name="form_token" value="{token}">'
            '<button type="submit">Connect</button></form>'
            '<p>This link expires in ten minutes. If it has expired or was already used, '
            'restart connect in SocialCli to obtain a new link.</p>'
            '<p><a href="/privacy.html">Privacy</a> · <a href="/terms.html">Terms</a></p>'
            '</main></body></html>')


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
