from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import json

from socialctl.native_schedule.approval import hash_value, native_digest
from socialctl.native_schedule.dispatcher import dispatch_ready
from socialctl.native_schedule.store import _replace
from tests.test_native_schedule_handoff import approved, WHEN


def api_job(tmp_path, *, platform='facebook'):
    store, job, payload, _ = approved(tmp_path, platform=platform)
    payload["delivery_mode"] = "at_time"
    changed = _replace(job, route="api", content_hash=hash_value(payload))
    changed = _replace(changed, approval_digest=native_digest(changed, payload))
    with store._db(write=True) as db:
        store._save(db, changed)
        db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job.id))
    return store, changed


CAPABILITY = {"facebook": {"route": "api", "verified_account": "account",
                           "verified_format": "reel", "grant_verified": True,
                           "remote_verified": True}}


class LostReply:
    def __init__(self):
        self.calls = 0

    def deliver(self, job, payload, checkpoint):
        self.calls += 1
        checkpoint({"phase": "submitted"})
        raise RuntimeError("reply lost")


class Success:
    def __init__(self):
        self.calls = 0

    def deliver(self, job, payload, checkpoint):
        self.calls += 1
        checkpoint({"phase": "submitted"})
        return "remote-123"


def test_api_waits_until_publish_time_and_lost_reply_never_replays(tmp_path):
    store, job = api_job(tmp_path)
    adapter = LostReply()
    options = {"store": store, "adapters": {"facebook": adapter}, "capabilities": CAPABILITY}
    assert dispatch_ready(WHEN - timedelta(seconds=1), store.brand, **options) == []
    assert store.get(job.id).state == "approved"
    assert dispatch_ready(WHEN, store.brand, **options) == []
    assert dispatch_ready(WHEN + timedelta(seconds=1), store.brand, **options) == []
    assert adapter.calls == 1
    assert store.get(job.id).state == "uncertain"
    assert store.get_attempts(job.id)[0]["checkpoint"]["phase"] == "submitted"


def test_api_success_records_remote_id_without_claiming_publication(tmp_path):
    store, job = api_job(tmp_path)
    adapter = Success()
    result = dispatch_ready(WHEN, store.brand, store=store,
                            adapters={"facebook": adapter}, capabilities=CAPABILITY)
    assert result == [job.id]
    assert adapter.calls == 1
    assert store.get(job.id).remote_id == "remote-123"
    assert store.get(job.id).state == "verifying"


def test_api_gate_rejects_other_account_without_write(tmp_path):
    store, job = api_job(tmp_path)
    adapter = Success()
    dispatch_ready(WHEN, store.brand, store=store, adapters={"facebook": adapter},
                   capabilities={"facebook": {"route": "api", "verified_account": "other"}})
    assert adapter.calls == 0
    assert store.get(job.id).state == "blocked"


def test_api_gate_rejects_unpiloted_format_without_write(tmp_path):
    store, job = api_job(tmp_path)
    adapter = Success()
    wrong = {"facebook": CAPABILITY["facebook"] | {"verified_format": "video"}}
    dispatch_ready(WHEN, store.brand, store=store, adapters={"facebook": adapter},
                   capabilities=wrong)
    assert adapter.calls == 0
    assert store.get(job.id).state == "blocked"


def test_concurrent_api_dispatch_has_one_mutating_call(tmp_path):
    store, job = api_job(tmp_path)
    adapter = Success()
    def run(_):
        return dispatch_ready(WHEN, store.brand, store=store,
                              adapters={"facebook": adapter}, capabilities=CAPABILITY)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(map(len, results)) == 1
    assert adapter.calls == 1


def test_api_deadline_expired_does_not_send(tmp_path):
    store, job = api_job(tmp_path)
    adapter = Success()
    assert dispatch_ready(WHEN + timedelta(minutes=5), store.brand, store=store,
                          adapters={"facebook": adapter}, capabilities=CAPABILITY) == []
    assert adapter.calls == 0
    assert store.get(job.id).state == "blocked"


def test_crash_recovery_keeps_attempt_for_reconciliation(tmp_path):
    store, job = api_job(tmp_path)
    claimed = store.claim(job.id, "approved", now=WHEN)
    assert claimed is not None
    store.checkpoint(job.id, claimed.attempt_id, {"phase": "submitted"}, now=WHEN)
    assert store.recover_dispatching(now=WHEN + timedelta(seconds=1)) == [job.id]
    adapter = Success()
    assert dispatch_ready(WHEN + timedelta(seconds=2), store.brand, store=store,
                          adapters={"facebook": adapter}, capabilities=CAPABILITY) == []
    assert adapter.calls == 0
    assert store.get(job.id).state == "uncertain"
