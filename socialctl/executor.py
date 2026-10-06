"""Per-brand slot admission and durable executor heartbeat.

Call heartbeat/admitted_groups while holding ScheduleStore.executor_lock.
Slot windows own elapsed instants: where windows overlap the latest start wins.
The end is exclusive; previous-day windows may extend across midnight.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from socialctl.migration_files import write_json
from socialctl.scheduler import ScheduleEntry, ScheduleError, ScheduleStore


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timezone: str
    slots: list[str] = Field(min_length=1)
    window_minutes: int = Field(gt=0, le=1440)
    max_groups_per_slot: int = Field(gt=0, le=100)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("invalid IANA timezone") from exc
        return value

    @field_validator("slots")
    @classmethod
    def valid_slots(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", slot) for slot in value):
            raise ValueError("slots must be unique HH:MM values")
        return sorted(value)

    def active_slot(self, moment: datetime) -> str | None:
        if moment.utcoffset() is None:
            raise ScheduleError("the clock requires a timezone")
        zone = ZoneInfo(self.timezone)
        instant = moment.astimezone(timezone.utc)
        today = instant.astimezone(zone).date()
        candidates = []
        for day in (today - timedelta(days=1), today):
            for slot in self.slots:
                wall = datetime.combine(day, time.fromisoformat(slot))
                start = wall.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
                if start.astimezone(zone).replace(tzinfo=None) != wall:
                    continue  # nonexistent wall time
                if start <= instant < start + timedelta(minutes=self.window_minutes):
                    candidates.append((start, f"{day.isoformat()}/{slot}"))
        return max(candidates)[1] if candidates else None


def load_state(store: ScheduleStore) -> dict:
    path = store.root / "executor-state.json"
    if not path.exists():
        return {"version": 1, "slots": {}}
    try:
        state = json.loads(path.read_bytes())
        if state["version"] != 1 or not isinstance(state["slots"], dict):
            raise ValueError()
        for count in state["slots"].values():
            if type(count) is not int or count < 0:
                raise ValueError()
        return state
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ScheduleError("executor state is unreadable; review required") from exc


def save_state(store: ScheduleStore, state: dict) -> None:
    try:
        write_json(store.root / "executor-state.json", state)
    except OSError as exc:
        raise ScheduleError("could not save executor state") from exc


def heartbeat(store: ScheduleStore, state: str, moment: datetime) -> None:
    data = load_state(store)
    started = moment.isoformat() if state == "started" else data.get("heartbeat", {}).get("started_at")
    data["heartbeat"] = {"state": state, "timestamp": moment.isoformat(),
                         "started_at": started,
                         "backend": "sqlite" if store.database_path.exists() else "json"}
    save_state(store, data)


def admitted_groups(store: ScheduleStore, moment: datetime, *,
                    clock: Callable[[], datetime] | None = None) -> Iterator[list[ScheduleEntry]]:
    policy_path = store.root / "executor-policy.json"
    policy = None
    if policy_path.exists():
        try:
            policy = ExecutionPolicy.model_validate_json(policy_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ScheduleError("invalid executor policy") from exc
    groups: dict[tuple[str, datetime], list[ScheduleEntry]] = {}
    for entry in sorted(store.due(moment), key=lambda e: e.scheduled_at.astimezone(timezone.utc)):
        groups.setdefault((entry.slug, entry.scheduled_at.astimezone(timezone.utc)), []).append(entry)
    for group in groups.values():
        if policy:
            slot = policy.active_slot(clock() if clock else moment)
            if slot is None:
                return
            state = load_state(store)
            used = state["slots"].get(slot, 0)
            if used >= policy.max_groups_per_slot:
                return
            state["slots"][slot] = used + 1
            save_state(store, state)  # fail closed, even if persistence outcome is uncertain
        yield group


def health(store: ScheduleStore, moment: datetime) -> dict:
    state = load_state(store).get("heartbeat", {})
    try:
        stamp = datetime.fromisoformat(state["timestamp"])
        if stamp.utcoffset() is None:
            raise ValueError()
        age = (moment - stamp).total_seconds()
        status = state["state"]
        if status not in {"started", "finished", "error"}:
            raise ValueError()
        return {"state": status, "timestamp": stamp.isoformat(),
                "backend": "sqlite" if store.database_path.exists() else "json",
                "stale": age > 180 or age < -60}
    except (KeyError, ValueError, TypeError):
        return {"state": "unknown", "stale": True,
                "backend": "sqlite" if store.database_path.exists() else "json"}
