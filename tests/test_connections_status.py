"""Connection reads retain healthy platforms when another local capability is stale."""
import json

import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.brands import crear_brand, cargar_brand
from socialctl.models import Platform
from socialctl.connections import keychain
from tests.test_saas_cli import wire

runner = CliRunner()


@pytest.mark.parametrize('json_output', [True, False])
def test_stale_connections_do_not_hide_healthy_youtube(tmp_path, monkeypatch, json_output):
    server, _, ring = wire(tmp_path, monkeypatch)
    crear_brand(tmp_path, 'MyBrand')
    common = ['--brand', 'MyBrand', '--root', str(tmp_path)]
    assert runner.invoke(app, ['account', 'login', *common, '--service', 'https://social.example', '--no-browser']).exit_code == 0
    assert runner.invoke(app, ['connect', 'youtube', *common, '--no-browser']).exit_code == 0
    brand = cargar_brand(tmp_path, 'MyBrand')
    for platform in (Platform.FACEBOOK, Platform.INSTAGRAM, Platform.TIKTOK):
        metadata = {'version':1, 'auth_mode':'broker', 'protocol':'saas-v1',
                    'service_url':'https://social.example', 'connection_id':'stale-'+platform.value,
                    'account_id':'fictional-'+platform.value}
        keychain.save(brand, platform, metadata, 'fictional-old-secret-'+platform.value)
        brand.guardar_secreto(platform, metadata)
    before = {p.name:p.read_bytes() for p in brand.dir_secretos.glob('*.json')}
    result = runner.invoke(app, ['connections', *common, *(['--json'] if json_output else [])])
    assert result.exit_code == 3, result.output
    if json_output:
        data = json.loads(result.stdout)
        assert data['status'] == 'partial'
        assert [r['platform'] for r in data['connections']] == ['youtube']
        assert [r['platform'] for r in data['errors']] == ['facebook', 'instagram', 'tiktok']
        assert all(r['code'] == 'connection_unavailable' and r['http_status'] == 404 for r in data['errors'])
        assert all(r['retryable'] is False for r in data['errors'])
    else:
        assert 'youtube:' in result.stdout
        assert all(p+': ' in result.stderr for p in ('facebook','instagram','tiktok'))
        assert 'account attach' in result.stderr
    assert before == {p.name:p.read_bytes() for p in brand.dir_secretos.glob('*.json')}
    assert not any(secret in result.output for secret in ring.values.values())


def test_connections_json_preserves_busy_startup_reason(tmp_path, monkeypatch):
    from socialctl import youtube_data
    from socialctl.management.meta_comment_inbox import CommentInboxStore
    brand = crear_brand(tmp_path, 'Fictional')
    path = brand.raiz/'.socialctl/unified-inbox/state.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version':1,'items':{},'watermarks':{}}))
    monkeypatch.setattr(youtube_data, 'LOCK_WAIT_SECONDS', 0.01)
    with CommentInboxStore(brand.raiz).lock():
        result = runner.invoke(app, ['connections','--brand','Fictional','--root',str(tmp_path),'--json'])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data['error']['code'] == 'local_data_busy'
    assert data['error']['retryable'] is True


@pytest.mark.parametrize('same_account', [True, False])
def test_explicit_attach_recovers_deleted_capability_only_for_same_owner_approved_account(tmp_path, monkeypatch, same_account):
    from socialctl.connections import account_cli
    from tests.test_saas_auth import fields
    server, _, _ = wire(tmp_path, monkeypatch)
    crear_brand(tmp_path, 'MyBrand')
    common = ['--brand','MyBrand','--root',str(tmp_path)]
    login = ['account','login',*common,'--service','https://social.example','--no-browser']
    assert runner.invoke(app, login).exit_code == 0
    assert runner.invoke(app, ['connect','youtube',*common,'--no-browser']).exit_code == 0
    brand = cargar_brand(tmp_path, 'MyBrand')
    installation = account_cli.metadata(brand)
    previous = brand.leer_secreto(Platform.YOUTUBE)
    response = server.post('/account/installations/'+installation['connection_id']+'/revoke',
                           data=fields(server.get('/account/')), headers={'Origin':'https://social.example'}, follow_redirects=False)
    assert response.status_code == 303
    response = server.get('/api/saas/v1/connections/'+previous['connection_id'],
                          headers={'Authorization':'Bearer '+keychain.get(brand,Platform.YOUTUBE,previous)})
    assert response.status_code == 404 and 'connection_unavailable' in response.text
    assert runner.invoke(app, login).exit_code == 0
    if not same_account:
        previous['account_id'] = 'UC-another-account'
        brand.guardar_secreto(Platform.YOUTUBE, previous)
    result = runner.invoke(app, ['account','attach','youtube',*common])
    current = brand.leer_secreto(Platform.YOUTUBE)
    if same_account:
        assert result.exit_code == 0, result.output
        assert current['connection_id'] != previous['connection_id']
        assert current['account_id'] == previous['account_id']
        assert runner.invoke(app, ['connections',*common,'--json']).exit_code == 0
    else:
        assert result.exit_code == 1
        assert 'approved account changed' in result.output
        assert current == previous
