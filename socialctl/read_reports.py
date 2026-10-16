"""Versioned read envelopes and bounded, credential-safe diagnostics."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

import typer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from socialctl.agent_errors import error_document, error_for_code


def safe_error(message: str, *, secrets: tuple[str, ...] = ()) -> str:
    text = str(message)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]').replace(quote(secret, safe=''), '[REDACTED]')
    text = re.sub(r'(?i)\b(access_token|refresh_token|client_secret|connection_secret|poll_secret)\s*[=:]\s*[^&\s"\'<>]+', r'\1=[REDACTED]', text)
    text = re.sub(r'(?i)([?&]code=)[^&\s"\'<>]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(Authorization\s*:\s*Bearer\s+)[^\s"\'<>]+', r'\1[REDACTED]', text)
    return ''.join(c if c.isprintable() else ' ' for c in text)[:1000]


class ReadReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    status: Literal['ok', 'partial', 'error']
    observed_at: datetime = Field(default_factory=lambda:datetime.now(timezone.utc))
    coverage: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    cursor: str | None = None

    @field_validator('errors')
    @classmethod
    def actions(cls, rows):
        result = []
        for row in rows:
            detail = error_for_code(row.get('code', 'read_partial'), read_only=True)
            result.append({**row, **detail,
                'message':safe_error(row.get('message', detail['message'])),
                'guidance':detail['message']})
        return result


def annotate_partial_read(result):
    """Add guidance without changing legacy items, cursor or completion fields."""
    if not isinstance(result, dict) or result.get('complete') is not False:
        return result
    code = ('read_interrupted' if result.get('interrupted') is True else
            'read_deadline' if result.get('timed_out') is True else 'read_partial')
    detail = result.get('error_detail')
    if isinstance(detail, dict) and code == 'read_partial':
        code = detail.get('code', code)
    return {**result, 'error_detail':error_for_code(code, read_only=True)}


def load_read_brand(root, brand):
    """JSON read entry points must keep a structured error before any request."""
    from socialctl.brands import cargar_brand, BrandNoEncontrada, NombreDeMarcaInvalido, AccountsInvalido
    from socialctl.youtube_data import YouTubeDataCleanupError
    try:
        return cargar_brand(root, brand)
    except YouTubeDataCleanupError as exc:
        emit_report(ReadReport(status='error', errors=[error_document(exc,read_only=True)]))
        raise typer.Exit(1) from None
    except (BrandNoEncontrada, NombreDeMarcaInvalido, AccountsInvalido, OSError):
        emit_report(ReadReport(status='error', errors=[error_for_code('invalid_input')]))
        raise typer.Exit(1) from None


def emit_report(report: ReadReport) -> int:
    typer.echo(report.model_dump_json(indent=2))
    return read_exit_code(report.status)


def fail_read(exc: Exception, *, json_output: bool) -> None:
    detail = error_document(exc, read_only=True)
    message = detail['message'] if detail['code'] == 'local_configuration' else safe_error(str(exc))
    code = 2 if message.startswith('--timeout must') else 1
    if json_output:
        emit_report(ReadReport(status='error', errors=[{**detail, 'message':message}]))
    else:
        typer.echo(message)
    raise typer.Exit(code)


def read_exit_code(status: str) -> int:
    return {'ok': 0, 'partial': 3, 'error': 1}[status]
