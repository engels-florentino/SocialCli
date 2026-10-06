from tests.terminal import plain
import httpx
import json
from typer.testing import CliRunner

from socialctl.cli import app
from tests.test_meta_management import service
from tests.test_meta_webhooks import body, signature

runner = CliRunner()


def test_meta_cli_registered_and_requires_brand(tmp_path):
    help_result = runner.invoke(app, ["meta", "--help"])
    assert help_result.exit_code == 0, help_result.output
    edit_file = tmp_path / "edit.yml"
    edit_file.write_text(
        "version: 1\naction: post-delete\ntarget_id: '123_789'\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "meta",
            "prepare",
            "--platform",
            "facebook",
            "--file",
            str(edit_file),
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "--brand" in plain(result.output)


def test_meta_cli_complete_preview_and_exact_approval(tmp_path, monkeypatch):
    import socialctl.management.meta_cli as cli

    mod, brand, graph, _, store = service(tmp_path)
    (brand.raiz / "accounts.yml").write_text("facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n")
    edit_file = tmp_path / "edit.yml"
    edit_file.write_text(
        "version: 1\naction: post-update\ntarget_id: '123_789'\npatch:\n  message: After\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(graph)))
    common = ["--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)]

    apply_common = ["--brand", "Example", "--root", str(tmp_path)]

    result = runner.invoke(app, ["meta", "prepare", "--file", str(edit_file), *common])
    assert result.exit_code == 0, result.output
    change = store.load(next(store.root.glob("*.json")).stem)
    assert change.id in result.output
    assert change.fingerprint in result.output

    cancelled = runner.invoke(app, ["meta", "apply", change.id, *apply_common], input="wrong\n")
    assert cancelled.exit_code != 0
    assert not graph.writes

    applied = runner.invoke(app, ["meta", "apply", change.id, *apply_common], input=change.fingerprint + "\n")
    assert applied.exit_code == 0, applied.output
    assert store.load(change.id).status in {"verified", "accepted_unverifiable", "uncertain"}
    assert graph.writes

    status = runner.invoke(app, ["meta", "status", change.id, "--brand", "Example", "--root", str(tmp_path)])
    assert status.exit_code == 0
    assert change.id in status.output


def test_meta_webhook_cli(tmp_path, monkeypatch):
    import socialctl.management.meta_cli as cli
    mod, brand, graph, client, _ = service(tmp_path)
    (brand.raiz / "accounts.yml").write_text("facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n")
    monkeypatch.setattr(cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(graph)))
    raw = body()
    raw_file = tmp_path / "payload.json"
    raw_file.write_bytes(raw)
    secret = "APP_SECRET"

    verify = runner.invoke(app, ["meta", "webhook-verify", "--mode", "subscribe", "--challenge", "12345", "--expected-token", secret, "--provided-token", secret])
    assert verify.exit_code == 0
    assert "12345" in verify.output

    prep = runner.invoke(app, ["meta", "webhook-ingest", "--body", str(raw_file), "--platform", "facebook", "--signature", signature(raw), "--app-secret", secret, "--brand", "Example", "--root", str(tmp_path)])
    assert prep.exit_code == 0
    assert "accepted" in prep.output

    ev = runner.invoke(app, ["meta", "webhook-events", "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)])
    assert ev.exit_code == 0
    events = json.loads(ev.output)
    assert len(events) == 1
    event_id = events[0]["id"]

    out = runner.invoke(app, ["meta", "webhook-reconcile", event_id, "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)])
    assert out.exit_code == 0
    payload = json.loads(out.output)
    assert payload["state"] == "read_only_observed"


def test_meta_cli_identity_profile_content_readonly(tmp_path, monkeypatch):
    import socialctl.management.meta_cli as cli

    _, _, graph, client, _ = service(tmp_path)
    client.brand.raiz.joinpath("accounts.yml").write_text("facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n")
    monkeypatch.setattr(cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(graph)))

    identity = runner.invoke(
        app,
        ["meta", "identity", "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)],
    )
    assert identity.exit_code == 0, identity.output
    identity_payload = json.loads(identity.output)
    assert identity_payload["actor_id"] == "123"
    assert identity_payload["account_id"] == "123"

    profile = runner.invoke(
        app,
        ["meta", "profile", "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)],
    )
    assert profile.exit_code == 0, profile.output
    profile_payload = json.loads(profile.output)
    assert profile_payload["id"] == "123"
    assert "name" in profile_payload

    content = runner.invoke(
        app,
        ["meta", "content", "123_789", "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)],
    )
    assert content.exit_code == 0, content.output
    content_payload = json.loads(content.output)
    assert content_payload["id"] == "123_789"

    bad = runner.invoke(
        app,
        ["meta", "content", "789", "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)],
    )
    assert bad.exit_code != 0

    reactions = runner.invoke(
        app,
        ["meta", "reactions", "123_789", "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)],
    )
    assert reactions.exit_code == 0, reactions.output
    reaction_payload = json.loads(reactions.output)
    assert reaction_payload["complete"] is True
    assert reaction_payload["data"][0]["type"] == "LIKE"

    listing = runner.invoke(
        app,
        ["meta", "content-list", "--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)],
    )
    assert listing.exit_code == 0, listing.output
    listing_payload = json.loads(listing.output)
    assert listing_payload["complete"] is True
    assert listing_payload["data"][0]["id"] == "123_789"


def test_meta_cli_instagram_publishing_limit(tmp_path, monkeypatch):
    import socialctl.management.meta_cli as cli

    _, _, graph, client, _ = service(tmp_path, "instagram")
    client.brand.raiz.joinpath("accounts.yml").write_text("facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n")
    monkeypatch.setattr(cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(graph)))

    result = runner.invoke(
        app,
        [
            "meta",
            "publishing-limit",
            "--platform",
            "instagram",
            "--brand",
            "Example",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["quota_usage"]["quota_total"] == 24
    assert payload["config"]["time_unit"] == "DAY"


def test_meta_cli_content_insights_accepts_explicit_date_window(tmp_path, monkeypatch):
    import socialctl.management.meta_cli as cli

    _, _, graph, _, _ = service(tmp_path)
    (tmp_path / "Example" / "accounts.yml").write_text(
        "facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(graph)))
    result = runner.invoke(app, [
        "meta", "insights", "123_789", "--platform", "facebook", "--metric", "post_video_views",
        "--period", "day", "--since", "2026-09-12", "--until", "2026-09-13",
        "--brand", "Example", "--root", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"][0]["name"] == "post_video_views"
