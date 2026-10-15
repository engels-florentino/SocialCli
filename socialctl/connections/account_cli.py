"""Installation sign-in and safe connection installation for terminal AI agents."""
import base64
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import time
import webbrowser
import httpx
import typer
from socialctl.brands import cargar_brand
from socialctl.connections import keychain
from socialctl.connections.client import opaque,visible,service_origin
from socialctl.connections.saas_client import SaaSClient,ServiceRejected,TransportFailure
from socialctl.connections.cli import atomic_bytes,acquire_brand_lock,bind_account,validate_binding
from socialctl.models import Platform
from socialctl.workspace import default_root

DEFAULT_SAAS='https://socialcli.app'
account_app=typer.Typer(help='Sign in to SocialCli and manage this agent installation.')
Slot=Enum('Slot',{'ACCOUNT':'socialcli_account','PAIR':'socialcli_pair'})


def http_client():
    from socialctl.read_budget import LocalConfigurationError
    try:
        return httpx.Client(timeout=30,follow_redirects=False)
    except (httpx.InvalidURL,ValueError,OSError,ImportError):
        raise LocalConfigurationError('Check local proxy, CA and HTTP transport configuration; no request was sent.') from None


def metadata(brand):
    path=brand.dir_secretos/'socialcli-account.json'
    data=json.loads(path.read_text()) if path.exists() else {}
    if data and (data.get('version')!=1 or data.get('auth_mode')!='saas-installation'):
        raise ValueError('Invalid SocialCli installation metadata.')
    return data


def handoff(url,*,no_browser,seconds):
    typer.echo('Give this link directly to the user: '+url,err=True)
    typer.echo(f'Do not open, fetch or preview it. Keep this command running. The user has {seconds//60} minutes to approve. No content will be published.',err=True)
    if not no_browser: webbrowser.open(url)


def wait(client,path,secret,seconds,interval,ready):
    if type(seconds) not in {int,float} or not math.isfinite(seconds) or not 0<seconds<=1800 or type(interval) not in {int,float} or not math.isfinite(interval) or not 0<interval<=60:
        raise ValueError('Invalid SocialCli authorization timing.')
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=client.request('POST',path,secret=secret)
        if result.get('status') in ready: return result
        if result.get('status') in {'failed','denied','cancelled','consumed'}:
            raise ValueError('Authorization was cancelled or unavailable. Request a new link.')
        time.sleep(min(interval,max(0,deadline-time.monotonic())))
    raise ValueError('Authorization timed out. Request a new link.')


def output(value,json_output):
    if json_output: typer.echo(json.dumps(value))
    else: typer.echo(value['status'].replace('_',' ').capitalize()+'.')


def finish_pending_pair(client,brand,installation_id):
    """Finish delivery cleanup only after this durable installation is verified."""
    path=brand.dir_secretos/'socialcli-pairing.json'
    if not path.exists():return
    pending=json.loads(path.read_text())
    if pending['service_url']!=client.origin:
        raise ValueError('A pairing is pending for another service.')
    secret=keychain.get(brand,Slot.PAIR,pending)
    if secret is not None:
        pair_id=opaque(pending['connection_id'])
        try:
            result=client.request('POST','/api/saas/v1/pairings/'+pair_id+'/poll',secret=secret)
            if result.get('installation_id')!=installation_id or result.get('status') not in {'approved','consumed'}:
                raise ValueError('Pending pairing does not match the saved installation.')
            client.request('POST','/api/saas/v1/pairings/'+pair_id+'/ack',secret=secret)
        except ServiceRejected as exc:
            if exc.status!=404:raise  # Expired delivery cannot be replayed.
    # Missing proof can result from a crash between secret deletion and unlink.
    # The installation itself was authenticated; no new grant is inferred here.
    keychain.delete(brand,Slot.PAIR,pending)
    path.unlink(missing_ok=True)


@account_app.command('login')
def login(brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root'),
          service:str=typer.Option(DEFAULT_SAAS,'--service'),label:str=typer.Option('AI assistant','--label'),
          no_browser:bool=typer.Option(False,'--no-browser'),json_output:bool=typer.Option(False,'--json'),
          timeout:float=typer.Option(1800,'--timeout',min=1,max=1800),
          dev_local:bool=typer.Option(False,'--dev-local')):
    """Link this installation after the user signs in and approves in their browser."""
    lock=None
    try:
        brand=cargar_brand(root,brand_name);lock=acquire_brand_lock(brand);keychain.ensure_available(brand)
        with http_client() as transport:
            client=SaaSClient(service,transport,allow_http_local=dev_local)
            previous=metadata(brand)
            if previous:
                if previous['service_url']!=client.origin:
                    raise ValueError('Sign out of the existing SocialCli service before changing services.')
                try:
                    status=client.request('GET','/api/saas/v1/installations/'+opaque(previous['connection_id']),secret=keychain.get(brand,Slot.ACCOUNT,previous))
                except ServiceRejected as exc:
                    if exc.status!=401: raise
                else:
                    finish_pending_pair(client,brand,status['installation_id'])
                    output({'version':1,'status':'signed_in','installation_id':status['installation_id']},json_output);return
            pending_path=brand.dir_secretos/'socialcli-pairing.json'
            pending=json.loads(pending_path.read_text()) if pending_path.exists() else None
            if pending and pending['service_url']!=client.origin: raise ValueError('A pairing is pending for another service.')
            if pending:
                poll_secret=keychain.get(brand,Slot.PAIR,pending)
                pair_id=opaque(pending['connection_id']);start=pending['start']
            else:
                poll_secret=secrets.token_urlsafe(32)
                start=client.request('POST','/api/saas/v1/installations/pair',body={'label':label,'poll_challenge':hashlib.sha256(poll_secret.encode()).hexdigest()})
                pair_id=opaque(start.get('pairing_id'))
                pending={'version':1,'service_url':client.origin,'connection_id':pair_id,'start':start}
                keychain.save(brand,Slot.PAIR,pending,poll_secret)
                atomic_bytes(pending_path,json.dumps(pending).encode())
            expected=client.origin+'/account/pair/'+pair_id
            if start.get('browser_url')!=expected: raise ValueError('Invalid SocialCli sign-in URL.')
            handoff(expected,no_browser=no_browser,seconds=1800)
            try:
                result=wait(client,'/api/saas/v1/pairings/'+pair_id+'/poll',poll_secret,min(timeout,start['expires_in']),start['poll_interval'],{'approved'})
            except ServiceRejected as exc:
                if exc.status==404:
                    pending_path.unlink(missing_ok=True);keychain.delete(brand,Slot.PAIR,pending)
                raise
            installation_id=opaque(result.get('installation_id'));secret=result.get('installation_secret')
            if not isinstance(secret,str) or not 16<=len(secret)<=512: raise ValueError('Invalid installation credential.')
            saved={'version':1,'auth_mode':'saas-installation','service_url':client.origin,'connection_id':installation_id}
            if dev_local:saved['development_local']=True
            keychain.save(brand,Slot.ACCOUNT,saved,secret)
            atomic_bytes(brand.dir_secretos/'socialcli-account.json',json.dumps(saved).encode())
            # Credentials and metadata are durable before acknowledging delivery.
            client.request('POST','/api/saas/v1/pairings/'+pair_id+'/ack',secret=poll_secret)
            keychain.delete(brand,Slot.PAIR,pending);pending_path.unlink(missing_ok=True)
            if previous and previous['connection_id']!=installation_id:keychain.delete(brand,Slot.ACCOUNT,previous)
            output({'version':1,'status':'signed_in','installation_id':installation_id},json_output)
    except (KeyboardInterrupt,typer.Abort):
        typer.echo('Sign-in interrupted. Run the same command to resume.',err=True);raise typer.Exit(1)
    except Exception as exc:
        if json_output:
            from socialctl.agent_errors import emit_error
            emit_error(exc, read_only=False)
        typer.echo(str(exc) if isinstance(exc,ValueError) else 'Could not sign in safely. Your pending sign-in can be resumed.',err=True);raise typer.Exit(1)
    finally:
        if lock is not None:os.close(lock)


@account_app.command('status')
def status(brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root'),json_output:bool=typer.Option(False,'--json')):
    """Read this installation without refreshing provider tokens."""
    try:
        brand=cargar_brand(root,brand_name);saved=metadata(brand)
        if not saved: raise ValueError('Not signed in. Run socialcli account login.')
        with http_client() as transport:
            client=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
            result=client.request('GET','/api/saas/v1/installations/'+opaque(saved['connection_id']),secret=keychain.get(brand,Slot.ACCOUNT,saved))
        output({**result,'status':'signed_in'},json_output)
    except Exception as exc:
        if json_output:
            from socialctl.agent_errors import emit_error
            emit_error(exc, read_only=True)
        typer.echo(str(exc) if isinstance(exc,ValueError) else 'Could not read SocialCli installation status.',err=True);raise typer.Exit(1)


@account_app.command('logout')
def logout(brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root')):
    """Revoke this agent installation; other installations keep their access."""
    lock=None
    try:
        brand=cargar_brand(root,brand_name);lock=acquire_brand_lock(brand);saved=metadata(brand)
        if not saved: raise ValueError('Not signed in.')
        with http_client() as transport:
            client=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
            try:client.request('DELETE','/api/saas/v1/installations/'+opaque(saved['connection_id']),secret=keychain.get(brand,Slot.ACCOUNT,saved))
            except ServiceRejected as exc:
                if exc.status!=401:raise
        keychain.delete(brand,Slot.ACCOUNT,saved);(brand.dir_secretos/'socialcli-account.json').unlink(missing_ok=True)
        from socialctl.youtube_data import request_youtube_purge
        cleanup=request_youtube_purge(brand.raiz)
        if not cleanup['complete']:
            typer.echo('Local YouTube data cleanup is pending; run data-cleanup for this brand.',err=True)
        typer.echo('Signed out. This installation can no longer obtain social tokens.')
    except Exception as exc:
        typer.echo(str(exc) if isinstance(exc,ValueError) else 'Could not confirm sign-out. Keep your credentials and retry.',err=True);raise typer.Exit(1)
    finally:
        if lock is not None:os.close(lock)


def recovery_slot(platform):
    return Enum('RecoverySlot',{'VALUE':'socialcli_recovery_'+platform.value}).VALUE


def recovery_path(brand,platform):
    return brand.dir_secretos/('socialcli-recovery-'+platform.value+'.json')


def finalize_local(brand,platform,client,result):
    account=result.get('account',{});opaque(account.get('id'));connection_id=opaque(result.get('connection_id'))
    if result.get('platform')!=platform.value:raise ValueError('Connection platform mismatch.')
    secret=result.get('connection_secret')
    if not isinstance(secret,str) or not 16<=len(secret)<=512:raise ValueError('Invalid connection credential.')
    validate_binding(brand,platform,account)
    saved={'version':1,'auth_mode':'broker','protocol':'saas-v1','service_url':client.origin,
        'connection_id':connection_id,'account_id':account['id'],'account':account,'granted_scopes':result.get('granted_scopes',[])}
    if client.origin.startswith('http://'):saved['development_local']=True
    path=brand.dir_secretos/(platform.value+'.json');config=brand.raiz/'accounts.yml'
    old=brand.leer_secreto(platform)
    journal={'metadata':saved,'previous_config':base64.b64encode(config.read_bytes()).decode(),
        'previous_secret':base64.b64encode(path.read_bytes()).decode() if path.exists() else None,'previous_metadata':old}
    keychain.save(brand,platform,saved,secret)
    keychain.save(brand,recovery_slot(platform),saved,json.dumps(journal))
    atomic_bytes(recovery_path(brand,platform),json.dumps(saved).encode())
    activation_started=False
    try:
        bind_account(brand,platform,account);atomic_bytes(path,json.dumps(saved).encode())
        activation_started=True
        client.request('POST','/api/saas/v1/connections/'+connection_id+'/activate',secret=secret)
    except BaseException as exc:
        if activation_started and (not isinstance(exc,ServiceRejected) or exc.status>=500):
            raise ValueError('Activation could not be confirmed. Run socialcli account recover '+platform.value+' before any other connection change.') from None
        restore_local(brand,platform,journal)
        keychain.delete(brand,platform,saved)
        forget_recovery(brand,platform,saved)
        raise
    clear_recovery(brand,platform,journal)
    return saved


def restore_local(brand,platform,journal):
    config=brand.raiz/'accounts.yml';atomic_bytes(config,base64.b64decode(journal['previous_config']),config.stat().st_mode&0o777)
    path=brand.dir_secretos/(platform.value+'.json')
    if journal['previous_secret'] is None:path.unlink(missing_ok=True)
    else:atomic_bytes(path,base64.b64decode(journal['previous_secret']))


def clear_recovery(brand,platform,journal):
    saved=journal['metadata'];old=journal['previous_metadata']
    if old.get('auth_mode')=='broker' and old.get('connection_id')!=saved['connection_id']:
        keychain.delete(brand,platform,old)
    forget_recovery(brand,platform,saved)


def forget_recovery(brand,platform,saved):
    # Keep the journal available until its pointer is durably gone. A failed
    # unlink must remain recoverable; a leftover unreferenced secret is harmless.
    path=recovery_path(brand,platform)
    path.unlink(missing_ok=True)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
    keychain.delete(brand,recovery_slot(platform),saved)


@account_app.command('recover')
def recover(platform:Platform,brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root')):
    """Resolve an interrupted local install without repeating OAuth or publication."""
    lock=None
    try:
        brand=cargar_brand(root,brand_name);lock=acquire_brand_lock(brand,allow_recovery=True)
        saved=json.loads(recovery_path(brand,platform).read_text())
        journal=json.loads(keychain.get(brand,recovery_slot(platform),saved));secret=keychain.get(brand,platform,saved)
        with http_client() as transport:
            client=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
            try:client.request('POST','/api/saas/v1/connections/'+opaque(saved['connection_id'])+'/activate',secret=secret)
            except ServiceRejected as exc:
                if exc.status>=500: raise
                restore_local(brand,platform,journal);keychain.delete(brand,platform,saved)
                forget_recovery(brand,platform,saved)
                typer.echo('Pending activation was rejected. Previous local credentials restored.');return
        bind_account(brand,platform,saved['account']);atomic_bytes(brand.dir_secretos/(platform.value+'.json'),json.dumps(saved).encode())
        clear_recovery(brand,platform,journal);typer.echo('Connection recovered. No OAuth or publication was repeated.')
    except Exception as exc:
        typer.echo(str(exc) if isinstance(exc,ValueError) else 'Could not confirm recovery. Keep the pending records and retry.',err=True);raise typer.Exit(1)
    finally:
        if lock is not None:os.close(lock)


def connect_saas(brand,platform,*,no_browser,account_id=None,replace_independent=False,management=False,analytics=False,timeout=600):
    if recovery_path(brand,platform).exists():raise ValueError('A connection recovery is pending. Run socialcli account recover '+platform.value+'.')
    saved=metadata(brand);previous=brand.leer_secreto(platform)
    if previous and previous.get('auth_mode')!='broker' and not replace_independent:
        raise ValueError('Replacing independent credentials requires explicit --replace-independent and browser approval.')
    keychain.ensure_available(brand)
    with http_client() as transport:
        client=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
        secret=keychain.get(brand,Slot.ACCOUNT,saved);installation_id=opaque(saved['connection_id'])
        poll_secret=secrets.token_urlsafe(32)
        start=client.request('POST','/api/saas/v1/installations/'+installation_id+'/authorizations',secret=secret,
            body={'platform':platform.value,'brand':brand.nombre,'poll_challenge':hashlib.sha256(poll_secret.encode()).hexdigest(),
                  'management':management,'analytics':analytics})
        auth_id=opaque(start.get('authorization_id'));expected=client.origin+'/connect/'+auth_id
        if start.get('browser_url')!=expected:raise ValueError('Invalid account authorization URL.')
        handoff(expected,no_browser=no_browser,seconds=600)
        result=wait(client,'/api/saas/v1/authorizations/'+auth_id+'/poll',poll_secret,min(timeout,start['expires_in']),start['poll_interval'],{'selected'})
        if account_id is not None and result.get('account',{}).get('id')!=account_id:raise ValueError('Browser-selected account differs from the explicitly requested ID. No connection was installed.')
        finalize_local(brand,platform,client,result)
        typer.echo(platform.value+' connected for '+visible(brand.nombre)+'. No content was published.')


@account_app.command('attach')
def attach(platform:Platform,brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root')):
    """Use an existing owner-approved account in this installation without OAuth."""
    lock=None
    try:
        brand=cargar_brand(root,brand_name);lock=acquire_brand_lock(brand)
        if recovery_path(brand,platform).exists():raise ValueError('Resolve pending connection recovery first.')
        previous=brand.leer_secreto(platform)
        saved=metadata(brand)
        if not saved:raise ValueError('Run socialcli account login first.')
        if previous and (previous.get('auth_mode')!='broker' or previous.get('protocol')!='saas-v1'
                         or previous.get('service_url')!=saved['service_url']):
            raise ValueError('A local connection already exists. Review it before replacing it.')
        keychain.ensure_available(brand)
        with http_client() as transport:
            client=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
            if previous:
                try:
                    client.request('GET','/api/saas/v1/connections/'+opaque(previous['connection_id']),
                        secret=keychain.get(brand,platform,previous))
                except ServiceRejected as exc:
                    # Only an authenticated capability whose installation is
                    # confirmed inactive may be replaced without another OAuth.
                    if exc.status!=401 or exc.code not in {'installation_revoked','installation_expired'}:raise
                else:raise ValueError('A local connection already exists. Review it before replacing it.')
            result=client.request('POST','/api/saas/v1/installations/'+opaque(saved['connection_id'])+'/attach',
                secret=keychain.get(brand,Slot.ACCOUNT,saved),body={'platform':platform.value,'brand':brand.nombre})
            if previous and result.get('account',{}).get('id')!=previous.get('account_id'):
                raise ValueError('The approved account changed. Review it before replacing this local connection.')
            finalize_local(brand,platform,client,result)
        typer.echo(platform.value+' attached for '+visible(brand.nombre)+'. No OAuth or publication was repeated.')
    except Exception as exc:
        typer.echo(str(exc) if isinstance(exc,ValueError) else 'Could not attach safely. Check your SocialCli account.',err=True);raise typer.Exit(1)
    finally:
        if lock is not None:os.close(lock)


def migration_origin_allowed(old,new,*,allow_http_local=False):
    source=service_origin(old,allow_http_local=allow_http_local)
    target=service_origin(new,allow_http_local=allow_http_local)
    # The only cross-origin transfer approved for this distribution. Never
    # accept aliases supplied by the destination service itself.
    return source==target or (source,target)==('https://social.florentino.pro','https://socialcli.app')


@account_app.command('migrate')
def migrate(platform:Platform,brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root'),
            no_browser:bool=typer.Option(False,'--no-browser'),timeout:float=typer.Option(600,'--timeout',min=1,max=600)):
    """Move an existing legacy connection after its owner confirms in the browser."""
    lock=None
    try:
        brand=cargar_brand(root,brand_name);lock=acquire_brand_lock(brand)
        if recovery_path(brand,platform).exists():raise ValueError('Resolve pending connection recovery first.')
        saved=metadata(brand);old=brand.leer_secreto(platform)
        if not saved:raise ValueError('Run socialcli account login first.')
        if old.get('auth_mode')!='broker' or old.get('protocol')=='saas-v1':
            raise ValueError('This command requires an existing legacy broker connection.')
        keychain.ensure_available(brand)
        with http_client() as transport:
            client=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
            if not migration_origin_allowed(old['service_url'],client.origin,allow_http_local=saved.get('development_local') is True and old.get('development_local') is True):
                raise ValueError('Legacy connection belongs to another service. Its credential cannot be transferred to this destination.')
            poll_secret=secrets.token_urlsafe(32)
            start=client.request('POST','/api/saas/v1/installations/'+opaque(saved['connection_id'])+'/migrations',
                secret=keychain.get(brand,Slot.ACCOUNT,saved),legacy_secret=keychain.get(brand,platform,old),
                body={'platform':platform.value,'brand':brand.nombre,'legacy_id':opaque(old['connection_id']),
                      'poll_challenge':hashlib.sha256(poll_secret.encode()).hexdigest()})
            migration_id=opaque(start.get('migration_id'));expected=client.origin+'/account/migrate/'+migration_id
            if start.get('browser_url')!=expected:raise ValueError('Invalid SocialCli migration URL.')
            handoff(expected,no_browser=no_browser,seconds=600)
            result=wait(client,'/api/saas/v1/migrations/'+migration_id+'/poll',poll_secret,
                min(timeout,start['expires_in']),start['poll_interval'],{'selected'})
            if result.get('account',{}).get('id')!=old.get('account_id'):
                raise ValueError('The migration account does not match the local connection.')
            finalize_local(brand,platform,client,result)
        typer.echo(platform.value+' migrated for '+visible(brand.nombre)+'. No OAuth or publication was repeated.')
    except Exception as exc:
        typer.echo(str(exc) if isinstance(exc,ValueError) else 'Could not confirm migration. Keep local credentials and check account recovery.',err=True);raise typer.Exit(1)
    finally:
        if lock is not None:os.close(lock)


@account_app.command('rotate')
def rotate(brand_name:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root(),'--root'),
           cancel:bool=typer.Option(False,'--cancel')):
    """Renew this installation before expiry; rerun to recover an interruption."""
    lock=None
    try:
        brand=cargar_brand(root,brand_name);lock=acquire_brand_lock(brand,allow_rotation=True)
        from socialctl.connections.rotation_cli import rotate_local
        typer.echo(rotate_local(brand,cancel=cancel))
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else 'Credential rotation could not be confirmed.'
        typer.echo(message+' Run socialcli account rotate with the same brand to resume; use --cancel only to cancel an uncommitted rotation.',err=True)
        raise typer.Exit(1)
    finally:
        if lock is not None:os.close(lock)
