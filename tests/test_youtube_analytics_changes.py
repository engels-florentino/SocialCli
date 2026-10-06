import copy
import json

import httpx
import pytest

from socialctl.management.changes import ApprovalMismatch
from socialctl.management.youtube_resources import ResourceError
from socialctl.models import Platform
from tests.test_youtube_analytics import service


class MutationAPI:
    def __init__(self):
        self.groups = [{"id": "group.opaque:1", "snippet": {"title": "Old"}, "contentDetails": {"itemType": "youtube#video"}}]
        self.items = []
        self.jobs = []
        self.hook = None
        self.writes = []

    def handler(self, request):
        path, p = request.url.path, request.url.params
        if request.method == "GET":
            if path.endswith("/groups"):
                return httpx.Response(200, json={"items": self.groups})
            if path.endswith("/groupItems"):
                return httpx.Response(200, json={"items": self.items})
            if path.endswith("/reportTypes"):
                return httpx.Response(200, json={"reportTypes": [{"id": "type1", "name": "Basic", "systemManaged": False}]})
            if path.endswith("/jobs"):
                return httpx.Response(200, json={"jobs": self.jobs})
            return httpx.Response(200, json=next(j for j in self.jobs if path.endswith("/" + j["id"])))
        self.writes.append(request)
        if self.hook:
            answer = self.hook(request)
            if answer is not None:
                return answer
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/groups"):
            if request.method == "PUT":
                assert set(body) == {"id", "snippet"} and set(body["snippet"]) == {"title"}
                self.groups[0]["snippet"] = body["snippet"]
                return httpx.Response(200, json=self.groups[0])
            if request.method == "POST":
                row = {"id": "new.group", **body}
                self.groups.append(row)
                return httpx.Response(200, json=row)
            self.groups = [g for g in self.groups if g["id"] != p["id"]]
            return httpx.Response(204)
        if path.endswith("/groupItems"):
            if request.method == "POST":
                row = {"id": "item.1", **body}
                self.items.append(row)
                return httpx.Response(200, json=row)
            self.items = [i for i in self.items if i["id"] != p["id"]]
            return httpx.Response(204)
        if request.method == "POST":
            row = {"id": "job1", **body}
            self.jobs.append(row)
            return httpx.Response(200, json=row)
        self.jobs = []
        return httpx.Response(204)


def setup_service(tmp_path):
    from socialctl.metricas.reporting_client import ReportingClient
    from socialctl.metricas.analytics_changes import AnalyticsStore
    api = MutationAPI()
    brand, client, requests = service(tmp_path, api.handler)
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "synthetic", "granted_scopes": ["https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/yt-analytics.readonly"]})
    return brand, ReportingClient(brand, client.client), AnalyticsStore(brand.raiz), api, requests


def test_group_title_only_exact_approval_intent_receipt(tmp_path):
    from socialctl.metricas.analytics_changes import prepare, apply
    _, client, store, api, _ = setup_service(tmp_path)
    edit = {"version": 1, "action": "group-update", "group_id": "group.opaque:1", "title": "Approved"}
    change = prepare(client, store, edit)
    with pytest.raises(ApprovalMismatch):
        apply(client, store, change.id, "yes")
    assert not api.writes
    def intent(request):
        assert store.load(change.id).status == "applying"
    api.hook = intent
    result = apply(client, store, change.id, change.fingerprint)
    assert result.verified and len(api.writes) == 1
    assert result.receipt and result.journal[-2]["event"] == "write_receipt"
    with pytest.raises(ValueError):
        prepare(client, store, {**edit, "item_type": "youtube#channel"})


def test_uncertain_insert_not_retried_under_new_uuid_and_scope_exact(tmp_path):
    from socialctl.metricas.analytics_changes import prepare, apply
    brand, client, store, api, _ = setup_service(tmp_path)
    edit = {"version": 1, "action": "group-create", "title": "New", "item_type": "youtube#video"}
    first = prepare(client, store, edit)
    api.hook = lambda r: httpx.Response(200, json={})
    assert apply(client, store, first.id, first.fingerprint).status == "uncertain"
    second = prepare(client, store, edit)
    with pytest.raises(ResourceError, match="uncertain"):
        apply(client, store, second.id, second.fingerprint)
    apply(client, store, first.id, first.fingerprint)
    assert len(api.writes) == 1
    brand.guardar_secreto(Platform.YOUTUBE, {"access_token": "synthetic", "granted_scopes": ["https://www.googleapis.com/auth/youtube.force-ssl"]})
    client._access_token = None
    with pytest.raises(ResourceError, match="scope"):
        prepare(client, store, edit)


def test_group_item_duplicate_204_no_invented_id_and_delete(tmp_path):
    from socialctl.metricas.analytics_changes import prepare, apply
    _, client, store, api, _ = setup_service(tmp_path)
    api.items = [{"id": "existing.item", "groupId": "group.opaque:1", "resource": {"id": "v1"}}]
    edit = {"version": 1, "action": "group-item-create", "group_id": "group.opaque:1", "resource_id": "v1"}
    change = prepare(client, store, edit)
    api.hook = lambda r: httpx.Response(204)
    result = apply(client, store, change.id, change.fingerprint)
    assert result.verified and result.result_id is None
    assert result.receipt["duplicate_membership"] is True
    api.hook = None
    delete = prepare(client, store, {"version": 1, "action": "group-item-delete", "group_id": "group.opaque:1", "item_id": "existing.item"})
    assert apply(client, store, delete.id, delete.fingerprint).verified


def test_jobs_and_group_create_delete_lifecycle(tmp_path):
    from socialctl.metricas.analytics_changes import prepare, apply
    _, client, store, api, _ = setup_service(tmp_path)
    for edit in ({"version": 1, "action": "job-create", "report_type_id": "type1", "name": "Explicit"},
        {"version": 1, "action": "job-delete", "job_id": "job1", "report_type_id": "type1"},
        {"version": 1, "action": "group-create", "title": "New", "item_type": "youtube#video"},
        {"version": 1, "action": "group-delete", "group_id": "new.group"}):
        change = prepare(client, store, edit)
        assert apply(client, store, change.id, change.fingerprint).verified
    assert len(api.writes) == 4


def test_reporting_delete_documented_empty_200_and_receipt_before_readback(tmp_path, monkeypatch):
    from socialctl.metricas.analytics_changes import prepare, apply
    _, client, store, api, _ = setup_service(tmp_path)
    api.jobs = [{"id": "job1", "name": "Explicit", "reportTypeId": "type1"}]
    change = prepare(client, store, {"version": 1, "action": "job-delete", "job_id": "job1", "report_type_id": "type1"})
    def deleted(request):
        api.jobs = []
        return httpx.Response(200, content=b"")
    api.hook = deleted
    original = client.jobs
    def check_receipt(*args):
        if api.writes:
            assert store.load(change.id).receipt["http_status"] == 200
        return original(*args)
    monkeypatch.setattr(client, "jobs", check_receipt)
    result = apply(client, store, change.id, change.fingerprint)
    assert result.verified


@pytest.mark.parametrize("status", [403, 429, 500, 302])
def test_no_remote_retries_and_immutable_preview_tampering(tmp_path, status):
    from socialctl.metricas.analytics_changes import prepare, apply
    _, client, store, api, _ = setup_service(tmp_path)
    change = prepare(client, store, {"version": 1, "action": "group-update", "group_id": "group.opaque:1", "title": "Exact"})
    api.hook = lambda r: httpx.Response(status)
    result = apply(client, store, change.id, change.fingerprint)
    assert result.status == ("failed" if status in {403, 429} else "uncertain")
    apply(client, store, change.id, change.fingerprint)
    assert len(api.writes) == 1
    path = store.path_for(change.id)
    raw = json.loads(path.read_text())
    raw["effects"].append("Unapproved effect")
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='fingerprint'):
        store.load(change.id)


@pytest.mark.parametrize("edit", [
    {"version": 1, "action": "job-create", "report_type_id": "type1", "name": "Explicit"},
    {"version": 1, "action": "job-delete", "job_id": "job1", "report_type_id": "type1"},
    {"version": 1, "action": "group-delete", "group_id": "group.opaque:1"},
])
def test_every_destructive_or_job_preview_discloses_conditional_guard_limit(tmp_path, edit):
    from socialctl.metricas.analytics_changes import prepare
    from socialctl.metricas.analytics_cli import preview
    _, client, store, api, _ = setup_service(tmp_path)
    if edit["action"] == "job-delete":
        api.jobs = [{"id": "job1", "name": "Explicit", "reportTypeId": "type1"}]
    change = prepare(client, store, edit)
    assert "no documented conditional version guard" in preview(change)
    assert any("generation" in effect or "Group deletion" in effect for effect in change.effects)
