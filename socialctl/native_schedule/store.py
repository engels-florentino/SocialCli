"""SQLite ledger. All writers use BEGIN IMMEDIATE, full sync, and CAS.

Constructing or reading a store never creates a directory/database. Recovery is
explicit: callers must quiesce workers before recover_dispatching; opening another
connection must not steal a live worker's claim. Uncertain jobs cannot be claimed.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .cadence import reservation_day, ZONE
from .inventory import validate_inventory, date
from .models import NativeJob, NativeObservation, aware
from .verification import evidence_matches, verify

# Remote outcomes may ONLY be established via evidence, not generic transitions.
TRANSITIONS = {
    'prepared': {'approved', 'blocked', 'cancelled'},
    'approved': {'ready', 'waiting_window', 'awaiting_ui', 'blocked', 'cancelled'},
    'waiting_window': {'ready', 'awaiting_ui', 'blocked', 'cancelled'},
    'ready': {'awaiting_ui', 'blocked', 'cancelled'},
    'dispatching': {'verifying', 'awaiting_ui', 'uncertain'},
    'awaiting_ui': {'verifying', 'uncertain'},
    'verifying': {'uncertain'},
    'native_scheduled': {'uncertain'},
    'uncertain': {'verifying'},
    'blocked': {'prepared'},
}


def _now(value):
    return aware(value or datetime.now(timezone.utc))


def _replace(job, **changes):
    # model_copy(update=...) bypasses validation; deliberately never use it.
    return NativeJob.model_validate(job.model_dump() | changes)


class NativeStore:
    def __init__(self, brand_root: Path, *, brand: str):
        if not brand or not brand.strip():
            raise ValueError('Explicit brand required')
        self.brand = brand
        self.path = Path(brand_root).resolve() / 'native-schedules.sqlite3'

    @contextmanager
    def _db(self, *, write=False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path) if write else self.path.as_uri() + '?mode=ro',
                             uri=not write, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1, 2):
                    raise ValueError('Unsupported native ledger schema')
                if version == 0:
                    for statement in (
                        'CREATE TABLE context (brand TEXT NOT NULL)',
                        'CREATE TABLE jobs (id TEXT PRIMARY KEY, legacy_entry_id TEXT, platform TEXT NOT NULL, account_id TEXT NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(legacy_entry_id,platform), UNIQUE(platform,account_id,day))',
                        'CREATE TABLE attempts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), approval_digest TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, checkpoint TEXT NOT NULL)',
                        'CREATE TABLE observations (id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), data TEXT NOT NULL, verified INTEGER NOT NULL)',
                    ):
                        db.execute(statement)
                    db.execute('INSERT INTO context VALUES (?)', (self.brand,))
                    db.execute('PRAGMA user_version=1')
            if db.execute('PRAGMA user_version').fetchone()[0] not in (1, 2):
                raise ValueError('Unsupported native ledger schema')
            if db.execute('PRAGMA user_version').fetchone()[0] == 2:
                for table in ('calendar_inventory','external_reservations'):
                    if not db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone():
                        raise ValueError('Calendar reservation ledger missing; keep writers stopped and recover evidence')
            if write:
                db.execute('CREATE TABLE IF NOT EXISTS calendar_inventory (digest TEXT PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS external_reservations (inventory_digest TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL, remote_ref TEXT NOT NULL, day TEXT NOT NULL, publish_at TEXT NOT NULL, UNIQUE(inventory_digest,platform,account_id,remote_ref,day,publish_at))')
                db.execute('CREATE INDEX IF NOT EXISTS external_slot ON external_reservations(platform,account_id,day)')
                # Old native writers must reject this ledger rather than ignore
                # the external reservations they do not know how to enforce.
                db.execute('PRAGMA user_version=2')
                db.execute('CREATE TABLE IF NOT EXISTS action_events (job_id TEXT NOT NULL, data TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS actions (job_id TEXT PRIMARY KEY, data TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS holds (job_id TEXT PRIMARY KEY, platform TEXT NOT NULL, account_id TEXT NOT NULL, day TEXT NOT NULL, UNIQUE(platform,account_id,day))')
                db.execute('CREATE TABLE IF NOT EXISTS cooldowns (job_id TEXT PRIMARY KEY, until_at TEXT NOT NULL, reason TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS notices (job_id TEXT NOT NULL, created_at TEXT NOT NULL, message TEXT NOT NULL)')
            if db.execute('SELECT brand FROM context').fetchone()[0] != self.brand:
                raise ValueError('Ledger belongs to a different brand')
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, job_id):
        row = db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
        return NativeJob.model_validate_json(row[0]) if row else None

    @staticmethod
    def _save(db, job):
        db.execute('UPDATE jobs SET data=? WHERE id=?', (job.model_dump_json(), job.id))

    def get(self, job_id: str) -> NativeJob | None:
        with self._db() as db:
            return self._get(db, job_id) if db else None

    def list_jobs(self) -> list[NativeJob]:
        with self._db() as db:
            return [NativeJob.model_validate_json(row[0]) for row in
                    db.execute('SELECT data FROM jobs ORDER BY day,id')] if db else []

    @staticmethod
    def _record_inventory(db, inventory):
        from .approval import hash_value
        validate_inventory(inventory)  # Recovery retains even stale protection.
        digest = hash_value(inventory)
        db.execute('INSERT OR IGNORE INTO calendar_inventory VALUES (?,?)',
                   (digest,json.dumps(inventory,allow_nan=False)))
        for item in inventory['objects']:
            instant = date(item['publish_at'])
            db.execute('INSERT OR IGNORE INTO external_reservations VALUES (?,?,?,?,?,?)',
                (digest,item['platform'],item['account_id'],item['remote_ref'],
                 instant.astimezone(ZONE).date().isoformat(),instant.isoformat()))

    def record_inventory(self, inventory):
        """Append evidence atomically, including known native refs; never release.

        Changed and absent refs conservatively retain every observed day. There
        is deliberately no automatic reconciliation/removal API.
        """
        validate_inventory(inventory)
        with self._db(write=True) as db:
            self._record_inventory(db,inventory)

    @staticmethod
    def _external_occupied(db, platform, account_id, day, *, remote_ref=None, publish_at=None):
        if remote_ref is not None and publish_at is not None:
            return db.execute(
                'SELECT 1 FROM external_reservations WHERE platform=? AND account_id=? AND day=? AND NOT (remote_ref=? AND publish_at=?)',
                (platform,account_id,day,remote_ref,publish_at.isoformat())).fetchone() is not None
        return db.execute('SELECT 1 FROM external_reservations WHERE platform=? AND account_id=? AND day=?',
                          (platform,account_id,day)).fetchone() is not None

    @classmethod
    def _check_external(cls, db, job):
        if cls._external_occupied(db,job.platform,job.account_id,reservation_day(job.publish_at,job.timezone_name)):
            raise ValueError('Slot reserved by external calendar evidence; reconcile before delivery')

    def prepare(self, job: NativeJob, *, payload: dict, now: datetime | None = None, inventory: dict | None = None) -> NativeJob:
        now = _now(now)
        if job.approval_digest is not None:
            raise ValueError('Prepared jobs cannot carry a prefilled approval digest')
        if (job.brand != self.brand or job.state != 'prepared' or job.attempt_id
                or job.remote_id):
            raise ValueError('Only new prepared jobs for this explicit brand may be inserted')
        if job.publish_at <= now:
            raise ValueError('Cannot prepare a past publication')
        day = reservation_day(job.publish_at, job.timezone_name)
        serialized_payload = json.dumps(payload, allow_nan=False)
        # Commit learned occupancy first, even if a competing admission rejects
        # this job. The following write transaction rechecks all reservations.
        if inventory is not None:
            self.record_inventory(inventory)
        with self._db(write=True) as db:
            self._check_external(db,job)
            if db.execute('SELECT 1 FROM holds WHERE platform=? AND account_id=? AND day=?', (job.platform,job.account_id,day)).fetchone():
                raise ValueError('Slot reserved by pending remote action')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',
                (job.id, job.legacy_entry_id, job.platform, job.account_id, day,
                 job.model_dump_json(), serialized_payload))
        return job

    def get_payload(self, job_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone() if db else None
            return json.loads(row[0]) if row else None

    def transition(self, job_id: str, expected_state: str, state: str, *,
                   approval_digest: str | None = None, now: datetime | None = None) -> NativeJob | None:
        if state not in TRANSITIONS.get(expected_state, set()):
            raise ValueError('Unsafe state transition; reconcile remote evidence instead')
        with self._db(write=True) as db:
            job = self._get(db, job_id)
            if job is None or job.state != expected_state:
                return None
            changes = dict(state=state, updated_at=_now(now))
            if state == 'uncertain':
                changes['reconcile_after'] = max(filter(None, [
                    job.reconcile_after, job.observation_watermark, changes['updated_at']]))
            if approval_digest is not None:
                if (expected_state, state) != ('prepared', 'approved'):
                    raise ValueError('Approval can only be bound when approving a prepared job')
                changes['approval_digest'] = approval_digest
            updated = _replace(job, **changes)
            if state == 'approved' and updated.approval_digest is None:
                raise ValueError('Approval digest required')
            self._save(db, updated)
            return updated

    def claim(self, job_id: str, expected_state: str, *, now: datetime | None = None) -> NativeJob | None:
        if expected_state not in {'approved', 'ready', 'waiting_window'}:
            return None
        now = _now(now)
        with self._db(write=True) as db:
            job = self._get(db, job_id)
            if job is None:
                return None
            payload_row = db.execute('SELECT payload FROM jobs WHERE id=?', (job.id,)).fetchone()
            delivery_mode = json.loads(payload_row[0]).get('delivery_mode', 'native_schedule')
            if delivery_mode not in {'native_schedule', 'at_time'}:
                raise ValueError('Unknown approved delivery mode')
            due = (job.publish_at <= now < job.publish_at + timedelta(minutes=5)
                   if job.route == 'api' and delivery_mode == 'at_time'
                   else job.dispatch_after <= now < job.publish_at)
            if (job.state != expected_state or job.approval_digest is None
                or job.attempt_id is not None or job.route == 'blocked' or not due):
                return None
            cooldown=db.execute('SELECT until_at FROM cooldowns WHERE job_id=?',(job.id,)).fetchone()
            if cooldown and datetime.fromisoformat(cooldown[0]) > now:
                return None
            self._check_external(db,job)
            attempt_id = str(uuid4())
            updated = _replace(job, state='dispatching', attempt_id=attempt_id, updated_at=now)
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                (attempt_id, job_id, job.approval_digest, now.isoformat(), now.isoformat(), '{}'))
            self._save(db, updated)
            return updated

    def resume_attempt(self, job_id: str, attempt_id: str, *, now: datetime | None = None) -> NativeJob | None:
        """Atomically give one worker the existing uncertain attempt, never a new one."""
        with self._db(write=True) as db:
            job = self._get(db, job_id)
            if job is None or job.state != 'uncertain' or job.attempt_id != attempt_id:
                return None
            row = db.execute('SELECT checkpoint FROM attempts WHERE id=? AND job_id=?',
                             (attempt_id, job_id)).fetchone()
            if row is None or not json.loads(row[0]).get('session_uri'):
                return None
            updated = _replace(job, state='dispatching', updated_at=_now(now))
            self._save(db, updated)
            return updated

    def mark_api_submitted(self, job_id: str, attempt_id: str, remote_id: str,
                           *, now: datetime | None = None) -> NativeJob:
        """Bind a remote ID to the owning attempt without claiming publication."""
        if not isinstance(remote_id, str) or not remote_id.strip():
            raise ValueError('Nonempty remote ID required')
        with self._db(write=True) as db:
            job = self._get(db, job_id)
            if job is None or job.attempt_id != attempt_id or job.state != 'dispatching':
                raise ValueError('Dispatching attempt does not own this job')
            updated = _replace(job, remote_id=remote_id, state='verifying',
                               updated_at=_now(now))
            row = db.execute('SELECT checkpoint FROM attempts WHERE id=? AND job_id=?',
                             (attempt_id, job_id)).fetchone()
            if row is None:
                raise ValueError('Attempt missing')
            checkpoint = json.loads(row[0]) | {'remote_id': remote_id, 'phase': 'submitted_id'}
            db.execute('UPDATE attempts SET checkpoint=?,updated_at=? WHERE id=?',
                       (json.dumps(checkpoint, allow_nan=False), _now(now).isoformat(), attempt_id))
            self._save(db, updated)
            return updated

    def checkpoint(self, job_id: str, attempt_id: str, checkpoint: dict, *, now: datetime | None = None):
        """Merge durable upload/session IDs before any next remote write."""
        with self._db(write=True) as db:
            job = self._get(db, job_id)
            if not job or job.attempt_id != attempt_id or job.state not in {'dispatching', 'awaiting_ui', 'verifying', 'uncertain'}:
                raise ValueError('Attempt does not own this job')
            row = db.execute('SELECT checkpoint FROM attempts WHERE id=? AND job_id=?',
                             (attempt_id, job_id)).fetchone()
            if not row:
                raise ValueError('Attempt missing')
            if checkpoint.get('ui_submit_started_at'):
                self._check_external(db,job)
            merged = json.loads(row[0]) | checkpoint
            db.execute('UPDATE attempts SET checkpoint=?,updated_at=? WHERE id=?',
                (json.dumps(merged, allow_nan=False), _now(now).isoformat(), attempt_id))

    def get_attempts(self, job_id: str) -> list[dict]:
        with self._db() as db:
            if db is None:
                return []
            return [dict(row) | {'checkpoint': json.loads(row['checkpoint'])} for row in
                    db.execute('SELECT * FROM attempts WHERE job_id=? ORDER BY started_at,id', (job_id,))]

    def recover_dispatching(self, *, now: datetime | None = None) -> list[str]:
        """Only after workers have stopped: require reconciliation, never resubmit."""
        recovered = []
        with self._db(write=True) as db:
            for row in db.execute('SELECT data FROM jobs').fetchall():
                job = NativeJob.model_validate_json(row[0])
                if job.state == 'dispatching':
                    self._save(db, _replace(job, state='uncertain', updated_at=_now(now),
                        reconcile_after=max(filter(None, [job.reconcile_after,
                            job.observation_watermark, _now(now)]))))
                    recovered.append(job.id)
        return recovered

    def record_observation(self, observation: NativeObservation, *, now: datetime | None = None) -> bool:
        """Append even conflicting observations; only complete evidence confirms a job."""
        now = _now(now)
        with self._db(write=True) as db:
            job = self._get(db, observation.job_id)
            if job is None:
                raise ValueError('Unknown job')
            pending=db.execute('SELECT data FROM actions WHERE job_id=?',(job.id,)).fetchone()
            if pending and json.loads(pending[0])['status'] != 'confirmed':
                raise ValueError('Pending action requires action-specific reconciliation')
            historical_valid = verify(job, observation, now=now)
            published = (observation.state == 'published' and observation.public_visible
                and observation.processing_complete and observation.publish_at == job.publish_at
                and evidence_matches(job,observation,now=now)
                and (observation.actual_published_at is None or observation.actual_published_at <= observation.observed_at))
            historical_valid = historical_valid or published
            payload_row = db.execute('SELECT payload FROM jobs WHERE id=?',
                                     (job.id,)).fetchone()
            payload = json.loads(payload_row[0])
            known_refs = {job.remote_id} if job.remote_id else set()
            payload_target = payload.get('options', {}).get('video_id')
            if payload_target:
                known_refs.add(payload_target)
            for row in db.execute('SELECT checkpoint FROM attempts WHERE job_id=?',
                                  (job.id,)).fetchall():
                checkpoint_target = json.loads(row[0]).get('video_id')
                if checkpoint_target:
                    known_refs.add(checkpoint_target)
            target_matches = not known_refs or known_refs == {observation.remote_ref}
            delivery_started = (job.approval_digest is not None
                                and job.attempt_id is not None
                                and job.state in {'dispatching', 'awaiting_ui', 'verifying',
                                                  'uncertain', 'native_scheduled'})
            current = (job.observation_watermark is None
                       or observation.observed_at >= job.observation_watermark)
            after_barrier = (job.reconcile_after is None
                             or observation.observed_at > job.reconcile_after)
            mutable = job.state not in {'cancelled', 'published'}
            calendar_conflict = self._external_occupied(db,job.platform,job.account_id,
                reservation_day(job.publish_at,job.timezone_name),
                remote_ref=observation.remote_ref,publish_at=observation.publish_at)
            valid = (historical_valid and target_matches and not calendar_conflict and delivery_started and current
                     and after_barrier and mutable)
            db.execute('INSERT INTO observations(job_id,data,verified) VALUES (?,?,?)',
                       (job.id, observation.model_dump_json(), int(valid)))
            if valid:
                self._save(db, _replace(job, state='published' if published else 'native_scheduled',
                    remote_id=observation.remote_ref, updated_at=now,
                    observation_watermark=observation.observed_at))
            elif (delivery_started and mutable and current
                  and (not historical_valid or not target_matches or calendar_conflict)
                  and evidence_matches(job, observation, now=now)
                  and (observation.state != 'native_scheduled'
                       or observation.publish_at != job.publish_at
                       or not target_matches or calendar_conflict
                       or (job.remote_id is not None and job.remote_id != observation.remote_ref))):
                # Only authentic, bound contradictions advance the barrier; an API
                # scheduled response lacking a calendar is merely incomplete.
                self._save(db, _replace(job, state='uncertain', updated_at=now,
                    observation_watermark=observation.observed_at,
                    reconcile_after=max(filter(None, [job.reconcile_after, observation.observed_at]))))
            return valid

    def get_observations(self, job_id: str) -> list[NativeObservation]:
        with self._db() as db:
            return [NativeObservation.model_validate_json(row[0]) for row in
                db.execute('SELECT data FROM observations WHERE job_id=? ORDER BY id', (job_id,))] if db else []

    def notice(self, job_id, message, *, now=None):
        with self._db(write=True) as db:
            db.execute('INSERT INTO notices VALUES (?,?,?)',(job_id,_now(now).isoformat(),message))

    def get_notices(self, job_id):
        with self._db() as db:
            if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='notices'").fetchone():
                return []
            return [dict(row) for row in db.execute('SELECT * FROM notices WHERE job_id=? ORDER BY created_at',(job_id,))]

    def reserved_days(self, platform, account_id):
        with self._db() as db:
            if db is None:
                return set()
            days = {row[0] for row in db.execute('SELECT day FROM jobs WHERE platform=? AND account_id=?',(platform,account_id))}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='holds'").fetchone():
                days |= {row[0] for row in db.execute('SELECT day FROM holds WHERE platform=? AND account_id=?',(platform,account_id))}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='external_reservations'").fetchone():
                days |= {row[0] for row in db.execute('SELECT day FROM external_reservations WHERE platform=? AND account_id=?',(platform,account_id))}
            return days

    def get_action(self, job_id):
        with self._db() as db:
            if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='actions'").fetchone():
                return None
            row=db.execute('SELECT data FROM actions WHERE job_id=?',(job_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_action(self, job, action):
        with self._db(write=True) as db:
            if self._get(db,job.id) != job:
                raise ValueError('Job changed; review a new preview')
            prior=db.execute('SELECT data FROM actions WHERE job_id=?',(job.id,)).fetchone()
            if prior and json.loads(prior[0])['status'] != 'confirmed':
                raise ValueError('An action already owns this job; reconcile it')
            if action['kind']=='reschedule':
                target=NativeJob.model_validate_json(action['job'])
                self._check_external(db,target)
                day=reservation_day(target.publish_at,target.timezone_name)
                if db.execute('SELECT 1 FROM jobs WHERE platform=? AND account_id=? AND day=? AND id<>?',(job.platform,job.account_id,day,job.id)).fetchone():
                    raise ValueError('Destination slot occupied')
                db.execute('INSERT INTO holds VALUES (?,?,?,?)',(job.id,job.platform,job.account_id,day))
            db.execute('INSERT OR REPLACE INTO actions VALUES (?,?)',(job.id,json.dumps(action)))
            db.execute('INSERT INTO action_events VALUES (?,?)',(job.id,json.dumps(action)))

    def submit_action(self, job_id, *, now=None):
        now=_now(now)
        with self._db(write=True) as db:
            job=self._get(db,job_id)
            row=db.execute('SELECT data FROM actions WHERE job_id=?',(job_id,)).fetchone()
            action=json.loads(row[0]) if row else None
            if not action or action['status'] != 'approved':
                raise ValueError('Approved action required; submitted actions cannot be retried')
            if job.model_dump(mode='json') != action['preview']['before']:
                raise ValueError('Job changed since action approval; reconcile before submission')
            if action['kind']=='reschedule' and NativeJob.model_validate_json(action['job']).publish_at <= now:
                raise ValueError('Destination date expired; no remote write authorized')
            if action['kind']=='reschedule':
                self._check_external(db,NativeJob.model_validate_json(action['job']))
            action.update(status='submitted',submitted_at=now.isoformat())
            db.execute('UPDATE actions SET data=? WHERE job_id=?',(json.dumps(action),job_id))
            db.execute('INSERT INTO action_events VALUES (?,?)',(job_id,json.dumps(action)))
            self._save(db,_replace(job,state='uncertain',updated_at=now,reconcile_after=max(filter(None,[now,job.reconcile_after,job.observation_watermark]))))
            return action

    def confirm_action(self, observation, *, now=None):
        now=_now(now)
        with self._db(write=True) as db:
            job=self._get(db,observation.job_id)
            row=db.execute('SELECT data FROM actions WHERE job_id=?',(job.id,)).fetchone()
            action=json.loads(row[0]) if row else None
            if not action or action['status'] != 'submitted':
                raise ValueError('Submitted action required')
            target=NativeJob.model_validate_json(action['job'])
            # Consensus and audit share the transaction: a conflicting target is
            # invalid evidence, not a reason to discard a newer contradiction.
            known_refs = {job.remote_id} if job.remote_id else set()
            payload = json.loads(db.execute('SELECT payload FROM jobs WHERE id=?',(job.id,)).fetchone()[0])
            payload_target = payload.get('options', {}).get('video_id')
            if payload_target:
                known_refs.add(payload_target)
            for attempt in db.execute('SELECT checkpoint FROM attempts WHERE job_id=?',(job.id,)):
                checkpoint_target = json.loads(attempt[0]).get('video_id')
                if checkpoint_target:
                    known_refs.add(checkpoint_target)
            target_matches = known_refs == {observation.remote_ref}
            valid=(evidence_matches(target,observation,now=now)
                and observation.source=='ui'
                and target_matches
                and observation.observed_at > datetime.fromisoformat(action['submitted_at'])
                and (job.observation_watermark is None or observation.observed_at >= job.observation_watermark)
                and (job.reconcile_after is None or observation.observed_at > job.reconcile_after)
                and observation.publish_at==target.publish_at
                and ((action['kind']=='cancel' and observation.state=='cancelled')
                     or (action['kind']=='reschedule' and verify(target,observation,now=now))))
            if action['kind']=='reschedule' and self._external_occupied(db,target.platform,target.account_id,reservation_day(target.publish_at,target.timezone_name),remote_ref=observation.remote_ref,publish_at=observation.publish_at):
                valid = False
            db.execute('INSERT INTO observations(job_id,data,verified) VALUES (?,?,?)',(job.id,observation.model_dump_json(),int(valid)))
            if not valid:
                if (evidence_matches(target,observation,now=now)
                    and observation.observed_at > datetime.fromisoformat(action['submitted_at'])
                    and (job.observation_watermark is None or observation.observed_at >= job.observation_watermark)):
                    self._save(db,_replace(job,state='uncertain',updated_at=now,observation_watermark=observation.observed_at,
                        reconcile_after=max(filter(None,[job.reconcile_after,observation.observed_at]))))
                db.execute('INSERT INTO notices VALUES (?,?,?)',(job.id,now.isoformat(),'Action readback mismatches approved intent; reservations retained'))
                return False
            updated=_replace(target,state='cancelled' if action['kind']=='cancel' else 'native_scheduled',
                remote_id=observation.remote_ref,updated_at=now,observation_watermark=observation.observed_at,reconcile_after=job.reconcile_after)
            if action['kind']=='reschedule':
                db.execute('UPDATE jobs SET day=? WHERE id=?',(reservation_day(target.publish_at,target.timezone_name),job.id))
            self._save(db,updated)
            db.execute('DELETE FROM holds WHERE job_id=?',(job.id,))
            action['status']='confirmed'
            db.execute('UPDATE actions SET data=? WHERE job_id=?',(json.dumps(action),job.id))
            db.execute('INSERT INTO action_events VALUES (?,?)',(job.id,json.dumps(action)))
            return True

    def defer_before_write(self, job_id, until, *, reason, now=None):
        """Backoff only before a delivery claim; cannot clear an uncertain attempt."""
        now=_now(now)
        until=aware(until)
        if reason not in {'rate_limit','upload_budget','session_expired','outside_window'} or until <= now:
            raise ValueError('Explicit pre-write rejection and future cooldown required')
        with self._db(write=True) as db:
            job=self._get(db,job_id)
            if not job or job.attempt_id or job.state not in {'approved','ready','waiting_window'}:
                raise ValueError('Claimed/submitted attempts require reconciliation, not backoff')
            db.execute('INSERT OR REPLACE INTO cooldowns VALUES (?,?,?)',(job_id,until.isoformat(),reason))
            db.execute('INSERT INTO notices VALUES (?,?,?)',(job_id,now.isoformat(),f'Pre-write {reason}; deferred until {until.isoformat()}'))

    def cooldown_until(self, job_id):
        with self._db() as db:
            if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE name='cooldowns'").fetchone():
                return None
            row=db.execute('SELECT until_at FROM cooldowns WHERE job_id=?',(job_id,)).fetchone()
            return datetime.fromisoformat(row[0]) if row else None
