"""Durable account-bound content snapshots, independent of drafts and metrics."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from socialctl.identity import IdentityClient, ReadError, remote_id
from socialctl.inventory_sources import read_page
from socialctl.media_registry import safe
from socialctl.migration_files import write_json
from socialctl.scheduler import ScheduleStore


def now():
    return datetime.now(timezone.utc).isoformat()


class InventoryStore:
    def __init__(self, brand, platform, account_id):
        self.binding = {"brand": brand.nombre, "brand_root": str(brand.raiz.resolve()),
                        "platform": platform.value, "account_id": remote_id(account_id)}
        self.base = safe(brand.raiz / ".socialctl")
        key = hashlib.sha256(account_id.encode()).hexdigest()
        self.path = safe(self.base / "inventory" / platform.value / key / "snapshot.json")

    def load(self):
        safe(self.path)
        if not self.path.exists():
            return {"version": 1, **self.binding, "items": {}, "sync": None}
        try:
            data = json.loads(self.path.read_bytes())
            if data["version"] != 1 or any(data.get(k) != v for k, v in self.binding.items()):
                raise ValueError()
            if not isinstance(data["items"], dict):
                raise ValueError()
            for key, row in data["items"].items():
                if row["remote_id"] != remote_id(key) or any(row.get(k) != self.binding[k] for k in ("brand", "platform", "account_id")):
                    raise ValueError()
            return data
        except (ValueError, KeyError, TypeError, OSError):
            raise ReadError("inventory_invalid") from None

    @staticmethod
    def public_items(state):
        """Sanitize one already-loaded snapshot without reopening its path."""
        return [{k: v for k, v in row.items() if k != "raw"}
                for _, row in sorted(state["items"].items())]

    def list_items(self):
        return self.public_items(self.load())

    def _prepare(self):
        chain = [self.base, self.base / "inventory", self.path.parent.parent, self.path.parent]
        for directory in chain:
            safe(directory).mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
            ScheduleStore._sync_directory(directory.parent)

    @contextmanager
    def lock(self):
        self._prepare()
        fd = os.open(safe(self.path.parent / "sync.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ReadError("inventory_locked") from None
            yield
        finally:
            os.close(fd)

    def save(self, state):
        safe(self.path)
        write_json(self.path, state)


def sync_content(brand, platform, client, *, max_pages=20, resume=False):
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ReadError("invalid_page_limit")
    probe = IdentityClient(brand, platform, client)
    identity = probe.verify()  # Wrong identity cannot even create a local inventory.
    store = InventoryStore(brand, platform, probe.account_id)
    with store.lock():
        state = store.load()
        previous = state.get("sync")
        if resume:
            if not previous or previous["complete"] or not previous.get("resumable"):
                raise ReadError("resume_unavailable")
            run = previous
        else:
            run = {"id": str(uuid.uuid4()), "started_at": now(), "complete": False,
                   "next_cursor": None, "cursors": [], "seen_ids": [], "pages": 0,
                   "missing_resource_ids": [], "resumable": True}
        run["failure_class"] = None
        run["identity"] = identity
        for _ in range(max_pages):
            requested = run["next_cursor"]
            try:
                items, following, missing, source = read_page(probe, requested)
            except ReadError as exc:
                run["failure_class"] = exc.code
                break
            observed_at = now()
            for row in items:
                identifier = row["remote_id"]
                old = state["items"].get(identifier) or {}
                row["first_observed_at"] = old.get("first_observed_at", observed_at)
                row["last_observed_at"] = observed_at
                row["last_sync_id"] = run["id"]
                state["items"][identifier] = row
            run["seen_ids"] = sorted(set(run["seen_ids"]) | {i["remote_id"] for i in items})
            run["missing_resource_ids"] = sorted(set(run["missing_resource_ids"]) | set(missing))
            run["source"] = source
            run["pages"] += 1
            if requested:
                run["cursors"].append(requested)
            run["next_cursor"] = following
            if following and following in run["cursors"]:
                run["failure_class"], run["resumable"] = "repeated_cursor", False
            elif not following:
                run["complete"], run["resumable"] = True, False
                for identifier, row in state["items"].items():
                    if identifier not in run["seen_ids"]:
                        row["presence"] = "not_observed"
                        row["absence_observed_at"] = observed_at
            run["updated_at"] = observed_at
            state["sync"] = run
            store.save(state)  # Page and checkpoint commit in one atomic durable file.
            if run["complete"] or run["failure_class"]:
                break
        if not run["complete"] and not run["failure_class"]:
            run["failure_class"] = "page_limit"
        run["updated_at"] = now()
        state["sync"] = run
        store.save(state)
        return {"version": 1, "brand": brand.nombre, "platform": platform.value,
                "account_id": probe.account_id, "identity": identity, "sync_id": run["id"],
                "complete": run["complete"], "failure_class": run["failure_class"],
                "pagination_complete": run["complete"],
                "details_complete": run["complete"] and not run["missing_resource_ids"],
                "resume_available": run["resumable"], "pages": run["pages"],
                "observed_items": len(run["seen_ids"]), "stored_items": len(state["items"]),
                "missing_resource_ids": run["missing_resource_ids"], "deletion_proven": False,
                "source": run.get("source"), "updated_at": run["updated_at"]}
