"""Operator-facing migration lifecycle; no publication or remote writes."""
from pathlib import Path
import json

import typer

from socialctl.brands import cargar_brand
from socialctl import queue_migration

migration_app = typer.Typer(help="Reubica media y migra aprobaciones legacy verificadas.")
from socialctl.workspace import default_root

DEFAULT_ROOT = default_root()


def _run(operation):
    try:
        return operation()
    except Exception as exc:
        typer.echo(f"Error de migración: {exc}", err=True)
        raise typer.Exit(1) from exc


@migration_app.command("prepare")
def prepare(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT),
            backend: str = typer.Option("json"), dry_run: bool = typer.Option(False, "--dry-run")):
    proposal = _run(lambda: queue_migration.prepare(cargar_brand(root, brand), backend=backend, dry_run=dry_run))
    typer.echo(proposal["preview"])
    typer.echo("\nMapeo exacto de rutas:")
    for item in proposal["media"]:
        typer.echo(f"{item['source']} -> media/{item['relative_path']} -> {item['public_url']} ({item['size']} bytes, SHA256 {item['sha256']})")
    typer.echo("\n" + proposal["legacy_limitation"])
    for entry in proposal["entries"]:
        typer.echo(f"{entry['id']} | {entry['status']} | {entry['scheduled_at']} | intentos={entry['attempts']}")
    typer.echo(f"Propuesta: {proposal['id']}\nDigest: {proposal['digest']}")
    typer.echo("Dry-run: no se guardó propuesta ni se modificó cola." if dry_run else "Propuesta guardada; no se modificó cola ni se publicó contenido.")


@migration_app.command("stage")
def stage(brand: str = typer.Option(..., "--brand"), proposal: str = typer.Option(...),
          output: Path = typer.Option(...), root: Path = typer.Option(DEFAULT_ROOT)):
    manifest = _run(lambda: queue_migration.stage(cargar_brand(root, brand), proposal, output))
    typer.echo(json.dumps(manifest, indent=2, ensure_ascii=False))
    typer.echo(f"Transfer manifest: {output / 'transfer-manifest.json'}; transferencia remota a cargo del operador.")


@migration_app.command("apply")
def apply(brand: str = typer.Option(..., "--brand"), proposal: str = typer.Option(...),
          digest: str = typer.Option(...), root: Path = typer.Option(DEFAULT_ROOT),
          acknowledge_legacy_limitations: bool = typer.Option(False, "--acknowledge-legacy-limitations")):
    intent = _run(lambda: queue_migration.apply(cargar_brand(root, brand), proposal, digest,
                  acknowledge_legacy_limitations=acknowledge_legacy_limitations))
    typer.echo(f"Migración {proposal}: {intent['status']}; aprobaciones, cuentas y horarios conservados.")


@migration_app.command("rollback")
def rollback(brand: str = typer.Option(..., "--brand"), proposal: str = typer.Option(...),
             root: Path = typer.Option(DEFAULT_ROOT)):
    _run(lambda: queue_migration.rollback(cargar_brand(root, brand), proposal))
    typer.echo(f"Migración {proposal} restaurada. Media suministrada y copias conservadas.")
