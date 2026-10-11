"""Explicit secure-store setup, verified migration and recoverable key rotation."""
from __future__ import annotations

import json
from pathlib import Path
import typer

from socialctl.brands import cargar_brand
from socialctl.connections.credential_store import store_for, KeyringStore, configuration_path
from socialctl.connections.file_store import EncryptedFileStore, protected_parent, read_file, replace_file
from socialctl.models import Platform
from socialctl.workspace import default_root

credentials_app = typer.Typer(help='Configure explicit capability storage; provider credentials stay on the service.')


def write_configuration(brand, config):
    path=configuration_path(brand)
    path.parent.mkdir(mode=0o700,exist_ok=True)
    with protected_parent(path) as (parent,name):
        replace_file(parent,name,json.dumps(config,sort_keys=True).encode())


def external_paths(store_file,key_file,workspace=None):
    if store_file is None or key_file is None:
        raise ValueError('encrypted-file requires --store-file and --key-file')
    paths=[Path(store_file).absolute(),Path(key_file).absolute()]
    if paths[1] in {paths[0],paths[0].with_name(paths[0].name+'.lock'),paths[0].with_name(paths[0].name+'.before-rotation')}:
        raise ValueError('key must not alias the encrypted store, lock or recovery file')
    for path in paths:
        if workspace is not None and path.is_relative_to(Path(workspace).resolve()):
            raise ValueError('credential key and store must be outside the creator workspace')
        if any((parent/'.git').exists() for parent in path.parents):
            raise ValueError('credential key and store must be outside Git repositories')
        with protected_parent(path):
            pass
    if paths[0] == paths[1]:
        raise ValueError('key and store must be different files')
    if all(p.exists() for p in paths) and paths[0].samefile(paths[1]):
        raise ValueError('key and store must not alias the same file')
    return paths


def destination(backend,store_file=None,key_file=None,workspace=None):
    if backend=='keyring':
        return KeyringStore(),{'version':1,'backend':'keyring'}
    if backend!='encrypted-file':
        raise ValueError('backend must be keyring or encrypted-file')
    path,key=external_paths(store_file,key_file,workspace)
    store=EncryptedFileStore(path,key)
    store.ensure_available()
    return store,{'version':1,'backend':backend,'store_file':str(path),'key_file':str(key)}


def confirm(yes,message):
    typer.echo(message)
    if not yes and not typer.confirm('Apply this credential-store change?',default=False):
        raise typer.Exit(1)


def fail(exc):
    # Backend errors must never include capabilities or key material.
    typer.echo('Secure credential operation failed. Existing source credentials remain available; check protected paths, backend configuration and recovery backup.',err=True)
    raise typer.Exit(1)


@credentials_app.command('status')
def status(brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root')):
    try:
        selected=cargar_brand(root,brand)
        store=store_for(selected)
        store.ensure_available()
        value={'version':1,'backend':'encrypted-file' if isinstance(store,EncryptedFileStore) else 'keyring','available':True}
        if isinstance(store,EncryptedFileStore):
            value.update(store_file=str(store.path),key_file=str(store.key_path))
        typer.echo(json.dumps(value))
    except Exception as exc: fail(exc)


@credentials_app.command('configure')
def configure(backend:str=typer.Option(...,'--backend'),store_file:Path|None=typer.Option(None,'--store-file'),key_file:Path|None=typer.Option(None,'--key-file'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root'),yes:bool=typer.Option(False,'--yes')):
    try:
        from socialctl.connections.cli import acquire_brand_lock
        import os
        selected=cargar_brand(root,brand)
        fd=acquire_brand_lock(selected)
        try:
            if any(selected.leer_secreto(p).get('auth_mode')=='broker' for p in Platform):
                raise ValueError('existing connections require credentials migrate')
            dest,config=destination(backend,store_file,key_file,selected.raiz.parent)
            dest.ensure_available()
            confirm(yes,'Select '+backend+' capability storage. Keep the external key separate from backups and Git; losing it makes capabilities unrecoverable.')
            if isinstance(dest,EncryptedFileStore): dest.claim(selected)
            write_configuration(selected,config)
        finally: os.close(fd)
        typer.echo('Credential store configured. No account authorization or publication occurred.')
    except (OSError,ValueError) as exc: fail(exc)


@credentials_app.command('migrate')
def migrate(to:str=typer.Option(...,'--to'),store_file:Path|None=typer.Option(None,'--store-file'),key_file:Path|None=typer.Option(None,'--key-file'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root'),yes:bool=typer.Option(False,'--yes')):
    try:
        from socialctl.connections.cli import acquire_brand_lock
        import os
        selected=cargar_brand(root,brand)
        fd=acquire_brand_lock(selected)
        try:
            source=store_for(selected)
            dest,config=destination(to,store_file,key_file,selected.raiz.parent)
            if type(source) is type(dest):
                raise ValueError('use rotate for keys; migration requires another backend')
            source.ensure_available();dest.ensure_available()
            records=[]
            for p in Platform:
                meta=selected.leer_secreto(p)
                if meta.get('auth_mode')=='broker':
                    capability=source.get(selected,p,meta)
                    if not capability: raise ValueError('source capability missing')
                    records.append((p,meta,capability))
            confirm(yes,f'Migrate {len(records)} connection capabilities to {to}; verify destination before switching or removing source.')
            if isinstance(dest,EncryptedFileStore): dest.claim(selected)
            for p,meta,capability in records:
                existing = dest.get(selected,p,meta)
                if existing is not None and existing != capability: raise ValueError('destination capability conflicts with source')
                dest.save(selected,p,meta,capability)
                if dest.get(selected,p,meta)!=capability: raise ValueError('destination verification failed')
            write_configuration(selected,config)
            cleanup_failed=False
            for p,meta,_ in records:
                try: source.delete(selected,p,meta)
                except ValueError: cleanup_failed=True
        finally: os.close(fd)
        typer.echo('Migration verified; destination is active.' + (' Source cleanup incomplete; remove redundant source capabilities after confirming destination.' if cleanup_failed else ' Source capabilities removed.'))
    except (OSError,ValueError) as exc: fail(exc)


@credentials_app.command('rotate')
def rotate(new_key_file:Path=typer.Option(...,'--new-key-file'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root'),yes:bool=typer.Option(False,'--yes')):
    try:
        from socialctl.connections.cli import acquire_brand_lock
        import os
        selected=cargar_brand(root,brand)
        fd=acquire_brand_lock(selected)
        try:
            old=store_for(selected)
            if not isinstance(old,EncryptedFileStore): raise ValueError('rotation requires encrypted-file')
            path,key=external_paths(old.path,new_key_file,selected.raiz.parent)
            if key==old.key_path or key.samefile(old.key_path): raise ValueError('rotation requires a different key file')
            new=EncryptedFileStore(path,key)
            new_cipher=new.cipher()
            old_cipher=old.cipher()
            confirm(yes,'Rotate all encrypted capabilities. Retain the old key and protected .before-rotation backup until recovery is no longer needed.')
            with old.locked() as (parent,name):
                data=old.bind_data(old._load(parent,name,old_cipher), selected)
                config_path=configuration_path(selected)
                with protected_parent(config_path) as (config_parent,config_name):
                    original_config=read_file(config_parent,config_name)
                original=read_file(parent,name)
                backup=name+'.before-rotation'
                try:
                    # Do not overwrite an earlier recovery generation.
                    read_file(parent,backup)
                except FileNotFoundError: pass
                else: raise ValueError('previous rotation backup must be archived before another rotation')
                replace_file(parent,backup,original)
                try:
                    new._write(parent,name,data,new_cipher)
                    if new._load(parent,name,new_cipher)!=data: raise ValueError('rotation verification failed')
                    write_configuration(selected,{'version':1,'backend':'encrypted-file','store_file':str(path),'key_file':str(key)})
                except BaseException:
                    replace_file(parent,name,original)
                    with protected_parent(config_path) as (config_parent,config_name):
                        replace_file(config_parent,config_name,original_config)
                    os.unlink(backup,dir_fd=parent)
                    raise
        finally: os.close(fd)
        typer.echo('Key rotation verified. Protected recovery backup retained; keep the old key separately.')
    except (OSError,ValueError) as exc: fail(exc)
