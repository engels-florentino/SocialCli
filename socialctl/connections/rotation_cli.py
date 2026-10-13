"""Durable local rotation journal; no provider OAuth and no secrets in JSON files."""
import hashlib
import json
import secrets
from enum import Enum
from socialctl.connections import keychain
from socialctl.connections.client import opaque
from socialctl.connections.saas_client import SaaSClient,ServiceRejected
from socialctl.models import Platform

ROTATION=Enum('RotationSlot',{'VALUE':'socialcli_rotation'}).VALUE


def rotate_local(brand,*,cancel=False):
    from socialctl.connections import account_cli as account
    path=brand.dir_secretos/'socialcli-rotation.json'
    keychain.ensure_available(brand)
    if path.exists():
        pointer=json.loads(path.read_text());journal=json.loads(keychain.get(brand,ROTATION,pointer))
    else:
        if cancel:raise ValueError('No credential rotation is pending.')
        saved=account.metadata(brand)
        if not saved:raise ValueError('Run socialcli account login first.')
        if any(account.recovery_path(brand,p).exists() for p in Platform):
            raise ValueError('Finish pending connection recovery before rotating credentials.')
        with account.http_client() as transport:
            preflight=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
            preflight.request('GET','/api/saas/v1/installations/'+opaque(saved['connection_id']),secret=keychain.get(brand,account.Slot.ACCOUNT,saved))
        pointer={'version':1,'service_url':saved['service_url'],'connection_id':secrets.token_urlsafe(24)}
        journal={'old_installation':saved,'installation_secret':secrets.token_urlsafe(32),'connections':[]}
        for platform in Platform:
            old=brand.leer_secreto(platform)
            if old.get('protocol')=='saas-v1':
                if old['service_url']!=saved['service_url']:raise ValueError('Review connections belonging to another service before rotating.')
                journal['connections'].append({'platform':platform.value,'old':old,'secret':secrets.token_urlsafe(32)})
        keychain.save(brand,ROTATION,pointer,json.dumps(journal))
        account.atomic_bytes(path,json.dumps(pointer).encode())
    saved=journal['old_installation'];rid=opaque(pointer['connection_id'])
    with account.http_client() as transport:
        client=SaaSClient(saved['service_url'],transport,allow_http_local=saved.get('development_local') is True)
        base='/api/saas/v1/installations/'+opaque(saved['connection_id'])+'/rotations/'+rid
        if cancel or journal.get('cancelled'):
            if not journal.get('cancelled'):
                inactive=False
                try:
                    client.request('DELETE',base,secret=keychain.get(brand,account.Slot.ACCOUNT,saved))
                except ServiceRejected as exc:
                    if exc.status!=401:raise
                    # The old installation is inactive. If commit could have
                    # been sent, independently verify its durable replacement.
                    if journal.get('result'):
                        try:
                            client.request('GET','/api/saas/v1/installations/'+opaque(journal['result']['installation_id']),secret=journal['installation_secret'])
                        except ServiceRejected as replacement:
                            if replacement.status!=401:raise
                        else:
                            raise ValueError('Rotation already committed. Run socialcli account rotate without --cancel to finish recovery.')
                    # Without a durable result this client has never sent commit;
                    # an inactive old installation cannot commit in the future.
                    inactive=True
                journal.update(cancelled=True,inactive=inactive)
                keychain.save(brand,ROTATION,pointer,json.dumps(journal))
            result=journal.get('result')
            if result:
                keychain.delete(brand,account.Slot.ACCOUNT,{**saved,'connection_id':result['installation_id']})
                for entry,item in zip(journal['connections'],result['connections'],strict=True):
                    keychain.delete(brand,Platform(entry['platform']),{**entry['old'],'connection_id':item['connection_id']})
            if journal['inactive']:
                known={saved['connection_id']}
                if result:known.add(result['installation_id'])
                current_path=brand.dir_secretos/'socialcli-account.json'
                if current_path.exists() and json.loads(current_path.read_text()).get('connection_id') in known:
                    current_path.unlink()
                keychain.delete(brand,account.Slot.ACCOUNT,saved)
                for i,entry in enumerate(journal['connections']):
                    known={entry['old']['connection_id']}
                    if result:known.add(result['connections'][i]['connection_id'])
                    local=brand.dir_secretos/(entry['platform']+'.json')
                    if local.exists() and json.loads(local.read_text()).get('connection_id') in known:local.unlink()
                    keychain.delete(brand,Platform(entry['platform']),entry['old'])
            path.unlink();keychain.delete(brand,ROTATION,pointer)
            return ('Inactive credentials cleared. Run socialcli account login, then attach your existing accounts.' if journal['inactive']
                    else 'Pending rotation cancelled. Existing credentials remain active.')
        if 'result' not in journal:
            result=client.request('POST',base,secret=keychain.get(brand,account.Slot.ACCOUNT,saved),body={
                'installation_challenge':hashlib.sha256(journal['installation_secret'].encode()).hexdigest(),
                'connections':[{'connection_id':entry['old']['connection_id'],
                    'challenge':hashlib.sha256(entry['secret'].encode()).hexdigest()} for entry in journal['connections']]})
            opaque(result.get('installation_id'))
            if result.get('status')!='prepared' or not isinstance(result.get('connections'),list) or len(result['connections'])!=len(journal['connections']):
                raise ValueError('Invalid rotation response. Run socialcli account rotate --cancel to keep old credentials.')
            ids=set()
            for entry,item in zip(journal['connections'],result['connections'],strict=True):
                next_id=opaque(item.get('connection_id'))
                if (next_id in ids or next_id==entry['old']['connection_id'] or item.get('previous_id')!=entry['old']['connection_id']
                        or item.get('platform')!=entry['platform'] or item.get('account',{}).get('id')!=entry['old']['account_id']):
                    raise ValueError('Rotation connection mismatch. Keep local credentials and review account access.')
                ids.add(next_id)
            journal['result']=result
            keychain.save(brand,ROTATION,pointer,json.dumps(journal))
        result=journal['result'];next_install={**saved,'connection_id':result['installation_id']}
        keychain.save(brand,account.Slot.ACCOUNT,next_install,journal['installation_secret'])
        for entry,item in zip(journal['connections'],result['connections'],strict=True):
            keychain.save(brand,Platform(entry['platform']),{**entry['old'],'connection_id':item['connection_id']},entry['secret'])
        # Every replacement secret and the recovery journal are durable before
        # the server invalidates the old installation in its single transaction.
        client.request('POST','/api/saas/v1/rotations/'+rid+'/commit',secret=journal['installation_secret'])
        for entry,item in zip(journal['connections'],result['connections'],strict=True):
            account.atomic_bytes(brand.dir_secretos/(entry['platform']+'.json'),json.dumps({**entry['old'],'connection_id':item['connection_id']}).encode())
        account.atomic_bytes(brand.dir_secretos/'socialcli-account.json',json.dumps(next_install).encode())
        for entry in journal['connections']:keychain.delete(brand,Platform(entry['platform']),entry['old'])
        keychain.delete(brand,account.Slot.ACCOUNT,saved)
        # Remove the pointer before its secret so a cleanup interruption cannot
        # leave a visible journal that can no longer be recovered.
        path.unlink();keychain.delete(brand,ROTATION,pointer)
    return 'Credentials rotated. Other assistants and provider authorizations are unchanged.'
