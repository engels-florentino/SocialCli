import copy
import importlib
import json

import pytest
import yaml

from socialctl.management.changes import ApprovalMismatch, ChangeError, ChangeStore, EditFile, prepare_youtube_change
from socialctl.management.youtube import YouTubeManagementClient
from tests.test_youtube_metadata_parts import API, brand_at, isolated, metadata_clock


def batches():
    assert importlib.util.find_spec("socialctl.management.batches") is not None, "immutable batch service missing"
    return importlib.import_module("socialctl.management.batches")


def proposal(tmp_path, *, api=None, names=("video-1", "video-2", "video-3"), brand=None):
    module = batches()
    api = api or API()
    brand = brand or brand_at(tmp_path)
    file = tmp_path / (brand.nombre + "-batch.yml")
    file.write_text(yaml.safe_dump({"version": 2, "platform": "youtube", "operations": [
        {"video_id": ident, "kind": "snippet", "patch": {"title": "New " + ident}} for ident in names]}))
    client = api.client(brand)
    store = module.BatchStore(brand.raiz)
    change = module.prepare_batch(client, store, file)
    return module, api, client, store, change, file


def test_partial_success_continues_and_restart_never_repeats_operations(tmp_path):
    module, api, client, store, change, file = proposal(tmp_path)
    api.failures["video-2"] = 403
    result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert [op.status for op in result.operations] == ["applied", "failed", "applied"]
    module.apply_batch(client, module.BatchStore(client.brand.raiz), change.id, change.fingerprint)
    assert [ident for ident, *_ in api.writes] == ["video-1", "video-2", "video-3"]


def test_batch_conflict_is_independent_and_actual_changes_invalidate(tmp_path):
    module, api, client, store, change, _ = proposal(tmp_path)
    api.videos["video-2"]["etag"] = "new-etag"
    api.videos["video-2"]["contentDetails"]["duration"] = "PT2M"
    result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert [op.status for op in result.operations] == ["applied", "conflict", "applied"]
    assert [ident for ident, *_ in api.writes] == ["video-1", "video-3"]


def test_uncertain_restart_only_reconciles_reads_and_resumes_pending(tmp_path):
    module, api, client, store, change, _ = proposal(tmp_path)
    api.failures["video-1"] = "timeout"
    original = client.update_parts
    def interrupted(ident, *args, **kwargs):
        if ident == "video-2":
            raise KeyboardInterrupt
        return original(ident, *args, **kwargs)
    client.update_parts = interrupted
    with pytest.raises(KeyboardInterrupt):
        module.apply_batch(client, store, change.id, change.fingerprint)
    assert [op.status for op in store.load(change.id).operations] == ["uncertain", "applying", "pending"]
    client.update_parts = original
    result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert [op.status for op in result.operations] == ["manual_review", "manual_review", "applied"]
    assert [ident for ident, *_ in api.writes] == ["video-1", "video-3"]


@pytest.mark.parametrize("tamper", ["file", "approval", "membership", "patch", "account", "account_config", "accounts_file", "brand"])
def test_stale_binding_rejects_before_any_write(tmp_path, tamper):
    module, api, client, store, change, file = proposal(tmp_path)
    digest = change.fingerprint
    if tamper == "file":
        file.write_text(file.read_text() + "\n# changed\n")
    elif tamper == "approval":
        digest = "0" * 64
    elif tamper in {"membership", "patch"}:
        raw = json.loads(store.path_for(change.id).read_text())
        if tamper == "membership":
            raw["operations"].reverse()
        else:
            raw["operations"][0]["patch"]["title"] = "Tampered"
        store.path_for(change.id).write_text(json.dumps(raw))
    elif tamper == "account":
        client.brand.cuentas["youtube"]["channel_id"] = "changed"
    elif tamper == "account_config":
        client.brand.cuentas["youtube"]["other_setting"] = "changed"
    elif tamper == "accounts_file":
        (client.brand.raiz / "accounts.yml").write_text("youtube: {channel_id: changed}\n")
    else:
        client.brand = brand_at(tmp_path, "MarcaB")
    with pytest.raises(ChangeError):
        module.apply_batch(client, store, change.id, digest)
    assert api.writes == []


def test_duplicate_members_and_unowned_member_rejected_before_proposal(tmp_path):
    brand = brand_at(tmp_path)
    with pytest.raises(ChangeError, match='duplicate'):
        proposal(tmp_path, names=("video-1", "video-1"), brand=brand)
    api = API()
    api.videos["video-2"]["snippet"]["channelId"] = "other"
    with pytest.raises(Exception, match="belong"):
        proposal(tmp_path, api=api, brand=brand)
    assert not list((tmp_path / "MarcaA" / ".socialctl").glob("metadata-batches/*.json"))


def test_two_brands_have_independent_bound_batches(tmp_path):
    module, api_a, client_a, store_a, batch_a, _ = proposal(tmp_path)
    api_b = API("channel-b")
    brand_b = brand_at(tmp_path, "MarcaB", "channel-b")
    _, api_b, client_b, store_b, batch_b, _ = proposal(tmp_path, api=api_b, brand=brand_b)
    with pytest.raises(ChangeError):
        module.apply_batch(client_b, store_a, batch_a.id, batch_a.fingerprint)
    assert all(op.status == "applied" for op in module.apply_batch(client_a, store_a, batch_a.id, batch_a.fingerprint).operations)
    assert all(op.status == "applied" for op in module.apply_batch(client_b, store_b, batch_b.id, batch_b.fingerprint).operations)


def test_restore_is_new_baseline_proposal_preserving_unrelated_current_fields(tmp_path):
    module, api, client, store, change, _ = proposal(tmp_path, names=("video-1",))
    module.apply_batch(client, store, change.id, change.fingerprint)
    api.videos["video-1"]["snippet"]["description"] = "Independent current text"
    restore = module.prepare_restore(client, store, change.id)
    assert restore.id != change.id and restore.fingerprint != change.fingerprint
    assert restore.operations[0].after["snippet"]["title"] == "Old"
    assert restore.operations[0].after["snippet"]["description"] == "Independent current text"
    assert len(api.writes) == 1
    with pytest.raises(ApprovalMismatch):
        module.apply_batch(client, store, restore.id, change.fingerprint)
    module.apply_batch(client, store, restore.id, restore.fingerprint)
    assert len(api.writes) == 2
    assert api.videos["video-1"]["snippet"]["description"] == "Independent current text"


@pytest.mark.parametrize("old", [None, "absent"])
def test_restore_nullable_or_absent_old_snippet_fields_is_explicit(tmp_path, old):
    module = batches()
    api = API()
    brand = brand_at(tmp_path)
    if old == "absent":
        del api.videos["video-1"]["snippet"]["tags"]
        del api.videos["video-1"]["snippet"]["defaultLanguage"]
    else:
        api.videos["video-1"]["snippet"].update(tags=None, defaultLanguage=None)
    client = api.client(brand)
    file = tmp_path / "reset.yml"
    file.write_text(yaml.safe_dump({"version": 2, "platform": "youtube", "operations": [
        {"video_id": "video-1", "kind": "snippet", "patch": {"tags": ["new"], "defaultLanguage": "fr"}}]}))
    store = module.BatchStore(brand.raiz)
    original = module.prepare_batch(client, store, file)
    module.apply_batch(client, store, original.id, original.fingerprint)
    restore = module.prepare_restore(client, store, original.id)
    assert restore.operations[0].patch == {"tags": []}
    assert "defaultLanguage" in " ".join(restore.unavailable)
    assert restore.operations[0].after["snippet"]["defaultLanguage"] == "fr"
    assert restore.operations[0].resets == ["tags: empty list (original absent/null)"]


def test_restore_v1_uses_original_changed_fields_only(tmp_path):
    module = batches()
    api = API()
    brand = brand_at(tmp_path)
    legacy = prepare_youtube_change(YouTubeManagementClient(brand, api.client(brand).client), ChangeStore(brand.raiz),
        EditFile(version=1, platform="youtube", video_id="video-1", patch={"title": "New"}))
    api.videos["video-1"]["snippet"].update(title="New", description="Keep now")
    restore = module.prepare_restore(api.client(brand), module.BatchStore(brand.raiz), legacy.id)
    assert restore.operations[0].patch == {"title": "Old"}
    assert restore.operations[0].after["snippet"]["description"] == "Keep now"


def test_expired_schedule_reports_failure_then_continues_other_members(tmp_path, monkeypatch):
    module, api, client, store, original, file = proposal(tmp_path)
    raw = yaml.safe_load(file.read_text())
    raw["operations"][0] = {"video_id": "video-1", "kind": "schedule", "never_published": True,
                            "patch": {"privacyStatus": "private", "publishAt": "2099-01-01T00:00:00Z"}}
    file.write_text(yaml.safe_dump(raw))
    batch = module.prepare_batch(client, store, file)
    from datetime import datetime, timezone
    from socialctl.management import metadata_parts
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2100, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(metadata_parts, "datetime", Future)
    result = module.apply_batch(client, store, batch.id, batch.fingerprint)
    assert [op.status for op in result.operations] == ["failed", "applied", "applied"]
    assert [ident for ident, *_ in api.writes] == ["video-2", "video-3"]


def test_durable_intent_failure_prevents_put_and_restarts_read_only(tmp_path, monkeypatch):
    module, api, client, store, batch, _ = proposal(tmp_path)
    def interrupted(_):
        raise OSError("simulated directory fsync failure")
    monkeypatch.setattr(store, "_sync_directory", interrupted)
    with pytest.raises(ChangeError, match="save"):
        module.apply_batch(client, store, batch.id, batch.fingerprint)
    assert api.writes == []


def test_uncertain_after_provider_success_reconciles_without_duplicate_put(tmp_path):
    module, api, client, store, batch, _ = proposal(tmp_path, names=("video-1",))
    original = client.update_parts
    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic lost response")
    client.update_parts = lost_response
    first = module.apply_batch(client, store, batch.id, batch.fingerprint)
    assert first.operations[0].status == "uncertain"
    second = module.apply_batch(client, store, batch.id, batch.fingerprint)
    assert second.operations[0].status == "applied" and len(api.writes) == 1


def test_restored_empty_tags_verify_when_api_omits_empty_array(tmp_path):
    module, api, client, store, batch, file = proposal(tmp_path, names=("video-1",))
    del api.videos["video-1"]["snippet"]["tags"]
    raw = yaml.safe_load(file.read_text())
    raw["operations"][0]["patch"] = {"tags": ["new"]}
    file.write_text(yaml.safe_dump(raw))
    original = module.prepare_batch(client, store, file)
    module.apply_batch(client, store, original.id, original.fingerprint)
    restored = module.prepare_restore(client, store, original.id)
    api.after_write = lambda ident: api.videos[ident]["snippet"].pop("tags", None)
    result = module.apply_batch(client, store, restored.id, restored.fingerprint)
    assert result.operations[0].status == "applied"


def test_changed_restore_baseline_conflicts_without_overwriting_current_metadata(tmp_path):
    module, api, client, store, original, _ = proposal(tmp_path, names=("video-1",))
    restored = module.prepare_restore(client, store, original.id)
    api.videos["video-1"]["snippet"]["description"] = "Changed after restore preview"
    result = module.apply_batch(client, store, restored.id, restored.fingerprint)
    assert result.operations[0].status == "conflict" and api.writes == []


@pytest.mark.parametrize("kind,patch", [
    ("status", {"embeddable": False}), ("audience", {"selfDeclaredMadeForKids": True}),
    ("synthetic", {"containsSyntheticMedia": True}), ("privacy", {"privacyStatus": "private"}),
])
@pytest.mark.parametrize("expire_at", ["before_apply", "final_auth"])
def test_preserved_schedule_expiry_fails_only_affected_batch_member(
    tmp_path, monkeypatch, metadata_clock, kind, patch, expire_at
):
    module, api, client, store, _, file = proposal(tmp_path)
    api.videos["video-1"]["status"]["publishAt"] = "2099-01-01T00:00:00Z"
    original = copy.deepcopy(api.videos["video-1"])
    raw = yaml.safe_load(file.read_text())
    raw["operations"][0] = {"video_id": "video-1", "kind": kind, "patch": patch}
    file.write_text(yaml.safe_dump(raw))
    batch = module.prepare_batch(client, store, file)
    assert batch.operations[0].after["status"]["publishAt"] == "2099-01-01T00:00:00Z"
    if expire_at == "before_apply":
        metadata_clock.expire()
    else:
        authenticate = client._authenticated_channel
        calls = 0
        def delayed_auth(token):
            nonlocal calls
            result = authenticate(token)
            calls += 1
            if calls == 3:  # apply inspection, direct inspection, final pre-PUT auth
                metadata_clock.expire()
            return result
        monkeypatch.setattr(client, "_authenticated_channel", delayed_auth)
    result = module.apply_batch(client, store, batch.id, batch.fingerprint)
    assert [op.status for op in result.operations] == ["failed", "applied", "applied"]
    assert [ident for ident, *_ in api.writes] == ["video-2", "video-3"]
    assert api.videos["video-1"] == original
    assert result.operations[0].after == batch.operations[0].after


@pytest.mark.parametrize('other_change', [None, 'title', 'thumbnail_path', 'privacy'])
def test_thumbnail_signature_rotation_preserves_only_equivalent_snapshot(tmp_path, other_change):
    api = API()
    api.videos['video-1']['snippet']['thumbnails'] = {'default': {'url':'https://i9.ytimg.com/vi/video-1/default.jpg?sqp=old&rs=old', 'width':120, 'height':90}}
    module, api, client, store, change, _ = proposal(tmp_path, api=api, names=('video-1',))
    api.videos['video-1']['etag'] = 'rotated-etag'
    api.videos['video-1']['snippet']['thumbnails']['default']['url'] = 'https://i9.ytimg.com/vi/video-1/default.jpg?sqp=new&rs=new'
    if other_change == 'title': api.videos['video-1']['snippet']['title'] = 'Independent edit'
    if other_change == 'thumbnail_path': api.videos['video-1']['snippet']['thumbnails']['default']['url'] = 'https://i9.ytimg.com/vi/video-1/other.jpg?sqp=new&rs=new'
    if other_change == 'privacy': api.videos['video-1']['status']['privacyStatus'] = 'public'
    result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert result.operations[0].status == ('applied' if other_change is None else 'conflict')
    assert len(api.writes) == (1 if other_change is None else 0)
    if api.writes: assert api.writes[0][2]['if-match'] == 'rotated-etag'


def test_keyword_permutation_readback_keeps_approval_and_payload(tmp_path):
    module, api, client, store, change, file = proposal(tmp_path, names=("video-1",))
    raw = yaml.safe_load(file.read_text())
    raw["operations"][0]["patch"]["tags"] = ["z", "a", "z"]
    file.write_text(yaml.safe_dump(raw))
    change = module.prepare_batch(client, store, file)
    approved = change.model_dump()
    api.after_write = lambda ident: api.videos[ident]["snippet"]["tags"].sort()
    result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert result.operations[0].status == "applied"
    assert result.fingerprint == approved["fingerprint"]
    assert result.operations[0].after == approved["operations"][0]["after"]
    assert len(api.writes) == 1
    assert api.writes[0][1]["snippet"]["tags"] == ["z", "a", "z"]


@pytest.mark.parametrize("tags", [["a"], ["a", "z", "extra"], ["a", "z", "z"], ["A", "z"], ["a ", "z"], "az", [[], "z"]])
def test_keyword_readback_rejects_exact_multiset_changes(tmp_path, tags):
    from types import SimpleNamespace
    module = batches()
    expected = {"snippet": {"tags": ["a", "z"]}}
    current = SimpleNamespace(resource={"snippet": {"tags": tags}, "status": {}})
    assert not module._matches(current, expected)


def test_audio_drift_is_diagnosed_and_never_replayed(tmp_path):
    api = API()
    del api.videos["video-1"]["snippet"]["defaultAudioLanguage"]
    module, api, client, store, change, _ = proposal(tmp_path, api=api, names=("video-1",))
    api.after_write = lambda ident: api.videos[ident]["snippet"].update(defaultAudioLanguage="en-US")
    result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert result.operations[0].status == "uncertain"
    assert result.operations[0].journal[-1]["mismatch_paths"] == "snippet.defaultAudioLanguage"
    for _ in range(2):
        result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert result.operations[0].status == "manual_review"
    assert len(api.writes) == 1
    fresh = module.prepare_batch(client, store, tmp_path / (client.brand.nombre + "-batch.yml"))
    api.after_write = lambda ident: api.videos[ident]["snippet"]["tags"].reverse()
    assert module.apply_batch(client, store, fresh.id, fresh.fingerprint).operations[0].status == "applied"


@pytest.mark.parametrize("part,field,before,after", [
    ("snippet", "title", "one", "two"),
    ("snippet", "defaultAudioLanguage", "es", "en-US"),
    ("status", "embeddable", True, False),
    ("localizations", "en", ["a", "b"], ["b", "a"]),
])
def test_keyword_equivalence_does_not_relax_other_fields(part, field, before, after):
    from types import SimpleNamespace
    resource = {"snippet": {}, "status": {}, "localizations": {}}
    resource[part][field] = after
    assert not batches()._matches(SimpleNamespace(resource=resource), {part: {field: before}})


def test_tag_permutation_before_write_still_invalidates_snapshot(tmp_path):
    api = API()
    api.videos["video-1"]["snippet"]["tags"] = ["z", "a"]
    module, api, client, store, change, _ = proposal(tmp_path, api=api, names=("video-1",))
    api.videos["video-1"]["snippet"]["tags"].reverse()
    result = module.apply_batch(client, store, change.id, change.fingerprint)
    assert result.operations[0].status == "conflict"
    assert not api.writes


@pytest.mark.parametrize('second_change', [None, 'etag', 'title', 'extra', 'duration'])
def test_etag_churn_requires_full_snapshot_at_both_reads(tmp_path, monkeypatch, second_change):
    module, api, client, store, batch, _ = proposal(tmp_path, names=('video-1',))
    observed = copy.deepcopy(batch.operations[0].observed)
    api.videos['video-1']['etag'] = 'etag-apply'
    inspect = client.inspect
    reads = 0
    def changing_read(ident):
        nonlocal reads
        reads += 1
        if reads == 2:
            video = api.videos[ident]
            if second_change == 'etag': video['etag'] = 'etag-final'
            if second_change == 'title': video['snippet']['title'] = 'Concurrent title'
            if second_change == 'extra': video['extraField'] = 'Concurrent value'
            if second_change == 'duration': video['contentDetails']['duration'] = 'PT2M'
        return inspect(ident)
    monkeypatch.setattr(client, 'inspect', changing_read)
    result = module.apply_batch(client, store, batch.id, batch.fingerprint)
    op = result.operations[0]
    assert op.observed == observed
    if second_change in {None, 'etag'}:
        assert op.status == 'applied' and len(api.writes) == 1
        expected_etag = 'etag-final' if second_change == 'etag' else 'etag-apply'
        assert api.writes[0][2]['if-match'] == expected_etag
        intents = [entry for entry in op.journal if entry['event'] == 'write_intent']
        assert intents[-1]['if_match'] == expected_etag
    else:
        assert op.status == 'conflict' and not api.writes


def test_etag_equivalence_does_not_retry_conditional_412(tmp_path):
    module, api, client, store, batch, _ = proposal(tmp_path, names=('video-1',))
    api.videos['video-1']['etag'] = 'etag-current'
    api.failures['video-1'] = 412
    for _ in range(2):
        result = module.apply_batch(client, store, batch.id, batch.fingerprint)
        assert result.operations[0].status == 'conflict'
    assert len(api.writes) == 1
    assert api.writes[0][2]['if-match'] == 'etag-current'
