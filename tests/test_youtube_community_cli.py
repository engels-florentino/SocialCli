import json

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.management.community_changes import prepare_community
from tests.test_youtube_community import service

runner = CliRunner()


def test_community_cli_requires_brand_full_preview_exact_approval_no_yes(tmp_path, monkeypatch):
    brand, api, client, store = service(tmp_path)
    monkeypatch.setattr("socialctl.management.community_cli.make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))
    file = tmp_path / "edit.yml"
    file.write_text("version: 1\naction: add\nvideo_id: video-1\ntext: Exact supplied CLI text\n")
    args = ["content", "youtube-community"]
    root = ["--brand", brand.nombre, "--root", str(tmp_path)]
    missing = runner.invoke(app, [*args, "prepare", "--file", str(file), "--dry-run", "--root", str(tmp_path)])
    assert missing.exit_code != 0 and not api.requests
    preview = runner.invoke(app, [*args, "prepare", "--file", str(file), "--dry-run", *root])
    assert preview.exit_code == 0, preview.output
    assert "PREVIEW COMPLETO" in preview.output and "Exact supplied CLI text" in preview.output
    change = store.load(next(store.root.glob("*.json")).stem)
    assert json.dumps(change.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) in preview.output
    rejected = runner.invoke(app, [*args, "apply", change.id, *root], input="yes\n")
    assert rejected.exit_code != 0 and not api.writes
    no_yes = runner.invoke(app, [*args, "apply", change.id, "--yes", *root])
    assert no_yes.exit_code != 0 and not api.writes
    result = runner.invoke(app, [*args, "apply", change.id, *root], input=change.fingerprint + "\n")
    assert result.exit_code == 0, result.output
    assert result.output.index("Exact supplied CLI text") < result.output.index("Escribe la huella")
    assert len(api.writes) == 1
    for cmd in ("status", "reconcile"):
        assert runner.invoke(app, [*args, cmd, change.id, *root]).exit_code == 0
    assert len(api.writes) == 1


def test_read_commands_do_not_write(tmp_path, monkeypatch):
    brand, api, _, _ = service(tmp_path)
    monkeypatch.setattr("socialctl.management.community_cli.make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(api.handler)))
    for cmd in (["threads-list", "video-1"], ["replies-list", "video-1", "UgxThread", "UgxTop"],
                ["comment-show", "video-1", "UgxThread", "UgxTop"], ["subscriptions-list"],
                ["rating-show", "external-video"], ["search", "Exact supplied query"], ["activities-list"],
                ["catalog", "abuse-reasons"], ["catalog", "languages"], ["catalog", "regions"], ["catalog", "categories", "--region", "ES"]):
        result = runner.invoke(app, ["content", "youtube-community", *cmd, "--brand", brand.nombre, "--root", str(tmp_path)])
        assert result.exit_code == 0, result.output
    assert not api.writes


@pytest.mark.parametrize("failure", ["lost-id", "forbidden", "callback", "success"])
def test_independent_youtube_first_comment_durable_media_and_no_reupload(tmp_path, monkeypatch, failure):
    from socialctl.adapters.base import ADAPTADORES, Adapter
    from socialctl.models import CampaignType, Platform, PlatformPost, Post, PostResult, PostStatus
    from socialctl.publisher import publicar
    from socialctl.publication_steps import PublicationStore, retry_first_comment, retry_media_blockers
    brand, api, client, store = service(tmp_path)
    post = Post(slug="clip", brand=brand.nombre, campaign=CampaignType.POST_IMAGEN,
        platforms={Platform.YOUTUBE: PlatformPost(platform=Platform.YOUTUBE, body="Body", first_comment="Approved first", media=[])})
    uploads = []
    class Upload(Adapter):
        platform = Platform.YOUTUBE
        def validate(self, pp, brand):
            # This test isolates comment durability; upload is a synthetic success.
            return []
        def publish(self, pp, b, http):
            uploads.append(pp)
            return PostResult(platform=Platform.YOUTUBE, status=PostStatus.PUBLICADO, platform_id="video-1")
    monkeypatch.setitem(ADAPTADORES, Platform.YOUTUBE, Upload)
    factory = httpx.Client
    monkeypatch.setattr("socialctl.publisher.httpx.Client", lambda **kwargs: factory(transport=httpx.MockTransport(api.handler)))
    def on_write(request):
        data = PublicationStore(brand.raiz).load(occurrence)
        assert data["media"]["platform_id"] == "video-1"
        assert data["comment_change_id"] and store.load(data["comment_change_id"]).status == "applying"
        if failure == "lost-id":
            return httpx.Response(200, json={})
        if failure == "forbidden":
            return httpx.Response(403)
    api.on_write = on_write
    def callback(result):
        if failure == "callback":
            raise OSError("synthetic disk")
    occurrence = "00000000-0000-0000-0000-000000000017"
    first = publicar(post, brand, occurrence_ids={Platform.YOUTUBE: occurrence}, on_media_result=callback)[0]
    assert first.status is PostStatus.PUBLICADO and first.publication_id == occurrence
    assert first.first_comment_status == {"lost-id": "uncertain", "forbidden": "failed", "callback": "pending", "success": "verified"}[failure]
    assert PublicationStore(brand.raiz).load(occurrence)["account"]["account_id"] == "channel-a"
    assert Platform.YOUTUBE in retry_media_blockers(brand, "clip", [Platform.YOUTUBE])
    api.on_write = None
    result = retry_first_comment(brand, client.client, occurrence)
    assert result.first_comment_status == ("uncertain" if failure == "lost-id" else "verified")
    assert len(uploads) == 1
    assert len(api.writes) == (2 if failure == "forbidden" else 1)
    second = publicar(post, brand, occurrence_ids={Platform.YOUTUBE: occurrence})[0]
    assert second.platform_id == "video-1" and len(uploads) == 1
