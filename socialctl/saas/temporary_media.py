"""Private, quota-bound staging for Instagram. URLs expire 15 minutes after reservation.

Only opaque filenames live here, outside database backups. Both the application
janitor and a host timer remove expired files, including interrupted uploads.
"""
import asyncio
import hashlib
import hmac
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import time

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

TTL = 900
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_OWNER = 512 * 1024 * 1024
KINDS = {'image/jpeg': '.jpg', 'video/mp4': '.mp4', 'video/quicktime': '.mov'}
FILE = re.compile(r'([0-9]+)-([A-Za-z0-9_-]{32})\.(part|data)\Z')


class Reservation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    size: int = Field(gt=0, le=MAX_FILE)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    content_type: str = Field(pattern=r'^(image/jpeg|video/mp4|video/quicktime)$')


def cleanup_directory(root, now=None):
    """Does not need vault keys or a running container; safe across restarts."""
    now = time.time() if now is None else now
    removed = 0
    for path in Path(root).iterdir():
        match = FILE.fullmatch(path.name)
        if match and int(match[1]) <= now:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


class TemporaryMedia:
    def __init__(self, metadata, root, clock=time.time):
        self.metadata, self.vault, self.clock = metadata, metadata.vault, clock
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise ValueError('Temporary media must use a private real directory')
        self.root.chmod(0o700)
        self.cleanup()

    def cleanup(self):
        return cleanup_directory(self.root, self.clock())

    def path(self, media_id, expiry, suffix='data'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}', media_id):
            raise HTTPException(404, 'Temporary media unavailable.')
        return self.root / f'{int(expiry)}-{media_id}.{suffix}'

    def reserve(self, db, connection_id, owner, body):
        # Allocation accounting survives credential deletion and cancelled uploads.
        # Keep reservations charged until expiry even after early deletion, so an
        # unlinked but still open upload FD cannot bypass disk admission.
        db.execute('DELETE FROM saas_media_allocations WHERE expires<=?',(self.clock(),))
        total=db.execute('SELECT coalesce(sum(size),0) FROM saas_media_allocations').fetchone()[0]
        own,count=db.execute('SELECT coalesce(sum(size),0),count(*) FROM saas_media_allocations WHERE user_id=?',(owner['user_id'],)).fetchone()
        if count>=10 or own+body.size>MAX_OWNER:
            raise HTTPException(429,'media_quota: Wait for your temporary files to expire.')
        if total+body.size>MAX_TOTAL or shutil.disk_usage(self.root).free < body.size+256*1024*1024:
            raise HTTPException(503,'media_capacity: Temporary media storage is busy.')
        media_id=secrets.token_urlsafe(24);download=secrets.token_urlsafe(32)
        expiry=math.floor(self.clock())+TTL
        payload=body.model_dump() | {'user_id':owner['user_id'],'connection_id':connection_id,
            'grant_id':owner['grant_id'],'installation_id':owner['installation_id'],
            'download_hash':hashlib.sha256(download.encode()).hexdigest(),'state':'reserved'}
        db.execute('INSERT INTO saas_media_allocations VALUES(?,?,?,?)',(media_id,owner['user_id'],body.size,expiry))
        self.vault.put(db,'saas-media',media_id,payload,expires=expiry)
        return media_id,download,expiry

    def checked(self, db, media_id, connection_id=None):
        record=self.vault.get(db,'saas-media',media_id)
        if record is None or record['expires']<=self.clock():
            raise HTTPException(404,'Temporary media expired or unavailable.')
        if connection_id and record['payload']['connection_id']!=connection_id:
            raise HTTPException(404,'Temporary media unavailable.')
        return record

    def remove(self,media_id,expiry):
        for suffix in ('part','data'):
            self.path(media_id,expiry,suffix).unlink(missing_ok=True)


def install_media_routes(app, service, connections, origin):
    vault=service.vault
    app.state.temporary_media=service

    def owner(db,connection_id,request,*,require_subscription=True):
        record=connections.capability(db,connection_id,request)
        if require_subscription:connections.allowed(db,record['payload']['user_id'])
        if record['payload']['platform']!='instagram':
            raise HTTPException(403,'Temporary hosting is available for Instagram connections only.')
        return record['payload']

    @app.post('/api/saas/v1/connections/{connection_id}/media',status_code=201)
    def reserve(connection_id:str,body:Reservation,request:Request):
        service.cleanup()
        with vault.transaction() as db:
            data=owner(db,connection_id,request)
            if not service.metadata.admit(db,'media:'+data['user_id'],service.clock(),limit=20):
                raise HTTPException(429,'Too many temporary uploads. Wait a minute.')
            ident,secret,expiry=service.reserve(db,connection_id,data,body)
        return {'version':1,'media_id':ident,'expires_at':expiry,
            'upload_url':f'{origin}/api/saas/v1/connections/{connection_id}/media/{ident}',
            'download_url':f'{origin}/media/{ident}/{secret}'}

    @app.put('/api/saas/v1/connections/{connection_id}/media/{media_id}')
    async def upload(connection_id:str,media_id:str,request:Request):
        def claim():
            with vault.transaction() as db:
                owner(db,connection_id,request)
                record=service.checked(db,media_id,connection_id)
                if record['payload']['state']!='reserved':
                    raise HTTPException(409,'Upload already attempted. Reserve a new temporary file.')
                record['payload']['state']='uploading'
                vault.put(db,'saas-media',media_id,record['payload'],expires=record['expires'])
                return record
        record=await asyncio.to_thread(claim)
        partial=service.path(media_id,record['expires'],'part')
        complete=service.path(media_id,record['expires'])
        payload=record['payload'];size=0;digest=hashlib.sha256()
        try:
            async with asyncio.timeout(min(300,max(0,record['expires']-service.clock()))):
                fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as output:
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>payload['size'] or service.clock()>=record['expires']:
                            raise HTTPException(413,'Temporary upload exceeded its size or time limit.')
                        digest.update(chunk)
                        for offset in range(0,len(chunk),65536):
                            if service.clock()>=record['expires']:
                                raise HTTPException(408,'Temporary upload expired.')
                            await asyncio.to_thread(output.write,chunk[offset:offset+65536])
                if size!=payload['size'] or digest.hexdigest()!=payload['sha256']:
                    raise HTTPException(400,'Temporary upload does not match its declared bytes.')
                def finish():
                    with vault.transaction() as db:
                        owner(db,connection_id,request)
                        current=service.checked(db,media_id,connection_id)
                        if current['payload']['state']!='uploading':raise HTTPException(409,'Upload changed.')
                        os.replace(partial,complete)
                        current['payload']['state']='ready'
                        vault.put(db,'saas-media',media_id,current['payload'],expires=current['expires'])
                await asyncio.to_thread(finish)
        except BaseException:
            await asyncio.to_thread(service.remove,media_id,record['expires'])
            raise
        return {'version':1,'status':'ready','expires_at':record['expires']}

    @app.delete('/api/saas/v1/connections/{connection_id}/media/{media_id}')
    def delete(connection_id:str,media_id:str,request:Request):
        with vault.transaction() as db:
            owner(db,connection_id,request,require_subscription=False)
            record=service.checked(db,media_id,connection_id)
            service.remove(media_id,record['expires'])
            vault.delete(db,'saas-media',media_id)
        return {'version':1,'status':'deleted'}

    @app.api_route('/media/{media_id}/{download}',methods=['GET','HEAD'])
    def download(media_id:str,download:str,request:Request):
        with vault.read_transaction() as db:
            record=service.checked(db,media_id);data=record['payload']
            grant=vault.get(db,'saas-grant',data['grant_id'])
            installation=vault.get(db,'saas-installation',data['installation_id'])
            if not connections.access.decision(db,data['user_id'],service.clock()).allowed:
                raise HTTPException(404,'Temporary media unavailable.')
            if (data['state']!='ready' or not hmac.compare_digest(data['download_hash'],hashlib.sha256(download.encode()).hexdigest())
                    or grant is None or grant['expires']<=service.clock()
                    or installation is None or installation['expires']<=service.clock()):
                raise HTTPException(404,'Temporary media unavailable.')
        path=service.path(media_id,record['expires'])
        if not path.is_file() or path.is_symlink():raise HTTPException(404,'Temporary media unavailable.')
        size=data['size'];start=0;end=size-1;status=200
        headers={'Accept-Ranges':'bytes','Content-Disposition':'inline; filename="media'+KINDS[data['content_type']]+'"'}
        requested=request.headers.get('range')
        if requested:
            match=re.fullmatch(r'bytes=([0-9]*)-([0-9]*)',requested)
            if not match or not any(match.groups()):raise HTTPException(416,'Invalid media range.',headers={'Content-Range':f'bytes */{size}'})
            if match[1]:start=int(match[1]);end=min(int(match[2]),end) if match[2] else end
            else:start=max(0,size-int(match[2]))
            if start>end:raise HTTPException(416,'Invalid media range.',headers={'Content-Range':f'bytes */{size}'})
            status=206;headers['Content-Range']=f'bytes {start}-{end}/{size}'
        headers['Content-Length']=str(end-start+1)
        if request.method=='HEAD':return Response(status_code=status,media_type=data['content_type'],headers=headers)
        def content():
            try:
                with path.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining>0 and service.clock()<record['expires']:
                        chunk=stream.read(min(65536,remaining))
                        if not chunk:break
                        remaining-=len(chunk)
                        yield chunk
            except FileNotFoundError:
                return
        return StreamingResponse(content(),status_code=status,media_type=data['content_type'],headers=headers)
