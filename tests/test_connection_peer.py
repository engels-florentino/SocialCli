from types import SimpleNamespace
from starlette.requests import Request


def test_only_trusted_proxy_can_supply_real_ip():
    from socialctl.connection_service.peer import client_ip
    settings=SimpleNamespace(trusted_proxy_cidrs=('172.30.5.0/24',))
    def request(peer,header):
        return Request({'type':'http','client':(peer,1234),'headers':[(b'x-real-ip',header.encode())]})
    assert client_ip(request('172.30.5.2','203.0.113.7'),settings)=='203.0.113.7'
    assert client_ip(request('198.51.100.2','203.0.113.7'),settings)=='198.51.100.2'
    assert client_ip(request('172.30.5.2','203.0.113.7, 192.0.2.1'),settings)=='172.30.5.2'
    assert client_ip(request('172.30.5.2','2001:db8::1'),settings)=='2001:db8::1'


def test_old_rate_buckets_are_pruned_for_different_windows(tmp_path):
    from tests.test_saas_lifecycle import connected
    client,now,_,_=connected(tmp_path);metadata=client.app.state.saas
    with metadata.vault.transaction() as db:
        metadata.admit(db,'short',now[0],limit=3)
        metadata.admit(db,'long',now[0],limit=3,seconds=86400)
    now[0]+=120
    client.app.state.saas_lifecycle.prune_expired()
    import hashlib
    with metadata.vault.read_transaction() as db:
        assert db.execute('SELECT count(*) FROM saas_rates WHERE key=?',(hashlib.sha256(b'short').hexdigest(),)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM saas_rates WHERE key=?',(hashlib.sha256(b'long').hexdigest(),)).fetchone()[0]==1
