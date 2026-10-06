import re

import yaml

import socialctl.cli as cli
from socialctl.scheduler import ScheduleStore
from tests.test_scheduler_cli import _make_social, runner, app


def invocation(root, *options):
    return runner.invoke(app, ["schedule", "clip", "--at", "2030-01-01T00:00:00Z", "--brand", "Histopast", "--root", str(root), *options])


def test_preview_digest_approves_exact_server_post(tmp_path):
    brand, _ = _make_social(tmp_path)
    preview = invocation(tmp_path, "--dry-run")
    assert preview.exit_code == 0, preview.output
    match = re.search(r"Approval digest: ([a-f0-9]{64})", preview.output)
    assert match, preview.output
    assert not ScheduleStore(brand.raiz).load()
    approved = invocation(tmp_path, "--yes", "--approval-digest", match[1])
    assert approved.exit_code == 0, approved.output
    assert ScheduleStore(brand.raiz).get("clip/facebook").status == "approved"


def test_server_rejects_post_changed_since_remote_preview(tmp_path):
    brand, path = _make_social(tmp_path)
    preview = invocation(tmp_path, "--dry-run")
    match = re.search(r"Approval digest: ([a-f0-9]{64})", preview.output)
    assert match, preview.output
    data = yaml.safe_load(path.read_text())
    data["platforms"]["facebook"]["body"] = "Changed after remote preview"
    path.write_text(yaml.safe_dump(data))
    result = invocation(tmp_path, "--yes", "--approval-digest", match[1])
    assert result.exit_code == 1
    assert not ScheduleStore(brand.raiz).load()


def test_schedule_rechecks_after_render_and_confirmation(tmp_path, monkeypatch):
    brand, path = _make_social(tmp_path)
    original = cli.render_preview
    def changed(post, errors, **kwargs):
        preview = original(post, errors, **kwargs)
        data = yaml.safe_load(path.read_text())
        data["platforms"]["facebook"]["body"] = "Changed on disk"
        path.write_text(yaml.safe_dump(data))
        return preview
    monkeypatch.setattr(cli, "render_preview", changed)
    result = invocation(tmp_path, "--yes")
    assert result.exit_code == 1, result.output
    assert not ScheduleStore(brand.raiz).load()


def test_digest_binds_scheduled_instant_and_platform_selection(tmp_path):
    _, _ = _make_social(tmp_path)
    preview = invocation(tmp_path, "--dry-run")
    match = re.search(r"Approval digest: ([a-f0-9]{64})", preview.output)
    assert match, preview.output
    result = runner.invoke(app, ["schedule", "clip", "--at", "2030-01-02T00:00:00Z", "--brand", "Histopast", "--root", str(tmp_path), "--approval-digest", match[1], "--yes"])
    assert result.exit_code == 1


def test_queue_keeps_preview_hash_if_payload_changes_after_final_check(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from socialctl.scheduler import approval_hash
    from socialctl.models import Platform
    from socialctl.brands import cargar_brand
    from socialctl.postfile import cargar_post
    brand, _ = _make_social(tmp_path)
    selected = cargar_brand(tmp_path, "Histopast")
    expected = approval_hash(cargar_post(selected, "clip"), selected, Platform.FACEBOOK)
    original = cli.render_preview
    displayed = []
    def render(post, errors, **kwargs):
        displayed.append(post)
        return original(post, errors, **kwargs)
    def late_change():
        displayed[0].platforms[Platform.FACEBOOK].body = "late mutation"
        return datetime.now(timezone.utc)
    monkeypatch.setattr(cli, "render_preview", render)
    monkeypatch.setattr(cli, "now_utc", late_change)
    result = invocation(tmp_path, "--yes")
    assert result.exit_code == 0, result.output
    assert ScheduleStore(brand.raiz).get("clip/facebook").content_hash == expected


def test_server_failed_validation_has_structured_preview_without_writes(tmp_path):
    import json
    brand, _ = _make_social(tmp_path)
    (brand.raiz / "accounts.yml").write_text("facebook: {}")
    result = invocation(tmp_path, "--dry-run", "--preview-json")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["protocol"] == "socialctl.schedule-preview.v1"
    assert payload["valid"] is False
    assert "Cuerpo aprobado" in payload["preview"]
    assert "falta page_id" in payload["preview"]
    assert not ScheduleStore(brand.raiz).load()


def test_structured_preview_cannot_mutate_queue_without_dryrun(tmp_path):
    brand, _ = _make_social(tmp_path)
    result = invocation(tmp_path, "--yes", "--preview-json")
    assert result.exit_code == 1, result.output
    assert not ScheduleStore(brand.raiz).load()


def test_structured_validation_preview_contains_post_loader_warnings(tmp_path):
    import json
    brand, path = _make_social(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["slug"] = "different-slug"
    path.write_text(yaml.safe_dump(data))
    (brand.raiz / "accounts.yml").write_text("facebook: {}")
    result = invocation(tmp_path, "--dry-run", "--preview-json")
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "AVISO:" in payload["preview"]
    assert "Cuerpo aprobado" in payload["preview"]
    assert "falta page_id" in payload["preview"]


def test_new_meta_video_missing_origin_fails_server_preview(tmp_path, monkeypatch):
    import json
    from tests.test_scheduler_cli import _make_video_social
    brand, _ = _make_video_social(tmp_path, monkeypatch, origin=None)
    result = invocation(tmp_path, '--dry-run', '--preview-json')
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload['valid'] is False
    assert 'declara standalone o youtube_long' in payload['preview']
    assert ScheduleStore(brand.raiz).load() == []


def test_changed_source_invalidates_preview_approval_even_with_same_comment(tmp_path, monkeypatch):
    from tests.test_scheduler_cli import _make_video_social
    from socialctl.scheduler import approval_hash
    from socialctl.postfile import cargar_post
    from socialctl.models import Platform
    brand, path = _make_video_social(tmp_path, monkeypatch, origin='youtube_long', source='R4cUGeaKrfU',
        comment='🎥 Video completo en https://www.youtube.com/watch?v=R4cUGeaKrfU')
    preview = invocation(tmp_path, '--dry-run')
    assert preview.exit_code == 0, preview.output
    match = re.search(r'Approval digest: ([a-f0-9]{64})', preview.output)
    old = approval_hash(cargar_post(brand, 'clip'), brand, Platform.FACEBOOK)
    data = yaml.safe_load(path.read_text())
    data['platforms']['facebook']['source_video_id'] = 'Vhb3l5-KmEg'
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    assert approval_hash(cargar_post(brand, 'clip'), brand, Platform.FACEBOOK) != old
    result = invocation(tmp_path, '--yes', '--approval-digest', match[1])
    assert result.exit_code == 1, result.output
    assert ScheduleStore(brand.raiz).load() == []
