"""Operator-facing migration lifecycle; no publication or remote writes."""
from pathlib import Path
import json

import typer

from socialctl.brands import cargar_brand
from socialctl import queue_migration

migration_app = typer.Typer(help="Relocate media and migrate verified legacy approvals.")
from socialctl.workspace import default_root

DEFAULT_ROOT = default_root()


def _run(operation):
    try:
        return operation()
    except Exception as exc:
        typer.echo(f"Migration error: {exc}", err=True)
        raise typer.Exit(1) from exc


@migration_app.command("prepare")
def prepare(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT),
            backend: str = typer.Option("json"), dry_run: bool = typer.Option(False, "--dry-run")):
    proposal = _run(lambda: queue_migration.prepare(cargar_brand(root, brand), backend=backend, dry_run=dry_run))
    typer.echo(proposal["preview"])
    typer.echo("\nExact path mapping:")
    for item in proposal["media"]:
        typer.echo(f"{item['source']} -> media/{item['relative_path']} -> {item['public_url']} ({item['size']} bytes, SHA256 {item['sha256']})")
    typer.echo("\n" + proposal["legacy_limitation"])
    for entry in proposal["entries"]:
        typer.echo(f"{entry['id']} | {entry['status']} | {entry['scheduled_at']} | intentos={entry['attempts']}")
    typer.echo(f"Propuesta: {proposal['id']}\nDigest: {proposal['digest']}")
    typer.echo("Dry-run: no proposal saved or queue modified." if dry_run else "Proposal saved; queue unchanged and no content published.")


@migration_app.command("stage")
def stage(brand: str = typer.Option(..., "--brand"), proposal: str = typer.Option(...),
          output: Path = typer.Option(...), root: Path = typer.Option(DEFAULT_ROOT)):
    manifest = _run(lambda: queue_migration.stage(cargar_brand(root, brand), proposal, output))
    typer.echo(json.dumps(manifest, indent=2, ensure_ascii=False))
    typer.echo(f"Transfer manifest: {output / 'transfer-manifest.json'}; remote transfer is the operator's responsibility.")


@migration_app.command("apply")
def apply(brand: str = typer.Option(..., "--brand"), proposal: str = typer.Option(...),
          digest: str = typer.Option(...), root: Path = typer.Option(DEFAULT_ROOT),
          acknowledge_legacy_limitations: bool = typer.Option(False, "--acknowledge-legacy-limitations")):
    intent = _run(lambda: queue_migration.apply(cargar_brand(root, brand), proposal, digest,
                  acknowledge_legacy_limitations=acknowledge_legacy_limitations))
    typer.echo(f"Migration {proposal}: {intent['status']}; approvals, accounts and schedules preserved.")


@migration_app.command("rollback")
def rollback(brand: str = typer.Option(..., "--brand"), proposal: str = typer.Option(...),
             root: Path = typer.Option(DEFAULT_ROOT)):
    _run(lambda: queue_migration.rollback(cargar_brand(root, brand), proposal))
    typer.echo(f"Migration {proposal} restored. Supplied media and copies preserved.")
