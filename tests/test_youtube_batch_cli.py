import json

import httpx
import pytest

from typer.testing import CliRunner

from socialctl.cli import app
from tests.test_youtube_batches import proposal
from tests.test_youtube_metadata_parts import isolated


def wire(monkeypatch, client, api):
    from socialctl.management import cli
    monkeypatch.setattr(cli, "_brand", lambda root, name: client.brand)
    monkeypatch.setattr(cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))


def test_complete_batch_dryrun_and_apply_share_exact_digest(tmp_path, monkeypatch):
    module, api, client, store, original, file = proposal(tmp_path)
    wire(monkeypatch, client, api)
    runner = CliRunner()
    preview = runner.invoke(app, ["content", "edit-batch", "--file", str(file), "--brand", "MarcaA", "--dry-run"])
    assert preview.exit_code == 0, preview.output
    records = [store.load(path.stem) for path in store.root.glob("*.json") if path.stem != original.id]
    batch = records[0]
    for ident in ("video-1", "video-2", "video-3"):
        assert ident in preview.output
    assert batch.fingerprint in preview.output
    assert "internal" in preview.output and "Visible hashtags" in preview.output
    assert "Before" in preview.output and "After" in preview.output
    assert api.writes == []
    denied = runner.invoke(app, ["changes", "apply", batch.id, "--brand", "MarcaA", "--yes", "--approval-stdin"], input="yes\n")
    assert denied.exit_code == 1 and api.writes == []
    api.failures["video-2"] = 412
    result = runner.invoke(app, ["changes", "apply", batch.id, "--brand", "MarcaA", "--approval-stdin"], input=batch.fingerprint + "\n")
    assert result.exit_code == 1
    assert "conflict" in result.output and "applied" in result.output
    assert [ident for ident, *_ in api.writes] == ["video-1", "video-2", "video-3"]


def test_restore_cli_demands_dryrun_then_new_exact_approval(tmp_path, monkeypatch):
    module, api, client, store, batch, _ = proposal(tmp_path, names=("video-1",))
    wire(monkeypatch, client, api)
    runner = CliRunner()
    denied = runner.invoke(app, ["changes", "restore", batch.id, "--brand", "MarcaA"])
    assert denied.exit_code == 1 and "--dry-run" in denied.output
    result = runner.invoke(app, ["changes", "restore", batch.id, "--brand", "MarcaA", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "restoration" in result.output
    assert 'does not restore' in result.output
    assert api.writes == []
    ids = [path.stem for path in store.root.glob("*.json") if path.stem != batch.id]
    status = runner.invoke(app, ["changes", "status", ids[0], "--brand", "MarcaA"])
    assert status.exit_code == 0
    assert json.loads(status.output)["restored_from"] == batch.id


@pytest.mark.parametrize('http_status,reason', [
    (400, 'invalidTitle'), (403, 'quotaExceeded'), (403, 'insufficientPermissions'),
    (403, 'forbidden'), (400, 'unknown-secret-reason'),
])
def test_rejection_diagnostics_are_safe_visible_and_not_retried(tmp_path, monkeypatch, http_status, reason):
    from tests.test_youtube_metadata_parts import API
    api = API()
    handler = api.handler
    secret = 'synthetic-local-token'
    unsafe = (f'Authorization: Bearer {secret} https://user:password@example.invalid/?access_token={secret} '
              'refresh_token=private-refresh ' + 'x' * 2000)
    def reject(request):
        if request.method == 'PUT':
            # The fictional provider records the request, then definitively rejects it.
            api.failures['video-1'] = http_status
            handler(request)
            return httpx.Response(http_status, json={'error': {
                'message': unsafe, 'errors': [{'reason': reason, 'message': unsafe}],
                'credential': secret}})
        return handler(request)
    api.handler = reject
    module, api, client, store, batch, _ = proposal(tmp_path, api=api, names=('video-1',))
    original_fingerprint = batch.fingerprint
    original_op = batch.operations[0].model_dump(exclude={'status', 'journal'})
    for _ in range(2):
        result = module.apply_batch(client, store, batch.id, batch.fingerprint)
        assert result.operations[0].status == 'failed'
    assert len(api.writes) == 1
    assert result.fingerprint == original_fingerprint
    assert result.operations[0].model_dump(exclude={'status', 'journal'}) == original_op
    event = result.operations[0].journal[-1]
    assert event['event'] == 'write_rejected' and event['http_status'] == str(http_status)
    if reason != 'unknown-secret-reason':
        assert event['reason'] == reason
    else:
        assert 'reason' not in event
    assert len(event['error']) < 200
    persisted = store.path_for(batch.id).read_text()
    for forbidden in (secret, 'password', 'private-refresh', 'example.invalid', 'unknown-secret-reason'):
        assert forbidden not in persisted
    assert 'reauthorize' not in event['error']
    assert ('youtube.force-ssl' in event['error']) == (reason == 'insufficientPermissions')
    wire(monkeypatch, client, api)
    status = CliRunner().invoke(app, ['changes', 'status', batch.id, '--brand', 'MarcaA'])
    assert status.exit_code == 0, status.output
    shown = json.loads(status.output)['operations'][0]['journal'][-1]
    assert shown == event
    # An older journal has no diagnostic fields; loading/status preserves it.
    result.operations[0].journal[-1] = {'at': event['at'], 'event': 'write_rejected'}
    store.save(result)
    old_status = CliRunner().invoke(app, ['changes', 'status', batch.id, '--brand', 'MarcaA'])
    assert old_status.exit_code == 0
    assert json.loads(old_status.output)['operations'][0]['journal'][-1] == result.operations[0].journal[-1]
