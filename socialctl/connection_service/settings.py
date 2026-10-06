"""Explicit service origin and environment-only provider configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AppCredentials:
    client_id: str
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class Settings:
    public_url: str
    database: Path
    encryption_keys: list[bytes] = field(repr=False)
    providers: dict[str, AppCredentials] = field(default_factory=dict, repr=False)
    allow_http_local: bool = False

    def __post_init__(self):
        parts = urlsplit(self.public_url)
        local = self.allow_http_local and parts.hostname in {'localhost', '127.0.0.1', '::1'}
        if (not parts.hostname or parts.scheme not in ({'https', 'http'} if local else {'https'})
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {'', '/'} or parts.port is not None and not 1 <= parts.port <= 65535):
            raise ValueError('public URL must be a fixed HTTPS origin')
        if not self.encryption_keys:
            raise ValueError('an external encryption key is required')
        object.__setattr__(self, 'public_url', self.public_url.rstrip('/'))
        if not set(self.providers) <= {'youtube', 'facebook', 'instagram', 'tiktok'}:
            raise ValueError('unsupported provider configuration')
        if any(not p.client_id or not p.client_secret for p in self.providers.values()):
            raise ValueError('provider credentials are incomplete')

    def callback(self, platform: str) -> str:
        if platform not in {'youtube', 'facebook', 'instagram', 'tiktok'}:
            raise ValueError('unsupported platform')
        return f'{self.public_url}/oauth/{platform}/callback'

    @classmethod
    def from_environment(cls):
        providers = {}
        for prefix, names in [('GOOGLE', ['youtube']), ('META', ['facebook', 'instagram']), ('TIKTOK', ['tiktok'])]:
            for name in names:
                if os.environ.get(f'SOCIALCLI_ENABLE_{name.upper()}') != '1':
                    continue
                client_id = os.environ.get(f'SOCIALCLI_{prefix}_CLIENT_ID', '')
                client_secret = os.environ.get(f'SOCIALCLI_{prefix}_CLIENT_SECRET', '')
                providers[name] = AppCredentials(client_id, client_secret)
        keys = [x.strip().encode() for x in os.environ.get('SOCIALCLI_ENCRYPTION_KEYS', '').split(',') if x.strip()]
        return cls(public_url=os.environ.get('SOCIALCLI_PUBLIC_URL', ''),
                   database=Path(os.environ.get('SOCIALCLI_DATABASE', '/data/connections.sqlite3')),
                   encryption_keys=keys, providers=providers,
                   allow_http_local=os.environ.get('SOCIALCLI_ALLOW_HTTP_LOCAL') == '1')
