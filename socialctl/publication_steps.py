"""Durable Meta/YouTube publication occurrences and separately recoverable comments.

The caller has already approved the complete publication preview. Each explicit
publish creates a fresh occurrence. A scheduler supplies a stable occurrence UUID;
comment retry only consumes the original occurrence, never invokes an uploader.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from socialctl.management.meta_comments import (
    CommentError, CommentStore, MetaCommentsClient, apply_comment, prepare_comment,
)
from socialctl.models import Platform, PostResult, PostStatus


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    id: str
    brand: str
    brand_root: str
    platform: str
    slug: str
    account: dict
    text: str | None
    provenance: str
    source_mapping_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)
    fingerprint: str
    state: str = "uploading"
    media: dict | None = None
    comment_change_id: str | None = None


def _fingerprint(data):
    immutable = {key: value for key, value in data.items()
                 if key not in {"fingerprint", "state", "media", "comment_change_id"}}
    return hashlib.sha256(json.dumps(immutable, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


class PublicationStore(CommentStore):
    def __init__(self, brand_root: Path):
        self.root = brand_root / ".socialctl" / "publications"

    def load(self, identifier):
        try:
            data = Publication.model_validate_json(self.path_for(identifier).read_text()).model_dump()
        except (OSError, ValidationError):
            raise CommentError("attempt has no durable journal: manual reconciliation required; do not repeat the comment") from None
        if data["id"] != identifier or data["version"] != 1 or not hmac.compare_digest(data["fingerprint"], _fingerprint(data)):
            raise CommentError("publication journal was modified")
        return data


def _account(brand, platform):
    account = brand.cuentas.get(platform.value) or {}
    if platform is Platform.YOUTUBE:
        return {"account_id": account.get("channel_id")}
    return {"account_id": account.get("page_id" if platform is Platform.FACEBOOK else "ig_user_id"),
            "page_id": (brand.cuentas.get("facebook") or {}).get("page_id")}


def _bound(brand, data):
    platform = Platform(data["platform"])
    if (data["brand"], data["brand_root"], data["account"]) != (
            brand.nombre, str(brand.raiz.resolve()), _account(brand, platform)):
        raise CommentError("brand or account differs from the approved attempt")
    if not data["provenance"]:
        raise CommentError("approval provenance is missing; manual review required")


def _stored_media(data):
    """Validate association independently of current credentials or comment state."""
    if data["media"] is None:
        return None
    try:
        result = PostResult.model_validate(data["media"])
    except ValidationError:
        raise CommentError("invalid media result in the journal; manual review required") from None
    if result.platform.value != data["platform"] or result.publication_id != data["id"]:
        raise CommentError("media result is not linked to its journal")
    return result


def retry_media_blockers(brand, slug, platforms):
    """Read structured brand-local history; do not infer chronology from filenames.

    Old records have no ordering evidence. Any protected matching occurrence holds
    retry; an explicit fresh publish remains available after manual reconciliation.
    An unreadable record cannot safely be classified as unrelated.
    """
    selected = set(platforms) & {Platform.FACEBOOK, Platform.INSTAGRAM, Platform.YOUTUBE}
    blockers = {}
    if not selected:
        return blockers
    store = PublicationStore(brand.raiz)
    try:
        with os.scandir(store.root) as directory:
            identifiers = [entry.name[:-5] for entry in directory if entry.name.endswith(".json")]
    except FileNotFoundError:
        if not store.root.is_symlink():
            return blockers
        raise CommentError("journal directory is inaccessible; retry blocked") from None
    except OSError:
        raise CommentError("journal directory is unreadable; retry blocked") from None
    for identifier in identifiers:
        try:
            data = store.load(identifier)
            if (data["brand"], data["brand_root"]) != (brand.nombre, str(brand.raiz.resolve())):
                raise CommentError("journal is outside its brand directory")
            platform = Platform(data["platform"])
        except (CommentError, ValueError):
            raise CommentError("publication journal is unreadable or lacks a reliable link; retry blocked") from None
        if data["slug"] != slug or platform not in selected:
            continue
        try:
            _bound(brand, data)
            result = _stored_media(data)
            definite_failure = (result is not None and result.status is PostStatus.ERROR
                and data["state"] == "upload_result" and not result.platform_id
                and data["media"].get("riesgo_duplicado") is False)
        except CommentError:
            definite_failure = False
            result = None
        if not definite_failure:
            detail = f"media published {result.platform_id}" if result and result.status is PostStatus.PUBLICADO else "media is uncertain or lacks a verifiable link"
            blockers.setdefault(platform, []).append(f"journal {identifier}: {detail}; do not upload again")
    return blockers


def _comment_issue(result, data):
    result.warnings.append("Media published; first comment needs review. Use comments retry-first with the attempt ID.")
    result.first_comment_status = "manual_review"
    result.first_comment_change_id = data["comment_change_id"]
    return result


def _resume_publication(brand, http, store, data, *, on_media_result=None):
    result = _stored_media(data)
    if result is None:
        return PostResult(platform=Platform(data["platform"]), status=PostStatus.ERROR,
            publication_id=data["id"], riesgo_duplicado=True,
            error="journal has no media result: uncertain outcome; manual reconciliation required")
    try:
        _bound(brand, data)
        if on_media_result is not None:
            on_media_result(result)
        if result.status is not PostStatus.PUBLICADO or not result.platform_id:
            return result  # Keep definite failure distinct from uncertain upload.
        return _first_comment(brand, http, store, data)
    except Exception:
        if result.status is PostStatus.PUBLICADO and result.platform_id:
            return _comment_issue(result, data)
        # No next step attempted; the stored upload outcome remains authoritative.
        result.warnings.append("Could not resume the journal; review manually before taking another step.")
        return result


def _pending_source(brand, data):
    from socialctl.source_links import verify_source_binding
    try:
        verify_source_binding(brand, data['slug'], data.get('source_mapping_digest'))
    except ValueError as exc:
        raise CommentError(str(exc)) from None


def _first_comment(brand, http, store, data, *, retry_rejected=False):
    _bound(brand, data)
    if not data["media"]:
        raise CommentError("media has no durable result; reconcile manually, do not upload again")
    result = PostResult.model_validate(data["media"])
    if result.status is not PostStatus.PUBLICADO or not result.platform_id:
        raise CommentError("media is unconfirmed; comment requires manual review")
    if not data["text"]:
        return result
    if result.platform is Platform.YOUTUBE:
        return _youtube_first_comment(brand, http, store, data, result, retry_rejected=retry_rejected)
    comments = CommentStore(brand.raiz)
    client = MetaCommentsClient(brand, result.platform, http)
    if data["comment_change_id"]:
        change = comments.load(data["comment_change_id"])
        if change.status == "proposed" or (change.status == "rejected" and retry_rejected):
            _pending_source(brand, data)
        if change.status == "rejected" and retry_rejected:
            previous = change.id
            change = prepare_comment(client, comments, media_id=result.platform_id, text=data["text"])
            change.journal.append({"event": "explicit_retry_after_definitive_rejection", "previous_change": previous})
            comments.save(change)
            data["comment_change_id"] = change.id
            store.write_json(data["id"], data)
    else:
        _pending_source(brand, data)
        change = prepare_comment(client, comments, media_id=result.platform_id, text=data["text"])
        data["comment_change_id"] = change.id
        store.write_json(data["id"], data)  # Reference persisted before comment intent/POST.
    if (change.text, change.media_id, change.action, change.brand, change.brand_root) != (
            data["text"], result.platform_id, "add", data["brand"], data["brand_root"]):
        raise CommentError("comment differs from the approved first comment")
    # Approval is inherited only from this immutable, approved publication;
    # callers cannot pass unrelated ChangeSets or silently adopt legacy comments.
    change = apply_comment(client, comments, change.id, change.fingerprint)
    result.warnings = [warning for warning in result.warnings
                       if not warning.startswith("Media published; first comment ")]
    result.first_comment_change_id = change.id
    result.first_comment_id = change.remote_id
    result.first_comment_status = change.status
    if change.status != "verified":
        result.warnings.append(f"Media published; first comment {change.status}. Check comments status {change.id}.")
    data["media"] = result.model_dump(mode="json")
    store.write_json(data["id"], data)
    return result


def _youtube_first_comment(brand, http, store, data, result, *, retry_rejected):
    from socialctl.management.community_changes import CommunityStore, prepare_community, apply_community
    from socialctl.management.youtube_community import YouTubeCommunityClient
    comments = CommunityStore(brand.raiz)
    client = YouTubeCommunityClient(brand, http)
    edit = {"action": "add", "video_id": result.platform_id, "text": data["text"]}
    previous = None
    if data["comment_change_id"]:
        change = comments.load(data["comment_change_id"])
        if change.status == "proposed" or (change.status == "failed" and retry_rejected):
            _pending_source(brand, data)
        if change.status == "failed" and retry_rejected:
            previous = change.id
            change = prepare_community(client, comments, edit)
    else:
        _pending_source(brand, data)
        change = prepare_community(client, comments, edit)
    if (change.target_brand, change.brand_root, change.target_account, change.edit) != (
        data["brand"], data["brand_root"], data["account"]["account_id"], {"version": 1, **edit}):
        raise CommentError("YouTube comment differs from the originally approved text/video/actor")
    if data["comment_change_id"] != change.id:
        if previous:
            change.journal.append({"event": "explicit_retry_after_definitive_rejection", "previous_change": previous})
            comments.save(change)
        data["comment_change_id"] = change.id
        store.write_json(data["id"], data)  # Durable reference before comment POST.
    change = apply_community(client, comments, change.id, change.fingerprint)
    result.warnings = [w for w in result.warnings if not w.startswith("Media published; first comment ")]
    result.first_comment_change_id = change.id
    result.first_comment_id = change.result_id
    result.first_comment_status = change.status
    if change.status != "verified":
        result.warnings.append(f"Media published; first comment {change.status}. Check content youtube-community status {change.id}.")
    data["media"] = result.model_dump(mode="json")
    store.write_json(data["id"], data)
    return result


def retry_first_comment(brand, http, publication_id):
    store = PublicationStore(brand.raiz)
    with store.apply_lock(publication_id):
        return _first_comment(brand, http, store, store.load(publication_id), retry_rejected=True)


def publish_with_steps(post, brand, platform, adapter, http, *, on_media_result=None,
                       occurrence_id=None, provenance="approved-publish-preview", retry_guard=False):
    store = PublicationStore(brand.raiz)
    gate = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([
        "publication-gate", str(brand.raiz.resolve()), post.slug, platform.value])))
    with store.apply_lock(gate):
        if retry_guard:
            blockers = retry_media_blockers(brand, post.slug, [platform])
            if platform in blockers:
                raise CommentError("; ".join(blockers[platform]))
        return _publish_occurrence(post, brand, platform, adapter, http,
            on_media_result=on_media_result, occurrence_id=occurrence_id, provenance=provenance)


def _publish_occurrence(post, brand, platform, adapter, http, *, on_media_result=None,
                        occurrence_id=None, provenance):
    store = PublicationStore(brand.raiz)
    identifier = occurrence_id or str(uuid.uuid4())
    if post.brand != brand.nombre:
        raise CommentError("post brand does not match the approved brand")
    with store.apply_lock(identifier):
        if store.path_for(identifier).exists():
            # Stable scheduler occurrence: never upload again after an interruption.
            existing = store.load(identifier)
            if (existing["slug"], existing["platform"], existing["brand"], existing["brand_root"]) != (
                    post.slug, platform.value, brand.nombre, str(brand.raiz.resolve())):
                raise CommentError("journal UUID belongs to another post, platform or brand")
            return _resume_publication(brand, http, store, existing, on_media_result=on_media_result)
        data = Publication(id=identifier, brand=brand.nombre, brand_root=str(brand.raiz.resolve()),
            platform=platform.value, slug=post.slug, account=_account(brand, platform),
            text=post.platforms[platform].first_comment, provenance=provenance,
            source_mapping_digest=post.source_mapping_digest, fingerprint="").model_dump()
        data["fingerprint"] = _fingerprint(data)
        try:
            store.write_json(identifier, data)
        except CommentError:
            return PostResult(platform=platform, status=PostStatus.ERROR,
                              error="could not save the intent; publication was not attempted")
        try:
            result = adapter.publish(post.platforms[platform], brand, http)
        except Exception:
            result = PostResult(platform=platform, status=PostStatus.ERROR,
                                error="publication interrupted; remote outcome is uncertain", riesgo_duplicado=True)
        result.publication_id = identifier
        if data["text"]:
            result.first_comment_status = "pending"
        data["media"] = result.model_dump(mode="json")
        data["state"] = "published" if result.status is PostStatus.PUBLICADO else "upload_result"
        try:
            store.write_json(identifier, data)
            if on_media_result is not None:
                on_media_result(result)  # Critical: a failure blocks the next remote step.
        except Exception:
            result.warnings.append("Media persistence did not complete; comment blocked. Review the journal manually.")
            return result
        if result.status is not PostStatus.PUBLICADO or not result.platform_id:
            return result
        try:
            return _first_comment(brand, http, store, data)
        except Exception:
            return _comment_issue(result, data)
