"""A configuration flip cannot convert sandbox access into live billing."""
from dataclasses import replace

import pytest

from socialctl.saas.billing import BillingService
from tests.test_saas_billing import setup, USER


def reopen(billing, settings):
    return BillingService(billing.metadata, settings, billing.client,
                          billing.clock, billing.origin)


@pytest.mark.parametrize('change', [
    {'mode': 'live', 'secret_key': 'sk_live_fictional'},
    {'account_id': 'acct_foreign'},
])
def test_database_rejects_billing_environment_change_without_network(tmp_path, change):
    _, _, metadata, stripe, billing = setup(tmp_path)
    with pytest.raises(ValueError, match='billing environment'):
        reopen(billing, replace(billing.settings, **change))
    assert stripe.calls == []
    # The original environment remains usable after the rejected startup.
    reopen(billing, billing.settings)


@pytest.mark.parametrize('legacy', ['billing', 'paid_access', 'foreign_event'])
def test_legacy_unbound_live_cutover_is_rejected_without_deleting_state(tmp_path, legacy):
    _, _, metadata, stripe, billing = setup(tmp_path)
    with metadata.vault.transaction() as db:
        metadata.vault.delete(db, 'saas-billing-environment', 'active')
        if legacy == 'billing':
            db.execute('INSERT INTO saas_billing(user_id,customer_key,customer_started) VALUES(?,?,?)',
                       (USER, 'original-intent', 1000))
        elif legacy == 'orphan_checkout_intent':
            metadata.vault.put(db, 'saas-checkout-intent', 'original-attempt', {'user_id': USER, 'deferred': {}}, expires=9000)
        elif legacy == 'paid_access':
            db.execute('INSERT INTO saas_entitlements(user_id,paid_until) VALUES(?,?)', (USER, 9000))
        else:
            db.execute("INSERT INTO saas_billing_events(account_id,mode,event_id,body_hash,type,received) VALUES(?,?,?,?,?,?)",
                       ('acct_foreign', 'live', 'evt_original', 'hash', 'customer.updated', 1000))
    live = replace(billing.settings, mode='live', secret_key='sk_live_fictional')
    with pytest.raises(ValueError, match='billing environment'):
        reopen(billing, live)
    assert stripe.calls == []
    with metadata.vault.transaction() as db:
        table = {'billing': 'saas_billing', 'paid_access': 'saas_entitlements',
                 'foreign_event': 'saas_billing_events'}[legacy]
        assert db.execute('SELECT count(*) FROM '+table).fetchone()[0] == 1
        assert metadata.vault.get(db, 'saas-billing-environment', 'active') is None


def test_unbound_empty_database_can_bind_live_and_reject_test_restart(tmp_path):
    _, _, metadata, stripe, billing = setup(tmp_path)
    with metadata.vault.transaction() as db:
        metadata.vault.delete(db, 'saas-billing-environment', 'active')
    live = replace(billing.settings, mode='live', secret_key='sk_live_fictional')
    reopened = reopen(billing, live)
    reopen(reopened, live)
    with pytest.raises(ValueError, match='billing environment'):
        reopen(reopened, billing.settings)
    assert stripe.calls == []


@pytest.mark.parametrize('legacy', ['customer_intent', 'customer', 'checkout_intent', 'checkout_session', 'paid_access', 'same_account_event', 'orphan_checkout_intent'])
def test_unbound_legacy_test_state_never_guesses_account_without_enrollment(tmp_path, legacy):
    _, _, metadata, stripe, billing = setup(tmp_path)
    with metadata.vault.transaction() as db:
        metadata.vault.delete(db, 'saas-billing-environment', 'active')
        if legacy == 'orphan_checkout_intent':
            metadata.vault.put(db, 'saas-checkout-intent', 'original-attempt', {'user_id': USER, 'deferred': {}}, expires=9000)
        elif legacy == 'paid_access':
            db.execute('INSERT INTO saas_entitlements(user_id,paid_until) VALUES(?,?)', (USER, 9000))
        elif legacy == 'same_account_event':
            db.execute('INSERT INTO saas_billing_events(account_id,mode,event_id,body_hash,type,received) VALUES(?,?,?,?,?,?)',
                       (billing.settings.account_id, 'test', 'evt_original', 'hash', 'customer.updated', 1000))
        else:
            db.execute('INSERT INTO saas_billing(user_id,customer_key,customer_started,customer_id) VALUES(?,?,?,?)',
                       (USER, 'original-uncertain-intent', 1000, 'cus_original' if legacy != 'customer_intent' else ''))
            if legacy.startswith('checkout'):
                db.execute('UPDATE saas_billing SET attempt=?,checkout_id=? WHERE user_id=?',
                           ('original-checkout-intent', 'cs_original' if legacy == 'checkout_session' else '', USER))
        before = {table: [tuple(row) for row in db.execute('SELECT * FROM '+table)]
                  for table in ('saas_billing', 'saas_billing_events', 'saas_entitlements')}
    for settings in (replace(billing.settings, account_id='acct_other_test'), billing.settings):
        with pytest.raises(ValueError, match='billing environment'):
            reopen(billing, settings)
        with metadata.vault.transaction() as db:
            assert metadata.vault.get(db, 'saas-billing-environment', 'active') is None
            if legacy == 'orphan_checkout_intent':
                assert metadata.vault.get(db, 'saas-checkout-intent', 'original-attempt')['payload'] == {'user_id': USER, 'deferred': {}}
            assert {table: [tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in before} == before
    assert stripe.calls == []
