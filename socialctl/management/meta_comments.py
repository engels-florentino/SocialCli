"""Owned-media Meta comments; exact approval, durable intent, no ambiguous replay.

Facebook Login only, Graph v26.0. A successful create does not prove pinning or
link clickability. Missing/partial comment reads never prove remote absence.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from contextlib import ExitStack
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from socialctl.auth import AuthError, obtener_token
from socialctl.brands import Brand
from socialctl.management.changes import ChangeError, ChangeStore, now_utc
from socialctl.models import Platform

GRAPH = "https://graph.facebook.com/v26.0"


class CommentError(ChangeError):
    """Sanitized read, binding, approval or persistence failure."""


class CommentRejected(CommentError):
    pass


class CommentUncertain(CommentError):
    pass


class CommentCursorRejected(CommentError):
    """Graph rejected an opaque `after` cursor supplied by this client."""


def _id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:_[0-9]+)*", value):
        raise CommentError("Meta requires a numeric ID, not a URL or path")
    return value


class MetaCommentsClient:
    def __init__(self, brand: Brand, platform: Platform, client: httpx.Client):
        if platform not in {Platform.FACEBOOK, Platform.INSTAGRAM}:
            raise CommentError("comments available only for Facebook and Instagram")
        self.brand, self.platform, self.client = brand, platform, client
        self._access_token = None

    @property
    def account_id(self):
        key = "page_id" if self.platform is Platform.FACEBOOK else "ig_user_id"
        return _id((self.brand.cuentas.get(self.platform.value) or {}).get(key))

    def _request(self, method, path, **kwargs):
        try:
            # Keep the authenticated token identical across preflight, mutation
            # and read-back even if another process rotates the credential file.
            if self._access_token is None:
                self._access_token = obtener_token(self.brand, self.platform, self.client)
            token = self._access_token
        except AuthError:
            raise CommentError("Meta requires valid brand credentials") from None
        writing = method != "GET"
        probe_budget = getattr(self, '_probe_budget', None)
        if probe_budget is not None and not writing:
            probe_budget.check()
            kwargs['timeout'] = min(10, probe_budget.remaining())
            kwargs['extensions'] = {'socialcli_read_budget':probe_budget}
        try:
            response = self.client.request(method, f"{GRAPH}/{path}",
                headers={"Authorization": f"Bearer {token}"}, follow_redirects=False, **kwargs)
        except Exception:
            error = CommentUncertain if writing else CommentError
            raise error("Meta: transporte interrumpido; resultado desconocido" if writing else
                        "Meta: failed to complete read") from None
        if not response.is_success:
            params = kwargs.get("params") or {}
            if method == "GET" and isinstance(params.get("after"), str):
                try:
                    remote_error = response.json().get("error")
                except Exception:
                    remote_error = None
                if isinstance(remote_error, dict):
                    message = remote_error.get("message")
                    if (remote_error.get("code") == 100 and isinstance(message, str)
                            and "cursor" in message.casefold()):
                        raise CommentCursorRejected(
                            "Meta rejected saved incremental cursor")
            error = (CommentUncertain if response.status_code >= 500 or response.is_redirect
                     else CommentRejected) if writing else CommentError
            from socialctl.management.meta_errors import graph_error
            raise error(graph_error(response, method, path, token))
        try:
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise ValueError()
        except Exception:
            raise (CommentUncertain if writing else CommentError)("Meta: unverifiable response") from None
        return data

    def inspect_media(self, media_id):
        media_id = _id(media_id)
        configured = self.account_id
        me = self._request("GET", "me", params={"fields": "id,instagram_business_account"}
                           if self.platform is Platform.INSTAGRAM else {"fields": "id"})
        page = _id((self.brand.cuentas.get("facebook") or {}).get("page_id"))
        if me.get("id") != page:
            raise CommentError("authenticated Page does not match brand")
        if self.platform is Platform.INSTAGRAM and (me.get("instagram_business_account") or {}).get("id") != configured:
            raise CommentError("authenticated Instagram account does not match brand")
        owner_field = "from" if self.platform is Platform.FACEBOOK else "owner"
        media = self._request("GET", media_id, params={"fields": f"id,{owner_field}"})
        if media.get("id") != media_id or (media.get(owner_field) or {}).get("id") != configured:
            raise CommentError("exact media owner was not verified")
        return {"id": media_id, "owner_id": configured, "authenticated_page": page}

    def list_comments(self, media_id, *, parent_id=None, max_pages=100, include_moderation=False,
                      after=None):
        if type(include_moderation) is not bool:
            raise CommentError("include_moderation must be boolean")
        self.inspect_media(media_id)
        if parent_id:
            self.find_comment(media_id, parent_id)
        return self._list(parent_id or media_id,
            "replies" if parent_id and self.platform is Platform.INSTAGRAM else "comments", max_pages,
            include_moderation=include_moderation, after=after)

    def _list(self, target, edge, max_pages=100, *, include_moderation=False, after=None):
        path = f"{_id(target)}/{edge}"
        fields = ("id,message,from{id,name},created_time,parent,is_hidden"
                  if self.platform is Platform.FACEBOOK else
                  "id,text,from{id,username},timestamp,parent_id,hidden")
        if not include_moderation:
            fields = ("id,message,from{id,name},created_time,parent"
                      if self.platform is Platform.FACEBOOK else
                      "id,text,from{id,username},timestamp,parent_id")
        params = {"fields": fields, "limit": "100"}
        if after is not None:
            if not isinstance(after, str) or not re.fullmatch(r"[A-Za-z0-9_=-]{1,2048}", after):
                raise CommentError("invalid incremental cursor")
            params["after"] = after
        rows, cursors, last_cursor = [], ({after} if after else set()), None
        for _ in range(max_pages):
            try:
                payload = self._request("GET", path, params=params)
            except KeyboardInterrupt:
                if not rows: raise
                return {"data":rows, "complete":False, "absence_proven":False,
                        "next_cursor":params.get("after"), "error":"Read interrupted", "interrupted":True}
            except CommentCursorRejected as exc:
                if not rows: raise
                return {"data":rows, "complete":False, "absence_proven":False,
                        "next_cursor":None, "error":str(exc)}
            except CommentError as exc:
                if not rows: raise
                return {"data":rows, "complete":False, "absence_proven":False,
                        "next_cursor":params.get("after"), "error":str(exc)}
            data = payload.get("data")
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                if not rows: raise CommentError("Meta: invalid comments list")
                return {"data":rows, "complete":False, "absence_proven":False,
                        "next_cursor":params.get("after"), "error":"Meta: invalid later comments page"}
            rows.extend(data)
            paging = payload.get("paging", {})
            if not isinstance(paging, dict):
                break
            if not paging.get("next"):
                return {"data": rows, "complete": True, "absence_proven": False,
                        "next_cursor": None}
            cursor = (paging.get("cursors") or {}).get("after")
            if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_=-]{1,2048}", cursor) or cursor in cursors:
                break
            cursors.add(cursor)
            last_cursor = cursor
            params = {**params, "after": cursor}
        return {"data": rows, "complete": False, "absence_proven": False,
                "next_cursor": last_cursor}

    def find_comment(self, media_id, comment_id, *, parent_id=None, own=False):
        comment_id = _id(comment_id)
        result = self.list_comments(media_id, parent_id=parent_id)
        matches = [row for row in result["data"] if row.get("id") == comment_id]
        if len(matches) != 1:
            raise CommentError("comment not verified on media; absence or permissions inconclusive")
        row = matches[0]
        if self.platform is Platform.FACEBOOK:
            explicit_parent = (row.get('parent') or {}).get('id')
            if explicit_parent is not None and explicit_parent != (parent_id or media_id):
                raise CommentError('comment parent differs from requested target')
        elif row.get('parent_id') is not None and row['parent_id'] != parent_id:
            raise CommentError('comment parent differs from requested target')
        field = "message" if self.platform is Platform.FACEBOOK else "text"
        if not isinstance(row.get(field), str):
            raise CommentError("comment does not return verifiable text")
        author = row.get("from") or {}
        if own and author.get("id") != self.account_id:
            raise CommentError("comment's own author was not verified by ID")
        return {"id": comment_id, "text": row[field], "author_id": author.get("id"),
                "media_id": media_id, "parent_id": parent_id}

    def inspect_comment(self, comment_id: str) -> dict:
        if self.platform is not Platform.FACEBOOK:
            raise CommentError('direct comment inspection is supported here only for Facebook')
        comment_id = _id(comment_id)
        return self._request('GET', comment_id,
            params={'fields':'id,message,from{id},parent{id},object{id}'})

    def verify_comment(self, change):
        from socialctl.read_budget import ReadBudget
        self._probe_budget = ReadBudget(10)
        try:
            return self._verify_comment(change)
        except httpx.HTTPError:
            raise CommentError('Meta comment verification exceeded its read budget or failed') from None
        finally:
            self._probe_budget = None

    def _verify_comment(self, change):
        current = None
        if self.platform is Platform.FACEBOOK:
            self.inspect_media(change.media_id)
            row = None
            # Read-only eventual-consistency probes. A missing direct read can fall
            # back to exact membership; a contradictory object never becomes proof.
            for _ in range(3):
                try:
                    row = self.inspect_comment(change.remote_id)
                    break
                except CommentError:
                    pass
            if row is not None:
                parent = (row.get('parent') or {}).get('id')
                media = (row.get('object') or {}).get('id')
                target_matches = (parent == change.parent_id and media in {None, change.media_id}) if change.parent_id else (
                    media == change.media_id and parent is None or parent == change.media_id and media in {None, change.media_id})
                if row.get('id') != change.remote_id or (row.get('from') or {}).get('id') != self.account_id or not target_matches:
                    raise CommentError('direct comment identity, actor or target does not match approved effect')
                current = {'id':row['id'], 'text':row.get('message'), 'author_id':self.account_id,
                    'media_id':change.media_id, 'parent_id':change.parent_id}
        if current is None:
            current = self.find_comment(change.media_id, change.remote_id,
                                        parent_id=change.parent_id, own=True)
        if current["text"] != change.text:
            raise CommentError("remote text differs from approved text")
        return current

    def write(self, change):
        if change.action == "delete":
            result = self._request("DELETE", _id(change.comment_id))
        elif change.action == "edit":
            result = self._request("POST", _id(change.comment_id), data={"message": change.text})
        else:
            edge = "replies" if change.action == "reply" and self.platform is Platform.INSTAGRAM else "comments"
            result = self._request("POST", f"{_id(change.parent_id or change.media_id)}/{edge}",
                                   data={"message": change.text})
        if change.action in {"delete", "edit"}:
            if result.get("success") is not True:
                raise CommentUncertain("Meta did not confirm comment mutation")
            return change.comment_id
        try:
            return _id(result.get("id"))
        except CommentError:
            raise CommentUncertain("Meta did not return a valid comment ID; do not repeat") from None


class CommentChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: Literal[1] = 1
    platform: Literal["facebook", "instagram"]
    brand: str
    brand_root: str
    account_id: str
    authenticated_page: str
    media_id: str
    action: Literal["add", "reply", "edit", "delete"]
    parent_id: str | None = None
    comment_id: str | None = None
    text: str | None = None
    before: dict
    created_at: str
    inbox_binding: dict[str, str] | None = None
    fingerprint: str = ""
    status: Literal["proposed", "applying", "uncertain", "rejected", "verified", "applied_unverified"] = "proposed"
    remote_id: str | None = None
    candidates: list[str] = Field(default_factory=list)
    journal: list[dict] = Field(default_factory=list)


MUTABLE = {"fingerprint", "status", "remote_id", "candidates", "journal"}


def fingerprint(change):
    # `inbox_binding` was added after version 1 shipped. Excluding its default
    # preserves fingerprints for pre-existing standalone ChangeSets, while a
    # populated binding becomes immutable and is covered by the approval hash.
    excluded = MUTABLE | ({"inbox_binding"} if change.inbox_binding is None else set())
    data = change.model_dump(mode="json", exclude=excluded)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


class CommentStore(ChangeStore):
    """Separate schema/namespace, shared UUID validation and interprocess lock."""
    def __init__(self, brand_root: Path):
        self.root = brand_root / ".socialctl" / "comments"

    def load(self, change_id):
        try:
            change = CommentChange.model_validate_json(self.path_for(change_id).read_text())
        except (OSError, ValidationError):
            raise CommentError("failed to read comment ChangeSet") from None
        if change.id != change_id or not hmac.compare_digest(fingerprint(change), change.fingerprint):
            raise CommentError("comment proposal was altered")
        return change

    def save(self, change):
        if not hmac.compare_digest(fingerprint(change), change.fingerprint):
            raise CommentError("comment proposal was altered")
        self.write_json(change.id, change.model_dump(mode="json"))

    def write_json(self, identifier, payload):
        path = self.path_for(identifier)
        temporary = None
        try:
            self._ensure_root()
            fd, temporary = tempfile.mkstemp(prefix=f"{identifier}.", suffix=".tmp", dir=self.root)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._sync_directory(self.root)
        except OSError:
            if temporary:
                try: os.unlink(temporary)
                except OSError: pass
            raise CommentError("failed to persist durable journal; next step blocked") from None


def _validate_operation(client, *, action, text, parent_id, comment_id):
    if action not in {"add", "reply", "edit", "delete"}:
        raise CommentError("unknown action")
    if action == "edit" and client.platform is Platform.INSTAGRAM:
        raise CommentError("Instagram: text editing is unavailable; content will not be deleted and recreated")
    if action != "delete" and (not isinstance(text, str) or not text.strip()):
        raise CommentError("explicit nonempty text required")
    if action == "delete" and text is not None:
        raise CommentError("delete does not accept text")
    if ((action == "reply" and parent_id is None) or (action == "add" and parent_id is not None)
            or (action in {"edit", "delete"}) != (comment_id is not None)):
        raise CommentError("parent/comment IDs incompatible with action")


def prepare_comment(client, store, *, media_id, text=None, action="add", parent_id=None,
                    comment_id=None, inbox_binding=None):
    _validate_operation(client, action=action, text=text, parent_id=parent_id, comment_id=comment_id)
    media = client.inspect_media(media_id)
    before = {"media": media}
    if parent_id:
        before["parent"] = client.find_comment(media_id, parent_id)
    if comment_id:
        before["comment"] = client.find_comment(media_id, comment_id, parent_id=parent_id, own=True)
    change = CommentChange(id=str(uuid.uuid4()), platform=client.platform.value,
        brand=client.brand.nombre, brand_root=str(client.brand.raiz.resolve()),
        account_id=client.account_id, authenticated_page=media["authenticated_page"],
        media_id=media_id, action=action, parent_id=parent_id, comment_id=comment_id,
        text=text, before=before, created_at=now_utc().isoformat(),
        inbox_binding=inbox_binding)
    change.fingerprint = fingerprint(change)
    store.save(change)
    return change


def _bound(client, change):
    _validate_operation(client, action=change.action, text=change.text,
                        parent_id=change.parent_id, comment_id=change.comment_id)
    if (change.brand, change.brand_root, change.platform, change.account_id) != (
        client.brand.nombre, str(client.brand.raiz.resolve()), client.platform.value, client.account_id):
        raise CommentError("configured brand/account does not match proposal")
    if client.inspect_media(change.media_id) != change.before["media"]:
        raise CommentError("remote identity or ownership changed")


def _event(change, event):
    change.journal.append({"at": now_utc().isoformat(), "event": event})


def _reconcile(client, store, change):
    _bound(client, change)
    if change.status in {"verified", "rejected", "proposed"}:
        return change
    try:
        if change.remote_id and change.action != "delete":
            client.verify_comment(change)
            change.status = "verified"
            _event(change, "read_back_verified")
        elif change.action == "delete":
            # A missing node / empty edge can also mean lost permission.
            client.list_comments(change.media_id)
            _event(change, "delete_read_back_not_proof_of_absence")
        else:
            listing = client.list_comments(change.media_id, parent_id=change.parent_id)
            field = "message" if client.platform is Platform.FACEBOOK else "text"
            change.candidates = [row["id"] for row in listing["data"]
                if row.get(field) == change.text and isinstance(row.get("id"), str)]
            change.status = "uncertain"
            _event(change, "candidates_are_not_creation_proof")
    except CommentError:
        _event(change, "read_back_incomplete_or_denied")
    store.save(change)
    return change


def reconcile_comment(client, store, change_id):
    with store.apply_lock(change_id):
        return _reconcile(client, store, store.load(change_id))


def operation_key(change):
    return json.dumps([change.brand_root, change.platform, change.account_id, change.media_id])


def apply_comment(client, store, change_id, approval_digest):
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        if not hmac.compare_digest(approval_digest, change.fingerprint):
            raise CommentError("approval does not match exact fingerprint")
        locks.enter_context(store.apply_lock(str(uuid.uuid5(uuid.NAMESPACE_URL, operation_key(change)))))
        _bound(client, change)
        if change.status != "proposed":
            return _reconcile(client, store, change)
        for path in store.root.glob('*.json'):
            other = store.load(path.stem)
            if other.id != change.id and operation_key(other) == operation_key(change):
                if other.status in {'applying','uncertain'}:
                    raise CommentError(f'uncertain outcome pending in {other.id}; another UUID cannot replay it')
                if other.status == 'verified' and (other.action,other.parent_id,other.comment_id,other.text) == (change.action,change.parent_id,change.comment_id,change.text):
                    raise CommentError('identical verified operation already exists; do not duplicate')
        if change.parent_id and client.find_comment(change.media_id, change.parent_id) != change.before["parent"]:
            raise CommentError("parent comment changed since preview")
        if change.comment_id and client.find_comment(change.media_id, change.comment_id,
                parent_id=change.parent_id, own=True) != change.before["comment"]:
            raise CommentError("comment changed since preview")
        change.status = "applying"
        _event(change, "approved_exact_digest_and_write_intent")
        store.save(change)  # Failure here MUST block remote mutation.
        try:
            change.remote_id = client.write(change)
        except CommentRejected:
            change.status = "rejected"
            _event(change, "remote_rejected_check_permissions")
        except CommentUncertain:
            change.status = "uncertain"
            _event(change, "remote_outcome_uncertain_never_auto_repost")
        else:
            change.status = "applied_unverified" if change.action == "delete" else "uncertain"
            _event(change, "remote_id_received")
        store.save(change)  # ID durable BEFORE read-back or any other remote step.
        return _reconcile(client, store, change)
