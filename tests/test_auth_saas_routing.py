"""The normal auth entry point must never ask SaaS users for app secrets."""
import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.brands import crear_brand


@pytest.mark.parametrize('platform', ['youtube','facebook','instagram','tiktok'])
def test_auth_without_installation_fails_fast_with_login_instruction(tmp_path, platform):
    crear_brand(tmp_path,'Example')
    child=subprocess.Popen([sys.executable,'-m','socialctl','auth',platform,'--brand','Example','--root',str(tmp_path)],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        child.wait(timeout=5)
        stdout,stderr=child.communicate()
        assert child.returncode==1
        assert 'account login' in stdout+stderr
        assert 'client_key:' not in stdout+stderr and 'client_secret:' not in stdout+stderr
    finally:
        if child.poll() is None:child.kill();child.communicate()


@pytest.mark.parametrize('platform', ['youtube','facebook','instagram','tiktok'])
def test_auth_signed_in_uses_saas_connection_without_developer_prompt(tmp_path, monkeypatch, platform):
    brand=crear_brand(tmp_path,'Example')
    (brand.dir_secretos/'socialcli-account.json').write_text(json.dumps({
        'version':1,'auth_mode':'saas-installation','service_url':'https://social.example','connection_id':'fictional-installation'}))
    from socialctl.connections import account_cli
    calls=[]
    def connect(selected, network, **options):calls.append((selected.nombre,network.value,options))
    monkeypatch.setattr(account_cli,'connect_saas',connect)
    result=CliRunner().invoke(app,['auth',platform,'--brand','Example','--root',str(tmp_path),'--no-browser','--timeout','15'])
    assert result.exit_code==0,result.output
    assert calls==[('Example',platform,dict(no_browser=True,management=False,analytics=False,timeout=15,replace_independent=False))]
    assert 'client_key' not in result.output and 'client_secret' not in result.output


def test_auth_alias_completes_real_saas_routes_with_fictional_browser(tmp_path, monkeypatch):
    from tests.test_saas_cli import wire
    from socialctl.brands import cargar_brand
    from socialctl.models import Platform
    wire(tmp_path,monkeypatch)
    crear_brand(tmp_path,'Example')
    runner=CliRunner();common=['--brand','Example','--root',str(tmp_path)]
    login=runner.invoke(app,['account','login',*common,'--service','https://social.example','--no-browser'])
    assert login.exit_code==0,login.output
    result=runner.invoke(app,['auth','youtube',*common,'--no-browser'])
    assert result.exit_code==0,result.output
    saved=cargar_brand(tmp_path,'Example').leer_secreto(Platform.YOUTUBE)
    assert saved['protocol']=='saas-v1' and saved['account_id']=='UC-two'
    assert '/connect/' in result.output and 'Google Cloud client_id' not in result.output


def test_legacy_developer_auth_requires_terminal_even_when_explicit(tmp_path):
    crear_brand(tmp_path,'Example')
    child=subprocess.Popen([sys.executable,'-m','socialctl','auth','tiktok','--independent',
                            '--brand','Example','--root',str(tmp_path)],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        child.wait(timeout=5)
        stdout,stderr=child.communicate()
        assert child.returncode==1 and 'interactive terminal' in stdout+stderr
        assert 'TikTok client_key:' not in stdout+stderr
    finally:
        if child.poll() is None:child.kill();child.communicate()


def test_default_auth_preserves_legacy_broker_and_explains_saas_migration(tmp_path):
    from socialctl.models import Platform
    brand=crear_brand(tmp_path,'Example')
    old={'auth_mode':'broker','connection_id':'fictional-legacy'}
    brand.guardar_secreto(Platform.TIKTOK,old)
    result=CliRunner().invoke(app,['auth','tiktok','--brand','Example','--root',str(tmp_path)])
    assert result.exit_code==1
    assert 'account login' in result.output and 'account migrate' in result.output
    assert 'disconnect' not in result.output.lower()
    assert brand.leer_secreto(Platform.TIKTOK)==old
