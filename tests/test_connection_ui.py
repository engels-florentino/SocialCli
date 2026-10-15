"""Customer UI uses packaged assets and preserves the connection protocol."""
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.test_connection_service import make_service, start, authorize


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_connect_page_loads_same_origin_packaged_brand_assets(tmp_path):
    client, store = make_service(tmp_path)
    value, _ = start(client)
    page = client.get(value['browser_url'])
    tags = Elements(page.text).tags
    styles = [a['href'] for t,a in tags if t == 'link' and a.get('rel') == 'stylesheet']
    logos = [a['src'] for t,a in tags if t == 'img' and a.get('alt') == 'SocialCli']
    assert len(styles) == len(logos) == 1
    assert styles[0].startswith('/connect/assets/') and logos[0].startswith('/connect/assets/')
    css = client.get(styles[0])
    logo = client.get(logos[0])
    assert css.status_code == 200 and css.headers['content-type'].startswith('text/css')
    assert logo.status_code == 200 and logo.headers['content-type'] == 'image/png'
    approved = Path(__file__).parents[1] / 'docs/assets/socialcli-logo-1024.png'
    assert logo.content == approved.read_bytes()
    # Rendering/assets must never start the single-use authorization.
    with store.transaction() as db:
        assert 'state' not in store.get(db,'authorization',value['authorization_id'])['payload']
    assert not any(t == 'script' or 'style' in a or any(k.startswith('on') for k in a) for t,a in tags)


@pytest.mark.parametrize('platform,button',[
    ('youtube','Continue with Google'),('facebook','Continue with Facebook'),
    ('instagram','Continue with Facebook'),('tiktok','Continue with TikTok')])
def test_connect_button_identifies_the_actual_login_provider(platform, button):
    from socialctl.connection_service.landing import landing_page
    html = landing_page('fictional-authorization','fictional-form-token',platform=platform)
    assert button in html
    tags = Elements(html).tags
    assert next(a for t,a in tags if t == 'form')['method'] == 'post'
    assert next(a for t,a in tags if t == 'form')['action'] == '/connect/fictional-authorization'
    assert next(a for t,a in tags if t == 'input' and a.get('name') == 'form_token')['value'] == 'fictional-form-token'


@pytest.mark.parametrize('cancelled',[False,True])
def test_callback_has_branding_and_does_not_claim_account_is_already_connected(tmp_path, cancelled):
    client, _ = make_service(tmp_path)
    value, _ = start(client)
    _, page = authorize(client,value,error=cancelled)
    tags = Elements(page.text).tags
    assert any(t == 'img' and a.get('alt') == 'SocialCli' for t,a in tags)
    assert 'Return to SocialCli' in page.text
    assert ('Authorization cancelled' if cancelled else 'Authorization received') in page.text
    assert '<h1>Connected' not in page.text
    assert 'provider-access-one' not in page.text and 'provider-refresh-one' not in page.text


def test_expired_browser_link_shows_recovery_ui_but_api_keeps_json(tmp_path):
    now = [1000.0]
    client, _ = make_service(tmp_path,clock=lambda:now[0])
    value, headers = start(client)
    now[0] += 601
    page = client.get(value['browser_url'])
    assert page.status_code == 404 and page.headers['content-type'].startswith('text/html')
    assert any(t == 'img' and a.get('alt') == 'SocialCli' for t,a in Elements(page.text).tags)
    assert 'new connection link' in page.text
    assert value['authorization_id'] not in page.text
    api = client.post(f"/v1/authorizations/{value['authorization_id']}/poll",headers=headers)
    assert api.status_code == 404 and api.headers['content-type'].startswith('application/json')
    assert api.json()['detail'] == 'connection or authorization not found'


def test_invalid_callback_is_branded_and_never_reflects_query_data(tmp_path):
    client, _ = make_service(tmp_path)
    page = client.get('/oauth/youtube/callback',params={'state':'<script>secret</script>','code':'provider-secret'})
    assert page.status_code == 400 and page.headers['content-type'].startswith('text/html')
    assert 'provider-secret' not in page.text and '<script>' not in page.text
    assert 'no-store' in page.headers['cache-control']
    assert "style-src 'self'" in page.headers['content-security-policy']


def test_form_values_are_html_escaped():
    from socialctl.connection_service.landing import landing_page
    html = landing_page('id" onmouseover="bad','token" autofocus onfocus="bad')
    tags = Elements(html).tags
    assert not any(any(k.startswith('on') or k == 'autofocus' for k in attrs) for _,attrs in tags)


@pytest.mark.parametrize('platform,name', [
    ('youtube', 'YouTube'), ('facebook', 'Facebook'),
    ('instagram', 'Instagram'), ('tiktok', 'TikTok'),
])
def test_connection_names_destination_and_serves_local_platform_logo(tmp_path, platform, name):
    from socialctl.connection_service.landing import landing_page
    from xml.etree import ElementTree

    html = landing_page('fictional-authorization', 'fictional-form-token', platform=platform)
    assert f'<h1 id="page-title">Connect to {name}</h1>' in html
    assert f'<title>Connect to {name} · SocialCli</title>' in html
    logos = [attrs for tag, attrs in Elements(html).tags
             if tag == 'img' and attrs.get('class') == 'provider-logo']
    assert len(logos) == 1
    from socialctl.connection_service.landing import PROVIDER_LOGOS
    filename = PROVIDER_LOGOS[platform]
    assert logos[0]['src'] == f'/connect/assets/{filename}'
    client, _ = make_service(tmp_path)
    response = client.get(logos[0]['src'])
    assert response.status_code == 200
    asset = Path(__file__).parents[1] / 'socialctl/connection_service/assets' / filename
    assert response.content == asset.read_bytes()
    if not filename.endswith('.svg'):
        assert response.headers['content-type'].startswith('image/' + filename.rsplit('.', 1)[1])
        return
    assert response.headers['content-type'].startswith('image/svg+xml')
    svg = ElementTree.fromstring(response.content)
    assert svg.findall('.//{http://www.w3.org/2000/svg}path')
    assert not any(element.tag.rsplit('}', 1)[-1] in {'text', 'script', 'image', 'foreignObject'}
                   for element in svg.iter())
    assert not any('href' in key and not value.startswith('#')
                   for element in svg.iter() for key, value in element.attrib.items())


@pytest.mark.parametrize('platform', ['youtube', 'facebook', 'instagram', 'tiktok'])
def test_provider_identity_persists_in_landing_and_callback_shell(platform):
    from socialctl.connection_service.landing import landing_page, result_page, PROVIDER_LOGOS, PROVIDERS
    name = PROVIDERS[platform][0]
    for html in [landing_page('fictional-id', 'fictional-token', platform=platform),
                 result_page('No content was published.', success=True, platform=platform),
                 result_page('No account was connected.', cancelled=True, platform=platform)]:
        tags = Elements(html).tags
        icon = next(a for t,a in tags if t == 'link' and a.get('rel') == 'icon')
        assert icon['href'] == '/connect/assets/' + PROVIDER_LOGOS[platform]
        assert name in html.split('<title>',1)[1].split('</title>',1)[0]
        assert name.upper() + ' CONNECTION' in html
    assert 'Continue with Facebook' in landing_page('fictional-id','fictional-token',platform='instagram')
