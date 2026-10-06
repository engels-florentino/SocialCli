"""Approval binds immutable intent and freshly read existing media bytes."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from .models import NativeJob

SCHEMA = 'socialctl.native-schedule.approval.v1'
_SECRET = re.compile(r'(?i)(access.?token|refresh.?token|client.?secret|password|authorization|api.?key|\bbearer\s+\S+|[?&](?:token|sig|signature|key)=)')


def reject_secrets(value):
    """Fail closed before rendering; never interpolate the offending value."""
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    candidate = value if isinstance(value, str) else text
    if _SECRET.search(candidate) or re.search(r'(?i)["\']?(?:token|secret|credentials)["\']?\s*[:=]', candidate):
        raise ValueError('Secret-bearing payload rejected')
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {'token', 'secret', 'credentials', 'upload_url', 'signed_url'}:
                raise ValueError('Secret-bearing payload rejected')
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)
    elif isinstance(value, str):
        # Find intact raw tokens first: decoded whitespace/quotes must never
        # truncate the token and hide later signed/credential parameters.
        for match in re.findall(r'https?://[^\s"<>]+', value, flags=re.IGNORECASE):
            url = urlsplit(match)
            netloc = _decode_component(url.netloc)
            query = _decode_component(url.query)
            fragment = _decode_component(url.fragment)
            # Only known public parameters may cross the preview boundary.
            public = {'v', 't', 'start', 'end', 'list', 'index', 'page', 'q', 'search', 'lang'}
            if ('@' in netloc or fragment or
                any(key.lower() not in public for key, _ in parse_qsl(query, keep_blank_values=True))):
                raise ValueError('Credential-capable URL rejected')


def _decode_component(value: str) -> str:
    """Bounded repeated decoding for inspection only; original copy is retained."""
    for _ in range(8):
        newer = unquote(value)
        if newer == value:
            return value
        value = newer
    raise ValueError('Excessively encoded URL component rejected')



def validate_payload(platform: str, payload: dict) -> None:
    """Validate content semantics separately from file provenance; never rewrite."""
    from socialctl.models import MediaAsset, MediaKind, Platform, PlatformPost
    from socialctl.publication_policy import validate_derivation

    origin = payload.get('content_origin')
    if origin not in {'standalone', 'youtube_long'}:
        raise ValueError('content_origin must explicitly be standalone or youtube_long')
    options = payload.get('options')
    if not isinstance(options, dict):
        raise ValueError('Content options required')
    # There is one canonical source identity, not competing adapter-specific ones.
    for key in ('content_origin', 'source_video_id'):
        if key in options:
            raise ValueError('Declare content_origin/source_video_id at payload level, not options')
    media = payload.get('media')
    if not isinstance(media, list):
        raise ValueError('Media list required')
    if options.get('format') == 'text':
        if platform != 'facebook' or media or not isinstance(payload.get('copy'), str) or not payload['copy'].strip():
            raise ValueError('Text-only content requires Facebook, nonempty copy and no media')
    elif not media:
        raise ValueError('Media content requires existing supplied files')
    assets = []
    for item in media:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise ValueError('Media path required')
        kind = MediaKind.VIDEO if options.get('format') in {'video','reel'} or Path(item['path']).suffix.lower() in {'.mp4','.mov','.webm','.mkv','.avi'} else MediaKind.IMAGE
        assets.append(MediaAsset(path=Path(item['path']),kind=kind))
    post = PlatformPost(platform=Platform(platform),body=payload['copy'],media=assets,
        content_origin=origin,source_video_id=payload.get('source_video_id'),
        first_comment=payload.get('first_comment'))
    errors = validate_derivation(post,require_origin=True)
    if errors:
        raise ValueError('Invalid content origin/source_video_id/first_comment contract')


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def hash_value(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def media_hashes(payload: dict) -> list[dict]:
    reject_secrets(payload)
    media = payload.get('media')
    if not isinstance(media, list) or (not media and payload.get('options', {}).get('format') != 'text'):
        raise ValueError('Existing media with path and origin required')
    result = []
    for item in media:
        if (not isinstance(item, dict) or not isinstance(item.get('path'), str)
            or not isinstance(item.get('origin'), str) or not item['origin'].strip()):
            raise ValueError('Existing media requires path and explicit origin; legacy content is not modified')
        path = Path(item['path'])
        if not path.is_absolute() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError('Media must be an existing nonempty absolute file')
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        result.append({'path': str(path), 'sha256': sha})
    return result


def approval_document(job: NativeJob, payload: dict) -> dict:
    # Lifecycle IDs/timestamps/evidence are not user intent.
    fields = ('brand', 'platform', 'account_id', 'legacy_entry_id', 'publish_at',
              'timezone_name', 'dispatch_after', 'route', 'media_hash', 'content_hash')
    raw = job.model_dump(mode='json')
    document = {'schema': SCHEMA, 'intent': {key:raw[key] for key in fields},
                'payload': payload, 'media': media_hashes(payload)}
    reject_secrets(document)
    return document


def native_digest(job: NativeJob, payload: dict) -> str:
    return hash_value(approval_document(job, payload))


def verify_approval(job: NativeJob, payload: dict, digest: str | None = None) -> str:
    validate_payload(job.platform, payload)
    actual = native_digest(job, payload)
    expected = digest if digest is not None else job.approval_digest
    if not expected or actual != expected:
        raise ValueError('Approval digest missing or changed; show a new preview')
    if hash_value(media_hashes(payload)) != job.media_hash or hash_value(payload) != job.content_hash:
        raise ValueError('Approval digest content/media mismatch; show a new preview')
    return actual
