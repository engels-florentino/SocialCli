"""Metadata contracts over local transport; no credentials/network/subprocess."""
import copy
import importlib
import json
import socket
import subprocess
from datetime import datetime, timezone

import httpx
import pytest

from socialctl.brands import crear_brand
from socialctl.management.changes import ChangeError
from socialctl.models import Platform


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    def blocked(*args, **kwargs):
        raise AssertionError("external network/subprocess prohibited")
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


@pytest.fixture
def metadata_clock(monkeypatch):
    from socialctl.management import metadata_parts
    class Clock(datetime):
        moment = datetime(2098, 1, 1, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.moment

        @classmethod
        def expire(cls):
            cls.moment = datetime(2100, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(metadata_parts, "datetime", Clock)
    return Clock


class API:
    def __init__(self, channel="channel-a"):
        self.channel = channel
        self.videos = {ident: {
            "id": ident, "etag": "etag-1",
            "snippet": {"channelId": channel, "title": "Old", "description": "Old text",
                        "categoryId": "27", "defaultLanguage": "es", "defaultAudioLanguage": "es",
                        "tags": ["internal"], "publishedAt": "2025-01-01T00:00:00Z"},
            "status": {"privacyStatus": "private", "embeddable": True, "license": "youtube",
                       "publicStatsViewable": True, "uploadStatus": "processed", "madeForKids": False,
                       "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False},
            "localizations": {"en": {"title": "Old translation", "description": "Old translation text"}},
            "contentDetails": {"duration": "PT1M"},
        } for ident in ("video-1", "video-2", "video-3")}
        self.writes = []
        self.reads = []
        self.failures = {}
        self.after_write = None

    def handler(self, request):
        assert request.url.host == "www.googleapis.com"
        if request.url.path.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": self.channel}]})
        assert request.url.path == "/youtube/v3/videos"
        if request.method == "GET":
            ident = request.url.params["id"]
            self.reads.append(ident)
            return httpx.Response(200, json={"items": [self.videos[ident]]})
        assert request.method == "PUT"
        body = json.loads(request.content)
        ident = body.pop("id")
        self.writes.append((ident, copy.deepcopy(body), dict(request.headers), request.url.params["part"]))
        failure = self.failures.get(ident)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret must not escape", request=request)
        if failure:
            return httpx.Response(failure, json={"error": {"message": "denied"}})
        assert request.headers["If-Match"] == self.videos[ident]["etag"]
        for part, fields in body.items():
            assert set(fields).isdisjoint({"uploadStatus", "madeForKids", "channelId", "publishedAt"})
            preserved = ({"channelId": self.channel} if part == "snippet" else {})
            self.videos[ident][part] = {**preserved, **fields}
        self.videos[ident]["etag"] += "x"
        if self.after_write:
            self.after_write(ident)
        return httpx.Response(200, json={"id": ident})

    def client(self, brand):
        module = importlib.import_module("socialctl.management.youtube_metadata")
        return module.YouTubeMetadataClient(brand, httpx.Client(transport=httpx.MockTransport(self.handler)))


def brand_at(tmp_path, name="MarcaA", channel="channel-a"):
    brand = crear_brand(tmp_path, name)
    brand.cuentas["youtube"] = {"channel_id": channel}
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "synthetic-local-token"})
    return brand


def parts():
    assert importlib.util.find_spec("socialctl.management.metadata_parts") is not None, "strict metadata parts missing"
    return importlib.import_module("socialctl.management.metadata_parts")


def test_metadata_preserves_each_mutable_part_and_scopes_conditional_put(tmp_path):
    module = parts()
    api = API()
    client = api.client(brand_at(tmp_path))
    observed = client.inspect("video-1")
    op = module.MetadataEdit(video_id="video-1", kind="privacy", patch={"privacyStatus": "unlisted"})
    before, after = module.propose_parts(observed, op)
    assert before["status"]["privacyStatus"] == "private"
    assert set(after) == {"status"}
    assert after["status"] == {"privacyStatus": "unlisted", "embeddable": True, "license": "youtube",
                               "publicStatsViewable": True, "selfDeclaredMadeForKids": False,
                               "containsSyntheticMedia": False}
    client.update_parts("video-1", after, etag=observed.etag)
    assert api.writes[0][3] == "status"
    assert api.writes[0][2]["if-match"] == "etag-1"


@pytest.mark.parametrize("kind,patch", [
    ("snippet", {"defaultAudioLanguage": "en"}), ("snippet", {"madeForKids": True}),
    ("status", {"privacyStatus": "public"}), ("privacy", {"privacyStatus": "oops"}),
    ("audience", {"selfDeclaredMadeForKids": "false"}), ("synthetic", {"containsSyntheticMedia": None}),
    ("localizations", {"en": {"title": "x", "description": "y", "extra": 1}}),
])
def test_strict_field_operation_allowlists(kind, patch):
    module = parts()
    with pytest.raises((ValueError, ChangeError)):
        module.MetadataEdit(video_id="video-1", kind=kind, patch=patch)


def test_translations_merge_languages_and_require_default_language(tmp_path):
    module = parts()
    api = API()
    client = api.client(brand_at(tmp_path))
    edit = module.MetadataEdit(video_id="video-1", kind="localizations",
                              patch={"fr": {"title": "Français", "description": "Texte"}})
    _, after = module.propose_parts(client.inspect("video-1"), edit)
    assert after == {"localizations": {"en": {"title": "Old translation", "description": "Old translation text"},
                                        "fr": {"title": "Français", "description": "Texte"}}}
    del api.videos["video-1"]["snippet"]["defaultLanguage"]
    with pytest.raises(ChangeError, match="defaultLanguage"):
        module.propose_parts(client.inspect("video-1"), edit)


def test_schedule_requires_explicit_history_assertion_private_and_future(tmp_path):
    module = parts()
    api = API()
    client = api.client(brand_at(tmp_path))
    with pytest.raises(ValueError, match="never_published"):
        module.MetadataEdit(video_id="video-1", kind="schedule", patch={"publishAt": "2099-01-01T00:00:00Z"})
    edit = module.MetadataEdit(video_id="video-1", kind="schedule", never_published=True,
                              patch={"publishAt": "2099-01-01T00:00:00Z", "privacyStatus": "private"})
    _, after = module.propose_parts(client.inspect("video-1"), edit)
    assert after["status"]["publishAt"] == "2099-01-01T00:00:00Z"
    assert after["status"]["privacyStatus"] == "private"


@pytest.mark.parametrize("text", ["0:01 A\n0:20 B\n0:40 C", "0:00 A\n0:20 B",
                                 "0:00 A\n0:05 B\n0:40 C", "0:00 A\n0:20 B\n0:55 C"])
def test_chapters_reject_invalid_starts_counts_and_final_duration(tmp_path, text):
    module = parts()
    client = API().client(brand_at(tmp_path))
    edit = module.MetadataEdit(video_id="video-1", kind="chapters", patch={"description": text})
    with pytest.raises(ChangeError, match="chapter"):
        module.propose_parts(client.inspect("video-1"), edit)


def test_chapters_preserve_supplied_surrounding_text_and_validate_duration(tmp_path):
    module = parts()
    client = API().client(brand_at(tmp_path))
    text = "Intro supplied\n\n0:00 First\n0:20 Second\n0:40 Third\n\nUser footer #visible"
    edit = module.MetadataEdit(video_id="video-1", kind="chapters", patch={"description": text})
    _, after = module.propose_parts(client.inspect("video-1"), edit)
    assert after["snippet"]["description"] == text
    assert after["snippet"]["defaultAudioLanguage"] == "es"


@pytest.mark.parametrize("part", ["snippet", "status"])
def test_unknown_remote_fields_and_wrong_owner_fail_closed(tmp_path, part):
    parts()
    api = API()
    client = api.client(brand_at(tmp_path))
    api.videos["video-1"][part]["futureMutable"] = "value"
    with pytest.raises(Exception, match="unknown"):
        client.inspect("video-1")
    del api.videos["video-1"][part]["futureMutable"]
    api.videos["video-1"]["snippet"]["channelId"] = "channel-other"
    with pytest.raises(Exception, match="belong"):
        client.inspect("video-1")
    assert api.writes == []


def test_audio_language_mutation_rejected_even_at_direct_client_boundary(tmp_path):
    module = parts()
    api = API()
    client = api.client(brand_at(tmp_path))
    snippet = module.editable_parts(client.inspect("video-1").resource)["snippet"]
    snippet["defaultAudioLanguage"] = "fr"
    with pytest.raises(Exception, match="defaultAudioLanguage"):
        client.update_parts("video-1", {"snippet": snippet}, etag="etag-1")
    assert api.writes == []


@pytest.mark.parametrize("patch", [{"publishAt": "20990101T000000Z", "privacyStatus": "private"},
                                  {"publishAt": "2099-01-01T00:00:00Z", "privacyStatus": []}])
def test_schedule_schema_rejects_non_rfc3339_and_non_scalar_values(patch):
    module = parts()
    with pytest.raises(ValueError):
        module.MetadataEdit(video_id="video-1", kind="schedule", never_published=True, patch=patch)


@pytest.mark.parametrize("kind,patch,part", [
    ("status", {"embeddable": False, "license": "creativeCommon", "publicStatsViewable": False}, "status"),
    ("audience", {"selfDeclaredMadeForKids": True}, "status"),
    ("synthetic", {"containsSyntheticMedia": True}, "status"),
    ("localizations", {"fr": {"title": "Titre", "description": "Description"}}, "localizations"),
])
def test_supported_operations_write_only_their_part_and_preserve_other_fields(tmp_path, kind, patch, part):
    module = parts()
    api = API()
    client = api.client(brand_at(tmp_path))
    observed = client.inspect("video-1")
    before, after = module.propose_parts(observed, module.MetadataEdit(video_id="video-1", kind=kind, patch=patch))
    client.update_parts("video-1", after, etag=observed.etag)
    assert api.writes[0][1] == after and api.writes[0][3] == part
    assert all(after[part][key] == value for key, value in before[part].items() if key not in patch)


@pytest.mark.parametrize("body", [{"status": {"privacyStatus": "unlisted"}}, {"localizations": {}}])
def test_direct_client_rejects_incomplete_part_instead_of_erasing_omitted_fields(tmp_path, body):
    parts()
    api = API()
    client = api.client(brand_at(tmp_path))
    with pytest.raises(Exception, match="omitted"):
        client.update_parts("video-1", body, etag="etag-1")
    assert api.writes == []


@pytest.mark.parametrize("kind,patch", [
    ("status", {"embeddable": False}), ("audience", {"selfDeclaredMadeForKids": True}),
    ("synthetic", {"containsSyntheticMedia": True}), ("privacy", {"privacyStatus": "private"}),
])
def test_all_status_proposals_reject_expired_preserved_publish_at(tmp_path, metadata_clock, kind, patch):
    module = parts()
    api = API()
    api.videos["video-1"]["status"]["publishAt"] = "2099-01-01T00:00:00Z"
    original = copy.deepcopy(api.videos["video-1"])
    client = api.client(brand_at(tmp_path))
    metadata_clock.expire()
    observed = client.inspect("video-1")  # Read-only inspection still works.
    with pytest.raises(ChangeError, match="publishAt.*future"):
        module.propose_parts(observed, module.MetadataEdit(video_id="video-1", kind=kind, patch=patch))
    assert api.writes == [] and api.videos["video-1"] == original


@pytest.mark.parametrize("expire_at", ["before_update", "final_auth"])
def test_direct_status_boundary_rejects_expired_preserved_publish_at(tmp_path, monkeypatch, metadata_clock, expire_at):
    from socialctl.management.youtube import YouTubeUpdateRejected
    module = parts()
    api = API()
    api.videos["video-1"]["status"]["publishAt"] = "2099-01-01T00:00:00Z"
    original = copy.deepcopy(api.videos["video-1"])
    client = api.client(brand_at(tmp_path))
    outgoing = {"status": module.editable_parts(client.inspect("video-1").resource)["status"]}
    outgoing["status"]["embeddable"] = False
    if expire_at == "before_update":
        metadata_clock.expire()
    else:
        authenticate = client._authenticated_channel
        calls = 0
        def delayed_auth(token):
            nonlocal calls
            result = authenticate(token)
            calls += 1
            if calls == 2:  # after richer validation, final pre-PUT authentication
                metadata_clock.expire()
            return result
        monkeypatch.setattr(client, "_authenticated_channel", delayed_auth)
    with pytest.raises(YouTubeUpdateRejected, match="publishAt.*future"):
        client.update_parts("video-1", outgoing, etag="etag-1")
    assert outgoing["status"]["publishAt"] == "2099-01-01T00:00:00Z"
    assert api.writes == [] and api.videos["video-1"] == original
