"""Owner consent cannot be replaced by agent credentials or stale browser forms."""
import pytest
from tests.test_saas_auth import service, fields, login, USER
from tests.test_saas_pairing import pair, approve, poll

ORIGIN = {'Origin': 'https://social.example'}


def login_without_acceptance(client):
    form = fields(client.get('/account/login'))
    form.pop('privacy_accept', None)
    form['email'] = 'creator@example.test'
    response = client.post('/account/login', data=form, headers=ORIGIN)
    form = fields(response)
    form['code'] = '123456'
    assert client.post('/account/verify', data=form, headers=ORIGIN, follow_redirects=False).status_code == 303


def test_signin_without_acceptance_cannot_enable_assistant_but_can_delete_or_logout(tmp_path):
    client, _, _ = service(tmp_path)
    login_without_acceptance(client)
    value, _ = pair(client)
    page = client.get(value['browser_url'], follow_redirects=False)
    assert page.status_code == 303 and page.headers['location'].startswith('/account/privacy?')
    assert client.post(value['browser_url'], data=fields(client.get('/account/privacy')), headers=ORIGIN).status_code == 403
    assert client.get('/account/delete').status_code == 200
    account = client.get('/account/')
    assert 'Review the Privacy Policy' in account.text
    assert client.post('/account/logout', data={'csrf': fields(account)['csrf']}, headers=ORIGIN, follow_redirects=False).status_code == 303


def test_privacy_receipt_requires_owner_csrf_current_version_and_explicit_choice(tmp_path):
    client, now, _ = service(tmp_path)
    login_without_acceptance(client)
    page = client.get('/account/privacy')
    assert page.status_code == 200
    from types import SimpleNamespace
    data = fields(SimpleNamespace(text=page.text.split('</form>',1)[0]))
    for change in ({'privacy_accept': ''}, {'privacy_version': 'obsolete'}, {'csrf': 'forged'}):
        assert client.post('/account/privacy', data={**data, **change}, headers=ORIGIN).status_code in {400, 403}
    other, _, _ = service(tmp_path / 'other')
    assert other.post('/account/privacy', data=data, headers=ORIGIN).status_code in {401, 403}
    assert client.post('/account/privacy', data=data, headers={'Origin': 'https://attacker.example'}).status_code == 403
    assert client.post('/account/privacy', data=data, headers=ORIGIN, follow_redirects=False).status_code == 303
    vault = client.app.state.saas.vault
    with vault.read_transaction() as db:
        receipt = vault.get(db, 'saas-consent', USER)['payload']
    assert receipt['privacy_version'] == data['privacy_version']
    assert receipt['accepted_at'] == now[0] and receipt['user_id'] == USER
    value, _ = pair(client)
    assert client.get(value['browser_url'], follow_redirects=False).status_code == 200


def test_assistant_transfer_requires_explicit_current_acknowledgment(tmp_path):
    client, _, _ = service(tmp_path)
    login(client)
    value, proof = pair(client)
    page = client.get(value['browser_url'])
    data = fields(page)
    assert 'assistant_accept' in data
    data.pop('assistant_accept')
    assert client.post(value['browser_url'], data=data, headers=ORIGIN).status_code == 400
    assert poll(client, value, proof).json()['status'] == 'pending'
    assert approve(client, value).status_code == 200
    ready = poll(client, value, proof).json()
    vault = client.app.state.saas.vault
    with vault.read_transaction() as db:
        installation = vault.get(db, 'saas-installation', ready['installation_id'])['payload']
    assert installation['assistant_consent']['version'] == fields(page)['assistant_version']
    assert installation['assistant_consent']['accepted_at'] == 1000.0


def test_material_policy_change_blocks_existing_installation_without_blocking_revocation(tmp_path, monkeypatch):
    client, _, _ = service(tmp_path)
    login(client)
    value, proof = pair(client)
    approve(client, value)
    ready = poll(client, value, proof).json()
    from socialctl.saas import consent
    monkeypatch.setattr(consent, 'PRIVACY_VERSION', 'next-material-policy')
    url = '/api/saas/v1/installations/' + ready['installation_id']
    auth = {'Authorization': 'Bearer ' + ready['installation_secret']}
    denied = client.get(url, headers=auth)
    assert denied.status_code == 403 and 'privacy_acceptance_required' in denied.text
    assert client.delete(url, headers=auth).status_code == 200


def test_standalone_connection_cannot_start_without_browser_privacy_and_assistant_choices(tmp_path):
    from tests.test_connection_service import make_service, start
    client, vault = make_service(tmp_path)
    value, _ = start(client)
    page = client.get(value['browser_url'])
    data = fields(page)
    data.pop('privacy_accept', None)
    refused = client.post(value['browser_url'], data=data, headers=ORIGIN, follow_redirects=False)
    assert refused.status_code in {400, 403}
    with vault.read_transaction() as db:
        assert 'state' not in vault.get(db, 'authorization', value['authorization_id'])['payload']


def test_material_privacy_change_blocks_already_connected_legacy_token_release(tmp_path, monkeypatch):
    from tests.test_connection_service import make_service, start, authorize, complete
    client, _ = make_service(tmp_path)
    value, proof = start(client)
    authorize(client, value)
    connection = complete(client, value, proof)
    from socialctl.saas import consent
    monkeypatch.setattr(consent, 'PRIVACY_VERSION', 'material-update')
    result = client.post('/v1/connections/' + connection['connection_id'] + '/token',
                         headers={'Authorization': 'Bearer ' + connection['connection_secret']})
    assert result.status_code == 403 and 'privacy_acceptance_required' in result.text


def test_legacy_callback_rejects_policy_changed_after_browser_review(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from tests.test_connection_service import make_service, start, begin_browser
    client, vault = make_service(tmp_path)
    value, _ = start(client)
    redirect = begin_browser(client, value['browser_url'])
    state = parse_qs(urlsplit(redirect.headers['location']).query)['state'][0]
    from socialctl.saas import consent
    monkeypatch.setattr(consent, 'PRIVACY_VERSION', 'material-update')
    callback = client.get('/oauth/youtube/callback', params={'state':state,'code':'fictional-code'})
    assert callback.status_code == 403
    with vault.read_transaction() as db:
        record = vault.get(db, 'authorization', value['authorization_id'])['payload']
        assert record['status'] == 'pending' and 'result' not in record


@pytest.mark.parametrize('code',['privacy_acceptance_required','assistant_consent_required'])
@pytest.mark.parametrize('read_only',[True,False])
def test_consent_denial_preserves_owner_action_and_never_auto_accepts_or_retries(code,read_only):
    import httpx
    from socialctl.connections.saas_client import SaaSClient, ServiceRejected
    from socialctl.agent_errors import error_document
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            403,json={'detail':code+': private untrusted provider text'}))) as transport:
        client=SaaSClient('https://social.example',transport)
        with pytest.raises(ServiceRejected) as rejected:
            client.request('POST','/api/saas/v1/connections/fictional/token',secret='fictional-secret')
    result=error_document(rejected.value,read_only=read_only)
    assert result['code']==code and result['action']=='user_action'
    assert result['requires_user_action'] and not result['retryable']
    assert 'owner' in result['message'] and 'browser' in result['message']
    assert 'private untrusted' not in result['message']


@pytest.mark.parametrize('version',['PRIVACY_VERSION','ASSISTANT_VERSION'])
def test_existing_capability_cannot_read_account_status_or_tokens_after_consent_change(tmp_path,monkeypatch,version):
    from tests.test_saas_connections import setup,start,select,selected,activate
    client, _, installation = setup(tmp_path)
    value, proof = start(client,installation)
    select(client,value)
    connection=selected(client,value,proof)
    assert activate(client,connection).status_code==200
    from socialctl.saas import consent
    monkeypatch.setattr(consent,version,'material-change')
    url='/api/saas/v1/connections/'+connection['connection_id']
    auth={'Authorization':'Bearer '+connection['connection_secret']}
    assert client.get(url,headers=auth).status_code==403
    denied=client.post(url+'/token',headers=auth)
    assert denied.status_code==403 and 'access_token' not in denied.text
