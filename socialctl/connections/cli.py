"""Browser account connection with explicit binding and secure local capabilities."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import secrets
import sys
import math
import tempfile
import time
import webbrowser
from pathlib import Path

import httpx
import typer
import yaml

from socialctl.brands import cargar_brand
from socialctl.connections import keychain
from socialctl.connections.client import BrokerClient,DEFAULT_SERVICE,opaque,visible
from socialctl.models import Platform
from socialctl.workspace import default_root


def interactive_terminal():
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def http_client():
    return httpx.Client(timeout=30,follow_redirects=False)


def atomic_bytes(path:Path,content:bytes,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def acquire_brand_lock(brand):
    brand.dir_secretos.mkdir(exist_ok=True,mode=0o700)
    brand.dir_secretos.chmod(0o700)
    fd=os.open(brand.dir_secretos/'connection.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise ValueError('another account connection is in progress for this brand') from None
    return fd


def validate_binding(brand,platform,account):
    value=yaml.safe_load((brand.raiz/'accounts.yml').read_text()) or {}
    current_page=(value.get('facebook') or {}).get('page_id')
    requested=account.get('page_id') if platform is Platform.INSTAGRAM else account.get('id')
    other=Platform.FACEBOOK if platform is Platform.INSTAGRAM else Platform.INSTAGRAM
    if platform in (Platform.FACEBOOK,Platform.INSTAGRAM) and brand.leer_secreto(other) and str(current_page)!=str(requested):
        raise ValueError('Facebook Page differs from an existing linked connection; disconnect that connection or use another brand')


def bind_account(brand,platform,account):
    validate_binding(brand,platform,account)
    path=brand.raiz/'accounts.yml'
    text=path.read_text()
    value=yaml.safe_load(text) or {}
    if not isinstance(value,dict):
        raise ValueError('brand account configuration is invalid')
    field={'youtube':'channel_id','facebook':'page_id','instagram':'ig_user_id','tiktok':'open_id'}[platform.value]
    updates={platform.value:{field:account['id']}}
    if platform is Platform.INSTAGRAM:
        updates['facebook']={'page_id':opaque(account.get('page_id'))}
    if platform is Platform.TIKTOK:
        updates['tiktok'].update(mode='inbox',auditada=False)
    import re
    # Rewrite only affected top-level sections; unrelated sections and their
    # comments remain intact. Values from discovery are safely YAML-serialized.
    for section,fields in updates.items():
        current=value.get(section) or {}
        if not isinstance(current,dict):
            raise ValueError('brand account configuration is invalid')
        current.update(fields)
        pattern=re.compile(r'^'+re.escape(section)+r':[^\n]*\n(?:[ \t].*\n|\n|#[^\n]*\n)*',re.MULTILINE)
        replacement=yaml.safe_dump({section:current},sort_keys=False)
        match=pattern.search(text)
        if match:
            text=text[:match.start()]+replacement+text[match.end():]
        else:
            text+='\n'+replacement
    atomic_bytes(path,text.encode(),path.stat().st_mode&0o777)


def fail(message):
    typer.echo(message,err=True)
    raise typer.Exit(1)


def register(app):
    @app.command('connect')
    def connect(platform:Platform,
                brand_name:str=typer.Option(...,'--brand'),
                root:Path=typer.Option(default_root(),'--root'),
                service:str=typer.Option(DEFAULT_SERVICE,'--service',help='HTTPS connection service.'),
                no_browser:bool=typer.Option(False,'--no-browser',help='AI agents: print the single-use login link without opening it; give it directly to the user.'),
                account_id:str|None=typer.Option(None,'--account-id',help='Exact account ID to bind; required with --yes.'),
                yes:bool=typer.Option(False,'--yes',help='Confirm only the explicitly authorized --account-id connection.'),
                replace_independent:bool=typer.Option(False,'--replace-independent',help='Explicitly permit replacing independent credentials with --yes.'),
                management:bool=typer.Option(False,'--management',help='YouTube: metadata management; Facebook/Instagram: comments and webhook permissions.'),
                analytics:bool=typer.Option(False,'--analytics',help='YouTube, Facebook or Instagram: request analytics read access.'),
                dev_local:bool=typer.Option(False,'--dev-local',help='Development only: permit a loopback HTTP service.')):
        """Connect your own social account in the browser without developer credentials."""
        auth_id=None
        connected=None
        metadata=None
        lock_fd=None
        try:
            brand=cargar_brand(root,brand_name)
            lock_fd=acquire_brand_lock(brand)
            if management and platform is Platform.TIKTOK:
                raise ValueError('TikTok management permission is not available in this connection flow')
            if analytics and platform is Platform.TIKTOK:
                raise ValueError('TikTok analytics permission is not available in this connection flow')
            previous=brand.leer_secreto(platform)
            if previous.get('auth_mode')=='broker':
                raise ValueError('this platform already has a connection; disconnect it before reconnecting')
            if (yes and not account_id) or (not (yes and account_id) and not interactive_terminal()):
                raise ValueError('Noninteractive connection requires --account-id ID and --yes after explicit user authorization')
            if account_id is not None:
                opaque(account_id)
            if replace_independent and not yes:
                raise ValueError('--replace-independent requires --account-id ID and --yes')
            if previous and yes and not replace_independent:
                raise ValueError('Replacing independent credentials requires explicit --replace-independent')
            keychain.ensure_available()
            poll_secret=secrets.token_urlsafe(32)
            with http_client() as transport:
                broker=BrokerClient(service,transport,allow_http_local=dev_local)
                start=broker.request('POST','/v1/authorizations',body={'platform':platform.value,
                    'poll_challenge':hashlib.sha256(poll_secret.encode()).hexdigest(),
                    'management':management,'analytics':analytics})
                auth_id=opaque(start.get('authorization_id'))
                expected_url=broker.origin+'/connect/'+auth_id
                if start.get('browser_url')!=expected_url:
                    raise ValueError('invalid authorization browser URL')
                if no_browser:
                    typer.echo(f'Give this single-use link directly to the user to connect {platform.value}: {expected_url}')
                    typer.echo('Do not open, fetch or preview this link. The user must open it and press Connect if shown. It expires in 10 minutes.')
                    typer.echo('Waiting for the user to authorize. Keep this command running. No content will be published.')
                else:
                    typer.echo(f'Opening your browser to connect {platform.value}. No content will be published.')
                    typer.echo(f'If it does not open, visit: {expected_url}')
                    webbrowser.open(expected_url)
                expiry=start.get('expires_in',600)
                interval=start.get('poll_interval',2)
                if (type(expiry) not in {int,float} or not math.isfinite(expiry) or not 0<expiry<=600
                        or type(interval) not in {int,float} or not math.isfinite(interval) or not 0<interval<=60):
                    raise ValueError('invalid authorization timing')
                sys.stdout.flush()
                deadline=time.monotonic()+expiry
                while True:
                    result=broker.request('POST',f'/v1/authorizations/{auth_id}/poll',secret=poll_secret)
                    if result.get('status')=='ready':
                        break
                    if result.get('status') in {'denied','failed'}:
                        raise ValueError('authorization cancelled or incomplete; check permissions and retry')
                    if result.get('status') not in {'pending','exchanging'} or time.monotonic()>=deadline:
                        raise ValueError('authorization timed out; retry connecting your account')
                    time.sleep(min(interval,max(0,deadline-time.monotonic())))
                accounts=result.get('accounts')
                if not isinstance(accounts,list) or not 1<=len(accounts)<=500:
                    raise ValueError('no eligible authorized accounts were returned')
                for index,account in enumerate(accounts,1):
                    opaque(account.get('id'))
                    typer.echo(f"{index}. {visible(account.get('name',''))} ({account['id']})")
                if account_id is not None:
                    matches=[i for i,a in enumerate(accounts,1) if a['id']==account_id]
                    if len(matches)!=1:
                        raise ValueError('requested account was not uniquely returned by the provider; no connection installed')
                    selection=matches[0]
                else:
                    selection=1 if len(accounts)==1 else typer.prompt('Choose an account number',type=int)
                if not 1<=selection<=len(accounts):
                    raise ValueError('invalid account selection')
                account=accounts[selection-1]
                validate_binding(brand,platform,account)
                if previous:
                    typer.echo('Confirming will replace the existing independent-app credentials for this platform.')
                if platform is Platform.INSTAGRAM:
                    typer.echo(f"Linked Facebook Page: {opaque(account.get('page_id'))}")
                if platform is Platform.TIKTOK:
                    typer.echo('This connection uses inbox mode; finish posting in TikTok.')
                if not yes and not typer.confirm(f"Connect {visible(account.get('name',''))} ({account['id']}) to {visible(brand.nombre)}?",default=False):
                    broker.request('DELETE',f'/v1/authorizations/{auth_id}',secret=poll_secret,allow_missing=True)
                    auth_id=None
                    typer.echo('Connection cancelled. Existing credentials were preserved.')
                    return
                connected=broker.request('POST',f'/v1/authorizations/{auth_id}/complete',secret=poll_secret,body={'account_id':account['id']})
                auth_id=None
                if connected.get('platform')!=platform.value or connected.get('account')!=account:
                    raise ValueError('completed account differs from the account you confirmed')
                opaque(connected.get('connection_id'))
                capability=connected.get('connection_secret')
                if not isinstance(capability,str) or not 16<=len(capability)<=512:
                    raise ValueError('invalid connection credential')
                metadata={'version':1,'auth_mode':'broker','service_url':broker.origin,
                          'connection_id':connected['connection_id'],'account_id':account['id'],
                          'account':account,'granted_scopes':connected.get('granted_scopes',[])}
                if dev_local:
                    metadata['development_local']=True
                config_path=brand.raiz/'accounts.yml'
                secret_path=brand.dir_secretos/f'{platform.value}.json'
                original_config=config_path.read_bytes()
                original_secret=secret_path.read_bytes() if secret_path.exists() else None
                try:
                    keychain.save(brand,platform,metadata,capability)
                    bind_account(brand,platform,account)
                    brand.dir_secretos.mkdir(exist_ok=True,mode=0o700)
                    brand.dir_secretos.chmod(0o700)
                    atomic_bytes(secret_path,json.dumps(metadata,indent=2).encode())
                    # Activate only after both keyring and local binding are durable.
                    # The returned provider token is discarded, never persisted.
                    broker.request('POST',f"/v1/connections/{metadata['connection_id']}/token",secret=capability)
                except BaseException:
                    try:
                        atomic_bytes(config_path,original_config,config_path.stat().st_mode&0o777)
                        if original_secret is None:
                            secret_path.unlink(missing_ok=True)
                        else:
                            atomic_bytes(secret_path,original_secret)
                    except BaseException:
                        raise ValueError('Credential restoration could not be completed; restore the brand from your private backup before retrying') from None
                    raise
                typer.echo(f'{platform.value} connected for {visible(brand.nombre)}. Credentials are held in your OS keyring.')
                connected=None  # Successfully installed: do not cancel in finally.
        except (KeyboardInterrupt,typer.Abort):
            fail('Connection cancelled.')
        except ValueError as exc:
            fail(str(exc))
        except Exception:
            fail('Could not connect safely; existing credentials were preserved.')
        finally:
            if auth_id or connected:
                try:
                    with http_client() as transport:
                        cleanup=BrokerClient(service,transport,allow_http_local=dev_local)
                        if connected and connected.get('connection_id') and connected.get('connection_secret'):
                            cleanup.request('DELETE',f"/v1/connections/{opaque(connected['connection_id'])}",secret=connected['connection_secret'],allow_missing=True)
                        elif auth_id:
                            cleanup.request('DELETE',f'/v1/authorizations/{auth_id}',secret=poll_secret,allow_missing=True)
                    if metadata:
                        keychain.delete(brand,platform,metadata)
                except Exception:
                    typer.echo('Cleanup could not be confirmed. Contact the service operator before retrying.',err=True)
            if lock_fd is not None:
                os.close(lock_fd)

    @app.command('connections')
    def connections(brand_name:str=typer.Option(...,'--brand'),
                    root:Path=typer.Option(default_root(),'--root'),
                    json_output:bool=typer.Option(False,'--json')):
        """Read connection status without renewing provider tokens or publishing."""
        try:
            brand=cargar_brand(root,brand_name)
            rows=[]
            with http_client() as transport:
                for platform in Platform:
                    metadata=brand.leer_secreto(platform)
                    if metadata.get('auth_mode')!='broker':
                        continue
                    broker=BrokerClient(metadata['service_url'],transport,allow_http_local=metadata.get('development_local') is True)
                    secret=keychain.get(brand,platform,metadata)
                    status=broker.request('GET',f"/v1/connections/{opaque(metadata['connection_id'])}",secret=secret)
                    if status.get('platform')!=platform.value or status.get('account',{}).get('id')!=metadata['account_id']:
                        raise ValueError('connection account mismatch')
                    rows.append(status)
            result={'version':1,'brand':brand.nombre,'connections':rows}
            if json_output:
                typer.echo(json.dumps(result,indent=2))
            elif not rows:
                typer.echo('No shared connections. Independent-app credentials use socialcli auth status.')
            else:
                for row in rows:
                    typer.echo(f"{row['platform']}: {visible(row['account']['name'])} ({row['account']['id']})"+(' — reconnect required' if row.get('needs_reconnect') else ' — connected'))
        except ValueError as exc:
            fail(str(exc))
        except Exception:
            fail('Could not read connection status safely.')

    @app.command('disconnect')
    def disconnect(platform:Platform,brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root')):
        """Delete a shared connection and its local capability after confirmation."""
        lock_fd=None
        try:
            brand=cargar_brand(root,brand_name)
            lock_fd=acquire_brand_lock(brand)
            metadata=brand.leer_secreto(platform)
            if metadata.get('auth_mode')!='broker':
                raise ValueError('no shared connection exists for this platform')
            if not typer.confirm(f'Disconnect {platform.value} from {visible(brand.nombre)}?',default=False):
                return
            secret=keychain.get(brand,platform,metadata)
            with http_client() as transport:
                broker=BrokerClient(metadata['service_url'],transport,allow_http_local=metadata.get('development_local') is True)
                broker.request('DELETE',f"/v1/connections/{opaque(metadata['connection_id'])}",secret=secret,allow_missing=True)
            keychain.delete(brand,platform,metadata)
            (brand.dir_secretos/f'{platform.value}.json').unlink(missing_ok=True)
            typer.echo('Disconnected. To also revoke provider authorization, remove SocialCli in the provider account settings.')
        except ValueError as exc:
            fail(str(exc))
        except Exception:
            fail('Could not confirm disconnection; retry after checking service and keyring availability.')

        finally:
            if lock_fd is not None:
                os.close(lock_fd)
