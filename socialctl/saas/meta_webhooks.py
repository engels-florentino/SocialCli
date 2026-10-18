"""Opt-in Meta callbacks: encrypted owner-bound hints, never social operations.

Enrollment only reads existing provider subscriptions. Account owners consent in
our browser; callback payloads cannot enroll accounts or perform social writes.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
from html import escape

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from socialctl.management.meta_client import GRAPH
from socialctl.management.meta_schema import MetaError
from socialctl.management.meta_webhooks import verify_challenge
from socialctl.media_registry import parse_json
from socialctl.saas import consent, pages
from socialctl.saas.routes import read_form

PATH = '/api/saas/v1/meta/webhook'
BINDING = 'saas-meta-webhook-binding'
EVENT = 'saas-meta-webhook-event'
RETENTION = 6 * 86400
LEASE = 86400
MAX_BODY = 1024 * 1024
OWNER_LIMIT = 2000
GLOBAL_LIMIT = 20000
BINDING_LIMIT = 10000
FIELDS = {'facebook': {'feed'}, 'instagram': {'comments', 'mentions'}}
CONSENT_VERSION = 'meta-hints-2026-10-18'


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _id(value, *, composite=False):
    pattern = r'[0-9]{1,128}(?:_[0-9]{1,128})?' if composite else r'[0-9]{1,128}'
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else None


def _unavailable():
    raise HTTPException(409, 'webhook_binding_unverified: Review the current account and existing Meta subscriptions.')


class MetaWebhooks:
    def __init__(self, connections, public_url, clock):
        self.connections, self.vault, self.clock = connections, connections.vault, clock
        self.callback = public_url + PATH
        self.verify_token = os.environ.get('SOCIALCLI_META_WEBHOOK_VERIFY_TOKEN', '')
        self.previous_secret = os.environ.get('SOCIALCLI_META_WEBHOOK_PREVIOUS_SECRET', '')
        try:
            self.previous_until = float(os.environ.get('SOCIALCLI_META_WEBHOOK_PREVIOUS_UNTIL', '0'))
            if (len(self.verify_token) > 512 or bool(self.previous_secret) != bool(self.previous_until)
                    or self.previous_secret and (not math.isfinite(self.previous_until)
                        or not clock() < self.previous_until <= clock()+36*3600)):
                raise ValueError()
        except ValueError:
            raise ValueError('Invalid bounded Meta webhook configuration.') from None

    def credentials(self, platform=None):
        if not self.verify_token:
            raise HTTPException(503, 'webhook_disabled: Hosted Meta delivery is not enabled.')
        available = self.connections.providers.settings.providers
        configured = [available[p] for p in FIELDS if p in available]
        if (not configured or len({(p.client_id, p.client_secret) for p in configured}) != 1
                or platform is not None and platform not in available):
            raise HTTPException(503, 'webhook_configuration_unavailable: Review Meta app configuration.')
        return available[platform] if platform else configured[0]

    def current(self, db, user_id, platform):
        user = self.connections.metadata.user(db, user_id)
        slot = db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',
                          (user_id, platform)).fetchone()
        grant = self.vault.get(db, 'saas-grant', slot['grant_id']) if slot else None
        if (user is None or user['deleting'] or user['suspended'] or grant is None
                or grant['expires'] <= self.clock() or grant['payload'].get('user_id') != user_id
                or grant['payload'].get('platform') != platform):
            _unavailable()
        consent.require_privacy(self.connections.metadata, db, user_id)
        return slot['grant_id'], grant

    def purge(self, db):
        db.execute('DELETE FROM records WHERE kind IN (?,?) AND expires<=?',
                   (BINDING, EVENT, self.clock()))

    def register(self, user_id, platform, wanted):
        """Called only after browser-owned explicit consent and CSRF validation."""
        credentials = self.credentials(platform)
        if not wanted or not wanted <= FIELDS.get(platform, set()):
            raise HTTPException(400, 'Select supported webhook fields.')
        with self.vault.read_transaction() as db:
            grant_id, record = self.current(db, user_id, platform)
            self.connections.allowed(db, user_id)
        payload = record['payload']; account = payload['account']; grant = payload['grant']
        page_id = account.get('page_id') if platform == 'instagram' else account.get('id')
        if not _id(page_id) or not _id(account.get('id')):
            _unavailable()
        token = grant.get('access_token')
        if not isinstance(token, str) or not token or grant.get('expires_at', 0) <= self.clock():
            _unavailable()
        providers = self.connections.providers
        app_headers = {'Authorization': f'Bearer {credentials.client_id}|{credentials.client_secret}'}
        headers = {'Authorization': 'Bearer ' + token}
        try:
            metadata = providers.request('GET', GRAPH+'/debug_token', headers=app_headers,
                params={'input_token': token}).get('data')
            required = {'pages_manage_metadata', 'pages_read_engagement'}
            if platform == 'instagram': required |= {'instagram_basic', 'instagram_manage_comments'}
            if (not isinstance(metadata, dict) or metadata.get('is_valid') is not True
                    or metadata.get('app_id') != credentials.client_id or metadata.get('type') not in {None, 'PAGE'}
                    or metadata.get('profile_id') != page_id
                    or not isinstance(metadata.get('scopes'), list)
                    or any(not isinstance(scope, str) for scope in metadata['scopes'])
                    or not required <= set(metadata['scopes'])):
                _unavailable()
            actor = providers.request('GET', GRAPH+'/me', headers=headers,
                params={'fields': 'id,instagram_business_account' if platform == 'instagram' else 'id'})
            if (actor.get('id') != page_id or platform == 'instagram'
                    and (actor.get('instagram_business_account') or {}).get('id') != account['id']):
                _unavailable()
            subscriptions = providers.request('GET', GRAPH+'/'+credentials.client_id+'/subscriptions',
                                               headers=app_headers)
            obj = 'page' if platform == 'facebook' else 'instagram'
            rows = subscriptions.get('data')
            matches = [s for s in rows if isinstance(s, dict) and s.get('object') == obj] if isinstance(rows, list) else []
            if (len(matches) != 1 or matches[0].get('active') is not True
                    or matches[0].get('callback_url') != self.callback
                    or not isinstance(matches[0].get('fields'), list)
                    or not wanted <= {f.get('name') for f in matches[0].get('fields', []) if isinstance(f, dict)}):
                _unavailable()
            installed = providers.request('GET', GRAPH+'/'+page_id+'/subscribed_apps', headers=headers,
                                          params={'fields': 'id,subscribed_fields', 'limit': 100})
            rows = installed.get('data')
            matches = [s for s in rows if isinstance(s, dict) and s.get('id') == credentials.client_id] if isinstance(rows, list) else []
            # Facebook Login IG delivery also requires installation on the linked
            # Page with at least one Page field. This candidate requires feed.
            if (len(matches) != 1 or not isinstance(matches[0].get('subscribed_fields'), list)
                    or any(not isinstance(field, str) for field in matches[0]['subscribed_fields'])
                    or 'feed' not in matches[0]['subscribed_fields']):
                _unavailable()
        except (ValueError, TypeError, KeyError, MetaError):
            _unavailable()
        key = _hash([obj, account['id']])
        with self.vault.transaction() as db:
            self.purge(db)
            current_id, current = self.current(db, user_id, platform)
            if (current_id != grant_id or current['payload'] != payload):
                _unavailable()
            existing = self.vault.get(db, BINDING, key)
            if existing and (existing['payload']['user_id'], existing['payload']['grant_id']) != (user_id, grant_id):
                _unavailable()
            if not existing and db.execute('SELECT count(*) FROM records WHERE kind=?', (BINDING,)).fetchone()[0] >= BINDING_LIMIT:
                raise HTTPException(503, 'webhook_capacity: Try again after operator review.')
            self.vault.put(db, BINDING, key, {'user_id': user_id, 'grant_id': grant_id,
                'platform': platform, 'account_id': account['id'], 'page_id': page_id,
                'object': obj, 'fields': sorted(wanted), 'verified_at': self.clock(),
                'consent': {'version': CONSENT_VERSION, 'source': 'owner_browser', 'accepted_at': self.clock()}},
                expires=min(record['expires'], grant['expires_at'], self.clock()+LEASE))

    def binding(self, db, obj, account_id):
        binding = self.vault.get(db, BINDING, _hash([obj, account_id]))
        if binding is None or binding['expires'] <= self.clock(): return None
        data = binding['payload']
        try:
            grant_id, grant = self.current(db, data['user_id'], data['platform'])
        except HTTPException:
            return None
        if (grant_id != data['grant_id'] or grant['payload']['account'].get('id') != account_id
                or (data['platform'] == 'instagram' and grant['payload']['account'].get('page_id') != data['page_id'])
                or data['consent']['version'] != CONSENT_VERSION):
            return None
        return data

    def ingest(self, raw, signature):
        credentials = self.credentials()
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY:
            raise HTTPException(413, 'Invalid or excessive webhook body.')
        secrets = [credentials.client_secret]
        if self.previous_secret and self.clock() < self.previous_until: secrets.append(self.previous_secret)
        if (not isinstance(signature, str) or not re.fullmatch(r'sha256=[0-9a-f]{64}', signature)
                or not any(hmac.compare_digest(signature, 'sha256='+hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()) for secret in secrets)):
            raise HTTPException(403, 'Webhook signature rejected.')
        try:
            data = parse_json(raw, limit=MAX_BODY)
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(400, 'Invalid webhook JSON.') from None
        if (not isinstance(data, dict) or data.get('object') not in {'page', 'instagram'}
                or not isinstance(data.get('entry'), list) or not 1 <= len(data['entry']) <= 1000):
            raise HTTPException(400, 'Invalid webhook envelope.')
        size = sum(len(e.get('changes', [])) if isinstance(e, dict) and isinstance(e.get('changes'), list) else 0 for e in data['entry'])
        if size > 1000: raise HTTPException(413, 'Too many webhook updates.')
        counts = {'accepted': 0, 'duplicates': 0, 'out_of_order': 0, 'ignored': 0}
        with self.vault.transaction() as db:
            self.purge(db)
            global_count = db.execute('SELECT count(*) FROM records WHERE kind=?', (EVENT,)).fetchone()[0]
            owners = {}
            for entry in data['entry']:
                if not isinstance(entry, dict): counts['ignored'] += 1; continue
                binding = self.binding(db, data['object'], entry.get('id'))
                timestamp = entry.get('time'); changes = entry.get('changes')
                if (binding is None or type(timestamp) is not int
                        or not self.clock()-RETENTION <= timestamp <= self.clock()+300
                        or not isinstance(changes, list)):
                    counts['ignored'] += 1; continue
                for change in changes:
                    hint = self.hint(binding, change)
                    if hint is None: counts['ignored'] += 1; continue
                    identifier = _hash([binding['user_id'], binding['grant_id'], timestamp, change])
                    if self.vault.get(db, EVENT, identifier): counts['duplicates'] += 1; continue
                    owner = binding['user_id']
                    if owner not in owners:
                        rows = db.execute('SELECT id FROM saas_record_owners WHERE user_id=? AND kind=?',
                                          (owner, EVENT)).fetchall()
                        latest_times = {}
                        for row in rows:
                            saved = self.vault.get(db, EVENT, row['id'])['payload']
                            target = (saved['grant_id'], saved['target_id'])
                            latest_times[target] = max(latest_times.get(target, -1), saved['source_time'])
                        owners[owner] = [len(rows), latest_times]
                    count, latest_times = owners[owner]
                    if count >= OWNER_LIMIT or global_count >= GLOBAL_LIMIT:
                        raise HTTPException(503, 'webhook_capacity: Delivery was not accepted; retry later.')
                    target = (binding['grant_id'], hint['target_id'])
                    latest = latest_times.get(target, -1)
                    late = timestamp < latest
                    self.vault.put(db, EVENT, identifier, {**hint, 'user_id': binding['user_id'],
                        'grant_id': binding['grant_id'], 'platform': binding['platform'], 'account_id': binding['account_id'],
                        'source_time': timestamp, 'received_at': self.clock(), 'out_of_order': late,
                        'consent': binding['consent'],
                        'state': 'pending_read'}, expires=self.clock()+RETENTION)
                    owners[owner][0] += 1; latest_times[target] = max(latest, timestamp)
                    counts['accepted'] += 1; counts['out_of_order'] += int(late); global_count += 1
        return counts

    @staticmethod
    def hint(binding, change):
        if not isinstance(change, dict) or change.get('field') not in binding['fields']: return None
        value = change.get('value')
        if not isinstance(value, dict): return None
        field = change['field']
        media = value.get('media')
        target = value.get('post_id') if field == 'feed' else value.get('media_id', media.get('id') if isinstance(media, dict) else None)
        if not _id(target, composite=field == 'feed'): return None
        comment = value.get('comment_id') if field in {'feed', 'mentions'} else value.get('id')
        if comment is not None and not _id(comment, composite=field == 'feed'): return None
        # Caption/media mentions carry only media_id; comment mentions also
        # carry comment_id. Keep the distinction for the subsequent owned GET.
        if field == 'comments' and comment is None: return None
        return {'field': field, 'target_id': target, 'comment_id': comment}

    def authorized(self, db, connection_id, request):
        cap = self.connections.capability(db, connection_id, request)['payload']
        if cap['platform'] not in FIELDS: raise HTTPException(403, 'A Meta connection is required.')
        self.connections.allowed(db, cap['user_id'])
        grant_id, _ = self.current(db, cap['user_id'], cap['platform'])
        if grant_id != cap['grant_id']: _unavailable()
        return cap

    def events(self, connection_id, request, limit):
        if not 1 <= limit <= 100: raise HTTPException(400, 'Event limit must be between 1 and 100.')
        with self.vault.transaction() as db:
            self.purge(db)
            cap = self.authorized(db, connection_id, request)
            result = []
            for row in db.execute('SELECT id FROM saas_record_owners WHERE user_id=? AND kind=? ORDER BY id', (cap['user_id'], EVENT)):
                saved = self.vault.get(db, EVENT, row['id']); hint = saved['payload']
                if (hint['grant_id'] == cap['grant_id'] and hint['account_id'] == cap['account']['id']
                        and hint['state'] == 'pending_read'):
                    result.append({'id': row['id'], **{k:v for k,v in hint.items() if k not in {'user_id', 'grant_id', 'consent'}}})
                if len(result) >= limit: break
        return {'version': 1, 'events': result, 'remote_writes': 0, 'requires_owned_get': True}

    def ack(self, connection_id, request, event_id):
        with self.vault.transaction() as db:
            cap = self.authorized(db, connection_id, request)
            saved = self.vault.get(db, EVENT, event_id)
            if (saved is None or saved['expires'] <= self.clock()
                    or saved['payload']['user_id'] != cap['user_id'] or saved['payload']['grant_id'] != cap['grant_id']):
                raise HTTPException(404, 'Event unavailable.')
            saved['payload']['state'] = 'consumed_hint'
            self.vault.put(db, EVENT, event_id, saved['payload'], expires=saved['expires'])
        return {'version': 1, 'status': 'acknowledged_hint', 'remote_writes': 0}


def install_meta_webhook_routes(app, connections, sessions, settings, clock):
    receiver = MetaWebhooks(connections, settings.public_url, clock)
    app.state.saas_meta_webhooks = receiver

    @app.get(PATH, response_class=PlainTextResponse)
    def challenge(request: Request):
        receiver.credentials()
        keys = ('hub.mode', 'hub.verify_token', 'hub.challenge')
        if any(len(request.query_params.getlist(k)) != 1 for k in keys):
            raise HTTPException(403, 'Webhook verification rejected.')
        try:
            return verify_challenge(*(request.query_params[k] for k in keys), receiver.verify_token)
        except MetaError:
            raise HTTPException(403, 'Webhook verification rejected.') from None

    @app.post(PATH)
    async def receive(request: Request):
        receiver.credentials()
        if len(request.headers.getlist('x-hub-signature-256')) != 1:
            raise HTTPException(403, 'Webhook signature rejected.')
        raw = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(raw)+len(chunk) > MAX_BODY: raise HTTPException(413, 'Webhook body is too large.')
                    raw.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, 'Webhook body read timed out.') from None
        if not raw: raise HTTPException(400, 'Webhook body is empty.')
        return await asyncio.to_thread(receiver.ingest, bytes(raw), request.headers.get('x-hub-signature-256'))

    @app.get('/api/saas/v1/connections/{connection_id}/meta/webhook-events')
    def events(connection_id: str, request: Request, limit: int = 100):
        return receiver.events(connection_id, request, limit)

    @app.post('/api/saas/v1/connections/{connection_id}/meta/webhook-events/{event_id}/ack')
    def ack(connection_id: str, event_id: str, request: Request):
        return receiver.ack(connection_id, request, event_id)

    @app.get('/account/connections/{platform}/webhooks', response_class=HTMLResponse)
    def enrollment(platform: str, request: Request):
        sessions.require_user(request)
        receiver.credentials(platform)
        if platform not in FIELDS: raise HTTPException(404, 'Meta platform unavailable.')
        checks = ''.join(f'<label><input type="checkbox" name="field_{field}" value="1"> {field}</label>' for field in sorted(FIELDS[platform]))
        content = ('<h1 id="page-title">Enable Meta event hints</h1><p>This stores encrypted account and content IDs '
            'for up to six days. No comment text or author profiles are retained. Your linked assistants can read '
            'these hints and must verify content ownership before acting. Disconnecting removes hints. '
            'This does not subscribe your account at Meta or approve social actions. Existing verified subscriptions are required.</p>'
            f'<form method="post" action="/account/connections/{escape(platform)}/webhooks">'
            +pages.hidden('csrf', app.state.saas_csrf(request, 'meta-webhooks:'+platform))
            +pages.hidden('webhook_consent_version', CONSENT_VERSION)+checks
            +'<label><input type="checkbox" name="webhook_accept" value="1" required> Allow this event storage and assistant access</label>'
            +'<button type="submit">Enable verified hints for one day</button></form>')
        return HTMLResponse(pages.page('Meta event hints', content))

    @app.post('/account/connections/{platform}/webhooks')
    async def enroll(platform: str, request: Request):
        form = await read_form(request, settings.public_url)
        app.state.saas_valid_form(request, form, 'meta-webhooks:'+platform)
        user = await asyncio.to_thread(sessions.require_user, request)
        if form.get('webhook_accept') != '1' or form.get('webhook_consent_version') != CONSENT_VERSION:
            raise HTTPException(400, 'Review and accept Meta event storage before enabling it.')
        wanted = {field for field in FIELDS.get(platform, set()) if form.get('field_'+field) == '1'}
        await asyncio.to_thread(receiver.register, user.id, platform, wanted)
        return RedirectResponse('/account/', status_code=303)
