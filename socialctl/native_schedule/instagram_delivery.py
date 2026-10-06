"""Instagram container publishing with durable phase callbacks.

Video uses Meta's direct resumable upload. Images require an approved HTTPS
source whose bytes are compared with the supplied local file before creation.
"""

from hashlib import sha256
import ipaddress
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx

from .api_delivery import DeliveryUncertain
from .models import NativeJob

GRAPH = 'https://graph.facebook.com/v26.0'
_ID = re.compile(r'[A-Za-z0-9_-]+\Z')


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('Invalid Instagram identifier')
    return value


def _upload_uri(value, container_id):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != 'rupload.facebook.com'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment or
            not re.fullmatch(r'/ig-api-upload/v\d+(?:\.\d+)?/' + re.escape(container_id), parsed.path)):
        raise ValueError('Untrusted Instagram upload URI')
    return value


def _public_url(value, allowed_hosts):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or parsed.port not in (None, 443)
            or parsed.query):
        raise ValueError('Approved public HTTPS media URL required')
    host = parsed.hostname
    if host == 'localhost' or not '.' in host:
        raise ValueError('Public media host required')
    if host not in allowed_hosts:
        raise ValueError('Public media host is not explicitly approved')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('IP-literal media hosts are prohibited')
    return value


class InstagramDelivery:
    def __init__(self, client: httpx.Client, access_token: str, *,
                 max_polls: int = 5, sleep_s: float = 60.0,
                 allowed_media_hosts: frozenset[str] = frozenset()):
        if not access_token or max_polls < 1 or sleep_s < 0:
            raise ValueError('Instagram token and polling limits required')
        self.client = client
        self.token = access_token
        self.max_polls = max_polls
        self.sleep_s = sleep_s
        self.allowed_media_hosts = frozenset(allowed_media_hosts)

    def _post(self, url, data):
        response = self.client.post(url, data=data | {'access_token': self.token})
        response.raise_for_status()
        return response.json()

    def _status(self, container_id):
        for index in range(self.max_polls):
            response = self.client.get(f'{GRAPH}/{container_id}', params={
                'fields': 'status_code', 'access_token': self.token})
            response.raise_for_status()
            status = response.json().get('status_code')
            if status == 'FINISHED':
                return
            if status in {'ERROR', 'EXPIRED'}:
                raise DeliveryUncertain('Instagram container failed or expired')
            if index + 1 < self.max_polls:
                time.sleep(self.sleep_s)
        raise DeliveryUncertain('Instagram container processing incomplete')

    def _checked_image_url(self, item):
        url = _public_url(item.get('public_url'), self.allowed_media_hosts)
        local = Path(item['path'])
        expected_digest = sha256()
        with local.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                expected_digest.update(chunk)
        actual = sha256()
        with self.client.stream('GET', url, follow_redirects=False) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                actual.update(chunk)
        if actual.hexdigest() != expected_digest.hexdigest():
            raise ValueError('Hosted image differs from approved original bytes')
        return url

    def _video_container(self, edge, item, media_type, caption, checkpoint,
                         *, carousel_item=False):
        data = {'media_type': media_type, 'upload_type': 'resumable'}
        if media_type == 'STORIES' and caption:
            raise ValueError('Instagram Story caption cannot be published through this API')
        if media_type != 'STORIES':
            data['caption'] = caption
        if carousel_item:
            data['is_carousel_item'] = 'true'
        checkpoint({'phase': 'container_requested'})
        created = self._post(edge, data)
        container_id = _identifier(created.get('id'))
        uri = _upload_uri(created.get('uri'), container_id)
        checkpoint({'phase': 'container_created', 'creation_id': container_id})
        path = Path(item['path'])
        size = path.stat().st_size
        if size <= 0:
            raise ValueError('Nonempty original Instagram video required')
        checkpoint({'phase': 'upload_requested', 'creation_id': container_id})
        with path.open('rb') as original:
            response = self.client.post(uri, headers={
                'Authorization': f'OAuth {self.token}', 'offset': '0',
                'file_size': str(size), 'Content-Type': 'application/octet-stream',
            }, content=original, timeout=None)
        response.raise_for_status()
        if response.json().get('success') is not True:
            raise DeliveryUncertain('Instagram upload not confirmed')
        checkpoint({'phase': 'uploaded', 'creation_id': container_id})
        self._status(container_id)
        checkpoint({'phase': 'container_ready', 'creation_id': container_id})
        return container_id

    def deliver(self, job: NativeJob, payload: dict, checkpoint) -> str:
        if job.platform != 'instagram':
            raise ValueError('Approved Instagram job required')
        account = _identifier(job.account_id)
        edge = f'{GRAPH}/{account}/media'
        media = payload.get('media')
        fmt = payload.get('options', {}).get('format')
        if not isinstance(media, list) or not media:
            raise ValueError('Instagram media required')
        if fmt in {'reel', 'story'} and len(media) == 1:
            creation_id = self._video_container(edge, media[0],
                'REELS' if fmt == 'reel' else 'STORIES', payload['copy'], checkpoint)
        elif fmt == 'image' and len(media) == 1:
            url = self._checked_image_url(media[0])
            checkpoint({'phase': 'container_requested'})
            created = self._post(edge, {'image_url': url, 'caption': payload['copy']})
            creation_id = _identifier(created.get('id'))
            checkpoint({'phase': 'container_created', 'creation_id': creation_id})
            self._status(creation_id)
            checkpoint({'phase': 'container_ready', 'creation_id': creation_id})
        elif fmt == 'carousel' and 2 <= len(media) <= 10:
            children = []
            for index, item in enumerate(media):
                suffix = Path(item['path']).suffix.lower()
                if suffix in {'.mp4', '.mov'}:
                    child = self._video_container(edge, item, 'VIDEO', '',
                        lambda state, index=index: checkpoint(state | {'child_index': index}),
                        carousel_item=True)
                else:
                    url = self._checked_image_url(item)
                    checkpoint({'phase': 'child_requested', 'child_index': index})
                    created = self._post(edge, {'image_url': url,
                        'is_carousel_item': 'true'})
                    child = _identifier(created.get('id'))
                    checkpoint({'phase': 'child_created', 'child_index': index,
                                'creation_id': child})
                    self._status(child)
                children.append(child)
            checkpoint({'phase': 'container_requested', 'children': children})
            created = self._post(edge, {'media_type': 'CAROUSEL',
                'children': ','.join(children), 'caption': payload['copy']})
            creation_id = _identifier(created.get('id'))
            checkpoint({'phase': 'container_created', 'creation_id': creation_id,
                        'children': children})
            self._status(creation_id)
            checkpoint({'phase': 'container_ready', 'creation_id': creation_id})
        else:
            raise ValueError('Unsupported approved Instagram format/media count')

        checkpoint({'phase': 'publish_requested', 'creation_id': creation_id})
        published = self._post(f'{GRAPH}/{account}/media_publish', {
            'creation_id': creation_id})
        return _identifier(published.get('id'))


def read_media(media_id: str, account_id: str, client: httpx.Client,
               access_token: str, *, expected_caption: str,
               expected_format: str) -> dict:
    """Read a published media object and check its owner and approved copy."""
    media_id = _identifier(media_id)
    account_id = _identifier(account_id)
    expected_type = {'reel': 'VIDEO', 'story': 'STORY',
                     'image': 'IMAGE', 'carousel': 'CAROUSEL_ALBUM'}.get(expected_format)
    if expected_type is None:
        raise ValueError('Unsupported Instagram readback format')
    response = client.get(f'{GRAPH}/{media_id}', params={
        'fields': 'id,owner,caption,media_type,permalink,timestamp',
        'access_token': access_token})
    response.raise_for_status()
    item = response.json()
    owner = item.get('owner')
    owner_id = owner.get('id') if isinstance(owner, dict) else owner
    if item.get('id') != media_id or owner_id != account_id:
        raise ValueError('Instagram media differs from approved account or ID')
    if item.get('media_type') != expected_type:
        raise ValueError('Instagram media type differs from approved format')
    if expected_format != 'story' and item.get('caption') != expected_caption:
        raise ValueError('Instagram caption differs from approved copy')
    if not isinstance(item.get('permalink'), str) or not item['permalink'].startswith(
            'https://www.instagram.com/'):
        raise ValueError('Instagram media permalink missing')
    return item
