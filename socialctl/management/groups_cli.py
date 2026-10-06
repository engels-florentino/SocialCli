"""Local CLI for manual Facebook Group publication handoff."""
from __future__ import annotations

import json
import os
import webbrowser
from datetime import datetime
from enum import Enum
from pathlib import Path

import typer

from socialctl.management.changes import ChangeError
from socialctl.management.cli import DEFAULT_ROOT, _brand, _fail
from socialctl.management.facebook_groups import (
    CONFIRMATION_PHRASE,
    GroupError,
    GroupStore,
    approve_handoff,
    confirm_group,
    export_queue,
    load_group,
    mark_handoff_opened,
    prepare_group,
    record_handoff_error,
)


group_app = typer.Typer(
    help="Prepare manual Group publications; does not use or emulate the retired API."
)


class ExportFormat(str, Enum):
    JSON = "json"
    TEXT = "text"


def _datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise GroupError(f"{field} must be an ISO 8601 timestamp with timezone") from None
    if parsed.utcoffset() is None:
        raise GroupError(f"{field} must include a timezone")
    return parsed


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_group_preview(change) -> str:
    if change.media:
        dimensions = f"{change.media.width}x{change.media.height}"
        duration = (
            f"{change.media.duration_s} s"
            if change.media.duration_s is not None
            else "not applicable"
        )
        media = (
            f"\n  Path: {change.media.relative_path}\n"
            f"  SHA-256: {change.media.sha256}\n"
            f"  Size: {change.media.size_bytes} bytes\n"
            f"  Type: {change.media.kind}\n"
            f"  Dimensions: {dimensions}\n"
            f"  Duration: {duration}"
        )
    else:
        media = " (no media)"
    final_text = f"{change.copy_text}\n\n{change.youtube_url}"
    return (
        "FULL PREVIEW — manual Facebook Group publication\n"
        f"ChangeSet: {change.id}\n"
        f"Brand: {change.brand}\n"
        f"Group: {change.destination.name}\n"
        f"Group URL: {change.destination.url}\n"
        f"Suggested window: {change.suggested_window.start.isoformat()} → "
        f"{change.suggested_window.end.isoformat()}\n"
        f"Existing media:{media}\n"
        f"Status: {change.status}\n\n"
        f"Final text to copy:\n{final_text}\n\n"
        "Limitation: Meta retired the Groups API. socialcli will not send any POST, "
        "scraping or private endpoint calls; user publishes manually.\n"
        f"Exact fingerprint for approval: {change.fingerprint}"
    )


def render_handoff(change) -> str:
    media = change.media.relative_path if change.media else "(no media)"
    return (
        "BROWSER HANDOFF — does not confirm publication\n"
        f"1. Open: {change.destination.url}\n"
        f"2. Copy exactly:\n{change.copy_text}\n\n{change.youtube_url}\n"
        f"3. Attach existing media: {media}\n"
        "4. Publish manually and save the post URL or a screenshot/PDF.\n"
        f"5. Record the result with: socialcli group confirm {change.id} "
        "--post-url URL --brand BRAND\n"
        "Until confirmation, this status does not represent a publication."
    )


def _render_text(items: list[dict]) -> str:
    if not items:
        return "Groups queue is empty."
    chunks = []
    for item in items:
        media = (item.get("media") or {}).get("relative_path") or "(no media)"
        confirmation = item.get("confirmation") or {}
        chunks.append(
            f"[{item['status']}] {item['group']['name']} — {item['id']}\n"
            f"Group: {item['group']['url']}\n"
            f"Window: {item['suggested_window']['start']} → {item['suggested_window']['end']}\n"
            f"Media: {media}\n"
            f"Copy:\n{item['copy']}\n\n{item['youtube_url']}\n"
            f"Confirmation supplied: {confirmation.get('post_url') or '(none)'}\n"
            f"Reminder: {item['reminder']}"
        )
    return "\n\n".join(chunks)


def _write_new(path: Path, content: str) -> None:
    if ".secrets" in path.parts:
        raise GroupError("export cannot be written inside .secrets")
    if not path.parent.exists() or not path.parent.is_dir():
        raise GroupError("export destination folder does not exist")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise GroupError("export file already exists; it will not be overwritten") from None
    except OSError as exc:
        raise GroupError(f"failed to write export: {exc}") from None


@group_app.command("prepare")
def prepare(
    group_name: str = typer.Option(..., "--group-name", help="Visible target Group name."),
    group_url: str = typer.Option(..., "--group-url", help="URL HTTPS exacta /groups/GRUPO."),
    copy: str = typer.Option(..., "--copy", help="Exact supplied post text."),
    youtube_url: str = typer.Option(..., "--youtube-url", help="Link to the long-form documentary."),
    window_start: str = typer.Option(..., "--window-start", help="ISO 8601 start with timezone."),
    window_end: str = typer.Option(..., "--window-end", help="ISO 8601 end with timezone."),
    media: str | None = typer.Option(None, "--media", help="Optional path inside <Brand>/media/."),
    brand: str = typer.Option(..., "--brand", help="Owning brand; never inferred."),
    root: Path = typer.Option(DEFAULT_ROOT, "--root", help="Social project root."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Persist and display proposal; never publish."),
) -> None:
    """Validate and persist a local proposal without opening browser or accessing network."""
    selected = _brand(root, brand)
    try:
        change = prepare_group(
            selected,
            GroupStore(selected.raiz),
            group_name=group_name,
            group_url=group_url,
            copy=copy,
            youtube_url=youtube_url,
            window_start=_datetime(window_start, "window_start"),
            window_end=_datetime(window_end, "window_end"),
            media=media,
        )
    except (ChangeError, OSError, ValueError) as exc:
        _fail(str(exc))
    typer.echo("Remote DRY-RUN: local proposal persisted; no Facebook publication.")
    typer.echo(render_group_preview(change))


@group_app.command("status")
def status(
    change_id: str,
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Display ChangeSet and local journal without network access."""
    selected = _brand(root, brand)
    try:
        change = load_group(selected, GroupStore(selected.raiz), change_id)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(change))


@group_app.command("export")
def export(
    change_id: str | None = typer.Argument(None, help="Optional UUID; without it, export the entire queue."),
    format: ExportFormat = typer.Option(ExportFormat.TEXT, "--format"),
    output: Path | None = typer.Option(None, "--output", help="Optional new file; never overwritten."),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Export JSON or readable views from local storage."""
    selected = _brand(root, brand)
    try:
        items = export_queue(selected, GroupStore(selected.raiz), change_id)
        content = _json({"version": 1, "authority": "local_manual_handoff", "items": items}) if format is ExportFormat.JSON else _render_text(items)
        if output is not None:
            _write_new(output, content)
        else:
            typer.echo(content)
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    if output is not None:
        typer.echo(f"Export local escrito: {output}")


@group_app.command("handoff")
def handoff(
    change_id: str,
    approval_digest: str | None = typer.Option(None, "--approval-digest", help="Exact fingerprint from prepare."),
    open_browser: bool = typer.Option(False, "--open-browser", help="Open a tab; do not publish or fill forms."),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Approve package and hand it to user for manual publication."""
    selected = _brand(root, brand)
    store = GroupStore(selected.raiz)
    try:
        prepared = load_group(selected, store, change_id)
        typer.echo(render_group_preview(prepared))
        digest = approval_digest or typer.prompt("Enter the exact fingerprint to approve")
        change = approve_handoff(selected, store, change_id, digest)
        typer.echo(render_handoff(change))
        if open_browser and change.status == "handoff_ready":
            try:
                opened = webbrowser.open_new_tab(change.destination.url)
            except OSError as exc:
                record_handoff_error(selected, store, change_id, f"failed to open browser: {exc}")
                raise GroupError("failed to open browser; handoff remains ready") from None
            if not opened:
                record_handoff_error(selected, store, change_id, "browser did not confirm opening")
                raise GroupError("browser did not confirm opening; handoff remains ready")
            change = mark_handoff_opened(selected, store, change_id)
            typer.echo("Tab opened. socialcli has not published or filled the form.")
        elif open_browser:
            typer.echo("Handoff was already opened or confirmed; no additional tab will open.")
    except (ChangeError, OSError) as exc:
        _fail(str(exc))


@group_app.command("confirm")
def confirm(
    change_id: str,
    post_url: str | None = typer.Option(None, "--post-url", help="Post URL in the same Group."),
    evidence: str | None = typer.Option(None, "--evidence", help="Existing JPG/PNG/WebP/PDF, relative to the brand."),
    confirmation: str | None = typer.Option(None, "--confirmation", help="Exact explicit confirmation phrase."),
    brand: str = typer.Option(..., "--brand"),
    root: Path = typer.Option(DEFAULT_ROOT, "--root"),
) -> None:
    """Record user-supplied evidence without querying or publishing to Facebook."""
    selected = _brand(root, brand)
    try:
        phrase = confirmation or typer.prompt(f"Enter exactly {CONFIRMATION_PHRASE}")
        change = confirm_group(
            selected,
            GroupStore(selected.raiz),
            change_id,
            confirmation_phrase=phrase,
            post_url=post_url,
            evidence=evidence,
        )
    except (ChangeError, OSError) as exc:
        _fail(str(exc))
    typer.echo(_json(change))
    typer.echo("Manual confirmation recorded; authority: user-supplied statement/evidence.")
