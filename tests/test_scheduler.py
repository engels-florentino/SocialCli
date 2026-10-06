import fcntl
import multiprocessing
import os
from datetime import datetime, timedelta, timezone
from queue import Empty
from pathlib import Path

import pytest

from socialctl.brands import Brand
from socialctl.models import CampaignType, MediaAsset, MediaKind, Platform, PlatformPost, Post
from socialctl.scheduler import (
    ScheduleEntry,
    ScheduleError,
    ScheduleStore,
    approval_hash,
    approval_review_reason,
    legacy_approval_hash,
    parse_scheduled_at,
)


def _entry(entry_id: str, at: str) -> ScheduleEntry:
    now = datetime.now(timezone.utc)
    return ScheduleEntry(
        id=entry_id,
        brand="Histopast",
        slug="clip",
        platform="facebook",
        scheduled_at=parse_scheduled_at(at),
        created_at=now,
        updated_at=now,
    )


def _post(brand_root: Path, *, media: list[MediaAsset] | None = None) -> Post:
    return Post(
        slug="clip",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(
                platform=Platform.FACEBOOK,
                title="Título",
                body="Cuerpo aprobado",
                hashtags=["historia"],
                media=media or [],
                link="https://example.test/fuente",
                first_comment="Comentario aprobado",
            )
        },
    )


def _brand(brand_root: Path, page_id: str = "page-123") -> Brand:
    return Brand(
        nombre="Histopast",
        raiz=brand_root,
        cuentas={"facebook": {"page_id": page_id}},
    )


def _hold_executor(brand_root: Path, ready, release) -> None:
    store = ScheduleStore(brand_root)
    with store.executor_lock():
        ready.put("locked")
        release.get(timeout=5)


def _add_entry_while_locked(brand_root: Path, started, finished) -> None:
    started.put("started")
    ScheduleStore(brand_root).add(
        _entry("worker/facebook", "2026-09-15T14:00:00-04:00")
    )
    finished.put("finished")


def test_parse_scheduled_at_requires_timezone():
    with pytest.raises(ScheduleError):
        parse_scheduled_at("2026-09-15T14:00:00")


def test_store_add_many_is_atomic_on_duplicate(tmp_path):
    store = ScheduleStore(tmp_path / "Histopast")
    store.add(_entry("clip/facebook", "2026-09-15T14:00:00-04:00"))
    with pytest.raises(ScheduleError):
        store.add_many([
            _entry("clip/instagram", "2026-09-15T14:00:00-04:00"),
            _entry("clip/facebook", "2026-09-15T14:00:00-04:00"),
        ])
    assert [entry.id for entry in store.load()] == ["clip/facebook"]


def test_store_is_scoped_to_brand_and_due_is_utc_aware(tmp_path):
    histopast = ScheduleStore(tmp_path / "Histopast")
    otra = ScheduleStore(tmp_path / "OtraMarca")
    histopast.add(_entry("clip/facebook", "2020-09-15T14:00:00-04:00"))
    assert len(histopast.due()) == 1
    assert otra.load() == []


@pytest.mark.parametrize("historical_status", ["cancelled", "published", "error"])
def test_reused_id_claims_and_transitions_the_new_active_entry(
    tmp_path, historical_status
):
    """Catches resolving a reused ID to its first terminal history entry."""
    store = ScheduleStore(tmp_path / "Histopast")
    historical = _entry("clip/facebook", "2019-09-15T14:00:00-04:00")
    historical.status = historical_status
    historical.platform_id = "old-remote"
    historical.created_at = datetime(2019, 1, 1, tzinfo=timezone.utc)
    store.add(historical)
    current = _entry("clip/facebook", "2020-09-15T14:00:00-04:00")
    current.content_hash = "v2:new-approval"
    store.add(current)

    assert store.get(current.id).content_hash == "v2:new-approval"
    claimed = store.claim_due(current.id, datetime.now(timezone.utc))
    assert claimed is not None
    assert claimed.content_hash == "v2:new-approval"
    finished = store.transition(
        current.id,
        {"running"},
        status="published",
        moment=datetime.now(timezone.utc),
        platform_id="new-remote",
    )

    assert finished is not None
    assert finished.platform_id == "new-remote"
    history = store.load()
    assert history[0].status == historical_status
    assert history[0].platform_id == "old-remote"
    assert history[1].status == "published"
    assert history[1].platform_id == "new-remote"


def test_cancel_with_reused_id_targets_new_active_entry(tmp_path):
    """Catches cancel replacing the first historical entry instead of the current one."""
    store = ScheduleStore(tmp_path / "Histopast")
    historical = _entry("clip/facebook", "2019-09-15T14:00:00-04:00")
    historical.status = "published"
    historical.platform_id = "old-remote"
    historical.created_at = datetime(2019, 1, 1, tzinfo=timezone.utc)
    store.add(historical)
    store.add(_entry("clip/facebook", "2030-09-15T14:00:00-04:00"))

    store.cancel("clip/facebook", datetime.now(timezone.utc))

    history = store.load()
    assert [(item.status, item.platform_id) for item in history] == [
        ("published", "old-remote"),
        ("cancelled", None),
    ]


def test_reschedule_with_reused_id_targets_new_active_entry(tmp_path):
    """Catches reschedule mutating the first historical entry instead of the current one."""
    store = ScheduleStore(tmp_path / "Histopast")
    historical = _entry("clip/facebook", "2019-09-15T14:00:00-04:00")
    historical.status = "error"
    historical.last_error = "old error"
    historical.created_at = datetime(2019, 1, 1, tzinfo=timezone.utc)
    store.add(historical)
    store.add(_entry("clip/facebook", "2030-09-15T14:00:00-04:00"))
    new_time = parse_scheduled_at("2031-09-15T14:00:00-04:00")

    store.reschedule("clip/facebook", new_time, datetime.now(timezone.utc))

    history = store.load()
    assert history[0].status == "error"
    assert history[0].last_error == "old error"
    assert history[1].status == "approved"
    assert history[1].scheduled_at == new_time


def test_update_rejects_a_stale_historical_occurrence_of_reused_id(tmp_path):
    """Catches legacy update replacing the active generation with a stale object."""
    store = ScheduleStore(tmp_path / "Histopast")
    historical = _entry("clip/facebook", "2019-09-15T14:00:00-04:00")
    historical.status = "cancelled"
    historical.created_at = datetime(2019, 1, 1, tzinfo=timezone.utc)
    store.add(historical)
    current = _entry("clip/facebook", "2030-09-15T14:00:00-04:00")
    current.content_hash = "v2:current"
    store.add(current)
    stale = historical.model_copy(deep=True)
    stale.status = "approved"

    with pytest.raises(ScheduleError, match='obsolete'):
        store.update(stale)

    assert store.get(current.id).content_hash == "v2:current"


def test_executor_lock_rejects_a_second_process_for_the_same_brand(tmp_path):
    """Catches removing the per-brand executor flock or making it blocking."""
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Queue()
    brand_root = tmp_path / "Histopast"
    process = context.Process(target=_hold_executor, args=(brand_root, ready, release))
    process.start()
    try:
        assert ready.get(timeout=5) == "locked"
        with pytest.raises(ScheduleError, match='another executor'):
            with ScheduleStore(brand_root).executor_lock():
                pass
    finally:
        release.put("release")
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0


def test_store_mutation_waits_for_the_interprocess_read_modify_write_lock(tmp_path):
    """Catches an add path that reads or writes without the mutation flock."""
    context = multiprocessing.get_context("spawn")
    started = context.Queue()
    finished = context.Queue()
    brand_root = tmp_path / "Histopast"
    lock_root = brand_root / ".socialctl"
    lock_root.mkdir(parents=True)
    descriptor = os.open(lock_root / "schedules.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    process = context.Process(
        target=_add_entry_while_locked,
        args=(brand_root, started, finished),
    )
    process.start()
    try:
        assert started.get(timeout=5) == "started"
        with pytest.raises(Empty):
            finished.get(timeout=0.2)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    try:
        assert finished.get(timeout=5) == "finished"
    finally:
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    assert process.exitcode == 0
    assert ScheduleStore(brand_root).get("worker/facebook").status == "approved"


def test_atomic_save_syncs_new_queue_directory_and_replaced_entry(
    tmp_path, monkeypatch
):
    """Catches returning from save before directory entries are durable."""
    brand_root = tmp_path / "Histopast"
    brand_root.mkdir()
    store = ScheduleStore(brand_root)
    synced = []
    original = store._sync_directory

    def record(path):
        original(path)
        synced.append(path)

    monkeypatch.setattr(store, "_sync_directory", record)
    store.save([_entry("clip/facebook", "2026-09-15T14:00:00-04:00")])

    assert synced == [brand_root, brand_root / ".socialctl"]


def test_save_retry_resyncs_parent_after_new_directory_sync_failure(
    tmp_path, monkeypatch
):
    """A visible mkdir whose parent fsync failed must be synced on retry."""
    brand_root = tmp_path / "Histopast"
    brand_root.mkdir()
    store = ScheduleStore(brand_root)
    original = store._sync_directory
    failed_once = False
    successful = []

    def fail_first_parent_sync(path):
        nonlocal failed_once
        if path == brand_root and not failed_once:
            failed_once = True
            raise OSError("simulated parent directory fsync failure")
        original(path)
        successful.append(path)

    monkeypatch.setattr(store, "_sync_directory", fail_first_parent_sync)
    entries = [_entry("clip/facebook", "2026-09-15T14:00:00-04:00")]

    with pytest.raises(ScheduleError, match='save'):
        store.save(entries)
    assert store.root.is_dir()
    assert brand_root not in successful

    store.save(entries)

    assert successful == [brand_root, store.root]


@pytest.mark.parametrize("executor_status", ["running", "published"])
def test_cancel_and_reschedule_cannot_overwrite_executor_state(
    tmp_path, executor_status
):
    """Catches get-then-update commands overwriting newer executor state."""
    store = ScheduleStore(tmp_path / "Histopast")
    entry = _entry("clip/facebook", "2020-09-15T14:00:00-04:00")
    store.add(entry)

    claimed = store.claim_due(entry.id, datetime.now(timezone.utc))
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempts == 1
    if executor_status == "published":
        transitioned = store.transition(
            entry.id,
            {"running"},
            status="published",
            moment=datetime.now(timezone.utc),
            platform_id="remote-1",
        )
        assert transitioned is not None

    with pytest.raises(ScheduleError, match=f'current state {executor_status}'):
        store.cancel(entry.id, datetime.now(timezone.utc))
    with pytest.raises(ScheduleError, match=f'current state {executor_status}'):
        store.reschedule(
            entry.id,
            parse_scheduled_at("2030-09-15T14:00:00-04:00"),
            datetime.now(timezone.utc),
        )

    assert store.get(entry.id).status == executor_status


def test_recover_stale_holds_uncertain_running_entry_for_manual_review(tmp_path):
    """Catches restoring stale running work to approved and republishing it."""
    store = ScheduleStore(tmp_path / "Histopast")
    entry = _entry("clip/facebook", "2020-09-15T14:00:00-04:00")
    entry.status = "running"
    entry.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    store.add(entry)

    assert store.recover_stale(max_age_s=900) == 1

    recovered = store.get(entry.id)
    assert recovered.status == "manual_review"
    assert 'remote result' in (recovered.last_error or "")
    assert store.due() == []


@pytest.mark.parametrize("field", ["body", "first_comment"])
def test_v2_approval_hash_covers_each_publishable_text_field(tmp_path, field):
    """Catches omitting body or first_comment from the approved payload."""
    post = _post(tmp_path)
    brand = _brand(tmp_path)
    original = approval_hash(post, brand, Platform.FACEBOOK)
    changed = post.model_copy(deep=True)
    setattr(changed.platforms[Platform.FACEBOOK], field, "Texto cambiado")

    assert original.startswith("v2:")
    assert approval_hash(changed, brand, Platform.FACEBOOK) != original


def test_v2_approval_hash_detects_same_size_media_replacement(tmp_path):
    """Catches trusting media size/path/mtime instead of streaming its bytes."""
    media_path = tmp_path / "clip.jpg"
    media_path.write_bytes(b"ABCD")
    original_stat = media_path.stat()
    asset = MediaAsset(path=media_path, kind=MediaKind.IMAGE, ruta_relativa="clip.jpg")
    post = _post(tmp_path, media=[asset])
    brand = _brand(tmp_path)
    original = approval_hash(post, brand, Platform.FACEBOOK)

    media_path.write_bytes(b"WXYZ")
    os.utime(media_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    assert media_path.stat().st_size == original_stat.st_size
    assert media_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert approval_hash(post, brand, Platform.FACEBOOK) != original


def test_v2_approval_hash_covers_brand_account_identity(tmp_path):
    """Catches publishing an approval through a different configured account."""
    post = _post(tmp_path)

    first = approval_hash(post, _brand(tmp_path, "page-123"), Platform.FACEBOOK)
    second = approval_hash(post, _brand(tmp_path, "page-999"), Platform.FACEBOOK)

    assert first != second


def test_legacy_matched_hash_is_held_because_it_did_not_cover_bytes_or_account(tmp_path):
    """Catches treating a matched legacy hash as equivalent to a v2 approval."""
    post = _post(tmp_path)
    brand = _brand(tmp_path)
    stored = legacy_approval_hash(post, Platform.FACEBOOK)

    reason = approval_review_reason(stored, post, brand, Platform.FACEBOOK)

    assert reason is not None
    assert 'legacy' in reason
    assert "bytes" in reason
    assert 'account' in reason


def test_legacy_mismatch_and_missing_hash_are_never_silently_approved(tmp_path):
    """Catches approving unverifiable old entries or changed legacy content."""
    post = _post(tmp_path)
    brand = _brand(tmp_path)
    old_post = post.model_copy(deep=True)
    old_post.platforms[Platform.FACEBOOK].body = "Cuerpo anterior"
    old_hash = legacy_approval_hash(old_post, Platform.FACEBOOK)

    mismatch = approval_review_reason(old_hash, post, brand, Platform.FACEBOOK)
    missing = approval_review_reason(None, post, brand, Platform.FACEBOOK)

    assert mismatch is not None and 'does not match' in mismatch
    assert missing is not None and 'no approval fingerprint' in missing


def test_matching_v2_hash_is_the_only_approval_that_can_run(tmp_path):
    """Catches accidentally holding a current, unchanged v2 approval."""
    post = _post(tmp_path)
    brand = _brand(tmp_path)
    stored = approval_hash(post, brand, Platform.FACEBOOK)

    assert approval_review_reason(stored, post, brand, Platform.FACEBOOK) is None
