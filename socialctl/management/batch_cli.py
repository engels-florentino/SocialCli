"""Full batch previews and the same exact-digest approval used by V1 commands."""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import typer

from socialctl.management.batches import BatchStore, apply_batch, prepare_batch, prepare_restore
from socialctl.management.changes import ChangeError
from socialctl.management.youtube import YouTubeManagementError
from socialctl.management.youtube_metadata import YouTubeMetadataClient


def render_batch_preview(batch):
    def formatted(value):
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
    lines = [f"V2 batch: {batch.id}", f"Brand: {batch.brand} ({batch.brand_root})",
             f"Canal: {batch.account}", f"Exact fingerprint for approval: {batch.fingerprint}",
             "Linked files:\n" + formatted(batch.source_files)]
    if batch.restored_from:
        lines.append(f"Proposed restoration of: {batch.restored_from}")
    for number, op in enumerate(batch.operations, 1):
        before, after = formatted(op.before), formatted(op.after)
        lines.extend([f"\nOperation {number}: {op.video_id} / {op.kind} / {op.status}",
                      f"Operation fingerprint: {op.fingerprint}",
                      "Explicit patch:\n" + formatted(op.patch),
                      "Before:\n" + before, "After (complete body per part):\n" + after,
                      "Diff:\n" + ("\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(),
                            fromfile="actual", tofile="propuesto", lineterm="")) or "(no differences)")])
        snippet = op.after.get("snippet", op.observed["snippet"])
        visible = re.findall(r"(?<!\w)#[\w]+", snippet.get("title", "") + "\n" + snippet.get("description", ""))
        lines.append("Tags internos: " + formatted(snippet.get("tags", [])))
        lines.append("Visible hashtags in title/description: " + formatted(visible))
        if op.resets:
            lines.append("Explicit resets: " + formatted(op.resets))
        if op.kind == "schedule":
            lines.append("YouTube native scheduling: private + user declaration of never published; "
                         "API determines historical eligibility. This is separate from the server queue.")
        if op.kind == "chapters":
            lines.append("Chapters: supplied text validated against API duration; does not prove UI feature availability.")
    lines.extend(["Restoration unavailable: " + formatted(batch.unavailable),
                  "Limitations: metadata only; does not restore deletions or media uploads. "
                  "defaultAudioLanguage is preserved; editing it is unverified."])
    return "\n".join(lines)


def apply_selected(selected, change_id, yes):
    from socialctl.management import cli
    store = BatchStore(selected.raiz)
    batch = store.load(change_id)
    typer.echo("Complete DRY-RUN before approval; no YouTube writes performed.")
    typer.echo(render_batch_preview(batch))
    if yes:
        typer.echo("--yes does not replace exact batch approval.")
    try:
        approval = typer.prompt("Enter the exact batch fingerprint to approve")
    except (EOFError, KeyboardInterrupt):
        cli._fail("Application cancelled: exact approval missing")
    if approval != batch.fingerprint:
        cli._fail("Application cancelled: incorrect fingerprint")
    with cli.make_http_client() as http:
        result = apply_batch(YouTubeMetadataClient(selected, http), store, change_id, approval)
    for op in result.operations:
        typer.echo(f"{op.video_id}: {op.status}")
    if any(op.status != "applied" for op in result.operations):
        typer.echo("Partial result. Resume processes only pending operations; uncertain attempts are reread only.")
        raise typer.Exit(1)


def register(content_app, changes_app, default_root):
    @content_app.command("edit-batch")
    def content_edit_batch(
        file: Path = typer.Option(..., "--file"),
        brand: str = typer.Option(..., "--brand"),
        root: Path = typer.Option(default_root, "--root"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        """Prepare a V2 batch with explicit IDs and one operation per video."""
        from socialctl.management import cli
        if not dry_run:
            cli._fail("Preparing a batch requires --dry-run and its full preview")
        selected = cli._brand(root, brand)
        try:
            with cli.make_http_client() as http:
                batch = prepare_batch(YouTubeMetadataClient(selected, http), BatchStore(selected.raiz), file)
        except (ChangeError, YouTubeManagementError, OSError) as exc:
            cli._fail(str(exc))
        typer.echo("DRY-RUN: no YouTube writes performed.")
        typer.echo(render_batch_preview(batch))

    @changes_app.command("restore")
    def changes_restore(
        change_id: str = typer.Argument(...),
        brand: str = typer.Option(..., "--brand"),
        root: Path = typer.Option(default_root, "--root"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ):
        """Propose restoring saved fields against a fresh remote read."""
        from socialctl.management import cli
        if not dry_run:
            cli._fail("Restoration requires --dry-run; then approve the new fingerprint")
        selected = cli._brand(root, brand)
        try:
            with cli.make_http_client() as http:
                batch = prepare_restore(YouTubeMetadataClient(selected, http), BatchStore(selected.raiz), change_id)
        except (ChangeError, YouTubeManagementError, OSError) as exc:
            cli._fail(str(exc))
        typer.echo("DRY-RUN: new local proposal, no remote writes.")
        typer.echo(render_batch_preview(batch))
