import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.native_schedule.approval import native_digest
from socialctl.native_schedule.models import NativeJob
from socialctl.native_schedule.store import NativeStore

runner = CliRunner()


def fixture(tmp_path):
    brand = tmp_path / 'Histopast'
    brand.mkdir()
    (brand / 'accounts.yml').write_text('facebook:\n  page_id: page-1\n')
    (brand / 'clip.mp4').write_bytes(b'video')
    data = dict(id='clip', platform='facebook', account_id='page-1',
        publish_at='2035-01-01T09:00:00-05:00', dispatch_after='2034-12-31T14:00:00Z',
        route='ui', payload=dict(calendar='Meta Business Suite', visibility='public',
        copy='Hello', content_origin='standalone', source_video_id=None, options={'format': 'reel'}, first_comment='Source',
        media=[{'path':'clip.mp4','origin':'user-provided'}]))
    (brand / 'native-calendar.json').write_text(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(), coverage_start='2034-12-31T00:00:00-05:00', coverage_end='2035-02-01T00:00:00-05:00', accounts=[dict(platform='facebook',account_id='page-1',complete=True)], objects=[])))
    (brand / 'native.json').write_text(json.dumps(data))
    return brand, data


def invoke(tmp_path, *args):
    return runner.invoke(app, ['native-schedule', *args, '--brand', 'Histopast', '--root', str(tmp_path)])


def digest(output):
    return re.search(r'Approval digest: ([a-f0-9]{64})', output)[1]


def test_prepare_readonly_stable_approve_and_changed_media(tmp_path):
    brand, _ = fixture(tmp_path)
    first = invoke(tmp_path, 'prepare', 'native.json', '--dry-run')
    assert first.exit_code == 0, first.output
    assert '09:00:00-05:00' in first.output and '14:00:00+00:00' in first.output
    assert 'Source' in first.output and 'Meta Business Suite' in first.output
    assert not (brand / 'native-schedules.sqlite3').exists()
    d = digest(first.output)
    assert d == digest(invoke(tmp_path, 'prepare', 'native.json', '--dry-run').output)
    saved = invoke(tmp_path, 'prepare', 'native.json', '--persist', '--approval-digest', d)
    assert saved.exit_code == 0, saved.output
    assert invoke(tmp_path, 'dispatch', 'clip', '--yes').exit_code == 1
    assert invoke(tmp_path, 'approve', 'clip', '--approval-digest', d).exit_code == 0
    assert NativeStore(brand, brand='Histopast').get('clip').state == 'approved'
    (brand / 'clip.mp4').write_bytes(b'changed')
    result = invoke(tmp_path, 'dispatch', 'clip', '--yes')
    assert result.exit_code == 1 and 'digest' in result.output
    assert NativeStore(brand, brand='Histopast').get('clip').state == 'approved'


def test_digest_full_binding(tmp_path):
    brand, data = fixture(tmp_path)
    from socialctl.native_schedule.cli import proposal
    from socialctl.brands import cargar_brand
    job, payload = proposal(cargar_brand(tmp_path, 'Histopast'), 'native.json')
    base = native_digest(job, payload)
    for key, value in [('account_id','other'), ('route','api'), ('timezone_name','UTC'),
                       ('publish_at',job.publish_at+timedelta(days=1))]:
        changed = NativeJob.model_validate(job.model_dump() | {key:value})
        assert native_digest(changed,payload) != base
    for key, value in [('copy','changed'),('options',{'format':'video'}),('first_comment','other')]:
        assert native_digest(job,payload | {key:value}) != base
    assert native_digest(job.model_copy(update={'id':'new','created_at':datetime.now(timezone.utc)}),payload) == base
    (brand / 'clip.mp4').write_bytes(b'other')
    assert native_digest(job,payload) != base


def test_overflow_secrets_and_missing_origin(tmp_path):
    brand, data = fixture(tmp_path)
    d = digest(invoke(tmp_path,'prepare','native.json','--dry-run').output)
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',d).exit_code == 0
    data['id'] = 'second'
    (brand/'native.json').write_text(json.dumps(data))
    result = invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code == 0 and '2035-01-02T09:00:00-05:00' in result.output and 'Overflow' in result.output
    data['payload']['options']['access_token'] = 'DO-NOT-PRINT'
    (brand/'native.json').write_text(json.dumps(data))
    result = invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code == 1 and 'DO-NOT-PRINT' not in result.output
    del data['payload']['options']['access_token']
    del data['payload']['media'][0]['origin']
    (brand/'native.json').write_text(json.dumps(data))
    assert invoke(tmp_path,'prepare','native.json','--dry-run').exit_code == 1


@pytest.mark.parametrize('operation',['dispatch','reconcile','cancel','reschedule'])
def test_explicit_approval_never_bypassed(tmp_path, operation):
    brand, _ = fixture(tmp_path)
    d = digest(invoke(tmp_path,'prepare','native.json','--dry-run').output)
    invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',d)
    assert invoke(tmp_path,operation,'clip','--yes').exit_code == 1
    invoke(tmp_path,'approve','clip','--approval-digest',d)
    result = invoke(tmp_path,operation,'clip','--yes')
    assert result.exit_code == (0 if operation in {'dispatch','reconcile'} else 1)
    assert NativeStore(brand,brand='Histopast').get_attempts('clip') == []


def test_remote_marker_never_reads_local_ledger_or_manifest(tmp_path, monkeypatch):
    import shlex
    import subprocess
    from tests.test_schedule_remote import marker
    from socialctl.brands import cargar_brand
    brand, _ = fixture(tmp_path)
    config = marker(cargar_brand(tmp_path,'Histopast'))
    (brand/'native-schedules.sqlite3').write_bytes(b'stale and invalid')
    (brand/'native.json').unlink()
    (brand/'accounts.yml').write_text('invalid: [')
    (brand/'brand.md').mkdir()
    calls = []
    def ssh(argv, **kwargs):
        calls.append(shlex.split(argv[-1]))
        return subprocess.CompletedProcess(argv,0,'Remote authoritative result\n','')
    monkeypatch.setattr(subprocess,'run',ssh)
    for args in [('status',),('prepare','native.json','--dry-run'),
                 ('prepare','native.json','--persist','--approval-digest','a'*64),
                 ('approve','clip','--approval-digest','a'*64),('dispatch','clip','--yes'),
                 ('reconcile','clip'),('cancel','clip','--dry-run'),('reschedule','clip','--publish-at','2027-01-02T14:00:00+00:00','--dry-run')]:
        result = invoke(tmp_path,*args)
        assert result.exit_code == 0, result.output
        assert result.output == 'Remote authoritative result\n'
        assert calls[-1] == [config['executable'],'native-schedule',*args,'--brand','Histopast','--root',config['root']]
    assert (brand/'native-schedules.sqlite3').read_bytes() == b'stale and invalid'


@pytest.mark.parametrize('output',['access_token=SUPER-SECRET','https://upload.example/a?token=SUPER-SECRET','{"token":"SUPER-SECRET"}'])
def test_remote_output_secret_rejected(tmp_path, monkeypatch, output):
    import subprocess
    from tests.test_schedule_remote import marker
    from socialctl.brands import cargar_brand
    fixture(tmp_path)
    marker(cargar_brand(tmp_path,'Histopast'))
    monkeypatch.setattr(subprocess,'run',lambda argv,**kw:subprocess.CompletedProcess(argv,0,output,''))
    result = invoke(tmp_path,'status')
    assert result.exit_code == 1 and 'SUPER-SECRET' not in result.output


def test_wrong_digest_or_changed_media_cannot_approve(tmp_path):
    brand,_ = fixture(tmp_path)
    d = digest(invoke(tmp_path,'prepare','native.json','--dry-run').output)
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest','a'*64).exit_code == 1
    assert not (brand/'native-schedules.sqlite3').exists()
    invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',d)
    assert invoke(tmp_path,'approve','clip','--approval-digest','a'*64).exit_code == 1
    (brand/'clip.mp4').write_bytes(b'new bytes')
    assert invoke(tmp_path,'approve','clip','--approval-digest',d).exit_code == 1
    assert NativeStore(brand,brand='Histopast').get('clip').state == 'prepared'


def test_required_brand_and_status_readonly(tmp_path):
    brand,_ = fixture(tmp_path)
    assert invoke(tmp_path,'status').exit_code == 0
    assert not (brand/'native-schedules.sqlite3').exists()
    result = runner.invoke(app,['native-schedule','status','--root',str(tmp_path)])
    assert result.exit_code == 2


def test_derivative_exact_public_cta_and_text_only(tmp_path):
    brand,data = fixture(tmp_path)
    data['payload'].update(content_origin='youtube_long',source_video_id='M3zbWgbb4lo',
        first_comment='🎥 Video completo en https://www.youtube.com/watch?v=M3zbWgbb4lo')
    (brand/'native.json').write_text(json.dumps(data))
    result = invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code == 0, result.output
    assert data['payload']['first_comment'] in result.output
    d = digest(result.output)
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',d).exit_code == 0
    assert invoke(tmp_path,'approve','clip','--approval-digest',d).exit_code == 0
    data['id']='text'
    data['payload'].update(media=[],options={'format':'text'},content_origin='standalone',source_video_id=None,first_comment=None)
    (brand/'native.json').write_text(json.dumps(data))
    result = invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code == 0, result.output
    d = digest(result.output)
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',d).exit_code == 0
    assert invoke(tmp_path,'approve','text','--approval-digest',d).exit_code == 0


@pytest.mark.parametrize('changes',[
    {'content_origin':None},{'content_origin':'unknown'},
    {'content_origin':'youtube_long','source_video_id':None},
    {'content_origin':'youtube_long','source_video_id':'M3zbWgbb4lo','first_comment':'wrong'},
    {'content_origin':'standalone','source_video_id':'M3zbWgbb4lo'},
    {'content_origin':'youtube_long','source_video_id':'invalid'},
    {'media':[]},{'media':[],'options':{'format':'text'},'copy':''},
])
def test_invalid_content_semantics_rejected(tmp_path,changes):
    brand,data = fixture(tmp_path)
    data['payload'].update(changes)
    (brand/'native.json').write_text(json.dumps(data))
    result = invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code == 1, result.output
    assert not (brand/'native-schedules.sqlite3').exists()


@pytest.mark.parametrize('url',[
    'HTTPS://upload.example/a?X-Amz-Signature=SUPER-SECRET',
    'https://example.com/a?%74oken=SUPER-SECRET',
    'hTTpS://example.com/a?X%252dAmz%252dSignature=SUPER-SECRET',
    'https://example.com/a#access_token=SUPER-SECRET',
    'https://user:SUPER-SECRET@example.com/a',
    'HTTPS://upload.example/a?upload_id=SUPER-SECRET',
    'https://upload.example/a?v=hello%20&X-Amz-Signature=SUPER-SECRET',
    'https://upload.example/a?v=hello%22&X-Amz-Signature=SUPER-SECRET',
    'https://upload.example/a?v=hello%0A&X-Amz-Signature=SUPER-SECRET',
    'https://upload.example/a?v=hello%2520&X-Amz-Signature=SUPER-SECRET',
    'https://upload.example/a?v=hello%2522&X-Amz-Signature=SUPER-SECRET',
    'https://upload.example/a?v=hello%250A&X-Amz-Signature=SUPER-SECRET',
])
def test_credential_url_rejected_local_and_remote(tmp_path,monkeypatch,url):
    import subprocess
    from tests.test_schedule_remote import marker
    from socialctl.brands import cargar_brand
    brand,data = fixture(tmp_path)
    data['payload']['first_comment']=url
    (brand/'native.json').write_text(json.dumps(data))
    result = invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code == 1 and 'SUPER-SECRET' not in result.output
    marker(cargar_brand(tmp_path,'Histopast'))
    monkeypatch.setattr(subprocess,'run',lambda argv,**kw:subprocess.CompletedProcess(argv,0,url,''))
    result = invoke(tmp_path,'status')
    assert result.exit_code == 1 and 'SUPER-SECRET' not in result.output


def test_text_kind_cannot_remove_media_for_instagram(tmp_path):
    brand,data = fixture(tmp_path)
    (brand/'accounts.yml').write_text('instagram:\n  ig_user_id: page-1\n')
    data['platform']='instagram'
    data['payload'].update(media=[],options={'format':'text'})
    (brand/'native.json').write_text(json.dumps(data))
    assert invoke(tmp_path,'prepare','native.json','--dry-run').exit_code == 1


def test_semantics_rechecked_before_approval(tmp_path):
    from socialctl.native_schedule.cli import proposal
    from socialctl.brands import cargar_brand
    from socialctl.native_schedule.approval import hash_value
    brand,_ = fixture(tmp_path)
    job,payload = proposal(cargar_brand(tmp_path,'Histopast'),'native.json')
    del payload['content_origin']
    job = NativeJob.model_validate(job.model_dump() | {'content_hash':hash_value(payload)})
    d = native_digest(job,payload)
    store = NativeStore(brand,brand='Histopast')
    store.prepare(job,payload=payload)
    result = invoke(tmp_path,'approve','clip','--approval-digest',d)
    assert result.exit_code == 1 and 'content_origin' in result.output
    assert store.get('clip').state == 'prepared'


@pytest.mark.parametrize('change,expected',[
    ('missing','native-calendar.json required'),
    ('stale','Fresh complete'),
    ('future','Fresh complete'),
    ('coverage','Full local publication day'),
    ('account','account mismatch'),
    ('incomplete','Incomplete native'),
])
def test_ordinary_prepare_requires_fresh_complete_calendar(tmp_path,change,expected):
    brand,_=fixture(tmp_path)
    path=brand/'native-calendar.json'
    data=json.loads(path.read_text())
    if change=='missing':path.unlink()
    else:
        if change=='stale':data['observed_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        if change=='future':data['observed_at']=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
        if change=='coverage':data['coverage_end']='2035-01-01T10:00:00-05:00'
        if change=='account':data['accounts'][0]['account_id']='wrong'
        if change=='incomplete':data['accounts'][0]['complete']=False
        path.write_text(json.dumps(data))
    result=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code==1 and expected in result.output,result.output
    assert not (brand/'native-schedules.sqlite3').exists()


def test_calendar_changed_since_preview_requires_new_digest_without_writes(tmp_path):
    brand,_=fixture(tmp_path)
    shown=invoke(tmp_path,'prepare','native.json','--dry-run')
    path=brand/'native-calendar.json';data=json.loads(path.read_text())
    data['objects']=[dict(platform='facebook',account_id='page-1',remote_ref='external',publish_at='2035-01-01T10:00:00-05:00')]
    path.write_text(json.dumps(data))
    result=invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',digest(shown.output))
    assert result.exit_code==1 and 'digest' in result.output
    assert not (brand/'native-schedules.sqlite3').exists()
    shown=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert '2035-01-02T09:00:00-05:00' in shown.output
    assert not (brand/'native-schedules.sqlite3').exists()
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',digest(shown.output)).exit_code==0
    assert NativeStore(brand,brand='Histopast').reserved_days('facebook','page-1')=={'2035-01-01','2035-01-02'}
