"""Upload approved original bytes; never send a connection secret to another host."""
from contextlib import contextmanager
import hashlib
import re
import time
from urllib.parse import urlsplit

import httpx
from socialctl.models import Platform
from socialctl.connections.client import broker_for,opaque,account_id
from socialctl.connections import keychain

MAX_FILE=512*1024*1024
MIMES={'.jpg':'image/jpeg','.jpeg':'image/jpeg','.mp4':'video/mp4','.mov':'video/quicktime'}


def available(brand):
    data=brand.leer_secreto(Platform.INSTAGRAM)
    return data.get('auth_mode')=='broker' and data.get('protocol')=='saas-v1'


@contextmanager
def upload(brand,path,client):
    metadata=brand.leer_secreto(Platform.INSTAGRAM)
    broker=broker_for(metadata,client)
    ident=opaque(metadata.get('connection_id'))
    if metadata.get('account_id')!=account_id(brand,Platform.INSTAGRAM):
        raise ValueError('The Instagram account differs from its approved connection.')
    secret=keychain.get(brand,Platform.INSTAGRAM,metadata)
    if not isinstance(secret,str) or not 16<=len(secret)<=512:
        raise ValueError('Instagram connection credentials are unavailable.')
    size=path.stat().st_size;mime=MIMES.get(path.suffix.lower())
    if not 0<size<=MAX_FILE or mime is None:
        raise ValueError('Temporary Instagram hosting accepts JPEG, MP4 or MOV originals up to 512 MiB.')
    with path.open('rb') as original:
        digest=hashlib.file_digest(original,'sha256').hexdigest()
    result=broker.request('POST',f'/api/saas/v1/connections/{ident}/media',secret=secret,
        body={'size':size,'sha256':digest,'content_type':mime})
    media_id=result.get('media_id','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{32}',media_id):raise ValueError('Invalid temporary upload response.')
    endpoint=f'/api/saas/v1/connections/{ident}/media/{media_id}'
    target=broker.origin+endpoint
    download=result.get('download_url','');expiry=result.get('expires_at')
    if (result.get('upload_url')!=target or not isinstance(download,str)
            or not re.fullmatch(re.escape(broker.origin+'/media/'+media_id+'/')+r'[A-Za-z0-9_-]{43}',download)
            or type(expiry) not in (int,float) or not time.time()<expiry<=time.time()+901):
        raise ValueError('Invalid temporary upload destination or lifetime.')
    try:
        try:
            with path.open('rb') as original:
                response=client.put(target,content=original,headers={'Authorization':'Bearer '+secret,
                    'Content-Type':mime,'Content-Length':str(size)},timeout=300,follow_redirects=False)
            if not response.is_success:
                raise ValueError('Temporary media upload failed. Check account access, storage limits and connection.')
        except httpx.HTTPError:
            raise ValueError('Temporary media upload could not be confirmed. No Instagram publication was attempted.') from None
        yield download
    finally:
        try:
            broker.request('DELETE',endpoint,secret=secret)
        except Exception:
            pass  # Server expiry and independent janitor remain the durable fence.
