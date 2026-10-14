"""Explicit JSON for durable community operations, with separate approval UI."""
import json
import sys
from contextlib import nullcontext, redirect_stdout

import typer

from socialctl.agent_errors import error_document, error_for_code
from socialctl.brands import cargar_brand, BrandNoEncontrada, NombreDeMarcaInvalido, AccountsInvalido
from socialctl.management.changes import ApprovalMismatch
from socialctl.read_reports import safe_error


class OperationOutput:
    def __init__(self, json_output, *, read_only=False):
        self.json_output = json_output
        self.read_only = read_only
        self.started = False
        self.change_id = None

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, traceback):
        if exc is None or isinstance(exc, typer.Exit):
            return False
        if not isinstance(exc, (Exception, KeyboardInterrupt)):
            return False
        if not self.json_output:
            if isinstance(exc, (typer.Abort, KeyboardInterrupt)):
                return False
            typer.echo(safe_error(str(exc)))
            raise typer.Exit(1) from None
        if self.started:
            # Service entry can persist/send a write before a later failure. Even
            # a failed journal save cannot prove that the provider rejected it.
            detail = error_for_code('operation_uncertain', read_only=False)
        elif isinstance(exc, (BrandNoEncontrada, NombreDeMarcaInvalido, AccountsInvalido)):
            detail = error_for_code('invalid_input')
        elif isinstance(exc, ApprovalMismatch):
            detail = error_for_code('approval_mismatch')
        elif isinstance(exc, (typer.Abort, KeyboardInterrupt)):
            detail = error_for_code('operation_cancelled')
        else:
            detail = error_document(exc, read_only=self.read_only)
        payload = {'version': 1, 'status': 'error', 'error': detail}
        if self.change_id is not None:
            payload['change_id'] = self.change_id
        typer.echo(json.dumps(payload))
        raise typer.Exit(1) from None

    def brand(self, root, name):
        return cargar_brand(root, name)

    def approve(self, change, preview):
        self.change_id = change.id
        typer.echo(preview, err=self.json_output)
        # Typer's input helper still prints the last prompt character to stdout
        # even with err=True. Route the entire prompt interaction, never results.
        with redirect_stdout(sys.stderr) if self.json_output else nullcontext():
            digest = typer.prompt('Enter the exact fingerprint to approve', err=self.json_output)
        if digest != change.fingerprint:
            raise ApprovalMismatch('approval does not match exact fingerprint')
        return digest

    def begin_write(self):
        self.started = True

    def emit(self, result):
        payload = result.model_dump(mode='json')
        if self.json_output and result.status not in {'verified', 'respondido'}:
            code = {
                'proposed': 'approval_required', 'draft': 'approval_required',
                'rejected': 'operation_rejected', 'failed': 'operation_rejected',
                'conflict': 'operation_rejected', 'bloqueado': 'operation_rejected',
                'applying': 'operation_uncertain', 'uncertain': 'operation_uncertain',
                'applied_unverified': 'operation_uncertain', 'partial': 'operation_uncertain',
                'accepted_unverifiable': 'operation_uncertain', 'incierto': 'operation_uncertain',
            }.get(result.status, 'operation_failed')
            payload['error_detail'] = error_for_code(code, read_only=False)
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
