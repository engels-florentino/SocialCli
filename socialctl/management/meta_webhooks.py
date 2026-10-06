"""Offline webhook boundary; no listener, subscriptions, messaging or mutations.

HMAC authenticates exact raw bytes. Events are hints for a subsequent owned GET,
never an authoritative replacement for remote content or an instruction to write.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import contextmanager

from socialctl.management.meta_schema import MetaError, meta_id
from socialctl.management.resource_changes import ResourceStore
from socialctl.media_registry import parse_json

MAX_BODY = 1024 * 1024
MAX_EVENTS = 20000  # Never evict replay protection silently.


def verify_challenge(mode, provided_token, challenge, expected_token):
    if (mode != "subscribe" or not all(isinstance(v, str) and v for v in (provided_token, expected_token, challenge))
            or len(challenge) > 4096 or not hmac.compare_digest(provided_token.encode(), expected_token.encode())):
        raise MetaError("webhook verification rejected")
    return challenge


class WebhookStore:
    def __init__(self, brand, platform):
        if platform not in {"facebook", "instagram"}:
            raise MetaError("unsupported webhook platform")
        self.brand, self.platform = brand, platform
        self.account_id = meta_id((brand.cuentas.get(platform) or {}).get("page_id" if platform == "facebook" else "ig_user_id"))
        self.root = brand.raiz / ".socialctl" / "meta-webhooks"
        self.path = self.root / f"{platform}-{self.account_id}.sqlite3"

    @contextmanager
    def database(self):
        guard = ResourceStore(self.brand.raiz)
        guard.root = self.root
        guard._ensure_root()
        if self.path.is_symlink():
            raise MetaError("webhook DB does not accept symlinks")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, target TEXT NOT NULL, source_time INTEGER NOT NULL, payload TEXT NOT NULL, stale INTEGER NOT NULL, state TEXT NOT NULL, observation TEXT)")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ingest(self, raw, signature, app_secret):
        if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY or not isinstance(signature, str)
                or not isinstance(app_secret, str) or not app_secret):
            raise MetaError("invalid webhook body/signature/secret")
        expected = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            raise MetaError("invalid webhook signature")
        try:
            data = parse_json(raw, limit=MAX_BODY)
        except (ValueError, TypeError, RecursionError):
            raise MetaError("invalid webhook JSON") from None
        if not isinstance(data, dict) or data.get("object") != ("page" if self.platform == "facebook" else "instagram"):
            raise MetaError("webhook object does not match platform")
        entries = data.get("entry")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise MetaError("empty/excessive webhook entries")
        events = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("id") != self.account_id:
                raise MetaError("webhook account not selected")
            timestamp = entry.get("time")
            changes = entry.get("changes")
            if type(timestamp) is not int or not 0 <= timestamp <= 2**53 or not isinstance(changes, list) or not 1 <= len(changes) <= 100:
                raise MetaError("invalid webhook time/changes")
            for change in changes:
                if not isinstance(change, dict) or change.get("field") not in ({"feed"} if self.platform == "facebook" else {"comments", "mentions"}):
                    raise MetaError("webhook field outside normal content; messaging is not ingested")
                value = change.get("value")
                if not isinstance(value, dict):
                    raise MetaError("invalid webhook value")
                media = value.get("media")
                target = value.get("post_id") if self.platform == "facebook" else value.get("media_id", media.get("id") if isinstance(media, dict) else None)
                meta_id(target, composite=self.platform == "facebook")
                canonical = json.dumps({"object": data["object"], "account": self.account_id, "time": timestamp,
                                        "change": change}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                identifier = hashlib.sha256(canonical.encode()).hexdigest()
                events.append((identifier, target, timestamp, canonical))
        if len(events) > 1000:
            raise MetaError("too many events in one body")
        counts = {"accepted": 0, "duplicates": 0, "out_of_order": 0}
        with self.database() as conn:
            count = conn.execute("SELECT count(*) FROM events").fetchone()[0]
            for identifier, target, timestamp, canonical in events:
                if conn.execute("SELECT 1 FROM events WHERE id=?", (identifier,)).fetchone():
                    counts["duplicates"] += 1
                    continue
                if count >= MAX_EVENTS:
                    raise MetaError("webhook store full; replay evidence is not deleted automatically")
                latest = conn.execute("SELECT max(source_time) FROM events WHERE target=?", (target,)).fetchone()[0]
                stale = latest is not None and timestamp < latest
                conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,NULL)", (identifier, target, timestamp, canonical, int(stale), "pending_read"))
                counts["accepted"] += 1
                counts["out_of_order"] += int(stale)
                count += 1
        return counts

    def events(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise MetaError("event limit out of range")
        if not self.path.exists():
            return []
        with self.database() as conn:
            rows = conn.execute("SELECT id,target,source_time,stale,state FROM events ORDER BY source_time DESC,id LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("id", "target_id", "source_time", "out_of_order", "state"), row)) for row in rows]

    def reconcile(self, client, event_id):
        if (client.brand.nombre, str(client.brand.raiz.resolve()), client.platform.value, client.account_id) != (
                self.brand.nombre, str(self.brand.raiz.resolve()), self.platform, self.account_id):
            raise MetaError("event and client belong to another brand/account")
        with self.database() as conn:
            row = conn.execute("SELECT target,payload FROM events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise MetaError("evento desconocido")
        try:
            payload = json.loads(row[1])["change"]
            if payload["field"] == "mentions":
                observation = client.mentioned_comment(payload["value"].get("comment_id"), row[0])
            else:
                observation = client.content(row[0])
            state = "read_only_observed"
        except MetaError:
            observation, state = {"reason": "not_found_or_inaccessible_is_not_absence"}, "unresolved"
        with self.database() as conn:
            conn.execute("UPDATE events SET state=?,observation=? WHERE id=?", (state, json.dumps(observation), event_id))
        return {"id": event_id, "state": state, "observation": observation, "remote_writes": 0}
