import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner
from socialctl.cli import app
from socialctl.scheduler import ScheduleEntry, ScheduleStore, ScheduleError, occurrence_id
from socialctl.native_schedule import migration as m
from socialctl.native_schedule.store import NativeStore

NOW = datetime(2030, 11, 2, 12, tzinfo=timezone.utc)


def setup(tmp_path, monkeypatch, backend=None):
    monkeypatch.setattr(m, 'now_utc', lambda: NOW)
    root = tmp_path / 'Histopast'
    root.mkdir()
    (root / 'accounts.yml').write_text('facebook:\n  page_id: page\ninstagram:\n  ig_user_id: ig\n')
    (root / 'clip.mp4').write_bytes(b'supplied')
    store = ScheduleStore(root, backend=backend)
    entries = [ScheduleEntry(id=f'{slug}/{platform}', brand='Histopast', slug=slug, platform=platform,
        scheduled_at=NOW+timedelta(hours=2), created_at=NOW-timedelta(days=1), updated_at=NOW,
        content_hash='old-approval') for slug, platform in [('one','facebook'),('one','instagram'),('two','facebook')]]
    store.save(entries)
    payload = dict(calendar='Meta Business Suite', visibility='public', copy='Unchanged',
        content_origin='standalone', source_video_id=None, options={'format':'reel','share_to_facebook_story':False},
        first_comment='Separate later action', media=[{'path':'clip.mp4','origin':'user-provided'}])
    manifest = dict(version=1, brand='Histopast', planned_at=NOW.isoformat(),
        inventory=dict(observed_at=NOW.isoformat(),coverage_start='2030-11-02T00:00:00-04:00',coverage_end=(NOW+timedelta(days=30)).isoformat(),accounts=[dict(platform=p,account_id=a,complete=True) for p,a in [('facebook','page'),('instagram','ig')]],objects=[]),
        candidates=[dict(entry_id=e.id,created_at=e.created_at.isoformat(),route='ui',payload=payload) for e in entries])
    (root/'native-migration.json').write_text(json.dumps(manifest))
    return root,store,entries,manifest


def save(root, manifest):
    (root/'native-migration.json').write_text(json.dumps(manifest))


@pytest.mark.parametrize('backend',[None,'sqlite'])
def test_readonly_preview_and_transfer_preserves_audit(tmp_path,monkeypatch,backend):
    root,old,entries,manifest=setup(tmp_path,monkeypatch,backend)
    before=old.load()
    preview=m.prepare_migration('Histopast',root=tmp_path)
    assert not (root/'native-schedules.sqlite3').exists()
    assert preview==m.prepare_migration('Histopast',root=tmp_path)
    assert [r['job']['publish_at'] for r in preview['transfers']]==['2030-11-02T13:00:00Z','2030-11-02T13:00:00Z','2030-11-03T14:00:00Z']
    result=m.apply_migration(preview['digest'],brand='Histopast',root=tmp_path)
    assert result['status']=='committed'
    assert old.load()==before
    native=NativeStore(root,brand='Histopast')
    assert len(native.list_jobs())==3
    assert all(j.state=='prepared' and j.approval_digest is None for j in native.list_jobs())
    assert old.due(NOW+timedelta(days=10))==[]
    assert old.claim_due(entries[0].id,NOW+timedelta(days=10),expected=entries[0]) is None
    assert (old.root/'migration-active.json').exists() # historical reader refuses
    for item in preview['transfers']:
        assert m._date(item['original']['scheduled_at'])==entries[0].scheduled_at


def test_inventory_refs_uncertain_and_published_never_upload(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    entries[0].platform_id='already-remote'
    entries[1].status='manual_review'
    entries[2].status='published'
    old.save(entries)
    p=m.prepare_migration('Histopast',root=tmp_path)
    assert not p['transfers'] and len(p['excluded'])==3
    entries[0].platform_id=None
    entries[0].status='approved'
    old.save(entries)
    manifest['inventory']['objects']=[dict(platform='facebook',account_id='page',remote_ref='existing',publish_at='2030-11-02T10:00:00-04:00',occurrence_id=occurrence_id(root,entries[0]))]
    save(root,manifest)
    assert not m.prepare_migration('Histopast',root=tmp_path)['transfers']


def test_remote_reservations_and_stale_inventory(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    manifest['inventory']['objects']=[dict(platform='facebook',account_id='page',remote_ref='existing',publish_at='2030-11-02T10:00:00-04:00')]
    save(root,manifest)
    p=m.prepare_migration('Histopast',root=tmp_path)
    assert p['transfers'][0]['job']['publish_at']=='2030-11-03T14:00:00Z'
    assert p['transfers'][1]['job']['publish_at']=='2030-11-02T13:00:00Z'
    manifest['inventory']['observed_at']=(NOW-timedelta(days=2)).isoformat()
    save(root,manifest)
    with pytest.raises(ValueError): m.prepare_migration('Histopast',root=tmp_path)


@pytest.mark.parametrize('boundary',['intent','guard','tombstones','reservations','job:0','job:1','job:2','commit'])
def test_crash_every_durable_boundary_recovers_without_two_owners(tmp_path,monkeypatch,boundary):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    def fail(point):
        if point==boundary: raise RuntimeError('simulated process death')
    monkeypatch.setattr(m,'_checkpoint',fail)
    with pytest.raises(RuntimeError): m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    native=NativeStore(root,brand='Histopast')
    if native.list_jobs():
        if boundary=='commit':
            assert old.claim_due(entries[0].id,NOW+timedelta(days=10)) is None
        else:
            with pytest.raises(ScheduleError): old.claim_due(entries[0].id,NOW+timedelta(days=10))
    monkeypatch.setattr(m,'_checkpoint',lambda _:None)
    r=m.recover_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert r['status']=='committed'
    assert len(native.list_jobs())==3
    assert old.due(NOW+timedelta(days=10))==[]
    assert all(j.approval_digest is None for j in native.list_jobs())


def test_tombstone_claim_recheck_corruption_and_reused_id(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    selected=old.due(NOW+timedelta(days=1))[0]
    m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert old.claim_due(selected.id,NOW+timedelta(days=1),expected=selected) is None
    entries[0].status='published'
    old.save(entries)
    fresh=entries[0].model_copy(update={'status':'approved','created_at':NOW})
    old.add(fresh)
    assert old.claim_due(fresh.id,NOW+timedelta(days=1)).status=='running'
    (old.root/'legacy-tombstones.json').write_text('{}')
    for operation in [lambda:old.due(NOW),lambda:old.claim_due(entries[1].id,NOW),old.assert_ready]:
        with pytest.raises(ScheduleError): operation()
    assert old.load() # metrics reader remains independent


def test_changed_input_wrong_digest_and_cli(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    with pytest.raises(ValueError): m.apply_migration('a'*64,brand='Histopast',root=tmp_path)
    (root/'clip.mp4').write_bytes(b'changed')
    with pytest.raises(ValueError): m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    runner=CliRunner()
    result=runner.invoke(app,['native-schedule','migration-prepare','--dry-run','--brand','Histopast','--root',str(tmp_path)])
    assert result.exit_code==0,result.output
    p=json.loads(result.output)
    result=runner.invoke(app,['native-schedule','migration-apply','--digest',p['digest'],'--brand','Histopast','--root',str(tmp_path)])
    assert result.exit_code==0,result.output


def test_identified_remote_protected_even_without_new_upload(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    for entry in entries: entry.platform_id='existing-object'
    old.save(entries)
    p=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert not NativeStore(root,brand='Histopast').list_jobs()
    assert old.due(NOW+timedelta(days=10))==[]
    # Even date edits cannot resurrect a transferred occurrence.
    changed=old.get(entries[0].id)
    changed.scheduled_at=NOW-timedelta(days=1)
    old.update(changed)
    assert old.claim_due(changed.id,NOW) is None


@pytest.mark.parametrize('change',[
    lambda x:x['inventory'].update(coverage_end='2030-11-02T12:01:00Z'),
    lambda x:x['inventory'].update(coverage_start='2030-11-02T08:59:00-04:00'),
    lambda x:x['inventory'].update(coverage_end='2030-11-03T09:01:00-05:00'),
    lambda x:x['inventory']['accounts'][0].update(complete=False),
    lambda x:x['inventory'].update(observed_at='2031-01-01T00:00:00Z'),
    lambda x:x['candidates'][0]['payload'].update(content_origin=None),
    lambda x:x['candidates'][0]['payload']['options'].pop('share_to_facebook_story'),
    lambda x:x['candidates'][0]['payload']['media'][0].update(path='../outside.mp4'),
])
def test_incomplete_inventory_and_payload_fail_closed(tmp_path,monkeypatch,change):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    change(manifest);save(root,manifest)
    with pytest.raises(ValueError):m.prepare_migration('Histopast',root=tmp_path)
    assert not (root/'native-schedules.sqlite3').exists()
    assert not (old.root/'migration-active.json').exists()


def test_results_retained_and_never_reuploaded(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    path=root/'posts'/'one'/'resultado.json';path.parent.mkdir(parents=True)
    result={'resultados':[{'platform':'facebook','platform_id':'remote','status':'publicado'}]}
    path.write_text(json.dumps(result))
    p=m.prepare_migration('Histopast',root=tmp_path)
    assert len(p['transfers'])==2
    m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert json.loads(path.read_text())==result
    assert len(NativeStore(root,brand='Histopast').list_jobs())==2


@pytest.mark.parametrize('target',['intent.json','migration-active.json','legacy-tombstones.json'])
def test_crash_before_atomic_write_never_releases_native(tmp_path,monkeypatch,target):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    write=m.write_json
    def fail(path,value):
        if path.name==target:raise OSError('disk failure before rename')
        write(path,value)
    monkeypatch.setattr(m,'write_json',fail)
    with pytest.raises(OSError):m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert not NativeStore(root,brand='Histopast').list_jobs()
    monkeypatch.setattr(m,'write_json',write)
    if target=='intent.json':m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    else:m.recover_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert old.due(NOW+timedelta(days=10))==[]


def test_expired_or_changed_recovery_keeps_guard(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    def fail(point):
        if point=='tombstones':raise RuntimeError()
    monkeypatch.setattr(m,'_checkpoint',fail)
    with pytest.raises(RuntimeError):m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    monkeypatch.setattr(m,'_checkpoint',lambda _:None)
    monkeypatch.setattr(m,'now_utc',lambda:NOW+timedelta(days=40))
    with pytest.raises(ValueError):m.recover_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert (old.root/'legacy-tombstones.json').exists()
    with pytest.raises(ScheduleError):old.claim_due(entries[0].id,NOW+timedelta(days=40))


def test_legacy_rollback_refuses_transferred_authority(tmp_path,monkeypatch):
    from socialctl import queue_migration
    from socialctl.brands import cargar_brand
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    directory=tmp_path/'old-migration';directory.mkdir();(directory/'intent.json').write_text('{}')
    monkeypatch.setattr(queue_migration,'load_proposal',lambda *a:{})
    monkeypatch.setattr(queue_migration,'_directory',lambda *a:directory)
    with pytest.raises(ScheduleError,match='transferencia nativa'):
        queue_migration.rollback(cargar_brand(tmp_path,'Histopast'),'ignored')
    assert old.due(NOW+timedelta(days=10))==[]


def test_remote_migration_bounded_grammar(tmp_path,monkeypatch):
    from socialctl import schedule_remote
    calls=[]
    monkeypatch.setattr(schedule_remote,'_send',lambda *args:calls.append(args))
    for args in [ ['migration-prepare','--manifest','native-migration.json','--dry-run'],
                  ['migration-apply','--manifest','native-migration.json','--digest','a'*64],
                  ['migration-recover','--digest','a'*64],['deliver'] ]:
        schedule_remote.send_native(tmp_path,'Histopast',args)
    assert len(calls)==4
    for args in [['migration-prepare','--manifest','../file','--dry-run'],
                 ['migration-apply','--manifest','native.json','--digest','wrong'],
                 ['migration-recover','--digest','a'*64,'--yes'],['deliver','anything']]:
        with pytest.raises(ScheduleError):schedule_remote.send_native(tmp_path,'Histopast',args)


def test_legacy_relocation_cannot_remove_historical_guard(tmp_path,monkeypatch):
    from socialctl import queue_migration
    from socialctl.brands import cargar_brand
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    with pytest.raises(ScheduleError,match='transferencia nativa'):
        queue_migration.prepare(cargar_brand(tmp_path,'Histopast'),dry_run=True)
    assert (old.root/'migration-active.json').exists()


def test_committed_missing_guard_or_ledger_never_releases_legacy(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    guard=old.root/'migration-active.json';raw=guard.read_bytes();guard.unlink()
    with pytest.raises(ScheduleError):old.due(NOW+timedelta(days=10))
    guard.write_bytes(raw)
    (old.root/'legacy-tombstones.json').unlink()
    with pytest.raises(ScheduleError):old.claim_due(entries[0].id,NOW+timedelta(days=10))


def test_periodic_delivery_ignores_prepared_and_reports_ui(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(p['digest'],brand='Histopast',root=tmp_path)
    result=CliRunner().invoke(app,['native-schedule','deliver','--brand','Histopast','--root',str(tmp_path)])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['delivered']==[]
    assert all(j.state=='prepared' for j in NativeStore(root,brand='Histopast').list_jobs())


@pytest.mark.parametrize('boundary',['intent','guard','tombstones','reservations','job:0','job:1','job:2','commit'])
def test_sigkill_at_durable_boundaries(tmp_path,monkeypatch,boundary):
    import subprocess
    import sys
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    p=m.prepare_migration('Histopast',root=tmp_path)
    code='''
import os, signal, sys
from datetime import datetime
from pathlib import Path
from socialctl.native_schedule import migration as m
m.now_utc=lambda: datetime.fromisoformat('2030-11-02T12:00:00+00:00')
def checkpoint(point):
    if point==sys.argv[3]: os.kill(os.getpid(),signal.SIGKILL)
m._checkpoint=checkpoint
m.apply_migration(sys.argv[2],brand='Histopast',root=Path(sys.argv[1]))
'''
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path),p['digest'],boundary],capture_output=True)
    assert result.returncode==-9,result.stderr.decode()
    jobs=NativeStore(root,brand='Histopast').list_jobs()
    if jobs: assert (old.root/'legacy-tombstones.json').exists()
    m.recover_migration(p['digest'],brand='Histopast',root=tmp_path)
    assert old.due(NOW+timedelta(days=10))==[]
    assert len(NativeStore(root,brand='Histopast').list_jobs())==3


def test_second_batch_merges_protection_without_releasing_first(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    all_candidates=manifest['candidates']
    manifest['candidates']=all_candidates[:1];save(root,manifest)
    first=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(first['digest'],brand='Histopast',root=tmp_path)
    first_ledger=json.loads((old.root/'legacy-tombstones.json').read_bytes())
    manifest['candidates']=all_candidates[1:];save(root,manifest)
    second=m.prepare_migration('Histopast',root=tmp_path)
    m.apply_migration(second['digest'],brand='Histopast',root=tmp_path)
    final=json.loads((old.root/'legacy-tombstones.json').read_bytes())
    assert all(final['entries'][key]==value for key,value in first_ledger['entries'].items())
    assert old.due(NOW+timedelta(days=10))==[]
    assert len(NativeStore(root,brand='Histopast').list_jobs())==3

    del final['entries'][next(iter(first_ledger['entries']))]
    (old.root/'legacy-tombstones.json').write_text(json.dumps(final))
    with pytest.raises(ScheduleError):old.due(NOW+timedelta(days=10))


@pytest.mark.parametrize('evidence',[
    None,
    {'resultados':[{'platform_id':'already-created'}]},
    {'resultados':[{'platform':None,'platform_id':'already-created'}]},
    {'resultados':[{'platform':7,'platform_id':'already-created'}]},
    {'resultados':[{'platform':['facebook'],'platform_id':'already-created'}]},
    {'resultados':[{'platform':'unknown','platform_id':'already-created'}]},
    {'resultados':[], 'results':[{'platform':'facebook','platform_id':'already-created'}]},
    {'resultados':[{'platform':'youtube'}, {'platform_id':'ambiguous'}]},
])
def test_ambiguous_existing_result_never_becomes_fresh_upload(tmp_path,monkeypatch,evidence):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    path=root/'posts'/'one'/'resultado.json';path.parent.mkdir(parents=True)
    original=json.dumps(evidence).encode();path.write_bytes(original)
    preview=m.prepare_migration('Histopast',root=tmp_path)
    assert {item['original']['slug'] for item in preview['transfers']}=={'two'}
    assert len(preview['excluded'])==2
    m.apply_migration(preview['digest'],brand='Histopast',root=tmp_path)
    assert len(NativeStore(root,brand='Histopast').list_jobs())==1
    assert path.read_bytes()==original
    assert not path.with_name('resultado.json.corrupto').exists()
    assert old.load()==entries


@pytest.mark.parametrize('evidence',[
    {'resultados':[{'platform':'youtube','platform_id':'unrelated'}]},
    {'results':[{'platform':'youtube','platform_id':'unrelated'}]},
    [{'platform':'youtube','platform_id':'unrelated'}],
    {'platform':'youtube','platform_id':'unrelated'},
])
def test_valid_unrelated_result_evidence_keeps_destinations_independent(tmp_path,monkeypatch,evidence):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    path=root/'posts'/'one'/'resultado.json';path.parent.mkdir(parents=True)
    original=json.dumps(evidence).encode();path.write_bytes(original)
    preview=m.prepare_migration('Histopast',root=tmp_path)
    assert len(preview['transfers'])==3
    assert preview['excluded']==[]
    assert path.read_bytes()==original


def test_nonexistent_result_is_not_ambiguous_evidence(tmp_path,monkeypatch):
    root,old,entries,manifest=setup(tmp_path,monkeypatch)
    preview=m.prepare_migration('Histopast',root=tmp_path)
    assert len(preview['transfers'])==3 and not preview['excluded']
    assert not (root/'posts').exists()
