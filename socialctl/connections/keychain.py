"""Connection capabilities live only in supported OS credential stores."""
from __future__ import annotations

import hashlib

import keyring

SECURE_MODULES = {'keyring.backends.macOS', 'keyring.backends.Windows',
                  'keyring.backends.SecretService', 'keyring.backends.libsecret',
                  'keyring.backends.kwallet'}


def _backend():
    try:
        backend = keyring.get_keyring()
        candidates = backend.backends if type(backend).__module__ == 'keyring.backends.chainer' else [backend]
        for candidate in candidates:
            if type(candidate).__module__ in SECURE_MODULES and candidate.priority > 0:
                return candidate
    except Exception:
        pass
    raise ValueError('a secure OS keyring is required; unlock or configure your system credential store')


def ensure_available():
    _backend()


def _key(brand, platform, metadata):
    root_hash = hashlib.sha256(str(brand.raiz.resolve()).encode()).hexdigest()
    return f'socialcli:{root_hash}', f"{platform.value}:{metadata['service_url']}:{metadata['connection_id']}"


def get(brand, platform, metadata):
    try:
        return _backend().get_password(*_key(brand,platform,metadata))
    except Exception:
        raise ValueError('connection credential unavailable in the secure OS keyring') from None


def save(brand, platform, metadata, secret):
    try:
        backend = _backend()
        key = _key(brand,platform,metadata)
        backend.set_password(*key,secret)
        if backend.get_password(*key) != secret:
            raise ValueError()
    except Exception:
        raise ValueError('could not save the connection in the secure OS keyring') from None


def delete(brand, platform, metadata):
    try:
        backend = _backend()
        key = _key(brand,platform,metadata)
        if backend.get_password(*key) is not None:
            backend.delete_password(*key)
    except Exception:
        raise ValueError('could not delete the connection from the secure OS keyring') from None
