"""Controlled-clock transactional reminder outbox; no real email deliveries."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from tests.test_saas_access import setup
from tests.test_saas_auth import USER,EMAIL
from socialctl.saas.access import AccessControl


def prepared(tmp_path):
    from socialctl.saas.reminders import Reminders
    metadata,now=setup(tmp_path)
    with metadata.vault.transaction() as db:
        _,end=AccessControl(metadata).activate_trial(db,USER,now[0])
    return Reminders(metadata,lambda:now[0],'https://social.example'),metadata,now,end


class Mailbox:
    def __init__(self): self.messages=[]
    def send(self,recipient,message,before_send):
        if not before_send(): return 'cancelled'
        self.messages.append((recipient,message))
        return 'accepted'


def states(metadata):
    with metadata.vault.transaction() as db:
        return [dict(row) for row in db.execute('SELECT * FROM saas_reminders ORDER BY milestone DESC')]


def test_exact_milestones_are_once_each_and_match_trial_deadline(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);mailbox=Mailbox()
    assert reminders.run(mailbox)['accepted']==0
    now[0]=end-3*86400-1;reminders.run(mailbox);assert not mailbox.messages
    now[0]+=1
    assert reminders.run(mailbox)['accepted']==1
    assert reminders.run(mailbox)['accepted']==0
    now[0]=end-86400
    assert reminders.run(mailbox)['accepted']==1
    assert len(mailbox.messages)==2
    assert all(recipient==EMAIL for recipient,_ in mailbox.messages)
    from socialctl.saas.pages import utc_date
    for _,message in mailbox.messages:
        assert utc_date(end) in message.get_body(preferencelist=('plain',)).get_content()
        assert 'USD 10/month' in str(message)
        assert 'https://social.example/account/' in str(message)
    assert len({message['Message-ID'] for _,message in mailbox.messages})==2
    now[0]=end;reminders.run(mailbox);assert len(mailbox.messages)==2


def test_delayed_maintenance_skips_obsolete_milestone_and_expired_trials(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);mailbox=Mailbox()
    now[0]=end-100
    assert reminders.run(mailbox)['accepted']==1
    assert {r['state'] for r in states(metadata)}=={'accepted','cancelled'}
    now[0]=end;assert reminders.run(mailbox)['accepted']==0


def test_payment_suspension_or_deletion_before_data_cancels_delivery(tmp_path):
    from socialctl.saas.lifecycle import AccountLifecycle
    for change in ('paid','suspended','deleted'):
        reminders,metadata,now,end=prepared(tmp_path/change);now[0]=end-3*86400
        class Changed(Mailbox):
            def send(self,recipient,message,before_send):
                if change=='deleted': AccountLifecycle(metadata,lambda:now[0]).request_delete(USER)
                else:
                    with metadata.vault.transaction() as db:
                        if change=='paid': db.execute("UPDATE saas_entitlements SET subscription_status='active',paid_until=? WHERE user_id=?",(end+86400,USER))
                        else: db.execute('UPDATE saas_users SET suspended=1 WHERE id=?',(USER,))
                return super().send(recipient,message,before_send)
        mailbox=Changed();assert reminders.run(mailbox)['accepted']==0
        assert mailbox.messages==[]


def test_safe_failure_retries_with_same_message_id_after_backoff(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-3*86400
    ids=[]
    class Retry(Mailbox):
        def send(self,recipient,message,before_send):
            ids.append(message['Message-ID'])
            if len(ids)==1:return 'retry'
            return super().send(recipient,message,before_send)
    mailbox=Retry();reminders.run(mailbox)
    assert reminders.run(mailbox)['accepted']==0 and len(ids)==1
    now[0]+=301;assert reminders.run(mailbox)['accepted']==1
    assert ids[0]==ids[1]


def test_lost_ack_never_retries_and_does_not_hide_backlog(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-3*86400
    class Lost(Mailbox):
        def send(self,recipient,message,before_send):
            assert before_send()
            self.messages.append((recipient,message))
            raise OSError('private SMTP detail')
    mailbox=Lost()
    assert reminders.run(mailbox)['uncertain']==1
    now[0]+=301;assert reminders.run(mailbox)['uncertain']==1
    assert len(mailbox.messages)==1


def test_two_workers_cannot_send_same_job(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-3*86400
    entered,release=Event(),Event()
    class Waiting(Mailbox):
        def send(self,recipient,message,before_send):
            entered.set();assert release.wait(5)
            return super().send(recipient,message,before_send)
    mailbox=Waiting()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(reminders.run,mailbox)
        try:
            assert entered.wait(5)
            assert reminders.run(mailbox)['accepted']==0
        finally:release.set()
        assert first.result()['accepted']==1
    assert len(mailbox.messages)==1


def test_stale_preparation_is_reclaimable_but_stale_send_is_not(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);mailbox=Mailbox()
    reminders.run(mailbox);now[0]=end-3*86400
    with metadata.vault.transaction() as db:
        db.execute("UPDATE saas_reminders SET state='preparing',lease='dead-worker',lease_until=? WHERE milestone=3",(now[0]-1,))
    assert reminders.run(mailbox)['accepted']==1
    now[0]=end-86400
    with metadata.vault.transaction() as db:
        db.execute("UPDATE saas_reminders SET state='sending',lease='dead-worker',lease_until=? WHERE milestone=1",(now[0]-1,))
    assert reminders.run(mailbox)['uncertain']==1 and len(mailbox.messages)==1


def test_plain_sql_outbox_contains_no_email_or_message_body(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-3*86400
    reminders.run(Mailbox())
    assert EMAIL not in str(states(metadata))
    assert 'USD' not in str(states(metadata))


def test_backoff_is_reported_as_pending_even_before_next_attempt(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-3*86400
    class Offline:
        def send(self,*args):return 'retry'
    assert reminders.run(Offline())['retry_pending']==1
    assert reminders.run(Offline())['retry_pending']==1


def test_unstarted_trial_never_creates_reminder_and_schema_upgrade_preserves_identity(tmp_path):
    from socialctl.saas.reminders import Reminders
    from socialctl.saas.store import SaaSStore
    metadata,now=setup(tmp_path)
    with metadata.vault.transaction() as db:
        db.execute('DROP TABLE saas_reminders');db.execute('DELETE FROM saas_schema WHERE version>=6')
        db.execute('ALTER TABLE saas_rates DROP COLUMN expires')
        db.execute('ALTER TABLE saas_billing_events DROP COLUMN semantic_hash')
    upgraded=SaaSStore(metadata.vault,metadata.eligibility_keys)
    assert Reminders(upgraded,lambda:now[0],'https://social.example').run(Mailbox())['accepted']==0
    assert states(upgraded)==[]
    with upgraded.vault.transaction() as db:
        assert upgraded.vault.get(db,'saas-user',USER)['payload']['email']==EMAIL


def test_disabled_sending_keeps_uncertainty_visible_and_expires_receipts(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path)
    reminders.run(Mailbox())
    with metadata.vault.transaction() as db:
        db.execute("UPDATE saas_reminders SET state='uncertain' WHERE milestone=3")
    now[0]=end-86400
    result=reminders.run(None)
    assert result['disabled'] is True and result['uncertain']==1
    assert not any(row['attempts'] for row in states(metadata))
    now[0]=end+31*86400
    reminders.run(None)
    assert states(metadata)==[]


def test_stale_worker_snapshot_cannot_bypass_retry_backoff(tmp_path):
    from socialctl.saas.models import AuthenticatedUser
    reminders,metadata,now,end=prepared(tmp_path)
    other='22222222-2222-4222-8222-222222222222'
    with metadata.vault.transaction() as db:
        metadata.ensure_user(db,AuthenticatedUser(other,'second@example.test'),now[0])
        AccessControl(metadata).activate_trial(db,other,now[0])
    reminders.prepare()
    with metadata.vault.transaction() as db:
        db.execute("UPDATE saas_reminders SET id='aaaaaaaaaaaaaaaa' WHERE user_id=? AND milestone=3",(USER,))
        db.execute("UPDATE saas_reminders SET id='bbbbbbbbbbbbbbbb' WHERE user_id=? AND milestone=3",(other,))
    now[0]=end-3*86400
    entered,release=Event(),Event();attempts=[]
    class SlowFirst(Mailbox):
        def send(self,recipient,message,before_send):
            if recipient==EMAIL:
                entered.set();assert release.wait(5)
                return super().send(recipient,message,before_send)
            attempts.append(recipient)
            return 'retry'
    mailbox=SlowFirst()
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker=pool.submit(reminders.run,mailbox)
        try:
            assert entered.wait(5)
            reminders.run(mailbox)
        finally:release.set()
        worker.result()
    assert attempts==['second@example.test']


def test_maintenance_reports_disabled_sender_backlog_then_prunes_receipts(tmp_path,monkeypatch,capsys):
    import json
    import pytest
    from socialctl.saas import maintenance
    from socialctl.saas.settings import SaaSSettings
    from socialctl.connection_service.settings import Settings
    reminders,metadata,now,end=prepared(tmp_path);reminders.prepare()
    with metadata.vault.transaction() as db:
        db.execute("UPDATE saas_reminders SET state='uncertain' WHERE milestone=3")
    settings=Settings(public_url='https://social.example',database=metadata.vault.path,
        encryption_keys=metadata.eligibility_keys,providers={},saas=SaaSSettings(auth_url='https://auth.example'))
    monkeypatch.setattr(maintenance.Settings,'from_environment',lambda:settings)
    monkeypatch.setattr(maintenance.time,'time',lambda:now[0])
    monkeypatch.setattr(maintenance.sys,'argv',['maintenance'])
    with pytest.raises(SystemExit) as error:maintenance.main()
    assert error.value.code==1
    result=json.loads(capsys.readouterr().out)
    assert result['reminders']['disabled'] is True and result['reminders']['uncertain']==1
    now[0]=end+31*86400;maintenance.main()
    assert json.loads(capsys.readouterr().out)['reminders']['uncertain']==0
    assert states(metadata)==[]


def test_early_subscription_suppresses_trial_purchase_reminder(tmp_path):
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-3*86400
    with metadata.vault.transaction() as db:
        db.execute("UPDATE saas_entitlements SET subscription_status='active',paid_until=0 WHERE user_id=?",(USER,))
    mailbox=Mailbox()
    assert reminders.run(mailbox)['accepted']==0
    assert not mailbox.messages
