"""Exact schedules reject conflicts, DST gaps and implicit timezone changes."""
from datetime import datetime, timezone
import json
import re

import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.native_schedule.store import NativeStore

runner=CliRunner()


def setup(tmp_path, *, when='2035-01-03T19:30:00+01:00', zone='Europe/Madrid'):
    brand=tmp_path/'Example';brand.mkdir()
    (brand/'accounts.yml').write_text('facebook:\n  page_id: page-example\n')
    (brand/'clip.mp4').write_bytes(b'fictional original bytes')
    manifest={'id':'clip','platform':'facebook','account_id':'page-example',
        'publish_at':when,'dispatch_after':'2029-01-01T00:00:00Z','route':'ui',
        'schedule':{'mode':'exact','timezone':zone},
        'payload':{'calendar':'Meta Business Suite','visibility':'public','copy':'Approved caption',
            'content_origin':'standalone','source_video_id':None,'first_comment':None,
            'options':{'format':'reel','share_to_facebook_story':False},
            'media':[{'path':'clip.mp4','origin':'user'}]}}
    inventory={'observed_at':datetime.now(timezone.utc).isoformat(),
        'coverage_start':'2030-01-01T00:00:00Z','coverage_end':'2036-01-01T00:00:00Z',
        'accounts':[{'platform':'facebook','account_id':'page-example','complete':True}], 'objects':[]}
    (brand/'native.json').write_text(json.dumps(manifest))
    (brand/'native-calendar.json').write_text(json.dumps(inventory))
    return brand,manifest,inventory


def invoke(tmp_path,*args):
    return runner.invoke(app,['native-schedule',*args,'--brand','Example','--root',str(tmp_path)])


def test_customer_exact_local_time_persists_without_nine_am_overflow(tmp_path):
    brand,manifest,_=setup(tmp_path)
    result=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code==0,result.output
    assert 'Final local: 2035-01-03T19:30:00+01:00 Europe/Madrid' in result.output
    assert 'Final UTC: 2035-01-03T18:30:00+00:00' in result.output
    assert not (brand/'native-schedules.sqlite3').exists()
    digest=re.search(r'Approval digest: ([a-f0-9]{64})',result.output)[1]
    assert invoke(tmp_path,'prepare','native.json','--persist','--approval-digest',digest).exit_code==0
    assert invoke(tmp_path,'approve','clip','--approval-digest',digest).exit_code==0
    saved=NativeStore(brand,brand='Example').get('clip')
    assert saved.schedule_mode=='exact' and saved.timezone_name=='Europe/Madrid'
    assert saved.publish_at==datetime(2035,1,3,18,30,tzinfo=timezone.utc)
    manifest['id']='second';manifest['publish_at']='2035-01-03T21:15:00+01:00'
    (brand/'native.json').write_text(json.dumps(manifest))
    assert invoke(tmp_path,'prepare','native.json','--dry-run').exit_code==0
    manifest['publish_at']='2035-01-03T19:30:00+01:00'
    (brand/'native.json').write_text(json.dumps(manifest))
    collision=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert collision.exit_code==1
    assert len(NativeStore(brand,brand='Example').list_jobs())==1


@pytest.mark.parametrize('when,zone',[
    ('2035-01-03T19:30:00','Europe/Madrid'),
    ('2035-01-03T19:30:00+02:00','Europe/Madrid'),
    ('2035-01-03T19:30:00.001+01:00','Europe/Madrid'),
    ('2030-03-10T02:30:00-05:00','America/New_York'),
    ('2030-03-10T02:30:00-04:00','America/New_York'),
    ('2035-01-03T19:30:00+01:00','Not/A_Zone'),
    ('2020-01-03T19:30:00+01:00','Europe/Madrid'),
])
def test_invalid_exact_local_time_never_creates_ledger(tmp_path,when,zone):
    brand,_,_=setup(tmp_path,when=when,zone=zone)
    result=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code==1,result.output
    assert not (brand/'native-schedules.sqlite3').exists()


@pytest.mark.parametrize('offset,utc',[('-04:00','05:30'),('-05:00','06:30')])
def test_repeated_dst_hour_uses_customer_explicit_offset(tmp_path,offset,utc):
    setup(tmp_path,when='2030-11-03T01:30:00'+offset,zone='America/New_York')
    result=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code==0,result.output
    assert f'Final UTC: 2030-11-03T{utc}:00+00:00' in result.output


def test_inventory_coverage_uses_customer_local_day(tmp_path):
    brand,_,inventory=setup(tmp_path,when='2035-01-03T00:30:00+09:00',zone='Asia/Tokyo')
    inventory.update(coverage_start='2035-01-03T00:00:00+09:00',coverage_end='2035-01-04T00:00:00+09:00')
    (brand/'native-calendar.json').write_text(json.dumps(inventory))
    result=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code==0,result.output
    inventory['coverage_start']='2035-01-03T00:01:00+09:00'
    (brand/'native-calendar.json').write_text(json.dumps(inventory))
    assert invoke(tmp_path,'prepare','native.json','--dry-run').exit_code==1


def test_exact_external_inventory_same_instant_different_offset_is_not_moved(tmp_path):
    brand,_,inventory=setup(tmp_path)
    inventory['objects']=[dict(platform='facebook',account_id='page-example',remote_ref='external',publish_at='2035-01-03T13:30:00-05:00')]
    (brand/'native-calendar.json').write_text(json.dumps(inventory))
    result=invoke(tmp_path,'prepare','native.json','--dry-run')
    assert result.exit_code==1
    assert not (brand/'native-schedules.sqlite3').exists()
