"""Offline insights must preserve uncertainty, provenance and actual windows."""
import json
from datetime import date

import pytest
from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.metricas.insights import build_report, render_report


def save(brand, day, *, followers=100, account='channel-a', state='ok', pieces=None,
         warnings=None, observed=None, filename=None):
    root=brand.raiz/'metricas';root.mkdir(exist_ok=True)
    data={'fecha':day,'marca':brand.nombre,'redes':{'youtube':{
        'estado':state,'account_id':account,'observed_at':observed or day+'T12:00:00Z',
        'cuenta':{'seguidores':followers},'piezas':pieces or [],'warnings':warnings or []}}}
    path=root/((filename or day)+'.json');path.write_text(json.dumps(data))
    return path


def piece(identifier, views):
    return {'id':identifier,'url':'https://example.com/'+identifier,'titulo':identifier,
            'tipo':'vertical','publicado_el':'2026-09-01T12:00:00Z',
            'acumulado':{'vistas':views}}


@pytest.fixture
def brand(tmp_path):return crear_brand(tmp_path,'Example')


def result(brand):return build_report(brand,as_of=date(2026,10,13))


def test_growth_and_rankings_use_comparable_identity_and_actual_times(brand):
    save(brand,'2026-09-29',followers=50)
    save(brand,'2026-10-06',followers=100,pieces=[piece('old',100),piece('top',20)])
    save(brand,'2026-10-13',followers=125,pieces=[piece('old',110),piece('top',80),piece('new',900)])
    report=result(brand);row=report.data['platforms']['youtube']
    assert report.status=='ok'
    assert row['account']['followers']=={'current':125,'baseline':100,'change':25,'percent_change':25.0}
    assert row['comparison']['elapsed_seconds']==604800
    assert row['previous_comparison']['account']['followers']['change']==50
    assert [p['id'] for p in row['top_content']['view_change']]==['top','old']
    assert row['top_content']['view_change'][0]['value']==60
    assert row['top_content']['lifetime_views'][0]['id']=='new'
    assert row['top_content']['without_baseline']==1
    assert row['best_posting_time']['status']=='unavailable'


@pytest.mark.parametrize('account,expected',[(None,'account_identity_unknown'),('channel-b','account_changed')])
def test_legacy_or_replaced_account_cannot_produce_growth(brand,account,expected):
    save(brand,'2026-10-06',account=account,pieces=[piece('same',5)])
    save(brand,'2026-10-13',pieces=[piece('same',50)])
    report=result(brand);row=report.data['platforms']['youtube']
    assert report.status=='partial'
    assert row['comparison']['reason']==expected
    assert row['account']['followers']['change'] is None
    assert row['top_content']['view_change']==[]
    assert row['top_content']['lifetime_views'][0]['value']==50


def test_unknown_zero_and_decreasing_counters_are_not_invented(brand):
    save(brand,'2026-10-06',followers=0,pieces=[piece('zero',0),piece('unknown',None),piece('adjusted',90)])
    save(brand,'2026-10-13',followers=5,pieces=[piece('zero',10),piece('unknown',40),piece('adjusted',80),piece('bad',-1)])
    row=result(brand).data['platforms']['youtube']
    assert row['account']['followers']['change']==5
    assert row['account']['followers']['percent_change'] is None
    assert [(p['id'],p['value']) for p in row['top_content']['view_change']]==[('zero',10)]
    assert row['top_content']['counter_corrections']==['adjusted']
    assert 'bad' not in [p['id'] for p in row['top_content']['lifetime_views']]


def test_missing_baseline_does_not_substitute_an_uneven_period(brand):
    save(brand,'2026-10-05',followers=10)
    save(brand,'2026-10-13',followers=100)
    report=result(brand)
    assert report.status=='partial'
    assert report.data['platforms']['youtube']['comparison']['reason']=='baseline_unavailable'


def test_stale_partial_read_keeps_verified_values_and_fixed_diagnostics(brand):
    save(brand,'2026-10-05',followers=100)
    save(brand,'2026-10-12',followers=110,warnings=['access_token=private-secret'])
    report=result(brand)
    assert report.status=='partial'
    assert report.data['latest_snapshot']=='2026-10-12'
    assert report.data['platforms']['youtube']['account']['followers']['change']==10
    assert 'private-secret' not in report.model_dump_json()
    assert report.coverage[0]['stale'] is True
    assert report.coverage[0]['source_partial'] is True


@pytest.mark.parametrize('mode',['corrupt','wrong_brand','wrong_date'])
def test_invalid_snapshot_is_sanitized_and_not_replaced_with_older_data(brand,mode):
    save(brand,'2026-10-06')
    p=save(brand,'2026-10-13')
    if mode=='corrupt':p.write_text('access_token=private-secret {')
    else:
        data=json.loads(p.read_text());data['marca' if mode=='wrong_brand' else 'fecha']='Other' if mode=='wrong_brand' else '2026-10-12';p.write_text(json.dumps(data))
    report=result(brand)
    assert report.status=='error'
    assert 'private-secret' not in report.model_dump_json()
    assert report.data['platforms']=={}


def test_duplicate_ids_do_not_generate_ambiguous_rankings(brand):
    save(brand,'2026-10-06',pieces=[piece('a',1)])
    save(brand,'2026-10-13',pieces=[piece('a',30),piece('a',20),piece('z',10),piece('b',10)])
    report=result(brand);row=report.data['platforms']['youtube']
    assert report.status=='partial'
    assert [p['id'] for p in row['top_content']['lifetime_views']]==['b','z']
    assert row['top_content']['view_change']==[]


@pytest.mark.parametrize('observed',['2026-10-06T12:00:00Z','2026-10-13T12:00:00'])
def test_reused_or_unzoned_read_cannot_define_an_observed_interval(brand,observed):
    save(brand,'2026-10-06',followers=10)
    save(brand,'2026-10-13',followers=30,observed=observed)
    report=result(brand)
    assert report.status=='partial'
    assert report.data['platforms']['youtube']['account']['followers']['change'] is None


def test_report_with_no_snapshots_is_actionable(brand):
    report=result(brand)
    assert report.status=='error'
    assert report.errors[0]['code']=='snapshots_unavailable'
    assert report.errors[0]['action']=='collect_metrics'


def test_human_renderer_does_not_emit_terminal_controls(brand):
    p=piece('safe',10);p['titulo']='Hello\x1b[31m [bold]';p['url']='https://example.com/\nBAD'
    save(brand,'2026-10-13',pieces=[p])
    rendered=render_report(result(brand))
    assert '\x1b' not in rendered
    assert '\nBAD' not in rendered


def test_cli_json_and_human_report_are_offline_and_do_not_mutate_snapshots(brand,tmp_path,monkeypatch):
    import httpx
    from socialctl.cli import app
    def forbidden(*a,**kw):raise AssertionError('offline report attempted network access')
    monkeypatch.setattr(httpx.Client,'request',forbidden)
    save(brand,'2026-10-06',followers=20)
    latest=save(brand,'2026-10-13',followers=25)
    before=latest.read_bytes()
    argv=['report','--brand','Example','--root',str(tmp_path),'--as-of','2026-10-13']
    response=CliRunner().invoke(app,argv+['--json'])
    assert response.exit_code==0,response.output
    payload=json.loads(response.stdout)
    assert payload['data']['platforms']['youtube']['account']['followers']['change']==5
    human=CliRunner().invoke(app,argv)
    assert human.exit_code==0,human.output
    assert 'observed change: +5' in human.stdout
    assert latest.read_bytes()==before


@pytest.mark.parametrize('args,code',[(['--as-of','bad'],2),(['--days','0'],2),([],1)])
def test_cli_errors_stay_json(brand,tmp_path,args,code):
    from socialctl.cli import app
    response=CliRunner().invoke(app,['report','--brand','Example','--root',str(tmp_path),'--json',*args])
    assert response.exit_code==code,response.output
    assert json.loads(response.stdout)['status']=='error'


def test_future_or_unrelated_observation_does_not_claim_the_requested_window(brand):
    save(brand,'2026-10-06',followers=10)
    save(brand,'2026-10-13',followers=30,observed='2026-11-13T12:00:00Z')
    report=result(brand)
    assert report.status=='partial'
    assert report.data['platforms']['youtube']['comparison']['status']=='unavailable'


def test_previous_period_for_replaced_account_is_not_compared_to_current_account(brand):
    save(brand,'2026-09-29',followers=10,account='former-account')
    save(brand,'2026-10-06',followers=30,account='former-account')
    save(brand,'2026-10-13',followers=90)
    row=result(brand).data['platforms']['youtube']
    assert row['previous_comparison']['status']=='unavailable'
    assert row['previous_comparison']['account']['followers']['change'] is None


def test_human_report_explains_corrections_and_unmatched_content(brand):
    save(brand,'2026-10-06',pieces=[piece('adjusted',40)])
    save(brand,'2026-10-13',pieces=[piece('adjusted',30),piece('new',10)])
    text=render_report(result(brand))
    assert 'counter corrections: 1' in text
    assert 'without baseline: 1' in text


def test_previous_day_read_outside_any_local_timezone_is_stale(brand):
    save(brand,'2026-10-06',followers=10)
    save(brand,'2026-10-13',followers=30,observed='2026-10-12T01:00:00Z')
    report=result(brand)
    assert report.status=='partial'
    assert report.coverage[0]['complete'] is False
    assert report.data['platforms']['youtube']['comparison']['reason']=='observation_date_mismatch'


def test_account_view_counter_correction_is_not_negative_growth_but_follower_loss_is(brand):
    for day,views,followers in [('2026-10-06',100,40),('2026-10-13',80,30)]:
        path=save(brand,day,followers=followers)
        data=json.loads(path.read_text());data['redes']['youtube']['cuenta']['total_vistas']=views
        path.write_text(json.dumps(data))
    report=result(brand);row=report.data['platforms']['youtube']
    assert row['account']['views']['change'] is None
    assert row['account']['views']['counter_correction'] is True
    assert row['account']['views']['observed_difference']==-20
    assert row['account']['followers']['change']==-10
    assert report.status=='partial'
    assert 'views counter correction: -20' in render_report(report)


@pytest.mark.parametrize('start,end',[(None,None),(10,-1),(None,10)])
def test_unknown_content_views_cannot_report_complete_coverage(brand,start,end):
    save(brand,'2026-10-06',followers=10,pieces=[piece('same',start)])
    save(brand,'2026-10-13',followers=20,pieces=[piece('same',end)])
    report=result(brand)
    assert report.status=='partial'
    assert report.coverage[0]['complete'] is False
    assert report.data['platforms']['youtube']['account']['followers']['change']==10
