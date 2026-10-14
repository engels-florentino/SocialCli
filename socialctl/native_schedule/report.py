"""Bound native command envelopes; provider state and command exit stay separate."""
import json
import sys
from contextlib import redirect_stdout
from contextvars import ContextVar

import typer

from socialctl.agent_errors import error_document, error_for_code
from .approval import reject_secrets
from .models import NativeJob

active_report = ContextVar('native_report', default=None)
READ_ONLY = {'status', 'handoff', 'migration-prepare'}


def with_action(value):
    codes = {'prepared': 'approval_required', 'approved': 'native_wait',
             'waiting_window': 'native_wait', 'ready': 'native_dispatch',
             'awaiting_ui': 'native_ui_required', 'ui_required': 'native_ui_required',
             'conflict': 'operation_rejected', 'dispatching': 'operation_uncertain',
             'uncertain': 'operation_uncertain', 'verifying': 'native_verify',
             'pending_verification': 'native_verify', 'native_scheduled': 'native_verify',
             'blocked': 'operation_rejected', 'unsupported': 'unsupported_operation'}
    result = {k: v for k, v in value.items() if k != 'next_action'}
    code = codes.get(result.get('state'))
    if code:
        result['next_action'] = error_for_code(code, read_only=False)
    return result


def validate_remote(text, *, command, brand, exit_code, request_args=()):
    if len(text) > 1_000_000:
        raise ValueError('Oversized native envelope')
    value = json.loads(text)
    fields = {'version', 'kind', 'command', 'brand', 'status', 'exit_code', 'data', 'jobs', 'jobs_complete', 'errors'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int
            or value['version'] != 1 or value['kind'] != 'native-schedule'
            or value['command'] != command or value['brand'] != brand
            or type(value['exit_code']) is not int or value['exit_code'] != exit_code
            or not 0 <= exit_code <= 255 or value['status'] != ('ok' if exit_code == 0 else 'error')
            or type(value['jobs_complete']) is not bool):
        raise ValueError('Mismatched native envelope')
    for key in ('data', 'jobs', 'errors'):
        if not isinstance(value[key], list) or any(not isinstance(row, dict) for row in value[key]):
            raise ValueError('Invalid native envelope rows')
    for row in value['jobs']:
        base = {k: v for k, v in row.items() if k != 'next_action'}
        reject_secrets(base)
        NativeJob.model_validate_json(json.dumps(base))
        if base['brand'] != brand:
            raise ValueError('Mismatched native job brand')
    for row in value['data']:
        reject_secrets({k: v for k, v in row.items() if k != 'next_action'})
    from .remote_data import validate_data
    validate_data(value['data'], command=command, brand=brand, jobs=value['jobs'], request_args=request_args)
    # Only internal catalogue codes supply remote action semantics, never an
    # arbitrary peer's message/action. Data/job safety is checked independently.
    value['errors'] = [error_for_code(row.get('code'), read_only=command in READ_ONLY) for row in value['errors']]
    value['jobs'] = [with_action(row) for row in value['jobs']]
    value['data'] = [with_action(row) for row in value['data']]
    return value


class NativeReport:
    def __init__(self, command, brand):
        self.command, self.brand = command, brand
        self.data, self.jobs, self.errors = [], [], []
        self.store = None
        self.started = False
        self.jobs_complete = False
        self.remote = None

    def __enter__(self):
        self.token = active_report.set(self)
        self.redirect = redirect_stdout(sys.stderr)
        self.redirect.__enter__()
        return self

    def bind(self, store):
        self.store = store
        self.snapshot()

    def snapshot(self):
        rows = [job.model_dump(mode='json') for job in self.store.list_jobs()]
        reject_secrets(rows)
        self.jobs = [with_action(row) for row in rows]
        # Durable checkpoints can precede a job's final remote_id (for example,
        # interruption after upload START). Expose IDs only, never upload URLs.
        for row in rows:
            for attempt in self.store.get_attempts(row['id']):
                checkpoint = attempt['checkpoint']
                remote_id = checkpoint.get('video_id') or checkpoint.get('remote_id')
                if remote_id:
                    evidence = {'job_id': row['id'], 'attempt_id': attempt['id'],
                                'remote_id': remote_id, 'source': 'checkpoint'}
                    reject_secrets(evidence)
                    if evidence not in self.data:
                        self.data.append(evidence)

    def __exit__(self, kind, exc, traceback):
        code = 0 if exc is None else (exc.exit_code or 0) if isinstance(exc, typer.Exit) else 130 if isinstance(exc, KeyboardInterrupt) else 1
        try:
            if self.remote is None:
                if exc is not None and code:
                    known = next((row['next_action'] for row in reversed(self.data)
                                  if 'next_action' in row), None)
                    self.errors.append(known if isinstance(exc, typer.Exit) and known else
                        error_for_code('operation_failed') if isinstance(exc, typer.Exit) else
                        error_for_code('operation_uncertain') if self.started else
                        error_document(exc, read_only=self.command in READ_ONLY))
                if self.store is not None:
                    try:
                        self.snapshot()
                        self.jobs_complete = True
                    except Exception:
                        self.errors.append(error_for_code('reporting_incomplete'))
                        code = code or 1
                payload = {'version': 1, 'kind': 'native-schedule', 'command': self.command,
                           'brand': self.brand, 'status': 'ok' if code == 0 else 'error',
                           'exit_code': code, 'data': self.data, 'jobs': self.jobs,
                           'jobs_complete': self.jobs_complete, 'errors': self.errors}
            else:
                payload = self.remote
                code = payload['exit_code']
        finally:
            active_report.reset(self.token)
            self.redirect.__exit__(kind, exc, traceback)
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        if code:
            raise typer.Exit(code) from None
        return exc is not None


def bind_store(store):
    report = active_report.get()
    if report is not None:
        report.bind(store)


def capture(value):
    report = active_report.get()
    if report is not None:
        reject_secrets(value)
        report.data.append(with_action(value))


def begin_submission():
    report = active_report.get()
    if report is not None:
        report.started = True
