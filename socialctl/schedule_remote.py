"""An explicit, bounded SSH client. Never forwards ordinary local commands."""
from __future__ import annotations

import re
from datetime import datetime
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal

import typer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from socialctl.brands import _validar_nombre_de_marca
from socialctl.scheduler import ScheduleError, parse_scheduled_at

remote_app = typer.Typer(help="Gestiona la cola autoritativa por SSH; solo posts existentes en el servidor.")
from socialctl.workspace import default_root

DEFAULT_ROOT = default_root()
REMOTE_NOTICE = "Ejecutor remoto: la copia local está obsoleta y no es autoritativa. Usa schedule-remote status o schedule-remote health con --brand."


class SchedulePreview(BaseModel):
    """Only this validation-only envelope may cross SSH on a failed dry-run."""
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol: Literal["socialctl.schedule-preview.v1"]
    valid: bool
    preview: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    scheduled_at: str

    @field_validator("scheduled_at")
    @classmethod
    def valid_instant(cls, value):
        parse_scheduled_at(value)
        return value

    @field_validator("preview")
    @classmethod
    def no_credentials(cls, value):
        if re.search(r"(?i)(?:[\"']?(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|password|authorization|token)[\"']?\s*[:=]|\bbearer\s+\S+)", value):
            raise ValueError("preview contiene un posible secreto")
        return value


def is_remote(brand_root: Path) -> bool:
    path = brand_root / ".socialctl/remote-executor.json"
    return path.exists() or path.is_symlink()


class RemoteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host: str
    user: str
    port: int = Field(gt=0, le=65535)
    root: str
    executable: str

    @field_validator("host")
    @classmethod
    def host_valid(cls, value):
        if len(value) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", part) or len(part) > 63 for part in value.split(".")):
            raise ValueError("host inválido")
        return value

    @field_validator("user")
    @classmethod
    def user_valid(cls, value):
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", value):
            raise ValueError("usuario inválido")
        return value

    @field_validator("root", "executable")
    @classmethod
    def path_valid(cls, value):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in value.split("/") or str(PurePosixPath(value)) != value or value == "/":
            raise ValueError("ruta absoluta inválida")
        return value

    @field_validator("executable")
    @classmethod
    def executable_valid(cls, value):
        if not value.endswith("/.venv/bin/socialctl"):
            raise ValueError("ejecutable socialctl inválido")
        return value


def _component(value: str) -> str:
    if not re.fullmatch(r"[\w][\w .-]*", value) or value in {".", ".."}:
        raise ScheduleError("identificador remoto inválido")
    return value


def _entry(value: str) -> str:
    parts = value.split("/")
    if len(parts) != 2 or parts[1] not in {"facebook", "instagram"}:
        raise ScheduleError("identificador remoto inválido")
    _component(parts[0])
    return value


def _send(root: Path, brand: str, args: list[str]) -> None:
    try:
        _component(brand)
        brand_root = _validar_nombre_de_marca(root, brand)
        config = RemoteConfig.model_validate_json((brand_root / ".socialctl/remote-executor.json").read_bytes())
    except Exception as exc:
        raise ScheduleError("configuración remota inválida o ausente") from exc
    preview_mode = args[0] == "schedule" and "--dry-run" in args
    if preview_mode:
        args = [*args, "--preview-json"]
    # The server runs the same strict schedule validation as a local proposal.
    # No compatibility context crosses SSH; only run-due can authorize old entries.
    command = shlex.join([config.executable] + args + ["--brand", brand, "--root", config.root])
    argv = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=15", "-p", str(config.port), "--", f"{config.user}@{config.host}", command]
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=180, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScheduleError("conexión remota fallida; revisa SSH y schedule-remote status antes de reintentar") from exc
    if preview_mode and result.returncode in {0, 1}:
        try:
            response = SchedulePreview.model_validate_json(result.stdout)
            if response.valid != (result.returncode == 0):
                raise ValueError("estado inconsistente")
        except ValueError as exc:
            raise ScheduleError("preview remoto inválido o no seguro; no se mostró salida sin verificar") from exc
        typer.echo(response.preview)
        if not response.valid:
            raise ScheduleError("No se programa nada: corrige los problemas de arriba.")
        typer.echo(f"Approval digest: {response.digest}")
        typer.echo(f"--dry-run: se programaría para {response.scheduled_at}.")
        return
    if result.returncode:
        # Transport and remote exceptions can contain credentials/URLs.
        raise ScheduleError("comando remoto fallido; verifica el estado remoto antes de reintentar")
    if args[0] == "native-schedule":
        from socialctl.native_schedule.approval import reject_secrets
        try:
            if len(result.stdout) > 1_000_000:
                raise ValueError("oversized output")
            reject_secrets(result.stdout)
        except ValueError as exc:
            raise ScheduleError("respuesta nativa remota inválida o no segura") from exc
    typer.echo(result.stdout, nl=False)


def _run(operation):
    try:
        operation()
    except ScheduleError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc


@remote_app.command("status")
def status(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT)):
    _run(lambda: _send(root, brand, ["schedule-status"]))


@remote_app.command("health")
def health(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT)):
    _run(lambda: _send(root, brand, ["schedule-health"]))


@remote_app.command("cancel")
def cancel(entry_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT)):
    _run(lambda: _send(root, brand, ["schedule-cancel", _entry(entry_id)]))


@remote_app.command("reschedule")
def reschedule(entry_id: str, at: str = typer.Option(..., "--at"),
               brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT)):
    def operation():
        parse_scheduled_at(at)
        _send(root, brand, ["schedule-reschedule", _entry(entry_id), "--at", at])
    _run(operation)


@remote_app.command("schedule")
def schedule(slug: str, at: str = typer.Option(..., "--at"),
             brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(DEFAULT_ROOT),
             dry_run: bool = typer.Option(False, "--dry-run"),
             yes: bool = typer.Option(False, "--yes"),
             approval_digest: str | None = typer.Option(None, "--approval-digest"),
             only: list[str] = typer.Option(None, "--only")):
    def operation():
        parse_scheduled_at(at)
        args = ["schedule", _component(slug), "--at", at]
        if dry_run:
            if yes or approval_digest:
                raise ScheduleError("dry-run no admite aprobación")
            args += ["--dry-run"]
        else:
            if not yes or not approval_digest or not re.fullmatch(r"[a-f0-9]{64}", approval_digest):
                raise ScheduleError("muestra --dry-run y aprueba su digest con --approval-digest y --yes")
            args += ["--approval-digest", approval_digest, "--yes"]
        for platform in only or []:
            if platform not in {"facebook", "instagram"}:
                raise ScheduleError("red remota inválida")
            args += ["--only", platform]
        _send(root, brand, args)
    _run(operation)


def send_native(root: Path, brand: str, args: list[str]) -> None:
    """Bounded native command grammar; paths resolve only at authoritative root."""
    operations={'prepare','approve','status','dispatch','reconcile','handoff','submit-marker','cancel','reschedule','migration-prepare','migration-apply','migration-recover','deliver'}
    if not args or args[0] not in operations:
        raise ScheduleError('comando nativo remoto inválido')
    operation=args[0]
    identifier=lambda value: bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value))
    digest=lambda value: bool(re.fullmatch(r'[a-f0-9]{64}',value))
    valid=operation in {'status','deliver'} and len(args)==1
    if operation=='migration-prepare':
        valid=len(args)==4 and args[1]=='--manifest' and identifier(args[2]) and args[3]=='--dry-run'
    elif operation=='migration-apply':
        valid=len(args)==5 and args[1]=='--manifest' and identifier(args[2]) and args[3]=='--digest' and digest(args[4])
    elif operation=='migration-recover':
        valid=len(args)==3 and args[1]=='--digest' and digest(args[2])
    elif operation not in {'status','deliver'} and len(args)>=2:
        valid=identifier(args[1])
        tail=args[2:]
        if operation=='prepare':
            valid=valid and (tail==['--dry-run'] or (len(tail)==3 and tail[:2]==['--persist','--approval-digest'] and digest(tail[2])))
        elif operation=='approve':
            valid=valid and len(tail)==2 and tail[0]=='--approval-digest' and digest(tail[1])
        elif operation=='reconcile':
            valid=valid and (tail in ([],['--yes']) or (len(tail)==2 and tail[0]=='--observation' and identifier(tail[1])))
        elif operation in {'cancel','reschedule'}:
            if operation=='reschedule':
                try:
                    from socialctl.native_schedule.models import aware
                    stamp=aware(datetime.fromisoformat(tail[1].replace('Z','+00:00')))
                    valid=valid and tail[0]=='--publish-at'
                    tail=tail[2:]
                except (ValueError,IndexError):
                    valid=False
            valid=valid and (tail==['--dry-run'] or (len(tail)==2 and tail[0]=='--approval-digest' and digest(tail[1])))
        else:
            valid=valid and (tail==[] or (operation=='dispatch' and tail==['--yes']))
    if not valid:
        raise ScheduleError('argumentos nativos remotos inválidos')
    _send(root,brand,['native-schedule',*args])
