"""Explicit authenticated capability storage for Linux/macOS headless clients."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

LIMIT = 4 * 1024 * 1024


@contextmanager
def protected_parent(path):
    """Walk without following symlinks; pin the protected immediate directory."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('credential paths must be absolute without parent traversal')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('credential parent directory must be owned by this user with mode 0700')
        yield fd, path.name
    finally:
        os.close(fd)


def checked_fd(parent, name, *, create=False):
    if create:
        try:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        except FileExistsError:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
    else:
        fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise ValueError('credential files must be regular, owned by this user, mode 0600 and not hard-linked')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_file(parent, name):
    fd = checked_fd(parent, name)
    try:
        raw = os.read(fd, LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError('credential file exceeds size limit')
        return raw
    finally:
        os.close(fd)


def replace_file(parent, name, raw):
    # Check existing destination without following aliases before replacement.
    try:
        fd = checked_fd(parent, name)
    except FileNotFoundError:
        pass
    else:
        os.close(fd)
    temporary = '.' + name + '-' + secrets.token_hex(12)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try: os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError: pass


class EncryptedFileStore:
    def __init__(self, path, key_path):
        self.path, self.key_path = Path(path), Path(key_path)
        if self.path == self.key_path:
            raise ValueError('master key and encrypted store must be different files')

    def cipher(self):
        try:
            from cryptography.fernet import Fernet
            with protected_parent(self.key_path) as (parent, name):
                return Fernet(read_file(parent, name).strip())
        except Exception:
            raise ValueError('encrypted credentials require a protected valid external key and the headless-credentials extra') from None

    @contextmanager
    def locked(self):
        try:
            with protected_parent(self.path) as (parent, name):
                fd = checked_fd(parent, name+'.lock', create=True)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    yield parent, name
                finally:
                    os.close(fd)
        except Exception:
            raise ValueError('secure encrypted credential store unavailable, unsafe or corrupt') from None

    def _load(self, parent, name, cipher):
        try:
            raw = read_file(parent, name)
        except FileNotFoundError:
            return {'version':1, 'records':{}}
        envelope = json.loads(raw)
        if envelope.get('version') != 1:
            raise ValueError('unsupported encrypted credential version')
        data = json.loads(cipher.decrypt(envelope['ciphertext'].encode()))
        if data.get('version') != 1 or not isinstance(data.get('records'), dict):
            raise ValueError('invalid encrypted credential envelope')
        return data

    def _write(self, parent, name, data, cipher):
        raw = json.dumps({'version':1,'ciphertext':cipher.encrypt(json.dumps(data,sort_keys=True).encode()).decode()}).encode()
        if len(raw) > LIMIT:
            raise ValueError('credential store exceeds size limit')
        replace_file(parent, name, raw)

    def ensure_available(self):
        cipher = self.cipher()
        with self.locked() as (parent,name):
            self._load(parent,name,cipher)

    @staticmethod
    def bind_data(data, brand):
        root = str(brand.raiz.resolve())
        if data.get('brand_root') not in {None, root} or any(
                record.get('binding', {}).get('brand') != root for record in data['records'].values()):
            raise ValueError('one encrypted store belongs to exactly one brand; use a different store file')
        data['brand_root'] = root
        return data

    def claim(self, brand):
        cipher = self.cipher()
        with self.locked() as (parent,name):
            data = self.bind_data(self._load(parent,name,cipher), brand)
            self._write(parent,name,data,cipher)

    @staticmethod
    def binding(brand, platform, metadata):
        return {'version':1, 'brand':str(brand.raiz.resolve()), 'platform':platform.value,
            'service_url':metadata['service_url'], 'connection_id':metadata['connection_id'],
            'account_id':metadata['account_id']}

    def _record(self, brand, platform, metadata):
        binding = self.binding(brand,platform,metadata)
        return hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest(), binding

    def get(self, brand, platform, metadata):
        key, binding = self._record(brand,platform,metadata)
        cipher = self.cipher()
        with self.locked() as (parent,name):
            record = self.bind_data(self._load(parent,name,cipher), brand)['records'].get(key)
            if record is None:
                return None
            if record.get('binding') != binding or not isinstance(record.get('secret'), str):
                raise ValueError('credential binding mismatch')
            return record['secret']

    def save(self, brand, platform, metadata, secret):
        if not isinstance(secret,str) or not secret or len(secret) > 8192:
            raise ValueError('invalid connection capability')
        key, binding = self._record(brand,platform,metadata)
        cipher = self.cipher()
        with self.locked() as (parent,name):
            data = self.bind_data(self._load(parent,name,cipher), brand)
            data['records'][key] = {'binding':binding,'secret':secret}
            self._write(parent,name,data,cipher)
            if self._load(parent,name,cipher) != data:
                raise ValueError('credential save verification failed')

    def delete(self, brand, platform, metadata):
        key, _ = self._record(brand,platform,metadata)
        cipher = self.cipher()
        with self.locked() as (parent,name):
            data = self.bind_data(self._load(parent,name,cipher), brand)
            data['records'].pop(key,None)
            self._write(parent,name,data,cipher)
