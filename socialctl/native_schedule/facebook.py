"""Native Facebook Reels behind an explicit account pilot gate.

Transport readback never claims Business Suite calendar visibility. Other formats
remain closed. Missing pilot evidence never falls back to immediate publication.
"""
from dataclasses import dataclass
from typing import Literal

import httpx

from socialctl.brands import Brand
from socialctl.auth import obtener_token
from socialctl.models import Platform

from .approval import verify_approval
from .models import NativeJob
from .store import NativeStore
from .facebook_reels import FacebookReelDelivery, GRAPH, _safe_identifier
from .handoff import _target


@dataclass(frozen=True)
class FacebookEligibility:
    account_id: str = ''
    api_enabled: bool = False
    page_pilot_verified: bool = False
    evidence_ref: str = ''


@dataclass(frozen=True)
class FacebookScheduleResult:
    state: Literal["ui_required", "pending_verification", "uncertain"] = "ui_required"
    remote_id: str | None = None
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
    eligibility: FacebookEligibility | None = None,
) -> FacebookScheduleResult:
    """Upload directly to Meta only after immutable approval and pilot evidence."""
    _context(job, payload, brand, store)
    eligibility = eligibility or FacebookEligibility()
    if (job.route != 'api' or eligibility.account_id != job.account_id
            or eligibility.api_enabled is not True or eligibility.page_pilot_verified is not True
            or not eligibility.evidence_ref or payload.get('options', {}).get('format') != 'reel'):
        return FacebookScheduleResult()
    if store.get_attempts(job.id):
        target = _target(job, payload, store)
        remote_id = _safe_identifier(target) if target is not None else None
        return FacebookScheduleResult('uncertain', remote_id, 'Existing attempt requires reconciliation; never upload again')
    if payload.get('delivery_mode', 'native_schedule') != 'native_schedule':
        raise ValueError('Native Facebook submission requires native_schedule delivery mode')
    options = payload['options']
    if set(options) - {'format', 'share_to_facebook_story'} or options.get('share_to_facebook_story', False) is not False:
        raise ValueError('Unsupported Reel options; prepare an exact supported preview')
    if payload.get('visibility') != 'public':
        raise ValueError('Scheduled Page Reel must explicitly request eventual public visibility')
    token = obtener_token(brand, Platform.FACEBOOK, client)
    response = client.get(f'{GRAPH}/me', params={'fields': 'id', 'access_token': token})
    response.raise_for_status()
    if response.json().get('id') != job.account_id:
        raise ValueError('Authenticated Page differs from approved account')
    adapter = FacebookReelDelivery(client, token)
    adapter._schedule_time(job)
    claimed = store.claim(job.id, job.state)
    if claimed is None:
        raise ValueError('Job could not be claimed')
    remote_id = None
    try:
        verify_approval(claimed, payload)
        remote_id = adapter.deliver(claimed, payload,
            lambda value: store.checkpoint(claimed.id, claimed.attempt_id, value))
        store.mark_api_submitted(claimed.id, claimed.attempt_id, remote_id)
        return FacebookScheduleResult('pending_verification', remote_id,
            'Meta readback matches the schedule; fresh Business Suite calendar evidence is still required')
    except Exception:
        current = store.get(job.id)
        if current.state in {'dispatching', 'verifying'}:
            store.transition(job.id, current.state, 'uncertain')
        target = _target(store.get(job.id), payload, store)
        remote_id = _safe_identifier(target) if target is not None else None
        return FacebookScheduleResult('uncertain', remote_id,
            'Reconcile the existing remote attempt before any further upload')
