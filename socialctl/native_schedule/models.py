"""Frozen identities; ledger methods, never assignment, change lifecycle state."""
from datetime import datetime, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Nonempty = Annotated[str, Field(min_length=1, pattern=r'.*\S.*')]
Platform = Literal['youtube', 'facebook', 'instagram', 'tiktok']
State = Literal['prepared', 'approved', 'waiting_window', 'ready', 'dispatching',
    'awaiting_ui', 'verifying', 'native_scheduled', 'published', 'cancelled', 'blocked', 'uncertain']


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timestamp must have a timezone')
    back = value.astimezone(timezone.utc).astimezone(value.tzinfo)
    if back.replace(tzinfo=None) != value.replace(tzinfo=None) or back.fold != value.fold:
        raise ValueError('Nonexistent or invalid local timestamp')
    return value.astimezone(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)

    @field_validator('*')
    @classmethod
    def timestamps(cls, value):
        return aware(value) if isinstance(value, datetime) else value


class NativeJob(StrictModel):
    id: Nonempty
    legacy_entry_id: Nonempty | None = None
    brand: Nonempty
    platform: Platform
    account_id: Nonempty
    media_hash: Digest
    content_hash: Digest
    publish_at: datetime
    timezone_name: str
    dispatch_after: datetime
    approval_digest: Digest | None = None
    route: Literal['api', 'ui', 'blocked']
    state: State = 'prepared'
    remote_id: Nonempty | None = None
    attempt_id: Nonempty | None = None
    observation_watermark: datetime | None = None
    reconcile_after: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator('timezone_name')
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    @model_validator(mode='after')
    def ordered(self):
        if self.dispatch_after >= self.publish_at:
            raise ValueError('dispatch_after must precede publish_at')
        if self.updated_at < self.created_at:
            raise ValueError('updated_at precedes creation')
        return self


class NativeObservation(StrictModel):
    job_id: Nonempty
    platform: Platform
    account_id: Nonempty
    content_hash: Digest
    approval_digest: Digest
    remote_ref: Nonempty
    publish_at: datetime
    observed_at: datetime
    source: Literal['api', 'ui']
    calendar_visible: bool
    state: Literal['native_scheduled', 'published', 'draft', 'cancelled', 'missing', 'conflict']
    public_visible: bool = False
    processing_complete: bool = False
    actual_published_at: datetime | None = None
    evidence_path: Nonempty
    evidence_sha256: Digest

    @model_validator(mode='after')
    def api_not_calendar(self):
        if self.source == 'api' and self.calendar_visible:
            raise ValueError('API response alone cannot attest native calendar visibility')
        return self
