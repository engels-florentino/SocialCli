"""Durable, exact approval and read-only recovery for owned YouTube resources."""
from __future__ import annotations

import copy
import hmac
import json
import re
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from socialctl.management.changes import ApprovalMismatch, now_utc
from socialctl.management.resource_changes import ResourceStore, digest, _event
from socialctl.management.owned_schema import build_after, edit_raw, editable, owned_resource_id, mapping, merge, validate_edit, target
from socialctl.management.owned_assets import inspect_owned_asset, banner_url
from socialctl.management.youtube_resources import ResourceError, ResourceConflict, ResourceRejected, ResourceUncertain, resource_id


class OwnedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    kind: Literal["youtube-owned-resource"] = "youtube-owned-resource"
    id: str
    target_brand: str
    target_account: str
    edit: dict
    resource: Literal["playlists", "playlistItems", "channelSections", "channels", "playlistImages", "videos"]
    target_id: str | None = None
    before: dict
    after: dict
    asset: dict | None = None
    effects: list[str]
    plan: list[dict] = Field(default_factory=list)
    fingerprint: str = ""
    created_at: datetime
    updated_at: datetime
    status: Literal["proposed", "applying", "uncertain", "failed", "conflict", "partial", "verified", "accepted_unverifiable"] = "proposed"
    verified: bool = False
    result_id: str | None = None
    receipt: dict | None = None
    steps: list[dict] = Field(default_factory=list)
    journal: list[dict] = Field(default_factory=list)


def fingerprint(change):
    raw = change.model_dump(mode="json")
    return digest(json.dumps({k: v for k, v in raw.items() if k not in {
        "fingerprint", "updated_at", "status", "verified", "result_id", "receipt", "steps", "journal"}},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


class OwnedStore(ResourceStore):
    model_type = OwnedChange
    fingerprint_for = staticmethod(fingerprint)

    def __init__(self, brand_root):
        self.root = Path(brand_root) / ".socialctl" / "youtube-owned"


def complete(report):
    if not report["complete"]:
        raise ResourceError("lista incompleta; no se puede preparar/aplicar: " + str(report.get("error")))
    return report["items"]


def ordered(rows):
    rows = sorted(copy.deepcopy(rows), key=lambda r: r["snippet"]["position"])
    if [r["snippet"]["position"] for r in rows] != list(range(len(rows))):
        raise ResourceError("lista incompleta: posiciones no contiguas o duplicadas")
    return rows


def baseline(client, edit):
    resource, rid = target(edit)
    if resource == "channels":
        row = client.inspect_channel(rid)
    elif resource == "videos":
        row = client.inspect_video(rid)
    elif rid:
        row = client.one(resource, rid, parent=edit.playlist_id)
    elif resource in {"playlistItems", "playlistImages"}:
        row = client.one("playlists", edit.playlist_id)
    else:
        row = client.inspect_channel(client.configured_channel_id)
    before = {"resource": row}
    if edit.action.startswith("watermark-"):
        before["watermark_state"] = {"known": False, "reason": "no_public_read_method"}
    if resource == "playlistItems":
        before["parent"] = client.one("playlists", edit.playlist_id)
    if edit.action in {"item-insert", "items-reorder"} or (edit.action == "item-update" and "snippet" in edit.patch):
        before["collection"] = ordered(complete(client.list_items(edit.playlist_id)))
        if edit.action == "item-insert":
            if any(r["snippet"]["resourceId"]["videoId"] == edit.video_id for r in before["collection"]):
                raise ResourceConflict("ya existe membresía con este videoId; no se deduplica por título")
            if edit.position > len(before["collection"]):
                raise ResourceError("posición de inserción fuera de la lista")
    if resource == "playlistImages":
        before["parent"] = client.one("playlists", edit.playlist_id)
        before["collection"] = complete(client.list_images(edit.playlist_id))
        if edit.action == "image-insert" and any(r["snippet"]["type"] == edit.image_type for r in before["collection"]):
            raise ResourceConflict("ya existe imagen con ese tipo; requiere image-update explícito")
    if edit.action == "playlist-create":
        before["collection"] = complete(client.list_playlists())
    if edit.action == "playlist-update" and isinstance(edit.patch.get("status"), dict) and edit.patch["status"].get("podcastStatus") == "enabled":
        before["image_eligibility"] = complete(client.list_images(edit.playlist_id))
        if not before["image_eligibility"]:
            raise ResourceError("podcastStatus=enabled requiere una imagen existente; no se crea automáticamente")
    if edit.action == "section-create":
        before["collection"] = complete(client.list_sections())
        if len(before["collection"]) >= 10:
            raise ResourceError("máximo documentado de 10 secciones")
    return before


def effects_for(edit):
    notes = ["Se aprueba solo la marca/cuenta, IDs y valores completos mostrados; permisos/eligibilidad efectivos dependen del proveedor."]
    if edit.action.endswith("-delete"):
        notes.append("Eliminación irreversible del ID exacto; el snapshot es evidencia, no backup restaurable ni restauración automática.")
    if edit.action in {"playlist-delete", "item-delete"}:
        notes.append("La eliminación de playlist/membresía no elimina sus vídeos subyacentes.")
    if edit.action in {"item-insert", "items-reorder", "item-update"}:
        notes.append("playlistItemId, playlistId y videoId son IDs distintos; los cambios de posición desplazan otras membresías. ManualSortRequired requiere ajuste explícito externo; nunca se altera automáticamente.")
    if edit.action == "item-update":
        notes.append("startAt/endAt obsoletos solo se reenvían si ya existen al preservar contentDetails; YouTube los ignora. Su edición está deshabilitada y no se promete restauración ni retención.")
    if edit.action == "items-reorder":
        notes.append("Ordenación de múltiples elementos NO atómica: resultados parciales durables por elemento; reconcile solo relee y nunca continúa escrituras.")
    if edit.action == "channel-audience":
        notes.append("Declaración explícita de audiencia selfDeclaredMadeForKids; no inferida. Una discrepancia de documentación o rechazo de status se conserva, sin afirmar éxito.")
    if edit.action.startswith("image-"):
        notes.append("playlistImage no documenta ETag: comprobación previa de valores/propietario, con carrera remota lectura-escritura; sin garantía condicional. Nunca se usa el ETag del playlist como ETag de imagen.")
    if edit.file:
        notes.append("Bytes suministrados exactos, sin generación/conversión. Validación local de contenedor/dimensiones; proveedor valida procesamiento. El readback de metadatos no prueba igualdad visual o de bytes.")
    if edit.action == "image-update" and edit.file:
        notes.append("Media update: JPEG/PNG cuadrado suministrado hasta 52428800 bytes (50MiB), límite de Discovery. Image insert conserva el límite documentado de 2MiB.")
    if edit.action == "banner-upload":
        notes.append("Solo sube el banner y guarda duramente su URL; no aplica branding. banner-apply prepara otra aprobación exacta con esa URL.")
    if edit.action == "banner-apply":
        notes.append("Aplica la URL del recibo durable de subida con un channels.update separado; no cambia el título del canal.")
    if edit.action.startswith("watermark-"):
        notes.append("Estado anterior del watermark DESCONOCIDO: no hay método público de lectura y channels.list no admite invideoBranding. Freshness/propiedad del canal no prueba estado del watermark ni ofrece condición If-Match. Un 204 solo da accepted_unverifiable, nunca prueba bytes, metadatos ni ausencia.")
    if edit.action == "watermark-set":
        notes.append("Reemplazo completo explícito del watermark con archivo/timing/targetChannelId aprobados; no se conservan valores anteriores desconocidos. durationMs='omit' ordena omitir ese campo intencionalmente, no preservarlo.")
    return notes


def watermark_after(edit, before):
    timing = dict(mapping(edit.timing, {"type", "offsetMs", "durationMs"}, "timing"))
    if (set(timing) != {"type", "offsetMs", "durationMs"} or not isinstance(timing.get("type"), str)
        or timing["type"] not in {"offsetFromStart", "offsetFromEnd"}):
        raise ResourceError("reemplazo exige timing type/offsetMs/durationMs explícitos; durationMs='omit' indica omisión intencional")
    if timing["durationMs"] == "omit":
        del timing["durationMs"]
    for key in {"offsetMs", "durationMs"} & set(timing):
        if not isinstance(timing[key], str) or not re.fullmatch(r"\d{1,20}", timing[key]) or int(timing[key]) > 18446744073709551615:
            raise ResourceError("timing requiere uint64 decimal como texto")
    target_channel = resource_id(edit.target_channel_id)
    return {"timing": timing, "targetChannelId": target_channel}


def reorder_plan(edit, before):
    rows = ordered(before["collection"])
    positions = [p.model_dump() for p in edit.positions]
    if ({p["item_id"] for p in positions} != {r["id"] for r in rows}
        or len(positions) != len(rows) or sorted(p["position"] for p in positions) != list(range(len(rows)))):
        raise ResourceError("reorder exige cada playlistItemId exacto una vez y todas las posiciones contiguas")
    plan = []
    for wanted in sorted(positions, key=lambda p: p["position"]):
        row = next(r for r in rows if r["id"] == wanted["item_id"])
        if row["snippet"]["position"] == wanted["position"]:
            continue
        body = {"snippet": {**editable("playlistItems", "snippet", row), "position": wanted["position"]}}
        plan.append({"item_id": row["id"], "body": body})
        rows.remove(row)
        rows.insert(wanted["position"], row)
        for pos, other in enumerate(rows):
            other["snippet"]["position"] = pos
    return {"positions": positions}, plan


def expected_body(edit, resource, rid, before, account, asset):
    """Reconstruct outgoing parts from the strict edit, never trust stored after."""
    after, plan = {}, []
    if edit.action == "items-reorder":
        after, plan = reorder_plan(edit, before)
    elif edit.action == "watermark-set":
        after = watermark_after(edit, before)
    elif edit.action == "banner-apply":
        url = banner_url(before["upload_receipt"]["url"])
        after = {"brandingSettings": merge(editable("channels", "brandingSettings", before["resource"]), {"image": {"bannerExternalUrl": url}})}
    elif not edit.action.endswith("-delete") and edit.action not in {"watermark-unset", "banner-upload"}:
        after = build_after(edit, resource, before["resource"] if rid else {}, account)
    if resource == "playlistImages" and asset:
        explicit_dimensions = (edit.patch or {}).get("snippet", {})
        for field in ("width", "height"):
            if field in explicit_dimensions and explicit_dimensions[field] != asset[field]:
                raise ResourceError("dimensiones explícitas no coinciden con los bytes suministrados")
            after["snippet"][field] = asset[field]
    if edit.action == "item-update" and "collection" in before and after["snippet"]["position"] >= len(before["collection"]):
        raise ResourceError("posición de update fuera de la lista")
    return after, plan


def prepare_owned(client, store, edit):
    edit = validate_edit(edit)
    resource, rid = target(edit)
    account = client.identity()
    before = baseline(client, edit)
    if edit.action == "banner-apply":
        uploaded = store.load(edit.upload_change_id)
        if (uploaded.edit["action"] != "banner-upload" or uploaded.target_brand != client.brand.nombre
            or uploaded.target_account != account or uploaded.status != "accepted_unverifiable" or not uploaded.receipt):
            raise ResourceError("banner-apply requiere recibo durable de upload de la misma marca/cuenta")
        url = banner_url(uploaded.receipt.get("url"))
        before["upload_receipt"] = {"change_id": uploaded.id, "fingerprint": uploaded.fingerprint, "url": url}
    asset = None
    if edit.file:
        asset, _ = inspect_owned_asset(edit.file, edit.action)
        edit.file = asset["path"]
    after, plan = expected_body(edit, resource, rid, before, account, asset)
    moment = now_utc()
    change = OwnedChange(id=str(uuid.uuid4()), target_brand=client.brand.nombre, target_account=account,
        edit=edit_raw(edit), resource=resource, target_id=rid, before=before, after=after, asset=asset,
        effects=effects_for(edit), plan=plan, created_at=moment, updated_at=moment)
    change.fingerprint = fingerprint(change)
    _event(change, "prepared")
    store.save(change)
    return change


def identity(client, change):
    if client.brand.nombre != change.target_brand or client.configured_channel_id != change.target_account:
        raise ResourceError("marca/cuenta no coincide con la propuesta")
    if client.identity() != change.target_account:
        raise ResourceError("canal autenticado distinto")


def operation_key(change):
    edit = validate_edit(change.edit)
    # Serialize all ordering/membership operations per playlist. Unknown create
    # outcomes block the entire create family, even if the user changes a title.
    if change.resource in {"playlistItems", "playlistImages"}:
        target_key = [change.resource, edit.playlist_id]
    elif edit.action in {"playlist-create", "section-create"}:
        target_key = [change.resource, "create"]
    else:
        target_key = [change.resource, change.target_id]
    return json.dumps([change.target_account, *target_key])


def receipt_for(change, response):
    edit = validate_edit(change.edit)
    if edit.action == "banner-upload":
        # Store the returned URL before validating it or preparing application.
        return {"url": response.get("url"), "accepted": True}
    if edit.action.endswith("-delete") or edit.action.startswith("watermark-"):
        return response
    try:
        rid = owned_resource_id(change.resource, response.get("id"))
    except ResourceError:
        rid = None
    change.result_id = rid
    valid = bool(rid) and (change.target_id is None or rid == change.target_id)
    snip = response.get("snippet", {})
    if change.target_id is None:
        valid = valid and rid not in {row["id"] for row in change.before.get("collection", [])}
        if not isinstance(snip, dict):
            valid = False
        elif change.resource in {"playlistItems", "playlistImages"}:
            valid = valid and all(snip.get(k) == v for k, v in change.after["snippet"].items())
        else:
            valid = valid and snip.get("channelId") == change.target_account
    if "kind" in response:
        from socialctl.management.youtube_owned import KINDS
        valid = valid and response["kind"] == KINDS[change.resource]
    return {"id": rid, "identity_valid": valid}


def readback(client, store, change):
    edit = validate_edit(change.edit)
    try:
        identity(client, change)
        if edit.action == "banner-upload":
            if change.receipt and banner_url(change.receipt.get("url")):
                change.status = "accepted_unverifiable"
            _event(change, "upload_only_no_branding_readback")
        elif edit.action == "items-reorder":
            rows = ordered(complete(client.list_items(edit.playlist_id)))
            actual = {r["id"]: r["snippet"]["position"] for r in rows}
            expected = {p["item_id"]: p["position"] for p in change.after["positions"]}
            _event(change, "order_readback", positions=actual)
            if actual == expected and len(change.steps) == len(change.plan) and all(s["status"] == "verified" for s in change.steps):
                change.status, change.verified = "verified", True
        elif edit.action.startswith("watermark-"):
            _event(change, "watermark_readback_unavailable", state_known=False,
                reason="no_public_read_method", bytes_verified=False, absence_verified=False)
            if change.receipt and change.receipt.get("http_status") == 204:
                change.status, change.verified = "accepted_unverifiable", False
        elif edit.action.endswith("-delete"):
            row = client.one(change.resource, change.target_id, parent=edit.playlist_id, optional=True)
            _event(change, "delete_readback", absent_observed=row is None, absence_alone_is_not_proof=True)
            if row is None and change.receipt and change.receipt.get("http_status") == 204:
                change.status, change.verified = "verified", True
        elif change.receipt and change.receipt.get("identity_valid"):
            row = client.one(change.resource, change.result_id, parent=edit.playlist_id)
            actual = {p: editable(change.resource, p, row) for p in change.after}
            match = actual == change.after
            _event(change, "metadata_readback", matches=match, observed=actual, bytes_verified=False)
            if match:
                if change.asset:
                    change.status = "accepted_unverifiable"
                else:
                    change.status, change.verified = "verified", True
        else:
            _event(change, "identity_unresolved", limitation="No se infiere identidad de títulos ni de candidatos; no se repite el create.")
    except ResourceError as exc:
        _event(change, "readback_unavailable", error=str(exc))
    store.save(change)
    return change


def reconcile_owned(client, store, change_id):
    with store.apply_lock(change_id):
        change = store.load(change_id)
        identity(client, change)
        if change.status in {"applying", "uncertain", "partial", "accepted_unverifiable"}:
            if change.status == "applying":
                change.status = "uncertain"
            return readback(client, store, change)
        return change


def apply_owned(client, store, change_id, approval_digest):
    with ExitStack() as locks:
        locks.enter_context(store.apply_lock(change_id))
        change = store.load(change_id)
        edit = validate_edit(change.edit)
        if target(edit) != (change.resource, change.target_id) or bool(edit.file) != bool(change.asset):
            raise ResourceError("propuesta no coincide con el esquema de recurso/archivo")
        if not hmac.compare_digest(approval_digest, change.fingerprint):
            raise ApprovalMismatch("la aprobación no coincide con la huella exacta")
        key = operation_key(change)
        locks.enter_context(store.apply_lock(str(uuid.uuid5(uuid.NAMESPACE_URL, key))))
        identity(client, change)
        if change.status != "proposed":
            if change.status in {"applying", "uncertain", "partial", "accepted_unverifiable"}:
                if change.status == "applying":
                    change.status = "uncertain"
                return readback(client, store, change)
            return change
        for path in store.root.glob("*.json"):
            other = store.load(path.stem)
            unresolved_steps = any(s.get("status") in {"applying", "uncertain"} for s in other.steps)
            if other.id != change.id and operation_key(other) == key and (other.status in {"applying", "uncertain"} or unresolved_steps):
                raise ResourceError(f"resultado incierto pendiente en {other.id}; otra UUID no permite repetir la escritura")
        try:
            current = baseline(client, edit)
            expected = {k: v for k, v in change.before.items() if k != "upload_receipt"}
            if current != expected:
                raise ResourceConflict("conflict: el recurso/listado cambió desde el preview")
            if edit.action == "banner-apply":
                uploaded = store.load(edit.upload_change_id)
                binding = change.before["upload_receipt"]
                if (uploaded.fingerprint != binding["fingerprint"] or not uploaded.receipt or uploaded.receipt.get("url") != binding["url"]
                    or uploaded.status != "accepted_unverifiable" or uploaded.target_account != change.target_account or uploaded.target_brand != change.target_brand):
                    raise ResourceConflict("conflict: cambió el recibo del banner")
        except ResourceConflict:
            change.status = "conflict"
            _event(change, "pre_write_conflict")
            store.save(change)
            raise
        data = None
        if change.asset:
            asset, data = inspect_owned_asset(edit.file, edit.action)
            if asset != change.asset:
                raise ResourceError("archivo/bytes cambiaron desde la huella aprobada")
        after, plan = expected_body(edit, change.resource, change.target_id, change.before, change.target_account, change.asset)
        if after != change.after or plan != change.plan or effects_for(edit) != change.effects:
            raise ResourceError("cuerpo/plan/efectos no coincide con el esquema de la propuesta")
        if edit.action == "items-reorder":
            from socialctl.management.owned_reorder import apply_order
            return apply_order(client, store, change, edit, approval_digest)
        change.status = "applying"
        _event(change, "write_intent", approval_digest=approval_digest)
        store.save(change)
        try:
            response = client.effect(edit, change.resource, change.target_id, change.after,
                etag=None if change.target_id is None else change.before["resource"].get("etag"), asset=change.asset, data=data)
        except ResourceRejected as exc:
            change.status = "conflict" if isinstance(exc, ResourceConflict) else "failed"
            _event(change, "write_rejected", error=str(exc))
        except ResourceUncertain as exc:
            change.status = "uncertain"
            _event(change, "write_uncertain", error=str(exc))
        else:
            change.status = "uncertain"
            change.receipt = receipt_for(change, response)
            _event(change, "write_response", result_id=change.result_id, receipt=change.receipt)
        store.save(change)
        return readback(client, store, change) if change.status == "uncertain" else change
