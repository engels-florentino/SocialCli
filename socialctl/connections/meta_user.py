"""Owned Instagram User operations through SaaS; User tokens never leave it."""
from socialctl.connections.client import broker_for, opaque, account_id
from socialctl.connections import keychain
from socialctl.connections.saas_client import ServiceRejected
from socialctl.management.meta_schema import MetaError, MetaRejected, MetaUncertain, meta_id


class RemoteMetaUser:
    def __init__(self, brand, platform, client):
        self.brand, self.platform, self.client = brand, platform, client

    def request(self, phase, edit, *, change=None):
        writing = phase == 'mutate'
        try:
            if (self.brand.dir_secretos / 'socialcli-rotation.json').exists():
                raise MetaError('Credential rotation is pending; finish it before management operations.')
            metadata = self.brand.leer_secreto(self.platform)
            expected = opaque(metadata.get('account_id'))
            if (metadata.get('auth_mode') != 'broker' or metadata.get('protocol') != 'saas-v1'
                    or expected != account_id(self.brand, self.platform)):
                raise MetaError('Instagram management connection does not match the selected account.')
            connection = opaque(metadata.get('connection_id'))
            secret = keychain.get(self.brand, self.platform, metadata)
            if not isinstance(secret, str) or not 16 <= len(secret) <= 512:
                raise MetaError('Connection credential unavailable in the selected secure store.')
            body = {'phase': phase, 'edit': edit.model_dump(exclude_none=True)}
            if phase in {'mutate', 'status'}:
                if change is None:
                    raise MetaError('Remote mutation requires the approved durable ChangeSet.')
                body.update(operation_id=change.id, expected_identity=change.identity, expected_before=change.before)
            result = broker_for(metadata, self.client).request('POST',
                f'/v1/connections/{connection}/instagram-management', secret=secret, body=body)
            identity = result.get('identity')
            page_id = (self.brand.cuentas.get('facebook') or {}).get('page_id')
            if (result.get('platform') != 'instagram' or result.get('account', {}).get('id') != expected
                    or not isinstance(identity, dict) or set(identity) != {'actor_id', 'token_mode', 'page_id', 'account_id'}
                    or identity.get('token_mode') != 'facebook_user' or identity.get('account_id') != expected
                    or identity.get('page_id') != page_id):
                raise ValueError('Account mismatch')
            meta_id(identity['actor_id'])
            if phase == 'inspect' and not isinstance(result.get('before'), dict):
                raise ValueError('Missing observation')
            if phase in {'mutate', 'status'}:
                receipt = result.get('receipt')
                if edit.action == 'media-delete':
                    if receipt != {'success': True, 'target_id': edit.target_id}:
                        raise ValueError('Invalid receipt')
                elif not isinstance(receipt, dict) or set(receipt) != {'id', 'accepted'} or receipt['accepted'] is not True:
                    raise ValueError('Invalid receipt')
                else:
                    meta_id(receipt['id'])
            return result
        except MetaError:
            raise
        except ServiceRejected as exc:
            error = MetaUncertain if writing and (exc.status >= 500 or exc.status == 429 or exc.code == 'operation_uncertain') else MetaRejected if writing else MetaError
            failure = error(str(exc))
            failure._socialcli_error_code = exc.code
            raise failure from None
        except Exception:
            raise (MetaUncertain if writing else MetaError)(
                'Instagram management response unavailable or invalid; no automatic retry.') from None
