"""Owned resource workflows use only synthetic accounts, bytes and MockTransport."""
import copy
import json

import httpx
import pytest

from socialctl.management.changes import ApprovalMismatch, ChangeError
from socialctl.management.owned_changes import OwnedStore, prepare_owned, apply_owned, reconcile_owned
from socialctl.management.owned_schema import OwnedEdit
from socialctl.management.youtube_owned import YouTubeOwnedClient
from tests.test_youtube_management import _brand, _snippet
from tests.test_youtube_resources import PNG, multipart


def playlist(pid="playlist-1"):
    return {"kind": "youtube#playlist", "id": pid, "etag": "playlist-etag", "snippet": {
        "channelId": "channel-a", "title": "Supplied title", "description": "Keep description",
        "defaultLanguage": "es", "thumbnails": {}},
        "status": {"privacyStatus": "private", "podcastStatus": "disabled"},
        "localizations": {"en": {"title": "Keep title", "description": "Keep text"}}}


def item(i=0):
    return {"kind": "youtube#playlistItem", "id": f"item-{i}", "etag": f"item-etag-{i}",
        "snippet": {"playlistId": "playlist-1", "channelId": "channel-a", "position": i,
            "title": "Identical title", "resourceId": {"kind": "youtube#video", "videoId": f"video-{i}"}},
        "contentDetails": {"videoId": f"video-{i}", "note": "Keep note", "startAt": "2", "endAt": "20"}}


class API:
    def __init__(self):
        self.account = "channel-a"
        self.rows = {
            "channels": [{"kind": "youtube#channel", "id": "channel-a", "etag": "channel-etag",
                "snippet": {"title": "Current channel", "description": "Keep", "defaultLanguage": "es"},
                "brandingSettings": {"channel": {"title": "Current channel", "description": "Keep",
                    "keywords": "keep keywords", "country": "ES", "defaultLanguage": "es",
                    "unsubscribedTrailer": "video-1", "trackingAnalyticsAccountId": "UA-1"},
                    "image": {"bannerExternalUrl": "https://yt3.googleusercontent.com/old"}},
                "status": {"selfDeclaredMadeForKids": False, "madeForKids": False, "privacyStatus": "public"},
                "localizations": {"en": {"title": "Localized", "description": "Keep"}}}],
            "playlists": [playlist()], "playlistItems": [item(i) for i in range(3)],
            "videos": [{"kind": "youtube#video", "id": "video-1", "etag": "video-etag", "snippet": _snippet(),
                "recordingDetails": {"recordingDate": "2020-01-01T00:00:00Z"},
                "processingDetails": {"processingStatus": "succeeded"}, "fileDetails": {"fileName": "supplied.mp4"}}],
            "channelSections": [{"kind": "youtube#channelSection", "id": "section-1", "etag": "section-etag",
                "snippet": {"channelId": "channel-a", "type": "multiplePlaylists", "title": "Keep", "position": 0},
                "contentDetails": {"playlists": ["playlist-1"]}}],
            "playlistImages": [],
        }
        self.requests = []
        self.on_write = None
        self.on_read = None
        self.pages = None

    @property
    def writes(self):
        return [r for r in self.requests if r.method != "GET"]

    def handler(self, request):
        self.requests.append(request)
        if request.method == "GET" and self.on_read:
            result = self.on_read(request)
            if result is not None:
                return result
        resource = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            if resource == "channels" and "invideoBranding" in request.url.params.get("part", "").split(","):
                return httpx.Response(400, json={"error": {"errors": [{"reason": "unknownPart", "location": "part"}]}})
            if resource == "channels" and request.url.params.get("part") == "id":
                return httpx.Response(200, json={"items": [{"id": self.account}],
                    "pageInfo": {"totalResults": 1, "resultsPerPage": 5}})
            if self.pages and resource == "playlistItems" and "id" not in request.url.params:
                return self.pages(request)
            rows = copy.deepcopy(self.rows[resource])
            if "id" in request.url.params:
                rows = [r for r in rows if r["id"] == request.url.params["id"]]
            if resource == "channelSections":
                return httpx.Response(200, json={"kind": "youtube#channelSectionListResponse", "etag": "sections-etag", **({"items": rows} if rows else {})})
            return httpx.Response(200, json={"items": rows, "pageInfo": {"totalResults": len(rows), "resultsPerPage": 50}})
        if self.on_write:
            result = self.on_write(request)
            if result is not None:
                return result
        if resource == "insert" and "channelBanners" in request.url.path:
            return httpx.Response(200, json={"kind": "youtube#channelBannerResource", "url": "https://yt3.googleusercontent.com/new-banner"})
        if resource in {"set", "unset"}:
            # Neither public method exposes watermark readback on Channel.
            return httpx.Response(204)
        if request.method == "DELETE":
            self.rows[resource] = [r for r in self.rows[resource] if r["id"] != request.url.params["id"]]
            return httpx.Response(204)
        if request.headers.get("Content-Type", "").startswith("multipart/related"):
            body, _ = multipart(request)
        else:
            body = json.loads(request.content)
        if request.method == "POST":
            rid = "created-image:hero" if resource == "playlistImages" else "created-" + resource
            row = {"id": rid, **body}
            if resource != "playlistImages":
                row["etag"] = "created-etag"
                row["snippet"]["channelId"] = self.account
            self.rows[resource].append(row)
        else:
            row = next(r for r in self.rows[resource] if r["id"] == body["id"])
            if resource == "playlistItems" and "position" in body.get("snippet", {}):
                ordered = sorted(self.rows[resource], key=lambda r: r["snippet"]["position"])
                ordered.remove(row)
                ordered.insert(body["snippet"]["position"], row)
                for pos, other in enumerate(ordered):
                    other["snippet"]["position"] = pos
            for part, value in body.items():
                if part != "id":
                    readonly = {"channelId"}
                    if resource == "playlistItems":
                        readonly |= {"title", "description", "thumbnails", "channelTitle", "videoId", "videoPublishedAt"}
                    preserved = {k: v for k, v in row.get(part, {}).items() if k in readonly}
                    row[part] = copy.deepcopy(value)
                    row[part].update(preserved)
            if resource != "playlistImages":
                row["etag"] = "updated-" + str(len(self.writes))
        return httpx.Response(200, json=row)

    def client(self, brand):
        return YouTubeOwnedClient(brand, httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=True))


@pytest.fixture
def env(tmp_path):
    brand = _brand(tmp_path)
    api = API()
    return api, api.client(brand), OwnedStore(brand.raiz), tmp_path


def prepare(env, action, **kwargs):
    _, client, store, _ = env
    return prepare_owned(client, store, {"action": action, **kwargs})


def apply(env, change):
    return apply_owned(env[1], env[2], change.id, change.fingerprint)


def test_playlist_update_preserves_parts_and_conditional_intent(env):
    api, _, store, _ = env
    change = prepare(env, "playlist-update", playlist_id="playlist-1", patch={"snippet": {"title": "New title"}, "status": {"privacyStatus": "unlisted"}})
    assert not api.writes
    assert change.after["snippet"]["description"] == "Keep description"
    assert change.after["status"]["podcastStatus"] == "disabled"
    def intent(request):
        persisted = store.load(change.id)
        assert persisted.status == "applying" and persisted.journal[-1]["event"] == "write_intent"
    api.on_write = intent
    result = apply(env, change)
    assert result.status == "verified"
    assert api.writes[0].headers["If-Match"] == "playlist-etag"
    assert "channelId" not in json.loads(api.writes[0].content)["snippet"]
    assert api.writes[0].url.params["part"] == "snippet,status"


def test_channel_audience_is_separate_and_branding_preserves_omitted(env):
    change = prepare(env, "channel-update", channel_id="channel-a", patch={"brandingSettings": {"channel": {"description": "New description"}}})
    assert change.after["brandingSettings"]["channel"]["keywords"] == "keep keywords"
    assert change.after["brandingSettings"]["image"]["bannerExternalUrl"].endswith("/old")
    assert apply(env, change).status == "verified"
    change = prepare(env, "channel-audience", channel_id="channel-a", audience=True)
    assert apply(env, change).status == "verified"
    assert json.loads(env[0].writes[-1].content) == {"id": "channel-a", "status": {"selfDeclaredMadeForKids": True}}


@pytest.mark.parametrize("patch", [{"brandingSettings": {"channel": {"title": "Forbidden"}}}, {"status": {"selfDeclaredMadeForKids": True}}, {"invideoPromotion": {}}, {"brandingSettings": {}, "localizations": {}}])
def test_channel_forbidden_or_combined_parts_rejected(env, patch):
    with pytest.raises(ChangeError):
        prepare(env, "channel-update", channel_id="channel-a", patch=patch)
    assert not env[0].writes


def test_playlist_create_id_durable_and_reconcile_is_readonly(env):
    api, client, store, _ = env
    change = prepare(env, "playlist-create", patch={"snippet": {"title": "New", "description": "Supplied"}, "status": {"privacyStatus": "private"}})
    def read(request):
        if request.url.params.get("id") == "created-playlists":
            assert store.load(change.id).result_id == "created-playlists"
    api.on_read = read
    result = apply(env, change)
    assert result.status == "verified" and result.result_id == "created-playlists"
    apply(env, change)
    reconcile_owned(client, store, change.id)
    assert len(api.writes) == 1


@pytest.mark.parametrize("action,kw", [("playlist-create", {"patch": {"snippet": {"title": "New", "description": "Supplied"}, "status": {"privacyStatus": "private"}}}), ("item-insert", {"playlist_id": "playlist-1", "video_id": "video-new", "position": 1})])
def test_uncertain_create_blocks_fresh_uuid_without_guessing_titles(env, action, kw):
    api = env[0]
    change = prepare(env, action, **kw)
    api.on_write = lambda r: httpx.Response(503)
    assert apply(env, change).status == "uncertain"
    another = prepare(env, action, **kw)
    with pytest.raises(ChangeError, match="incierto"):
        apply(env, another)
    assert len(api.writes) == 1


def test_items_distinguish_membership_video_and_preserve_note_times(env):
    change = prepare(env, "item-update", playlist_id="playlist-1", item_id="item-1", patch={"contentDetails": {"note": "New note"}})
    assert change.after["contentDetails"]["startAt"] == "2"
    assert change.after["contentDetails"]["endAt"] == "20"
    assert apply(env, change).status == "verified"
    change = prepare(env, "item-delete", playlist_id="playlist-1", item_id="item-1")
    assert apply(env, change).status == "verified"
    assert env[0].rows["videos"] and env[0].writes[-1].url.params["id"] == "item-1"
    assert all("videos" not in r.url.path for r in env[0].writes)


def test_membership_dedup_uses_video_id_and_rejects_partial_list(env):
    with pytest.raises(ChangeError, match="existe"):
        prepare(env, "item-insert", playlist_id="playlist-1", video_id="video-1", position=0)
    env[0].pages = lambda r: httpx.Response(200, json={"items": [item()], "nextPageToken": "repeat", "pageInfo": {"totalResults": 3, "resultsPerPage": 1}})
    report = env[1].list_items("playlist-1")
    assert not report["complete"] and report["items"] and not report["absence_proven"]
    with pytest.raises(ChangeError, match="incomplet"):
        prepare(env, "item-insert", playlist_id="playlist-1", video_id="video-new", position=0)


def test_reorder_keeps_partial_results_and_never_replays(env):
    api, client, store, _ = env
    change = prepare(env, "items-reorder", playlist_id="playlist-1", positions=[{"item_id": "item-2", "position": 0}, {"item_id": "item-1", "position": 1}, {"item_id": "item-0", "position": 2}])
    def write(request):
        if len(api.writes) == 2:
            assert store.load(change.id).steps[0]["status"] == "verified"
            return httpx.Response(403)
    api.on_write = write
    result = apply(env, change)
    assert result.status == "partial" and result.steps[0]["result_id"] == "item-2"
    assert result.steps[1]["status"] == "failed"
    apply(env, change)
    reconcile_owned(client, store, change.id)
    assert len(api.writes) == 2


def test_section_update_and_create_preserve_types_and_content(env):
    change = prepare(env, "section-update", section_id="section-1", patch={"snippet": {"title": "New section"}})
    assert change.after["snippet"]["type"] == "multiplePlaylists"
    assert apply(env, change).status == "verified"
    change = prepare(env, "section-create", patch={"snippet": {"type": "singlePlaylist", "position": 1}, "contentDetails": {"playlists": ["playlist-1"]}})
    assert apply(env, change).result_id == "created-channelSections"


def test_recording_date_and_video_delete_are_separate_irreversible_proposals(env):
    change = prepare(env, "video-recording-date", video_id="video-1", recording_date="2021-01-01T00:00:00Z")
    assert apply(env, change).status == "verified"
    assert env[0].writes[-1].url.params["part"] == "recordingDetails"
    change = prepare(env, "video-delete", video_id="video-1")
    assert "irreversible" in " ".join(change.effects)
    assert apply(env, change).status == "verified"
    assert env[0].writes[-1].method == "DELETE" and not env[0].rows["videos"]


@pytest.mark.parametrize("failure", ["account", "owner", "etag", "unknown"])
def test_ownership_concurrency_unknown_fields_prevent_effects(env, failure):
    change = prepare(env, "playlist-update", playlist_id="playlist-1", patch={"snippet": {"title": "New"}})
    if failure == "account":
        env[0].account = "channel-b"
    elif failure == "owner":
        env[0].rows["playlists"][0]["snippet"]["channelId"] = "channel-b"
    elif failure == "etag":
        env[0].rows["playlists"][0]["etag"] = "edited"
    else:
        env[0].rows["playlists"][0]["snippet"]["unknownMutable"] = "unknown"
    with pytest.raises(ChangeError):
        apply(env, change)
    assert not env[0].writes


def test_exact_approval_and_tamper_detection(env):
    change = prepare(env, "playlist-delete", playlist_id="playlist-1")
    with pytest.raises(ApprovalMismatch):
        apply_owned(env[1], env[2], change.id, "wrong")
    path = env[2].path_for(change.id)
    raw = json.loads(path.read_text())
    raw["edit"]["playlist_id"] = "other"
    path.write_text(json.dumps(raw))
    with pytest.raises(ChangeError, match="huella"):
        apply(env, change)
    assert not env[0].writes


def test_playlist_images_use_discovery_parent_and_no_fabricated_etag(env):
    file = env[3] / "supplied.png"
    file.write_bytes(PNG)
    change = prepare(env, "image-insert", playlist_id="playlist-1", file=str(file), image_type="hero")
    assert "ETag" in " ".join(change.effects)
    result = apply(env, change)
    assert result.result_id == "created-image:hero" and result.status == "accepted_unverifiable"
    assert "If-Match" not in env[0].writes[-1].headers
    reads = [r for r in env[0].requests if r.url.path.endswith("playlistImages") and r.method == "GET"]
    assert all(r.url.params["parent"] == "playlist-1" and "playlistId" not in r.url.params for r in reads)
    change = prepare(env, "image-update", playlist_id="playlist-1", image_id=result.result_id, patch={"snippet": {"width": 1}})
    assert change.after["snippet"]["height"] == 1
    assert apply(env, change).status == "verified"
    assert env[0].writes[-1].url.path == "/youtube/v3/playlistImages"
    change = prepare(env, "image-delete", playlist_id="playlist-1", image_id=result.result_id)
    assert apply(env, change).status == "verified"


def test_supplied_watermark_204_does_not_claim_original_image_readback(env):
    file = env[3] / "supplied.png"
    file.write_bytes(PNG)
    change = prepare(env, "watermark-set", channel_id="channel-a", target_channel_id="channel-a", file=str(file), timing={"type": "offsetFromEnd", "offsetMs": "5000", "durationMs": "1000"})
    assert change.after["timing"]["durationMs"] == "1000"
    assert change.before["watermark_state"]["known"] is False
    assert apply(env, change).status == "accepted_unverifiable"
    assert multipart(env[0].writes[-1])[1] == PNG
    change = prepare(env, "watermark-unset", channel_id="channel-a")
    assert apply(env, change).status == "accepted_unverifiable"
    assert not env[2].load(change.id).verified
    assert reconcile_owned(env[1], env[2], change.id).status == "accepted_unverifiable"


def test_video_inspection_exposes_selected_owner_fields_without_mutability(env):
    result = env[1].inspect_video("video-1", parts=["processingDetails", "fileDetails", "recordingDetails", "snippet"])
    assert result["fileDetails"]["fileName"] == "supplied.mp4"
    assert result["snippet"]["defaultAudioLanguage"] == "es-ES"
    with pytest.raises(ChangeError):
        prepare(env, "video-recording-date", video_id="video-1", patch={"snippet": {"defaultAudioLanguage": "en"}})
    assert not env[0].writes


# Supplied fixture bytes with a 2048x1152 header. Local validation intentionally
# checks header/dimensions only; the provider still owns actual image decoding.
BANNER = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000800000004800806000000000000000000000049454e44ae426082")


def test_banner_upload_receipt_then_separate_exact_branding_approval(env):
    api, _, store, root = env
    file = root / "supplied-banner.png"
    file.write_bytes(BANNER)
    uploaded = prepare(env, "banner-upload", channel_id="channel-a", file=str(file))
    assert apply(env, uploaded).status == "accepted_unverifiable"
    assert len(api.writes) == 1 and api.writes[0].content == BANNER
    assert api.rows["channels"][0]["brandingSettings"]["image"]["bannerExternalUrl"].endswith("/old")
    proposed = prepare(env, "banner-apply", channel_id="channel-a", upload_change_id=uploaded.id)
    assert len(api.writes) == 1
    assert proposed.after["brandingSettings"]["channel"]["keywords"] == "keep keywords"
    def write(request):
        assert store.load(uploaded.id).receipt["url"] == proposed.after["brandingSettings"]["image"]["bannerExternalUrl"]
        assert store.load(proposed.id).journal[-1]["event"] == "write_intent"
    api.on_write = write
    assert apply(env, proposed).status == "verified"
    assert json.loads(api.writes[-1].content)["brandingSettings"]["channel"]["title"] == "Current channel"


@pytest.mark.parametrize("url", ["https://evil.invalid/banner", "http://yt3.googleusercontent.com/x", "https://yt3.googleusercontent.com@evil.invalid/x", "not-a-url"])
def test_bad_banner_response_never_applies_or_reuploads(env, url):
    file = env[3] / "banner.png"
    file.write_bytes(BANNER)
    change = prepare(env, "banner-upload", channel_id="channel-a", file=str(file))
    env[0].on_write = lambda r: httpx.Response(200, json={"url": url})
    assert apply(env, change).status == "uncertain"
    assert env[2].load(change.id).receipt["url"] == url
    with pytest.raises(ChangeError):
        prepare(env, "banner-apply", channel_id="channel-a", upload_change_id=change.id)
    apply(env, change)
    assert len(env[0].writes) == 1


def test_image_media_update_uses_discovery_multipart_and_exact_dimensions(env):
    env[0].rows["playlistImages"] = [{"id": "playlist-1:hero", "snippet": {"playlistId": "playlist-1", "type": "hero", "width": 20, "height": 20}}]
    file = env[3] / "image.png"
    file.write_bytes(PNG)
    change = prepare(env, "image-update", playlist_id="playlist-1", image_id="playlist-1:hero", file=str(file))
    assert change.after["snippet"]["width"] == change.after["snippet"]["height"] == 1
    assert change.asset["local_max_bytes"] == 50 * 1024 * 1024
    assert apply(env, change).status == "accepted_unverifiable"
    request = env[0].writes[-1]
    assert request.method == "PUT" and request.url.path == "/upload/youtube/v3/playlistImages"
    assert multipart(request)[1] == PNG and "If-Match" not in request.headers


@pytest.mark.parametrize("mode", ["changed", "symlink", "directory", "oversized", "rectangular"])
def test_asset_constraints_fail_before_effects(env, mode):
    from socialctl.management.owned_assets import inspect_owned_asset
    file = env[3] / "image.png"
    file.write_bytes(PNG)
    if mode == "changed":
        change = prepare(env, "image-insert", playlist_id="playlist-1", image_type="hero", file=str(file))
        file.write_bytes(BANNER)
        with pytest.raises(ChangeError):
            apply(env, change)
    elif mode == "symlink":
        link = env[3] / "symlink.png"
        link.symlink_to(file)
        with pytest.raises(ChangeError):
            inspect_owned_asset(link, "image-insert")
    elif mode == "directory":
        with pytest.raises(ChangeError):
            inspect_owned_asset(env[3], "image-insert")
    else:
        file.write_bytes(BANNER if mode == "rectangular" else b"x" * (2 * 1024 * 1024 + 1))
        with pytest.raises(ChangeError):
            inspect_owned_asset(file, "image-insert")
    assert not env[0].writes


@pytest.mark.parametrize("mode", ["timeout", "redirect", "bad-json", "wrong-kind", "wrong-id"])
def test_update_uncertainty_is_durable_and_never_replays(env, mode):
    change = prepare(env, "playlist-update", playlist_id="playlist-1", patch={"snippet": {"title": "New"}})
    def write(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("must-not-leak-token")
        if mode == "redirect":
            return httpx.Response(307, headers={"Location": "https://evil.invalid/steal"})
        if mode == "bad-json":
            return httpx.Response(200, content=b"bad")
        return httpx.Response(200, json={"id": "playlist-1" if mode == "wrong-kind" else "other-id", "kind": "youtube#video"})
    env[0].on_write = write
    result = apply(env, change)
    assert result.status == "uncertain"
    assert "must-not-leak-token" not in str(result.model_dump())
    apply(env, change)
    another = prepare(env, "playlist-update", playlist_id="playlist-1", patch={"snippet": {"title": "Different"}})
    with pytest.raises(ChangeError, match="incierto"):
        apply(env, another)
    assert len(env[0].writes) == 1 and all(r.url.host == "www.googleapis.com" for r in env[0].requests)


def test_failed_intent_persistence_means_zero_effects(env, monkeypatch):
    change = prepare(env, "playlist-delete", playlist_id="playlist-1")
    save = env[2].save
    def failing_save(value):
        if value.status == "applying":
            raise ChangeError("fsync failure")
        return save(value)
    monkeypatch.setattr(env[2], "save", failing_save)
    with pytest.raises(ChangeError, match="fsync"):
        apply(env, change)
    assert not env[0].writes


def test_unpersisted_create_receipt_never_repeats_after_restart(env, monkeypatch):
    change = prepare(env, "playlist-create", patch={"snippet": {"title": "New", "description": "Exact"}, "status": {"privacyStatus": "private"}})
    save = env[2].save
    def failing_save(value):
        if value.result_id:
            raise ChangeError("receipt fsync failure")
        return save(value)
    monkeypatch.setattr(env[2], "save", failing_save)
    with pytest.raises(ChangeError, match="fsync"):
        apply(env, change)
    monkeypatch.setattr(env[2], "save", save)
    assert apply(env, change).status == "uncertain"
    assert len(env[0].writes) == 1


def test_readback_failure_preserves_valid_create_id(env):
    change = prepare(env, "playlist-create", patch={"snippet": {"title": "New", "description": "Exact"}, "status": {"privacyStatus": "private"}})
    def read(request):
        if request.url.params.get("id") == "created-playlists":
            return httpx.Response(403)
    env[0].on_read = read
    assert apply(env, change).status == "uncertain"
    assert env[2].load(change.id).result_id == "created-playlists"
    env[0].on_read = None
    assert reconcile_owned(env[1], env[2], change.id).status == "verified"
    assert len(env[0].writes) == 1


def test_image_empty_omission_requires_explicit_zero_total(env):
    def read(request):
        if request.url.path.endswith("playlistImages"):
            return httpx.Response(200, json={"kind": "youtube#playlistImageListResponse", "pageInfo": {"totalResults": 0, "resultsPerPage": 50}})
    env[0].on_read = read
    assert env[1].list_images("playlist-1")["complete"]
    env[0].on_read = lambda r: httpx.Response(200, json={"kind": "youtube#playlistImageListResponse"}) if r.url.path.endswith("playlistImages") else None
    assert not env[1].list_images("playlist-1")["complete"]


def test_explicit_id_count_contradiction_is_not_absence(env):
    change = prepare(env, "video-delete", video_id="video-1")
    def read(request):
        if env[0].writes and request.url.path.endswith("videos"):
            return httpx.Response(200, json={"items": [], "pageInfo": {"totalResults": 1, "resultsPerPage": 50}})
    env[0].on_read = read
    assert apply(env, change).status == "uncertain"


def test_successful_reorder(env):
    edit = {"playlist_id": "playlist-1", "positions": [{"item_id": "item-2", "position": 0}, {"item_id": "item-1", "position": 1}, {"item_id": "item-0", "position": 2}]}
    change = prepare(env, "items-reorder", **edit)
    assert apply(env, change).status == "verified"
    assert len(env[0].writes) == 2 and all(s["status"] == "verified" for s in env[2].load(change.id).steps)


@pytest.mark.parametrize("patch", [{"status": {"privacyStatus": []}}, {"status": {"podcastStatus": []}}])
def test_bad_playlist_enum_types_are_safe_validation_errors(env, patch):
    with pytest.raises(ChangeError):
        prepare(env, "playlist-update", playlist_id="playlist-1", patch=patch)
    assert not env[0].writes


def test_unknown_existing_mutable_nested_field_is_not_discarded(env):
    env[0].rows["channels"][0]["brandingSettings"]["channel"]["futureMutable"] = "retain"
    with pytest.raises(ChangeError, match="desconocid"):
        prepare(env, "channel-update", channel_id="channel-a", patch={"brandingSettings": {"channel": {"description": "New"}}})
    assert not env[0].writes


def test_localization_patch_keeps_omitted_languages_and_fields(env):
    change = prepare(env, "playlist-update", playlist_id="playlist-1", patch={"localizations": {"en": {"title": "New"}, "fr": {"title": "Nouveau", "description": "Texte"}}})
    assert change.after["localizations"]["en"]["description"] == "Keep text"
    assert apply(env, change).status == "verified"


@pytest.mark.parametrize("action,kwargs", [("playlist-delete", {"playlist_id": "playlist-1"}), ("section-delete", {"section_id": "section-1"})])
def test_owned_delete_variants_confirm_receipt_and_readback(env, action, kwargs):
    change = prepare(env, action, **kwargs)
    assert apply(env, change).status == "verified"
    assert env[2].load(change.id).receipt == {"http_status": 204}
    assert env[0].writes[-1].method == "DELETE"


def test_delete_timeout_and_missing_resource_is_still_uncertain(env):
    change = prepare(env, "video-delete", video_id="video-1")
    def write(request):
        env[0].rows["videos"] = []
        raise httpx.ReadTimeout("synthetic timeout")
    env[0].on_write = write
    assert apply(env, change).status == "uncertain"
    assert not env[2].load(change.id).verified
    apply(env, change)
    assert len(env[0].writes) == 1


@pytest.mark.parametrize("field", ["startAt", "endAt"])
def test_deprecated_item_time_mutation_is_rejected_but_preservation_remains(env, field):
    with pytest.raises(ChangeError, match="no editable|no editables|desconocid"):
        prepare(env, "item-update", playlist_id="playlist-1", item_id="item-1", patch={"contentDetails": {field: "30"}})
    assert not env[0].writes


def test_podcast_enable_requires_complete_image_eligibility(env):
    with pytest.raises(ChangeError, match="imagen"):
        prepare(env, "playlist-update", playlist_id="playlist-1", patch={"status": {"podcastStatus": "enabled"}})
    env[0].rows["playlistImages"] = [{"id": "playlist-1:hero", "snippet": {"playlistId": "playlist-1", "type": "hero", "width": 20, "height": 20}}]
    change = prepare(env, "playlist-update", playlist_id="playlist-1", patch={"status": {"podcastStatus": "enabled"}})
    assert change.before["image_eligibility"][0]["id"] == "playlist-1:hero"
    assert apply(env, change).status == "verified"


def test_item_note_limit_is_validated_before_effect(env):
    with pytest.raises(ChangeError, match="280"):
        prepare(env, "item-update", playlist_id="playlist-1", item_id="item-1", patch={"contentDetails": {"note": "x" * 281}})
    assert not env[0].writes


def test_reorder_remote_drift_after_first_move_stops_remaining_effects(env):
    change = prepare(env, "items-reorder", playlist_id="playlist-1", positions=[{"item_id": "item-2", "position": 0}, {"item_id": "item-1", "position": 1}, {"item_id": "item-0", "position": 2}])
    def read(request):
        stored = env[2].load(change.id)
        if stored.steps and stored.steps[0]["status"] == "verified":
            env[0].rows["playlistItems"][0]["contentDetails"]["note"] = "Other editor"
    env[0].on_read = read
    result = apply(env, change)
    assert result.status == "partial" and len(env[0].writes) == 1
    assert result.steps[0]["status"] == "verified"


def test_listing_paginates_fifty_plus_one_and_stops_at_bound(env):
    # All rows are supplied fixtures with stable membership and resource IDs.
    def pages(request):
        rows = [item(i) for i in range(50)] if "pageToken" not in request.url.params else [item(50)]
        payload = {"items": rows, "pageInfo": {"totalResults": 51, "resultsPerPage": 50}}
        if "pageToken" not in request.url.params:
            payload["nextPageToken"] = "second"
        return httpx.Response(200, json=payload)
    env[0].pages = pages
    full = env[1].list_items("playlist-1")
    assert full["complete"] and len(full["items"]) == 51 and full["pages"] == 2
    partial = env[1].list_items("playlist-1", max_pages=1)
    assert not partial["complete"] and len(partial["items"]) == 50


def test_image_remote_drift_is_rejected_before_unconditional_effect(env):
    row = {"id": "playlist-1:hero", "snippet": {"playlistId": "playlist-1", "type": "hero", "width": 20, "height": 20}}
    env[0].rows["playlistImages"] = [row]
    change = prepare(env, "image-delete", playlist_id="playlist-1", image_id=row["id"])
    row["snippet"]["width"] = 30
    with pytest.raises(ChangeError, match="conflict"):
        apply(env, change)
    assert not env[0].writes


def test_known_existing_id_is_not_accepted_as_new_creation_identity(env):
    change = prepare(env, "section-create", patch={"snippet": {"type": "multiplePlaylists", "title": "Keep", "position": 0}, "contentDetails": {"playlists": ["playlist-1"]}})
    env[0].on_write = lambda r: httpx.Response(200, json=env[0].rows["channelSections"][0])
    result = apply(env, change)
    assert result.status == "uncertain" and result.result_id == "section-1"
    assert not result.receipt["identity_valid"]


@pytest.mark.parametrize("url,method", [("https://evil.invalid/youtube/v3/playlists", "GET"), ("http://www.googleapis.com/youtube/v3/playlists", "GET"), ("https://www.googleapis.com/youtube/v3/search", "POST"), ("https://www.googleapis.com/youtube/v3/channels", "DELETE")])
def test_owned_transport_rejects_nonallowlisted_routes(env, url, method):
    count = len(env[0].requests)
    with pytest.raises(ChangeError):
        env[1]._request(method, url)
    assert len(env[0].requests) == count


def test_corrupt_outgoing_body_cannot_bypass_resource_schema_with_new_digest(env):
    from socialctl.management.owned_changes import fingerprint
    change = prepare(env, "channel-update", channel_id="channel-a", patch={"brandingSettings": {"channel": {"description": "New"}}})
    change.after["brandingSettings"]["channel"]["title"] = "Forbidden title mutation"
    change.fingerprint = fingerprint(change)
    env[2].save(change)
    with pytest.raises(ChangeError, match="esquema|cuerpo|propuesta"):
        apply(env, change)
    assert not env[0].writes


def test_direct_effect_rejects_cross_resource_dispatch(env):
    edit = OwnedEdit(action="playlist-delete", playlist_id="playlist-1")
    with pytest.raises(ChangeError):
        env[1].effect(edit, "videos", "video-1", {}, etag="video-etag")
    assert not env[0].writes


@pytest.mark.parametrize("opaque_id", ["playlist-1:hero", "playlist-1/hero", "playlist-1|hero", "playlist-1+hero", "playlist-1?type=hero#image"])
def test_opaque_playlist_image_ids_remain_exact_encoded_data(env, opaque_id):
    env[0].rows["playlistImages"] = [{"id": opaque_id, "snippet": {"playlistId": "playlist-1", "type": "hero", "width": 1, "height": 1}}]
    change = prepare(env, "image-delete", playlist_id="playlist-1", image_id=opaque_id)
    assert apply(env, change).status == "verified"
    request = env[0].writes[0]
    assert request.url.path == "/youtube/v3/playlistImages" and request.url.params["id"] == opaque_id


@pytest.mark.parametrize("opaque_id", ["", "x" * 513, "playlist\nhero", "playlist hero", "https://example.invalid/image", "user:pass@host/image"])
def test_malformed_image_ids_rejected(env, opaque_id):
    with pytest.raises(ChangeError):
        prepare(env, "image-delete", playlist_id="playlist-1", image_id=opaque_id)
    assert not env[0].writes


def test_image_update_discovery_size_bound_is_distinct_from_insert(env):
    from socialctl.management.owned_assets import inspect_owned_asset
    # Synthetic supplied bytes only: header checking does not claim decodability.
    file = env[3] / "supplied-large-image.png"
    file.write_bytes(PNG[:-8] + b"x" * (2 * 1024 * 1024) + PNG[-8:])
    asset, data = inspect_owned_asset(file, "image-update")
    assert asset["local_max_bytes"] == 52428800 and len(data) > 2 * 1024 * 1024
    with pytest.raises(ChangeError, match="límite"):
        inspect_owned_asset(file, "image-insert")
    with file.open("wb") as supplied:
        supplied.truncate(52428801)
    with pytest.raises(ChangeError, match="52428800"):
        inspect_owned_asset(file, "image-update")
    assert not env[0].writes


def test_watermark_timeout_never_infers_success_from_missing_public_state(env):
    file = env[3] / "watermark.png"
    file.write_bytes(PNG)
    change = prepare(env, "watermark-set", channel_id="channel-a", target_channel_id="channel-a", file=str(file), timing={"type": "offsetFromEnd", "offsetMs": "5000", "durationMs": "omit"})
    def write(request):
        raise httpx.ReadTimeout("synthetic timeout")
    env[0].on_write = write
    assert apply(env, change).status == "uncertain"
    assert env[2].load(change.id).receipt is None
    assert reconcile_owned(env[1], env[2], change.id).status == "uncertain"
    apply(env, change)
    assert len(env[0].writes) == 1


def test_default_channel_inspection_never_requests_unsupported_part(env):
    assert env[1].inspect_channel("channel-a")["id"] == "channel-a"
    with pytest.raises(ChangeError, match="partes"):
        env[1].inspect_channel("channel-a", parts=["invideoBranding"])
    for request in env[0].requests:
        assert "invideoBranding" not in request.url.params.get("part", "").split(",")


@pytest.mark.parametrize("timing", [{"type": "offsetFromEnd", "offsetMs": "5000"}, {"type": "offsetFromEnd", "offsetMs": "5000", "durationMs": None}])
def test_watermark_unknown_before_requires_explicit_duration_value_or_omission(env, timing):
    file = env[3] / "watermark.png"
    file.write_bytes(PNG)
    with pytest.raises(ChangeError):
        prepare(env, "watermark-set", channel_id="channel-a", target_channel_id="channel-a", file=str(file), timing=timing)
    assert not env[0].writes


def test_watermark_duration_omission_is_explicit_replacement_not_preservation(env):
    file = env[3] / "watermark.png"
    file.write_bytes(PNG)
    change = prepare(env, "watermark-set", channel_id="channel-a", target_channel_id="channel-a", file=str(file), timing={"type": "offsetFromEnd", "offsetMs": "5000", "durationMs": "omit"})
    assert change.after == {"timing": {"type": "offsetFromEnd", "offsetMs": "5000"}, "targetChannelId": "channel-a"}
    assert change.edit["timing"]["durationMs"] == "omit"
    assert change.before["watermark_state"]["known"] is False
    assert apply(env, change).status == "accepted_unverifiable"
    assert not env[2].load(change.id).verified
    assert multipart(env[0].writes[-1])[0] == change.after


def section_missing(request, *, code=404, reason="channelSectionNotFound"):
    if request.url.path.endswith("/channelSections") and "id" in request.url.params:
        return httpx.Response(code, json={"error": {"errors": [{"reason": reason}], "message": "SENSITIVE_PROVIDER_MESSAGE"}})


def test_section_delete_204_then_documented_404_is_verified(env):
    change = prepare(env, "section-delete", section_id="section-1")
    def write(request):
        # Receipt must be saved before the readback request.
        def read(request):
            if request.url.path.endswith("/channelSections"):
                assert env[2].load(change.id).receipt == {"http_status": 204}
            return section_missing(request)
        env[0].on_read = read
        return httpx.Response(204)
    env[0].on_write = write
    assert apply(env, change).status == "verified"
    assert env[2].load(change.id).verified
    assert len(env[0].writes) == 1


@pytest.mark.parametrize("code,reason", [(404, "notFound"), (404, "channelNotFound"), (403, "channelSectionNotFound"), (403, "channelSectionForbidden")])
def test_section_delete_unrelated_error_does_not_prove_absence(env, code, reason):
    change = prepare(env, "section-delete", section_id="section-1")
    def write(request):
        env[0].on_read = lambda request: section_missing(request, code=code, reason=reason)
        return httpx.Response(204)
    env[0].on_write = write
    assert apply(env, change).status == "uncertain"
    assert not env[2].load(change.id).verified


@pytest.mark.parametrize("documented_404", [False, True])
def test_section_missing_without_delete_receipt_is_not_success(env, documented_404):
    change = prepare(env, "section-delete", section_id="section-1")
    def write(request):
        env[0].rows["channelSections"] = []
        if documented_404:
            env[0].on_read = section_missing
        raise httpx.ReadTimeout("synthetic timeout")
    env[0].on_write = write
    assert apply(env, change).status == "uncertain"
    assert reconcile_owned(env[1], env[2], change.id).status == "uncertain"
    assert env[2].load(change.id).receipt is None
    assert len(env[0].writes) == 1


def test_empty_sections_unpaginated_envelope_can_prepare_first_section(env):
    env[0].rows["channelSections"] = []
    report = env[1].list_sections()
    assert report["complete"] and report["items"] == [] and report["pages"] == 1
    assert not report["absence_proven"]
    change = prepare(env, "section-create", patch={"snippet": {"type": "singlePlaylist", "position": 0}, "contentDetails": {"playlists": ["playlist-1"]}})
    assert apply(env, change).status == "verified"
    for request in env[0].requests:
        if request.method == "GET" and request.url.path.endswith("/channelSections"):
            assert not {"pageToken", "maxResults"} & set(request.url.params)


@pytest.mark.parametrize("payload", [{}, {"kind": "youtube#channelSectionListResponse"},
    {"kind": "youtube#wrong", "etag": "etag"},
    {"kind": "youtube#channelSectionListResponse", "etag": "etag", "items": None},
    {"kind": "youtube#channelSectionListResponse", "etag": "etag", "nextPageToken": "next"},
    {"kind": "youtube#channelSectionListResponse", "etag": "etag", "pageInfo": {"totalResults": 0}},
])
def test_sections_reject_malformed_or_invented_pagination_envelope(env, payload):
    env[0].on_read = lambda r: httpx.Response(200, json=payload) if r.url.path.endswith("/channelSections") else None
    assert not env[1].list_sections()["complete"]
    with pytest.raises(ChangeError):
        env[1].one("channelSections", "section-1", optional=True)


@pytest.mark.parametrize("reorder", [False, True])
def test_manual_sort_reason_is_durable_without_provider_message(env, reorder):
    if reorder:
        change = prepare(env, "items-reorder", playlist_id="playlist-1", positions=[{"item_id": f"item-{i}", "position": p} for p, i in enumerate([2, 1, 0])])
    else:
        change = prepare(env, "item-update", playlist_id="playlist-1", item_id="item-1", patch={"snippet": {"position": 0}})
    env[0].on_write = lambda r: httpx.Response(400, json={"error": {"errors": [{"reason": "manualSortRequired", "message": "SENSITIVE_PROVIDER_MESSAGE"}], "message": "SENSITIVE_PROVIDER_MESSAGE"}})
    assert apply(env, change).status == "failed"
    journal = json.dumps(env[2].load(change.id).journal)
    assert "manualSortRequired" in journal
    assert "SENSITIVE_PROVIDER_MESSAGE" not in journal


@pytest.mark.parametrize("field,limit", [("keywords", 500), ("description", 1000)])
def test_channel_branding_text_documented_bounds(env, field, limit):
    with pytest.raises(ChangeError, match=str(limit)):
        prepare(env, "channel-update", channel_id="channel-a", patch={"brandingSettings": {"channel": {field: "é" * (limit + 1)}}})
    change = prepare(env, "channel-update", channel_id="channel-a", patch={"brandingSettings": {"channel": {field: "é" * limit}}})
    assert apply(env, change).status == "verified"


def test_section_documented_absence_cannot_bypass_authenticated_binding(env):
    change = prepare(env, "section-delete", section_id="section-1")
    def write(request):
        env[0].account = "other-channel"
        env[0].on_read = section_missing
        return httpx.Response(204)
    env[0].on_write = write
    assert apply(env, change).status == "uncertain"
    assert env[2].load(change.id).receipt == {"http_status": 204}


def test_section_not_found_reason_is_not_generic_optional_absence(env):
    from socialctl.management.youtube_resources import ResourceError
    env[0].on_read = lambda r: httpx.Response(404, json={"error": {"errors": [{"reason": "channelSectionNotFound"}]}}) if r.url.path.endswith("/playlists") else None
    with pytest.raises(ResourceError):
        env[1].one("playlists", "playlist-1", optional=True)


@pytest.mark.parametrize("extra", [{"items": []}, {"items": [item()] * 11}])
def test_sections_explicit_empty_and_row_bound(env, extra):
    payload = {"kind": "youtube#channelSectionListResponse", "etag": "etag", **extra}
    env[0].on_read = lambda r: httpx.Response(200, json=payload) if r.url.path.endswith("/channelSections") else None
    assert env[1].list_sections()["complete"] is (not extra["items"])


SECTION_ID = "UCsynthetic_owner-channel.section_abc-123"


@pytest.mark.parametrize("opaque_id", [SECTION_ID, "section.parent/child|part+value?x#fragment"])
def test_section_opaque_id_read_update_and_delete_roundtrip(env, opaque_id):
    env[0].rows["channelSections"][0]["id"] = opaque_id
    assert env[1].list_sections()["items"][0]["id"] == opaque_id
    change = prepare(env, "section-update", section_id=opaque_id, patch={"snippet": {"title": "New"}})
    result = apply(env, change)
    assert result.status == "verified" and result.result_id == opaque_id
    assert result.receipt == {"id": opaque_id, "identity_valid": True}
    assert json.loads(env[0].writes[-1].content)["id"] == opaque_id
    change = prepare(env, "section-delete", section_id=opaque_id)
    assert apply(env, change).status == "verified"
    for request in env[0].requests:
        if "channelSections" in request.url.path:
            assert request.url.path == "/youtube/v3/channelSections"
            if "id" in request.url.params:
                assert request.url.params["id"] == opaque_id


def test_section_create_composite_receipt_survives_readback_failure_reconcile(env):
    def write(request):
        body = json.loads(request.content)
        row = {"id": SECTION_ID, "kind": "youtube#channelSection", "etag": "created-etag", **body}
        row["snippet"]["channelId"] = env[0].account
        env[0].rows["channelSections"].append(row)
        env[0].on_read = lambda r: httpx.Response(503) if r.url.path.endswith("/channelSections") else None
        return httpx.Response(200, json=row)
    change = prepare(env, "section-create", patch={"snippet": {"type": "singlePlaylist", "position": 1}, "contentDetails": {"playlists": ["playlist-1"]}})
    env[0].on_write = write
    assert apply(env, change).status == "uncertain"
    stored = env[2].load(change.id)
    assert stored.result_id == SECTION_ID and stored.receipt["identity_valid"]
    env[0].on_read = None
    assert reconcile_owned(env[1], env[2], change.id).status == "verified"
    assert len(env[0].writes) == 1


@pytest.mark.parametrize("bad_id", ["https://www.youtube.com/section/foo", "https://user:pass@example.com/x", "user@host", "section.a,section.b", "section\nother", "section a", "", "x" * 513])
def test_section_malformed_ids_rejected_before_lookup(env, bad_id):
    with pytest.raises(ChangeError):
        prepare(env, "section-delete", section_id=bad_id)
    assert not env[0].requests


@pytest.mark.parametrize("action,kwargs", [("section-update", {"patch": {"snippet": {"title": "New"}}}),
    ("section-update", {"patch": {"snippet": {"type": "multiplePlaylists", "title": "New"}}})])
def test_unknown_section_type_is_readable_but_mutation_fails_closed(env, action, kwargs):
    env[0].rows["channelSections"][0]["snippet"]["type"] = "channelsectiontypeundefined"
    report = env[1].list_sections()
    assert report["complete"] and report["items"][0]["snippet"]["type"] == "channelsectiontypeundefined"
    assert env[1].one("channelSections", "section-1")["snippet"]["type"] == "channelsectiontypeundefined"
    with pytest.raises(ChangeError):
        prepare(env, action, section_id="section-1", **kwargs)
    assert not env[0].writes


def test_unknown_section_type_still_allows_exact_id_only_deletion(env):
    env[0].rows["channelSections"][0]["id"] = SECTION_ID
    env[0].rows["channelSections"][0]["snippet"]["type"] = "channelsectiontypeundefined"
    change = prepare(env, "section-delete", section_id=SECTION_ID)
    assert change.before["resource"]["snippet"]["type"] == "channelsectiontypeundefined"
    assert "irreversible" in " ".join(change.effects)
    assert apply(env, change).status == "verified"
    assert env[0].writes[-1].url.params["id"] == SECTION_ID
    assert not env[0].writes[-1].content


def test_section_dot_ids_do_not_broaden_video_ids(env):
    with pytest.raises(ChangeError):
        prepare(env, "video-delete", video_id=SECTION_ID)
    assert not env[0].requests


@pytest.mark.parametrize("failure", ["account", "owner", "returned_id"])
def test_section_composite_id_keeps_authenticated_owner_and_exact_identity(env, failure):
    env[0].rows["channelSections"][0]["id"] = SECTION_ID
    if failure == "account":
        env[0].account = "foreign-channel"
    elif failure == "owner":
        env[0].rows["channelSections"][0]["snippet"]["channelId"] = "foreign-channel"
    else:
        row = copy.deepcopy(env[0].rows["channelSections"][0])
        row["id"] = "different.composite-id"
        env[0].on_read = lambda r: httpx.Response(200, json={"kind": "youtube#channelSectionListResponse", "etag": "etag", "items": [row]}) if r.url.path.endswith("/channelSections") else None
    with pytest.raises(ChangeError):
        prepare(env, "section-delete", section_id=SECTION_ID)
    assert not env[0].writes


def test_section_composite_update_rejects_different_response_id(env):
    env[0].rows["channelSections"][0]["id"] = SECTION_ID
    change = prepare(env, "section-update", section_id=SECTION_ID, patch={"snippet": {"title": "New"}})
    env[0].on_write = lambda r: httpx.Response(200, json={"id": "different.composite-id", "kind": "youtube#channelSection"})
    assert apply(env, change).status == "uncertain"
    stored = env[2].load(change.id)
    assert stored.result_id == "different.composite-id" and not stored.receipt["identity_valid"]
    assert reconcile_owned(env[1], env[2], change.id).status == "uncertain"
    assert len(env[0].writes) == 1


def test_watermark_replacement_does_not_inherit_undocumented_channel_state(env):
    env[0].rows["channels"][0]["invideoBranding"] = {"targetChannelId": "foreign-channel", "timing": {"type": "offsetFromStart", "offsetMs": "7", "durationMs": "8"}}
    file = env[3] / "watermark.png"
    file.write_bytes(PNG)
    with pytest.raises(ChangeError):
        prepare(env, "watermark-set", channel_id="channel-a", file=str(file), timing={"type": "offsetFromEnd", "offsetMs": "5000", "durationMs": "omit"})
    change = prepare(env, "watermark-set", channel_id="channel-a", target_channel_id="channel-a", file=str(file), timing={"type": "offsetFromEnd", "offsetMs": "5000", "durationMs": "omit"})
    assert change.before["watermark_state"]["known"] is False
    assert change.after == {"timing": {"type": "offsetFromEnd", "offsetMs": "5000"}, "targetChannelId": "channel-a"}
    assert "DESCONOCIDO" in " ".join(change.effects)


@pytest.mark.parametrize("failure", ["account", "etag"])
def test_watermark_unknown_state_retains_channel_preflight(env, failure):
    change = prepare(env, "watermark-unset", channel_id="channel-a")
    if failure == "account":
        env[0].account = "foreign-channel"
    else:
        env[0].rows["channels"][0]["etag"] = "changed-etag"
    with pytest.raises(ChangeError):
        apply(env, change)
    assert not env[0].writes
