"""Facebook Page Reel START/upload/FINISH through the official Graph endpoints."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .api_delivery import DeliveryUncertain
from .models import NativeJob

GRAPH = 'https://graph.facebook.com/v26.0'
_IDENTIFIER = re.compile(r'[A-Za-z0-9_-]+\Z')


def _safe_identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError('Invalid Graph identifier')
    return value


def _upload_url(value: str, video_id: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'rupload.facebook.com'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment or
            not re.fullmatch(r'/video-upload/v\d+(?:\.\d+)?/' + re.escape(video_id), parsed.path)):
        raise ValueError('Untrusted Reel upload URL')
    return value


class FacebookReelDelivery:
    def __init__(self, client: httpx.Client, page_token: str, *, clock=None):
        if not page_token:
            raise ValueError('Page token required')
        self.client = client
        self.page_token = page_token
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _schedule_time(self, job: NativeJob) -> int:
        # Conservative product window; not a claim about every Graph format.
        delta = job.publish_at - self.clock()
        if not timedelta(minutes=10) < delta <= timedelta(days=28):
            raise ValueError('Reel schedule window requires more than 10 minutes and at most 28 days')
        if job.publish_at.microsecond:
            raise ValueError('Reel schedule window requires whole seconds; no silent time rounding')
        return int(job.publish_at.timestamp())

    def deliver(self, job: NativeJob, payload: dict, checkpoint) -> str:
        if job.platform != 'facebook' or payload.get('options', {}).get('format') != 'reel':
            raise ValueError('Approved Facebook Reel required')
        if len(payload.get('media', [])) != 1:
            raise ValueError('One original Reel file required')
        mode = payload.get('delivery_mode', 'native_schedule')
        if mode not in {'native_schedule', 'at_time'}:
            raise ValueError('Unknown delivery mode; new approval required')
        if mode == 'native_schedule':
            self._schedule_time(job)
        page_id = _safe_identifier(job.account_id)
        path = Path(payload['media'][0]['path'])
        size = path.stat().st_size
        if size <= 0 or not path.is_file():
            raise ValueError('Nonempty original Reel file required')
        edge = f'{GRAPH}/{page_id}/video_reels'

        checkpoint({'phase': 'start_requested'})
        response = self.client.post(edge, data={
            'access_token': self.page_token, 'upload_phase': 'START'})
        response.raise_for_status()
        started = response.json()
        video_id = _safe_identifier(started.get('video_id'))
        upload_url = _upload_url(started.get('upload_url'), video_id)
        checkpoint({'phase': 'started', 'video_id': video_id})

        checkpoint({'phase': 'upload_requested', 'video_id': video_id})
        with path.open('rb') as original:
            response = self.client.post(upload_url, headers={
                'Authorization': f'OAuth {self.page_token}',
                'offset': '0', 'file_size': str(size),
                'Content-Type': 'application/octet-stream',
            }, content=original, timeout=None)
        response.raise_for_status()
        if response.json().get('success') is not True:
            raise DeliveryUncertain('Reel upload was not confirmed')
        checkpoint({'phase': 'uploaded', 'video_id': video_id})

        finish = {
            'access_token': self.page_token, 'upload_phase': 'FINISH',
            'video_id': video_id, 'video_state': 'PUBLISHED',
            'description': payload['copy'],
        }
        if mode == 'native_schedule':
            # A long upload must not silently turn an expired schedule into now.
            finish.update(video_state='SCHEDULED', scheduled_publish_time=str(self._schedule_time(job)))
        checkpoint({'phase': 'finish_requested', 'video_id': video_id})
        response = self.client.post(edge, data=finish)
        response.raise_for_status()
        if response.json().get('success') is not True:
            raise DeliveryUncertain('Reel finalization was not confirmed')
        if mode == 'native_schedule':
            checkpoint({'phase': 'schedule_submitted', 'video_id': video_id})
            read_reel(video_id, page_id, self.client, self.page_token,
                      expected_description=payload['copy'], expected_publish_at=job.publish_at)
            checkpoint({'phase': 'schedule_verified', 'video_id': video_id,
                        'scheduled_publish_time': job.publish_at.isoformat()})
        return video_id


def read_reel(video_id: str, page_id: str, client: httpx.Client,
              page_token: str, *, expected_description: str,
              expected_publish_at: datetime | None = None) -> dict:
    """Read the Video node and compare ownership and approved description."""
    video_id = _safe_identifier(video_id)
    page_id = _safe_identifier(page_id)
    fields = 'id,description,from,status'
    if expected_publish_at is not None:
        fields += ',published,scheduled_publish_time'
    response = client.get(f'{GRAPH}/{video_id}', params={
        'fields': fields, 'access_token': page_token})
    response.raise_for_status()
    item = response.json()
    if item.get('id') != video_id or item.get('from', {}).get('id') != page_id:
        raise ValueError('Reel readback belongs to another Page')
    if item.get('description') != expected_description:
        raise ValueError('Reel description differs from approved copy')
    if expected_publish_at is not None:
        try:
            stamp = datetime.fromisoformat(item['scheduled_publish_time'].replace('Z', '+00:00'))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError('Reel schedule readback is missing or invalid') from None
        if item.get('published') is not False or stamp.utcoffset() is None or stamp != expected_publish_at:
            raise ValueError('Reel schedule differs from the approved time or is already public')
    return item
