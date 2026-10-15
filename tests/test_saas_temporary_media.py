"""Temporary hosting is bounded, owner-bound and never renews its deadline."""
from dataclasses import replace
import hashlib
from pathlib import Path
from tests.test_saas_lifecycle import connected


def media_service(tmp_path):
    client,now,installation,connection=connected(tmp_path)
    from socialctl.saas.temporary_media import TemporaryMedia,install_media_routes
    # Use an isolated directory, outside the credential database directory.
    root=tmp_path/'temporary'
    service=TemporaryMedia(client.app.state.saas,root,lambda:now[0])
    install_media_routes(client.app,service,client.app.state.saas_connections,'https://social.example')
    # Fixture connection is YouTube; grant Instagram explicitly for upload tests.
    vault=client.app.state.saas.vault
    with vault.transaction() as db:
        rec=vault.get(db,'saas-capability',connection['connection_id'])
        rec['payload']['platform']='instagram'
        vault.put(db,'saas-capability',connection['connection_id'],rec['payload'],expires=rec['expires'],credential_hash=rec['credential_hash'])
        db.execute("UPDATE saas_slots SET platform='instagram'")
    return client,now,connection,service


def reserve(client,connection,data=b'fictional image'):
    headers={'Authorization':'Bearer '+connection['connection_secret']}
    response=client.post('/api/saas/v1/connections/'+connection['connection_id']+'/media',
        json={'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'content_type':'image/jpeg'},headers=headers)
    assert response.status_code==201,response.text
    return response.json(),headers


def test_upload_download_expiry_and_restart_cleanup(tmp_path):
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)
    assert value['expires_at']==now[0]+900
    assert client.get(value['download_url']).status_code==404
    assert client.put(value['upload_url'],content=b'fictional image',headers=headers).status_code==200
    assert client.get(value['download_url']).content==b'fictional image'
    assert client.head(value['download_url']).headers['content-length']=='15'
    ranged=client.get(value['download_url'],headers={'Range':'bytes=0-3'})
    assert ranged.status_code==206 and ranged.content==b'fict'
    now[0]+=900
    assert client.get(value['download_url']).status_code==404
    service.cleanup()
    assert not list(service.root.iterdir())


def test_wrong_secret_hash_overwrite_and_quota_are_rejected(tmp_path):
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)
    assert client.put(value['upload_url'],content=b'fictional image',headers={'Authorization':'Bearer '+'x'*32}).status_code in {401,404}
    assert client.put(value['upload_url'],content=b'wrong',headers=headers).status_code==400
    assert client.get(value['download_url']).status_code==404
    value,headers=reserve(client,connection)
    assert client.put(value['upload_url'],content=b'fictional image',headers=headers).status_code==200
    assert client.put(value['upload_url'],content=b'fictional image',headers=headers).status_code==409
    assert client.delete(value['upload_url'],headers=headers).status_code==200
    assert client.get(value['download_url']).status_code==404


def test_large_upload_streams_through_real_middleware_and_quota_expires(tmp_path):
    client,now,connection,service=media_service(tmp_path)
    data=b'x'*20000
    value,headers=reserve(client,connection,data)
    assert client.put(value['upload_url'],content=data,headers=headers).status_code==200
    for _ in range(9):reserve(client,connection)
    response=client.post('/api/saas/v1/connections/'+connection['connection_id']+'/media',
        json={'size':1,'sha256':hashlib.sha256(b'x').hexdigest(),'content_type':'image/jpeg'},headers=headers)
    assert response.status_code==429
    now[0]+=901
    assert not service.cleanup() < 1
    reserve(client,connection)


def test_host_cleanup_removes_expired_partial_without_database_or_container(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('media_cleanup',Path(__file__).parents[1]/'deploy/saas/temporary_media_cleanup.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/'private';root.mkdir(mode=0o700)
    expired=root/('1000-'+'a'*32+'.part');expired.write_bytes(b'incomplete')
    active=root/('1100-'+'b'*32+'.data');active.write_bytes(b'active')
    assert module.cleanup(root,1001)==1
    assert not expired.exists() and active.exists()


def test_expired_trial_cannot_reserve_temporary_storage(tmp_path):
    client,now,connection,service=media_service(tmp_path)
    now[0]+=15*86400
    reply=client.post('/api/saas/v1/connections/'+connection['connection_id']+'/media',
        json={'size':1,'sha256':hashlib.sha256(b'x').hexdigest(),'content_type':'image/jpeg'},
        headers={'Authorization':'Bearer '+connection['connection_secret']})
    assert reply.status_code==402


def test_revoking_installation_does_not_free_reserved_disk_quota(tmp_path):
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)
    assert client.put(value['upload_url'],content=b'fictional image',headers=headers).status_code==200
    from tests.test_saas_auth import USER
    with service.vault.transaction() as db:
        cap=service.vault.get(db,'saas-capability',connection['connection_id'])
        client.app.state.saas_lifecycle.revoke(db,USER,cap['payload']['installation_id'])
        row=db.execute('SELECT size FROM saas_media_allocations WHERE id=?',(value['media_id'],)).fetchone()
        assert row is not None and row['size']==15
    assert client.get(value['download_url']).status_code==404


def test_cleanup_remains_available_after_trial_expiry(tmp_path):
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)
    assert client.put(value['upload_url'],content=b'fictional image',headers=headers).status_code==200
    with service.vault.transaction() as db:db.execute('UPDATE saas_entitlements SET trial_end=?',(now[0]-1,))
    assert client.delete(value['upload_url'],headers=headers).status_code==200
    assert client.get(value['download_url']).status_code==404
