"""Synthetic supplied assets + MockTransport only; never contact an account."""
import hashlib
import json
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from tests.test_youtube_management import _brand, _snippet
from socialctl.management.changes import ApprovalMismatch, ChangeError
from socialctl.management.youtube_resources import YouTubeResourcesClient, ResourceError
from socialctl.management.resource_changes import (
    ResourceStore, prepare_resource, apply_resource, reconcile_resource, inspect_asset,
)


SRT = b"1\n00:00:00,000 --> 00:00:01,000\nSupplied caption\n"
OLD = b"1\n00:00:00,000 --> 00:00:01,000\nOriginal caption\n"
# Valid synthetic one-pixel PNG fixture, supplied verbatim to the workflow.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")


@pytest.mark.parametrize("error", [
    {"errors": [{"reason": "SENSITIVE_PROVIDER_TOKEN"}], "message": "SENSITIVE_PROVIDER_MESSAGE"},
    {"errors": [{"reason": "manualSortRequired\nSENSITIVE_PROVIDER_MESSAGE"}]},
    {"errors": [{"reason": "manualSortRequired"}, {"reason": "channelSectionNotFound"}]},
    {"code": 403, "errors": [{"reason": "manualSortRequired"}]},
    {"errors": [{"reason": "manualSortRequired"}], "message": "SENSITIVE_PROVIDER_MESSAGE" * 1000},
])
def test_provider_reason_is_bounded_allowlisted_and_never_echoes_body(tmp_path, error):
    from socialctl.management.youtube_resources import ResourceRejected
    brand = _brand(tmp_path)
    client = YouTubeResourcesClient(brand, httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": error}))))
    with pytest.raises(ResourceRejected) as raised:
        client._request("DELETE", "https://www.googleapis.com/youtube/v3/captions", params={"id": "caption-1"})
    assert raised.value.provider_reason is None
    assert raised.value.http_status == 400
    assert "SENSITIVE" not in str(raised.value)


def track(cid="caption-1", **changes):
    return {"id": cid, "etag": "track-etag", "snippet": {
        "videoId": "video-1", "language": "es", "name": "Español",
        "isDraft": True, "status": "serving", **changes}}


def multipart(request):
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.content)
    parts = list(message.iter_parts())
    return json.loads(parts[0].get_payload(decode=True)), parts[1].get_payload(decode=True)


class API:
    def __init__(self):
        self.account = "channel-a"
        self.owner = "channel-a"
        self.tracks = [track()]
        self.bytes = {"caption-1": OLD}
        self.thumbnails = {"default": {"url": "https://i.ytimg.com/vi/video-1/default.jpg"}}
        self.requests = []
        self.on_write = None
        self.download_code = 200
        self.list_code = 200
        self.extra_list = {}

    @property
    def writes(self):
        return [r for r in self.requests if r.method != "GET"]

    def handler(self, request):
        self.requests.append(request)
        p = request.url.path
        if p == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": self.account}],
                "pageInfo": {"totalResults": 1, "resultsPerPage": 5}})
        if p == "/youtube/v3/videos":
            return httpx.Response(200, json={"items": [{"id": "video-1", "etag": "video-etag",
                "snippet": _snippet(channelId=self.owner, thumbnails=self.thumbnails)}]})
        if request.method == "GET" and p == "/youtube/v3/captions":
            return httpx.Response(self.list_code, json={"items": self.tracks, **self.extra_list})
        if request.method == "GET" and p.startswith("/youtube/v3/captions/"):
            return httpx.Response(self.download_code, content=self.bytes.get(p.rsplit("/", 1)[1], b""))
        if self.on_write:
            return self.on_write(request)
        if p == "/upload/youtube/v3/thumbnails/set":
            return httpx.Response(200, json={"etag": "set-etag", "items": [self.thumbnails]})
        if request.method in {"POST", "PUT"}:
            if request.headers["Content-Type"].startswith("multipart/related"):
                body, data = multipart(request)
            else:
                body, data = json.loads(request.content), None
            cid = body.get("id", "new-caption")
            item = next((t for t in self.tracks if t["id"] == cid), None)
            if item is None:
                item = track(cid)
                self.tracks.append(item)
            item["snippet"].update(body.get("snippet", {}))
            item["etag"] = "after-etag"
            if data is not None:
                self.bytes[cid] = data
            return httpx.Response(200, json=item)
        if request.method == "DELETE":
            self.tracks = [t for t in self.tracks if t["id"] != request.url.params["id"]]
            return httpx.Response(204)
        raise AssertionError(request)

    def client(self, brand):
        return YouTubeResourcesClient(brand, httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True))


@pytest.fixture
def env(tmp_path):
    brand = _brand(tmp_path)
    api = API()
    asset = tmp_path / "supplied.srt"
    asset.write_bytes(SRT)
    return api, api.client(brand), ResourceStore(brand.raiz), asset


def insert(env):
    api, client, store, asset = env
    return prepare_resource(client, store, action="caption-insert", video_id="video-1",
        file=asset, language="en", name="English", draft=False)


def test_insert_binds_bytes_identity_draft_intent_id_and_readback(env):
    api, client, store, asset = env
    change = insert(env)
    assert not api.writes
    assert change.asset["sha256"] == hashlib.sha256(SRT).hexdigest()
    assert change.target_account == "channel-a"
    assert change.language == "en" and change.draft is False
    def write(request):
        persisted = store.load(change.id)
        assert persisted.status == "applying"
        assert any(e["event"] == "write_intent" for e in persisted.journal)
        api.on_write = None
        return api.handler(request)
    api.on_write = write
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "verified" and result.result_id == "new-caption"
    assert store.load(change.id).result_id == "new-caption"
    assert multipart(api.writes[0])[1] == SRT
    assert multipart(api.writes[0])[0] == {"snippet": {
        "videoId": "video-1", "language": "en", "name": "English", "isDraft": False}}
    for r in api.requests:
        assert r.url.host == "www.googleapis.com"
        assert "sync" not in r.url.params and "tlang" not in r.url.params and "tfmt" not in r.url.params
    count = len(api.writes)
    apply_resource(client, store, change.id, change.fingerprint)
    assert len(api.writes) == count


def test_approval_and_changed_asset_prevent_writes(env):
    api, client, store, asset = env
    change = insert(env)
    with pytest.raises(ApprovalMismatch):
        apply_resource(client, store, change.id, "wrong")
    asset.write_bytes(OLD)
    with pytest.raises(ChangeError, match='file|bytes|fingerprint'):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


@pytest.mark.parametrize("field", ["account", "owner"])
def test_ownership_rechecked_before_write(env, field):
    api, client, store, asset = env
    change = insert(env)
    setattr(api, field, "channel-b")
    with pytest.raises(ChangeError):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


def test_update_preserves_omitted_draft_and_backs_up_original_bytes(env):
    api, client, store, asset = env
    change = prepare_resource(client, store, action="caption-update", video_id="video-1",
        track_id="caption-1", file=asset)
    assert change.draft is True and change.language == "es"
    assert change.backup["sha256"] == hashlib.sha256(OLD).hexdigest()
    backup = store.backup_path(change.id)
    assert backup.read_bytes() == OLD and backup.stat().st_mode & 0o777 == 0o600
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    request = api.writes[0]
    assert request.method == "PUT" and request.headers["If-Match"] == "track-etag"
    body, data = multipart(request)
    assert body == {"id": "caption-1"} and request.url.params["part"] == "id"
    assert data == SRT


def test_draft_only_update_sends_only_documented_writable_field(env):
    api, client, store, asset = env
    change = prepare_resource(client, store, action="caption-update", video_id="video-1",
        track_id="caption-1", draft=False)
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert json.loads(api.writes[0].content) == {"id": "caption-1", "snippet": {"isDraft": False}}
    assert api.writes[0].url.path == "/youtube/v3/captions"


def test_conflicting_track_etag_or_bytes_never_overwrites(env):
    api, client, store, asset = env
    change = prepare_resource(client, store, action="caption-update", video_id="video-1",
        track_id="caption-1", file=asset)
    api.tracks[0]["etag"] = "other-editor"
    with pytest.raises(ChangeError, match="conflict"):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


@pytest.mark.parametrize("mode", ["timeout", "server", "bad-json", "wrong-id", "redirect"])
def test_uncertain_insert_never_reinserts_on_restart(env, mode):
    api, client, store, asset = env
    change = insert(env)
    def write(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("secret must not escape")
        if mode == "server":
            return httpx.Response(503)
        if mode == "bad-json":
            return httpx.Response(200, content=b"garbage")
        if mode == "redirect":
            return httpx.Response(307, headers={"Location": "https://evil.invalid/steal"})
        return httpx.Response(200, json={"id": "wrong", "snippet": {"videoId": "someone-else"}})
    api.on_write = write
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain"
    api.tracks.append(track("candidate", language="en", name="English", isDraft=False))
    restarted = ResourceStore(store.root.parents[1])
    result = apply_resource(client, restarted, change.id, change.fingerprint)
    assert result.status == "uncertain" and len(api.writes) == 1
    assert all(r.url.host == "www.googleapis.com" for r in api.requests)


def test_result_id_persisted_even_when_readback_fails(env):
    api, client, store, asset = env
    change = insert(env)
    def write(request):
        api.list_code = 403
        return httpx.Response(200, json=track("accepted-id", language="en", name="English", isDraft=False))
    api.on_write = write
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain"
    assert store.load(change.id).result_id == "accepted-id"


def test_delete_requires_explicit_track_and_confirmed_response_plus_readback(env):
    api, client, store, asset = env
    change = prepare_resource(client, store, action="caption-delete", video_id="video-1", track_id="caption-1")
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "verified" and len(api.writes) == 1
    assert api.writes[0].method == "DELETE" and api.writes[0].url.params["id"] == "caption-1"


def test_ambiguous_delete_absence_is_not_success_or_retry(env):
    api, client, store, asset = env
    change = prepare_resource(client, store, action="caption-delete", video_id="video-1", track_id="caption-1")
    def write(request):
        api.tracks = []
        raise httpx.ReadTimeout("lost")
    api.on_write = write
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain"
    result = reconcile_resource(client, store, change.id)
    assert result.status == "uncertain" and len(api.writes) == 1


def test_thumbnail_upload_is_raw_and_never_claims_original_fidelity(env, tmp_path):
    api, client, store, asset = env
    file = tmp_path / "supplied.png"
    file.write_bytes(PNG)
    change = prepare_resource(client, store, action="thumbnail-set", video_id="video-1", file=file)
    assert change.backup["fidelity"] == "metadata_only_no_original_bytes"
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "accepted_unverifiable"
    assert result.verified is False
    assert api.writes[0].content == PNG and api.writes[0].headers["Content-Type"] == "image/png"
    assert all(r.url.host == "www.googleapis.com" for r in api.requests)


def test_partial_permissions_do_not_become_empty_list_or_backup_success(env):
    api, client, store, asset = env
    api.list_code = 403
    with pytest.raises(ResourceError):
        client.list_captions("video-1")
    api.list_code = 200
    api.download_code = 403
    change = prepare_resource(client, store, action="caption-update", video_id="video-1", track_id="caption-1", draft=False)
    assert change.backup["fidelity"] == "unavailable"
    assert not api.writes


def test_no_invented_caption_pagination(env):
    api, client, store, asset = env
    api.extra_list = {"nextPageToken": "unexpected"}
    with pytest.raises(ResourceError, match="incomplete|pagin"):
        client.list_captions("video-1")


def test_two_brands_cannot_load_or_apply_each_others_proposals(env, tmp_path):
    api, client, store, asset = env
    change = insert(env)
    other = _brand(tmp_path, "Other", "channel-b")
    with pytest.raises(ChangeError):
        ResourceStore(other.raiz).load(change.id)
    with pytest.raises(ChangeError):
        apply_resource(api.client(other), store, change.id, change.fingerprint)
    assert not api.writes


@pytest.mark.parametrize("arguments", [
    {"action": "caption-insert", "language": "es", "name": "Español"},
    {"action": "caption-update", "track_id": "caption-1", "language": "fr"},
    {"action": "caption-delete"},
    {"action": "thumbnail-set", "draft": True},
])
def test_invalid_or_ambiguous_inputs_fail_without_writes(env, arguments):
    api, client, store, asset = env
    with pytest.raises(ChangeError):
        prepare_resource(client, store, video_id="video-1", **arguments)
    assert not api.writes


def test_asset_size_format_and_symlink_inspection(env, tmp_path):
    api, client, store, asset = env
    with pytest.raises(ChangeError):
        inspect_asset(asset, thumbnail=True)
    link = tmp_path / "link.srt"
    link.symlink_to(asset)
    with pytest.raises(ChangeError):
        inspect_asset(link, thumbnail=False)
    asset.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ChangeError):
        inspect_asset(asset, thumbnail=True)


@pytest.mark.parametrize("extension, data", [
    ("sbv", b"0:00:00.000,0:00:01.000\nHello\n"),
    ("sub", b"[SUBTITLE]\n00:00:00.00,00:00:01.00\nHello\n"),
    ("mpsub", b"FORMAT=TIME\n\n0 1\nHello\n"),
    ("lrc", b"[00:00.00]Hello\n"),
    ("smi", b"<SAMI><BODY><SYNC Start=0><P>Hello</P></BODY></SAMI>"),
    ("sami", b"<SAMI><BODY><SYNC Start=0><P>Hello</P></BODY></SAMI>"),
    ("rt", b'<window><time begin="00:00:00.0" end="00:00:01.0"/>Hello</window>'),
    ("ttml", b'<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="0s" end="1s">Hello</p></div></body></tt>'),
    ("dfxp", b'<tt xmlns="http://www.w3.org/2006/10/ttaf1"><body><div><p begin="0s" dur="1s">Hello</p></div></body></tt>'),
    ("scc", b"Scenarist_SCC V1.0\n\n00:00:00:00\t9420 9420\n"),
    ("vtt", b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n"),
])
def test_documented_text_formats_pass_supplied_bytes_unchanged(tmp_path, extension, data):
    file = tmp_path / f"supplied.{extension}"
    file.write_bytes(data)
    metadata, actual = inspect_asset(file, thumbnail=False)
    assert actual == data and metadata["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("status", [403, 409, 412])
def test_definite_write_rejection_is_recorded_and_never_retried(env, status):
    api, client, store, asset = env
    change = insert(env)
    api.on_write = lambda request: httpx.Response(status)
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == ("conflict" if status == 412 else "failed")
    apply_resource(client, store, change.id, change.fingerprint)
    assert len(api.writes) == 1


def test_accepted_caption_normalization_does_not_claim_byte_equality(env):
    api, client, store, asset = env
    change = insert(env)
    def write(request):
        api.on_write = None
        result = api.handler(request)
        api.bytes["new-caption"] = SRT.replace(b"\n", b"\r\n")
        return result
    api.on_write = write
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert not result.verified and result.status == "uncertain"
    assert result.result_id == "new-caption"


def test_delete_204_with_read_permission_loss_is_not_verified(env):
    api, client, store, asset = env
    change = prepare_resource(client, store, action="caption-delete", video_id="video-1", track_id="caption-1")
    def write(request):
        api.list_code = 403
        return httpx.Response(204)
    api.on_write = write
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain" and not result.verified


def test_caption_download_blocks_redirect_even_with_redirecting_client(env):
    api, client, store, asset = env
    original = api.handler
    def handler(request):
        if request.url.path.startswith("/youtube/v3/captions/"):
            api.requests.append(request)
            return httpx.Response(302, headers={"Location": "https://evil.invalid/steal"})
        return original(request)
    client.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(ResourceError):
        client.download_caption("video-1", "caption-1")
    assert all(r.url.host == "www.googleapis.com" for r in api.requests)


def test_failed_intent_persistence_prevents_effect(env, monkeypatch):
    api, client, store, asset = env
    change = insert(env)
    original = store.save
    def save(value):
        if value.status == "applying":
            raise ChangeError("fsync failed")
        original(value)
    monkeypatch.setattr(store, "save", save)
    with pytest.raises(ChangeError):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


def test_receipt_persistence_failure_leaves_intent_that_never_reinserts(env, monkeypatch):
    api, client, store, asset = env
    change = insert(env)
    original = store.save
    def save(value):
        if value.result_id:
            raise ChangeError("fsync failed")
        original(value)
    monkeypatch.setattr(store, "save", save)
    with pytest.raises(ChangeError):
        apply_resource(client, store, change.id, change.fingerprint)
    monkeypatch.setattr(store, "save", original)
    assert store.load(change.id).status == "applying"
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain" and len(api.writes) == 1


def test_caption_download_is_private_no_clobber_and_exact(env, tmp_path):
    from socialctl.management.resource_changes import save_download
    api, client, store, asset = env
    target = tmp_path / "download.srt"
    save_download(target, client.download_caption("video-1", "caption-1"))
    assert target.read_bytes() == OLD and target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ChangeError):
        save_download(target, b"replacement")
    assert target.read_bytes() == OLD


@pytest.mark.parametrize("extension", ["cap", "tds", "cin", "stl", "asc"])
def test_documented_opaque_formats_are_explicitly_provider_validated(tmp_path, extension):
    file = tmp_path / f"supplied.{extension}"
    file.write_bytes(b"\x00\xfeopaque supplied fixture")
    metadata, data = inspect_asset(file, thumbnail=False)
    assert metadata["local_format_validation"] == "extension_only/provider_validation_required"
    assert metadata["mime"] == "application/octet-stream" and data == file.read_bytes()


def test_oauth_refresh_never_follows_credential_redirects(env):
    from socialctl.models import Platform
    api, client, store, asset = env
    client.brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "expired", "expira_en": 0,
        "refresh_token": "synthetic-secret", "client_secret": "synthetic-client-secret"})
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://evil.invalid/steal"})
    client.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(ResourceError):
        client.list_captions("video-1")
    assert len(requests) == 1 and requests[0].url.host == "oauth2.googleapis.com"


def test_new_proposal_cannot_blindly_repeat_an_uncertain_insert(env):
    api, client, store, asset = env
    first = insert(env)
    second = insert(env)
    def timeout(request):
        raise httpx.ReadTimeout("lost")
    api.on_write = timeout
    apply_resource(client, store, first.id, first.fingerprint)
    with pytest.raises(ChangeError, match="uncertain|pending"):
        apply_resource(client, store, second.id, second.fingerprint)
    assert len(api.writes) == 1


def test_file_changed_during_ownership_reads_is_rejected(env):
    api, client, store, asset = env
    change = insert(env)
    original = client.list_captions
    def listing(video_id):
        rows = original(video_id)
        asset.write_bytes(OLD)
        return rows
    client.list_captions = listing
    with pytest.raises(ChangeError, match='file'):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


def test_missing_private_backup_stops_destructive_change(env):
    api, client, store, asset = env
    change = prepare_resource(client, store, action="caption-delete", video_id="video-1", track_id="caption-1")
    store.backup_path(change.id).unlink()
    with pytest.raises(ChangeError, match="backup"):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


def test_unknown_caption_format_and_xml_entities_are_rejected(tmp_path):
    file = tmp_path / "supplied.mp4"
    file.write_bytes(SRT)
    with pytest.raises(ChangeError):
        inspect_asset(file, thumbnail=False)
    file = tmp_path / "supplied.ttml"
    file.write_bytes(b'<!DOCTYPE tt [<!ENTITY x SYSTEM "file:///private">]><tt/>')
    with pytest.raises(ChangeError):
        inspect_asset(file, thumbnail=False)


def test_download_body_is_bounded_while_streaming(env, monkeypatch):
    import socialctl.management.youtube_resources as resources
    api, client, store, asset = env
    monkeypatch.setattr(resources, "MAX_CAPTION", 10)
    with pytest.raises(ChangeError, match="limit"):
        client.download_caption("video-1", "caption-1")


def test_mutated_immutable_proposal_is_rejected(env):
    api, client, store, asset = env
    change = insert(env)
    raw = json.loads(store.path_for(change.id).read_text())
    raw["language"] = "fr"
    store.path_for(change.id).write_text(json.dumps(raw))
    with pytest.raises(ChangeError, match="fingerprint"):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


def test_ambiguous_authenticated_channels_are_rejected(env):
    api, client, store, asset = env
    original = api.handler
    def handler(request):
        if request.url.path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": "channel-a"}, {"id": "channel-b"}]})
        return original(request)
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ChangeError, match="channel|identity"):
        insert((api, client, store, asset))
    assert not api.writes


def test_removed_account_configuration_fails_cleanly_before_apply(env):
    api, client, store, asset = env
    change = insert(env)
    client.brand.cuentas["youtube"] = {}
    with pytest.raises(ChangeError):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes


@pytest.mark.parametrize("metadata", [
    {"nextPageToken": "another-page", "pageInfo": {"totalResults": 2, "resultsPerPage": 1}},
    {"nextPageToken": "another-page", "pageInfo": {"totalResults": 1, "resultsPerPage": 1}},
    {"prevPageToken": "previous-page", "pageInfo": {"totalResults": 1, "resultsPerPage": 1}},
    *[{"nextPageToken": value, "pageInfo": {"totalResults": 1, "resultsPerPage": 1}}
      for value in (None, "", 0, False, [], {})],
    {},
    *[{"pageInfo": value} for value in (None, [], "invalid", {},
        {"totalResults": 1}, {"resultsPerPage": 1},
        {"totalResults": 2, "resultsPerPage": 1},
        {"totalResults": 0, "resultsPerPage": 1},
        {"totalResults": "1", "resultsPerPage": 1},
        {"totalResults": True, "resultsPerPage": 1},
        {"totalResults": 1, "resultsPerPage": 0},
        {"totalResults": 1, "resultsPerPage": -1},
        {"totalResults": 1, "resultsPerPage": 51},
        {"totalResults": 1, "resultsPerPage": 1.0},
        {"totalResults": 1, "resultsPerPage": "1"},
        {"totalResults": 1, "resultsPerPage": True})],
])
def test_incomplete_or_malformed_channel_identity_blocks_prewrite(env, metadata):
    api, client, store, asset = env
    change = insert(env)
    original = api.handler
    def handler(request):
        if request.url.path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": "channel-a"}], **metadata})
        return original(request)
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ChangeError, match="identity|channel"):
        apply_resource(client, store, change.id, change.fingerprint)
    assert not api.writes
    assert store.load(change.id).status == "proposed"


@pytest.mark.parametrize("capacity", [1, 2, 5, 50])
def test_complete_unique_channel_identity_without_continuation_is_accepted(env, capacity):
    api, client, store, asset = env
    original = api.handler
    def handler(request):
        if request.url.path == "/youtube/v3/channels":
            return httpx.Response(200, json={"items": [{"id": "channel-a"}],
                "pageInfo": {"totalResults": 1, "resultsPerPage": capacity}})
        return original(request)
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    change = insert(env)
    result = apply_resource(client, store, change.id, change.fingerprint)
    assert result.status == "verified" and len(api.writes) == 1
