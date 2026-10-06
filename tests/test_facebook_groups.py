from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.management.changes import ChangeError
from socialctl.management.facebook_groups import (
    CAPABILITY,
    CONFIRMATION_PHRASE,
    GroupError,
    GroupStore,
    approve_handoff,
    confirm_group,
    export_queue,
    group_fingerprint,
    mark_handoff_opened,
    prepare_group,
    validate_group_url,
    validate_post_url,
    validate_youtube_url,
)


runner = CliRunner()
START = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def brand(tmp_path):
    return crear_brand(tmp_path, "Example")


def proposal(brand, **overrides):
    values = {
        "group_name": "Historia del Caribe",
        "group_url": "https://www.facebook.com/groups/historia.caribe/",
        "copy": "Una historia documentada para debatir con fuentes.",
        "youtube_url": "https://youtu.be/R4cUGeaKrfU",
        "window_start": START,
        "window_end": END,
        "media": None,
    }
    values.update(overrides)
    return prepare_group(brand, GroupStore(brand.raiz), **values)


def write_image(path, *, color="red"):
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=32x32",
            "-frames:v", "1", str(path),
        ],
        check=True,
        capture_output=True,
    )


def write_minimal_pdf(path, *, objects=None, root=1):
    if objects is None:
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>",
        ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode())
        data.extend(body + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {root} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(data)


def test_prepare_is_durable_canonical_and_has_no_remote_client(brand):
    change = proposal(brand)

    assert change.status == "prepared"
    assert change.capability == CAPABILITY
    assert change.destination.url == "https://www.facebook.com/groups/historia.caribe"
    assert change.youtube_url == "https://www.youtube.com/watch?v=R4cUGeaKrfU"
    assert change.fingerprint == group_fingerprint(change)
    stored = json.loads(GroupStore(brand.raiz).path_for(change.id).read_text())
    assert stored["copy"] == change.copy_text
    assert "copy_text" not in stored
    assert GroupStore(brand.raiz).load(change.id) == change
    assert change.journal[0]["event"] == "prepared_local_no_api_write"


def test_prepare_rejects_exact_duplicate_even_after_confirmation(brand):
    change = proposal(brand)
    approve_handoff(brand, GroupStore(brand.raiz), change.id, change.fingerprint)
    confirm_group(
        brand,
        GroupStore(brand.raiz),
        change.id,
        confirmation_phrase=CONFIRMATION_PHRASE,
        post_url="https://www.facebook.com/groups/historia.caribe/posts/123",
    )

    with pytest.raises(GroupError, match="duplicada"):
        proposal(brand)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("http://www.facebook.com/groups/example", "HTTPS"),
        ("https://graph.facebook.com/v26.0/groups/example", "facebook.com"),
        ("https://www.facebook.com/groups/example?access_token=secret", "secreto|query"),
        ("https://127.0.0.1/groups/example", "facebook.com"),
        ("https://www.facebook.com/example", "forma"),
    ],
)
def test_group_url_rejects_private_or_ambiguous_targets(value, message):
    with pytest.raises(GroupError, match=message):
        validate_group_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://api.youtube.com/watch?v=R4cUGeaKrfU",
        "https://www.youtube.com/watch?v=R4cUGeaKrfU&access_token=secret",
        "http://youtu.be/R4cUGeaKrfU",
        "https://youtu.be/too-short",
        "https://www.youtube.com/shorts/R4cUGeaKrfU",
    ],
)
def test_youtube_url_accepts_only_public_long_watch_links(value):
    with pytest.raises(GroupError):
        validate_youtube_url(value)


def test_url_normalization_and_post_must_belong_to_approved_group():
    assert validate_youtube_url("https://youtu.be/R4cUGeaKrfU") == (
        "https://www.youtube.com/watch?v=R4cUGeaKrfU",
        "R4cUGeaKrfU",
    )
    assert validate_post_url(
        "https://m.facebook.com/groups/example/permalink/pfbid123/", "example"
    ) == "https://www.facebook.com/groups/example/permalink/pfbid123"
    with pytest.raises(GroupError, match="mismo Grupo"):
        validate_post_url("https://www.facebook.com/groups/other/posts/1", "example")


@pytest.mark.parametrize(
    "field",
    [
        {"copy": "Mira esto access_token=supersecretvalue"},
        {"copy": "Authorization: Bearer abcdefghijklmnop"},
        {"group_name": "sk-abcdefghijklmnop"},
    ],
)
def test_prepare_rejects_secrets_in_exportable_text(brand, field):
    with pytest.raises(GroupError, match="secreto|credencial"):
        proposal(brand, **field)


def test_window_requires_timezone_and_forward_order(brand):
    with pytest.raises(GroupError, match="zona horaria"):
        proposal(brand, window_start=START.replace(tzinfo=None))
    with pytest.raises(GroupError, match="posterior"):
        proposal(brand, window_end=START)


def test_approval_is_exact_and_handoff_is_idempotent(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    with pytest.raises(ChangeError, match="huella exacta"):
        approve_handoff(brand, store, change.id, "0" * 64)
    assert store.load(change.id).status == "prepared"

    ready = approve_handoff(brand, store, change.id, change.fingerprint)
    assert ready.status == "handoff_ready"
    journal = list(ready.journal)
    same = approve_handoff(brand, store, change.id, change.fingerprint)
    assert same.journal == journal
    assert [event["event"] for event in same.journal][-2:] == [
        "approved_exact_digest",
        "browser_handoff_ready_no_publication",
    ]


def test_opening_handoff_never_claims_publication_and_is_idempotent(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    opened = mark_handoff_opened(brand, store, change.id)

    assert opened.status == "awaiting_manual_confirmation"
    assert opened.confirmation is None
    assert opened.handoff_opened_at is not None
    assert mark_handoff_opened(brand, store, change.id).journal == opened.journal


def test_confirm_requires_handoff_phrase_and_evidence(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    with pytest.raises(GroupError, match="exactamente"):
        confirm_group(
            brand, store, change.id, confirmation_phrase="sí",
            post_url="https://www.facebook.com/groups/historia.caribe/posts/123",
        )
    with pytest.raises(GroupError, match="handoff"):
        confirm_group(
            brand, store, change.id, confirmation_phrase=CONFIRMATION_PHRASE,
            post_url="https://www.facebook.com/groups/historia.caribe/posts/123",
        )
    approve_handoff(brand, store, change.id, change.fingerprint)
    with pytest.raises(GroupError, match="--post-url"):
        confirm_group(brand, store, change.id, confirmation_phrase=CONFIRMATION_PHRASE)


def test_confirm_is_manual_and_cannot_be_overwritten(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    url = "https://www.facebook.com/groups/historia.caribe/posts/123"
    confirmed = confirm_group(
        brand, store, change.id,
        confirmation_phrase=CONFIRMATION_PHRASE, post_url=url,
    )

    assert confirmed.status == "confirmed_manual"
    assert confirmed.confirmation.authority == "user_supplied_manual_confirmation"
    assert confirmed.confirmation.post_url == url
    journal = list(confirmed.journal)
    assert confirm_group(
        brand, store, change.id,
        confirmation_phrase=CONFIRMATION_PHRASE, post_url=url,
    ).journal == journal
    with pytest.raises(GroupError, match="no se sobrescribe"):
        confirm_group(
            brand, store, change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            post_url="https://www.facebook.com/groups/historia.caribe/posts/456",
        )


def test_evidence_is_existing_local_non_secret_file(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "evidence.png"
    write_image(evidence)
    result = confirm_group(
        brand, store, change.id,
        confirmation_phrase=CONFIRMATION_PHRASE, evidence="evidence.png",
    )
    assert result.confirmation.evidence.relative_path == "evidence.png"
    assert result.confirmation.evidence.sha256

    second = proposal(brand, copy="Una segunda propuesta distinta.")
    approve_handoff(brand, store, second.id, second.fingerprint)
    (brand.raiz / ".secrets").mkdir(exist_ok=True)
    (brand.raiz / ".secrets" / "proof.png").write_bytes(b"secret")
    with pytest.raises(GroupError, match=".secrets"):
        confirm_group(
            brand, store, second.id,
            confirmation_phrase=CONFIRMATION_PHRASE, evidence=".secrets/proof.png",
        )


@pytest.mark.parametrize("suffix", [".jpg", ".png", ".webp", ".pdf"])
def test_evidence_rejects_fake_bytes_even_with_allowed_suffix(brand, suffix):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / f"fake{suffix}"
    evidence.write_bytes(b"these are not image or PDF bytes")

    with pytest.raises(GroupError, match="PDF|formato|stream|imagen"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


def test_evidence_accepts_structurally_parseable_pdf(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "evidence.pdf"
    write_minimal_pdf(evidence)

    result = confirm_group(
        brand,
        store,
        change.id,
        confirmation_phrase=CONFIRMATION_PHRASE,
        evidence=evidence.name,
    )

    assert result.confirmation.evidence.relative_path == evidence.name


def test_evidence_rejects_catalog_pages_pointing_directly_to_page(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "catalog-direct-page.pdf"
    write_minimal_pdf(
        evidence,
        objects=[
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Page /MediaBox [0 0 10 10] >>",
        ],
    )

    with pytest.raises(GroupError, match=r"Catalog /Pages.*?/Type /Pages"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


@pytest.mark.parametrize(
    "page",
    [
        b"<< /Type /Page /Note (/Parent 2 0 R) /MediaBox [0 0 10 10] >>",
        b"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 10 10] >>",
    ],
)
def test_evidence_rejects_pdf_page_with_missing_or_incorrect_parent(brand, page):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "bad-parent.pdf"
    write_minimal_pdf(
        evidence,
        objects=[
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            page,
        ],
    )

    with pytest.raises(GroupError, match="Parent inexistente o incorrecto"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


@pytest.mark.parametrize(
    "objects",
    [
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 3 0 R] /Count 2 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>",
        ],
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            b"<< /Type /Pages /Parent 2 0 R /Kids [5 0 R] /Count 1 >>",
            b"<< /Type /Pages /Parent 2 0 R /Kids [5 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 10 10] >>",
        ],
    ],
)
def test_evidence_rejects_pdf_duplicate_or_shared_kids(brand, objects):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "shared-kid.pdf"
    write_minimal_pdf(evidence, objects=objects)

    with pytest.raises(GroupError, match="Kid duplicado o compartido"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


def test_evidence_rejects_pdf_incoherent_page_count(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "bad-count.pdf"
    write_minimal_pdf(
        evidence,
        objects=[
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 2 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>",
        ],
    )

    with pytest.raises(GroupError, match="recuento de páginas incoherente"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


@pytest.mark.parametrize(
    "catalog",
    [
        b"<< /Ignored (/Type /Catalog /Pages 2 0 R) /Type /NotCatalog >>",
        b"<< /Ignored true % /Type /Catalog /Pages 2 0 R\n /Type /NotCatalog >>",
    ],
)
def test_evidence_pdf_dictionary_ignores_strings_and_comments(brand, catalog):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "lexical-dictionary.pdf"
    write_minimal_pdf(
        evidence,
        objects=[
            catalog,
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>",
        ],
    )

    with pytest.raises(GroupError, match="Root no es un catálogo"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


def test_evidence_rejects_avi_mjpeg_renamed_as_jpeg(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "avi-renamed.jpg"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=32x32:rate=2",
            "-t", "1", "-c:v", "mjpeg", "-f", "avi", str(evidence),
        ],
        check=True,
        capture_output=True,
    )

    with pytest.raises(GroupError, match="formato real|JPEG"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


@pytest.mark.parametrize(
    "objects,root",
    [
        (
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>",
            ],
            9,
        ),
        (
            [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [] /Count 0 >>",
            ],
            1,
        ),
    ],
)
def test_evidence_rejects_pdf_without_resolvable_nonempty_page_tree(brand, objects, root):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "fake-structure.pdf"
    write_minimal_pdf(evidence, objects=objects, root=root)

    with pytest.raises(GroupError, match="Root inexistente|páginas reales"):
        confirm_group(
            brand,
            store,
            change.id,
            confirmation_phrase=CONFIRMATION_PHRASE,
            evidence=evidence.name,
        )


def test_confirm_idempotency_uses_durable_evidence_after_file_disappears(brand):
    store = GroupStore(brand.raiz)
    change = proposal(brand)
    approve_handoff(brand, store, change.id, change.fingerprint)
    evidence = brand.raiz / "evidence.png"
    write_image(evidence)
    confirmed = confirm_group(
        brand,
        store,
        change.id,
        confirmation_phrase=CONFIRMATION_PHRASE,
        evidence=evidence.name,
    )
    journal = list(confirmed.journal)
    evidence.unlink()

    repeated = confirm_group(
        brand,
        store,
        change.id,
        confirmation_phrase=CONFIRMATION_PHRASE,
        evidence=evidence.name,
    )

    assert repeated.confirmation == confirmed.confirmation
    assert repeated.journal == journal


def test_existing_media_is_hashed_and_rechecked_before_handoff(brand, tmp_path):
    source = tmp_path / "source.jpg"
    write_image(source)
    target = brand.raiz / "media" / "group.jpg"
    shutil.copyfile(source, target)
    change = proposal(brand, media="group.jpg")
    assert change.media.relative_path == "group.jpg"
    assert change.media.sha256

    target.write_bytes(b"changed after approval preview")
    with pytest.raises(GroupError, match="media"):
        approve_handoff(brand, GroupStore(brand.raiz), change.id, change.fingerprint)


def test_media_rejects_mp3_and_audio_only_mp4(brand):
    for name in ("audio.mp3", "audio.mp4"):
        path = brand.raiz / "media" / name
        codec = "libmp3lame" if path.suffix == ".mp3" else "aac"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=1", "-c:a", codec, str(path),
            ],
            check=True,
            capture_output=True,
        )
        with pytest.raises(GroupError, match="formato|audio solo|pista de vídeo"):
            proposal(brand, media=name, copy=f"Propuesta para {name}")


def test_media_rejects_image_extension_that_disagrees_with_real_codec(brand):
    jpeg = brand.raiz / "media" / "source.jpg"
    disguised = brand.raiz / "media" / "disguised.png"
    write_image(jpeg)
    shutil.copyfile(jpeg, disguised)

    with pytest.raises(GroupError, match="no coincide"):
        proposal(brand, media=disguised.name)


def test_media_symlink_outside_brand_is_rejected(brand, tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    (brand.raiz / "media" / "escape.jpg").symlink_to(outside)
    with pytest.raises(GroupError):
        proposal(brand, media="escape.jpg")


def test_export_queue_is_sanitized_and_readable(brand):
    change = proposal(brand)
    item = export_queue(brand, GroupStore(brand.raiz))[0]
    assert item["id"] == change.id
    assert item["copy"] == change.copy_text
    assert "brand_root" not in item and "dedupe_key" not in item
    assert "no publica" in item["reminder"]


def _prepare_cli(brand, root, *, media=None):
    args = [
        "group", "prepare",
        "--group-name", "Historia del Caribe",
        "--group-url", "https://www.facebook.com/groups/historia.caribe",
        "--copy", "Una historia documentada.",
        "--youtube-url", "https://youtu.be/R4cUGeaKrfU",
        "--window-start", "2026-09-16T18:00:00Z",
        "--window-end", "2026-09-16T20:00:00Z",
        "--brand", brand.nombre,
        "--root", str(root),
        "--dry-run",
    ]
    if media is not None:
        args.extend(["--media", media])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return GroupStore(brand.raiz).load(next(GroupStore(brand.raiz).root.glob("*.json")).stem), result


def test_cli_full_preview_export_handoff_and_confirmation_are_local(brand, tmp_path, monkeypatch):
    change, prepared = _prepare_cli(brand, tmp_path)
    assert "PREVIEW COMPLETO" in prepared.output
    for value in (change.id, change.fingerprint, change.copy_text, change.youtube_url, change.destination.url):
        assert value in prepared.output

    opened = []
    monkeypatch.setattr("socialctl.management.groups_cli.webbrowser.open_new_tab", lambda url: opened.append(url) or True)
    handed = runner.invoke(
        app,
        ["group", "handoff", change.id, "--approval-digest", change.fingerprint,
         "--open-browser", "--brand", brand.nombre, "--root", str(tmp_path)],
    )
    assert handed.exit_code == 0, handed.output
    assert opened == [change.destination.url]
    assert "no confirma publicación" in handed.output
    assert GroupStore(brand.raiz).load(change.id).status == "awaiting_manual_confirmation"

    exported = runner.invoke(
        app,
        ["group", "export", "--format", "json", "--brand", brand.nombre, "--root", str(tmp_path)],
    )
    assert exported.exit_code == 0, exported.output
    payload = json.loads(exported.output)
    assert payload["authority"] == "local_manual_handoff"
    assert payload["items"][0]["status"] == "awaiting_manual_confirmation"

    confirmed = runner.invoke(
        app,
        ["group", "confirm", change.id,
         "--post-url", "https://www.facebook.com/groups/historia.caribe/posts/123",
         "--confirmation", CONFIRMATION_PHRASE,
         "--brand", brand.nombre, "--root", str(tmp_path)],
    )
    assert confirmed.exit_code == 0, confirmed.output
    assert "user_supplied_manual_confirmation" in confirmed.output


@pytest.mark.parametrize("suffix", [".jpg", ".mp4"])
def test_cli_preview_shows_every_fingerprinted_media_field(brand, tmp_path, suffix):
    media_path = brand.raiz / "media" / f"preview{suffix}"
    if suffix == ".jpg":
        write_image(media_path)
    else:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "color=c=blue:s=32x32:r=5:d=1", "-pix_fmt", "yuv420p",
                str(media_path),
            ],
            check=True,
            capture_output=True,
        )
    change, prepared = _prepare_cli(brand, tmp_path, media=media_path.name)

    expected_duration = (
        f"Duración: {change.media.duration_s} s"
        if change.media.duration_s is not None
        else "Duración: no aplica"
    )
    for value in (
        change.media.relative_path,
        change.media.sha256,
        str(change.media.size_bytes),
        change.media.kind,
        f"{change.media.width}x{change.media.height}",
        expected_duration,
    ):
        assert value in prepared.output


def test_cli_requires_brand_and_wrong_approval_never_opens_browser(brand, tmp_path, monkeypatch):
    missing = runner.invoke(app, ["group", "export"])
    assert missing.exit_code != 0
    change, _ = _prepare_cli(brand, tmp_path)
    opened = []
    monkeypatch.setattr("socialctl.management.groups_cli.webbrowser.open_new_tab", lambda url: opened.append(url) or True)
    result = runner.invoke(
        app,
        ["group", "handoff", change.id, "--approval-digest", "wrong", "--open-browser",
         "--brand", brand.nombre, "--root", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert not opened
    assert GroupStore(brand.raiz).load(change.id).status == "prepared"


def test_cli_default_handoff_does_not_open_browser(brand, tmp_path, monkeypatch):
    change, _ = _prepare_cli(brand, tmp_path)
    monkeypatch.setattr(
        "socialctl.management.groups_cli.webbrowser.open_new_tab",
        lambda url: pytest.fail("default handoff must not open a browser"),
    )
    result = runner.invoke(
        app,
        ["group", "handoff", change.id, "--approval-digest", change.fingerprint,
         "--brand", brand.nombre, "--root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert GroupStore(brand.raiz).load(change.id).status == "handoff_ready"


def test_cli_browser_failure_preserves_retryable_handoff(brand, tmp_path, monkeypatch):
    change, _ = _prepare_cli(brand, tmp_path)
    monkeypatch.setattr("socialctl.management.groups_cli.webbrowser.open_new_tab", lambda url: False)
    result = runner.invoke(
        app,
        ["group", "handoff", change.id, "--approval-digest", change.fingerprint,
         "--open-browser", "--brand", brand.nombre, "--root", str(tmp_path)],
    )
    assert result.exit_code != 0
    stored = GroupStore(brand.raiz).load(change.id)
    assert stored.status == "handoff_ready"
    assert "apertura" in stored.last_error
    assert stored.confirmation is None


def test_cli_export_creates_private_file_and_never_overwrites(brand, tmp_path):
    _prepare_cli(brand, tmp_path)
    output = tmp_path / "queue.json"
    args = [
        "group", "export", "--format", "json", "--output", str(output),
        "--brand", brand.nombre, "--root", str(tmp_path),
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["authority"] == "local_manual_handoff"
    before = output.read_bytes()
    second = runner.invoke(app, args)
    assert second.exit_code != 0
    assert output.read_bytes() == before


def test_status_rejects_changeset_copied_between_brands_before_render(tmp_path):
    first = crear_brand(tmp_path, "First")
    second = crear_brand(tmp_path, "Second")
    change = proposal(first)
    source = GroupStore(first.raiz).path_for(change.id)
    target_store = GroupStore(second.raiz)
    target_store.root.mkdir(parents=True)
    shutil.copyfile(source, target_store.path_for(change.id))

    result = runner.invoke(
        app,
        ["group", "status", change.id, "--brand", second.nombre, "--root", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "marca configurada" in result.output
    assert change.copy_text not in result.output
