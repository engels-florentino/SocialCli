"""Explicit capability-store selection; no fallback when a backend fails."""
from __future__ import annotations
import json
from typing import Protocol


class CapabilityStore(Protocol):
    def ensure_available(self): ...
    def get(self, brand, platform, metadata): ...
    def save(self, brand, platform, metadata, secret): ...
    def delete(self, brand, platform, metadata): ...


class KeyringStore:
    def ensure_available(self):
        from socialctl.connections.keychain import _backend
        _backend()
    def get(self, *args):
        from socialctl.connections.keychain import _os_get
        return _os_get(*args)
    def save(self, *args):
        from socialctl.connections.keychain import _os_save
        return _os_save(*args)
    def delete(self, *args):
        from socialctl.connections.keychain import _os_delete
        return _os_delete(*args)


def configuration_path(brand):
    return brand.dir_secretos/'credential-store.json'


def store_for(brand) -> CapabilityStore:
    path = configuration_path(brand)
    if not path.exists() and not path.is_symlink():
        return KeyringStore()
    try:
        from socialctl.connections.file_store import protected_parent, read_file, EncryptedFileStore
        with protected_parent(path) as (parent,name):
            config = json.loads(read_file(parent,name))
        if config == {'version':1,'backend':'keyring'}:
            return KeyringStore()
        if config.get('version') != 1 or config.get('backend') != 'encrypted-file' or set(config) != {'version','backend','store_file','key_file'}:
            raise ValueError()
        from socialctl.connections.credential_cli import external_paths
        path,key=external_paths(config['store_file'],config['key_file'],brand.raiz.parent)
        return EncryptedFileStore(path,key)
    except Exception:
        raise ValueError('secure credential store configuration is invalid or unsafe') from None
