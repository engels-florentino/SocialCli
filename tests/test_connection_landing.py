"""Login previews cannot consume or bind a pairing session."""
import re
from fastapi.testclient import TestClient
from tests.test_connection_service import make_service, start


def form_token(response):
    return re.search(r'name="form_token" value="([^"]+)"', response.text).group(1)


def click_connect(client, url):
    landing = client.get(url, follow_redirects=False)
    assert landing.status_code == 200
    return client.post(url, data={'form_token': form_token(landing)}, follow_redirects=False)


def test_preview_get_head_and_other_browser_do_not_consume(tmp_path):
    client, store = make_service(tmp_path)
    value, headers = start(client)
    url = value['browser_url']
    preview = client.get(url, follow_redirects=False)
    assert preview.status_code == 200 and 'Connect' in preview.text
    assert client.get(url).status_code == 200
    assert client.head(url).status_code == 200
    with store.transaction() as db:
        assert 'state' not in store.get(db, 'authorization', value['authorization_id'])['payload']
    human = TestClient(client.app, base_url='https://social.example')
    response = click_connect(human, url)
    assert response.status_code == 303
    assert client.get(url).status_code == 409
    assert 'no-store' in preview.headers['cache-control']
    assert preview.headers['referrer-policy'] == 'same-origin'
    assert 'noindex' in preview.headers['x-robots-tag']


def test_form_requires_matching_cookie_and_same_origin(tmp_path):
    client, store = make_service(tmp_path)
    value, headers = start(client)
    url = value['browser_url']
    page = client.get(url)
    token = form_token(page)
    other = TestClient(client.app, base_url='https://social.example')
    assert other.post(url, data={'form_token':token}).status_code == 403
    assert client.post(url, data={'form_token':token}, headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post(url, data={}).status_code == 403
    assert client.post(url, data={'form_token':token}, follow_redirects=False).status_code == 303
    assert client.post(url, data={'form_token':token}, follow_redirects=False).status_code in {403,409}


def test_expired_form_cannot_start_authorization(tmp_path):
    now=[1000.0]
    client, store = make_service(tmp_path, clock=lambda:now[0])
    value, headers = start(client)
    url = value['browser_url']
    token = form_token(client.get(url))
    now[0] += 601
    assert client.post(url, data={'form_token':token}, follow_redirects=False).status_code in {403,404}


def test_two_tabs_share_cookie_without_invalidating_first_form(tmp_path):
    client, store = make_service(tmp_path)
    value, headers = start(client)
    url = value['browser_url']
    first = form_token(client.get(url))
    second = form_token(client.get(url))
    assert first != second
    assert client.post(url, data={'form_token': first}, follow_redirects=False).status_code == 303
    assert client.post(url, data={'form_token': second}, follow_redirects=False).status_code in {403,409}


def test_concurrent_posts_start_only_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    client, store = make_service(tmp_path)
    value, headers = start(client)
    url = value['browser_url']
    token = form_token(client.get(url))
    cookie = '; '.join(f'{k}={v}' for k, v in client.cookies.items())
    def submit(_):
        with TestClient(client.app, base_url='https://social.example') as browser:
            return browser.post(url, data={'form_token':token}, headers={'Cookie':cookie}, follow_redirects=False).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, range(2))) == [303,409]


def test_landing_csp_allows_only_its_provider_redirect(tmp_path):
    client, store = make_service(tmp_path)
    value, headers = start(client)
    policy = client.get(value['browser_url']).headers['content-security-policy']
    form_action = next(part.strip() for part in policy.split(';') if part.strip().startswith('form-action '))
    assert form_action.split() == ['form-action', "'self'", 'https://accounts.google.com']
    assert 'https://www.facebook.com' not in form_action
    assert '*' not in form_action


def test_start_does_not_block_event_loop_while_vault_is_locked(tmp_path):
    import asyncio
    import threading
    import time
    import httpx
    client, store = make_service(tmp_path)
    value, headers = start(client)
    url = value['browser_url']
    token = form_token(client.get(url))
    cookie = '; '.join(f'{k}={v}' for k,v in client.cookies.items())
    locked = threading.Event()
    def hold_lock():
        with store.transaction():
            locked.set()
            time.sleep(.3)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='https://social.example') as browser:
            started = time.monotonic()
            task = asyncio.create_task(browser.post(url, data={'form_token':token}, headers={'Cookie':cookie}, follow_redirects=False))
            await asyncio.sleep(.03)
            elapsed = time.monotonic() - started
            assert (await task).status_code == 303
            return elapsed
    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert locked.wait(1)
    try:
        assert asyncio.run(exercise()) < .15
    finally:
        holder.join()
