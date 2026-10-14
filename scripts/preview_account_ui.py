"""Build offline account UI fixtures from real routes and fictional test services.

Run from a development checkout with `uv run python -m scripts.preview_account_ui`.
Serve the output on loopback; this is a visual catalogue, not an auth server.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
import argparse
import hashlib
import json
import re
import shutil

import httpx

from tests.test_saas_auth import service, fields, login
from tests.test_saas_connections import provider, start, selected, activate
from tests.test_saas_pairing import pair, approve, poll
from tests.test_connection_service import begin_browser
from socialctl.saas import pages
from socialctl.saas.access import AccessDecision


ROUTE_EXPECTATIONS = {
    'login': (200, 'Send sign-in code'),
    'email-invalid': (400, 'Enter a valid email address.'),
    'code': (200, 'Enter your sign-in code'),
    'code-invalid': (400, 'That code is invalid or expired.'),
    'code-expired': (403, 'This code expired. Request a new sign-in code.'),
    'account-empty': (200, 'No social accounts connected yet.'),
    'assistant-approve': (200, 'Allow Fictional assistant?'),
    'assistant-linked': (200, 'Assistant linked'),
    'youtube-connect': (200, 'confirm your account in this browser.'),
    'account-select': (200, 'Choose your account'),
    'account-confirmed': (200, 'ACCOUNT CONFIRMED'),
    'account-connected': (200, 'Disconnect'),
    'account-delete': (200, 'Type DELETE'),
    'assistant-long-name': (200, 'Allow AssistantWithAVeryLongUnbrokenFictionalNameForLayoutChecking123456789?'),
    'email-unavailable': (503, 'Email delivery is temporarily unavailable.'),
    'signin-unavailable': (503, 'Sign-in is temporarily unavailable.'),
}


def build(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    assets = Path(__file__).resolve().parents[1] / 'socialctl/connection_service/assets'
    shutil.copytree(assets, output / 'connect/assets', dirs_exist_ok=True)
    catalogue = []

    def save(name, html, source):
        status = None
        if hasattr(html, 'text'):
            expected_status, marker = ROUTE_EXPECTATIONS[name]
            assert html.status_code == expected_status, (name, html.status_code, expected_status)
            assert html.headers['content-type'].startswith('text/html'), name
            assert marker in html.text, (name, 'expected page marker missing')
            status = html.status_code
            html = html.text
        css_hash = hashlib.sha256((assets / 'connection.css').read_bytes()).hexdigest()[:12]
        html = html.replace('/connect/assets/connection.css', '/connect/assets/connection.css?v=' + css_hash)
        # Fixtures contain only fake accounts; additionally remove ephemeral form tokens.
        html = re.sub(r'<input type="hidden"[^>]*>', '', html)
        html = html.replace('<head>', '<head><meta http-equiv="Content-Security-Policy" '
            'content="default-src \'none\'; style-src \'self\'; img-src \'self\'; '
            'form-action \'none\'; base-uri \'none\'">', 1)
        (output / f'{name}.html').write_text(html)
        catalogue.append({'name': name, 'source': source, 'http_status': status})

    with TemporaryDirectory(prefix='socialcli-ui-fictional-') as tmp:
        client, now, _ = service(Path(tmp), provider=provider)
        now[0] = 1791979200  # Fixed fictional date for readable subscription dates.
        save('login', client.get('/account/login'), 'GET /account/login')
        form = fields(client.get('/account/login')); form['email'] = 'invalid'
        save('email-invalid', client.post('/account/login', data=form,
             headers={'Origin': 'https://social.example'}), 'POST /account/login: invalid email')
        # Capture the actual OTP page rather than copying its rendering into the fixture.
        form = fields(client.get('/account/login')); form['email'] = 'creator@example.test'
        otp = client.post('/account/login', data=form, headers={'Origin': 'https://social.example'})
        save('code', otp, 'POST /account/login: OTP sent')
        form = fields(otp); form['code'] = '000000'
        save('code-invalid', client.post('/account/verify', data=form,
             headers={'Origin': 'https://social.example'}), 'POST /account/verify: rejected code')
        now[0] += 601
        save('code-expired', client.post('/account/verify', data=form,
             headers={'Origin': 'https://social.example'}), 'POST /account/verify: expired attempt')
        now[0] += 3600
        login(client)
        save('account-empty', client.get('/account/'), 'GET /account/: empty account')
        value, proof = pair(client, 'Fictional assistant')
        save('assistant-approve', client.get(value['browser_url']), 'GET /account/pair/: pending')
        save('assistant-linked', approve(client, value), 'POST /account/pair/: approved')
        capability = poll(client, value, proof).json()
        value, proof = start(client, capability)
        save('youtube-connect', client.get(value['browser_url']), 'GET /connect/: YouTube')
        redirect = begin_browser(client, value['browser_url'])
        state = parse_qs(urlsplit(redirect.headers['location']).query)['state'][0]
        response = client.get('/oauth/youtube/callback', params={'state': state, 'code': 'fictional'})
        save('account-select', response, 'GET /oauth/youtube/callback: choose account')
        form = fields(response); form['account_id'] = 'UC-two'
        save('account-confirmed', client.post('/account/connect/' + value['authorization_id'],
             data=form, headers={'Origin': 'https://social.example'}), 'POST /account/connect/: confirmed')
        assert activate(client, selected(client, value, proof)).status_code == 200
        save('account-connected', client.get('/account/'), 'GET /account/: active trial, connection, assistant')
        save('account-delete', client.get('/account/delete'), 'GET /account/delete: confirmation only')
        value, _ = pair(client, 'AssistantWithAVeryLongUnbrokenFictionalNameForLayoutChecking123456789')
        save('assistant-long-name', client.get(value['browser_url']), 'GET /account/pair/: long label')
        client.close()

    for case, endpoint in [('email-unavailable', '/otp'), ('signin-unavailable', '/verify')]:
        with TemporaryDirectory(prefix='socialcli-ui-fictional-') as tmp:
            def unavailable(request):
                if request.url.path == endpoint:
                    return httpx.Response(503, json={})
            client, _, _ = service(Path(tmp), handler=unavailable)
            form = fields(client.get('/account/login')); form['email'] = 'creator@example.test'
            response = client.post('/account/login', data=form, headers={'Origin': 'https://social.example'})
            if endpoint == '/verify':
                form = fields(response); form['code'] = '123456'
                response = client.post('/account/verify', data=form, headers={'Origin': 'https://social.example'})
            save(case, response, 'Identity unavailable: ' + endpoint)
            client.close()

    save('code-long-email', pages.verify('fictional', 'fictional',
         'a' * 64 + '@' + 'b' * 63 + '.' + 'c' * 63 + '.example.test'), 'OTP renderer: long email')
    for reason, status, cancelled, processing in [
        ('trial_not_started', '', False, False), ('trial', '', False, False),
        ('trial_expired', '', False, False), ('paid', 'active', False, False),
        ('paid', 'active', True, False), ('subscription_required', 'past_due', False, False),
        ('trial_expired', '', False, True), ('suspended', '', False, False),
    ]:
        name = 'billing-' + ('canceled' if cancelled else 'processing' if processing else reason)
        detail = pages.subscription(AccessDecision(reason in {'trial', 'paid'}, reason, 1794571200),
            {'trial_end': 1793188800, 'subscription_status': status},
            {'customer_id': 'cus_fictional' if status else '', 'cancel_at_period_end': cancelled},
            'fictional', enabled=True, processing=processing)
        save(name, pages.page('Your subscription', '<h1 id="page-title">Your subscription</h1>' + detail),
             'Subscription renderer: ' + reason)
    (output / 'manifest.json').write_text(json.dumps(catalogue, indent=2) + '\n')
    names = json.dumps([item['name'] for item in catalogue])
    (output / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>SocialCli fictional UI catalogue</title>
<style>body{font:16px system-ui;margin:20px;background:#ddd}nav{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}iframe{border:0;background:white;display:block}select,button{font:inherit;padding:8px}output{display:block;margin:10px 0;white-space:pre-wrap}</style>
<h1>SocialCli — fictional UI fixtures</h1><p>Forms are blocked. These pages cannot connect accounts, send email or charge money.</p>
<nav><label>Page <select id="page"></select></label><label>Width <select id="width"><option>360</option><option>390</option><option>1440</option></select></label><button id="prev">Previous</button><button id="next">Next</button></nav>
<output id="result"></output><iframe title="SocialCli fixture" sandbox="allow-same-origin"></iframe>
<script>
const names=__NAMES__, select=document.querySelector('#page'), width=document.querySelector('#width'), frame=document.querySelector('iframe');
for(const name of names)select.add(new Option(name,name));
const params=new URLSearchParams(location.search);if(names.includes(params.get('page')))select.value=params.get('page');if(['360','390','1440'].includes(params.get('width')))width.value=params.get('width');
function show(){frame.style.width=width.value+'px';frame.style.height='900px';frame.src=select.value+'.html';history.replaceState(null,'','?page='+select.value+'&width='+width.value)}
frame.onload=()=>{const doc=frame.contentDocument;frame.style.height=Math.max(900,doc.documentElement.scrollHeight)+'px';document.querySelector('#result').textContent='Page: '+select.value+' | viewport: '+doc.documentElement.clientWidth+'px | content: '+doc.documentElement.scrollWidth+'px | horizontal overflow: '+(doc.documentElement.scrollWidth>doc.documentElement.clientWidth)+' | overflowing blocks: '+[...doc.querySelectorAll('h1,p,button,fieldset,legend,.connection-card')].filter(el=>el.scrollWidth>el.clientWidth+1).map(el=>el.tagName.toLowerCase()+(el.className?'.'+el.className:'')).join(', ');};
select.onchange=width.onchange=show;document.querySelector('#prev').onclick=()=>{select.selectedIndex=(select.selectedIndex+names.length-1)%names.length;show()};document.querySelector('#next').onclick=()=>{select.selectedIndex=(select.selectedIndex+1)%names.length;show()};show();
</script></html>'''.replace('__NAMES__', names))
    print(f'{len(catalogue)} fictional pages written to {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.superpowers/account-ui-catalogue'))
    build(parser.parse_args().output)
