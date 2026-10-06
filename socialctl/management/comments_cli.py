"""Bounded Meta comments commands: owned media, complete previews, exact approval."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer

from socialctl.management.cli import DEFAULT_ROOT, _brand, _fail
from socialctl.management.changes import ChangeError
from socialctl.management.meta_comments import (
    CommentStore, MetaCommentsClient, apply_comment, prepare_comment, reconcile_comment,
)
from socialctl.management.meta_comment_inbox import (
    CommentInboxStore,
    apply_inbox_draft,
    grouped_inbox,
    prepare_inbox_draft,
    reconcile_inbox_draft,
    sync_inbox,
)
from socialctl.models import Platform
from socialctl.publication_steps import PublicationStore, retry_first_comment

comments_app = typer.Typer(help="Comentarios durables de Facebook/Instagram en medios propios; Instagram no permite editar texto.")


def make_http_client():
    return httpx.Client(timeout=30.0, follow_redirects=False)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_comment_preview(change):
    return ("PREVIEW COMPLETO — comentario independiente\n" + _json(change.model_dump(mode="json")) +
            "\nNo se modifica el medio. Crear no demuestra que el enlace sea clicable ni que el comentario esté fijado."
            "\nDelete elimina explícitamente el comentario propio; una lectura vacía no demuestra ausencia."
            f"\nHuella exacta para aprobar: {change.fingerprint}")


def _inbox_record(selected, platform: Platform, media_id: str, comment_id: str):
    if platform not in {Platform.FACEBOOK, Platform.INSTAGRAM}:
        raise ChangeError("la bandeja solo admite Facebook e Instagram")
    account_key = "page_id" if platform is Platform.FACEBOOK else "ig_user_id"
    account_id = (selected.cuentas.get(platform.value) or {}).get(account_key)
    rows = [row for row in CommentInboxStore(selected.raiz).load().comments
            if (row.platform, row.account_id, row.media_id, row.comment_id) ==
            (platform.value, account_id, media_id, comment_id)]
    if len(rows) != 1:
        raise ChangeError("comentario no observado de forma inequívoca en la bandeja")
    return rows[0]


def _inbox_record_for_change(selected, change):
    data = CommentInboxStore(selected.raiz).load()
    binding = change.inbox_binding
    if binding:
        rows = [row for row in data.comments
                if (row.platform, row.account_id, row.media_id, row.comment_id) ==
                (binding.get("platform"), binding.get("account_id"),
                 binding.get("media_id"), binding.get("comment_id"))]
        if len(rows) != 1:
            raise ChangeError("el vínculo durable del ChangeSet con la bandeja no coincide")
        if rows[0].change_id != change.id:
            raise ChangeError(
                "este borrador de bandeja fue reemplazado por otro ChangeSet; no se puede aplicar")
        if rows[0].idempotency_key != binding.get("idempotency_key"):
            raise ChangeError("el vínculo durable del ChangeSet con la bandeja no coincide")
        return rows[0]
    # Compatibility for inbox drafts created before the binding was embedded in
    # the ChangeSet: both the current pointer and immutable preparation events
    # keep superseded drafts out of the legacy apply/reconcile path.
    rows = [row for row in data.comments if row.change_id == change.id or
            change.id in row.draft_change_ids or any(
                event.get("event") == "explicit_draft_prepared" and
                event.get("change_id") == change.id for event in row.journal)]
    if len(rows) > 1:
        raise ChangeError("el ChangeSet aparece duplicado en la bandeja")
    return rows[0] if rows else None


def render_draft_preview(record, change):
    return ("PREVIEW COMPLETO — respuesta a comentario observado\n"
            + _json({"source_comment": record.model_dump(mode="json"),
                     "changeset": change.model_dump(mode="json")})
            + "\nNo se responderá sin aprobación exacta. Un resultado incierto bloquea otro POST."
            + f"\nHuella exacta para aprobar: {change.fingerprint}")


@comments_app.command("list")
def list_comments(media_id: str, platform: Platform = typer.Option(..., "--platform"),
                  brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                  parent_id: str | None = typer.Option(None, "--parent-id"),
                  include_moderation: bool = typer.Option(False, "--include-moderation", help="Incluye estado oculto cuando el proveedor lo expone.")):
    """Lee comentarios paginados verificando cuenta y propiedad; nunca prueba ausencia."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaCommentsClient(selected, platform, http).list_comments(
                media_id, parent_id=parent_id, include_moderation=include_moderation)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@comments_app.command("show")
def show_comment(media_id: str, comment_id: str, platform: Platform = typer.Option(..., "--platform"),
                 brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                 parent_id: str | None = typer.Option(None, "--parent-id")):
    """Verifica ID y pertenencia del comentario al medio propio (o a su padre)."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaCommentsClient(selected, platform, http).find_comment(media_id, comment_id, parent_id=parent_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@comments_app.command("sync")
def sync(media_id: str, platform: Platform = typer.Option(..., "--platform"),
         brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
         since: str | None = typer.Option(None, "--since", help="Fecha RFC3339 inicial; después se reutiliza el cursor temporal durable."),
         max_pages: int = typer.Option(100, "--max-pages", min=1, max=100)):
    """Lee comentarios nuevos, deduplica IDs y actualiza la bandeja; nunca responde."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = sync_inbox(MetaCommentsClient(selected, platform, http),
                CommentInboxStore(selected.raiz), media_id=media_id, since=since,
                max_pages=max_pages)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@comments_app.command("inbox")
def inbox(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
          platform: Platform | None = typer.Option(None, "--platform"),
          media_id: str | None = typer.Option(None, "--media-id")):
    """Muestra la bandeja agrupada por pieza, autor, idioma y tipo; no toca Meta."""
    selected = _brand(root, brand)
    if platform not in {None, Platform.FACEBOOK, Platform.INSTAGRAM}:
        _fail("la bandeja solo admite Facebook e Instagram")
    try:
        data = CommentInboxStore(selected.raiz).load()
    except ChangeError as exc:
        _fail(str(exc))
    typer.echo(_json({"version": data.version,
        "groups": grouped_inbox(data, platform=platform.value if platform else None, media_id=media_id),
        "cursors": [row.model_dump(mode="json") for row in data.cursors
                    if (not platform or row.platform == platform.value) and
                       (not media_id or row.media_id == media_id)]}))


@comments_app.command("draft")
def draft(media_id: str, comment_id: str, platform: Platform = typer.Option(..., "--platform"),
          text: str = typer.Option(..., "--text", help="Respuesta literal aportada; no se genera con IA."),
          brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
          dry_run: bool = typer.Option(False, "--dry-run", help="Compatibilidad: preparar siempre es solo local.")):
    """Prepara un borrador explícito y su ChangeSet; no hace ningún POST."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            change, record = prepare_inbox_draft(MetaCommentsClient(selected, platform, http),
                CommentStore(selected.raiz), CommentInboxStore(selected.raiz),
                media_id=media_id, comment_id=comment_id, text=text)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo("DRY-RUN remoto: borrador y ChangeSet locales; ninguna escritura en Meta.")
    typer.echo(render_draft_preview(record, change))


@comments_app.command("apply-draft")
def apply_draft(media_id: str, comment_id: str,
                platform: Platform = typer.Option(..., "--platform"),
                brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                max_replies: int = typer.Option(10, "--max-replies", min=1,
                    help="Máximo de intenciones de respuesta en la ventana y cuenta."),
                window_seconds: int = typer.Option(3600, "--window-seconds", min=1)):
    """Muestra el preview y aplica solo tras escribir la huella exacta."""
    selected = _brand(root, brand)
    try:
        record = _inbox_record(selected, platform, media_id, comment_id)
        if not record.change_id:
            raise ChangeError("el comentario no tiene borrador")
        change = CommentStore(selected.raiz).load(record.change_id)
        typer.echo(render_draft_preview(record, change))
        digest = typer.prompt("Escribe la huella exacta para aprobar")
        if digest != change.fingerprint:
            _fail("aprobación no coincide con la huella exacta")
        with make_http_client() as http:
            result = apply_inbox_draft(MetaCommentsClient(selected, platform, http),
                CommentStore(selected.raiz), CommentInboxStore(selected.raiz),
                media_id=media_id, comment_id=comment_id, approval_digest=digest,
                max_replies=max_replies, window_seconds=window_seconds)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))
    if result.status != "respondido":
        raise typer.Exit(1)


@comments_app.command("reconcile-draft")
def reconcile_draft(media_id: str, comment_id: str,
                    platform: Platform = typer.Option(..., "--platform"),
                    brand: str = typer.Option(..., "--brand"),
                    root: Path = typer.Option(DEFAULT_ROOT, "--root")):
    """Solo GET: reconcilia un resultado incierto y nunca repite el POST."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = reconcile_inbox_draft(MetaCommentsClient(selected, platform, http),
                CommentStore(selected.raiz), CommentInboxStore(selected.raiz),
                media_id=media_id, comment_id=comment_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))
    if result.status != "respondido":
        raise typer.Exit(1)


@comments_app.command("prepare")
def prepare(media_id: str, platform: Platform = typer.Option(..., "--platform"),
            brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
            action: str = typer.Option("add", "--action", help="add, reply, edit (solo Facebook) o delete (autor propio)."),
            text: str | None = typer.Option(None, "--text", help="Texto exacto aportado por el usuario; no se genera copy."),
            parent_id: str | None = typer.Option(None, "--parent-id"),
            comment_id: str | None = typer.Option(None, "--comment-id"),
            dry_run: bool = typer.Option(False, "--dry-run", help="Solo prepara y muestra el ChangeSet; no escribe en Meta.")):
    """Prepara add/reply/edit/delete sin escritura remota y muestra propuesta completa."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            change = prepare_comment(MetaCommentsClient(selected, platform, http), CommentStore(selected.raiz),
                media_id=media_id, text=text, action=action, parent_id=parent_id, comment_id=comment_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo("DRY-RUN remoto: propuesta local; ninguna escritura en Meta.")
    typer.echo(render_comment_preview(change))


@comments_app.command("status")
def status(change_id: str, brand: str = typer.Option(..., "--brand"),
           root: Path = typer.Option(DEFAULT_ROOT, "--root")):
    """Muestra el ChangeSet y diario completos sin tocar Meta."""
    selected = _brand(root, brand)
    try:
        change = CommentStore(selected.raiz).load(change_id)
    except ChangeError as exc:
        _fail(str(exc))
    typer.echo(_json(change.model_dump(mode="json")))


@comments_app.command("apply")
def apply(change_id: str, brand: str = typer.Option(..., "--brand"),
          root: Path = typer.Option(DEFAULT_ROOT, "--root")):
    """Muestra preview completo y exige escribir su huella exacta; un POST incierto no se repite."""
    selected = _brand(root, brand)
    store = CommentStore(selected.raiz)
    try:
        change = store.load(change_id)
        if _inbox_record_for_change(selected, change):
            _fail("este ChangeSet pertenece a la bandeja; usa comments apply-draft para conservar estado y frecuencia")
        typer.echo(render_comment_preview(change))
        digest = typer.prompt("Escribe la huella exacta para aprobar")
        if digest != change.fingerprint:
            _fail("aprobación no coincide con la huella exacta")
        with make_http_client() as http:
            result = apply_comment(MetaCommentsClient(selected, Platform(change.platform), http), store, change_id, digest)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))
    if result.status != "verified":
        raise typer.Exit(1)


@comments_app.command("reconcile")
def reconcile(change_id: str, brand: str = typer.Option(..., "--brand"),
              root: Path = typer.Option(DEFAULT_ROOT, "--root")):
    """Solo GET remoto: candidatos idénticos no son prueba de creación; nunca POST."""
    selected = _brand(root, brand)
    store = CommentStore(selected.raiz)
    try:
        change = store.load(change_id)
        if _inbox_record_for_change(selected, change):
            _fail("este ChangeSet pertenece a la bandeja; usa comments reconcile-draft")
        with make_http_client() as http:
            result = reconcile_comment(MetaCommentsClient(selected, Platform(change.platform), http), store, change_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))


@comments_app.command("publication-status")
def publication_status(publication_id: str, brand: str = typer.Option(..., "--brand"),
                       root: Path = typer.Option(DEFAULT_ROOT, "--root")):
    """Lee un intento durable: medio confirmado, texto aprobado y ChangeSet de comentario."""
    selected = _brand(root, brand)
    try:
        result = PublicationStore(selected.raiz).load(publication_id)
    except ChangeError as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@comments_app.command("retry-first")
def retry_first(publication_id: str, brand: str = typer.Option(..., "--brand"),
                root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                dry_run: bool = typer.Option(False, "--dry-run")):
    """Reanuda solo el comentario originalmente aprobado; nunca sube medios ni repite POST inciertos."""
    selected = _brand(root, brand)
    try:
        data = PublicationStore(selected.raiz).load(publication_id)
        typer.echo("Primer comentario: aprobación heredada del intento original completo:\n" + _json(data))
        if dry_run:
            return
        with make_http_client() as http:
            result = retry_first_comment(selected, http, publication_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))
    if result.first_comment_status not in {None, "verified"}:
        raise typer.Exit(1)
