"""Agent actions accompany observations without contacting real providers."""
import json
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.cli import app


def test_stats_keeps_good_metrics_and_exposes_typed_read_failure(tmp_path,monkeypatch):
    from socialctl.metricas.base import LECTORES
    from socialctl.metricas.modelos import LecturaRed,EstadoLectura,Cuenta
    from socialctl.models import Platform
    crear_brand(tmp_path,'Example')
    class Good:
        def leer(self,*args):return LecturaRed(estado=EstadoLectura.OK,cuenta=Cuenta(seguidores=7))
    class Failed:
        def leer(self,*args):raise httpx.ReadTimeout('https://example.test/?access_token=never-print',request=httpx.Request('GET','https://example.test/'))
    monkeypatch.setitem(LECTORES,Platform.YOUTUBE,Good)
    monkeypatch.setitem(LECTORES,Platform.FACEBOOK,Failed)
    result=CliRunner().invoke(app,['stats','--brand','Example','--root',str(tmp_path),'--only','youtube','--only','facebook','--json'])
    assert result.exit_code==3,result.output
    payload=json.loads(result.stdout)
    assert payload['status']=='partial'
    assert payload['data']['snapshot']['redes']['youtube']['cuenta']['seguidores']==7
    assert payload['errors'][0]['code']=='transport_failure'
    assert payload['errors'][0]['action']=='retry_later'
    assert payload['errors'][0]['retryable'] is True
    assert 'never-print' not in result.stdout


def test_unconfigured_inbox_distinguishes_account_selection_from_unsupported_platform(tmp_path):
    crear_brand(tmp_path,'Example')
    result=CliRunner().invoke(app,['inbox','--brand','Example','--root',str(tmp_path),'--since','2026-10-01T00:00:00Z','--json'])
    assert result.exit_code==1,result.output
    report=json.loads(result.stdout)
    rows={e['platform']:e for e in report['errors']}
    assert rows['youtube']['code']=='account_not_configured'
    assert rows['youtube']['requires_user_action'] is True
    assert rows['tiktok']['code']=='unsupported_operation'
    assert rows['tiktok']['retryable'] is False


@pytest.mark.parametrize('kind',['meta','youtube'])
def test_partial_comment_cli_keeps_items_and_cursor_with_recovery_action(tmp_path,monkeypatch,kind):
    crear_brand(tmp_path,'Example')
    payload={'items':[{'id':'retained'}],'data':[{'id':'retained'}],'complete':False,'cursor':'next',
             'error':'Read timed out','timed_out':True}
    if kind=='meta':
        monkeypatch.setattr('socialctl.management.comments_cli.MetaCommentsClient',lambda *a:SimpleNamespace(list_comments=lambda *a,**kw:payload))
        command=['comments','list','789','--platform','facebook']
    else:
        monkeypatch.setattr('socialctl.management.community_cli.YouTubeCommunityClient',lambda *a:SimpleNamespace(list_threads=lambda *a,**kw:payload))
        command=['content','youtube-community','threads-list','video-1']
    result=CliRunner().invoke(app,[*command,'--brand','Example','--root',str(tmp_path),'--json'])
    assert result.exit_code==1,result.output
    report=json.loads(result.stdout)
    assert all(report[k]==v for k,v in payload.items())
    assert report['error_detail']['action']=='adjust_read'


@pytest.mark.parametrize('command',[
    ['comments','list','789','--platform','facebook'],
    ['content','youtube-community','threads-list','video-1']])
def test_invalid_brand_in_read_cli_keeps_json_contract(tmp_path,command):
    result=CliRunner().invoke(app,[*command,'--brand','Missing','--root',str(tmp_path),'--json'])
    assert result.exit_code==1,result.output
    report=json.loads(result.stdout)
    assert report['status']=='error' and report['errors'][0]['action']=='correct_input'


@pytest.mark.parametrize('failure',['unsupported','state'])
def test_local_comments_inbox_failures_keep_json_contract(tmp_path,monkeypatch,failure):
    from socialctl.management.changes import ChangeError
    crear_brand(tmp_path,'Example')
    command=['comments','inbox','--brand','Example','--root',str(tmp_path),'--json']
    if failure=='unsupported':
        command+=['--platform','youtube']
    else:
        def broken(*args):raise ChangeError('invalid local inbox')
        monkeypatch.setattr('socialctl.management.comments_cli.CommentInboxStore.load',broken)
    result=CliRunner().invoke(app,command)
    assert result.exit_code==1
    report=json.loads(result.stdout)
    assert report['status']=='error'
    assert report['errors'][0]['retryable'] is False
