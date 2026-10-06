"""Fixed-endpoint OAuth connectors; only server code sees provider app secrets."""
from __future__ import annotations

import base64
import hashlib
import math
import re
import time
from urllib.parse import urlencode

import httpx

from socialctl.connection_service.settings import Settings

GOOGLE_READ = 'https://www.googleapis.com/auth/youtube.readonly'
GOOGLE_UPLOAD = 'https://www.googleapis.com/auth/youtube.upload'
GOOGLE_MANAGEMENT = 'https://www.googleapis.com/auth/youtube.force-ssl'
GOOGLE_ANALYTICS = 'https://www.googleapis.com/auth/yt-analytics.readonly'
GRAPH = 'https://graph.facebook.com/v26.0'


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value):
        raise ValueError('account discovery failed')
    return value


def token_string(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 16384 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError('provider returned invalid credentials')
    return value


def lifetime(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('provider returned invalid expiration')
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError('provider returned invalid expiration') from None
    if not math.isfinite(seconds) or not 0 < seconds <= 366 * 86400:
        raise ValueError('provider returned invalid expiration')
    return seconds


class Providers:
    def __init__(self, settings: Settings, client: httpx.Client):
        self.settings, self.client = settings, client

    def scopes(self, platform, *, management=False, analytics=False):
        if management and platform != 'youtube':
            raise ValueError('management permission is YouTube-only')
        if analytics and platform not in {'youtube', 'facebook', 'instagram'}:
            raise ValueError('analytics permission is available for YouTube, Facebook and Instagram')
        scopes = {
            'youtube': [GOOGLE_READ, GOOGLE_UPLOAD],
            'tiktok': ['user.info.basic', 'video.upload'],
            'facebook': ['pages_show_list', 'pages_read_engagement', 'pages_manage_posts'],
            'instagram': ['pages_show_list', 'pages_read_engagement', 'instagram_basic', 'instagram_content_publish'],
        }.get(platform)
        if scopes is None:
            raise ValueError('unsupported platform')
        analytics_scope = {'youtube': GOOGLE_ANALYTICS, 'facebook': 'read_insights',
                           'instagram': 'instagram_manage_insights'}
        return scopes + ([GOOGLE_MANAGEMENT] if management else []) + ([analytics_scope[platform]] if analytics else [])

    def authorization_url(self, platform, state, verifier, *, management=False, analytics=False):
        credentials = self.settings.providers[platform]
        scopes = self.scopes(platform, management=management, analytics=analytics)
        params = {'client_id': credentials.client_id, 'redirect_uri': self.settings.callback(platform),
                  'response_type': 'code', 'scope': ' '.join(scopes), 'state': state}
        if platform == 'youtube':
            params.update(access_type='offline', prompt='consent select_account',
                          code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                          code_challenge_method='S256')
            url = 'https://accounts.google.com/o/oauth2/v2/auth'
        elif platform == 'tiktok':
            # This service uses Web Login Kit, rather than the legacy desktop flow.
            params['client_key'] = params.pop('client_id')
            params['scope'] = ','.join(scopes)
            url = 'https://www.tiktok.com/v2/auth/authorize/'
        else:
            params['scope'] = ','.join(scopes)
            params['auth_type'] = 'rerequest'
            url = 'https://www.facebook.com/v26.0/dialog/oauth'
        return url + '?' + urlencode(params)

    def request(self, method, url, **kwargs):
        try:
            # Endpoints are constructed solely inside this module. No provider
            # next-page URL, redirect or user-supplied target is followed.
            with self.client.stream(method, url, timeout=20, follow_redirects=False, **kwargs) as response:
                if response.status_code != 200:
                    raise ValueError()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError()
                import json
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError()
                error = body.get('error')
                if error and (not isinstance(error, dict) or error.get('code') != 'ok'):
                    raise ValueError()
                return body
        except Exception:
            raise ValueError('provider request failed; reconnect or check app configuration') from None

    def normalize(self, platform, body, required, *, previous=None):
        scope = body.get('scope')
        if scope is None and previous is not None:
            granted = previous['granted_scopes']
        elif isinstance(scope, str):
            granted = sorted(set(scope.replace(',', ' ').split()))
        else:
            raise ValueError('provider permissions could not be verified')
        if not set(required) <= set(granted):
            raise ValueError('required permissions were not granted')
        result = {'access_token': token_string(body.get('access_token')),
                  'refresh_token': token_string(body.get('refresh_token') or (previous or {}).get('refresh_token')),
                  'expires_at': time.time() + lifetime(body.get('expires_in')),
                  'granted_scopes': granted}
        if platform == 'tiktok':
            result['open_id'] = identifier(body.get('open_id'))
            if previous and previous.get('open_id') != result['open_id']:
                raise ValueError('authorized account changed; reconnect')
        if body.get('refresh_expires_in') is not None:
            result['refresh_expires_at'] = time.time() + lifetime(body['refresh_expires_in'])
        elif previous and previous.get('refresh_expires_at'):
            result['refresh_expires_at'] = previous['refresh_expires_at']
        return result

    def meta_selected_pages(self, credentials, user_token, required, fields):
        """Recover only explicitly approved Pages when Meta omits its accounts edge."""
        metadata = self.request('GET', f'{GRAPH}/debug_token',
            headers={'Authorization': f'Bearer {credentials.client_id}|{credentials.client_secret}'},
            params={'input_token': user_token}).get('data')
        if (not isinstance(metadata, dict) or metadata.get('is_valid') is not True
                or metadata.get('app_id') != credentials.client_id or metadata.get('type') != 'USER'):
            raise ValueError('account discovery failed; provider token could not be verified')
        granular = metadata.get('granular_scopes')
        if not isinstance(granular, list):
            raise ValueError('account discovery failed; explicit Page authorization is required')
        authorized = None
        for scope in (s for s in required if s.startswith('pages_')):
            entries = [g for g in granular if isinstance(g, dict) and g.get('scope') == scope]
            if len(entries) != 1 or not isinstance(entries[0].get('target_ids'), list):
                raise ValueError('account discovery failed; explicit Page authorization is required')
            targets = entries[0]['target_ids']
            if (not targets or len(targets) > 100 or any(
                    not isinstance(t, str) or not re.fullmatch(r'[0-9]{1,32}', t) for t in targets)):
                raise ValueError('account discovery failed; select a bounded list of Pages')
            authorized = set(targets) if authorized is None else authorized & set(targets)
        if not authorized:
            raise ValueError('account discovery failed; Page permissions do not match')
        rows = []
        for page_id in sorted(authorized):
            row = self.request('GET', f'{GRAPH}/{page_id}',
                headers={'Authorization': f'Bearer {user_token}'}, params={'fields': fields})
            if row.get('id') != page_id:
                raise ValueError('account discovery failed; Page identity does not match authorization')
            rows.append(row)
        return rows

    def exchange(self, platform, code, verifier, *, management=False, analytics=False):
        credentials = self.settings.providers[platform]
        required = self.scopes(platform, management=management, analytics=analytics)
        data = {'client_id': credentials.client_id, 'client_secret': credentials.client_secret,
                'code': code, 'grant_type': 'authorization_code', 'redirect_uri': self.settings.callback(platform)}
        if platform == 'youtube':
            data['code_verifier'] = verifier
            body = self.request('POST', 'https://oauth2.googleapis.com/token', data=data)
            grant = self.normalize(platform, body, required)
            result = self.request('GET', 'https://www.googleapis.com/youtube/v3/channels',
                                  headers={'Authorization': f"Bearer {grant['access_token']}"},
                                  params={'part': 'id,snippet', 'mine': 'true', 'maxResults': 50})
            items = result.get('items')
            if not isinstance(items, list) or len(items) != 1 or result.get('nextPageToken') or not isinstance(items[0], dict):
                raise ValueError('account discovery failed; choose one YouTube channel during Google authorization')
            row = items[0]
            accounts = [{'id': identifier(row.get('id')), 'name': str((row.get('snippet') or {}).get('title') or row['id'])[:256]}]
        elif platform == 'tiktok':
            data['client_key'] = data.pop('client_id')
            body = self.request('POST', 'https://open.tiktokapis.com/v2/oauth/token/', data=data)
            grant = self.normalize(platform, body, required)
            result = self.request('GET', 'https://open.tiktokapis.com/v2/user/info/',
                                  headers={'Authorization': f"Bearer {grant['access_token']}"},
                                  params={'fields': 'open_id,display_name'})
            row = result.get('data', {}).get('user', {})
            account_id = identifier(row.get('open_id'))
            if account_id != grant['open_id']:
                raise ValueError('account discovery failed')
            accounts = [{'id': account_id, 'name': str(row.get('display_name') or account_id)[:256]}]
        else:
            body = self.request('GET', f'{GRAPH}/oauth/access_token', params=data)
            user_token = token_string(body.get('access_token'))
            # Long-lived user-token exchange stays on the server.
            body = self.request('GET', f'{GRAPH}/oauth/access_token', params={
                'grant_type':'fb_exchange_token', 'client_id':credentials.client_id,
                'client_secret':credentials.client_secret, 'fb_exchange_token':user_token})
            user_token = token_string(body.get('access_token'))
            headers = {'Authorization': f'Bearer {user_token}'}
            permissions = self.request('GET', f'{GRAPH}/me/permissions', headers=headers).get('data')
            if not isinstance(permissions, list):
                raise ValueError('provider permissions could not be verified')
            granted = sorted({p['permission'] for p in permissions if isinstance(p, dict) and p.get('status') == 'granted' and isinstance(p.get('permission'), str)})
            if not set(required) <= set(granted):
                raise ValueError('required permissions were not granted')
            grant = {'access_token': user_token, 'expires_at': time.time() + lifetime(body.get('expires_in', 60 * 86400)), 'granted_scopes': granted}
            accounts = []
            fields = 'id,name,access_token'
            if platform == 'instagram':
                fields += ',instagram_business_account{id,username,name}'
            params = {'fields':fields, 'limit':100}
            for page in range(5):
                result = self.request('GET', f'{GRAPH}/me/accounts', headers=headers, params=params)
                rows = result.get('data')
                if not isinstance(rows, list):
                    raise ValueError('account discovery failed')
                if page == 0 and not rows and not (result.get('paging') or {}).get('next'):
                    rows = self.meta_selected_pages(credentials, user_token, required, fields)
                for row in rows:
                    page_id = identifier(row.get('id'))
                    ig = row.get('instagram_business_account')
                    if platform == 'instagram' and not ig:
                        continue
                    account_id = identifier(ig.get('id')) if platform == 'instagram' else page_id
                    name = (ig.get('username') or ig.get('name') or account_id) if platform == 'instagram' else row.get('name') or account_id
                    accounts.append({'id':account_id, 'name':str(name)[:256], 'page_id':page_id,
                                     'access_token':token_string(row.get('access_token'))})
                if not (result.get('paging') or {}).get('next'):
                    break
                cursor = (result.get('paging') or {}).get('cursors', {}).get('after')
                if not isinstance(cursor, str) or len(cursor) > 4096 or page == 4:
                    raise ValueError('too many accounts; narrow the selection during authorization')
                params['after'] = cursor
            if not accounts or len(accounts) != len({a['id'] for a in accounts}):
                raise ValueError('account discovery failed; select an eligible Page or professional Instagram account')
        return {'platform':platform, 'grant':grant, 'accounts':accounts}

    def refresh(self, platform, grant):
        if platform not in {'youtube', 'tiktok'}:
            raise ValueError('this platform requires reconnection')
        if grant.get('refresh_expires_at', float('inf')) <= time.time():
            raise ValueError('authorization expired; reconnect')
        credentials = self.settings.providers[platform]
        data = {'client_id': credentials.client_id, 'client_secret': credentials.client_secret,
                'grant_type':'refresh_token', 'refresh_token':token_string(grant.get('refresh_token'))}
        url = 'https://oauth2.googleapis.com/token'
        if platform == 'tiktok':
            data['client_key'] = data.pop('client_id')
            url = 'https://open.tiktokapis.com/v2/oauth/token/'
        body = self.request('POST', url, data=data)
        return self.normalize(platform, body, grant['granted_scopes'], previous=grant)
