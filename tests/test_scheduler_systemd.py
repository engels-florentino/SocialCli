"""Linux scheduling needs an actual active user manager, not just saved units."""
from pathlib import Path
import subprocess
import sys

import pytest

from socialctl import scheduler_systemd as service


def fake_manager(monkeypatch, tmp_path, *, enable=0, active=True, linger=True, manager=True, service_failure=False, absent=False, disable=0):
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'config'))
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'home')
    monkeypatch.setattr(service.shutil,'which',lambda name:'/test/'+name)
    calls=[]
    real_run=subprocess.run
    def run(argv,**kwargs):
        if not str(argv[0]).startswith('/test/'):
            return real_run(argv,**kwargs)
        calls.append(argv)
        args=argv[1:]
        code=0;out=''
        if args==['--user','show','--property=Version','--value']:
            code=0 if manager else 1;out='252'
        elif 'enable' in args:code=enable
        elif 'disable' in args:code=disable
        elif 'is-enabled' in args:out='enabled'
        elif 'is-active' in args:out='active' if active else 'inactive';code=0 if active else 3
        elif '--property=Linger' in args:out='yes' if linger else 'no'
        elif 'show' in args:
            unit=args[-1]
            if '--property=LoadState,ActiveState,UnitFileState' in args:
                out=('LoadState=not-found\nActiveState=inactive\nUnitFileState=\n' if absent
                     else 'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n')
            else:
                out='NeedDaemonReload=no\nDropInPaths=\nFragmentPath='+str(tmp_path/'config/systemd/user'/unit)+'\nActiveState='+('failed' if service_failure else 'inactive')+'\nResult='+('exit-code' if service_failure else 'success')+'\n'
        return subprocess.CompletedProcess(argv,code,out,'')
    monkeypatch.setattr(service.subprocess,'run',run)
    return calls


def test_installer_confirms_loaded_timer_for_explicit_workspace(tmp_path,monkeypatch):
    fake_manager(monkeypatch,tmp_path)
    root=tmp_path/'workspace';root.mkdir()
    result=service.install('Example',root)
    assert result['active'] is True and result['enabled'] is True
    assert result['configuration_matches'] is True
    files=list((tmp_path/'config/systemd/user').iterdir())
    assert {p.suffix for p in files}=={'.service','.timer'}
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    assert service.unit_name('Example',root)!=service.unit_name('Example',tmp_path/'another-workspace')


@pytest.mark.parametrize('options',[{'manager':False},{'enable':1},{'active':False}])
def test_installer_failure_cannot_report_success(tmp_path,monkeypatch,options):
    fake_manager(monkeypatch,tmp_path,**options)
    with pytest.raises(service.SupervisorError):service.install('Example',tmp_path/'workspace')
    if options.get('manager') is False:assert not (tmp_path/'config').exists()


def test_readonly_probe_requires_saved_units_and_linger(tmp_path,monkeypatch):
    fake_manager(monkeypatch,tmp_path,linger=False)
    root=tmp_path/'workspace'
    missing=service.inspect('Example',root)
    assert missing['configuration_matches'] is False
    assert not (tmp_path/'config').exists()
    installed=service.install('Example',root)
    assert installed['active'] is True
    assert installed['survives_logout'] is False


def test_uninstall_disables_timer_then_removes_only_its_units(tmp_path,monkeypatch):
    calls=fake_manager(monkeypatch,tmp_path)
    root=tmp_path/'workspace';root.mkdir();(root/'original.mp4').write_bytes(b'fixture')
    service.install('Example',root)
    other=tmp_path/'config/systemd/user/unrelated.service';other.write_text('User owned')
    service.uninstall('Example',root)
    assert other.read_text()=='User owned' and (root/'original.mp4').read_bytes()==b'fixture'
    assert not (tmp_path/'config/systemd/user'/(service.unit_name('Example',root)+'.timer')).exists()
    assert any('disable' in c and '--now' in c for c in calls)
    assert not any('stop' in c and c[-1].endswith('.service') for c in calls)


def test_uninstall_stops_cached_timer_even_when_unit_files_are_missing(tmp_path,monkeypatch):
    calls=fake_manager(monkeypatch,tmp_path)
    root=tmp_path/'workspace'
    assert service.uninstall('Example',root) is True
    timer=service.unit_name('Example',root)+'.timer'
    assert ['/test/systemctl','--user','stop',timer] in calls
    assert ['/test/systemctl','--user','disable','--now',timer] in calls
    assert not any('stop' in c and c[-1].endswith('.service') for c in calls)
    assert not (tmp_path/'config').exists()


def test_uninstall_cannot_claim_absence_when_manager_is_unavailable(tmp_path,monkeypatch):
    fake_manager(monkeypatch,tmp_path,manager=False)
    with pytest.raises(service.SupervisorError):
        service.uninstall('Example',tmp_path/'workspace')


def test_uninstall_reports_absence_only_after_querying_manager(tmp_path,monkeypatch):
    calls=fake_manager(monkeypatch,tmp_path,absent=True)
    assert service.uninstall('Example',tmp_path/'workspace') is False
    assert any('--property=LoadState,ActiveState,UnitFileState' in c for c in calls)
    assert not any('stop' in c or 'disable' in c for c in calls)


def test_uninstall_stops_timer_but_preserves_files_if_disable_fails(tmp_path,monkeypatch):
    calls=fake_manager(monkeypatch,tmp_path,disable=1)
    root=tmp_path/'workspace'
    service.install('Example',root)
    with pytest.raises(service.SupervisorError):service.uninstall('Example',root)
    assert len(list((tmp_path/'config/systemd/user').iterdir()))==2
    assert any('stop' in c and c[-1].endswith('.timer') for c in calls)
    assert not any('stop' in c and c[-1].endswith('.service') for c in calls)


def test_rendered_exec_uses_installed_interpreter_without_shell_or_expansion(tmp_path):
    root=tmp_path/'client space% $dollar "quote"'
    unit,timer=service.render('Example',root,executable=Path('/opt/python space/bin/python'))
    assert 'ExecStart="/opt/python space/bin/python" "-m" "socialctl" "run-due"' in unit
    assert 'space%% $$dollar \\"quote\\"' in unit
    directory = next(line.split('=',1)[1] for line in unit.splitlines() if line.startswith('WorkingDirectory='))
    assert Path(directory.replace('%%','%')) == root.resolve()
    assert 'OnUnitInactiveSec=60s' in timer
    with pytest.raises(service.SupervisorError):service.render('Example',Path('/unsafe\npath'))


@pytest.mark.parametrize('linger,service_failure,expected',[(True,False,0),(False,False,1),(True,True,1)])
def test_linux_cli_doctor_combines_manager_linger_and_executor(tmp_path,monkeypatch,linger,service_failure,expected):
    import json
    from datetime import datetime,timezone
    from typer.testing import CliRunner
    from socialctl.cli import app
    from socialctl.brands import crear_brand
    from socialctl.executor import heartbeat
    from socialctl.scheduler import ScheduleStore
    monkeypatch.setattr(sys,'platform','linux')
    fake_manager(monkeypatch,tmp_path,linger=linger,service_failure=service_failure)
    root=tmp_path/'workspace';brand=crear_brand(root,'Example')
    installed=CliRunner().invoke(app,['schedule-install','--brand','Example','--root',str(root)])
    assert installed.exit_code==0,installed.output
    heartbeat(ScheduleStore(brand.raiz),'finished',datetime.now(timezone.utc))
    result=CliRunner().invoke(app,['schedule-doctor','--brand','Example','--root',str(root),'--json'])
    assert result.exit_code==expected,result.output
    report=json.loads(result.stdout)
    assert report['supervisor']['kind']=='systemd_user'
    assert report['status']==('ready' if expected==0 else 'attention_required')
    assert report['unattended_delivery_guaranteed'] is False
    removed=CliRunner().invoke(app,['schedule-uninstall','--brand','Example','--root',str(root)])
    assert removed.exit_code==0,removed.output
    assert (brand.raiz/'.socialctl/executor-state.json').exists()
    assert list((tmp_path/'config/systemd/user').iterdir())==[]



def test_rendered_service_command_runs_in_external_empty_workspace(tmp_path,monkeypatch):
    import json,shlex
    from socialctl.brands import crear_brand
    fake_manager(monkeypatch,tmp_path)
    root=tmp_path/'external workspace';brand=crear_brand(root,'Example')
    service.install('Example',root)
    path=tmp_path/'config/systemd/user'/(service.unit_name('Example',root)+'.service')
    argv=shlex.split(next(line.split('=',1)[1] for line in path.read_text().splitlines() if line.startswith('ExecStart=')))
    result=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    state=json.loads((brand.raiz/'.socialctl/executor-state.json').read_text())
    assert state['heartbeat']['state']=='finished'
