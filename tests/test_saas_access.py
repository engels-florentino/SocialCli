"""Trial dates and entitlement are server-owned, atomic and non-renewable."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from socialctl.saas.access import AccessControl
from tests.test_saas_auth import service,login,USER


def setup(tmp_path):
    client,now,_=service(tmp_path);login(client)
    return client.app.state.saas,now


def test_simultaneous_first_activations_start_exactly_one_fourteen_day_trial(tmp_path):
    metadata,now=setup(tmp_path);access=AccessControl(metadata)
    def activate(_):
        with metadata.vault.transaction() as db:
            return access.activate_trial(db,USER,1000)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(activate,range(4)))==[(1000,1210600)]*4
    now[0]+=50000
    with metadata.vault.transaction() as db:
        assert access.activate_trial(db,USER,now[0])==(1000,1210600)
    assert access.access_for(USER,1210599).allowed
    end=access.access_for(USER,1210600)
    assert not end.allowed and end.reason=='trial_expired'


@pytest.mark.parametrize('status,paid_until,suspended,when,allowed,reason',[
    ('active',1300000,0,1210601,True,'paid'),
    ('canceled',1300000,0,1210601,True,'paid'),
    ('active',1300000,1,1100,False,'suspended'),
    ('past_due',1300000,0,1100,True,'trial'),
    ('past_due',1300000,0,1210601,False,'trial_expired'),
    ('trialing',1300000,0,1210601,False,'trial_expired'),
    ('paused',1300000,0,1210601,False,'trial_expired'),
    ('active',1300000,0,1300000,False,'trial_expired'),
])
def test_access_precedence_and_stripe_trial_never_extends_local_trial(tmp_path,status,paid_until,suspended,when,allowed,reason):
    metadata,_=setup(tmp_path);access=AccessControl(metadata)
    with metadata.vault.transaction() as db:
        access.activate_trial(db,USER,1000)
        db.execute('UPDATE saas_entitlements SET subscription_status=?,paid_until=? WHERE user_id=?',(status,paid_until,USER))
        db.execute('UPDATE saas_users SET suspended=? WHERE id=?',(suspended,USER))
    decision=access.access_for(USER,when)
    assert (decision.allowed,decision.reason)==(allowed,reason)


def test_deleting_and_recreating_identity_does_not_restart_trial_for_verified_email(tmp_path):
    from socialctl.saas.models import AuthenticatedUser
    metadata,_=setup(tmp_path);access=AccessControl(metadata)
    new_id='22222222-2222-4222-8222-222222222222'
    with metadata.vault.transaction() as db:
        access.activate_trial(db,USER,1000)
        db.execute('DELETE FROM saas_users WHERE id=?',(USER,))
        metadata.vault.delete(db,'saas-user',USER)
        metadata.ensure_user(db,AuthenticatedUser(new_id,'CREATOR@example.test'),9000)
        assert access.activate_trial(db,new_id,9000)==(1000,1210600)
    assert not access.access_for(new_id,1210600).allowed


def test_missing_user_cannot_activate_or_obtain_access(tmp_path):
    metadata,_=setup(tmp_path);access=AccessControl(metadata)
    assert not access.access_for('another-user',1000).allowed
    with pytest.raises(ValueError),metadata.vault.transaction() as db:
        access.activate_trial(db,'another-user',1000)


def test_recreated_identity_inherits_expired_trial_before_connection_activation(tmp_path):
    from socialctl.saas.models import AuthenticatedUser
    metadata,_=setup(tmp_path);access=AccessControl(metadata)
    new_id='22222222-2222-4222-8222-222222222222'
    with metadata.vault.transaction() as db:
        access.activate_trial(db,USER,1000)
        db.execute('DELETE FROM saas_users WHERE id=?',(USER,))
        metadata.vault.delete(db,'saas-user',USER)
        metadata.ensure_user(db,AuthenticatedUser(new_id,'CREATOR@example.test'),1210601)
    decision=access.access_for(new_id,1210601)
    assert not decision.allowed
    assert decision.reason=='trial_expired'
    assert decision.access_until==1210600


def test_verified_new_identity_does_not_start_trial_until_connection_activation(tmp_path):
    metadata,now=setup(tmp_path)
    assert AccessControl(metadata).access_for(USER,now[0]).reason=='trial_not_started'
