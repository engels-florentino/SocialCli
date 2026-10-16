import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from socialctl.brands import crear_brand

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
OLD = '2026-09-01T00:00:00+00:00'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def cleanup(brand, **kwargs):
    from socialctl.youtube_data import cleanup_youtube_data
    return cleanup_youtube_data(brand.raiz, now=NOW, **kwargs)


def test_cleanup_expires_youtube_only_and_preserves_supplied_editorial(tmp_path):
    brand = crear_brand(tmp_path, 'Fictional')
    snapshot = write(brand.raiz/'metricas'/'2026-09-01.json', {'fecha':'2026-09-01', 'redes':{'youtube':{'observed_at':OLD, 'piezas':[{'titulo':'private API title'}]}, 'facebook':{'piezas':[1]}}})
    pieces = brand.raiz/'metricas'/'piezas.yml'
    pieces.write_text(yaml.safe_dump([{'red':'youtube','id':'api-id','titulo':'API title','notas':'my original note','custom':'keep'}, {'red':'facebook','id':'fb','titulo':'Keep'}]))
    original = brand.raiz/'posts'/'original.mp4'; original.write_bytes(b'original')
    result = cleanup(brand)
    assert 'youtube' not in json.loads(snapshot.read_text())['redes']
    assert json.loads(snapshot.read_text())['redes']['facebook']['piezas'] == [1]
    rows = yaml.safe_load(pieces.read_text())
    assert rows[0]['notas'] == 'my original note' and rows[0]['custom'] == 'keep'
    assert 'titulo' not in rows[0] and 'id' not in rows[0]
    assert rows[1]['titulo'] == 'Keep'
    assert original.read_bytes() == b'original'
    assert result['complete']


def test_cleanup_purges_entire_old_inbox_history_even_when_last_seen_new(tmp_path):
    brand = crear_brand(tmp_path, 'Fictional')
    path = write(brand.raiz/'.socialctl/unified-inbox/state.json', {'version':1,'items':{'yt':{'platform':'youtube','first_observed_at':OLD,'last_observed_at':NOW.isoformat(),'text':'new','original_text':'old','history':[{'before':'old'}]},'fb':{'platform':'facebook','text':'keep'}},'watermarks':{json.dumps(['youtube','acct','media','published']):OLD}})
    cleanup(brand)
    state = json.loads(path.read_text())
    assert state['items'] == {'fb':{'platform':'facebook','text':'keep'}}
    assert state['watermarks'] == {}


def test_cleanup_keeps_studio_import_but_deletes_api_report_and_index_reference(tmp_path):
    brand = crear_brand(tmp_path, 'Fictional'); root=brand.raiz/'.socialctl/youtube-observations'
    write(root/'api.json', {'provenance':{'source':'youtubeReporting','observed_at':OLD},'rows':['private']})
    write(root/'studio.json', {'provenance':{'source':'youtubeStudio','observed_at':OLD},'rows':['original']})
    (root/'api.csv').write_text('api'); (root/'studio.csv').write_text('original')
    index=write(root/'index.json', {'imports':{'api':{},'studio':{}},'active':{'period':'api','other':'studio'}})
    cleanup(brand, purge=True)
    assert not (root/'api.csv').exists() and not (root/'api.json').exists()
    assert (root/'studio.csv').read_text()=='original'
    assert json.loads(index.read_text())['active']=={'other':'studio'}


def test_cleanup_tombstones_uncertain_journal_without_enabling_replay(tmp_path):
    brand=crear_brand(tmp_path,'Fictional')
    path=write(brand.raiz/'.socialctl/youtube-community/change.json',{'id':'change','created_at':OLD,'status':'uncertain','before':{'text':'private API'},'after':{'text':'private API'},'edit':{'text':'my supplied reply'},'fingerprint':'abc'})
    result=cleanup(brand)
    data=json.loads(path.read_text())
    assert data['data_lifecycle']=='purged'
    assert data['replay_blocked'] is True
    assert 'private API' not in path.read_text()
    assert data['user_input']['edit']['text']=='my supplied reply'
    assert result['manual_review']


def test_cleanup_fails_closed_for_symlink_and_does_not_touch_target(tmp_path):
    brand=crear_brand(tmp_path,'Fictional'); target=tmp_path/'external'; target.mkdir()
    (target/'data.json').write_text('private')
    (brand.raiz/'.socialctl').mkdir(); (brand.raiz/'.socialctl/youtube-community').symlink_to(target)
    result=cleanup(brand,purge=True)
    assert not result['complete']
    assert (target/'data.json').read_text()=='private'


def test_native_cleanup_blocks_upload_and_removes_provider_observations(tmp_path):
    import sqlite3
    brand=crear_brand(tmp_path,'Fictional'); path=brand.raiz/'native-schedules.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE jobs (id TEXT PRIMARY KEY, platform TEXT, data TEXT, payload TEXT); CREATE TABLE attempts (job_id TEXT, checkpoint TEXT); CREATE TABLE observations (job_id TEXT, data TEXT);')
        data={'id':'job','platform':'youtube','created_at':OLD,'state':'uncertain','remote_id':'API id','approval_digest':'digest','route':'api'}
        db.execute('INSERT INTO jobs VALUES (?,?,?,?)',('job','youtube',json.dumps(data),json.dumps({'copy':'supplied original'})))
        db.execute('INSERT INTO attempts VALUES (?,?)',('job',json.dumps({'remote_id':'API id'})))
        db.execute('INSERT INTO observations VALUES (?,?)',('job',json.dumps({'private':'API title'})))
    cleanup(brand)
    with sqlite3.connect(path) as db:
        data=json.loads(db.execute('SELECT data FROM jobs').fetchone()[0])
        assert data['state']=='blocked' and data['approval_digest'] is None and data['remote_id'] is None
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0]==0
        assert json.loads(db.execute('SELECT checkpoint FROM attempts').fetchone()[0])=={'data_lifecycle':'purged','replay_blocked':True}
        assert json.loads(db.execute('SELECT payload FROM jobs').fetchone()[0])['copy']=='supplied original'


def test_expiry_runs_before_loading_brand_and_explicit_purge_command(tmp_path):
    from socialctl.brands import cargar_brand
    from socialctl.cli import app
    from typer.testing import CliRunner
    brand=crear_brand(tmp_path,'Fictional')
    path=write(brand.raiz/'metricas'/'2026-01-01.json',{'fecha':'2026-01-01','redes':{'youtube':{'piezas':['API']}}})
    cargar_brand(tmp_path,'Fictional')
    assert 'youtube' not in json.loads(path.read_text())['redes']
    result=CliRunner().invoke(app,['data-cleanup','--brand','Fictional','--root',str(tmp_path),'--purge-youtube'])
    assert result.exit_code==0, result.output
    assert json.loads(result.stdout)['complete'] is True


def test_mixed_community_batch_retains_other_provider_evidence(tmp_path):
    brand=crear_brand(tmp_path,'Fictional')
    path=write(brand.raiz/'.socialctl/community-batches/batch.json',{'id':'batch','created_at':OLD,'children':[{'provider':'youtube-community','proposal':{'before':'private YouTube'}},{'provider':'meta-comments','proposal':{'before':'keep Meta'}}]})
    cleanup(brand)
    text=path.read_text()
    assert 'private YouTube' not in text
    assert 'keep Meta' in text


def test_purge_request_persists_until_failed_cleanup_is_resolved(tmp_path):
    from socialctl.youtube_data import request_youtube_purge
    brand=crear_brand(tmp_path,'Fictional'); directory=brand.raiz/'.socialctl/youtube-observations'
    directory.mkdir(parents=True); orphan=directory/'orphan.csv'; orphan.write_text('ambiguous original')
    result=request_youtube_purge(brand.raiz)
    assert not result['complete']
    assert (brand.raiz/'.socialctl/youtube-purge-required.json').exists()
    orphan.unlink()
    assert cleanup(brand)['complete']
    assert not (brand.raiz/'.socialctl/youtube-purge-required.json').exists()


def test_revoked_broker_token_removes_local_api_data_before_propagating_error(tmp_path, monkeypatch):
    from socialctl.connections import client
    from socialctl.connections.saas_client import ServiceRejected
    from socialctl.models import Platform
    brand=crear_brand(tmp_path,'Fictional')
    brand.cuentas['youtube']['channel_id']='channel'
    (brand.dir_secretos/'youtube.json').write_text(json.dumps({'auth_mode':'broker','version':1,'account_id':'channel','connection_id':'connection'}))
    path=write(brand.raiz/'metricas'/'2026-10-15.json',{'fecha':'2026-10-15','redes':{'youtube':{'piezas':['sensitive']}}})
    class Rejected:
        def request(self,*args,**kwargs):raise ServiceRejected('provider_authorization_expired','Expired',409)
    monkeypatch.setattr(client,'broker_for',lambda *args:Rejected())
    monkeypatch.setattr(client.keychain,'get',lambda *args:'secret-value-long-enough')
    with pytest.raises(ServiceRejected):client.broker_access_token(brand,Platform.YOUTUBE,object())
    assert 'youtube' not in json.loads(path.read_text())['redes']


def test_cleanup_sanitizes_legacy_schedules_and_managed_backup_without_erasing_meta(tmp_path):
    brand=crear_brand(tmp_path,'Fictional')
    rows=[{'platform':'youtube','created_at':OLD,'status':'running','platform_id':'private-remote','last_error':'private provider message','content_hash':'approval'}, {'platform':'facebook','platform_id':'keep'}]
    current=write(brand.raiz/'.socialctl/schedules.json',{'entries':rows})
    backup=write(brand.raiz/'.socialctl/native-migrations/old/backup/schedules.json',{'entries':rows})
    result=cleanup(brand)
    for path in (current,backup):
        data=json.loads(path.read_text())
        assert data['entries'][0]['status']=='blocked'
        assert data['entries'][0]['platform_id'] is None
        assert data['entries'][0]['content_hash'] is None
        assert data['entries'][1]['platform_id']=='keep'
        assert 'private provider' not in path.read_text()
    assert result['complete']


def test_direct_snapshot_read_removes_expired_api_data(tmp_path):
    from datetime import date
    from socialctl.metricas.almacen import cargar_snapshot
    brand=crear_brand(tmp_path,'Fictional')
    path=write(brand.raiz/'metricas/2020-01-01.json',{'fecha':'2020-01-01','marca':'Fictional','redes':{'youtube':{'estado':'ok','piezas':[]}}})
    snapshot=cargar_snapshot(brand,date(2020,1,1))
    assert not snapshot.redes
    assert 'youtube' not in json.loads(path.read_text())['redes']


def test_direct_inbox_read_refuses_expired_data_without_brand_reload(tmp_path):
    from socialctl.inbox import UnifiedInboxStore
    brand=crear_brand(tmp_path,'Fictional')
    write(brand.raiz/'.socialctl/unified-inbox/state.json',{'version':1,'items':{'yt':{'platform':'youtube','first_observed_at':'2020-01-01','text':'private'}},'watermarks':{}})
    with pytest.raises(ValueError,match='expired'):
        UnifiedInboxStore(brand.raiz).load()


def test_direct_report_cannot_expose_expired_snapshots(tmp_path):
    from datetime import date
    from socialctl.metricas.insights import build_report
    brand=crear_brand(tmp_path,'Fictional')
    path=write(brand.raiz/'metricas/2020-01-01.json',{'fecha':'2020-01-01','marca':'Fictional','redes':{'youtube':{'estado':'ok','piezas':[],'cuenta':{'seguidores':9876}}}})
    report=build_report(brand,as_of=date(2020,1,1))
    assert '9876' not in report.model_dump_json()
    assert 'youtube' not in json.loads(path.read_text())['redes']


def test_independent_google_invalid_grant_purges_local_api_data(tmp_path):
    import httpx
    from socialctl.auth import _refrescar, AuthError
    from socialctl.models import Platform
    brand=crear_brand(tmp_path,'Fictional')
    path=write(brand.raiz/'metricas/2026-10-15.json',{'fecha':'2026-10-15','redes':{'youtube':{'piezas':['private']}}})
    transport=httpx.MockTransport(lambda request:httpx.Response(400,json={'error':'invalid_grant'}))
    with httpx.Client(transport=transport) as client, pytest.raises(AuthError):
        _refrescar(brand,Platform.YOUTUBE,{'refresh_token':'fictional'},client)
    assert 'youtube' not in json.loads(path.read_text())['redes']


def test_history_cleanup_removes_multiline_provider_diagnostics(tmp_path):
    brand=crear_brand(tmp_path,'Fictional');path=brand.raiz/'historial.md'
    path.write_text('# History\n\n## episode\n- youtube: Failed\nprivate API detail\n- facebook: Keep me\n')
    cleanup(brand,purge=True)
    assert 'private API detail' not in path.read_text()
    assert '- facebook: Keep me' in path.read_text()


def test_native_expiry_preserves_supplied_future_approval_and_recent_remote_observation(tmp_path):
    import sqlite3
    brand=crear_brand(tmp_path,'Fictional');path=brand.raiz/'native-schedules.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE jobs (id TEXT PRIMARY KEY, platform TEXT, data TEXT, payload TEXT); CREATE TABLE attempts (job_id TEXT, started_at TEXT, checkpoint TEXT); CREATE TABLE observations (job_id TEXT, data TEXT);')
        for ident,remote,watermark in [('future',None,None),('active','remote-current',NOW.isoformat())]:
            data={'id':ident,'created_at':OLD,'state':'approved' if not remote else 'native_scheduled','route':'api','remote_id':remote,'approval_digest':'owner-approval','observation_watermark':watermark}
            db.execute('INSERT INTO jobs VALUES (?,?,?,?)',(ident,'youtube',json.dumps(data),'{}'))
    cleanup(brand)
    with sqlite3.connect(path) as db:
        rows={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM jobs')}
        assert rows['future']['state']=='approved'
        assert rows['future']['approval_digest']=='owner-approval'
        assert rows['active']['remote_id']=='remote-current'
        assert rows['active']['state']=='native_scheduled'


def test_native_refresh_does_not_extend_retention_of_old_checkpoints_or_actions(tmp_path):
    import sqlite3
    brand=crear_brand(tmp_path,'Fictional');path=brand.raiz/'native-schedules.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE jobs (id TEXT PRIMARY KEY, platform TEXT, data TEXT, payload TEXT); CREATE TABLE attempts (job_id TEXT, started_at TEXT, checkpoint TEXT); CREATE TABLE actions (job_id TEXT, data TEXT); CREATE TABLE action_events (job_id TEXT, data TEXT); CREATE TABLE notices (job_id TEXT, created_at TEXT, message TEXT);')
        data={'id':'active','created_at':OLD,'state':'native_scheduled','route':'api','remote_id':'current-remote','approval_digest':'approved','observation_watermark':NOW.isoformat()}
        db.execute('INSERT INTO jobs VALUES (?,?,?,?)',('active','youtube',json.dumps(data),'{}'))
        db.execute('INSERT INTO attempts VALUES (?,?,?)',('active',OLD,json.dumps({'diagnostic':'private old API'})))
        action=json.dumps({'created_at':OLD,'status':'submitted','preview':{'remote_ref':'private old API'}})
        db.execute('INSERT INTO actions VALUES (?,?)',('active',action));db.execute('INSERT INTO action_events VALUES (?,?)',('active',action))
        db.execute('INSERT INTO notices VALUES (?,?,?)',('active',OLD,'private old API'))
    cleanup(brand)
    with sqlite3.connect(path) as db:
        assert 'private old API' not in db.execute('SELECT checkpoint FROM attempts').fetchone()[0]
        action=json.loads(db.execute('SELECT data FROM actions').fetchone()[0]);assert action['replay_blocked'] is True
        assert db.execute('SELECT count(*) FROM action_events').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM notices').fetchone()[0]==0
        assert json.loads(db.execute('SELECT data FROM jobs').fetchone()[0])['remote_id']=='current-remote'


def test_legacy_expiry_preserves_unsent_user_schedule_intent_in_json_and_sqlite(tmp_path):
    import sqlite3
    brand=crear_brand(tmp_path,'Fictional')
    row={'id':'pending','platform':'youtube','created_at':OLD,'updated_at':OLD,
         'scheduled_at':'2027-01-01T12:00:00+00:00','status':'approved','attempts':0,
         'platform_id':None,'last_error':None,'content_hash':'owner-approved-original'}
    path=write(brand.raiz/'.socialctl/schedules.json',{'entries':[row]})
    database=brand.raiz/'.socialctl/schedules.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE entries (position INTEGER,payload TEXT)')
        db.execute('INSERT INTO entries VALUES(?,?)',(0,json.dumps(row)))
    assert cleanup(brand)['complete']
    assert json.loads(path.read_text())['entries']==[row]
    with sqlite3.connect(database) as db:assert json.loads(db.execute('SELECT payload FROM entries').fetchone()[0])==row
    cleanup(brand,purge=True)
    assert json.loads(path.read_text())['entries'][0]['status']=='blocked'


def test_legacy_nested_explicit_other_platform_is_not_scrubbed_as_youtube(tmp_path):
    brand=crear_brand(tmp_path,'Fictional')
    path=write(brand.raiz/'.socialctl/native-migrations/mixed/intent.json',{
        'platform':'youtube','created_at':OLD,'remote_id':'old-youtube',
        'other':{'platform':'facebook','remote_id':'keep-meta','status':'published','url':'https://example.test/meta'}})
    cleanup(brand,purge=True)
    data=json.loads(path.read_text())
    assert data['remote_id'] is None
    assert data['other']['remote_id']=='keep-meta' and data['other']['status']=='published'


def test_observation_cleanup_waits_for_import_lock_without_losing_studio_index(tmp_path):
    import uuid
    from socialctl.youtube_data import _lock
    brand=crear_brand(tmp_path,'Fictional');directory=brand.raiz/'.socialctl/youtube-observations'
    api=write(directory/'api.json',{'provenance':{'source':'youtubeReporting','observed_at':OLD}})
    (directory/'api.csv').write_text('provider bytes')
    studio=write(directory/'studio.json',{'provenance':{'source':'youtubeStudio','observed_at':OLD}})
    (directory/'studio.csv').write_text('supplied original')
    index=write(directory/'index.json',{'imports':{'api':{},'studio':{}},'active':{'api-period':'api','studio-period':'studio'}})
    lock=directory/(str(uuid.uuid5(uuid.NAMESPACE_URL,'youtube-observations-index'))+'.lock')
    with _lock(lock):
        result=cleanup(brand,purge=True)
    assert result['complete'] is False
    assert api.exists() and studio.exists()
    assert 'studio' in json.loads(index.read_text())['imports']
    assert cleanup(brand,purge=True)['complete'] is True
    assert not api.exists() and studio.exists()
    assert json.loads(index.read_text())['active']=={'studio-period':'studio'}


def test_cleanup_waits_for_short_inbox_write_and_preserves_fresh_data(tmp_path):
    import threading
    from socialctl.management.meta_comment_inbox import CommentInboxStore
    brand = crear_brand(tmp_path, 'Fictional')
    state = {'version':1, 'items':{'yt':{'platform':'youtube',
             'first_observed_at':NOW.isoformat(), 'text':'fresh'}}, 'watermarks':{}}
    path = write(brand.raiz/'.socialctl/unified-inbox/state.json', state)
    locked = threading.Event()

    def writer():
        with CommentInboxStore(brand.raiz).lock():
            locked.set()
            threading.Event().wait(0.15)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert locked.wait(2)
        result = cleanup(brand)
        assert result['complete'], result
        assert json.loads(path.read_text()) == state
    finally:
        thread.join(timeout=2)


def test_persistent_cleanup_lock_is_retryable_not_corrupt_and_retention_stays_blocked(tmp_path, monkeypatch):
    from socialctl import youtube_data
    from socialctl.agent_errors import error_document
    from socialctl.management.meta_comment_inbox import CommentInboxStore
    brand = crear_brand(tmp_path, 'Fictional')
    path = write(brand.raiz/'.socialctl/unified-inbox/state.json',
                 {'version':1, 'items':{'yt':{'platform':'youtube', 'first_observed_at':OLD}}, 'watermarks':{}})
    before = path.read_bytes()
    monkeypatch.setattr(youtube_data, 'LOCK_WAIT_SECONDS', 0.02, raising=False)
    with CommentInboxStore(brand.raiz).lock():
        result = cleanup(brand)
        assert result['complete'] is False
        assert result['errors'][0]['code'] == 'local_data_busy'
        with pytest.raises(youtube_data.YouTubeDataCleanupError) as caught:
            youtube_data.enforce_youtube_data(brand.raiz)
        assert 'another SocialCli operation' in str(caught.value)
        assert 'corrupt' not in str(caught.value)
        detail = error_document(caught.value, read_only=True)
        assert detail['code'] == 'local_data_busy' and detail['retryable'] is True
        assert path.read_bytes() == before
    assert cleanup(brand)['complete']
    assert json.loads(path.read_text())['items'] == {}


def test_json_read_preserves_cleanup_busy_action(tmp_path, monkeypatch, capsys):
    import typer
    from socialctl import youtube_data
    from socialctl.management.meta_comment_inbox import CommentInboxStore
    from socialctl.read_reports import load_read_brand
    brand = crear_brand(tmp_path, 'Fictional')
    write(brand.raiz/'.socialctl/unified-inbox/state.json', {'version':1,'items':{},'watermarks':{}})
    monkeypatch.setattr(youtube_data, 'LOCK_WAIT_SECONDS', 0.01)
    with CommentInboxStore(brand.raiz).lock(), pytest.raises(typer.Exit):
        load_read_brand(tmp_path, 'Fictional')
    report = json.loads(capsys.readouterr().out)
    assert report['errors'][0]['code'] == 'local_data_busy'
    assert report['errors'][0]['retryable'] is True
