"""Bounded Meta comments commands: owned media, complete previews, exact approval."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer

from socialctl.read_budget import active_read_budget, bounded_read, read_client

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

comments_app = typer.Typer(help="Durable Facebook/Instagram comments on owned media; Instagram does not allow text editing.")


def make_http_client():
    budget = active_read_budget.get()
    if budget is not None:
        return read_client(budget, progress=lambda: typer.echo('Reading provider data...', err=True))
    return httpx.Client(timeout=30.0, follow_redirects=False)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_comment_preview(change):
    return ("PREVIEW COMPLETO — comentario independiente\n" + _json(change.model_dump(mode="json")) +
            "\nMedia is not modified. Creation does not prove the link is clickable or the comment is pinned."
            "\nDelete explicitly removes owned comment; an empty read does not prove absence."
            f"\nExact fingerprint for approval: {change.fingerprint}")


def _inbox_record(selected, platform: Platform, media_id: str, comment_id: str):
    if platform not in {Platform.FACEBOOK, Platform.INSTAGRAM}:
        raise ChangeError("inbox supports only Facebook and Instagram")
    account_key = "page_id" if platform is Platform.FACEBOOK else "ig_user_id"
    account_id = (selected.cuentas.get(platform.value) or {}).get(account_key)
    rows = [row for row in CommentInboxStore(selected.raiz).load().comments
            if (row.platform, row.account_id, row.media_id, row.comment_id) ==
            (platform.value, account_id, media_id, comment_id)]
    if len(rows) != 1:
        raise ChangeError("comment was not unambiguously observed in inbox")
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
            raise ChangeError("durable ChangeSet link to inbox mismatch")
        if rows[0].change_id != change.id:
            raise ChangeError(
                "this inbox draft was replaced by another ChangeSet; cannot be applied")
        if rows[0].idempotency_key != binding.get("idempotency_key"):
            raise ChangeError("durable ChangeSet link to inbox mismatch")
        return rows[0]
    # Compatibility for inbox drafts created before the binding was embedded in
    # the ChangeSet: both the current pointer and immutable preparation events
    # keep superseded drafts out of the legacy apply/reconcile path.
    rows = [row for row in data.comments if row.change_id == change.id or
            change.id in row.draft_change_ids or any(
                event.get("event") == "explicit_draft_prepared" and
                event.get("change_id") == change.id for event in row.journal)]
    if len(rows) > 1:
        raise ChangeError("ChangeSet duplicated in inbox")
    return rows[0] if rows else None


def render_draft_preview(record, change):
    return ("PREVIEW COMPLETO — respuesta a comentario observado\n"
            + _json({"source_comment": record.model_dump(mode="json"),
                     "changeset": change.model_dump(mode="json")})
            + "\nNo reply without exact approval. An uncertain outcome blocks another POST."
            + f"\nExact fingerprint for approval: {change.fingerprint}")


@comments_app.command("list")
def list_comments(media_id: str, platform: Platform = typer.Option(..., "--platform"),
                  brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                  parent_id: str | None = typer.Option(None, "--parent-id"),
                  include_moderation: bool = typer.Option(False, "--include-moderation", help="Include hidden status when exposed by provider."), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
    """Read paginated comments with account and ownership checks; does not prove absence."""
    selected = _brand(root, brand)
    try:
        with bounded_read(timeout), make_http_client() as http:
            result = MetaCommentsClient(selected, platform, http).list_comments(
                media_id, parent_id=parent_id, include_moderation=include_moderation)
    except (ChangeError, OSError, ValueError, httpx.HTTPError) as exc:
        from socialctl.read_reports import fail_read
        fail_read(exc, json_output=json_output)
    typer.echo(_json(result))
    if json_output and result.get("complete") is False:
        raise typer.Exit(1)


@comments_app.command("show")
def show_comment(media_id: str, comment_id: str, platform: Platform = typer.Option(..., "--platform"),
                 brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                 parent_id: str | None = typer.Option(None, "--parent-id"), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
    """Verify comment ID and membership in owned media or parent."""
    selected = _brand(root, brand)
    try:
        with bounded_read(timeout), make_http_client() as http:
            result = MetaCommentsClient(selected, platform, http).find_comment(media_id, comment_id, parent_id=parent_id)
    except (ChangeError, OSError, ValueError, httpx.HTTPError) as exc:
        from socialctl.read_reports import fail_read
        fail_read(exc, json_output=json_output)
    typer.echo(_json(result))
    if json_output and result.get("complete") is False:
        raise typer.Exit(1)


@comments_app.command("sync")
def sync(media_id: str, platform: Platform = typer.Option(..., "--platform"),
         brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
         since: str | None = typer.Option(None, "--since", help="Initial RFC3339 timestamp; subsequent reads reuse durable time cursor."),
         max_pages: int = typer.Option(100, "--max-pages", min=1, max=100), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
    """Read new comments, deduplicate IDs and update inbox without replying."""
    selected = _brand(root, brand)
    try:
        with bounded_read(timeout), make_http_client() as http:
            result = sync_inbox(MetaCommentsClient(selected, platform, http),
                CommentInboxStore(selected.raiz), media_id=media_id, since=since,
                max_pages=max_pages)
    except (ChangeError, OSError, ValueError, httpx.HTTPError) as exc:
        from socialctl.read_reports import fail_read
        fail_read(exc, json_output=json_output)
    typer.echo(_json(result))
    if json_output and result.get("complete") is False:
        raise typer.Exit(1)


@comments_app.command("inbox")
def inbox(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
          platform: Platform | None = typer.Option(None, "--platform"),
          media_id: str | None = typer.Option(None, "--media-id"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
    """Display inbox grouped by item, author, language and type without accessing Meta."""
    selected = _brand(root, brand)
    if platform not in {None, Platform.FACEBOOK, Platform.INSTAGRAM}:
        _fail("inbox supports only Facebook and Instagram")
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
          text: str = typer.Option(..., "--text", help="Supplied literal reply; not AI-generated."),
          brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
          dry_run: bool = typer.Option(False, "--dry-run", help="Compatibility: preparation is always local only.")):
    """Prepare explicit draft and ChangeSet without POSTs."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            change, record = prepare_inbox_draft(MetaCommentsClient(selected, platform, http),
                CommentStore(selected.raiz), CommentInboxStore(selected.raiz),
                media_id=media_id, comment_id=comment_id, text=text)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo("Remote DRY-RUN: local draft and ChangeSet; no Meta writes.")
    typer.echo(render_draft_preview(record, change))


@comments_app.command("apply-draft")
def apply_draft(media_id: str, comment_id: str,
                platform: Platform = typer.Option(..., "--platform"),
                brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT, "--root"),
                max_replies: int = typer.Option(10, "--max-replies", min=1,
                    help="Maximum reply intentions within window and account."),
                window_seconds: int = typer.Option(3600, "--window-seconds", min=1)):
    """Display preview and apply only after entering exact fingerprint."""
    selected = _brand(root, brand)
    try:
        record = _inbox_record(selected, platform, media_id, comment_id)
        if not record.change_id:
            raise ChangeError("comment has no draft")
        change = CommentStore(selected.raiz).load(record.change_id)
        typer.echo(render_draft_preview(record, change))
        digest = typer.prompt("Enter the exact fingerprint to approve")
        if digest != change.fingerprint:
            _fail("approval does not match exact fingerprint")
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
    """GET only: reconcile uncertain outcome and never repeat POST."""
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
            action: str = typer.Option("add", "--action", help="add, reply, edit (Facebook only) or delete (own author)."),
            text: str | None = typer.Option(None, "--text", help="Exact user-supplied text; no copy generated."),
            parent_id: str | None = typer.Option(None, "--parent-id"),
            comment_id: str | None = typer.Option(None, "--comment-id"),
            dry_run: bool = typer.Option(False, "--dry-run", help="Only prepare and display ChangeSet; no Meta writes.")):
    """Prepare add/reply/edit/delete without remote writes and display full proposal."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            change = prepare_comment(MetaCommentsClient(selected, platform, http), CommentStore(selected.raiz),
                media_id=media_id, text=text, action=action, parent_id=parent_id, comment_id=comment_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo("Remote DRY-RUN: local proposal; no Meta writes.")
    typer.echo(render_comment_preview(change))


@comments_app.command("status")
def status(change_id: str, brand: str = typer.Option(..., "--brand"),
           root: Path = typer.Option(DEFAULT_ROOT, "--root"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
    """Display complete ChangeSet and journal without accessing Meta."""
    selected = _brand(root, brand)
    try:
        change = CommentStore(selected.raiz).load(change_id)
    except ChangeError as exc:
        _fail(str(exc))
    typer.echo(_json(change.model_dump(mode="json")))


@comments_app.command("apply")
def apply(change_id: str, brand: str = typer.Option(..., "--brand"),
          root: Path = typer.Option(DEFAULT_ROOT, "--root")):
    """Display full preview and require exact fingerprint entry; uncertain POST is never repeated."""
    selected = _brand(root, brand)
    store = CommentStore(selected.raiz)
    try:
        change = store.load(change_id)
        if _inbox_record_for_change(selected, change):
            _fail("this ChangeSet belongs to inbox; use comments apply-draft to preserve state and frequency")
        typer.echo(render_comment_preview(change))
        digest = typer.prompt("Enter the exact fingerprint to approve")
        if digest != change.fingerprint:
            _fail("approval does not match exact fingerprint")
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
    """Remote GET only: identical candidates do not prove creation; never POST."""
    selected = _brand(root, brand)
    store = CommentStore(selected.raiz)
    try:
        change = store.load(change_id)
        if _inbox_record_for_change(selected, change):
            _fail("this ChangeSet belongs to inbox; use comments reconcile-draft")
        with make_http_client() as http:
            result = reconcile_comment(MetaCommentsClient(selected, Platform(change.platform), http), store, change_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))


@comments_app.command("publication-status")
def publication_status(publication_id: str, brand: str = typer.Option(..., "--brand"),
                       root: Path = typer.Option(DEFAULT_ROOT, "--root"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
    """Read durable attempt containing confirmed media, approved text and comment ChangeSet."""
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
    """Resume only originally approved comment; never upload media or repeat uncertain POSTs."""
    selected = _brand(root, brand)
    try:
        data = PublicationStore(selected.raiz).load(publication_id)
        typer.echo("First comment: approval inherited from complete original attempt:\n" + _json(data))
        if dry_run:
            return
        with make_http_client() as http:
            result = retry_first_comment(selected, http, publication_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))
    if result.first_comment_status not in {None, "verified"}:
        raise typer.Exit(1)
