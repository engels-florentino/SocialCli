"""Native YouTube writes, closed by default; API readback is not Studio evidence.

Controller supplies independently verified account/project/pilot eligibility. Results
are transport observations, never ledger attestations. No legacy publish fallback.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
import os
import re

import httpx

from socialctl.adapters.youtube import URL_UPLOAD, URL_VIDEOS, _TIPO_MIME_VIDEO
from socialctl.brands import Brand
from socialctl.management.metadata_parts import MetadataEdit, propose_parts, validate_status
from socialctl.management.youtube_metadata import YouTubeMetadataClient
from .approval import verify_approval
from .models import NativeJob
from .store import NativeStore


@dataclass(frozen=True)
class YouTubeEligibility:
    account_id: str = ''
    project_verified: bool = False
    studio_pilot_verified: bool = False
    evidence_ref: str = ''
    api_enabled: bool = False


@dataclass(frozen=True)
class YouTubeScheduleResult:
    state: Literal['ui_required', 'pending_verification', 'uncertain', 'conflict', 'published']
    remote_id: str | None = None
    reason: str = ''
    resource: dict | None = None
    source: Literal['api'] = 'api'
    calendar_visible: Literal[False] = False


def _context(job, payload, brand, store):
    if (job.brand != brand.nombre or store.brand != job.brand or
        store.path.parent != brand.raiz.resolve() or job.platform != 'youtube' or
        (brand.cuentas.get('youtube') or {}).get('channel_id') != job.account_id):
        raise ValueError('Explicit brand/account/store mismatch')
    if store.get(job.id) != job or store.get_payload(job.id) != payload:
        raise ValueError('Stale job or changed stored payload')
    verify_approval(job, payload)


def _future(job):
    if job.publish_at <= datetime.now(timezone.utc):
        raise ValueError('Future publish_at required; immediate publication forbidden')


def _checkpoint(store, job, **values):
    # Resumable URI is a bearer-capable secret. SQLite journal inherits DB mode.
    os.chmod(store.path, 0o600)
    store.checkpoint(job.id, job.attempt_id, values)


def _uncertain(store, job, remote_id=None):
    current = store.get(job.id)
    if current.state in {'dispatching', 'verifying'}:
        store.transition(job.id, current.state, 'uncertain')
    return YouTubeScheduleResult('uncertain', remote_id, 'Reconcile existing attempt; never upload again')


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        raise ValueError('Invalid video identity')
    return value


def _session(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com' or
        parsed.port not in (None,443) or parsed.username or parsed.password or parsed.fragment or
        not parsed.path.startswith('/upload/')):
        raise ValueError('Invalid resumable endpoint')
    return value


_CHUNK_BYTES = 256 * 1024


def _upload_chunks(client, session, token, path, size, job, store, *, start_offset=0):
    """Send approved original bytes in resumable chunks; trust only confirmed Range."""
    if not 0 <= start_offset < size:
        raise ValueError('Invalid confirmed upload offset')
    offset = start_offset
    with path.open('rb') as source:
        while offset < size:
            _future(job)
            source.seek(offset)
            chunk = source.read(min(_CHUNK_BYTES, size - offset))
            if not chunk:
                raise ValueError('Original video ended during upload')
            end = offset + len(chunk) - 1
            response = client.put(session, headers={
                'Authorization': f'Bearer {token}',
                'Content-Length': str(len(chunk)),
                'Content-Type': _TIPO_MIME_VIDEO,
                'Content-Range': f'bytes {offset}-{end}/{size}',
            }, content=chunk, timeout=None)
            if response.status_code == 308:
                match = re.fullmatch(r'bytes=0-(\d+)', response.headers.get('Range', ''))
                confirmed = int(match.group(1)) + 1 if match else 0
                if not offset < confirmed <= end + 1 or confirmed >= size:
                    raise ValueError('Upload Range did not confirm forward progress')
                offset = confirmed
                _checkpoint(store, job, phase='uploading', offset=offset)
                continue
            response.raise_for_status()
            remote_id = _id(response.json()['id'])
            _checkpoint(store, job, phase='uploaded', offset=size, video_id=remote_id)
            if end + 1 != size:
                raise ValueError('Upload completed before all original bytes were sent')
            return remote_id
    raise ValueError('Upload ended without a remote video ID')


def _confirmed_offset(response, size):
    if response.status_code != 308:
        raise ValueError('Session did not report an incomplete upload')
    value = response.headers.get('Range')
    if value is None:
        return 0
    match = re.fullmatch(r'bytes=0-(\d+)', value)
    if not match:
        raise ValueError('Invalid resumable upload Range')
    offset = int(match.group(1)) + 1
    if not 0 < offset < size:
        raise ValueError('Invalid incomplete upload offset')
    return offset


def _metadata(payload, job):
    options = payload['options']
    allowed = {'format','title','categoryId','tags','defaultLanguage','video_id','never_published',
               'selfDeclaredMadeForKids','containsSyntheticMedia','embeddable','license','publicStatsViewable'}
    if set(options) - allowed or payload.get('visibility') != 'public':
        raise ValueError('Unsupported approved options; reprepare for Studio')
    snippet = {key: options[key] for key in ('title','categoryId','tags','defaultLanguage') if key in options}
    if not isinstance(snippet.get('title'),str) or not snippet['title'].strip():
        raise ValueError('Approved title required')
    snippet['description'] = payload['copy']
    status = {key:options[key] for key in ('selfDeclaredMadeForKids','containsSyntheticMedia','embeddable','license','publicStatsViewable') if key in options}
    status.update(privacyStatus='private',publishAt=job.publish_at.isoformat().replace('+00:00','Z'))
    validate_status(status, outgoing=True)
    return {'snippet':snippet,'status':status}


def _readback(job, payload, remote_id, api, expected_parts=None):
    token = api._token()
    if api._authenticated_channel(token) != job.account_id:
        return YouTubeScheduleResult('conflict',remote_id,'Authenticated channel mismatch')
    data=api._get_json(URL_VIDEOS,token,params={'id':remote_id,'part':'snippet,status,processingDetails'},operation='verificar programación')
    items=data.get('items')
    if not isinstance(items,list) or len(items)!=1:
        return YouTubeScheduleResult('uncertain',remote_id,'No unique remote readback')
    item=items[0]; snippet=item.get('snippet',{}); status=item.get('status',{})
    expected_options=payload['options']
    matches=(item.get('id')==remote_id and snippet.get('channelId')==job.account_id and
        snippet.get('description')==payload['copy'] and all(snippet.get(k)==expected_options[k]
        for k in ('title','categoryId','tags','defaultLanguage') if k in expected_options) and
        all(status.get(k)==expected_options[k] for k in ('selfDeclaredMadeForKids','containsSyntheticMedia','embeddable','license','publicStatsViewable') if k in expected_options))
    for part, fields in (expected_parts or {}).items():
        actual = item.get(part, {})
        for key, value in fields.items():
            if part == 'status' and key in {'privacyStatus', 'publishAt'}:
                continue
            if actual.get(key) != value:
                matches = False
    if not matches:
        return YouTubeScheduleResult('conflict',remote_id,'Remote identity/copy/options differ',item)
    if status.get('privacyStatus')=='public':
        return YouTubeScheduleResult('published',remote_id,'Remote public visibility observed',item)
    try:
        when=datetime.fromisoformat(status.get('publishAt','').replace('Z','+00:00'))
    except (ValueError,TypeError):
        when=None
    if status.get('privacyStatus')!='private' or when!=job.publish_at:
        return YouTubeScheduleResult('conflict',remote_id,'Remote schedule differs; no overwrite',item)
    if status.get('uploadStatus') in {'failed','rejected','deleted'} or item.get('processingDetails',{}).get('processingStatus') in {'failed','terminated'}:
        return YouTubeScheduleResult('conflict',remote_id,'Remote processing failed',item)
    return YouTubeScheduleResult('pending_verification',remote_id,'Studio calendar evidence required',item)


def schedule_youtube(job: NativeJob, payload: dict, *, brand: Brand, store: NativeStore,
                     client: httpx.Client, eligibility: YouTubeEligibility | None = None) -> YouTubeScheduleResult:
    _context(job,payload,brand,store)
    if job.state in {'uncertain','dispatching','verifying'} or store.get_attempts(job.id):
        return YouTubeScheduleResult('uncertain',job.remote_id,'Existing attempt requires reconciliation')
    eligibility=eligibility or YouTubeEligibility()
    if (job.route!='api' or eligibility.account_id!=job.account_id or not eligibility.api_enabled or
        not eligibility.project_verified or not eligibility.studio_pilot_verified or not eligibility.evidence_ref):
        return YouTubeScheduleResult('ui_required',reason='API eligibility unproven; reprepare and approve route=ui')
    _future(job)
    metadata=_metadata(payload,job)
    api=YouTubeMetadataClient(brand,client)
    remote_id=payload['options'].get('video_id') or job.remote_id
    if job.remote_id and remote_id!=job.remote_id:
        raise ValueError('Existing video identity mismatch')
    if remote_id:
        _id(remote_id)
        edit=MetadataEdit(video_id=remote_id,kind='schedule',never_published=payload['options'].get('never_published',False),patch={k:metadata['status'][k] for k in ('publishAt','privacyStatus')})
        observed=api.inspect(remote_id)
        if observed.resource['status'].get('privacyStatus')!='private':
            raise ValueError('Existing video is not private; no write')
        if any(observed.resource['snippet'].get(k)!=v for k,v in metadata['snippet'].items()):
            raise ValueError('Existing copy differs; reprepare exact metadata')
        if any(observed.resource['status'].get(k)!=v for k,v in metadata['status'].items() if k not in {'privacyStatus','publishAt'}):
            raise ValueError('Existing audience/options differ; reprepare')
        _,parts=propose_parts(observed,edit)
    else:
        if len(payload['media'])!=1: raise ValueError('Exactly one supplied video required')
        path=Path(payload['media'][0]['path']); size=path.stat().st_size
        token=api._token()
        if api._authenticated_channel(token)!=job.account_id: raise ValueError('Authenticated channel mismatch')
    _future(job)
    job=store.claim(job.id,job.state)
    if job is None: raise ValueError('Job could not be claimed')
    try:
        if remote_id:
            _checkpoint(store,job,video_id=remote_id,expected_parts=parts)
            verify_approval(job,payload)
            api.update_parts(remote_id,parts,etag=observed.etag)
        else:
            _future(job)
            response=client.post(URL_UPLOAD,params={'uploadType':'resumable','part':'snippet,status'},headers={'Authorization':f'Bearer {token}','X-Upload-Content-Length':str(size),'X-Upload-Content-Type':_TIPO_MIME_VIDEO},json=metadata)
            response.raise_for_status()
            session=_session(response.headers['Location'])
            _checkpoint(store,job,session_uri=session,total_bytes=size,offset=0,expected_parts=metadata)
            _future(job)
            verify_approval(job,payload)
            remote_id=_upload_chunks(client,session,token,path,size,job,store)
        result=_readback(job,payload,remote_id,api,parts if payload['options'].get('video_id') or job.remote_id else metadata)
        if result.state!='pending_verification':
            _uncertain(store,job,remote_id)
        else: store.transition(job.id,'dispatching','verifying')
        return result
    except Exception:
        return _uncertain(store,job,remote_id)


def resume_youtube(job: NativeJob, payload: dict, *, brand: Brand, store: NativeStore,
                   client: httpx.Client,
                   eligibility: YouTubeEligibility | None = None) -> YouTubeScheduleResult:
    """Explicitly resume the existing upload after querying its remote offset."""
    _context(job, payload, brand, store)
    eligibility = eligibility or YouTubeEligibility()
    if (job.state != 'uncertain' or job.route != 'api' or
            eligibility.account_id != job.account_id or not eligibility.api_enabled or
            not eligibility.project_verified or not eligibility.studio_pilot_verified or
            not eligibility.evidence_ref):
        return YouTubeScheduleResult('ui_required', reason='Existing API attempt or eligibility unproven')
    attempts = store.get_attempts(job.id)
    checkpoint = attempts[-1]['checkpoint'] if attempts else {}
    if (not job.attempt_id or not checkpoint.get('session_uri') or
            checkpoint.get('video_id')):
        return YouTubeScheduleResult('uncertain', job.remote_id,
                                     'No incomplete session can be resumed')
    session = _session(checkpoint['session_uri'])
    media = payload.get('media')
    if not isinstance(media, list) or len(media) != 1:
        raise ValueError('One approved original video required')
    path = Path(media[0]['path'])
    size = path.stat().st_size
    if size != checkpoint.get('total_bytes'):
        raise ValueError('Original video size differs from upload session')
    _future(job)
    claimed = store.resume_attempt(job.id, job.attempt_id)
    if claimed is None:
        return YouTubeScheduleResult('uncertain', job.remote_id,
                                     'Another worker owns the existing attempt')
    remote_id = None
    try:
        api = YouTubeMetadataClient(brand, client)
        token = api._token()
        if api._authenticated_channel(token) != job.account_id:
            raise ValueError('Authenticated channel mismatch')
        _checkpoint(store, claimed, phase='resume_query_requested')
        response = client.put(session, headers={
            'Authorization': f'Bearer {token}', 'Content-Length': '0',
            'Content-Range': f'bytes */{size}'}, content=b'')
        if response.status_code == 308:
            offset = _confirmed_offset(response, size)
            _checkpoint(store, claimed, phase='resume_offset_confirmed', offset=offset)
            verify_approval(claimed, payload)
            remote_id = _upload_chunks(client, session, token, path, size,
                                       claimed, store, start_offset=offset)
        else:
            response.raise_for_status()
            remote_id = _id(response.json()['id'])
            _checkpoint(store, claimed, phase='uploaded', offset=size, video_id=remote_id)
        result = _readback(claimed, payload, remote_id, api,
                           checkpoint.get('expected_parts'))
        if result.state == 'pending_verification':
            store.transition(job.id, 'dispatching', 'verifying')
        else:
            _uncertain(store, claimed, remote_id)
        return result
    except Exception:
        return _uncertain(store, claimed, remote_id)


def reconcile_youtube(job: NativeJob, payload: dict, *, brand: Brand, store: NativeStore,
                      client: httpx.Client) -> YouTubeScheduleResult:
    """Read or query the existing upload only. Never sends bytes or starts sessions."""
    _context(job,payload,brand,store)
    attempts=store.get_attempts(job.id)
    checkpoint=attempts[-1]['checkpoint'] if attempts else {}
    remote_id=checkpoint.get('video_id') or job.remote_id
    api=YouTubeMetadataClient(brand,client)
    try:
        if not remote_id and checkpoint.get('session_uri'):
            token=api._token()
            if api._authenticated_channel(token)!=job.account_id: raise ValueError('Channel mismatch')
            response=client.put(_session(checkpoint['session_uri']),headers={'Authorization':f'Bearer {token}','Content-Length':'0','Content-Range':f"bytes */{checkpoint['total_bytes']}"},content=b'')
            if response.status_code==308:
                return _uncertain(store,job)
            response.raise_for_status()
            remote_id=_id(response.json()['id'])
            _checkpoint(store,job,video_id=remote_id)
        if not remote_id: return _uncertain(store,job)
        result=_readback(job,payload,_id(remote_id),api,checkpoint.get('expected_parts'))
        if result.state in {'conflict','uncertain'}: _uncertain(store,job,remote_id)
        return result
    except Exception:
        return _uncertain(store,job,remote_id)
