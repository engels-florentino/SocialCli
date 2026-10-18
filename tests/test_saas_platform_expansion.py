"""Owner-approved additions preserve the installation and existing provider grants."""
import json
import hashlib
import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_saas_auth import service, login, fields, USER
from tests.test_saas_connections import provider, start, select, selected, activate, token
from tests.test_saas_platform_access import restricted, installation_auth


def path(cap):
    return '/account/installations/' + cap['installation_id'] + '/platforms'


def post(client, url, form):
    return client.post(url, data=form, headers={'Origin': 'https://social.example'}, follow_redirects=False)


def additions(page, *platforms):
    form = {k: v for k, v in fields(page).items() if not k.startswith('allow_')}
    form.update({f'allow_{p}': '1' for p in platforms})
    return form


def test_owner_adds_instagram_without_replacing_youtube_or_installation(tmp_path):
    client, _, _ = service(tmp_path, provider=provider); login(client)
    cap = restricted(client, ['youtube'])
    value, proof = start(client, cap); select(client, value)
    grant = selected(client, value, proof); assert activate(client, grant).status_code == 200
    base = '/api/saas/v1/installations/' + cap['installation_id']
    before = client.get(base, headers=installation_auth(cap)).json()
    page = client.get(path(cap))
    assert page.status_code == 200
    assert client.get(base, headers=installation_auth(cap)).json() == before
    # Merely opening the page does not grant any platform.
    form = additions(page, 'instagram')
    assert post(client, path(cap), form).status_code == 303
    after = client.get(base, headers=installation_auth(cap)).json()
    assert after['platform_access']['platforms'] == ['instagram', 'youtube']
    assert after['installation_id'] == before['installation_id']
    assert after['expires_at'] == before['expires_at']
    assert token(client, grant, refresh=False).json()['access_token'] == 'social-access'
    # The newly approved network can reach authorization without another login.
    from socialctl.connection_service.settings import AppCredentials
    client.app.state.saas_connections.providers.settings.providers['instagram'] = AppCredentials('fictional', 'fictional-secret')
    response = client.post(base + '/authorizations', headers=installation_auth(cap), json={
        'platform': 'instagram', 'brand': 'MyBrand', 'poll_challenge': hashlib.sha256(b'fictional-proof').hexdigest()})
    assert response.status_code == 201, response.text
    assert token(client, grant, refresh=False).status_code == 200


def test_stale_addition_forms_do_not_remove_existing_or_newly_added_platforms(tmp_path):
    client, _, _ = service(tmp_path); login(client); cap = restricted(client, ['youtube'])
    one = additions(client.get(path(cap)), 'instagram')
    two = additions(client.get(path(cap)), 'facebook')
    assert post(client, path(cap), one).status_code == 303
    assert post(client, path(cap), two).status_code == 303
    assert post(client, path(cap), two).status_code == 303
    result = client.get('/api/saas/v1/installations/' + cap['installation_id'], headers=installation_auth(cap))
    assert result.json()['platform_access']['platforms'] == ['facebook', 'instagram', 'youtube']


def test_additions_require_owner_browser_csrf_origin_and_valid_fields(tmp_path):
    client, _, _ = service(tmp_path); login(client); cap = restricted(client, ['youtube'])
    page = client.get(path(cap)); assert page.status_code == 200
    form = additions(page, 'instagram')
    stranger = TestClient(client.app, base_url='https://social.example')
    assert stranger.get(path(cap), follow_redirects=False).status_code == 303
    assert stranger.post(path(cap), data=form, headers=installation_auth(cap)).status_code in {401, 403}
    assert post(client, path(cap), {k:v for k,v in form.items() if k != 'csrf'}).status_code == 403
    assert client.post(path(cap), data=form, headers={'Origin':'https://evil.example'}).status_code == 403
    assert post(client, path(cap), {**form, 'allow_unknown':'1'}).status_code == 400
    assert post(client, path(cap), {**form, 'allow_instagram':'true'}).status_code == 400
    result = client.get('/api/saas/v1/installations/' + cap['installation_id'], headers=installation_auth(cap))
    assert result.json()['platform_access']['platforms'] == ['youtube']


@pytest.mark.parametrize('condition', ['other_owner', 'expired', 'revoked'])
def test_ineligible_installation_cannot_be_expanded(tmp_path, condition):
    client, now, _ = service(tmp_path); login(client); cap = restricted(client, ['youtube'])
    page = client.get(path(cap)); assert page.status_code == 200
    form = additions(page, 'instagram')
    vault = client.app.state.saas.vault
    with vault.transaction() as db:
        entry = vault.get(db, 'saas-installation', cap['installation_id'])
        if condition == 'revoked':
            vault.delete(db, 'saas-installation', cap['installation_id'])
        else:
            if condition == 'other_owner': entry['payload']['user_id'] = '22222222-2222-4222-8222-222222222222'
            vault.put(db, 'saas-installation', cap['installation_id'], entry['payload'],
                expires=now[0]-1 if condition == 'expired' else entry['expires'], credential_hash=entry['credential_hash'])
    assert client.get(path(cap)).status_code == 404
    assert post(client, path(cap), form).status_code == 404


def test_cli_provides_owner_link_without_browsing_or_changing_credentials(tmp_path, monkeypatch):
    from tests.test_saas_cli import wire, runner, app, crear_brand, cargar_brand
    from socialctl.connections import account_cli
    server, _, ring = wire(tmp_path, monkeypatch); crear_brand(tmp_path, 'MyBrand')
    common = ['--brand', 'MyBrand', '--root', str(tmp_path)]
    assert runner.invoke(app, ['account', 'login', *common, '--service', 'https://social.example', '--no-browser']).exit_code == 0
    brand = cargar_brand(tmp_path, 'MyBrand'); saved = account_cli.metadata(brand)
    vault = server.app.state.saas.vault
    with vault.transaction() as db:
        entry = vault.get(db, 'saas-installation', saved['connection_id'])
        entry['payload']['platform_access'] = {'version':1, 'platforms':['youtube'], 'source':'owner_selection'}
        vault.put(db, 'saas-installation', saved['connection_id'], entry['payload'], expires=entry['expires'], credential_hash=entry['credential_hash'])
    secrets_before = dict(ring.values)
    monkeypatch.setattr(account_cli.webbrowser, 'open', lambda *_: pytest.fail('Agent command opened a browser'))
    result = runner.invoke(app, ['account', 'permissions', *common, '--no-browser', '--json'])
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output['browser_url'] == 'https://social.example/account/installations/' + saved['connection_id'] + '/platforms'
    assert output['platform_access']['platforms'] == ['youtube']
    assert ring.values == secrets_before
    for secret in ring.values.values(): assert secret not in result.output
    assert account_cli.metadata(brand) == saved
    with vault.read_transaction() as db:
        assert vault.get(db, 'saas-installation', saved['connection_id'])['payload']['platform_access']['platforms'] == ['youtube']


def test_platform_link_survives_owner_sign_in_without_open_redirect(tmp_path):
    from tests.test_saas_auth import begin
    from urllib.parse import urlsplit, parse_qs
    client, _, _ = service(tmp_path); login(client); cap = restricted(client, ['youtube'])
    browser = TestClient(client.app, base_url='https://social.example')
    landing = browser.get(path(cap), follow_redirects=False)
    assert landing.status_code == 303
    next_path = parse_qs(urlsplit(landing.headers['location']).query)['next'][0]
    form = begin(browser, next_path=next_path); form['code'] = '123456'
    result = post(browser, '/account/verify', form)
    assert result.status_code == 303
    assert result.headers['location'] == path(cap)
    assert browser.get(result.headers['location']).status_code == 200


def test_blocked_cli_connection_points_to_permissions_without_logging_out(tmp_path, monkeypatch):
    from tests.test_saas_cli import wire, runner, app, crear_brand, cargar_brand
    from socialctl.connections import account_cli
    from tests.test_saas_platform_access import mutate_policy
    server, _, ring = wire(tmp_path, monkeypatch); crear_brand(tmp_path, 'MyBrand')
    common = ['--brand', 'MyBrand', '--root', str(tmp_path)]
    assert runner.invoke(app, ['account', 'login', *common, '--service', 'https://social.example', '--no-browser']).exit_code == 0
    saved = account_cli.metadata(cargar_brand(tmp_path, 'MyBrand'))
    mutate_policy(server, {'installation_id':saved['connection_id']}, {'version':1,'platforms':['instagram']})
    before = dict(ring.values)
    result = runner.invoke(app, ['connect', 'youtube', *common, '--no-browser'])
    assert result.exit_code == 1
    assert 'socialcli account permissions' in result.output
    assert ring.values == before
    assert runner.invoke(app, ['account','status',*common]).exit_code == 0


def test_permissions_command_keeps_credentials_when_service_is_older(tmp_path, monkeypatch):
    from tests.test_saas_cli import wire, runner, app, crear_brand
    from socialctl.connections import account_cli
    server, _, ring = wire(tmp_path, monkeypatch); crear_brand(tmp_path, 'MyBrand')
    common = ['--brand', 'MyBrand', '--root', str(tmp_path)]
    assert runner.invoke(app, ['account', 'login', *common, '--service', 'https://social.example', '--no-browser']).exit_code == 0
    factory = account_cli.http_client
    def transport(request):
        if request.url.path == '/api/saas/v1/capabilities':
            return httpx.Response(200,json={'version':1})
        with factory() as client:return client.send(request)
    monkeypatch.setattr(account_cli,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(transport)))
    before = dict(ring.values)
    result = runner.invoke(app,['account','permissions',*common,'--no-browser'])
    assert result.exit_code == 1 and 'service needs an update' in result.output
    assert '/platforms' not in result.output
    assert ring.values == before
