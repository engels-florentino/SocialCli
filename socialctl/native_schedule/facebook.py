"""Closed native Facebook API gate.

No Facebook format currently has the complete, account-tested create/read/calendar
contract required for an API scheduling attempt.  Keep the approved API intent
untouched and require a newly prepared, reviewed Business Suite route.
"""
from dataclasses import dataclass
from typing import Literal

import httpx

from socialctl.brands import Brand

from .approval import verify_approval
from .models import NativeJob
from .store import NativeStore


@dataclass(frozen=True)
class FacebookScheduleResult:
    state: Literal["ui_required"] = "ui_required"
    remote_id: None = None
    reason: str = (
        "Facebook API create/read/account eligibility is unproven; "
        "reprepare and approve route=ui for Meta Business Suite"
    )
    source: Literal["api"] = "api"
    calendar_visible: Literal[False] = False


def _context(job: NativeJob, payload: dict, brand: Brand, store: NativeStore) -> None:
    page_id = (brand.cuentas.get("facebook") or {}).get("page_id")
    if (
        job.brand != brand.nombre
        or store.brand != job.brand
        or store.path.parent != brand.raiz.resolve()
        or job.platform != "facebook"
        or page_id != job.account_id
    ):
        raise ValueError("Explicit brand/account/store mismatch")
    if store.get(job.id) != job or store.get_payload(job.id) != payload:
        raise ValueError("Stale job or changed stored payload")
    verify_approval(job, payload)


def schedule_facebook(
    job: NativeJob,
    payload: dict,
    *,
    brand: Brand,
    store: NativeStore,
    client: httpx.Client,
) -> FacebookScheduleResult:
    """Return the closed API decision without claiming or touching the network.

    ``client`` is explicit for the common adapter interface. It is deliberately
    unused until a format-specific create and read contract, effective account
    grants, and a Business Suite pilot have all been confirmed.
    """
    _context(job, payload, brand, store)
    return FacebookScheduleResult()
