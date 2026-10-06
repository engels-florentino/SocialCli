import base64
from datetime import datetime, timezone

import pytest
import yaml
from typer.testing import CliRunner

import socialctl.cli as cli
from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.brands import cargar_brand, crear_brand
from socialctl.cli import app
from socialctl.models import Platform, PostResult, PostStatus
from socialctl.postfile import cargar_post
from socialctl.scheduler import (
    ScheduleEntry,
    ScheduleStore,
    legacy_approval_hash,
    parse_scheduled_at,
)


runner = CliRunner()


class _FacebookOK(Adapter):
    platform = Platform.FACEBOOK

    def publish(self, post, brand, client):
        return PostResult(
            platform=self.platform,
            status=PostStatus.PUBLICADO,
            platform_id="remote-1",
            url="https://facebook.test/remote-1",
        )


def _make_social(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text(
        "facebook:\n  page_id: 'page-123'\n", encoding="utf-8"
    )
    post_dir = brand.dir_posts / "clip"
    post_dir.mkdir(parents=True)
    post_path = post_dir / "post.yml"
    post_path.write_text(
        yaml.safe_dump(
            {
                "slug": "clip",
                "campaign": "post-imagen",
                "platforms": {
                    "facebook": {
                        "body": "Cuerpo aprobado",
                        "content_origin": "standalone",
                        "hashtags": ["historia"],
                        "first_comment": "Comentario aprobado",
                    }
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return brand, post_path


def _schedule(root) -> None:
    result = runner.invoke(
        app,
        [
            "schedule",
            "clip",
            "--at",
            "2020-09-15T14:00:00-04:00",
            "--brand",
            "Histopast",
            "--root",
            str(root),
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.stdout


@pytest.mark.parametrize("field", ["body", "first_comment"])
def test_run_due_holds_changed_approved_text_without_publishing(
    tmp_path, monkeypatch, field
):
    """Catches run-due publishing after approved body/comment changed."""
    brand, post_path = _make_social(tmp_path)
    _schedule(tmp_path)
    data = yaml.safe_load(post_path.read_text(encoding="utf-8"))
    data["platforms"]["facebook"][field] = "Texto cambiado"
    post_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    calls = []

    class RecordingFacebook(_FacebookOK):
        def publish(self, post, brand, client):
            calls.append(post.body)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, RecordingFacebook)

    result = runner.invoke(
        app,
        ["run-due", "--brand", "Histopast", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert calls == []
    entry = ScheduleStore(brand.raiz).get("clip/facebook")
    assert entry.status == "manual_review"
    assert "huella de aprobación no coincide" in (entry.last_error or "")


@pytest.mark.parametrize("hash_kind", ["legacy", "missing"])
def test_run_due_holds_legacy_and_missing_approval_without_publishing(
    tmp_path, monkeypatch, hash_kind
):
    """Catches run-due silently accepting an old weak or absent hash."""
    brand, _ = _make_social(tmp_path)
    loaded_brand = cargar_brand(tmp_path, "Histopast")
    post = cargar_post(loaded_brand, "clip")
    content_hash = (
        legacy_approval_hash(post, Platform.FACEBOOK)
        if hash_kind == "legacy"
        else None
    )
    now = datetime.now(timezone.utc)
    ScheduleStore(brand.raiz).add(
        ScheduleEntry(
            id="clip/facebook",
            brand="Histopast",
            slug="clip",
            platform="facebook",
            scheduled_at=parse_scheduled_at("2020-09-15T14:00:00-04:00"),
            content_hash=content_hash,
            created_at=now,
            updated_at=now,
        )
    )
    calls = []

    class RecordingFacebook(_FacebookOK):
        def publish(self, post, brand, client):
            calls.append(post.body)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, RecordingFacebook)

    result = runner.invoke(
        app,
        ["run-due", "--brand", "Histopast", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert calls == []
    entry = ScheduleStore(brand.raiz).get("clip/facebook")
    assert entry.status == "manual_review"
    assert "revisión manual" in (entry.last_error or "")


def test_run_due_rejects_overlapping_executor_with_cli_error(tmp_path):
    """Catches run-due forgetting to acquire the per-brand executor lock."""
    brand, _ = _make_social(tmp_path)
    store = ScheduleStore(brand.raiz)

    with store.executor_lock():
        result = runner.invoke(
            app,
            ["run-due", "--brand", "Histopast", "--root", str(tmp_path)],
        )

    assert result.exit_code == 1
    assert "ya hay otro ejecutor" in result.stdout
    assert "Traceback" not in result.output


def test_failure_after_remote_acceptance_is_manual_review_with_remote_id(
    tmp_path, monkeypatch
):
    """Catches making a locally interrupted accepted publish retryable or losing its id."""
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, _FacebookOK)

    def fail_result_persistence(post, brand, results):
        raise OSError("disco no disponible")

    monkeypatch.setattr(cli, "guardar_resultado", fail_result_persistence)

    result = runner.invoke(
        app,
        ["run-due", "--brand", "Histopast", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.stdout
    entry = ScheduleStore(brand.raiz).get("clip/facebook")
    assert entry.status == "manual_review"
    assert entry.platform_id == "remote-1"
    assert "resultado remoto" in (entry.last_error or "")


def test_unexpected_adapter_exception_is_not_left_retryable(tmp_path, monkeypatch):
    """Catches publisher uncertainty becoming a scheduler error that can be rescheduled."""
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)

    class UnexpectedFacebook(Adapter):
        platform = Platform.FACEBOOK

        def publish(self, post, brand, client):
            raise RuntimeError("unexpected adapter failure")

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, UnexpectedFacebook)

    result = runner.invoke(
        app,
        ["run-due", "--brand", "Histopast", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.stdout
    entry = ScheduleStore(brand.raiz).get("clip/facebook")
    assert entry.status == "manual_review"
    assert "unexpected adapter failure" not in (entry.last_error or "")
    assert "incierto" in (entry.last_error or "")


def test_claim_is_directory_durable_before_publish(tmp_path, monkeypatch):
    """The remote adapter cannot run before the running rename is durable."""
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    events = []
    original = ScheduleStore._sync_directory

    def record(path):
        original(path)
        events.append(("directory_synced", path))

    class ObservingFacebook(_FacebookOK):
        def publish(self, platform_post, selected_brand, client):
            # Publication-start evidence now syncs its own directory after the
            # running claim. The claim directory must have been synced before
            # the adapter runs; it need not be the most recent directory sync.
            assert (
                "directory_synced",
                selected_brand.raiz / ".socialctl",
            ) in events
            events.append(("publish", platform_post.body))
            return super().publish(platform_post, selected_brand, client)

    monkeypatch.setattr(ScheduleStore, "_sync_directory", staticmethod(record))
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, ObservingFacebook)

    result = runner.invoke(
        app,
        ["run-due", "--brand", "Histopast", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert ("publish", "Cuerpo aprobado") in events
    assert ScheduleStore(brand.raiz).get("clip/facebook").status == "published"


def test_claim_directory_sync_failure_prevents_publish(tmp_path, monkeypatch):
    """A non-durable running claim must fail closed before any remote call."""
    brand, _ = _make_social(tmp_path)
    _schedule(tmp_path)
    calls = []
    original = ScheduleStore._sync_directory

    def fail_queue_directory(path):
        if path == brand.raiz / ".socialctl" and ScheduleStore(brand.raiz).get("clip/facebook").status == "running":
            raise OSError("simulated queue directory fsync failure")
        original(path)

    class RecordingFacebook(_FacebookOK):
        def publish(self, platform_post, selected_brand, client):
            calls.append(platform_post.platform)
            return super().publish(platform_post, selected_brand, client)

    monkeypatch.setattr(
        ScheduleStore,
        "_sync_directory",
        staticmethod(fail_queue_directory),
    )
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, RecordingFacebook)

    result = runner.invoke(
        app,
        ["run-due", "--brand", "Histopast", "--root", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "no se pudo guardar la cola" in result.stdout
    assert calls == []
    assert ScheduleStore(brand.raiz).get("clip/facebook").status == "running"


def test_schedule_batch_dry_run_shows_full_selected_content_without_writes(
    tmp_path, monkeypatch
):
    """A batch preview includes the payload, comment and media being approved."""
    brand, post_path = _make_social(tmp_path)
    image = brand.raiz / "media" / "cover.png"
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
    )
    data = yaml.safe_load(post_path.read_text(encoding="utf-8"))
    data["platforms"]["facebook"]["title"] = "Título del lote"
    data["platforms"]["facebook"]["media"] = ["cover.png"]
    post_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    batch = tmp_path / "batch.yml"
    batch.write_text(
        yaml.safe_dump(
            [{"slug": "clip", "at": "2030-09-15T14:00:00-04:00", "only": ["facebook"]}]
        ),
        encoding="utf-8",
    )

    def forbid_write(*_args, **_kwargs):
        raise AssertionError("dry-run tried to write the queue")

    monkeypatch.setattr(ScheduleStore, "add_many", forbid_write)

    result = runner.invoke(
        app,
        [
            "schedule-batch",
            str(batch),
            "--brand",
            "Histopast",
            "--root",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "--- FACEBOOK ---" in result.stdout
    assert "Titulo: Título del lote" in result.stdout
    assert "Cuerpo aprobado" in result.stdout
    assert "#historia" in result.stdout
    assert "Primer comentario: Comentario aprobado" in result.stdout
    assert "Media: cover.png (1x1)" in result.stdout
    assert "Problemas detectados: 0" in result.stdout
    assert "--dry-run: no se ha guardado la cola." in result.stdout
    assert not (brand.raiz / ".socialctl" / "schedules.json").exists()


def test_schedule_batch_yes_rejects_payload_changed_after_displayed_preview(
    tmp_path, monkeypatch
):
    """--yes skips only the prompt; it cannot approve a post-preview mutation."""
    brand, _ = _make_social(tmp_path)
    batch = tmp_path / "batch.yml"
    batch.write_text(
        yaml.safe_dump(
            [{"slug": "clip", "at": "2030-09-15T14:00:00-04:00", "only": ["facebook"]}]
        ),
        encoding="utf-8",
    )
    original = cli.render_preview

    def mutate_after_render(post, errors, **kwargs):
        rendered = original(post, errors, **kwargs)
        post.platforms[Platform.FACEBOOK].body = "Cuerpo cambiado después"
        return rendered

    monkeypatch.setattr(cli, "render_preview", mutate_after_render)

    result = runner.invoke(
        app,
        [
            "schedule-batch",
            str(batch),
            "--brand",
            "Histopast",
            "--root",
            str(tmp_path),
            "--yes",
        ],
    )

    assert result.exit_code == 1
    assert "Cuerpo aprobado" in result.stdout
    assert "cambió después del preview" in result.stdout
    assert not (brand.raiz / ".socialctl" / "schedules.json").exists()


def test_schedule_batch_yes_still_displays_preview_before_queue_write(
    tmp_path, monkeypatch
):
    """--yes is prompt control, never a substitute for rendering the payload."""
    brand, _ = _make_social(tmp_path)
    batch = tmp_path / "batch.yml"
    batch.write_text(
        yaml.safe_dump(
            [{"slug": "clip", "at": "2030-09-15T14:00:00-04:00", "only": ["facebook"]}]
        ),
        encoding="utf-8",
    )

    def forbid_remote_call(*_args, **_kwargs):
        raise AssertionError("scheduling tried to publish")

    monkeypatch.setattr(cli, "publicar", forbid_remote_call)

    result = runner.invoke(
        app,
        [
            "schedule-batch",
            str(batch),
            "--brand",
            "Histopast",
            "--root",
            str(tmp_path),
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "--- FACEBOOK ---" in result.stdout
    assert "Cuerpo aprobado" in result.stdout
    assert "Primer comentario: Comentario aprobado" in result.stdout
    assert "Cola actualizada: 1 entrada(s)." in result.stdout
    assert ScheduleStore(brand.raiz).get("clip/facebook").status == "approved"


# Frozen before origin fields were introduced; paths/timestamps do not enter v2.
PRE_ORIGIN_VIDEO_HASH = 'v2:5ac0f3b37e0e04ad5f8ab9f1d4ec0bd71cc3309e157c8ab1cff55ed8069412f8'


def _make_video_social(tmp_path, monkeypatch, *, origin='standalone', source=None, comment=None):
    from socialctl.models import MediaAsset, MediaKind
    import socialctl.postfile as postfile
    brand, path = _make_social(tmp_path)
    asset = brand.raiz / 'media/clip.mp4'
    asset.write_bytes(b'approved-video-bytes')
    monkeypatch.setattr(postfile, 'leer_media', lambda path, ruta_relativa: MediaAsset(
        path=path, ruta_relativa=ruta_relativa, kind=MediaKind.VIDEO,
        width=1080, height=1920, duration_s=20, size_bytes=20))
    data = yaml.safe_load(path.read_text())
    block = data['platforms']['facebook']
    block['media'] = ['clip.mp4']
    if origin is not None:
        block['content_origin'] = origin
    else:
        block.pop('content_origin', None)
    if source is not None:
        block['source_video_id'] = source
    if comment is not None:
        block['first_comment'] = comment
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return cargar_brand(tmp_path, 'Histopast'), path


@pytest.mark.parametrize('stored', ['valid', 'missing', 'mismatch'])
def test_pre_origin_approved_video_keeps_hash_and_only_valid_approval_runs(tmp_path, monkeypatch, stored):
    from socialctl.scheduler import approval_hash
    brand, path = _make_video_social(tmp_path, monkeypatch, origin=None)
    post = cargar_post(brand, 'clip')
    assert approval_hash(post, brand, Platform.FACEBOOK) == PRE_ORIGIN_VIDEO_HASH
    now = datetime.now(timezone.utc)
    entry = ScheduleEntry(id='clip/facebook', brand='Histopast', slug='clip', platform='facebook',
        scheduled_at=parse_scheduled_at('2020-09-15T14:00:00-04:00'), created_at=now, updated_at=now,
        content_hash={'valid': PRE_ORIGIN_VIDEO_HASH, 'missing': None, 'mismatch': 'v2:' + '0' * 64}[stored])
    store = ScheduleStore(brand.raiz)
    store.add(entry)
    before = [(e.id, e.scheduled_at, e.status, e.content_hash) for e in store.load()]
    original_post = path.read_bytes()
    calls = []
    class RecordingFacebook(_FacebookOK):
        def publish(self, post, brand, client):
            calls.append(post.body)
            return super().publish(post, brand, client)
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, RecordingFacebook)
    result = runner.invoke(app, ['run-due', '--brand', 'Histopast', '--root', str(tmp_path)])
    assert result.exit_code == 0, result.output
    after = [(e.id, e.scheduled_at, e.status, e.content_hash) for e in store.load()]
    expected_status = 'published' if stored == 'valid' else 'manual_review'
    assert after == [(before[0][0], before[0][1], expected_status, before[0][3])]
    assert path.read_bytes() == original_post
    assert len(calls) == (stored == 'valid')
    assert ('entrada anterior: origen no verificado' in result.output) == (stored == 'valid')
    # Repeated ticks never upload again to repair a failed/missing first comment.
    runner.invoke(app, ['run-due', '--brand', 'Histopast', '--root', str(tmp_path)])
    assert len(calls) == (stored == 'valid')


def test_explicit_wrong_origin_is_blocked_even_with_matching_approved_hash(tmp_path, monkeypatch):
    from socialctl.scheduler import approval_hash
    brand, _ = _make_video_social(tmp_path, monkeypatch, origin='youtube_long', source='R4cUGeaKrfU')
    now = datetime.now(timezone.utc)
    store = ScheduleStore(brand.raiz)
    store.add(ScheduleEntry(id='clip/facebook', brand='Histopast', slug='clip', platform='facebook',
        scheduled_at=parse_scheduled_at('2020-09-15T14:00:00-04:00'), created_at=now, updated_at=now,
        content_hash=approval_hash(cargar_post(brand, 'clip'), brand, Platform.FACEBOOK)))
    monkeypatch.setattr(cli, 'publicar', lambda *a, **kw: pytest.fail('invalid origin reached upload'))
    result = runner.invoke(app, ['run-due', '--brand', 'Histopast', '--root', str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert store.get('clip/facebook').status == 'error'
    assert 'comentario exacto' in result.output


@pytest.mark.parametrize('command', ['publish', 'schedule-batch'])
def test_new_origin_required_for_all_proposal_routes(tmp_path, monkeypatch, command):
    brand, _ = _make_video_social(tmp_path, monkeypatch, origin=None)
    if command == 'schedule-batch':
        batch = tmp_path / 'batch.yml'
        batch.write_text(yaml.safe_dump([{'slug': 'clip', 'at': '2030-01-01T00:00:00Z', 'only': ['facebook']}]))
        args = [command, str(batch)]
    else:
        args = [command, 'clip']
    result = runner.invoke(app, args + ['--dry-run', '--brand', 'Histopast', '--root', str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert 'declara standalone o youtube_long' in result.output
    assert not ScheduleStore(brand.raiz).load()


@pytest.mark.parametrize('mutation', ['bytes', 'account'])
def test_legacy_origin_exception_still_requires_original_bytes_and_identity(tmp_path, monkeypatch, mutation):
    brand, _ = _make_video_social(tmp_path, monkeypatch, origin=None)
    now = datetime.now(timezone.utc)
    store = ScheduleStore(brand.raiz)
    store.add(ScheduleEntry(id='clip/facebook', brand='Histopast', slug='clip', platform='facebook',
        scheduled_at=parse_scheduled_at('2020-09-15T14:00:00-04:00'), created_at=now, updated_at=now,
        content_hash=PRE_ORIGIN_VIDEO_HASH))
    if mutation == 'bytes':
        (brand.raiz / 'media/clip.mp4').write_bytes(b'modified-video-bytes')
    else:
        (brand.raiz / 'accounts.yml').write_text("facebook:\n  page_id: 'another-page'\n")
    monkeypatch.setattr(cli, 'publicar', lambda *a, **kw: pytest.fail('changed approval reached upload'))
    result = runner.invoke(app, ['run-due', '--brand', 'Histopast', '--root', str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert store.get('clip/facebook').status == 'manual_review'
    assert 'entrada anterior: origen no verificado' not in result.output


def test_legacy_instagram_still_checks_hosted_media(tmp_path, monkeypatch):
    from socialctl.scheduler import approval_hash, ScheduleError
    import socialctl.hosted_media as hosted
    brand, path = _make_video_social(tmp_path, monkeypatch, origin=None)
    data = yaml.safe_load(path.read_text())
    data['platforms'] = {'instagram': data['platforms']['facebook']}
    path.write_text(yaml.safe_dump(data))
    post = cargar_post(brand, 'clip')
    now = datetime.now(timezone.utc)
    store = ScheduleStore(brand.raiz)
    store.add(ScheduleEntry(id='clip/instagram', brand='Histopast', slug='clip', platform='instagram',
        scheduled_at=parse_scheduled_at('2020-09-15T14:00:00-04:00'), created_at=now, updated_at=now,
        content_hash=approval_hash(post, brand, Platform.INSTAGRAM)))
    class Instagram(Adapter):
        platform = Platform.INSTAGRAM
        def publish(self, *args):
            pytest.fail('invalid hosted media reached upload')
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, Instagram)
    checked = []
    def reject(post, brand, *, entry):
        checked.append(entry.id)
        raise ScheduleError('hosted bytes do not match')
    monkeypatch.setattr(hosted, 'verify_scheduled_instagram', reject)
    result = runner.invoke(app, ['run-due', '--brand', 'Histopast', '--root', str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert checked == ['clip/instagram']
    assert store.get('clip/instagram').status == 'manual_review'
