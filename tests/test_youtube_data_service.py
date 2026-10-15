"""Daily cleanup installation is explicit, isolated and honest about offline limits."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import typer
from typer.testing import CliRunner
import pytest


def workspace(tmp_path):
    root=tmp_path/'workspace with spaces % $literal'
    brand=root/'Example';brand.mkdir(parents=True)
    (brand/'brand.md').write_text('Fictional brand')
    (brand/'original.mp4').write_bytes(b'customer original')
    return root


def fixture(monkeypatch,tmp_path,platform='linux',*,manager=True,active=True,last_exit=0):
    from socialctl import youtube_data_service as service
    monkeypatch.setattr(sys,'platform',platform)
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'config'))
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'home')
    calls=[]
    def run(tool,args,*,check=True):
        calls.append((tool,args))
        out='';code=0
        if not manager:code=1
        elif tool=='launchctl':
            if args[0]=='print-disabled':out='disabled services = {\n}\n'
            elif args[0]=='print' and len(args[1].split('/'))>2:
                label=args[1].split('/')[-1]
                path=Path.home()/'Library/LaunchAgents'/(label+'.plist')
                if not path.exists():code=113
                else:
                    payload=plistlib.loads(path.read_bytes())
                    out='path = '+str(path)+'\nprogram = '+payload['ProgramArguments'][0]+'\narguments = {\n'+'\n'.join(payload['ProgramArguments'])+'\n}\nlast exit code = '+str(last_exit)+'\n'
        elif 'is-enabled' in args:out='enabled';code=0 if active else 1
        elif 'is-active' in args:out='active' if active else 'inactive';code=0 if active else 3
        elif tool=='loginctl':out='yes'
        elif '--property=Version' in args:out='256'
        elif 'show' in args:
            name=args[-1];path=tmp_path/'config/systemd/user'/name
            out='NeedDaemonReload=no\nDropInPaths=\nFragmentPath='+str(path)+'\nActiveState=inactive\nResult='+('success' if last_exit==0 else 'exit-code')+'\nExecMainStatus='+str(last_exit)+'\n'
        if check and code:raise service.SupervisorError('Supervisor operation failed.')
        return subprocess.CompletedProcess([tool,*args],code,out,'private manager diagnostic')
    monkeypatch.setattr(service,'_run',run)
    return service,calls


def cli(service):
    app=typer.Typer();service.register(app)
    return app


@pytest.mark.parametrize('platform',['linux','darwin'])
def test_default_install_is_complete_preview_without_writes_or_supervisor_calls(tmp_path,monkeypatch,platform):
    service,calls=fixture(monkeypatch,tmp_path,platform)
    root=workspace(tmp_path)
    result=CliRunner().invoke(cli(service),['data-cleanup-install','--brand','Example','--root',str(root)])
    assert result.exit_code==0,result.output
    report=json.loads(result.stdout)
    assert report['status']=='preview' and report['argv']==[sys.executable,'-m','socialctl','data-cleanup','--brand','Example','--root',str(root)]
    assert report['files'] and report['physical_erasure_guaranteed'] is False
    assert not calls and not (tmp_path/'config').exists() and not (tmp_path/'home').exists()
    assert (root/'Example/original.mp4').read_bytes()==b'customer original'


def test_linux_yes_installs_private_daily_units_bound_to_exact_workspace(tmp_path,monkeypatch):
    service,calls=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path)
    result=CliRunner().invoke(cli(service),['data-cleanup-install','--brand','Example','--root',str(root),'--yes'])
    assert result.exit_code==0,result.output
    report=json.loads(result.stdout)
    assert report['status']=='scheduled' and report['survives_logout'] is True
    paths=list((tmp_path/'config/systemd/user').iterdir())
    assert len(paths)==2 and all(p.stat().st_mode&0o777==0o600 for p in paths)
    timer=next(p for p in paths if p.suffix=='.timer').read_text()
    command=next(p for p in paths if p.suffix=='.service').read_text()
    assert 'OnCalendar=daily' in timer and 'Persistent=true' in timer
    assert 'data-cleanup' in command and '--purge-youtube' not in command
    assert '%% $$literal' in command and 'UMask=0077' in command
    assert any('enable' in args and '--now' in args for _,args in calls)
    assert not any(args and args[0]=='start' for _,args in calls)
    assert (root/'Example/original.mp4').read_bytes()==b'customer original'


def test_macos_installs_structured_arguments_without_shell_and_no_immediate_cleanup(tmp_path,monkeypatch):
    service,calls=fixture(monkeypatch,tmp_path,'darwin')
    root=workspace(tmp_path)
    result=CliRunner().invoke(cli(service),['data-cleanup-install','--brand','Example','--root',str(root),'--yes'])
    assert result.exit_code==0,result.output
    report=json.loads(result.stdout)
    assert report['status']=='scheduled' and report['survives_logout'] is False
    path=next((tmp_path/'home/Library/LaunchAgents').iterdir())
    payload=plistlib.loads(path.read_bytes())
    assert payload['ProgramArguments']==[sys.executable,'-m','socialctl','data-cleanup','--brand','Example','--root',str(root)]
    assert payload['StartCalendarInterval']=={'Hour':12,'Minute':0}
    assert payload['RunAtLoad'] is False and payload['Umask']==0o077
    assert path.stat().st_mode&0o777==0o600
    assert not any(args[0] in {'kickstart','start'} for _,args in calls)
    assert (root/'Example/original.mp4').read_bytes()==b'customer original'


@pytest.mark.parametrize('platform',['linux','darwin'])
def test_unavailable_manager_does_not_install_and_never_leaks_diagnostics(tmp_path,monkeypatch,platform):
    service,_=fixture(monkeypatch,tmp_path,platform,manager=False)
    root=workspace(tmp_path)
    result=CliRunner().invoke(cli(service),['data-cleanup-install','--brand','Example','--root',str(root),'--yes'])
    assert result.exit_code==1
    assert 'private manager diagnostic' not in result.output
    assert not (tmp_path/'config').exists() and not (tmp_path/'home').exists()


def test_status_reports_missing_then_tampered_supervisor_without_repairing_files(tmp_path,monkeypatch):
    service,_=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path);app=cli(service)
    missing=CliRunner().invoke(app,['data-cleanup-status','--brand','Example','--root',str(root)])
    assert missing.exit_code==1 and json.loads(missing.stdout)['status']=='attention_required'
    assert not (tmp_path/'config').exists()
    assert CliRunner().invoke(app,['data-cleanup-install','--brand','Example','--root',str(root),'--yes']).exit_code==0
    path=next((tmp_path/'config/systemd/user').glob('*.service'));path.write_text('User modified configuration')
    report=CliRunner().invoke(app,['data-cleanup-status','--brand','Example','--root',str(root)])
    assert report.exit_code==1 and json.loads(report.stdout)['configuration_matches'] is False
    reinstall=CliRunner().invoke(app,['data-cleanup-install','--brand','Example','--root',str(root),'--yes'])
    assert reinstall.exit_code==1 and path.read_text()=='User modified configuration'


def test_installer_rejects_symlinks_and_does_not_touch_other_user_files(tmp_path,monkeypatch):
    service,_=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path)
    preview=service.preview('Example',root)
    target=Path(preview['files'][0]['path']);target.parent.mkdir(parents=True)
    original=tmp_path/'important';original.write_text('Preserve me');target.symlink_to(original)
    with pytest.raises(service.SupervisorError):service.install('Example',root)
    assert original.read_text()=='Preserve me' and target.is_symlink()


def test_supervisor_identity_separates_workspaces_and_brands_and_rejects_bad_paths(tmp_path,monkeypatch):
    service,_=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path)
    assert service.unit_name('Example',root)!=service.unit_name('Example',tmp_path/'other')
    assert service.unit_name('Example',root)!=service.unit_name('Other',root)
    with pytest.raises(service.SupervisorError):service.preview('../outside',root)
    with pytest.raises(service.SupervisorError):service.preview('Example',Path('/bad\npath'))


@pytest.mark.parametrize('platform',['linux','darwin'])
def test_nonzero_cleanup_exit_is_attention_not_success(tmp_path,monkeypatch,platform):
    service,_=fixture(monkeypatch,tmp_path,platform,last_exit=1)
    root=workspace(tmp_path)
    result=CliRunner().invoke(cli(service),['data-cleanup-install','--brand','Example','--root',str(root),'--yes'])
    assert result.exit_code==1
    status=CliRunner().invoke(cli(service),['data-cleanup-status','--brand','Example','--root',str(root)])
    assert status.exit_code==1 and json.loads(status.stdout)['last_exit_code']==1


def test_generated_command_executes_cleanup_in_an_external_fixture_workspace(tmp_path,monkeypatch):
    service,_=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path)
    report=service.preview('Example',root)
    result=subprocess.run(report['argv'],cwd=root,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    payload=json.loads(result.stdout)
    assert payload['complete'] is True and payload['purge'] is False
    assert (root/'Example/original.mp4').read_bytes()==b'customer original'


def test_cli_requires_exact_scope_and_rejects_conflicting_install_options(tmp_path,monkeypatch):
    service,calls=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path);app=cli(service)
    for arguments in (['--root',str(root)],['--brand','Example'],
                      ['--brand','Example','--root',str(root),'--yes','--dry-run']):
        result=CliRunner().invoke(app,['data-cleanup-install',*arguments])
        assert result.exit_code==2
    assert not calls


def test_status_does_not_trust_unit_files_reached_through_symlinked_directory(tmp_path,monkeypatch):
    service,_=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path)
    service.install('Example',root)
    target=tmp_path/'config/systemd/user'
    other=tmp_path/'elsewhere';target.rename(other);target.symlink_to(other,target_is_directory=True)
    report=service.inspect('Example',root)
    assert report['status']=='attention_required' and report['configuration_matches'] is False


def test_status_reports_unavailable_systemctl_without_private_diagnostics(tmp_path,monkeypatch):
    service,_=fixture(monkeypatch,tmp_path)
    root=workspace(tmp_path);service.install('Example',root)
    fixture(monkeypatch,tmp_path,manager=False)
    result=CliRunner().invoke(cli(service),['data-cleanup-status','--brand','Example','--root',str(root)])
    assert result.exit_code==1
    assert json.loads(result.stdout)['reason']=='supervisor_unavailable'
    assert 'private manager diagnostic' not in result.stdout
