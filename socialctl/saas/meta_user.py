"""Narrow server-side User-token route; no generic Graph proxy or token export."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, model_validator

from socialctl.management.meta_client import GRAPH, MetaClient
from socialctl.management.meta_schema import MetaEdit, MetaError, MetaRejected, meta_id
from socialctl.models import Platform

USER_ACTIONS = {'media-delete': {'instagram_basic', 'instagram_manage_contents'},
                'mention-reply': {'instagram_basic', 'instagram_manage_comments',
                                  'pages_read_engagement', 'pages_show_list'}}
RETAIN_SECONDS = 90 * 86400


class UserOperation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    phase: Literal['identity', 'inspect', 'mutate', 'status']
    edit: MetaEdit
    operation_id: str | None = None
    expected_identity: dict | None = None
    expected_before: dict | None = None

    @model_validator(mode='after')
    def exact_operation(self):
        if self.edit.action not in USER_ACTIONS:
            raise ValueError('Only owned media deletion and exact mention replies are supported')
        if self.phase in {'mutate', 'status'}:
            if self.expected_identity is None or self.expected_before is None or self.operation_id is None:
                raise ValueError('Mutation requires an exact operation ID and approved observations')
            if str(UUID(self.operation_id)) != self.operation_id:
                raise ValueError('Invalid operation ID')
        elif any(v is not None for v in (self.operation_id, self.expected_identity, self.expected_before)):
            raise ValueError('Read phases do not accept mutation fields')
        return self


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def install_meta_user_route(app, connections, clock):
    vault, providers = connections.vault, connections.providers

    def authorized(db, connection_id, request):
        cap = connections.capability(db, connection_id, request)['payload']
        connections.allowed(db, cap['user_id'])
        if cap['platform'] != 'instagram':
            raise HTTPException(403, 'platform_not_allowed: An Instagram connection is required.')
        record = vault.get(db, 'saas-grant', cap['grant_id'])
        if record is None or record['expires'] <= clock():
            raise HTTPException(409, 'connection_inactive: Review the current connection.')
        return cap, record['payload']['grant']

    def existing(db, key, digest):
        row = vault.get(db, 'saas-meta-operation', key)
        if row is None:
            return None
        result = row['payload']
        if result['digest'] != digest:
            raise HTTPException(409, 'operation_conflict: Operation ID has different approved content.')
        if result['status'] == 'accepted':
            return result['response']
        if result['status'] == 'rejected':
            raise HTTPException(400, 'operation_rejected: The provider rejected this operation; it was not repeated.')
        raise HTTPException(409, 'operation_uncertain: Inspect the existing operation; never repeat the write.')

    def user_client(cap, grant, action):
        user = grant.get('meta_user_grant')
        if not isinstance(user, dict) or not isinstance(user.get('access_token'), str):
            raise HTTPException(409, 'user_authorization_required: Reconnect Instagram with owner-approved management consent.')
        expiry = user.get('expires_at')
        if type(expiry) not in {int, float} or expiry <= clock() + 60:
            raise HTTPException(409, 'provider_authorization_expired: Instagram user authorization expired.')
        if not USER_ACTIONS[action] <= set(user.get('granted_scopes') or []):
            raise HTTPException(409, 'provider_permissions_changed: Instagram management permissions are missing.')
        account = cap['account']
        actor = providers.request('GET', f'{GRAPH}/me',
            headers={'Authorization': 'Bearer ' + user['access_token']}, params={'fields': 'id'})
        secret = {**user, 'token_mode': 'facebook_user', 'actor_id': meta_id(actor.get('id'))}
        brand = SimpleNamespace(cuentas={'facebook': {'page_id': account['page_id']},
            'instagram': {'ig_user_id': account['id']}}, leer_secreto=lambda platform: secret)

        def explicit_page_authorization(actor_id):
            # Facebook Login can return an empty /me/accounts for explicitly
            # selected assets. Require fresh app/user/action-specific granular
            # grants and a current Page->Instagram link, never guessed tasks.
            credentials = providers.settings.providers['instagram']
            metadata = providers.request('GET', f'{GRAPH}/debug_token',
                headers={'Authorization': f'Bearer {credentials.client_id}|{credentials.client_secret}'},
                params={'input_token': user['access_token']}).get('data')
            required = USER_ACTIONS[action] | {'pages_show_list', 'pages_read_engagement'}
            if (not isinstance(metadata, dict) or metadata.get('is_valid') is not True
                    or metadata.get('type') != 'USER' or metadata.get('app_id') != credentials.client_id
                    or metadata.get('user_id') != actor_id or not isinstance(metadata.get('scopes'), list)
                    or not required <= set(metadata['scopes'])):
                raise MetaError('Current USER app and permission binding could not be verified')
            granular = metadata.get('granular_scopes')
            if not isinstance(granular, list) or not 1 <= len(granular) <= 100:
                raise MetaError('Explicit asset permission grants are required')
            for scope in required:
                entries = [g for g in granular if isinstance(g, dict) and g.get('scope') == scope]
                if len(entries) != 1:
                    raise MetaError('Explicit asset permission grant is missing or ambiguous')
                targets = entries[0].get('target_ids')
                expected = account['page_id'] if scope.startswith('pages_') else account['id']
                if (not isinstance(targets, list) or not 1 <= len(targets) <= 100
                        or any(meta_id(target) != target for target in targets) or expected not in targets):
                    raise MetaError('Permission was not granted for the selected asset')
            page = providers.request('GET', f'{GRAPH}/{account["page_id"]}',
                headers={'Authorization': 'Bearer ' + user['access_token']},
                params={'fields': 'id,instagram_business_account'})
            association = page.get('instagram_business_account')
            if (page.get('id') != account['page_id'] or not isinstance(association, dict)
                    or association.get('id') != account['id']):
                raise MetaError('Current Page and Instagram association could not be verified')
            return page

        client = MetaClient(brand, Platform.INSTAGRAM, providers.client,
                            user_page_authorizer=explicit_page_authorization)
        # Validate fresh permissions as well as the saved grant. Identity then
        # verifies Page authorization and the exact linked Instagram account.
        perms = client._pages('me/permissions', {'limit': '100'})
        scopes = {p.get('permission') for p in perms['data'] if p.get('status') == 'granted'}
        if not perms['complete'] or not USER_ACTIONS[action] <= scopes:
            raise HTTPException(409, 'provider_permissions_changed: Instagram management permissions are missing.')
        return client

    @app.post('/api/saas/v1/connections/{connection_id}/instagram-management')
    def operation(connection_id: str, body: UserOperation, request: Request):
        digest = _digest({**body.model_dump(mode='json'), 'phase': 'mutate'})
        with vault.transaction() as db:
            cap, grant = authorized(db, connection_id, request)
            # A replacement grant must not erase the same owner's account history.
            owner = [cap['user_id'], cap['platform'], cap['account']['id']]
            key = _digest([*owner, body.operation_id])
            target_key = _digest([*owner, body.edit.target_id])
            if body.phase in {'mutate', 'status'}:
                previous = existing(db, key, digest)
                if previous is not None:
                    return previous
                if body.phase == 'status':
                    raise HTTPException(404, 'operation_unavailable: No durable receipt is available; do not resend.')
        try:
            client = user_client(cap, grant, body.edit.action)
            identity = client.identity(user_required=body.edit.action == 'media-delete')
            result = {'version': 1, 'platform': 'instagram', 'account': cap['account'], 'identity': identity}
            if body.phase == 'identity':
                return result
            before = client.snapshot(body.edit)
            if body.phase == 'inspect':
                return {**result, 'before': before}
            if identity != body.expected_identity or before != body.expected_before:
                raise HTTPException(409, 'operation_conflict: Account or resource changed since the preview.')
        except HTTPException:
            raise
        except (MetaError, ValueError, KeyError, TypeError):
            raise HTTPException(400, 'operation_rejected: Instagram ownership or authorization could not be verified; no write sent.') from None

        # Recheck capability after network reads and claim the resource before
        # the remote write. Different operation IDs cannot evade an uncertain one.
        with vault.transaction() as db:
            current, _ = authorized(db, connection_id, request)
            if current != cap:
                raise HTTPException(409, 'connection_changed: Review the current connection.')
            previous = existing(db, key, digest)
            if previous is not None:
                return previous
            target = vault.get(db, 'saas-meta-target', target_key)
            if target is not None:
                prior = vault.get(db, 'saas-meta-operation', target['payload']['operation_key'])
                if prior is None or prior['payload']['status'] in {'intent', 'uncertain'}:
                    raise HTTPException(409, 'operation_uncertain: An earlier write on this resource needs review.')
            ownership = {'user_id': cap['user_id'], 'grant_id': cap['grant_id'],
                         'platform': cap['platform'], 'account_id': cap['account']['id']}
            saved = {**ownership, 'digest': digest, 'status': 'intent'}
            vault.put(db, 'saas-meta-operation', key, saved, expires=clock() + RETAIN_SECONDS)
            vault.put(db, 'saas-meta-target', target_key,
                {**ownership, 'operation_key': key}, expires=clock() + RETAIN_SECONDS)
        try:
            receipt = client.mutate(body.edit)
            response = {**result, 'receipt': receipt}
            saved.update(status='accepted', response=response)
        except MetaRejected:
            saved['status'] = 'rejected'
        except Exception:
            # No provider exception text or access token enters the API response.
            saved['status'] = 'uncertain'
        with vault.transaction() as db:
            # Account/grant deletion can race the provider response. Never
            # recreate an owner's records after their deletion request.
            try:
                authorized(db, connection_id, request)
            except HTTPException:
                # Authorization disappearing after dispatch does not mean
                # Meta rejected the write. Preserve the no-replay verdict.
                raise HTTPException(409, 'operation_uncertain: Authorization changed after dispatch; do not repeat.') from None
            if vault.get(db, 'saas-meta-operation', key) is None:
                raise HTTPException(409, 'operation_uncertain: Operation storage was removed after dispatch; do not repeat.')
            vault.put(db, 'saas-meta-operation', key, saved, expires=clock() + RETAIN_SECONDS)
        if saved['status'] == 'rejected':
            raise HTTPException(400, 'operation_rejected: Instagram rejected the write.')
        if saved['status'] != 'accepted':
            raise HTTPException(409, 'operation_uncertain: Instagram write outcome is uncertain; do not repeat.')
        return response
