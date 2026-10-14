"""Customer-owned Linux user timers. No shell, media transfer or policy changes."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


class SupervisorError(ValueError):
    pass


def unit_name(brand: str, root: Path) -> str:
    identity = str(Path(root).resolve()) + '\0' + brand
    return 'socialcli-' + hashlib.sha256(identity.encode()).hexdigest()[:20] + '-run-due'


def _quote(value, *, command=False):
    value = str(value)
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SupervisorError('Scheduler paths and names must not contain control characters')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if command:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def render(brand: str, root: Path, *, executable=None) -> tuple[str, str]:
    root = Path(root).resolve()
    executable = Path(executable or sys.executable).absolute()
    name = unit_name(brand,root)
    argv = [str(executable), '-m', 'socialctl', 'run-due', '--brand', brand, '--root', str(root)]
    command = ' '.join(_quote(arg,command=True) for arg in argv)
    # WorkingDirectory consumes the raw value, unlike ExecStart's word parser.
    # Reject suffixes the unit-file parser would strip or join to the next line.
    directory = str(root)
    if directory != directory.rstrip() or directory.endswith('\\'):
        raise SupervisorError('Scheduler workspace must not end with whitespace or a backslash')
    directory = directory.replace('%','%%')
    service = ('# Managed by SocialCli\n[Unit]\nDescription=SocialCli local queue executor\n\n'
        '[Service]\nType=oneshot\nWorkingDirectory=' + directory + '\nExecStart=' + command +
        '\nTimeoutStartSec=0\nUMask=0077\n')
    timer = ('# Managed by SocialCli\n[Unit]\nDescription=SocialCli local queue timer\n\n'
        '[Timer]\nOnActiveSec=5s\nOnUnitInactiveSec=60s\nAccuracySec=1s\nUnit=' + name +
        '.service\n\n[Install]\nWantedBy=timers.target\n')
    return service,timer


def _directory():
    configured = os.environ.get('XDG_CONFIG_HOME','').strip()
    base = Path(configured) if configured else Path.home()/'.config'
    if not base.is_absolute():
        raise SupervisorError('XDG_CONFIG_HOME must be an absolute directory')
    return base/'systemd'/'user'


def _run(tool, args, *, check=True):
    executable = shutil.which(tool)
    if not executable:
        raise SupervisorError(f'{tool} is unavailable; no working Linux user supervisor was verified')
    try:
        result = subprocess.run([executable,*args],capture_output=True,text=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise SupervisorError('Could not contact the Linux user supervisor; inspect the user service manager') from exc
    if check and result.returncode:
        raise SupervisorError('Linux user supervisor operation failed; inspect systemctl --user status and journalctl --user')
    return result


def _atomic(path, text):
    if path.is_symlink():
        raise SupervisorError('Refusing to replace a symlinked scheduler unit')
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.'+path.name,delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(0o600)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def inspect(brand: str, root: Path) -> dict:
    name = unit_name(brand,root)
    result = {'kind':'systemd_user','unit':name, 'configuration_matches':False,
              'enabled':False,'active':False,'survives_logout':None,'service_failed':None}
    try:
        directory = _directory()
        expected = render(brand,root)
        paths = [directory/(name+suffix) for suffix in ('.service','.timer')]
        if any(path.is_symlink() or not path.is_file() or path.read_text()!=text
               for path,text in zip(paths,expected)):
            return result
        for path in paths:
            observed = _run('systemctl',['--user','show','--property=NeedDaemonReload,DropInPaths,FragmentPath,ActiveState,Result',path.name])
            properties = dict(line.split('=',1) for line in observed.stdout.splitlines() if '=' in line)
            if (properties.get('NeedDaemonReload')!='no' or properties.get('DropInPaths')!=''
                    or properties.get('FragmentPath')!=str(path)):
                return result
            if path.suffix == '.service':
                result['service_failed'] = (properties.get('Result') != 'success'
                    or properties.get('ActiveState') not in {'active','inactive','activating'})
        result['configuration_matches']=True
        enabled = _run('systemctl',['--user','is-enabled',name+'.timer'],check=False)
        result['enabled']=enabled.returncode==0 and enabled.stdout.strip()=='enabled'
        active = _run('systemctl',['--user','is-active',name+'.timer'],check=False)
        result['active']=active.returncode==0 and active.stdout.strip()=='active'
        linger = _run('loginctl',['show-user',str(os.getuid()),'--property=Linger','--value'],check=False)
        if linger.returncode==0 and linger.stdout.strip() in {'yes','no'}:
            result['survives_logout']=linger.stdout.strip()=='yes'
    except (SupervisorError,OSError,ValueError):
        # Fixed fields only; do not echo manager output or environment variables.
        pass
    return result


def install(brand: str, root: Path) -> dict:
    expected = render(brand,root)  # Validate paths before effects.
    _run('systemctl',['--user','show','--property=Version','--value'])
    directory = _directory()
    directory.mkdir(parents=True,exist_ok=True)
    name = unit_name(brand,root)
    for suffix,text in zip(('.service','.timer'),expected):
        _atomic(directory/(name+suffix),text)
    _run('systemctl',['--user','daemon-reload'])
    _run('systemctl',['--user','enable','--now',name+'.timer'])
    report = inspect(brand,root)
    if not all(report[key] for key in ('configuration_matches','enabled','active')):
        raise SupervisorError('Scheduler units were saved, but an enabled active timer was not verified; run schedule-doctor')
    return report


def uninstall(brand: str, root: Path) -> bool:
    directory = _directory()
    name = unit_name(brand,root)
    paths = [directory/(name+suffix) for suffix in ('.timer','.service')]
    has_files = any(path.exists() or path.is_symlink() for path in paths)
    if any(path.is_symlink() for path in paths):
        raise SupervisorError('Refusing to remove a symlinked scheduler unit')
    # A manager can retain an active timer after its files disappear. Missing
    # files alone must never be reported as successful absence.
    _run('systemctl',['--user','show','--property=Version','--value'])
    observed = _run('systemctl',['--user','show','--property=LoadState,ActiveState,UnitFileState',name+'.timer'],check=False)
    properties = dict(line.split('=',1) for line in observed.stdout.splitlines() if '=' in line)
    absent = (properties.get('LoadState') == 'not-found'
              and properties.get('ActiveState') == 'inactive'
              and properties.get('UnitFileState') in {'','disabled','not-found'})
    if not has_files and absent:
        return False
    if properties.get('ActiveState') != 'inactive':
        # Stop first: disable --now can fail before its stop phase when the
        # on-disk configuration is damaged. Never stop the paired service.
        _run('systemctl',['--user','stop',name+'.timer'])
    _run('systemctl',['--user','disable','--now',name+'.timer'])
    # An in-flight approved write may finish. Killing it would create uncertainty.
    for path in paths:
        path.unlink(missing_ok=True)
    _run('systemctl',['--user','daemon-reload'])
    return True
