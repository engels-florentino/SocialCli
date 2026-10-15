"""Opt-in daily local retention supervisor, separate from publication scheduling.

No shell or cleanup runs inside the installer. The user manager runs the existing
brand-scoped data-cleanup command later. Sleep, power-off, logout, removed Python
environments and exported copies remain outside a physical-erasure guarantee.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

from socialctl.scheduler_systemd import SupervisorError, _quote, _atomic, _directory

LIMITS = ('Cleanup requires this machine, the selected Python environment and the user '
          'supervisor to remain available. Offline or logged-out machines and exported '
          'third-party copies cannot be guaranteed erased on schedule. This task does '
          'not publish, reconnect, or remove customer-supplied originals.')


def unit_name(brand, root):
    identity=str(Path(root).resolve())+'\0'+brand
    return 'com.socialcli.'+hashlib.sha256(identity.encode()).hexdigest()[:20]+'.data-cleanup'


def _workspace(brand,root):
    from socialctl.rutas import validar_componente_de_ruta
    root=Path(root).resolve()
    _quote(root);_quote(brand)
    selected=validar_componente_de_ruta(root,brand,SupervisorError,'brand name')
    if not root.is_dir() or not selected.is_dir() or not (selected/'brand.md').is_file():
        raise SupervisorError('Select an existing brand and its explicit workspace root.')
    return root


def _argv(brand,root):
    # Do not resolve the interpreter symlink: that can bypass the installed venv.
    executable=Path(sys.executable).absolute()
    _quote(executable)
    if not executable.is_file() or not os.access(executable,os.X_OK):
        raise SupervisorError('The current Python interpreter must remain executable for scheduled cleanup.')
    return [str(executable),'-m','socialctl','data-cleanup','--brand',brand,'--root',str(root)]


def preview(brand,root):
    root=_workspace(brand,root);name=unit_name(brand,root);argv=_argv(brand,root)
    if sys.platform.startswith('linux'):
        kind='systemd_user';directory=_directory()
        working=str(root)
        if working!=working.rstrip() or working.endswith('\\'):
            raise SupervisorError('The workspace must not end with whitespace or a backslash.')
        service=('# Managed by SocialCli data cleanup\n[Unit]\nDescription=SocialCli local data retention\n\n'
                 '[Service]\nType=oneshot\nWorkingDirectory='+working.replace('%','%%')+
                 '\nExecStart='+' '.join(_quote(arg,command=True) for arg in argv)+
                 '\nUMask=0077\nStandardOutput=null\nStandardError=null\nTimeoutStartSec=1h\n')
        timer=('# Managed by SocialCli data cleanup\n[Unit]\nDescription=SocialCli daily data retention\n\n'
               '[Timer]\nOnCalendar=daily\nPersistent=true\nAccuracySec=1s\nUnit='+name+
               '.service\n\n[Install]\nWantedBy=timers.target\n')
        files=[{'path':str(directory/(name+suffix)),'content':content}
               for suffix,content in (('.service',service),('.timer',timer))]
        actions=[['systemctl','--user','daemon-reload'],['systemctl','--user','enable','--now',name+'.timer']]
    elif sys.platform=='darwin':
        kind='launchd_user';directory=Path.home()/'Library'/'LaunchAgents'
        path=directory/(name+'.plist')
        payload={'Label':name,'ProgramArguments':argv,'WorkingDirectory':str(root),
                 'StartCalendarInterval':{'Hour':12,'Minute':0},'RunAtLoad':False,
                 'Umask':0o077,'ProcessType':'Background'}
        files=[{'path':str(path),'content':plistlib.dumps(payload,sort_keys=False).decode()}]
        actions=[['launchctl','enable',f'gui/{os.getuid()}/{name}'],
                 ['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)]]
    else:
        raise SupervisorError('Daily cleanup installation supports Linux user systemd and macOS launchd only.')
    return {'version':1,'status':'preview','kind':kind,'unit':name,'brand':brand,'root':str(root),
            'argv':argv,'files':files,'actions':actions,'interval':'daily',
            'physical_erasure_guaranteed':False,'limits':LIMITS}


def _run(tool,args,*,check=True):
    executable=shutil.which(tool)
    if not executable:raise SupervisorError('The user supervisor is unavailable; no active cleanup task was verified.')
    try:
        result=subprocess.run([executable,*args],capture_output=True,text=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise SupervisorError('Could not contact the user supervisor; inspect its status before retrying.') from None
    if check and result.returncode:
        raise SupervisorError('The user supervisor rejected the operation; inspect its status before retrying.')
    return result


def _private_regular(path):
    return (not path.is_symlink() and not any(parent.is_symlink() for parent in path.parents) and path.is_file() and path.stat().st_uid==os.getuid()
            and path.stat().st_mode&0o777==0o600)


def _check_destinations(files):
    # Never replace an existing unrelated/modified file, even at our hashed name.
    for item in files:
        path=Path(item['path'])
        if any(parent.is_symlink() for parent in path.parents):
            raise SupervisorError('Refusing a symlinked supervisor configuration directory.')
        if path.is_symlink() or (path.exists() and (not path.is_file()
                or path.stat().st_uid!=os.getuid() or path.read_text()!=item['content'])):
            raise SupervisorError('Existing supervisor configuration differs or is unsafe. Review it manually; nothing was replaced.')


def _mkdir_private(directory):
    missing=[];current=directory
    while not current.exists():missing.append(current);current=current.parent
    for path in reversed(missing):path.mkdir(mode=0o700)


def _properties(output):
    return dict(line.split('=',1) for line in output.splitlines() if '=' in line)


def _launchd_matches(output,plan):
    observed={}
    for line in output.splitlines():
        if ' = ' in line:
            key,value=line.strip().split(' = ',1);observed[key]=value
    arguments=re.search(r'^\s*arguments = \{\s*\n(.*?)^\s*\}',output,re.M|re.S)
    argv=[line.strip() for line in arguments.group(1).splitlines() if line.strip()] if arguments else []
    return observed.get('path')==plan['files'][0]['path'] and observed.get('program')==plan['argv'][0] and argv==plan['argv']


def inspect(brand,root):
    plan=preview(brand,root)
    report={key:plan[key] for key in ('version','kind','unit','brand','root','interval','physical_erasure_guaranteed','limits')}
    report.update(status='attention_required',reason='configuration_missing',configuration_matches=False,
                  registered=False,enabled=False,active=False,last_exit_code=None,survives_logout=None)
    try:
        matches=all(_private_regular(Path(item['path'])) and Path(item['path']).read_text()==item['content'] for item in plan['files'])
        if not matches:
            if any(Path(item['path']).exists() or Path(item['path']).is_symlink() for item in plan['files']):
                report['reason']='configuration_mismatch'
            return report
        report['configuration_matches']=True
        name=plan['unit']
        if plan['kind']=='systemd_user':
            for item in plan['files']:
                path=Path(item['path'])
                observed=_run('systemctl',['--user','show','--property=NeedDaemonReload,DropInPaths,FragmentPath,ActiveState,Result,ExecMainStatus',path.name])
                props=_properties(observed.stdout)
                if props.get('NeedDaemonReload')!='no' or props.get('DropInPaths')!='' or props.get('FragmentPath')!=str(path):
                    report.update(configuration_matches=False,reason='loaded_configuration_mismatch');return report
                if path.suffix=='.service':
                    code=props.get('ExecMainStatus','')
                    report['last_exit_code']=int(code) if code.isdigit() else None
                    if props.get('Result') not in {'','success'}:report['last_exit_code']=report['last_exit_code'] or 1
            report['registered']=True
            enabled=_run('systemctl',['--user','is-enabled',name+'.timer'],check=False)
            active=_run('systemctl',['--user','is-active',name+'.timer'],check=False)
            report['enabled']=enabled.returncode==0 and enabled.stdout.strip()=='enabled'
            report['active']=active.returncode==0 and active.stdout.strip()=='active'
            linger=_run('loginctl',['show-user',str(os.getuid()),'--property=Linger','--value'],check=False)
            if linger.returncode==0 and linger.stdout.strip() in {'yes','no'}:report['survives_logout']=linger.stdout.strip()=='yes'
        else:
            domain=f'gui/{os.getuid()}'
            observed=_run('launchctl',['print',domain+'/'+name],check=False)
            disabled=_run('launchctl',['print-disabled',domain],check=False)
            report['registered']=observed.returncode==0
            report['active']=report['registered'] and _launchd_matches(observed.stdout,plan)
            report['enabled']=disabled.returncode==0 and not re.search(r'"'+re.escape(name)+r'"\s*=>\s*true',disabled.stdout)
            report['survives_logout']=False
            last=re.search(r'^\s*last exit code = (\d+)\s*$',observed.stdout,re.M)
            if last:report['last_exit_code']=int(last.group(1))
        if report['last_exit_code'] not in {None,0}:report['reason']='last_cleanup_failed'
        elif report['registered'] and report['enabled'] and report['active']:
            report.update(status='scheduled',reason='scheduled')
        else:report['reason']='supervisor_not_active'
    except (SupervisorError,OSError,ValueError):
        report['reason']='supervisor_unavailable'
    return report


def install(brand,root):
    plan=preview(brand,root)
    _check_destinations(plan['files'])
    if plan['kind']=='systemd_user':
        _run('systemctl',['--user','show','--property=Version','--value'])
        existing=None
    else:
        domain=f'gui/{os.getuid()}'
        _run('launchctl',['print',domain])
        existing=_run('launchctl',['print',domain+'/'+plan['unit']],check=False)
        if existing.returncode==0 and not _launchd_matches(existing.stdout,plan):
            raise SupervisorError('An existing launchd job differs. Review it manually; nothing was replaced.')
    for item in plan['files']:
        path=Path(item['path']);_mkdir_private(path.parent);_atomic(path,item['content'])
    for action in plan['actions']:
        if existing is not None and existing.returncode==0 and action[1]=='bootstrap':continue
        _run(action[0],action[1:])
    report=inspect(brand,root)
    if report['status']!='scheduled':
        raise SupervisorError('Cleanup configuration was saved, but a working task was not verified. Run data-cleanup-status; no cleanup was run by the installer.')
    return report


def register(app):
    import typer
    def output(operation,brand,root):
        try:result=operation(brand,root)
        except (SupervisorError,OSError) as exc:
            message=str(exc) if isinstance(exc,SupervisorError) else 'Could not access the local supervisor configuration.'
            typer.echo(json.dumps({'version':1,'status':'error','message':message}))
            raise typer.Exit(1) from None
        typer.echo(json.dumps(result,ensure_ascii=False))
        if result['status']=='attention_required':raise typer.Exit(1)

    @app.command('data-cleanup-install')
    def data_cleanup_install(brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(...,'--root'),
            yes:bool=typer.Option(False,'--yes',help='Install and enable this exact brand/workspace daily cleanup task.'),
            dry_run:bool=typer.Option(False,'--dry-run',help='Show the complete configuration and actions without installing (the default).')):
        """Preview a daily local cleanup task; install only with explicit --yes."""
        if yes and dry_run:raise typer.BadParameter('Choose --dry-run or --yes, not both.')
        output(install if yes else preview,brand,root)

    @app.command('data-cleanup-status')
    def data_cleanup_status(brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(...,'--root')):
        """Inspect the local cleanup supervisor without changing it or running cleanup."""
        output(inspect,brand,root)
