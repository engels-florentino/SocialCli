"""Workspace-owned macOS queue agents; unknown supervisor evidence fails closed."""
import hashlib
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from socialctl.scheduler_systemd import SupervisorError


def unit_name(brand: str, root: Path) -> str:
    identity = str(Path(root).resolve()) + '\0' + brand
    return 'com.socialcli.' + hashlib.sha256(identity.encode()).hexdigest()[:20] + '.run-due'


def _plan(brand, root):
    from socialctl.rutas import validar_componente_de_ruta
    root = Path(root).resolve()
    selected = validar_componente_de_ruta(root, brand, SupervisorError, 'brand name')
    if not root.is_dir() or not (selected / 'brand.md').is_file():
        raise SupervisorError('Select an existing brand and its explicit workspace root.')
    # Preserve a venv interpreter symlink, rather than resolving to base Python.
    argv = [str(Path(sys.executable).absolute()), '-m', 'socialctl', 'run-due',
            '--brand', brand, '--root', str(root)]
    if any(any(ord(c) < 32 or ord(c) == 127 for c in arg) for arg in argv):
        raise SupervisorError('Scheduler paths and names must not contain control characters.')
    name = unit_name(brand, root)
    directory = Path.home() / 'Library/LaunchAgents'
    log_dir = selected / '.socialctl'
    payload = {'Label': name, 'ProgramArguments': argv, 'WorkingDirectory': str(root),
               'StartInterval': 60, 'RunAtLoad': True, 'Umask': 0o077,
               'StandardOutPath': str(log_dir / 'run-due.log'),
               'StandardErrorPath': str(log_dir / 'run-due.error.log'), 'ProcessType': 'Background'}
    return directory / (name + '.plist'), payload, plistlib.dumps(payload, sort_keys=False)


def _run(args, *, check=True):
    executable = shutil.which('launchctl')
    if not executable:
        raise SupervisorError('No launchd supervisor is available. Nothing was verified or changed.')
    try:
        result = subprocess.run([executable, *args], capture_output=True, text=True,
                                timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise SupervisorError('Could not contact the macOS user supervisor; inspect launchctl before retrying.') from None
    if check and result.returncode:
        raise SupervisorError('The macOS user supervisor rejected the operation; inspect launchctl before retrying.')
    return result


def _domain():
    domain = f'gui/{os.getuid()}'
    _run(['print', domain])
    return domain


def _job(domain, name):
    result = _run(['print', domain + '/' + name], check=False)
    if result.returncode == 0:
        return result.stdout
    # A successful domain probe plus the specific missing-service diagnostic is
    # necessary: arbitrary command failures are not evidence of absence.
    if (result.returncode == 113 and
            f'Could not find service "{name}" in domain' in result.stderr):
        return None
    raise SupervisorError('Could not verify whether the macOS scheduler is registered; nothing was replaced or removed.')


def _safe_parents(path):
    if not path.is_absolute() or any(parent.is_symlink() for parent in path.parents):
        raise SupervisorError('Refusing a symlinked supervisor configuration directory.')


def _configuration(path, expected):
    _safe_parents(path)
    if not path.exists() and not path.is_symlink():
        return False
    # Read without following the final link and validate the opened inode.
    if path.is_symlink() or not path.is_file():
        raise SupervisorError('Existing scheduler configuration is unsafe; review it manually.')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or stream.read() != expected):
            raise SupervisorError('Existing scheduler configuration differs or is not private and owned; nothing was replaced or removed.')
    return True


def _mkdir(directory):
    _safe_parents(directory / '.scheduler-check')
    missing = []
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def _save_new(path, content):
    # Link a complete private file into place without ever replacing a target,
    # including one created between the preflight check and this operation.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def _fields(output, target):
    """Read only unambiguous direct job fields, never nested environment fields.

    Apple explicitly documents `print` as diagnostic output, not an API. A
    changed/missing format therefore yields unverified evidence, not success.
    """
    lines = output.strip().splitlines()
    if not lines or lines[0].strip() != target + ' = {' or lines[-1].strip() != '}':
        return {}, []
    fields, argv, depth, arguments = {}, [], 1, False
    for line in lines[1:-1]:
        text = line.strip()
        if text == '}':
            depth -= 1
            if depth < 1:
                return {}, []
            if depth == 1:
                arguments = False
        elif text.endswith(' = {'):
            if depth == 1 and text == 'arguments = {':
                if 'arguments' in fields:
                    return {}, []
                fields['arguments'] = True
                arguments = True
            depth += 1
        elif depth == 2 and arguments:
            if not text:
                return {}, []
            argv.append(text)
        elif depth == 1 and ' = ' in text:
            key, value = text.split(' = ', 1)
            if key in fields:
                return {}, []
            fields[key] = value
    return (fields, argv) if depth == 1 else ({}, [])


def _loaded_matches(output, domain, path, payload):
    fields, argv = _fields(output, domain + '/' + payload['Label'])
    expected = {'path': str(path), 'type': 'LaunchAgent',
                'program': payload['ProgramArguments'][0],
                'working directory': payload['WorkingDirectory'],
                'stdout path': payload['StandardOutPath'], 'stderr path': payload['StandardErrorPath'],
                'run interval': '60 seconds'}
    return (all(fields.get(key) == value for key, value in expected.items())
            and argv == payload['ProgramArguments']
            and fields.get('state') in {'running', 'not running'}), fields


def _enabled(domain, name):
    result = _run(['print-disabled', domain], check=False)
    if result.returncode:
        return None
    lines = result.stdout.strip().splitlines()
    if not lines or lines[0].strip() != 'disabled services = {' or lines[-1].strip() != '}':
        return None
    # Current macOS prints enabled/disabled; retain the boolean spelling used
    # by earlier diagnostic fixtures without treating unknowns as on.
    states = r'(true|false|enabled|disabled)'
    entries = []
    for line in lines[1:-1]:
        if not re.fullmatch(r'\s*"[^"\n]+"\s*=>\s*' + states + r'\s*', line):
            return None
        match = re.fullmatch(r'\s*"' + re.escape(name) + r'"\s*=>\s*' + states + r'\s*', line)
        if match:
            entries.append(match.group(1))
        elif '"' + name + '"' in line:
            return None
    return entries[0] in {'false', 'enabled'} if len(entries) == 1 else None


def _legacy_present(brand, path, domain):
    name = f'com.socialctl.{brand.lower()}.run-due'
    legacy_path = path.parent / (name + '.plist')
    return legacy_path.exists() or legacy_path.is_symlink() or _job(domain, name) is not None


def inspect(brand: str, root: Path) -> dict:
    report = {'kind': 'launchd_user', 'unit': unit_name(brand, root),
              'configuration_matches': False, 'registered': False, 'enabled': None,
              'active': False, 'survives_logout': False, 'service_failed': None,
              'last_exit_code': None, 'last_terminating_signal': None,
              'reason': 'configuration_missing'}
    try:
        path, payload, content = _plan(brand, root)
        if not _configuration(path, content):
            return report
        domain = _domain()
        if _legacy_present(brand, path, domain):
            report['reason'] = 'legacy_job_present'
            return report
        observed = _job(domain, payload['Label'])
        report['registered'] = observed is not None
        if observed is None:
            report['reason'] = 'job_unregistered'
            return report
        matches, fields = _loaded_matches(observed, domain, path, payload)
        report['configuration_matches'] = matches
        if not matches:
            report['reason'] = 'loaded_configuration_unverified'
            return report
        report['active'] = True  # Registered interval task; a PID need not run between ticks.
        report['enabled'] = _enabled(domain, payload['Label'])
        code, signal = fields.get('last exit code', ''), fields.get('last terminating signal', '')
        if re.fullmatch(r'-?\d+', code):
            report['last_exit_code'] = int(code)
            report['service_failed'] = int(code) != 0
        if re.fullmatch(r'\d+', signal):
            report['last_terminating_signal'] = int(signal)
            if int(signal) != 0:
                report['service_failed'] = True
        elif 'last terminating signal' in fields and report['service_failed'] is not True:
            # Unknown signal text must not inherit success from an older exit.
            report['service_failed'] = None
        report['reason'] = ('last_execution_failed' if report['service_failed'] is True
                            else 'verified_now' if report['enabled'] is True and report['service_failed'] is False
                            else 'execution_or_enabled_state_unverified')
    except SupervisorError:
        report['reason'] = 'configuration_or_supervisor_unverified'
    except (OSError, ValueError):
        report['reason'] = 'configuration_unreadable'
    return report


def install(brand: str, root: Path) -> dict:
    path, payload, content = _plan(brand, root)
    saved = _configuration(path, content)
    domain = _domain()
    if _legacy_present(brand, path, domain):
        raise SupervisorError('A legacy brand-only scheduler exists. Review and explicitly migrate it first; nothing was replaced. See docs/local-scheduler.md.')
    observed = _job(domain, payload['Label'])
    if observed is not None and (not saved or not _loaded_matches(observed, domain, path, payload)[0]):
        raise SupervisorError('An existing launchd job differs or is unverified; nothing was replaced.')
    # launchd opens the log targets. Do not direct it through caller-supplied links.
    log_dir = Path(payload['StandardOutPath']).parent
    _safe_parents(log_dir / '.scheduler-check')
    for key in ('StandardOutPath', 'StandardErrorPath'):
        log = Path(payload[key])
        if log.is_symlink() or (log.exists() and (not log.is_file() or log.stat().st_uid != os.getuid())):
            raise SupervisorError('The scheduler log destination is unsafe; nothing was installed.')
    _mkdir(path.parent)
    _mkdir(log_dir)
    if not saved:
        _save_new(path, content)
    if not _configuration(path, content):
        raise SupervisorError('The scheduler configuration disappeared during inspection; nothing was loaded.')
    _run(['enable', domain + '/' + payload['Label']])
    if observed is None:
        _run(['bootstrap', domain, str(path)])
    report = inspect(brand, root)
    if (not all(report[key] is True for key in ('configuration_matches', 'registered', 'enabled', 'active'))
            or report['service_failed'] is True):
        raise SupervisorError('The scheduler configuration was saved, but a working enabled launchd job was not verified; run schedule-doctor.')
    return report


def uninstall(brand: str, root: Path) -> bool:
    path, payload, content = _plan(brand, root)
    saved = _configuration(path, content)
    domain = _domain()
    observed = _job(domain, payload['Label'])
    if observed is None and not saved:
        return False
    if observed is not None:
        if not saved or not _loaded_matches(observed, domain, path, payload)[0]:
            raise SupervisorError('The registered scheduler differs or is unverified; nothing was removed.')
        if not _configuration(path, content):
            raise SupervisorError('The scheduler ownership file disappeared during inspection; nothing was removed.')
        _run(['bootout', domain + '/' + payload['Label']])
        if _job(domain, payload['Label']) is not None:
            raise SupervisorError('The scheduler is still registered; its configuration was preserved.')
    # Recheck before unlinking; never delete an edited/foreign file after bootout.
    if _configuration(path, content):
        path.unlink()
    return True
