"""One read-only preflight retains independent validation failures."""
import json
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.models import Platform
from tests.test_inventory import brand_at, API

runner = CliRunner()


def test_doctor_reports_supplied_jpeg_and_granted_scope_gaps(tmp_path, monkeypatch):
    import socialctl.inventory_cli as cli
    from socialctl import media
    brand = brand_at(tmp_path)
    brand.guardar_secreto(Platform.YOUTUBE, {'access_token':'fictional', 'granted_scopes':['youtube.readonly']})
    supplied = brand.raiz / 'media' / 'supplied.jpeg'
    supplied.write_bytes(b'fictional JPEG fixture; inspection mocked')
    monkeypatch.setattr(media, '_ffprobe', lambda path: {'streams':[{'codec_type':'video','codec_name':'mjpeg','width':640,'height':480}], 'format':{'format_name':'jpeg_pipe'}})
    api = API()
    monkeypatch.setattr(cli, 'make_http_client', api.client)
    result = runner.invoke(app, ['doctor','--brand',brand.nombre,'--root',str(tmp_path),'--platform','youtube','--media',str(supplied),'--require-scope','youtube.force-ssl','--json'])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data['media']['files'][0]['valid'] is True
    assert data['media']['files'][0]['asset']['kind'] == 'image'
    assert data['accounts'][0]['identity']['verified'] is True
    assert data['scope_requirements'][0]['missing'] == ['youtube.force-ssl']
    assert data['accounts'][0]['write_permissions_verified'] is False
    assert all(r.method == 'GET' for r in api.requests)


def test_doctor_retains_bad_media_and_private_key_failures_without_writes(tmp_path, monkeypatch):
    import socialctl.inventory_cli as cli
    from socialctl.connections.credential_cli import write_configuration
    brand = brand_at(tmp_path / 'workspace')
    private = tmp_path / 'private'; private.mkdir(mode=0o700)
    key = private / 'key'; key.write_text('fictional-invalid-key'); key.chmod(0o644)
    store = private / 'store'
    write_configuration(brand, {'version':1,'backend':'encrypted-file','key_file':str(key),'store_file':str(store)})
    monkeypatch.setattr(cli, 'make_http_client', API().client)
    before = sorted(str(p) for p in tmp_path.rglob('*'))
    result = runner.invoke(app, ['doctor','--brand',brand.nombre,'--root',str(brand.raiz.parent),'--platform','youtube','--media',str(brand.raiz/'media/missing.jpeg'),'--json'])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data['credential_store']['available'] is False
    assert data['media']['files'][0]['valid'] is False
    assert data['accounts'][0]['identity']['verified'] is True
    assert before == sorted(str(p) for p in tmp_path.rglob('*'))
    assert 'fictional-invalid-key' not in result.output


def test_preflight_post_keeps_validation_for_other_platforms(tmp_path, monkeypatch):
    import socialctl.inventory_cli as cli
    from tests.test_media_preflight import setup_post
    brand, supplied, calls = setup_post(tmp_path, monkeypatch)
    brand.guardar_secreto(Platform.YOUTUBE, {'access_token':'fictional'})
    monkeypatch.setattr(cli, 'make_http_client', API(account='channel-example').client)
    result = runner.invoke(app, ['preflight','--brand',brand.nombre,'--root',str(tmp_path),'--platform','youtube','--post','clip','--json'])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data['post']['platforms']['youtube']['status'] == 'passed'
    assert data['post']['platforms']['facebook']['status'] == 'blocked'
    assert len(calls) == 1
    assert data['accounts'][0]['identity']['verified'] is True
