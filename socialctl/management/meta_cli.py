"""Durable Meta management commands with exact approval and explicit reconciliation."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path

import httpx
import typer

from socialctl.management.cli import DEFAULT_ROOT, _brand, _fail
from socialctl.management.meta_changes import MetaStore, apply_meta, prepare_meta, reconcile_meta
from socialctl.management.meta_client import MetaClient
from socialctl.management.meta_schema import MetaError, load_edit
from socialctl.management.changes import ChangeError
from socialctl.models import Platform
from socialctl.management.meta_webhooks import WebhookStore, verify_challenge

meta_app = typer.Typer(help="Manage explicit Meta changes with durable state and exact fingerprint.")


def make_http_client():
    return httpx.Client(timeout=30.0, follow_redirects=False)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_preview(change):
    return (
        "PREVIEW COMPLETO (meta-management)\n" + _json(change.model_dump(mode="json")) +
        "\nExact fingerprint for approval: " + change.fingerprint
    )


@meta_app.command("prepare")
def prepare(
    edit_file: Path = typer.Option(..., "--file", help="YAML with Meta action and required fields."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Only prepare and display proposal; do not write to Meta."),
) -> None:
    """Prepare a change without remote mutation and save durable ChangeSet."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            change = prepare_meta(MetaClient(selected, platform, http), MetaStore(selected.raiz),
                                  load_edit(edit_file))
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo("Remote DRY-RUN: local proposal; no Meta writes." if dry_run else "Proposal prepared: edit has not been sent." )
    typer.echo(render_preview(change))


@meta_app.command("status")
def status(
    change_id: str = typer.Argument(...),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Display proposal and complete journal without writes."""
    selected = _brand(root, brand)
    try:
        change = MetaStore(selected.raiz).load(change_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(change.model_dump(mode="json")))


@meta_app.command("apply")
def apply(
    change_id: str = typer.Argument(...),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    yes: bool = typer.Option(False, "--yes", help="Does not replace exact approval."),
) -> None:
    """Require exact fingerprint; uncertain writes are never blindly reprocessed."""
    selected = _brand(root, brand)
    store = MetaStore(selected.raiz)
    try:
        change = store.load(change_id)
        typer.echo(render_preview(change))
        if yes:
            typer.echo("NOTICE: --yes does not replace exact approval of this ChangeSet.")
        digest = typer.prompt("Enter the exact fingerprint to approve")
        if not digest == change.fingerprint:
            _fail("approval does not match exact fingerprint")
        with make_http_client() as http:
            result = apply_meta(MetaClient(selected, Platform(change.platform), http), store, change_id, digest)
    except (ChangeError, OSError, MetaError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))


@meta_app.command("reconcile")
def reconcile(
    change_id: str = typer.Argument(...),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Read only for safe reconciliation after uncertain changes."""
    selected = _brand(root, brand)
    store = MetaStore(selected.raiz)
    try:
        change = store.load(change_id)
        with make_http_client() as http:
            result = reconcile_meta(MetaClient(selected, Platform(change.platform), http), store, change_id)
    except (ChangeError, OSError, MetaError) as exc:
        _fail(str(exc))
    typer.echo(_json(result.model_dump(mode="json")))


@meta_app.command("webhook-verify")
def webhook_verify(
    mode: str = typer.Option(..., "--mode", help="hub.mode received from verification service"),
    challenge: str = typer.Option(..., "--challenge", help="hub.challenge recibido"),
    expected_token: str = typer.Option(..., "--expected-token", help="expected token configured in Meta"),
    provided_token: str = typer.Option(..., "--provided-token", help="token received during verification"),
) -> None:
    """Verify challenge and return same value if valid."""
    try:
        result = verify_challenge(mode=mode, provided_token=provided_token, challenge=challenge,
                                 expected_token=expected_token)
    except MetaError as exc:
        _fail(str(exc))
    typer.echo(result)


@meta_app.command("webhook-ingest")
def webhook_ingest(
    body_path: Path = typer.Option(..., "--body", help="Path to raw JSON payload received by webhook (exact bytes)"),
    platform: Platform = typer.Option(..., "--platform"),
    signature: str = typer.Option(..., "--signature", help="X-Hub-Signature-256: value without whitespace"),
    app_secret: str = typer.Option(..., "--app-secret", help="Meta app secret"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Ingest HMAC-validated webhook payload without writing to Meta."""
    selected = _brand(root, brand)
    try:
        raw = body_path.read_bytes()
        store = WebhookStore(selected, platform.value)
        counts = store.ingest(raw=raw, signature=signature, app_secret=app_secret)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(counts))


@meta_app.command("webhook-events")
def webhook_events(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List locally persisted webhook events."""
    selected = _brand(root, brand)
    try:
        store = WebhookStore(selected, platform.value)
        rows = store.events(limit=limit)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(rows))


@meta_app.command("webhook-reconcile")
def webhook_reconcile(
    event_id: str = typer.Argument(..., help="Persisted event ID (canonical sha256)"),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Reconcile an event using remote GET only; never write."""
    selected = _brand(root, brand)
    try:
        store = WebhookStore(selected, platform.value)
        with make_http_client() as http:
            result = store.reconcile(MetaClient(selected, platform, http), event_id)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("identity")
def identity(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Display identity and linked account after ownership validation."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).identity()
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("profile")
def profile(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    fields: str = typer.Option(None, "--fields", help="Comma-separated fields"),
) -> None:
    """Read active node profile without writes."""
    selected = _brand(root, brand)
    try:
        requested = None if fields is None else [part.strip() for part in fields.split(",") if part.strip()]
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).profile(fields=requested)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("content")
def content(
    target_id: str = typer.Argument(..., help="ID remoto (post/video/media)."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    kind: str = typer.Option(
        None,
        "--kind",
        help="facebook: post|video, instagram: media",
    ),
) -> None:
    """Read owned content and verify remote ownership signature."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).content(target_id, kind=kind or None)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("reactions")
def reactions(
    target_id: str = typer.Argument(..., help="Remote Facebook content ID for reaction reads."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    max_pages: int = typer.Option(10, "--max-pages", min=1, max=50),
) -> None:
    """Read reactions on owned publication (Meta/Facebook only)."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).reactions(target_id, max_pages=max_pages)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("content-list")
def content_list(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    edge: str = typer.Option(None, "--edge", help="feed|photos|posts|videos|video_reels|stories|scheduled_posts|media"),
    max_pages: int = typer.Option(10, "--max-pages"),
) -> None:
    """Read owned listing with bounded pagination."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).content_list(edge=edge or None, max_pages=max_pages)
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))


@meta_app.command("insights")
def insights(
    target_id: str,
    metric: list[str] = typer.Option(..., "--metric", help="Content metric; repeat for multiple metrics."),
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
    period: str | None = typer.Option(None, "--period"),
    since: str | None = typer.Option(None, "--since", help="UTC YYYY-MM-DD date."),
    until: str | None = typer.Option(None, "--until", help="Exclusive UTC YYYY-MM-DD date."),
    breakdown: list[str] = typer.Option(None, "--breakdown"),
    max_pages: int = typer.Option(10, "--max-pages", min=1, max=50),
) -> None:
    """Read owned content insights with explicit metrics and bounded pagination."""
    def timestamp(value, label):
        if value is None:
            return None
        try:
            return int(datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc).timestamp())
        except ValueError:
            _fail(f"--{label} must be a valid YYYY-MM-DD date")

    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).insights(
                target_id, metric, period=period,
                since=timestamp(since, "since"), until=timestamp(until, "until"),
                breakdown=breakdown or None, max_pages=max_pages,
            )
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))
    if result.get("complete") is False:
        raise typer.Exit(1)


@meta_app.command("publishing-limit")
def publishing_limit(
    platform: Platform = typer.Option(..., "--platform"),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Read documented publishing limit without writes."""
    selected = _brand(root, brand)
    try:
        with make_http_client() as http:
            result = MetaClient(selected, platform, http).content_publishing_limit()
    except (ChangeError, MetaError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(result))
