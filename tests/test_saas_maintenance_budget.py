"""Maintenance stops scheduling work without losing durable external outcomes."""
import httpx
import pytest
from fastapi import HTTPException
from tests.test_saas_auth import USER


def budget(seconds=1):
    from socialctl.saas.budget import WorkBudget
    monotonic=[0.0]
    return WorkBudget(seconds,clock=lambda:monotonic[0]),monotonic


def test_budget_prevents_second_stripe_request_and_caps_first_timeout(tmp_path):
    from tests.test_saas_billing import setup
    _,_,_,_,billing=setup(tmp_path)
    billing.budget,clock=budget(2);calls=[]
    def api(request):
        calls.append(request)
        assert request.extensions['timeout']['read']<=2
        clock[0]=3
        return httpx.Response(200,json={'id':'acct_social'})
    billing.client=httpx.Client(transport=httpx.MockTransport(api))
    # A read arriving after the deadline may be discarded; no subsequent call starts.
    try:billing.api('GET','/account')
    except HTTPException:pass
    with pytest.raises(HTTPException):billing.api('GET','/account')
    assert len(calls)==1


def test_budget_exhaustion_leaves_billing_work_due_and_reports_it(tmp_path):
    from tests.test_saas_billing import setup,expire
    from socialctl.saas.billing_reconcile import run_pending
    _,now,metadata,stripe,billing=setup(tmp_path);expire(metadata,now);billing.checkout(USER)
    work,clock=budget();clock[0]=2
    before=len(stripe.calls)
    result=run_pending(billing,budget=work)
    assert len(stripe.calls)==before
    assert result['due']>=1 and result['budget_exhausted'] is True
    with metadata.vault.transaction() as db:
        assert db.execute('SELECT sync_due FROM saas_billing').fetchone()[0]<=now[0]


def test_expired_deletion_budget_does_not_claim_or_call_identity(tmp_path):
    from tests.test_saas_auth import service,login
    from socialctl.saas.lifecycle import AccountLifecycle,run_deletions
    client,now,calls=service(tmp_path);login(client);metadata=client.app.state.saas
    AccountLifecycle(metadata,lambda:now[0]).request_delete(USER)
    work,clock=budget();clock[0]=2
    before=len(calls)
    result=run_deletions(metadata,client.app.state.saas_sessions.identity,None,lambda:now[0],budget=work)
    assert result['due']==1 and result['budget_exhausted'] is True
    assert len(calls)==before
    with metadata.vault.transaction() as db:assert db.execute('SELECT lease FROM saas_deletions').fetchone()[0] is None


def test_reminder_budget_expired_before_fence_cannot_send_body(tmp_path):
    from tests.test_saas_reminders import prepared,Mailbox,states
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-86400
    work,clock=budget()
    class SlowPreparation(Mailbox):
        def send(self,recipient,message,before_send):
            clock[0]=2
            return super().send(recipient,message,before_send)
    sender=SlowPreparation()
    result=reminders.run(sender,budget=work)
    assert sender.messages==[] and result['budget_exhausted'] is True
    assert all(row['state']!='sending' for row in states(metadata))


def test_reminder_backlog_is_counted_even_when_budget_already_expired(tmp_path):
    from tests.test_saas_reminders import prepared,Mailbox
    reminders,metadata,now,end=prepared(tmp_path);now[0]=end-86400
    work,clock=budget();clock[0]=2;sender=Mailbox()
    result=reminders.run(sender,budget=work)
    assert result['due']==1 and not sender.messages


def test_identity_budget_blocks_next_delete_and_preserves_completed_ack(tmp_path):
    from tests.test_saas_auth import service
    from socialctl.saas.auth import IdentityUnavailable
    client,_,_=service(tmp_path);identity=client.app.state.saas_sessions.identity
    from dataclasses import replace
    identity.settings=replace(identity.settings,auth_admin_key="fictional-admin-key")
    identity.budget,clock=budget(2);calls=[]
    def api(request):
        calls.append(request)
        assert request.extensions['timeout']['read']<=2
        clock[0]=3
        return httpx.Response(204)
    identity.client=httpx.Client(transport=httpx.MockTransport(api))
    identity.delete_user(USER)
    with pytest.raises(IdentityUnavailable):identity.delete_user(USER)
    assert len(calls)==1


def test_smtp_budget_stops_after_slow_tls_before_any_body(monkeypatch):
    from tests.test_saas_mail import Server,mailer
    server=Server();sender,message=mailer(monkeypatch,server)
    work,clock=budget(2);sender.budget=work;timeouts=[]
    class Socket:
        def settimeout(self,value):timeouts.append(value)
    server.sock=Socket()
    tls=server.starttls
    def slow_tls(**kwargs):tls(**kwargs);clock[0]=3
    server.starttls=slow_tls
    assert sender.send('fixture@example.test',message,lambda:True)=='retry'
    assert timeouts and max(timeouts)<=2
    assert server.events==['ehlo','tls','close']


def test_smtp_budget_keeps_success_received_after_data_deadline(monkeypatch):
    from tests.test_saas_mail import Server,mailer
    server=Server();sender,message=mailer(monkeypatch,server)
    sender.budget,clock=budget(2)
    class Socket:
        def settimeout(self,value):pass
    server.sock=Socket();data=server.data
    def slow_data(raw):clock[0]=3;return data(raw)
    server.data=slow_data
    assert sender.send('fixture@example.test',message,lambda:True)=='accepted'
