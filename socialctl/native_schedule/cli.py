"""Explicit preview/approval and account-gated local publication dispatch."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import typer

from socialctl.brands import cargar_brand, _validar_nombre_de_marca
from socialctl.schedule_remote import DEFAULT_ROOT, is_remote, send_native
from .approval import approval_document, hash_value, media_hashes, reject_secrets, validate_payload, verify_approval
from .cadence import ZONE, ZONE_NAME, plan_slots, reservation_day, exact_time
from .models import NativeJob, aware
from .store import NativeStore
from .report import NativeReport, active_report, bind_store, capture, begin_submission
from .inventory import validate_inventory, require_coverage

native_app = typer.Typer(help='Native scheduling: prepare MANIFEST --dry-run, prepare --persist --approval-digest DIGEST, approve ID --approval-digest DIGEST. See socialcli agent-guide for complete offline input examples.')
ACCOUNT_KEYS = {'youtube':'channel_id','facebook':'page_id','instagram':'ig_user_id','tiktok':'open_id'}


def component(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('Invalid native manifest/job identifier')
    return value


def calendar_inventory(brand):
    path = (brand.raiz / 'native-calendar.json').resolve()
    if not path.is_relative_to(brand.raiz.resolve()) or not path.is_file():
        raise ValueError('Fresh native-calendar.json required; inventory the official account calendar first')
    inventory = json.loads(path.read_text())
    validate_inventory(inventory,accounts=brand.cuentas,now=datetime.now(timezone.utc))
    return inventory


def proposal(brand, manifest: str, *, inventory=None):
    component(manifest)
    path = (brand.raiz / manifest).resolve()
    if not path.is_relative_to(brand.raiz.resolve()):
        raise ValueError('Manifest must remain in explicit brand root')
    raw = json.loads(path.read_text())
    reject_secrets(raw)
    required = {'id','platform','account_id','publish_at','dispatch_after','route','payload'}
    if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - {'schedule'}:
        raise ValueError('Invalid native manifest schema; see socialcli agent-guide')
    component(raw['id'])
    platform = raw['platform']
    if platform not in ACCOUNT_KEYS or raw['account_id'] != (brand.cuentas.get(platform) or {}).get(ACCOUNT_KEYS[platform]):
        raise ValueError('Account does not match explicit brand accounts.yml')
    payload = raw['payload']
    if not isinstance(payload, dict) or not {'calendar','visibility','copy','options','first_comment','media'} <= set(payload):
        raise ValueError('Payload requires calendar, visibility, copy, options, first_comment and media')
    if not all(isinstance(payload[key],str) for key in ('calendar','visibility','copy')) or not isinstance(payload['options'],dict):
        raise ValueError('Invalid payload fields')
    if not payload['calendar'].strip() or not payload['visibility'].strip() or not isinstance(payload['first_comment'],(str,type(None))):
        raise ValueError('Invalid calendar/visibility/comment')
    # Relative paths are frozen against the authoritative brand, never current cwd.
    if not isinstance(payload['media'],list):
        raise ValueError('Media list required')
    for item in payload['media']:
        if not isinstance(item,dict) or not isinstance(item.get('path'),str):
            raise ValueError('Media path and explicit origin required')
        item['path'] = str((brand.raiz / item['path']).resolve())
        if not Path(item['path']).is_relative_to(brand.raiz.resolve()) or '.secrets' in Path(item['path']).parts:
            raise ValueError('Media must remain inside brand root outside .secrets')
    validate_payload(platform, payload)
    schedule = raw.get('schedule')
    mode, zone = 'legacy_daily', ZONE_NAME
    if 'schedule' in raw:
        if (not isinstance(schedule, dict) or set(schedule) != {'mode', 'timezone'}
                or schedule['mode'] != 'exact' or not isinstance(schedule['timezone'],str)):
            raise ValueError('Schedule requires mode=exact and an explicit IANA timezone')
        mode, zone = schedule['mode'], schedule['timezone']
    entered = datetime.fromisoformat(raw['publish_at'].replace('Z','+00:00'))
    requested = exact_time(entered,zone) if mode == 'exact' else aware(entered)
    if mode == 'legacy_daily':
        reservation_day(requested, ZONE_NAME)
    dispatch = aware(datetime.fromisoformat(raw['dispatch_after'].replace('Z','+00:00')))
    store = NativeStore(brand.raiz,brand=brand.nombre)
    inventory = calendar_inventory(brand) if inventory is None else inventory
    now = datetime.now(timezone.utc)
    calendars = validate_inventory(inventory,accounts=brand.cuentas,now=now)
    if (platform,raw['account_id']) not in calendars:
        raise ValueError('Complete account calendar inventory required for destination')
    if mode == 'exact':
        if requested <= now:
            raise ValueError('Exact publication time must be in the future')
        final = requested
        for item in inventory['objects']:
            if (item['platform'] == platform and item['account_id'] == raw['account_id']
                    and aware(datetime.fromisoformat(item['publish_at'].replace('Z','+00:00'))) == final):
                raise ValueError('Exact publication time conflicts with external calendar evidence; choose another time')
    else:
        occupied = store.reserved_days(platform,raw['account_id']) | calendars[platform,raw['account_id']]
        final = plan_slots([requested],occupied=occupied)[0]
    require_coverage(inventory,final,zone)
    payload['requested_publish_at'] = requested.isoformat()
    job = NativeJob(id=raw['id'],brand=brand.nombre,platform=platform,account_id=raw['account_id'],
        media_hash=hash_value(media_hashes(payload)),content_hash=hash_value(payload),
        publish_at=final,timezone_name=zone,schedule_mode=mode,dispatch_after=dispatch,
        route=raw['route'],created_at=now,updated_at=now)
    if mode == 'exact':
        store.assert_available(job)
    return job,payload


def run(operation, *, json_output=False, command=None, brand=None):
    if json_output:
        with NativeReport(command, brand):
            operation()
        return
    try:
        operation()
    except typer.Exit:
        raise
    except Exception as exc:
        # Input/Pydantic/OS errors can contain the complete payload or credentials.
        if type(exc) is ValueError:
            message = str(exc)
            try:
                reject_secrets(message)
            except Exception:
                message = 'Invalid or unsafe native scheduling input'
        else:
            message = 'Native scheduling failed safely; check schema, paths, account and slot conflicts'
        typer.echo(message)
        raise typer.Exit(1) from exc


def selected(root, brand, args):
    brand_root = _validar_nombre_de_marca(root,brand)
    if is_remote(brand_root):
        report = active_report.get()
        if report is None:
            send_native(root,brand,args)
        else:
            # The SSH outcome is unknown until a bound envelope is received.
            if args[0] not in {'status', 'handoff', 'migration-prepare'}:
                begin_submission()
            report.remote = send_native(root,brand,[*args,'--json'])
        return None
    chosen = cargar_brand(root,brand)
    bind_store(NativeStore(chosen.raiz,brand=brand))
    return chosen


@native_app.command()
def prepare(manifest: str, brand: str = typer.Option(...,'--brand'),
            root: Path = typer.Option(DEFAULT_ROOT), dry_run: bool = typer.Option(False,'--dry-run'),
            persist: bool = typer.Option(False,'--persist'), approval_digest: str | None = typer.Option(None,'--approval-digest'), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        component(manifest)
        if dry_run == persist or (dry_run and approval_digest) or (persist and not approval_digest):
            raise ValueError('Use --dry-run OR --persist --approval-digest from the displayed preview')
        args = ['prepare',manifest] + (['--dry-run'] if dry_run else ['--persist','--approval-digest',approval_digest])
        chosen = selected(root,brand,args)
        if chosen is None:
            return
        inventory = calendar_inventory(chosen)
        job,payload = proposal(chosen,manifest,inventory=inventory)
        if persist:
            verify_approval(job,payload,approval_digest)
            NativeStore(chosen.raiz,brand=brand).prepare(job,payload=payload,inventory=inventory)
            typer.echo(f'Prepared: {job.id}; approval is still required')
            return
        document = approval_document(job,payload)
        capture(document)
        typer.echo(json.dumps(document,indent=2,ensure_ascii=False))
        typer.echo(f'Final local: {job.publish_at.astimezone(ZoneInfo(job.timezone_name)).isoformat()} {job.timezone_name}')
        typer.echo(f'Final UTC: {job.publish_at.isoformat()}')
        typer.echo(f'Delivery: {job.dispatch_after.isoformat()}')
        typer.echo(f'Schedule mode: {job.schedule_mode}')
        typer.echo(f'Overflow: requested {payload["requested_publish_at"]}; final {job.publish_at.isoformat()}; item preserved')
        typer.echo(f'Approval digest: {hash_value(document)}')
    run(operation, json_output=json_output, command='prepare', brand=brand)


@native_app.command()
def approve(job_id: str, approval_digest: str = typer.Option(...,'--approval-digest'),
            brand: str = typer.Option(...,'--brand'),root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        component(job_id)
        chosen = selected(root,brand,['approve',job_id,'--approval-digest',approval_digest])
        if chosen is None:
            return
        store = NativeStore(chosen.raiz,brand=brand)
        job = store.get(job_id)
        if job is None or job.state != 'prepared':
            raise ValueError('Existing prepared job required')
        verify_approval(job,store.get_payload(job_id),approval_digest)
        if not store.transition(job_id,'prepared','approved',approval_digest=approval_digest):
            raise ValueError('Job changed; show a new preview')
        typer.echo(f'Approved: {job_id}')
    run(operation, json_output=json_output, command='approve', brand=brand)


@native_app.command()
def status(brand: str = typer.Option(...,'--brand'),root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        chosen = selected(root,brand,['status'])
        if chosen is None:
            return
        from socialctl.publication_status import describe_native_job
        store=NativeStore(chosen.raiz,brand=brand)
        for job in store.list_jobs():
            text = f'{job.id} | {job.platform}/{job.account_id} | {describe_native_job(job,store.get_payload(job.id))}'
            reject_secrets(text)
            typer.echo(text)
    run(operation, json_output=json_output, command='status', brand=brand)



def artifact(chosen, name):
    component(name)
    path=(chosen.raiz/name).resolve()
    if not path.is_relative_to(chosen.raiz.resolve()):
        raise ValueError('Artifact must remain inside brand root')
    return path


def output(value):
    text=json.dumps(value,indent=2,ensure_ascii=False,default=str)
    reject_secrets(text)
    capture(value)
    typer.echo(text)


def eligibility_artifact(chosen, filename):
    relative = Path(filename)
    if relative.is_absolute() or not relative.parts:
        raise ValueError('Eligibility path must be relative to the brand')
    for part in relative.parts:
        component(part)
    path = (chosen.raiz / relative).resolve()
    if not path.is_relative_to(chosen.raiz.resolve()) or '.secrets' in path.parts:
        raise ValueError('Eligibility artifact must remain inside brand root outside secrets')
    return path


def api_eligibility(chosen, job, filename):
    from .facebook import FacebookEligibility
    from .youtube import YouTubeEligibility
    kind = {'facebook': FacebookEligibility, 'youtube': YouTubeEligibility}.get(job.platform)
    if kind is None:
        raise ValueError('Native API scheduling is not supported for this platform; no upload attempted')
    if filename is None:
        return kind()
    path = eligibility_artifact(chosen, filename)
    if path.stat().st_size > 16384:
        raise ValueError('Invalid eligibility file')
    data = json.loads(path.read_text())
    flags = ({'api_enabled', 'page_pilot_verified'} if job.platform == 'facebook'
             else {'api_enabled', 'project_verified', 'studio_pilot_verified'})
    if (not isinstance(data, dict) or set(data) != flags | {'version', 'platform', 'account_id', 'evidence_ref'}
            or type(data['version']) is not int or data['version'] != 1 or data['platform'] != job.platform
            or data['account_id'] != job.account_id or any(type(data[k]) is not bool for k in flags)):
        raise ValueError('Invalid or mismatched native API eligibility')
    reject_secrets(data)
    evidence = data['evidence_ref']
    if not isinstance(evidence, str) or not artifact(chosen, evidence).is_file() or not artifact(chosen, evidence).stat().st_size:
        raise ValueError('Native API eligibility requires the operator-recorded pilot evidence')
    return kind(**{k: v for k, v in data.items() if k not in {'version', 'platform'}})


@native_app.command('submit-api')
def submit_api(job_id: str, eligibility: str | None = typer.Option(None, '--eligibility'),
               brand: str = typer.Option(..., '--brand'), root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    """Submit one approved YouTube/Facebook native schedule with operator pilot evidence."""
    def operation():
        import httpx
        from socialctl.brands import cargar_brand
        component(job_id)
        # Media must stay on the client's executor. No implicit SSH or copying.
        if is_remote(_validar_nombre_de_marca(root, brand)):
            raise ValueError('Native submission requires the local customer workspace; no media transfer to the SaaS server')
        chosen = cargar_brand(root, brand)
        store = NativeStore(chosen.raiz, brand=brand)
        bind_store(store)
        job = store.get(job_id)
        if job is None or not job.approval_digest:
            raise ValueError('Existing job with explicit preview approval required')
        payload = store.get_payload(job_id)
        verify_approval(job, payload)
        proof = api_eligibility(chosen, job, eligibility)
        from .facebook import schedule_facebook
        from .youtube import schedule_youtube
        submit = schedule_facebook if job.platform == 'facebook' else schedule_youtube
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            begin_submission()
            result = submit(job, payload, brand=chosen, store=store, client=client, eligibility=proof)
        output({'job_id': job.id, 'state': result.state, 'remote_id': result.remote_id,
                'calendar_visible': result.calendar_visible, 'reason': result.reason,
                'server_media_storage': False})
        if result.state != 'pending_verification':
            raise typer.Exit(1)
    run(operation, json_output=json_output, command='submit-api', brand=brand)


def dispatch_local(chosen, store, *, job_id=None, eligibility=None, eligibility_dir=None):
    """Compose the existing native submitters; no new unguarded provider path."""
    import httpx
    from .dispatcher import dispatch_ready
    from .facebook import schedule_facebook, FacebookScheduleResult
    from .youtube import schedule_youtube
    directory = eligibility_artifact(chosen, eligibility_dir) if eligibility_dir else None
    if directory is not None and not directory.is_dir():
        raise ValueError('Eligibility directory is unavailable')
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        def delivery_context(job, payload):
            if job.platform != 'instagram':
                raise ValueError('This at-time API route is not enabled')
            from .instagram_runtime import delivery_context as instagram_context
            filename = str((directory / 'instagram.json').relative_to(chosen.raiz.resolve())) if directory else eligibility
            path = eligibility_artifact(chosen, filename) if filename else None
            return instagram_context(chosen, job, payload, path=path,
                evidence_path=lambda value: eligibility_artifact(chosen, value), client=client,
                clock=lambda: datetime.now(timezone.utc))
        def submit(job, payload):
            if job.platform not in {'facebook', 'youtube'}:
                return FacebookScheduleResult(reason='No supported native API submission for this platform')
            filename = eligibility
            if directory is not None:
                filename = str((directory / f'{job.platform}.json').relative_to(chosen.raiz.resolve()))
            proof = api_eligibility(chosen, job, filename)
            begin_submission()
            handler = schedule_facebook if job.platform == 'facebook' else schedule_youtube
            return handler(job, payload, brand=chosen, store=store, client=client, eligibility=proof)
        return dispatch_ready(datetime.now(timezone.utc), chosen.nombre, store=store,
                              job_id=job_id, native_submit=submit, delivery_context=delivery_context)


@native_app.command()
def dispatch(job_id: str, brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), yes: bool = typer.Option(False,'--yes'), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.'), eligibility: str | None = typer.Option(None, '--eligibility')):
    def operation():
        component(job_id)
        chosen=selected(root,brand,['dispatch',job_id] + (['--yes'] if yes else []) + (['--eligibility',eligibility] if eligibility else []))
        if chosen is None: return
        store=NativeStore(chosen.raiz,brand=brand)
        if store.get(job_id) is None or not store.get(job_id).approval_digest: raise ValueError('Explicit approval required; --yes cannot bypass approval')
        verify_approval(store.get(job_id),store.get_payload(job_id))
        dispatch_local(chosen,store,job_id=job_id,eligibility=eligibility)
        output({'job_id':job_id,'state':store.get(job_id).state,'notices':store.get_notices(job_id)})
        if store.get(job_id).state in {'blocked', 'uncertain'}:
            raise typer.Exit(1)
    run(operation, json_output=json_output, command='dispatch', brand=brand)


@native_app.command()
def handoff(job_id: str, brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        from .handoff import prepare_handoff
        component(job_id)
        chosen=selected(root,brand,['handoff',job_id])
        if chosen is None: return
        store=NativeStore(chosen.raiz,brand=brand)
        job=store.get(job_id)
        if job is None: raise ValueError('Unknown job')
        action=store.get_action(job_id)
        if action and action['status'] != 'confirmed':
            output({'action':action,'upload_allowed':False,'instruction':'Submit marker BEFORE final official UI confirmation; submitted means readback only'})
        else:
            output(prepare_handoff(job,store.get_payload(job_id),store=store))
    run(operation, json_output=json_output, command='handoff', brand=brand)


@native_app.command('submit-marker')
def submit_marker(job_id: str, brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        from .handoff import mark_ui_submit_started
        from .reconcile import mark_action_submit
        component(job_id)
        chosen=selected(root,brand,['submit-marker',job_id])
        if chosen is None: return
        store=NativeStore(chosen.raiz,brand=brand)
        action=store.get_action(job_id)
        if action and action['status'] != 'confirmed': mark_action_submit(job_id,store=store)
        else: mark_ui_submit_started(job_id,store=store)
        typer.echo('Submit boundary persisted. Confirm once in official UI; then import fresh readback. Never retry blindly.')
    run(operation, json_output=json_output, command='submit-marker', brand=brand)


@native_app.command()
def reconcile(job_id: str, observation: str | None = typer.Option(None,'--observation'), brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), yes: bool = typer.Option(False,'--yes'), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        from .models import NativeObservation
        from .reconcile import reconcile as import_readback
        component(job_id)
        args=['reconcile',job_id]
        if observation: component(observation); args += ['--observation',observation]
        chosen=selected(root,brand,args)
        if chosen is None: return
        store=NativeStore(chosen.raiz,brand=brand)
        job=store.get(job_id)
        if job is None or not job.approval_digest: raise ValueError('Explicit approval required')
        if observation:
            obs=NativeObservation.model_validate_json(artifact(chosen,observation).read_text())
            evidence=Path(obs.evidence_path)
            if not evidence.is_absolute():
                obs=NativeObservation.model_validate(obs.model_dump() | {'evidence_path':str(artifact(chosen,obs.evidence_path))})
            if not Path(obs.evidence_path).resolve().is_relative_to(chosen.raiz.resolve()) or '.secrets' in Path(obs.evidence_path).parts:
                raise ValueError('Evidence must remain inside brand root outside secrets')
            import_readback(job_id,store=store,observation=obs)
        output({'job_id':job_id,'state':store.get(job_id).state,'observations':[o.model_dump(mode='json') for o in store.get_observations(job_id)],'notices':store.get_notices(job_id),'instruction':'Fresh official UI attestation required; this command never writes remotely'})
    run(operation, json_output=json_output, command='reconcile', brand=brand)


def _register_action(kind):
    def command(job_id: str, yes: bool = typer.Option(False,'--yes'), publish_at: str | None = typer.Option(None,'--publish-at'), dry_run: bool = typer.Option(False,'--dry-run'), approval_digest: str | None = typer.Option(None,'--approval-digest'), brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
        def operation():
            from .reconcile import action_preview, approve_action
            component(job_id)
            if dry_run == bool(approval_digest): raise ValueError('Use --dry-run OR --approval-digest from complete action preview')
            args=[kind,job_id]
            if publish_at: args += ['--publish-at',publish_at]
            args += ['--dry-run'] if dry_run else ['--approval-digest',approval_digest]
            chosen=selected(root,brand,args)
            if chosen is None: return
            store=NativeStore(chosen.raiz,brand=brand)
            when=datetime.fromisoformat(publish_at.replace('Z','+00:00')) if publish_at else None
            if dry_run: output(action_preview(job_id,kind,store=store,publish_at=when))
            else: output(approve_action(job_id,approval_digest,kind=kind,store=store,publish_at=when))
        run(operation, json_output=json_output, command=kind, brand=brand)
    native_app.command(kind)(command)


for _operation in ('cancel','reschedule'):
    _register_action(_operation)


@native_app.command('migration-prepare')
def migration_prepare(brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT),
                      manifest: str = typer.Option('native-migration.json','--manifest'),
                      dry_run: bool = typer.Option(False,'--dry-run'), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        from .migration import prepare_migration
        component(manifest)
        if not dry_run: raise ValueError('Migration preparation requires --dry-run')
        chosen=selected(root,brand,['migration-prepare','--manifest',manifest,'--dry-run'])
        if chosen is not None: output(prepare_migration(brand,root=root,manifest=manifest))
    run(operation, json_output=json_output, command='migration-prepare', brand=brand)


@native_app.command('migration-apply')
def migration_apply(digest: str = typer.Option(...,'--digest'), brand: str = typer.Option(...,'--brand'),
                    root: Path = typer.Option(DEFAULT_ROOT), manifest: str = typer.Option('native-migration.json','--manifest'), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        from .migration import apply_migration
        component(manifest)
        chosen=selected(root,brand,['migration-apply','--manifest',manifest,'--digest',digest])
        if chosen is not None: output(apply_migration(digest,brand=brand,root=root,manifest=manifest))
    run(operation, json_output=json_output, command='migration-apply', brand=brand)


@native_app.command('migration-recover')
def migration_recover(digest: str = typer.Option(...,'--digest'), brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.')):
    def operation():
        from .migration import recover_migration
        chosen=selected(root,brand,['migration-recover','--digest',digest])
        if chosen is not None: output(recover_migration(digest,brand=brand,root=root))
    run(operation, json_output=json_output, command='migration-recover', brand=brand)


@native_app.command('deliver')
def deliver(brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), json_output: bool = typer.Option(False, '--json', help='One structured native result; preview on stderr.'), eligibility_dir: str | None = typer.Option(None, '--eligibility-dir')):
    """Periodic worker: approved jobs only; no graphical session on the server."""
    def operation():
        chosen=selected(root,brand,['deliver'] + (['--eligibility-dir',eligibility_dir] if eligibility_dir else []))
        if chosen is None: return
        store=NativeStore(chosen.raiz,brand=brand)
        delivered=dispatch_local(chosen,store,eligibility_dir=eligibility_dir)
        problems=[j.id for j in store.list_jobs() if j.state in {'blocked', 'uncertain'}]
        output({'delivered':delivered,'awaiting_ui':[j.id for j in store.list_jobs() if j.state=='awaiting_ui'],
                'needs_attention':problems,
                'instruction':'Official UI handoffs require an authorized operator session; API routes remain closed unless capability is enabled.'})
        if problems:
            raise typer.Exit(1)
    run(operation, json_output=json_output, command='deliver', brand=brand)
