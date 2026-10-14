"""Opt-in publication reports retain observations across incomplete execution."""
import json
import sys
from contextlib import redirect_stdout

import typer

from socialctl.agent_errors import error_document, error_for_code
from socialctl.read_reports import safe_error


class PublishOutput:
    def __init__(self, enabled, *, command, brand, slug):
        self.enabled = enabled
        self.command, self.brand, self.slug = command, brand, slug
        self.started = False
        self.status = None
        self.preview = None
        self.results = {}
        self.errors = []
        self.persistence = {'complete': None}
        self.retry = {'blocked': [], 'selected': []}

    def __enter__(self):
        if not self.enabled:
            return None
        self.redirect = redirect_stdout(sys.stderr)
        self.redirect.__enter__()
        return self

    def __exit__(self, kind, exc, traceback):
        if not self.enabled:
            return False
        self.redirect.__exit__(kind, exc, traceback)
        if exc is not None and not isinstance(exc, (Exception, KeyboardInterrupt)):
            return False
        code = (exc.exit_code or 0) if isinstance(exc, typer.Exit) else 130 if isinstance(exc, KeyboardInterrupt) else 1
        if exc is not None:
            if isinstance(exc, (KeyboardInterrupt, typer.Abort)) and not self.started:
                self.status = 'cancelled'
                self.add_error('operation_cancelled')
            elif not isinstance(exc, typer.Exit):
                self.errors.append(error_for_code('operation_uncertain') if self.started
                                   else error_document(exc, read_only=False))
            elif code and not self.errors and not self._result_errors():
                self.add_error('operation_uncertain' if self.started else 'invalid_input')
        failed = bool(self.errors or self._result_errors())
        status = self.status
        if status not in {'preview', 'cancelled'}:
            observed = any(r['status'] in {'publicado', 'pendiente_confirmacion'} for r in self.results.values())
            status = ('partial' if observed else 'error') if failed else ('ok' if observed else 'noop')
        payload = {'version': 1, 'command': self.command, 'brand': self.brand,
                   'slug': self.slug, 'status': status, 'resultados': list(self.results.values()),
                   'errors': self.errors, 'persistence': self.persistence}
        if self.preview is not None:
            payload['preview'] = self.preview
        if self.command == 'retry':
            payload['retry'] = self.retry
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        if exc is not None and not isinstance(exc, typer.Exit):
            raise typer.Exit(code) from None
        return False

    def _result_errors(self):
        return any('error_detail' in row or 'first_comment_error_detail' in row for row in self.results.values())

    def add_error(self, code, **context):
        self.errors.append({**context, **error_for_code(code, read_only=False)})

    def begin_write(self):
        self.started = True
        self.persistence = {'complete': False}

    def observe_safely(self, result):
        try:
            self.observe(result)
        except Exception:
            if not any(error['code'] == 'reporting_incomplete' for error in self.errors):
                self.add_error('reporting_incomplete')

    def observe(self, result):
        row = result.model_dump(mode='json')
        if row.get('error') is not None:
            row['error'] = safe_error(row['error'])
        if row.get('url') is not None:
            row['url'] = safe_error(row['url'])
        row['warnings'] = [safe_error(value) for value in row.get('warnings', [])]
        if row['riesgo_duplicado'] is True:
            row['error_detail'] = error_for_code('operation_uncertain', read_only=False)
        elif row['status'] == 'error':
            row['error_detail'] = error_for_code('operation_rejected', read_only=False)
        elif row['status'] == 'pendiente_confirmacion':
            row['error_detail'] = error_for_code('publication_pending', read_only=False)
        if row.get('first_comment_status') not in {None, 'verified'}:
            row['first_comment_error_detail'] = error_for_code('comment_incomplete', read_only=False)
        self.results[row['platform']] = row
