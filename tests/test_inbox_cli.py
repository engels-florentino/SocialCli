import json
import pytest
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.brands import crear_brand


def test_inbox_reports_unavailable_instead_of_empty_success(tmp_path):
    brand=crear_brand(tmp_path,'Example')
    result=CliRunner().invoke(app,['inbox','--brand',brand.nombre,'--root',str(tmp_path),'--since','ayer','--timezone','America/New_York','--json'])
    report=json.loads(result.stdout)
    assert result.exit_code==1
    assert {r['platform'] for r in report['coverage']}=={'youtube','facebook','instagram','tiktok'}
    assert all(not r['complete'] for r in report['coverage'])
    assert report['data']['resolved_since']


@pytest.mark.parametrize('config',['version: [','version: 1\ntimezone: []\n'])
def test_invalid_timezone_configuration_is_structured_json(tmp_path,config):
    brand=crear_brand(tmp_path,'Example')
    (brand.raiz/'inbox.yml').write_text(config)
    result=CliRunner().invoke(app,['inbox','--brand',brand.nombre,'--root',str(tmp_path),'--since','ayer','--json'])
    assert result.exit_code==2
    assert json.loads(result.stdout)['status']=='error'
