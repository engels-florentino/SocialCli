"""Bridge approved native occurrences to the existing durable comment machinery.

The native ledger owns media evidence; PublicationStore owns the dependent effect.
No accepted receipt, UI attestation, or partial list authorizes a comment here.
"""
from datetime import datetime
from hashlib import sha256
import json
import uuid

from socialctl.management.meta_comments import CommentError, MetaCommentsClient
from socialctl.models import Platform, PostResult, PostStatus
from socialctl.publication_steps import Publication, PublicationStore, _account, _fingerprint, _first_comment
from .approval import native_api_followup_pending, verify_approval
from .models import NativeObservation, aware
from .store import NativeStore, _now

PREFIX = 'native-schedule:'


def occurrence_id(brand, job):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        ['native-first-comment', str(brand.raiz.resolve()), job.id])))


def provenance(job):
    return PREFIX + json.dumps([job.id, job.approval_digest], separators=(',', ':'))


def _context(brand, store, job, payload):
    if (store.brand != brand.nombre or job.brand != brand.nombre
            or store.path.parent != brand.raiz.resolve() or job.route != 'api'
            or _account(brand, Platform(job.platform))['account_id'] != job.account_id
            or store.get(job.id) != job or store.get_payload(job.id) != payload):
        raise CommentError('Native comment brand/account/approved occurrence mismatch')
    verify_approval(job, payload)
    if (native_api_followup_pending(payload) or job.platform not in {'facebook', 'youtube', 'instagram'} or payload.get('visibility') != 'public'
            or (job.platform == 'instagram' and payload.get('options', {}).get('format') == 'story')):
        raise CommentError('Unsupported scheduled first-comment combination')
    if job.state not in {'dispatching', 'verifying', 'uncertain', 'native_scheduled', 'published'} or not job.attempt_id:
        raise CommentError('Native publication has no submitted occurrence')
    action = store.get_action(job.id)
    if action and action['status'] != 'confirmed':
        raise CommentError('Native action requires reconciliation before comments')


def _target(job, payload, store):
    refs = {job.remote_id} if job.remote_id else set()
    if payload.get('options', {}).get('video_id'):
        refs.add(payload['options']['video_id'])
    for attempt in store.get_attempts(job.id):
        checkpoint = attempt['checkpoint']
        if checkpoint.get('video_id'):
            refs.add(checkpoint['video_id'])
    if len(refs) != 1:
        raise CommentError('Native media target is missing or contradictory; no comment attempted')
    return refs.pop()


def read_published(brand, http, job, payload, remote_id, *, now):
    """Existing fixed-host provider readers, with a stricter public/processed gate."""
    if now < job.publish_at:
        raise CommentError('Approved publication time has not arrived')
    if job.platform == 'youtube':
        from socialctl.management.youtube_community import YouTubeCommunityClient
        from .youtube import _readback
        client = YouTubeCommunityClient(brand, http)
        observed = _readback(job, payload, remote_id, client)
        item = observed.resource or {}
        if (observed.state != 'published' or item.get('status', {}).get('madeForKids') is True
                or item.get('status', {}).get('uploadStatus') != 'processed'
                or item.get('processingDetails', {}).get('processingStatus') != 'succeeded'):
            raise CommentError('YouTube video is not verified public and fully processed')
        return item
    client = MetaCommentsClient(brand, Platform(job.platform), http)
    client.inspect_media(remote_id)  # Exact authenticated actor as well as owner.
    if job.platform == 'facebook':
        from .facebook_reels import read_reel
        item = read_reel(remote_id, job.account_id, http, client._access_token,
            expected_description=payload['copy'], require_published=True)
        if 'paging' in item:
            raise CommentError('Facebook published readback is incomplete')
        return item
    from .instagram_delivery import read_media
    item = read_media(remote_id, job.account_id, http, client._access_token,
        expected_caption=payload['copy'], expected_format=payload['options']['format'])
    if 'paging' in item:
        raise CommentError('Instagram published readback is incomplete')
    try:
        stamp = aware(datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00')))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise CommentError('Instagram published timestamp is missing or invalid') from None
    if stamp > now:
        raise CommentError('Instagram published timestamp is in the future')
    return item


def validate_native_publication(brand, http, data, *, now=None):
    """Also used by comments retry-first; an old public receipt cannot bypass reads."""
    try:
        job_id, digest = json.loads(data['provenance'][len(PREFIX):])
    except (ValueError, TypeError):
        raise CommentError('Invalid native approval provenance') from None
    store = NativeStore(brand.raiz, brand=brand.nombre)
    job = store.get(job_id)
    if job is None:
        raise CommentError('Native approved occurrence is missing')
    payload = store.get_payload(job_id)
    _context(brand, store, job, payload)
    target = _target(job, payload, store)
    if (data['id'] != occurrence_id(brand, job) or digest != job.approval_digest
            or data['platform'] != job.platform or data['text'] != payload.get('first_comment')
            or data['account'] != _account(brand, Platform(job.platform))
            or data['media']['platform_id'] != target):
        raise CommentError('Native comment differs from its exact approved occurrence')
    return read_published(brand, http, job, payload, target, now=_now(now))


def first_comment_status(brand, job, payload):
    if not payload.get('first_comment'):
        return {'status': 'not_requested'}
    identifier = occurrence_id(brand, job)
    result = {'status': 'pending', 'publication_id': identifier}
    store = PublicationStore(brand.raiz)
    if store.path_for(identifier).exists():
        try:
            data = store.load(identifier)
            if data['provenance'] != provenance(job) or data['text'] != payload['first_comment']:
                raise CommentError('Native publication approval changed')
            media = data.get('media') or {}
            result.update(status=media.get('first_comment_status') or 'pending',
                change_id=data['comment_change_id'], comment_id=media.get('first_comment_id'))
        except CommentError:
            result['status'] = 'manual_review'
    return result


def follow_up(brand, http, store, job_id, *, now=None, read_only=False):
    """One stable occurrence lock spans media proof, journal creation and comment step."""
    now = _now(now)
    job = store.get(job_id)
    payload = store.get_payload(job_id)
    if not payload.get('first_comment'):
        return {'status': 'not_requested'}
    publications = PublicationStore(brand.raiz)
    identifier = occurrence_id(brand, job)
    with publications.apply_lock(identifier):
        job = store.get(job_id)
        _context(brand, store, job, payload)
        target = _target(job, payload, store)
        item = read_published(brand, http, job, payload, target, now=now)
        if job.state != 'published':
            # A unique evidence artifact per read survives competing workers and restarts.
            evidence_id = str(uuid.uuid4())
            evidence = {'job_id': job.id, 'approval_digest': job.approval_digest,
                        'observed_at': now.isoformat(), 'remote': item}
            evidence_store = PublicationStore(brand.raiz)
            evidence_store.root = publications.root / 'native-evidence'
            evidence_store.write_json(evidence_id, evidence)
            path = evidence_store.path_for(evidence_id)
            observation = NativeObservation(job_id=job.id, platform=job.platform,
                account_id=job.account_id, content_hash=job.content_hash, approval_digest=job.approval_digest,
                remote_ref=target, publish_at=job.publish_at, observed_at=now, source='api', calendar_visible=False,
                state='published', public_visible=True, processing_complete=True,
                evidence_path=str(path), evidence_sha256=sha256(path.read_bytes()).hexdigest())
            if not store.record_observation(observation, now=now) and store.get(job.id).state != 'published':
                raise CommentError('Native published readback did not satisfy reconciliation')
        if publications.path_for(identifier).exists():
            data = publications.load(identifier)
        else:
            media = PostResult(platform=Platform(job.platform), status=PostStatus.PUBLICADO,
                platform_id=target, publication_id=identifier, first_comment_status='pending')
            data = Publication(id=identifier, brand=brand.nombre, brand_root=str(brand.raiz.resolve()),
                platform=job.platform, slug=job.id, account=_account(brand, Platform(job.platform)),
                text=payload['first_comment'], provenance=provenance(job), fingerprint='',
                created_at=now.isoformat(), state='published', media=media.model_dump(mode='json')).model_dump()
            data['fingerprint'] = _fingerprint(data)
            publications.write_json(identifier, data)
        # This also revalidates the native binding in the shared recovery path.
        _first_comment(brand, http, publications, data, reconcile_only=read_only)
        return first_comment_status(brand, store.get(job.id), payload)
