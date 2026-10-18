"""Agent-facing delivery choices must not promise unattended cloud publishing."""
import json

import pytest

from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.models import CampaignType, Platform, PlatformPost, Post
from socialctl.publisher import render_preview


runner = CliRunner()


def test_installed_guide_is_available_without_workspace_or_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ['agent-guide'])
    assert result.exit_code == 0, result.output
    assert 'socialcli account login' in result.stdout
    assert 'agent-guide publish' in result.stdout
    assert 'agent-guide inbox' in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_packaged_native_examples_support_preview_and_local_reconciliation(tmp_path):
    """Exercise the guide's schemas using fictional evidence, with no remote writes."""
    import hashlib
    import re
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from socialctl.native_schedule.store import NativeStore
    guide = runner.invoke(app, ['agent-guide','schedule']).stdout
    def example(name):
        section = guide.split(f'Example `{name}`', 1)[1]
        return json.loads(section.split('```json\n', 1)[1].split('```', 1)[0])
    brand = crear_brand(tmp_path, 'Example')
    (brand.raiz / 'accounts.yml').write_text('facebook:\n  page_id: page-example\n')
    (brand.raiz / 'clip.mp4').write_bytes(b'Fictional fixture, never uploaded')
    now = datetime.now(timezone.utc)
    inventory = example('native-calendar.json')
    inventory.update(observed_at=now.isoformat(), coverage_start=(now-timedelta(days=1)).isoformat(),
                     coverage_end=(now+timedelta(days=7)).isoformat())
    manifest = example('native.json')
    manifest.update(dispatch_after=(now-timedelta(minutes=1)).isoformat(),
        publish_at=(now.astimezone(ZoneInfo('Europe/Madrid'))+timedelta(days=2)).replace(hour=19,minute=30,second=0,microsecond=0).isoformat())
    for name, data in [('native-calendar.json', inventory), ('native.json', manifest),
                       ('operator-pilot.json', example('operator-pilot.json'))]:
        (brand.raiz / name).write_text(json.dumps(data))
    (brand.raiz / 'operator-pilot-evidence.txt').write_text('Fictional disabled pilot')
    def invoke(*args):
        return runner.invoke(app, ['native-schedule', *args, '--brand', 'Example', '--root', str(tmp_path)])
    preview = invoke('prepare', 'native.json', '--dry-run')
    assert preview.exit_code == 0, preview.output
    approval = re.search(r'Approval digest: ([a-f0-9]{64})', preview.stdout)[1]
    assert invoke('prepare', 'native.json', '--persist', '--approval-digest', approval).exit_code == 0
    assert invoke('approve', manifest['id'], '--approval-digest', approval).exit_code == 0
    blocked = invoke('submit-api', manifest['id'], '--eligibility', 'operator-pilot.json')
    assert json.loads(blocked.stdout)['state'] == 'ui_required'
    store = NativeStore(brand.raiz, brand='Example')
    assert store.get_attempts(manifest['id']) == []
    job = store.claim(manifest['id'], 'approved')
    store.mark_api_submitted(job.id, job.attempt_id, 'fictional-remote-id')
    evidence = b'Fictional calendar evidence for schema test only'
    (brand.raiz / 'calendar-evidence.png').write_bytes(evidence)
    observation = example('calendar-observation.json')
    observation.update(job_id=job.id, content_hash=job.content_hash, approval_digest=approval,
        remote_ref='fictional-remote-id', publish_at=job.publish_at.isoformat(),
        observed_at=datetime.now(timezone.utc).isoformat(), evidence_sha256=hashlib.sha256(evidence).hexdigest())
    (brand.raiz / 'calendar-observation.json').write_text(json.dumps(observation))
    result = invoke('reconcile', job.id, '--observation', 'calendar-observation.json')
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['state'] == 'native_scheduled'


def test_delivery_choices_distinguish_tiktok_inbox_from_excluded_direct(tmp_path):
    brand = crear_brand(tmp_path, 'Example')
    (brand.raiz / 'accounts.yml').write_text('tiktok:\n  open_id: creator-one\n')
    common = ['--brand', 'Example', '--root', str(tmp_path), '--json']
    result = runner.invoke(app, ['delivery-options', *common])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report['server_media_storage'] is False
    tiktok = report['platforms']['tiktok']
    assert tiktok['selected_delivery'] == 'creator_review'
    assert tiktok['automatic_native_schedule'] is False
    assert tiktok['direct_post_enabled'] is False
    assert report['platforms']['instagram']['automatic_native_schedule'] is False
    assert report['platforms']['facebook']['native_schedule_status'] == 'pilot_required'
    (brand.raiz / 'accounts.yml').write_text('tiktok:\n  open_id: creator-one\n  mode: direct\n  auditada: true\n')
    result = runner.invoke(app, ['delivery-options', *common])
    row = json.loads(result.stdout)['platforms']['tiktok']
    assert row['selected_delivery'] == 'excluded_direct_post'
    assert row['direct_post_enabled'] is False
    assert row['authorization_verified'] is False


def test_tiktok_complete_preview_discloses_human_finish_in_inbox(tmp_path):
    brand = crear_brand(tmp_path, 'Example')
    post = Post(slug='clip', brand='Example', campaign=CampaignType.CLIP_VERTICAL,
                platforms={Platform.TIKTOK: PlatformPost(platform=Platform.TIKTOK, body='Approved caption')})
    preview = render_preview(post, {}, brand=brand)
    assert 'creator review' in preview
    assert 'finish publishing in TikTok' in preview
    assert 'not automatically scheduled' in preview


def test_tiktok_direct_preview_cannot_look_like_inbox(tmp_path):
    brand = crear_brand(tmp_path, 'Example')
    brand.cuentas['tiktok'] = {'mode': 'direct', 'auditada': True}
    post = Post(slug='clip', brand='Example', campaign=CampaignType.CLIP_VERTICAL,
                platforms={Platform.TIKTOK: PlatformPost(platform=Platform.TIKTOK, body='Approved caption')})
    preview = render_preview(post, {}, brand=brand)
    assert 'Direct Post is disabled and excluded' in preview
    assert 'creator-reviewed inbox upload' in preview


def test_bootstrap_is_compact_and_topic_guides_work_offline(tmp_path,monkeypatch):
    from importlib.resources import files
    bootstrap=files('socialctl.ui').joinpath('assets/agents.md').read_text()
    assert len(bootstrap.encode())<=2000
    monkeypatch.chdir(tmp_path)
    for topic,expected in [('setup','credentials configure'),('publish','--dry-run'),('schedule','native-calendar.json'),('inbox','inbox'),('errors','action')]:
        result=runner.invoke(app,['agent-guide',topic])
        assert result.exit_code==0,result.output
        assert expected in result.stdout
    assert runner.invoke(app,['agent-guide','../../secrets']).exit_code==2
    assert not list(tmp_path.iterdir())


def test_instagram_preview_discloses_temporary_server_upload(tmp_path):
    brand=crear_brand(tmp_path,'Example')
    brand.guardar_secreto(Platform.INSTAGRAM,{'auth_mode':'broker','protocol':'saas-v1'})
    post=Post(slug='clip',brand='Example',campaign=CampaignType.CLIP_VERTICAL,
        platforms={Platform.INSTAGRAM:PlatformPost(platform=Platform.INSTAGRAM,body='Approved caption')})
    preview=render_preview(post,{},brand=brand)
    assert '15 minutes' in preview and 'temporary' in preview and 'florentino.pro' in preview


@pytest.mark.parametrize('auditada', [True, 'true', ' TRUE ', False, 'si'])
def test_public_tiktok_direct_is_excluded_without_token_or_network(tmp_path, monkeypatch, auditada):
    import httpx
    import yaml
    import socialctl.adapters.tiktok as tiktok
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('token-or-network')
        raise AssertionError('Direct Post must be refused before external access')
    monkeypatch.setattr(tiktok, 'obtener_token', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    brand = crear_brand(tmp_path, 'Fictional')
    (brand.raiz / 'accounts.yml').write_text(yaml.safe_dump({
        'tiktok': {'open_id': 'fictional-creator', 'mode': 'direct', 'auditada': auditada}}))
    common = ['--brand', 'Fictional', '--root', str(tmp_path)]
    options = runner.invoke(app, ['delivery-options', *common, '--json'])
    assert options.exit_code == 0, options.output
    row = json.loads(options.stdout)['platforms']['tiktok']
    assert row['direct_post_enabled'] is False
    assert row['selected_delivery'] == 'excluded_direct_post'
    assert row['delivery_status'] == 'excluded'
    folder = brand.dir_posts / 'fictional-clip'
    folder.mkdir()
    (folder / 'post.yml').write_text(yaml.safe_dump({
        'slug': 'fictional-clip', 'campaign': 'clip-vertical',
        'platforms': {'tiktok': {'body': 'Fictional caption'}}}))
    preview = runner.invoke(app, ['publish', 'fictional-clip', *common, '--only', 'tiktok', '--dry-run'])
    assert 'Direct Post is disabled and excluded' in preview.output
    assert 'creator-reviewed inbox upload' in preview.output
    assert calls == []
