"""Fictional jobs exercise real file ownership and conservative launchd evidence."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

import socialctl.cli as cli
import socialctl.scheduler_launchd as service
from socialctl.brands import crear_brand
from socialctl.executor import heartbeat
from socialctl.scheduler import ScheduleStore


class Launchd:
    """Only the OS boundary is fake; saved plists and CLI behavior are real."""
    def __init__(self, monkeypatch, tmp_path):
        self.directory = tmp_path / 'home/Library/LaunchAgents'
        self.jobs = {}
        self.disabled = {}
        self.fields = {}
        self.calls = []
        self.domain = f'gui/{os.getuid()}'
        self.available = True
        monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
        monkeypatch.setattr(sys, 'platform', 'darwin')
        monkeypatch.setattr(service.shutil, 'which', lambda tool: '/fictional/' + tool)
        monkeypatch.setattr(service.subprocess, 'run', self.run)

    def output(self, name):
        path, payload = self.jobs[name]
        fields = {
            'path': str(path), 'type': 'LaunchAgent', 'state': 'not running',
            'program': payload['ProgramArguments'][0],
            'working directory': payload['WorkingDirectory'],
            'stdout path': payload['StandardOutPath'],
            'stderr path': payload['StandardErrorPath'],
            'run interval': '60 seconds', 'last exit code': '0',
        }
        fields.update(self.fields)
        lines = [self.domain + '/' + name + ' = {']
        lines += ['\t' + key + ' = ' + value for key, value in fields.items() if value is not None]
        lines += ['\targuments = {'] + ['\t\t' + arg for arg in payload['ProgramArguments']] + ['\t}']
        return '\n'.join(lines + ['}'])

    def run(self, argv, **kwargs):
        self.calls.append(argv[1:])
        assert argv[0] == '/fictional/launchctl'
        args = argv[1:]
        code, out, err = 0, '', ''
        if not self.available:
            code, err = 1, 'private diagnostic must not escape'
        elif args == ['print', self.domain]:
            out = self.domain + ' = {\n}'
        elif args[:1] == ['print']:
            name = args[1].removeprefix(self.domain + '/')
            if name in self.jobs:
                out = self.output(name)
            else:
                code, err = 113, 'Could not find service "' + name + '" in domain for user gui: 501'
        elif args == ['print-disabled', self.domain]:
            out = 'disabled services = {\n' + ''.join(
                '\t"' + name + '" => ' + str(disabled).lower() + '\n'
                for name, disabled in self.disabled.items()) + '}'
        elif args[:1] == ['enable']:
            self.disabled[args[1].removeprefix(self.domain + '/')] = False
        elif args[:1] == ['bootstrap']:
            path = Path(args[2]); payload = plistlib.loads(path.read_bytes())
            if payload['Label'] in self.jobs:
                code = 5
            else:
                self.jobs[payload['Label']] = (path, payload)
        elif args[:1] == ['bootout']:
            name = args[1].removeprefix(self.domain + '/')
            if name in self.jobs: del self.jobs[name]
            else: code = 113
        else:
            pytest.fail('Unexpected launchctl arguments: ' + repr(args))
        return subprocess.CompletedProcess(argv, code, out, err)


def invoke(command, root):
    return CliRunner().invoke(cli.app, [command, '--brand', 'Example', '--root', str(root)] +
        (['--json'] if command == 'schedule-doctor' else []))


def installed(tmp_path, monkeypatch):
    manager = Launchd(monkeypatch, tmp_path)
    root = tmp_path / 'workspace'
    brand = crear_brand(root, 'Example')
    result = invoke('schedule-install', root)
    assert result.exit_code == 0, result.output
    heartbeat(ScheduleStore(brand.raiz), 'finished', datetime.now(timezone.utc))
    return manager, root, brand


def test_same_brand_workspaces_install_distinct_jobs_and_uninstall_only_selected(tmp_path, monkeypatch):
    manager, root, brand = installed(tmp_path, monkeypatch)
    first = set(manager.jobs)
    other = tmp_path / 'other'
    second_brand = crear_brand(other, 'Example')
    (brand.raiz / 'original.mp4').write_bytes(b'original')
    (second_brand.raiz / 'original.mp4').write_bytes(b'other')
    result = invoke('schedule-install', other)
    assert result.exit_code == 0, result.output
    assert len(manager.jobs) == 2
    assert first < set(manager.jobs)
    assert invoke('schedule-uninstall', other).exit_code == 0
    assert set(manager.jobs) == first
    assert (brand.raiz / 'original.mp4').read_bytes() == b'original'
    assert (second_brand.raiz / 'original.mp4').read_bytes() == b'other'
    assert (brand.raiz / '.socialctl/executor-state.json').exists()
    assert invoke('schedule-doctor', root).exit_code == 0


@pytest.mark.parametrize('legacy', ['file', 'registration'])
def test_legacy_brand_only_job_blocks_install_without_replacing_it(tmp_path, monkeypatch, legacy):
    manager = Launchd(monkeypatch, tmp_path)
    root = tmp_path / 'workspace'; crear_brand(root, 'Example')
    manager.directory.mkdir(parents=True)
    name = 'com.socialctl.example.run-due'
    path = manager.directory / (name + '.plist')
    if legacy == 'file':
        path.write_bytes(b'user-owned legacy configuration')
    else:
        manager.jobs[name] = (path, {'ProgramArguments': ['/other/python'], 'WorkingDirectory': '/other',
            'StandardOutPath': '/other/out', 'StandardErrorPath': '/other/err'})
    result = invoke('schedule-install', root)
    assert result.exit_code == 1, result.output
    assert 'legacy' in result.output.lower()
    assert not any(call[0] in {'bootout', 'bootstrap', 'enable'} for call in manager.calls)
    assert list(manager.directory.iterdir()) == ([path] if legacy == 'file' else [])
    if legacy == 'file':
        assert path.read_bytes() == b'user-owned legacy configuration'
    else:
        assert name in manager.jobs


@pytest.mark.parametrize('damage', ['changed', 'symlink', 'foreign', 'parent_symlink'])
def test_changed_or_unsafe_plist_is_neither_adopted_nor_removed(tmp_path, monkeypatch, damage):
    manager, root, _ = installed(tmp_path, monkeypatch)
    name = next(iter(manager.jobs)); path = manager.directory / (name + '.plist')
    if damage == 'changed':
        path.write_bytes(path.read_bytes() + b'<!-- changed -->')
    elif damage == 'symlink':
        target = tmp_path / 'foreign.plist'; path.rename(target); path.symlink_to(target)
    elif damage == 'parent_symlink':
        target = tmp_path / 'foreign-directory'; manager.directory.rename(target); manager.directory.symlink_to(target)
    else:
        monkeypatch.setattr(service.os, 'getuid', lambda: 999999)
    before = path.read_bytes()
    manager.calls.clear()
    assert invoke('schedule-doctor', root).exit_code == 1
    assert invoke('schedule-install', root).exit_code == 1
    assert invoke('schedule-uninstall', root).exit_code == 1
    assert path.read_bytes() == before
    assert name in manager.jobs
    assert not any(call[0] in {'bootout', 'bootstrap', 'enable'} for call in manager.calls)


@pytest.mark.parametrize('field,value', [
    ('path', '/foreign/job.plist'), ('program', '/foreign/python'),
    ('working directory', '/foreign'), ('run interval', '3600 seconds'),
    ('stdout path', '/foreign/out'), ('state', 'unknown'), ('program', None),
])
def test_loaded_mismatch_or_unknown_fields_never_authorize_install_or_removal(tmp_path, monkeypatch, field, value):
    manager, root, _ = installed(tmp_path, monkeypatch)
    manager.fields[field] = value
    manager.calls.clear()
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 1
    assert json.loads(result.stdout)['persistent_supervision'] == 'unverified'
    assert invoke('schedule-install', root).exit_code == 1
    assert invoke('schedule-uninstall', root).exit_code == 1
    assert not any(call[0] in {'bootout', 'bootstrap', 'enable'} for call in manager.calls)
    assert len(manager.jobs) == 1


@pytest.mark.parametrize('evidence', ['disabled', 'unknown_enabled', 'unregistered', 'failed', 'unknown_exit', 'signal'])
def test_current_supervisor_evidence_is_required_for_ready(tmp_path, monkeypatch, evidence):
    manager, root, _ = installed(tmp_path, monkeypatch)
    name = next(iter(manager.jobs))
    if evidence == 'disabled': manager.disabled[name] = True
    elif evidence == 'unknown_enabled': manager.disabled.clear()
    elif evidence == 'unregistered': manager.jobs.clear()
    elif evidence == 'failed': manager.fields['last exit code'] = '7'
    elif evidence == 'unknown_exit': manager.fields['last exit code'] = None
    else: manager.fields['last terminating signal'] = '9'
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report['status'] == 'attention_required'
    assert report['persistent_supervision'] == 'unverified'
    if evidence in {'failed', 'signal'}:
        assert report['error']['code'] == 'supervisor_service_failed'


@pytest.mark.parametrize('state,age', [('error', 0), ('finished', 3600), ('missing', 0)])
def test_healthy_supervisor_cannot_hide_missing_stale_or_error_heartbeat(tmp_path, monkeypatch, state, age):
    manager, root, brand = installed(tmp_path, monkeypatch)
    if state == 'missing': (brand.raiz / '.socialctl/executor-state.json').unlink()
    else: heartbeat(ScheduleStore(brand.raiz), state, datetime.now(timezone.utc) - timedelta(seconds=age))
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report['status'] == 'attention_required'
    assert report['error']['code'] in {'executor_error', 'executor_unverified'}


def test_healthy_registration_and_execution_are_verified_without_guaranteeing_delivery(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report['status'] == 'ready'
    assert report['persistent_supervision'] == 'verified_now'
    assert report['supervisor']['kind'] == 'launchd_user'
    assert report['supervisor']['last_exit_code'] == 0
    assert report['supervisor']['survives_logout'] is False
    assert report['unattended_delivery_guaranteed'] is False
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in manager.directory.iterdir())


def test_uninstall_distinguishes_absence_from_unavailable_manager(tmp_path, monkeypatch):
    manager = Launchd(monkeypatch, tmp_path)
    root = tmp_path / 'workspace'; crear_brand(root, 'Example')
    result = invoke('schedule-uninstall', root)
    assert result.exit_code == 0
    assert 'No macOS scheduler' in result.output
    assert not manager.directory.exists()
    manager.available = False
    assert invoke('schedule-uninstall', root).exit_code == 1


@pytest.mark.parametrize('damage', ['unknown_signal', 'malformed_disabled', 'duplicate_field', 'nested_spoof', 'arguments'])
def test_ambiguous_launchctl_output_fails_closed(tmp_path, monkeypatch, damage):
    manager, root, _ = installed(tmp_path, monkeypatch)
    original = manager.output
    if damage == 'unknown_signal':
        manager.fields['last terminating signal'] = 'SIGKILL'
    elif damage == 'malformed_disabled':
        original_run = manager.run
        def malformed(argv, **kwargs):
            result = original_run(argv, **kwargs)
            if argv[1] == 'print-disabled':
                result.stdout = result.stdout.replace('false', 'false\n\tunknown format')
            return result
        monkeypatch.setattr(service.subprocess, 'run', malformed)
    elif damage == 'duplicate_field':
        manager.output = lambda name: original(name).replace('\tprogram = ', '\tprogram = /foreign\n\tprogram = ')
    elif damage == 'nested_spoof':
        manager.fields['program'] = None
        manager.output = lambda name: original(name).replace('\targuments = {',
            '\tenvironment = {\n\t\tprogram = ' + str(sys.executable) + '\n\t}\n\targuments = {')
    else:
        manager.output = lambda name: original(name).replace('\t\trun-due\n', '\t\tother-command\n')
    assert invoke('schedule-doctor', root).exit_code == 1


def test_install_race_cannot_clobber_a_file_created_after_preflight(tmp_path, monkeypatch):
    manager = Launchd(monkeypatch, tmp_path)
    root = tmp_path / 'workspace'; crear_brand(root, 'Example')
    original = manager.run
    raced = []
    def race(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[1] == 'print' and argv[-1].endswith('.run-due') and 'com.socialcli.' in argv[-1]:
            name = argv[-1].rsplit('/', 1)[-1]
            path = manager.directory / (name + '.plist')
            manager.directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'concurrently created user configuration')
            raced.append(path)
        return result
    monkeypatch.setattr(service.subprocess, 'run', race)
    result = invoke('schedule-install', root)
    assert result.exit_code == 1
    assert raced[0].read_bytes() == b'concurrently created user configuration'
    assert not manager.jobs
    assert not any(call[0] in {'enable', 'bootstrap', 'bootout'} for call in manager.calls)


@pytest.mark.parametrize('operation', ['bootstrap', 'bootout'])
def test_failed_mutation_keeps_configuration_and_never_claims_success(tmp_path, monkeypatch, operation):
    manager = Launchd(monkeypatch, tmp_path)
    root = tmp_path / 'workspace'; crear_brand(root, 'Example')
    if operation == 'bootout':
        assert invoke('schedule-install', root).exit_code == 0
    original = manager.run
    def fail(argv, **kwargs):
        if argv[1] == operation:
            return subprocess.CompletedProcess(argv, 1, '', 'private OS diagnostic')
        return original(argv, **kwargs)
    monkeypatch.setattr(service.subprocess, 'run', fail)
    result = invoke('schedule-install' if operation == 'bootstrap' else 'schedule-uninstall', root)
    assert result.exit_code == 1
    assert 'private OS diagnostic' not in result.output
    assert len(list(manager.directory.glob('*.plist'))) == 1
    assert bool(manager.jobs) == (operation == 'bootout')


def test_uninstall_refuses_a_cached_job_when_its_ownership_file_is_missing(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    path = next(manager.directory.glob('*.plist')); path.unlink()
    assert invoke('schedule-uninstall', root).exit_code == 1
    assert len(manager.jobs) == 1


def test_uninstall_cleans_an_owned_unregistered_file_and_preserves_legacy_job(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    manager.jobs.clear()
    legacy = manager.directory / 'com.socialctl.example.run-due.plist'
    legacy.write_bytes(b'legacy preserved')
    result = invoke('schedule-uninstall', root)
    assert result.exit_code == 0, result.output
    assert list(manager.directory.iterdir()) == [legacy]
    assert legacy.read_bytes() == b'legacy preserved'


def test_install_is_idempotent_for_exact_owned_job_without_booting_it_out(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    before = next(manager.directory.iterdir()).stat().st_ino
    manager.calls.clear()
    assert invoke('schedule-install', root).exit_code == 0
    assert next(manager.directory.iterdir()).stat().st_ino == before
    assert not any(call[0] in {'bootstrap', 'bootout'} for call in manager.calls)


def test_unknown_signal_cannot_hide_a_known_nonzero_exit(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    manager.fields.update({'last exit code': '7', 'last terminating signal': 'unknown signal format'})
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 1
    assert json.loads(result.stdout)['error']['code'] == 'supervisor_service_failed'


def test_existing_configuration_changed_during_probe_is_not_bootstrapped(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    manager.jobs.clear()
    path = next(manager.directory.glob('*.plist'))
    original = manager.run
    def change(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[1] == 'print' and 'com.socialcli.' in argv[-1]:
            payload = plistlib.loads(path.read_bytes())
            payload['ProgramArguments'] = ['/foreign/python', 'other-command']
            path.write_bytes(plistlib.dumps(payload))
        return result
    monkeypatch.setattr(service.subprocess, 'run', change)
    manager.calls.clear()
    assert invoke('schedule-install', root).exit_code == 1
    assert not manager.jobs
    assert not any(call[0] in {'enable', 'bootstrap', 'bootout'} for call in manager.calls)
    assert plistlib.loads(path.read_bytes())['ProgramArguments'] == ['/foreign/python', 'other-command']


def test_remote_doctor_keeps_diagnostic_without_querying_local_supervisor(tmp_path, monkeypatch):
    manager = Launchd(monkeypatch, tmp_path)
    root = tmp_path / 'workspace'; brand = crear_brand(root, 'Example')
    directory = brand.raiz / '.socialctl'; directory.mkdir(exist_ok=True)
    (directory / 'remote-executor.json').write_text('{}')
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report['error']['code'] == 'remote_executor_unverified'
    assert report['persistent_supervision'] == 'unverified'
    assert manager.calls == []


def test_configuration_changed_during_probe_is_not_booted_out(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    path = next(manager.directory.glob('*.plist'))
    original = manager.run
    def change(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[1] == 'print' and 'com.socialcli.' in argv[-1]:
            path.write_bytes(path.read_bytes() + b'<!-- changed during probe -->')
        return result
    monkeypatch.setattr(service.subprocess, 'run', change)
    manager.calls.clear()
    assert invoke('schedule-uninstall', root).exit_code == 1
    assert len(manager.jobs) == 1
    assert not any(call[0] == 'bootout' for call in manager.calls)
    assert path.exists()


def _native_enabled_words(manager, monkeypatch):
    """macOS observed print-disabled spelling, not a new supervisor operation."""
    original = manager.run
    def observed_format(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[1] == 'print-disabled':
            result.stdout = result.stdout.replace('=> false', '=> enabled').replace('=> true', '=> disabled')
        return result
    monkeypatch.setattr(service.subprocess, 'run', observed_format)


def test_observed_enabled_spelling_allows_verified_install_and_ready_doctor(tmp_path, monkeypatch):
    manager = Launchd(monkeypatch, tmp_path)
    _native_enabled_words(manager, monkeypatch)
    root = tmp_path / 'workspace'; brand = crear_brand(root, 'Example')
    result = invoke('schedule-install', root)
    assert result.exit_code == 0, result.output
    heartbeat(ScheduleStore(brand.raiz), 'finished', datetime.now(timezone.utc))
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report['status'] == 'ready'
    assert report['supervisor']['enabled'] is True
    assert report['unattended_delivery_guaranteed'] is False


def test_observed_disabled_spelling_reports_disabled_without_certifying_ready(tmp_path, monkeypatch):
    manager, root, _ = installed(tmp_path, monkeypatch)
    manager.disabled[next(iter(manager.jobs))] = True
    _native_enabled_words(manager, monkeypatch)
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report['supervisor']['enabled'] is False
    assert report['persistent_supervision'] == 'unverified'


@pytest.mark.parametrize('entries', [
    ['enabled', 'enabled'], ['enabled', 'disabled'], ['enabled', 'false'],
    ['unknown'], ['enabled extra'], ['disabled extra'],
])
def test_observed_words_do_not_admit_duplicate_or_unknown_states(tmp_path, monkeypatch, entries):
    manager, root, _ = installed(tmp_path, monkeypatch)
    name = next(iter(manager.jobs))
    original = manager.run
    def ambiguous(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[1] == 'print-disabled':
            result.stdout = 'disabled services = {\n' + ''.join(
                '\t"' + name + '" => ' + token + '\n' for token in entries) + '}'
        return result
    monkeypatch.setattr(service.subprocess, 'run', ambiguous)
    result = invoke('schedule-doctor', root)
    assert result.exit_code == 1
    assert json.loads(result.stdout)['supervisor']['enabled'] is None
