"""Provider receipt IDs must remain usable without guessing comment aliases."""
import json

import pytest

from tests.test_meta_comments import service
from socialctl.operation_output import OperationOutput


@pytest.mark.parametrize('action', ['reply', 'edit', 'delete'])
def test_receipt_alias_can_prepare_followup_with_verified_canonical_target(tmp_path, action):
    mod, _, graph, client, store = service(tmp_path)
    url = 'https://www.facebook.com/reel/789/?comment_id=800'
    graph.comments['789_800'] = {**graph.row('789_800', 'Parent'), 'permalink_url': url}
    graph.comments['800'] = {**graph.row('800', 'Parent'), 'permalink_url': url}
    graph.pages = lambda r: {'data': [row for key, row in graph.comments.items()
        if key != '789_800' and (row.get('parent') or {}).get('id') ==
        ('800' if r.url.path.endswith('/800/comments') else '789')]}
    args = {'parent_id': '789_800'} if action == 'reply' else {'comment_id': '789_800'}
    change = mod.prepare_comment(client, store, media_id='789', action=action,
        text=None if action == 'delete' else 'Follow-up', **args)
    assert (change.parent_id if action == 'reply' else change.comment_id) == '800'
    assert change.before['parent' if action == 'reply' else 'comment']['id'] == '800'
    assert not graph.writes
    result = mod.apply_comment(client, store, change.id, change.fingerprint)
    assert result.status == ('applied_unverified' if action == 'delete' else 'verified')
    assert graph.writes[0][1] == ('800/comments' if action == 'reply' else '800')
    mod.apply_comment(client, store, change.id, change.fingerprint)
    assert len(graph.writes) == 1


@pytest.mark.parametrize('fault', ['duplicate', 'actor', 'text', 'parent', 'object', 'receipt', 'base_link', 'missing_actor'])
def test_receipt_alias_requires_unambiguous_identity_and_owned_membership(tmp_path, fault):
    mod, _, graph, client, _ = service(tmp_path)
    url = 'https://www.facebook.com/reel/789/?comment_id=800'
    direct = {**graph.row('789_800', 'Parent'), 'permalink_url': url}
    canonical = {**graph.row('800', 'Parent'), 'permalink_url': url}
    if fault == 'actor': canonical['from'] = {'id': '999'}
    if fault == 'text': canonical['message'] = 'Different'
    if fault == 'parent': direct['parent'] = {'id': '999'}
    if fault == 'object': direct['object'] = {'id': '999'}
    if fault == 'receipt': direct['id'] = '789_801'
    if fault == 'base_link': direct['permalink_url'] = canonical['permalink_url'] = 'https://www.facebook.com/reel/789/'
    if fault == 'missing_actor': direct.pop('from'); canonical.pop('from')
    graph.comments['789_800'] = direct
    graph.pages = lambda r: {'data': [canonical, dict(canonical, id='801')] if fault == 'duplicate' else [canonical]}
    with pytest.raises(mod.CommentError):
        client.find_comment('789', '789_800')
    assert not graph.writes


def test_accepted_delete_does_not_tell_agent_to_reconcile_forever(tmp_path, capsys):
    mod, _, graph, client, store = service(tmp_path)
    graph.comments['800'] = graph.row('800', 'Own test')
    change = mod.prepare_comment(client, store, media_id='789', action='delete', comment_id='800')
    result = mod.apply_comment(client, store, change.id, change.fingerprint)
    OperationOutput(True).emit(result)
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'applied_unverified'
    assert payload['error_detail']['code'] == 'operation_accepted_unverified'
    assert payload['error_detail']['action'] == 'inspect'
    assert payload['error_detail']['retryable'] is False
    mod.apply_comment(client, store, change.id, change.fingerprint)
    assert len(graph.writes) == 1


def test_video_delete_status_explains_provider_acceptance_without_claiming_absence(tmp_path):
    from tests.test_meta_management import service as meta_service
    from typer.testing import CliRunner
    from socialctl.cli import app
    mod, brand, graph, client, store = meta_service(tmp_path)
    change = mod.prepare_meta(client, store, {'action': 'video-delete', 'target_id': '789'})
    mod.apply_meta(client, store, change.id, change.fingerprint)
    result = CliRunner().invoke(app, ['meta', 'status', change.id,
        '--brand', brand.nombre, '--root', str(tmp_path)])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload['status'] == 'accepted_unverifiable'
    assert payload['error_detail']['code'] == 'operation_accepted_unverified'
    assert payload['error_detail']['action'] == 'inspect'
    assert not payload['error_detail']['retryable']
    assert len(graph.writes) == 1


@pytest.mark.parametrize('old_readable', [True, False])
def test_historical_verified_alias_cannot_bypass_duplicate_protection(tmp_path, old_readable):
    mod, _, graph, client, store = service(tmp_path)
    url = 'https://www.facebook.com/reel/789/?comment_id=800'
    alias = {**graph.row('789_800', 'Parent'), 'permalink_url': url}
    graph.comments['789_800'] = alias
    graph.pages = lambda r: {'data': [alias]}
    old = mod.prepare_comment(client, store, media_id='789', action='reply',
        parent_id='789_800', text='Same reply')
    old.status = 'verified'
    old.remote_id = '901'
    store.save(old)
    original_fingerprint = old.fingerprint
    graph.comments['800'] = {**alias, 'id': '800'}
    graph.pages = lambda r: {'data': [graph.comments['800']]}
    if not old_readable:
        graph.comments.pop('789_800')
    new = mod.prepare_comment(client, store, media_id='789', action='reply',
        parent_id='800', text='Same reply')
    with pytest.raises(mod.CommentError):
        mod.apply_comment(client, store, new.id, new.fingerprint)
    assert not graph.writes
    assert store.load(old.id).fingerprint == original_fingerprint
    assert store.load(old.id).parent_id == '789_800'
