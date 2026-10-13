"""Explicit SaaS configuration, separate from provider and local CLI credentials."""
from dataclasses import dataclass, field
from urllib.parse import urlsplit
import os


@dataclass(frozen=True)
class SaaSSettings:
    auth_url: str
    auth_key: str = field(default='', repr=False)
    allow_internal_auth: bool = False

    def __post_init__(self):
        url = urlsplit(self.auth_url)
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or url.scheme != 'https' and not (self.allow_internal_auth and url.scheme == 'http')):
            raise ValueError('Auth requires an HTTPS URL or explicit private-network HTTP configuration')
        object.__setattr__(self, 'auth_url', self.auth_url.rstrip('/'))

    @classmethod
    def from_environment(cls):
        if os.environ.get('SOCIALCLI_SAAS_ENABLED') != '1':
            return None
        return cls(auth_url=os.environ.get('SOCIALCLI_AUTH_URL',''),
                   auth_key=os.environ.get('SOCIALCLI_AUTH_KEY',''),
                   allow_internal_auth=os.environ.get('SOCIALCLI_AUTH_INTERNAL_HTTP') == '1')
