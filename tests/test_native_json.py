"""Native lifecycle JSON preserves typed evidence and never grants approval."""
import json
import shlex
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.native_schedule.store import NativeStore
from tests.test_native_schedule_exact_cli import setup

runner=CliRunner()


def invoke(tmp_path,*args):
    return runner.invoke(app,['native-schedule',*args,'--brand','Example','--root',str(tmp_path),'--json'])


def test_exact_native_json_preview_approval_and_status_preserve_time(tmp_path):
    brand,_,_=setup(tmp_path)
    preview=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert preview.exit_code==0,preview.output
    payload=json.loads(preview.stdout)
    document=payload['data'][0]
    from datetime import datetime, timezone
    assert datetime.fromisoformat(document['intent']['publish_at'].replace('Z','+00:00'))==datetime(2035,1,3,18,30,tzinfo=timezone.utc)
    assert 'Approved caption' in preview.stderr and '19:30:00+01:00 Europe/Madrid' in preview.stderr
    assert not (brand/'native-schedules.sqlite3').exists()
    from socialctl.native_schedule.approval import hash_value
    digest=hash_value(document)
    saved=invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',digest)
    assert saved.exit_code==0,saved.output
    assert json.loads(saved.stdout)['jobs'][0]['state']=='prepared'
    rejected=invoke(tmp_path,'dispatch','clip','--yes')
    assert rejected.exit_code==1 and json.loads(rejected.stdout)['errors'][0]['retryable'] is False
    approved=invoke(tmp_path,'approve','clip','--approval-digest',digest)
    assert approved.exit_code==0,approved.output
    job=json.loads(approved.stdout)['jobs'][0]
    assert job['state']=='approved' and job['timezone_name']=='Europe/Madrid'
    status=invoke(tmp_path,'status')
    assert status.exit_code==0 and json.loads(status.stdout)['jobs'][0]['state']=='approved'


@pytest.mark.parametrize('operation',[['status'],['prepare','missing.json','--dry-run'],['approve','clip','--approval-digest','a'*64],['submit-api','clip']])
def test_missing_native_workspace_is_one_safe_document(tmp_path,operation):
    result=invoke(tmp_path,*operation)
    assert result.exit_code==1,result.output
    report=json.loads(result.stdout)
    assert report['status']=='error' and report['exit_code']==1
    assert report['errors'][0]['retryable'] is False


def native_remote(tmp_path):
    from socialctl.brands import crear_brand
    from tests.test_schedule_remote import marker
    brand=crear_brand(tmp_path,'Example')
    marker(brand,host='executor.example',user='agent',root='/srv/example',executable='/srv/example/.venv/bin/socialctl')


def envelope(command='status',code=0):
    return {'version':1,'kind':'native-schedule','command':command,'brand':'Example',
        'status':'ok' if code==0 else 'error','exit_code':code,'data':[],
        'jobs':[],'jobs_complete':True,'errors':[]}


@pytest.mark.parametrize('code',[0,1,130])
def test_remote_json_forwarding_is_single_bound_envelope(tmp_path,monkeypatch,code):
    native_remote(tmp_path)
    value=envelope(code=code)
    calls=[]
    def remote(argv,**kwargs):
        calls.append(shlex.split(argv[-1]))
        return subprocess.CompletedProcess(argv,code,json.dumps(value),'private diagnostic')
    monkeypatch.setattr(subprocess,'run',remote)
    result=invoke(tmp_path,'status')
    assert result.exit_code==code,result.output
    assert json.loads(result.stdout)==value
    assert len(calls)==1 and '--json' in calls[0]
    assert not (tmp_path/'Example'/'native-schedules.sqlite3').exists()
    assert 'private diagnostic' not in result.output


@pytest.mark.parametrize('failure',['legacy','wrong_brand','wrong_command','wrong_exit','secret'])
def test_unverified_remote_mutation_reply_is_never_replayed(tmp_path,monkeypatch,failure):
    native_remote(tmp_path)
    value=envelope('dispatch')
    if failure=='wrong_brand':value['brand']='Other'
    if failure=='wrong_command':value['command']='status'
    if failure=='wrong_exit':value['exit_code']=1
    if failure=='secret':value['data']=[{'access_token':'DO-NOT-PRINT'}]
    stdout='legacy result' if failure=='legacy' else json.dumps(value)
    calls=[]
    def remote(argv,**kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv,0,stdout,'')
    monkeypatch.setattr(subprocess,'run',remote)
    result=invoke(tmp_path,'dispatch','clip','--yes')
    assert result.exit_code==1 and len(calls)==1,result.output
    report=json.loads(result.stdout)
    assert report['errors'][0]['action']=='reconcile' and not report['errors'][0]['retryable']
    assert 'DO-NOT-PRINT' not in result.output


def approved_api(tmp_path):
    from datetime import datetime, timedelta, timezone
    from socialctl.native_schedule.approval import hash_value
    now=datetime.now(timezone.utc).replace(microsecond=0)
    brand,manifest,inventory=setup(tmp_path,when=(now+timedelta(days=2)).isoformat(),zone='UTC')
    manifest.update(route='api',dispatch_after=(now-timedelta(minutes=1)).isoformat())
    (brand/'native.json').write_text(json.dumps(manifest))
    inventory.update(coverage_start=(now-timedelta(days=1)).isoformat(),coverage_end=(now+timedelta(days=7)).isoformat())
    (brand/'native-calendar.json').write_text(json.dumps(inventory))
    preview=invoke(tmp_path,'prepare','native.json','--dry-run')
    digest=hash_value(json.loads(preview.stdout)['data'][0])
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',digest).exit_code==0
    assert invoke(tmp_path,'approve','clip','--approval-digest',digest).exit_code==0
    (brand/'pilot.txt').write_text('Fictional offline test evidence')
    (brand/'pilot.json').write_text(json.dumps(dict(version=1,platform='facebook',account_id='page-example',
        api_enabled=True,page_pilot_verified=True,evidence_ref='pilot.txt')))
    return brand


def test_native_json_without_pilot_requires_ui_not_uncertain_write(tmp_path,monkeypatch):
    import httpx
    brand=approved_api(tmp_path)
    monkeypatch.setattr(httpx.Client,'request',lambda *a,**kw:pytest.fail('no upload authorized'))
    result=invoke(tmp_path,'submit-api','clip')
    value=json.loads(result.stdout)
    assert result.exit_code==1
    assert value['data'][0]['state']=='ui_required'
    assert value['data'][0]['next_action']['action']=='user_action'
    assert all(row['code']!='operation_uncertain' for row in value['errors'])
    assert NativeStore(brand,brand='Example').get_attempts('clip')==[]


@pytest.mark.parametrize('failure',['timeout','interrupt'])
def test_native_json_lost_finish_keeps_checkpoint_without_reupload(tmp_path,monkeypatch,failure):
    import httpx
    from socialctl.native_schedule import facebook
    brand=approved_api(tmp_path)
    monkeypatch.setattr(facebook,'obtener_token',lambda *a:'fictional')
    calls=[]
    def request(client,method,url,**kwargs):
        calls.append((method,url))
        req=httpx.Request(method,url)
        if method=='GET':return httpx.Response(200,json={'id':'page-example'},request=req)
        if 'rupload.facebook.com' in url:return httpx.Response(200,json={'success':True},request=req)
        if kwargs['data']['upload_phase']=='START':
            return httpx.Response(200,json={'video_id':'video-123','upload_url':'https://rupload.facebook.com/video-upload/v26.0/video-123'},request=req)
        if failure=='interrupt':raise KeyboardInterrupt()
        raise httpx.ReadTimeout('access_token=DO-NOT-PRINT',request=req)
    monkeypatch.setattr(httpx.Client,'request',request)
    result=invoke(tmp_path,'submit-api','clip','--eligibility','pilot.json')
    assert result.exit_code==(130 if failure=='interrupt' else 1),result.output
    value=json.loads(result.stdout)
    assert any(row.get('remote_id')=='video-123' for row in value['data'])
    assert value['errors'][0]['action']=='reconcile'
    assert 'DO-NOT-PRINT' not in result.output
    count=len(calls)
    again=invoke(tmp_path,'submit-api','clip','--eligibility','pilot.json')
    assert again.exit_code==1 and len(calls)==count
    assert json.loads(again.stdout)['data'][0]['remote_id']=='video-123'


def test_native_json_snapshot_failure_preserves_result_and_prior_jobs(tmp_path,monkeypatch):
    from socialctl.native_schedule import facebook
    from socialctl.native_schedule.report import NativeReport
    approved_api(tmp_path)
    original=NativeReport.snapshot
    calls=[]
    def snapshot(self):
        calls.append(1)
        if len(calls)>1:raise OSError('private diagnostic')
        return original(self)
    monkeypatch.setattr(NativeReport,'snapshot',snapshot)
    monkeypatch.setattr(facebook,'schedule_facebook',lambda *a,**kw:facebook.FacebookScheduleResult('pending_verification','video-123','offline observation'))
    result=invoke(tmp_path,'submit-api','clip','--eligibility','pilot.json')
    value=json.loads(result.stdout)
    assert result.exit_code==1 and value['jobs_complete'] is False
    assert value['jobs'][0]['state']=='approved'
    assert value['data'][0]['remote_id']=='video-123'
    assert value['errors'][0]['code']=='reporting_incomplete'
    assert 'private diagnostic' not in result.output


@pytest.mark.parametrize('case',['foreign_approval','malformed_approval','foreign_job','invented_published'])
def test_remote_data_must_match_schema_and_requested_job(tmp_path,monkeypatch,case):
    native_remote(tmp_path)
    command='prepare' if 'approval' in case else 'reconcile'
    value=envelope(command)
    if 'approval' in case:
        value['data']=[{'schema':'socialctl.native-schedule.approval.v1',
            'intent':{'brand':'Other' if case=='foreign_approval' else 'Example','account_id':'other-account'},
            'payload':{'copy':'DO-NOT-DISPLAY'},'media':[]}]
        args=['prepare','native.json','--dry-run']
    else:
        value['data']=[{'job_id':'other-job' if case=='foreign_job' else 'clip','state':'published','observations':[],'notices':[],'instruction':'DO-NOT-DISPLAY'}]
        args=['reconcile','clip']
    calls=[]
    def remote(argv,**kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv,0,json.dumps(value),'')
    monkeypatch.setattr(subprocess,'run',remote)
    result=invoke(tmp_path,*args)
    assert result.exit_code==1 and len(calls)==1
    assert 'DO-NOT-DISPLAY' not in result.output
    assert json.loads(result.stdout)['data']==[]


@pytest.mark.parametrize('action_kind',['cancel','reschedule'])
def test_real_local_lifecycle_envelopes_pass_remote_validation(tmp_path,action_kind):
    from datetime import datetime, timedelta, timezone
    from socialctl.native_schedule.report import validate_remote
    from socialctl.native_schedule.approval import hash_value
    now=datetime.now(timezone.utc).replace(microsecond=0)
    brand,manifest,inventory=setup(tmp_path,when=(now+timedelta(days=2)).isoformat(),zone='UTC')
    manifest['dispatch_after']=(now-timedelta(minutes=1)).isoformat()
    (brand/'native.json').write_text(json.dumps(manifest))
    inventory.update(coverage_start=(now-timedelta(days=1)).isoformat(),coverage_end=(now+timedelta(days=7)).isoformat())
    (brand/'native-calendar.json').write_text(json.dumps(inventory))
    def checked(*args, expected_exit=0):
        result=invoke(tmp_path,*args)
        assert result.exit_code==expected_exit,result.output
        value=validate_remote(result.stdout,command=args[0],brand='Example',exit_code=expected_exit,request_args=args)
        assert value==json.loads(result.stdout)
        return value
    preview=checked('prepare','native.json','--dry-run')
    digest=hash_value(preview['data'][0])
    checked('prepare','native.json','--persist','--approval-digest',digest)
    checked('approve','clip','--approval-digest',digest)
    checked('dispatch','clip','--yes')
    checked('handoff','clip')
    checked('submit-marker','clip')
    checked('reconcile','clip')
    checked('status')
    delivery=checked('deliver',expected_exit=1)
    assert delivery['data'][0]['needs_attention']==['clip']
    assert delivery['jobs'][0]['state']=='uncertain'
    from hashlib import sha256
    from socialctl.native_schedule.models import NativeObservation
    job=NativeStore(brand,brand='Example').get('clip')
    evidence=brand/'calendar.txt';evidence.write_text('Fictional calendar evidence')
    observation=NativeObservation(job_id=job.id,platform=job.platform,account_id=job.account_id,
        content_hash=job.content_hash,approval_digest=job.approval_digest,remote_ref='remote-example',
        publish_at=job.publish_at,observed_at=datetime.now(timezone.utc),source='ui',calendar_visible=True,
        state='native_scheduled',evidence_path=str(evidence),evidence_sha256=sha256(evidence.read_bytes()).hexdigest())
    (brand/'observation.json').write_text(observation.model_dump_json())
    checked('reconcile','clip','--observation','observation.json')
    extra=['--publish-at',(now+timedelta(days=3)).isoformat()] if action_kind=='reschedule' else []
    action_preview=checked(action_kind,'clip',*extra,'--dry-run')
    checked(action_kind,'clip',*extra,'--approval-digest',action_preview['data'][0]['digest'])
    checked('handoff','clip')
    checked('submit-marker','clip')
    checked('handoff','clip')


def test_real_migration_envelopes_pass_remote_validation(tmp_path,monkeypatch):
    from tests.test_native_schedule_migration import setup as migration_setup
    from socialctl.native_schedule.report import validate_remote
    root,_,_,_=migration_setup(tmp_path,monkeypatch)
    def checked(*args):
        result=runner.invoke(app,['native-schedule',*args,'--brand',root.name,'--root',str(tmp_path),'--json'])
        assert result.exit_code==0,result.output
        return validate_remote(result.stdout,command=args[0],brand=root.name,exit_code=0,request_args=args)
    preview=checked('migration-prepare','--manifest','native-migration.json','--dry-run')
    digest=preview['data'][0]['digest']
    checked('migration-apply','--manifest','native-migration.json','--digest',digest)
    checked('migration-recover','--digest',digest)
