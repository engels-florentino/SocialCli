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


def test_delete_during_streaming_upload_prevents_resurrection_and_keeps_quota(tmp_path):
    import asyncio
    import httpx
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)

    async def scenario():
        first_written=asyncio.Event()
        resume=asyncio.Event()

        async def chunks():
            yield b'fictional '
            first_written.set()
            await resume.wait()
            yield b'image'

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                                     base_url='https://social.example') as api:
            task=asyncio.create_task(api.put(value['upload_url'],content=chunks(),headers=headers))
            try:
                await asyncio.wait_for(first_written.wait(),5)
                assert list(service.root.glob('*.part'))
                assert (await api.delete(value['upload_url'],headers=headers)).status_code==200
                with service.vault.read_transaction() as db:
                    row=db.execute('SELECT size,expires FROM saas_media_allocations WHERE id=?',
                                   (value['media_id'],)).fetchone()
                    assert row['size']==15 and row['expires']==value['expires_at']
                resume.set()
                assert (await asyncio.wait_for(task,5)).status_code==404
                assert (await api.get(value['download_url'])).status_code==404
            finally:
                resume.set()
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    asyncio.run(scenario())
    assert not list(service.root.iterdir())
    now[0]=value['expires_at']+1
    service.cleanup()
    client.app.state.saas_lifecycle.prune_expired()
    with service.vault.read_transaction() as db:
        assert db.execute('SELECT count(*) FROM saas_media_allocations').fetchone()[0]==0


def test_failed_upload_releases_closed_writer_allocation_and_record(tmp_path):
    client,now,connection,service=media_service(tmp_path)
    for _ in range(11):
        value,headers=reserve(client,connection)
        assert client.put(value['upload_url'],content=b'wrong',headers=headers).status_code==400
        with service.vault.read_transaction() as db:
            assert db.execute('SELECT count(*) FROM saas_media_allocations').fetchone()[0]==0
            assert service.vault.get(db,'saas-media',value['media_id']) is None
        assert not list(service.root.iterdir())


def test_cancelled_upload_waits_for_writer_before_releasing_quota(tmp_path,monkeypatch):
    import asyncio
    import os
    from threading import Event
    from starlette.requests import Request
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)
    entered=Event();release=Event();closed=Event()
    original=os.fdopen
    class SlowWriter:
        def __init__(self,*args):self.stream=original(*args)
        def __enter__(self):return self
        def __exit__(self,*args):self.stream.close();closed.set()
        def write(self,chunk):
            entered.set();assert release.wait(5)
            return self.stream.write(chunk)
    monkeypatch.setattr(os,'fdopen',SlowWriter)
    route=next(r for r in client.app.routes if 'PUT' in getattr(r,'methods',set()) and r.path.endswith('/media/{media_id}'))
    async def scenario():
        async def receive():return {'type':'http.request','body':b'fictional image','more_body':False}
        request=Request({'type':'http','method':'PUT','headers':[(b'authorization',headers['Authorization'].encode())]},receive)
        task=asyncio.create_task(route.endpoint(connection['connection_id'],value['media_id'],request))
        try:
            assert await asyncio.to_thread(entered.wait,3)
            task.cancel()
            await asyncio.wait({task},timeout=.05)
            assert not closed.is_set(), 'Writer closed while its worker still owns the FD'
            with service.vault.read_transaction() as db:
                assert db.execute('SELECT count(*) FROM saas_media_allocations').fetchone()[0]==1
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)
        assert closed.is_set()
        with service.vault.read_transaction() as db:
            assert db.execute('SELECT count(*) FROM saas_media_allocations').fetchone()[0]==0
            assert service.vault.get(db,'saas-media',value['media_id']) is None
    asyncio.run(scenario())
    assert not list(service.root.iterdir())


def test_cancelled_finish_cannot_leave_ready_record_after_removing_file(tmp_path,monkeypatch):
    import asyncio
    import os
    from threading import Event
    from starlette.requests import Request
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)
    entered=Event();release=Event()
    original=os.replace
    def slow_replace(source,destination):
        original(source,destination)
        entered.set();assert release.wait(5)
    monkeypatch.setattr(os,'replace',slow_replace)
    route=next(r for r in client.app.routes if 'PUT' in getattr(r,'methods',set()) and r.path.endswith('/media/{media_id}'))
    async def scenario():
        async def receive():return {'type':'http.request','body':b'fictional image','more_body':False}
        request=Request({'type':'http','method':'PUT','headers':[(b'authorization',headers['Authorization'].encode())]},receive)
        task=asyncio.create_task(route.endpoint(connection['connection_id'],value['media_id'],request))
        try:
            assert await asyncio.to_thread(entered.wait,3)
            task.cancel()
            await asyncio.wait({task},timeout=.05)
            assert list(service.root.glob('*.data')), 'Cleanup raced the still-running finish transaction'
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)
        with service.vault.read_transaction() as db:
            assert db.execute('SELECT count(*) FROM saas_media_allocations').fetchone()[0]==0
            assert service.vault.get(db,'saas-media',value['media_id']) is None
    asyncio.run(scenario())
    assert not list(service.root.iterdir())


def test_cancelled_claim_settles_before_removing_reservation(tmp_path,monkeypatch):
    import asyncio
    from threading import Event
    from starlette.requests import Request
    client,now,connection,service=media_service(tmp_path)
    value,headers=reserve(client,connection)
    entered=Event();release=Event()
    original=service.vault.put
    def slow_claim(db,kind,ident,payload,**kwargs):
        original(db,kind,ident,payload,**kwargs)
        if kind=='saas-media' and payload['state']=='uploading':
            entered.set();assert release.wait(5)
    monkeypatch.setattr(service.vault,'put',slow_claim)
    route=next(r for r in client.app.routes if 'PUT' in getattr(r,'methods',set()) and r.path.endswith('/media/{media_id}'))
    async def scenario():
        async def receive():return {'type':'http.request','body':b'fictional image','more_body':False}
        request=Request({'type':'http','method':'PUT','headers':[(b'authorization',headers['Authorization'].encode())]},receive)
        task=asyncio.create_task(route.endpoint(connection['connection_id'],value['media_id'],request))
        try:
            assert await asyncio.to_thread(entered.wait,3)
            task.cancel()
            await asyncio.wait({task},timeout=.05)
            assert not task.done(), 'Claim worker escaped the cancelled upload owner'
            with service.vault.read_transaction() as db:
                assert db.execute('SELECT count(*) FROM saas_media_allocations').fetchone()[0]==1
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)
        with service.vault.read_transaction() as db:
            assert db.execute('SELECT count(*) FROM saas_media_allocations').fetchone()[0]==0
            assert service.vault.get(db,'saas-media',value['media_id']) is None
    asyncio.run(scenario())
    assert not list(service.root.iterdir())
