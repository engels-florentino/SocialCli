"""Account-bound, explicitly piloted Instagram worker composition."""
import json
from datetime import timedelta

from socialctl.management.meta_client import MetaClient
from socialctl.models import Platform
from .approval import reject_secrets, verify_approval
from .instagram_delivery import InstagramDelivery


def delivery_context(brand, job, payload, *, path, evidence_path, client, clock):
    if path is None or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError('Instagram delivery requires account-specific pilot evidence')
    proof = json.loads(path.read_text())
    reject_secrets(proof)
    fields = {'version', 'platform', 'account_id', 'delivery_mode', 'api_enabled',
              'account_pilot_verified', 'formats', 'allowed_media_hosts', 'evidence_ref'}
    if (not isinstance(proof, dict) or set(proof) != fields
            or type(proof['version']) is not int or proof['version'] != 1
            or proof['platform'] != 'instagram' or proof['account_id'] != job.account_id
            or proof['delivery_mode'] != 'at_time'
            or proof['api_enabled'] is not True or proof['account_pilot_verified'] is not True
            or not isinstance(proof['formats'], list) or not proof['formats']
            or any(fmt not in {'reel', 'story', 'image', 'carousel'} for fmt in proof['formats'])
            or payload.get('options', {}).get('format') not in proof['formats']
            or not isinstance(proof['allowed_media_hosts'], list)
            or any(not isinstance(host, str) or not host for host in proof['allowed_media_hosts'])
            or not isinstance(proof['evidence_ref'], str)):
        raise ValueError('Invalid or mismatched Instagram delivery eligibility')
    evidence = evidence_path(proof['evidence_ref'])
    if not evidence.is_file() or evidence.stat().st_size == 0:
        raise ValueError('Instagram delivery requires recorded account and format evidence')
    if (payload.get('delivery_mode') != 'at_time' or payload.get('visibility') != 'public'
            or set(payload['options']) - {'format', 'share_to_facebook_story'}
            or payload['options'].get('share_to_facebook_story', False) is not False):
        raise ValueError('Unsupported Instagram delivery mode, visibility or options')
    if payload.get('first_comment') and payload['options']['format'] == 'story':
        raise ValueError('Instagram Story first comments are unsupported')
    identity = MetaClient(brand, Platform.INSTAGRAM, client)
    required={'instagram_basic','instagram_content_publish','pages_read_engagement'}
    credential=brand.leer_secreto(Platform.INSTAGRAM)
    if credential.get('auth_mode')=='broker':
        from socialctl.connections.client import broker_access_token
        identity._token=broker_access_token(brand,Platform.INSTAGRAM,client,
            refresh=False,required_scopes=required)
    else:
        from socialctl.identity import credential_metadata
        granted=credential_metadata(credential)['granted_scopes']
        if granted['state']!='known' or not required<=set(granted['values']):
            raise ValueError('Required granted Instagram publishing permissions are missing or unknown')
    if identity.identity()['account_id'] != job.account_id:
        raise ValueError('Authenticated Instagram account differs from the approved job')

    def before_publish():
        now = clock()
        if not job.publish_at <= now < job.publish_at + timedelta(minutes=5):
            raise ValueError('Approved publication window expired; container will not be published late')
        verify_approval(job, payload)

    adapter = InstagramDelivery(client, identity._token,
        allowed_media_hosts=frozenset(proof['allowed_media_hosts']), before_publish=before_publish)
    # Granted scopes were checked separately from the operator pilot record.
    # Neither establishes production approval; the provider governs each call.
    capability = {'route': 'api', 'verified_account': job.account_id,
        'verified_format': payload['options']['format'], 'grant_verified': True, 'remote_verified': True}
    return adapter, capability
