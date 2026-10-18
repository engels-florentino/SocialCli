"""A frozen exporter or healthy web app must not hide a missing remote backup."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_saas_backup_alerts import load, setup


def health_module():
    path=Path(__file__).resolve().parents[1]/'deploy/saas/backup_health.py'
    spec=importlib.util.spec_from_file_location('backup_health',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_export_requires_remote_completion_and_omits_private_details(tmp_path):
    _,alerts=load();config=setup(tmp_path)
    assert alerts.health_snapshot(config,service_result='success',now=lambda:1200)=={
        'check':'backup','evaluated_at':1200,'completed_at':None,'healthy':False}
    alerts.remote_complete(config,now=lambda:1100)
    value=alerts.health_snapshot(config,service_result='success',now=lambda:1200)
    assert value=={'check':'backup','evaluated_at':1200,'completed_at':1000,'healthy':True}
    assert not alerts.health_snapshot(config,service_result='failed',now=lambda:1200)['healthy']
    assert not alerts.health_snapshot(config,service_result='success',now=lambda:9000)['healthy']


@pytest.mark.parametrize('change',[
    {'evaluated_at':1}, {'completed_at':1}, {'evaluated_at':10001},
    {'completed_at':10001}, {'healthy':False}, {'healthy':1},
    {'evaluated_at':float('nan')}, {'completed_at':True},
    {'check':'other'}, {'secret':'must not be exposed'},
])
def test_reader_fails_closed_without_exposing_receipt(tmp_path,change):
    module=health_module();path=tmp_path/'backup.json'
    value={'check':'backup','evaluated_at':9900,'completed_at':9800,'healthy':True}
    path.write_text(json.dumps(value|change))
    assert module.response(path,now=lambda:10000)==(503,b'backup-unavailable\n')


def test_reader_evaluates_freshness_on_every_request_and_recovers(tmp_path):
    module=health_module();path=tmp_path/'backup.json'
    assert module.response(path,now=lambda:10000)==(503,b'backup-unavailable\n')
    value={'check':'backup','evaluated_at':9900,'completed_at':9800,'healthy':True}
    path.write_text(json.dumps(value))
    assert module.response(path,now=lambda:10000)==(200,b'backup-current\n')
    assert module.response(path,now=lambda:10601)==(503,b'backup-unavailable\n')
    path.write_text(json.dumps(value|{'evaluated_at':10600}))
    assert module.response(path,now=lambda:10601)==(200,b'backup-current\n')
    path.write_text('x'*4097)
    assert module.response(path,now=lambda:10601)==(503,b'backup-unavailable\n')


def test_export_is_atomic_minimal_and_readable_without_private_access(tmp_path):
    _,alerts=load();private_dir=tmp_path/'private';private_dir.mkdir()
    config=setup(private_dir);alerts.remote_complete(config,now=lambda:1100)
    output=tmp_path/'public';output.mkdir(mode=0o755)
    alerts.export_health(config,output,service_result='success',now=lambda:1200)
    path=output/'backup.json'
    assert path.stat().st_mode & 0o777==0o644
    assert json.loads(path.read_text())['healthy'] is True
    assert set(output.iterdir())=={path}
    assert str(private_dir) not in path.read_text()


def test_http_is_read_only_uncached_and_never_serves_other_files(tmp_path):
    from http.client import HTTPConnection
    from http.server import HTTPServer
    import threading
    import time
    module=health_module();path=tmp_path/'backup.json'
    stamp=time.time()-1
    path.write_text(json.dumps({'check':'backup','evaluated_at':stamp,'completed_at':stamp,'healthy':True}))
    original=path.read_bytes()
    server=HTTPServer(('127.0.0.1',0),module.handler(path))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        for method,route,status in [('GET','/monitoring/backup',200),('GET','/backup.json',404),
                                    ('GET','/monitoring/backup?path=private',404),('POST','/monitoring/backup',501)]:
            connection=HTTPConnection(*server.server_address,timeout=3)
            connection.request(method,route);result=connection.getresponse()
            assert result.status==status
            body=result.read()
            if status==200:
                assert result.getheader('Cache-Control')=='no-store'
                assert body==b'backup-current\n'
            assert b'completed_at' not in body
            connection.close()
        assert path.read_bytes()==original
        path.unlink();path.symlink_to(tmp_path/'missing')
        assert module.response(path)==(503,b'backup-unavailable\n')
    finally:
        server.shutdown();thread.join();server.server_close()
