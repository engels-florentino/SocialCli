"""Versioned read envelopes and bounded, credential-safe diagnostics."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote

import typer
from pydantic import BaseModel, ConfigDict, Field


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


def emit_report(report: ReadReport) -> int:
    typer.echo(report.model_dump_json(indent=2))
    return 0 if report.status == 'ok' else 1


def fail_read(exc: Exception, *, json_output: bool) -> None:
    message = safe_error(str(exc))
    code = 2 if message.startswith('--timeout must') else 1
    if json_output:
        emit_report(ReadReport(status='error', errors=[{'message':message}]))
    else:
        typer.echo(message)
    raise typer.Exit(code)
