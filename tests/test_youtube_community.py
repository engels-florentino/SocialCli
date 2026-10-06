"""Task10 synthetic actor/parent bindings and durable community effects."""
import copy
import json

import httpx
import pytest

from socialctl.management.community_schema import CommunityEdit
from socialctl.management.community_changes import CommunityStore, prepare_community, apply_community, reconcile_community
from socialctl.management.youtube_community import YouTubeCommunityClient
from socialctl.management.youtube_resources import ResourceError
from socialctl.management.changes import ApprovalMismatch
from tests.test_youtube_management import _brand, _snippet


def comment(cid="UgxTop", author="channel-a", parent=None, text="Supplied original", status="published"):
    snippet = {"channelId": "channel-a", "authorChannelId": {"value": author},
        "textDisplay": "Rendered", "moderationStatus": status}
    if author == "channel-a":
        snippet["textOriginal"] = text
    if parent:
        snippet["parentId"] = parent
    return {"kind": "youtube#comment", "id": cid, "etag": "comment-etag", "snippet": snippet}


def thread(tid="UgxThread", top=None):
    return {"kind": "youtube#commentThread", "id": tid, "etag": "thread-etag", "snippet": {
        "channelId": "channel-a", "videoId": "video-1", "canReply": True,
        "totalReplyCount": 1, "topLevelComment": top or comment()}}


class API:
    def __init__(self):
        self.actor = "channel-a"
        self.threads = [thread()]
        self.replies = [comment("UgxTop.9_reply-2", parent="UgxTop")]
        self.subscriptions = []
        self.rating = "none"
        self.requests = []
        self.on_write = self.on_read = None

    @property
    def writes(self):
        return [r for r in self.requests if r.method != "GET"]

    def handler(self, request):
        self.requests.append(request)
        if request.method == "GET" and self.on_read:
            response = self.on_read(request)
            if response is not None:
                return response
        name = request.url.path.rsplit("/", 1)[-1]
        p = request.url.params
        if request.method == "GET":
            if name == "channels":
                rows = [{"id": self.actor}] if "mine" in p else [{"id": p["id"], "etag": "ext-etag", "snippet": {"title": "External"}}]
            elif name == "videos":
                rows = [{"id": p["id"], "etag": "video-etag", "snippet": _snippet(channelId="external" if p["id"] == "external-video" else "channel-a"), "status": {"privacyStatus": "public"}}]
            elif name == "commentThreads":
                rows = copy.deepcopy(self.threads)
                if "id" in p:
                    rows = [r for r in rows if r["id"] == p["id"]]
                elif "moderationStatus" in p:
                    rows = [r for r in rows if r["snippet"]["topLevelComment"]["snippet"]["moderationStatus"] == p["moderationStatus"]]
            elif name == "comments":
                rows = copy.deepcopy(self.replies)
                rows = [r for r in rows if r["snippet"].get("parentId") == p["parentId"]]
            elif name == "subscriptions":
                rows = copy.deepcopy(self.subscriptions)
            elif name == "getRating":
                return httpx.Response(200, json={"items": [{"videoId": p["id"], "rating": self.rating}]})
            elif name == "videoAbuseReportReasons":
                rows = [{"id": "R", "snippet": {"label": "Supplied reason", "secondaryReasons": [{"id": "S", "label": "Detail"}]}}]
            else:
                rows = []
            return httpx.Response(200, json={"items": rows, "pageInfo": {"totalResults": len(rows), "resultsPerPage": 50}})
        if self.on_write:
            response = self.on_write(request)
            if response is not None:
                return response
        body = json.loads(request.content) if request.content else {}
        if name == "commentThreads":
            row = thread("UgxCreatedThread", comment("UgxCreatedTop", text=body["snippet"]["topLevelComment"]["snippet"]["textOriginal"]))
            self.threads.append(row)
            return httpx.Response(200, json=row)
        if name == "comments":
            if request.method == "POST":
                row = comment("UgxTop.9_created", parent=body["snippet"]["parentId"], text=body["snippet"]["textOriginal"])
                self.replies.append(row)
            elif request.method == "PUT":
                row = next(c for c in [*[t["snippet"]["topLevelComment"] for t in self.threads], *self.replies] if c["id"] == body["id"])
                row["snippet"]["textOriginal"] = body["snippet"]["textOriginal"]
                row["etag"] = "changed"
            else:
                self.threads = [t for t in self.threads if t["snippet"]["topLevelComment"]["id"] != p["id"]]
                self.replies = [c for c in self.replies if c["id"] != p["id"]]
                return httpx.Response(204)
            return httpx.Response(200, json=row)
        if name == "setModerationStatus":
            for t in self.threads:
                if t["snippet"]["topLevelComment"]["id"] in p["id"].split(","):
                    t["snippet"]["topLevelComment"]["snippet"]["moderationStatus"] = p["moderationStatus"]
            return httpx.Response(204)
        if name == "subscriptions":
            if request.method == "DELETE":
                self.subscriptions = [r for r in self.subscriptions if r["id"] != p["id"]]
                return httpx.Response(204)
            row = {"kind": "youtube#subscription", "id": "sub.opaque=", "etag": "sub-etag", "snippet": {"channelId": self.actor, "resourceId": body["snippet"]["resourceId"]}}
            self.subscriptions.append(row)
            return httpx.Response(200, json=row)
        if name == "rate":
            self.rating = p["rating"]
        return httpx.Response(204)


def service(tmp_path, name="Example", channel="channel-a"):
    brand = _brand(tmp_path, name, channel)
    api = API()
    http = httpx.Client(transport=httpx.MockTransport(api.handler))
    return brand, api, YouTubeCommunityClient(brand, http), CommunityStore(brand.raiz)


def test_exact_approval_intent_and_distinct_returned_ids_before_readback(tmp_path):
    brand, api, client, store = service(tmp_path)
    change = prepare_community(client, store, {"action": "add", "video_id": "video-1", "text": "Exact supplied new text"})
    assert not api.writes
    with pytest.raises(ApprovalMismatch):
        apply_community(client, store, change.id, "wrong")
    def on_write(request):
        assert store.load(change.id).status == "applying"
    api.on_write = on_write
    def on_read(request):
        if api.writes:
            saved = store.load(change.id)
            assert saved.receipt and saved.result_id == "UgxCreatedTop"
            assert saved.receipt["thread_id"] == "UgxCreatedThread"
    api.on_read = on_read
    result = apply_community(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert len(api.writes) == 1
    assert json.loads(api.writes[0].content)["snippet"] == {"channelId": "channel-a", "videoId": "video-1", "topLevelComment": {"snippet": {"textOriginal": "Exact supplied new text"}}}


def test_reply_binds_distinct_thread_top_and_opaque_reply_ids(tmp_path):
    _, api, client, store = service(tmp_path)
    change = prepare_community(client, store, {"action": "reply", "video_id": "video-1", "thread_id": "UgxThread", "parent_id": "UgxTop", "text": "Reply new"})
    assert apply_community(client, store, change.id, change.fingerprint).status == "verified"
    assert json.loads(api.writes[0].content) == {"snippet": {"parentId": "UgxTop", "textOriginal": "Reply new"}}


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_own_edit_delete_author_gate_and_foreign_channel(tmp_path, action):
    _, api, client, store = service(tmp_path)
    edit = {"action": action, "video_id": "video-1", "thread_id": "UgxThread", "comment_id": "UgxTop"}
    if action == "edit":
        edit["text"] = "Edited supplied"
    api.threads[0]["snippet"]["topLevelComment"] = comment(author="foreign")
    with pytest.raises(ResourceError, match="author"):
        prepare_community(client, store, edit)
    assert not api.writes
    api.threads[0]["snippet"]["topLevelComment"] = comment()
    change = prepare_community(client, store, edit)
    result = apply_community(client, store, change.id, change.fingerprint)
    assert result.status in {"verified", "accepted_unverifiable"}
    assert len(api.writes) == 1


def test_pagination_preserves_partial_and_never_infers_absence(tmp_path):
    _, api, client, _ = service(tmp_path)
    def pages(request):
        if request.url.path.endswith("/comments"):
            if "pageToken" in request.url.params:
                assert request.url.params["pageToken"] == "opaque+cursor="
                return httpx.Response(403, json={"error": {"message": "token-secret"}})
            return httpx.Response(200, json={"items": api.replies, "nextPageToken": "opaque+cursor=",
                "pageInfo": {"totalResults": 2, "resultsPerPage": 50}})
    api.on_read = pages
    report = client.list_replies("video-1", "UgxThread", "UgxTop")
    assert not report["complete"] and not report["absence_proven"]
    assert len(report["items"]) == 1 and "token-secret" not in str(report)
    assert not api.writes


def test_terminal_truncated_total_is_not_complete(tmp_path):
    _, api, client, _ = service(tmp_path)
    api.on_read = lambda r: httpx.Response(200, json={"items": api.replies,
        "pageInfo": {"totalResults": 9, "resultsPerPage": 50}}) if r.url.path.endswith("/comments") else None
    assert client.list_replies("video-1", "UgxThread", "UgxTop")["complete"] is False


def test_moderation_batch_50_exact_ids_owners_statuses_and_receipt(tmp_path):
    _, api, client, store = service(tmp_path)
    api.threads = [thread(f"thread.{i}", comment(f"comment.{i}", author=f"author-{i}", status="heldForReview")) for i in range(50)]
    targets = [{"video_id": "video-1", "thread_id": t["id"], "comment_id": t["snippet"]["topLevelComment"]["id"]} for t in api.threads]
    change = prepare_community(client, store, {"action": "moderate", "targets": targets, "moderation_status": "published", "ban_author": False})
    def on_read(request):
        if api.writes:
            assert store.load(change.id).receipt == {"http_status": 204}
    api.on_read = on_read
    result = apply_community(client, store, change.id, change.fingerprint)
    assert result.status == "verified"
    assert len(api.writes) == 1 and not api.writes[0].content
    assert api.writes[0].url.params["id"].split(",") == [t["comment_id"] for t in targets]
    assert "banAuthor" not in api.writes[0].url.params
    observation = next(e for e in result.journal if e["event"] == "moderation_readback")
    assert len(observation["observations"]) == 50


def test_moderation_partial_and_explicit_ban_never_guesses_hidden_status(tmp_path):
    _, api, client, store = service(tmp_path)
    api.threads = [thread("t1", comment("c1", author="other-1", status="heldForReview")), thread("t2", comment("c2", author="other-2", status="heldForReview"))]
    targets = [{"video_id": "video-1", "thread_id": t["id"], "comment_id": t["snippet"]["topLevelComment"]["id"]} for t in api.threads]
    change = prepare_community(client, store, {"action": "moderate", "targets": targets, "moderation_status": "published", "ban_author": False})
    def partial(request):
        api.threads[0]["snippet"]["topLevelComment"]["snippet"]["moderationStatus"] = "published"
        return httpx.Response(204)
    api.on_write = partial
    result = apply_community(client, store, change.id, change.fingerprint)
    assert result.status == "partial" and not result.verified
    assert reconcile_community(client, store, change.id).status == "partial"
    assert len(api.writes) == 1


@pytest.mark.parametrize("patch", [
    {"moderation_status": "heldForReview"}, {"ban_author": True},
    {"targets": [{"video_id": "video-1", "thread_id": "UgxThread", "comment_id": "UgxTop"}] * 2},
    {"targets": [{"video_id": "video-1", "thread_id": "t", "comment_id": str(i)} for i in range(51)]},
])
def test_unsupported_moderation_transitions_ban_and_duplicate_ids_block(tmp_path, patch):
    _, api, client, store = service(tmp_path)
    raw = {"action": "moderate", "targets": [{"video_id": "video-1", "thread_id": "UgxThread", "comment_id": "UgxTop"}], "moderation_status": "published", "ban_author": False}
    with pytest.raises(ResourceError):
        prepare_community(client, store, {**raw, **patch})
    assert not api.writes


@pytest.mark.parametrize("failure", ["timeout", "500", "redirect", "lost-id", "wrong-author"])
def test_uncertain_insert_no_retry_no_new_uuid_no_candidate_adoption(tmp_path, failure):
    _, api, client, store = service(tmp_path)
    edit = {"action": "add", "video_id": "video-1", "text": "New exact"}
    first = prepare_community(client, store, edit)
    second = prepare_community(client, store, {**edit, "text": "Changed text still same target"})
    def fail(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET", request=request)
        if failure == "500":
            return httpx.Response(500, json={"error": {"message": "SECRET"}})
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.invalid/token"})
        if failure == "wrong-author":
            return httpx.Response(200, json=thread("created-thread", comment("created-comment", author="foreign")))
        return httpx.Response(200, json={})
    api.on_write = fail
    result = apply_community(client, store, first.id, first.fingerprint)
    assert result.status == "uncertain"
    apply_community(client, store, first.id, first.fingerprint)
    reconcile_community(client, store, first.id)
    with pytest.raises(ResourceError, match="UUID"):
        apply_community(client, store, second.id, second.fingerprint)
    assert len(api.writes) == 1
    assert "SECRET" not in store.path_for(first.id).read_text()


def test_two_brands_actor_parent_conflict_and_snapshot_tampering(tmp_path):
    _, api, client, store = service(tmp_path)
    _, api_b, client_b, _ = service(tmp_path, "Other", "channel-b")
    edit = {"action": "edit", "video_id": "video-1", "thread_id": "UgxThread", "comment_id": "UgxTop", "text": "New"}
    change = prepare_community(client, store, edit)
    with pytest.raises(ResourceError, match='brand'):
        apply_community(client_b, store, change.id, change.fingerprint)
    api.actor = "channel-b"
    with pytest.raises(ResourceError, match="authenticated"):
        apply_community(client, store, change.id, change.fingerprint)
    api.actor = "channel-a"
    api.threads[0]["snippet"]["topLevelComment"]["etag"] = "concurrent"
    with pytest.raises(ResourceError, match="conflict"):
        apply_community(client, store, change.id, change.fingerprint)
    assert store.load(change.id).status == "conflict" and not api.writes and not api_b.writes
    with pytest.raises(ResourceError, match="parent_id"):
        client.list_replies("video-1", "UgxThread", "UgxThread")


def test_explicit_external_rating_subscription_and_unsubscribe(tmp_path):
    _, api, client, store = service(tmp_path)
    for raw in ({"action": "rate", "video_id": "external-video", "rating": "like"},
                {"action": "subscribe", "channel_id": "external-channel"},
                {"action": "unsubscribe", "channel_id": "external-channel", "subscription_id": "sub.opaque="}):
        change = prepare_community(client, store, raw)
        assert apply_community(client, store, change.id, change.fingerprint).status == "verified"
    assert api.writes[0].url.params["id"] == "external-video" and not api.writes[0].content
    assert api.writes[2].url.params["id"] == "sub.opaque="
    with pytest.raises(ResourceError, match="own"):
        prepare_community(client, store, {"action": "subscribe", "channel_id": "channel-a"})


def test_abuse_report_is_separate_exact_reason_target_and_no_audit_side_effect(tmp_path):
    _, api, client, store = service(tmp_path)
    client.catalog("abuse-reasons")
    client.search("supplied query")
    client.activities()
    client.catalog("languages")
    client.catalog("regions")
    client.catalog("categories", region="ES")
    assert not api.writes
    change = prepare_community(client, store, {"action": "report-abuse", "video_id": "external-video", "reason_id": "R", "secondary_reason_id": "S", "comments": "Explicit supplied reason", "language": "es"})
    assert not api.writes
    result = apply_community(client, store, change.id, change.fingerprint)
    assert result.status == "accepted_unverifiable" and not result.verified
    reconcile_community(client, store, change.id)
    assert len(api.writes) == 1
    assert json.loads(api.writes[0].content) == {"videoId": "external-video", "reasonId": "R", "secondaryReasonId": "S", "comments": "Explicit supplied reason", "language": "es"}
    with pytest.raises(ResourceError, match="secondary"):
        prepare_community(client, store, {"action": "report-abuse", "video_id": "external-video", "reason_id": "R", "secondary_reason_id": "wrong"})


@pytest.mark.parametrize("action", ["markAsSpam", "abuseReports.insert", "pin", "heart", "poll", "cards", "end-screens", "related-shorts"])
def test_disabled_methods_never_dispatch(tmp_path, action):
    _, api, client, store = service(tmp_path)
    with pytest.raises(ResourceError):
        prepare_community(client, store, {"action": action})
    with pytest.raises(ResourceError):
        client._request("POST", "https://www.googleapis.com/youtube/v3/" + action)
    assert not api.requests


def test_exact_thread_response_cannot_hide_ambiguous_total(tmp_path):
    _, api, client, _ = service(tmp_path)
    api.on_read = lambda r: httpx.Response(200, json={"items": api.threads,
        "pageInfo": {"totalResults": 2, "resultsPerPage": 50}}) if r.url.path.endswith("/commentThreads") else None
    with pytest.raises(ResourceError, match="total"):
        client.thread("video-1", "UgxThread")


def test_delete_records_readback_without_claiming_filtered_absence(tmp_path):
    _, api, client, store = service(tmp_path)
    change = prepare_community(client, store, {"action": "delete", "video_id": "video-1", "thread_id": "UgxThread", "comment_id": "UgxTop"})
    result = apply_community(client, store, change.id, change.fingerprint)
    assert result.status == "accepted_unverifiable"
    assert any(e["event"] == "delete_readback" for e in result.journal)


def test_moderation_reuses_each_read_scope_within_single_preflight(tmp_path):
    _, api, client, store = service(tmp_path)
    api.threads = [thread(f"t{i}", comment(f"c{i}", author="other", status="heldForReview")) for i in range(5)]
    prepare_community(client, store, {"action": "moderate", "targets": [{"video_id": "video-1", "thread_id": f"t{i}", "comment_id": f"c{i}"} for i in range(5)], "moderation_status": "published", "ban_author": False})
    assert len([r for r in api.requests if r.url.path.endswith("/commentThreads")]) == 3


@pytest.mark.parametrize("capacity", [0, 5])
def test_page_capacity_must_cover_rows_before_authorizing_comment_write(tmp_path, capacity):
    _, api, client, store = service(tmp_path)
    change = prepare_community(client, store, {"action": "reply", "video_id": "video-1", "thread_id": "UgxThread", "parent_id": "UgxTop", "text": "New capacity-checked reply"})
    def replies(request):
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json={"items": copy.deepcopy(api.replies),
                "pageInfo": {"totalResults": len(api.replies), "resultsPerPage": capacity}})
    api.on_read = replies
    if capacity == 0:
        with pytest.raises(ResourceError, match='incomplete list'):
            apply_community(client, store, change.id, change.fingerprint)
        assert not api.writes and store.load(change.id).status == "proposed"
    else:
        assert apply_community(client, store, change.id, change.fingerprint).status == "verified"
        assert len(api.writes) == 1


def test_capacity_smaller_than_multiple_returned_rows_is_partial(tmp_path):
    _, api, client, _ = service(tmp_path)
    api.replies.append(comment("UgxTop.second", parent="UgxTop"))
    api.on_read = lambda r: httpx.Response(200, json={"items": api.replies,
        "pageInfo": {"totalResults": 2, "resultsPerPage": 1}}) if r.url.path.endswith("/comments") else None
    result = client.list_replies("video-1", "UgxThread", "UgxTop")
    assert not result["complete"] and not result["absence_proven"]
    assert result["items"] == [] and result["error"]


def test_contradictory_capacity_cannot_verify_subscription_membership(tmp_path):
    _, api, client, store = service(tmp_path)
    change = prepare_community(client, store, {"action": "subscribe", "channel_id": "external-channel"})
    capacity = 0
    def subscriptions(request):
        if api.writes and request.url.path.endswith("/subscriptions"):
            return httpx.Response(200, json={"items": copy.deepcopy(api.subscriptions),
                "pageInfo": {"totalResults": 1, "resultsPerPage": capacity}})
    api.on_read = subscriptions
    result = apply_community(client, store, change.id, change.fingerprint)
    assert result.status == "uncertain" and not result.verified
    assert result.result_id == "sub.opaque=" and result.receipt["identity_valid"]
    assert any(e["event"] == "readback_unavailable" for e in result.journal)
    capacity = 5
    result = reconcile_community(client, store, change.id)
    assert result.status == "verified" and len(api.writes) == 1


@pytest.mark.parametrize("kind", ["video", "channel", "playlist"])
def test_search_nonempty_explicit_parameters_and_distinct_resource_ids(tmp_path, kind):
    _, api, client, _ = service(tmp_path)
    row = {"kind": "youtube#searchResult", "etag": "search-etag",
        "id": {"kind": "youtube#" + kind, kind + "Id": "result-" + kind},
        "snippet": {"channelId": "external-channel", "title": "Resultado suministrado", "description": "Keep exact description"}}
    expected = {"part": "snippet", "q": "historia & música", "type": kind, "order": "date", "channelId": "external-channel", "maxResults": "50"}
    def search(request):
        if request.url.path.endswith("/search"):
            assert dict(request.url.params) == expected
            return httpx.Response(200, json={"kind": "youtube#searchListResponse", "etag": "list-etag", "items": [row],
                "pageInfo": {"totalResults": 900, "resultsPerPage": 5}})
    api.on_read = search
    result = client.search("historia & música", kind=kind, order="date", channel_id="external-channel")
    assert result["complete"] and result["pages"] == 1
    assert result["items"] == [row] and result["items"][0]["id"][kind + "Id"] == "result-" + kind
    assert result["scope"] == {"resource": "search", **{k: v for k, v in expected.items() if k != "maxResults"}}
    assert result["source"] == "https://www.googleapis.com/youtube/v3/search" and result["observed_at"]
    assert not api.writes


def test_activities_nonempty_owned_scope_and_exact_date_parameters(tmp_path):
    _, api, client, _ = service(tmp_path)
    row = {"kind": "youtube#activity", "etag": "activity-etag", "id": "activity.opaque=",
        "snippet": {"channelId": "channel-a", "type": "upload", "title": "Owned upload", "publishedAt": "2026-08-02T12:00:00Z"},
        "contentDetails": {"upload": {"videoId": "owned-upload-id"}}}
    expected = {"part": "snippet,contentDetails", "channelId": "channel-a", "publishedAfter": "2026-08-01T00:00:00Z", "publishedBefore": "2026-09-01T00:00:00Z", "maxResults": "50"}
    def activities(request):
        if request.url.path.endswith("/activities"):
            assert dict(request.url.params) == expected
            return httpx.Response(200, json={"kind": "youtube#activityListResponse", "etag": "list-etag", "items": [row],
                "pageInfo": {"totalResults": 1, "resultsPerPage": 5}})
    api.on_read = activities
    result = client.activities(published_after=expected["publishedAfter"], published_before=expected["publishedBefore"])
    assert result["complete"] and result["items"] == [row]
    assert result["items"][0]["contentDetails"]["upload"]["videoId"] == "owned-upload-id"
    assert result["scope"] == {"resource": "activities", **{k: v for k, v in expected.items() if k != "maxResults"}}
    assert not api.writes


@pytest.mark.parametrize("name,resource,row,region", [
    ("languages", "i18nLanguages", {"kind": "youtube#i18nLanguage", "etag": "language-etag", "id": "es", "snippet": {"hl": "es", "name": "español"}}, None),
    ("regions", "i18nRegions", {"kind": "youtube#i18nRegion", "etag": "region-etag", "id": "ES", "snippet": {"gl": "ES", "name": "España"}}, None),
    ("categories", "videoCategories", {"kind": "youtube#videoCategory", "etag": "category-etag", "id": "27", "snippet": {"title": "Educación", "channelId": "category-owner", "assignable": True}}, "ES"),
])
def test_catalog_nonempty_ids_snippets_and_exact_unpaginated_parameters(tmp_path, name, resource, row, region):
    _, api, client, _ = service(tmp_path)
    expected = {"part": "snippet", "hl": "es", **({"regionCode": region} if region else {})}
    def catalog(request):
        if request.url.path.endswith("/" + resource):
            assert dict(request.url.params) == expected
            return httpx.Response(200, json={"kind": row["kind"] + "ListResponse", "etag": "list-etag", "items": [row]})
    api.on_read = catalog
    result = client.catalog(name, language="es", region=region)
    assert result["complete"] and result["pages"] == 1 and result["items"] == [row]
    assert result["scope"] == {"resource": resource, **expected}
    assert result["source"] == "https://www.googleapis.com/youtube/v3/" + resource and result["observed_at"]
    assert not api.writes
