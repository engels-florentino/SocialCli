"""Explicit preview, persistence and approval. No remote publishing adapters here."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import typer

from socialctl.brands import cargar_brand, _validar_nombre_de_marca
from socialctl.schedule_remote import DEFAULT_ROOT, is_remote, send_native
from .approval import approval_document, hash_value, media_hashes, reject_secrets, validate_payload, verify_approval
from .cadence import ZONE, ZONE_NAME, plan_slots, reservation_day
from .models import NativeJob, aware
from .store import NativeStore
from .inventory import validate_inventory, require_coverage

native_app = typer.Typer(help='Native scheduling: prepare MANIFEST --dry-run, prepare --persist --approval-digest DIGEST, approve ID --approval-digest DIGEST. See NATIVE-SCHEDULING.md.')
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
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError('Invalid native manifest schema; see NATIVE-SCHEDULING.md')
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
    requested = aware(datetime.fromisoformat(raw['publish_at'].replace('Z','+00:00')))
    reservation_day(requested, ZONE_NAME)
    dispatch = aware(datetime.fromisoformat(raw['dispatch_after'].replace('Z','+00:00')))
    store = NativeStore(brand.raiz,brand=brand.nombre)
    inventory = calendar_inventory(brand) if inventory is None else inventory
    calendars = validate_inventory(inventory,accounts=brand.cuentas,now=datetime.now(timezone.utc))
    if (platform,raw['account_id']) not in calendars:
        raise ValueError('Complete account calendar inventory required for destination')
    occupied = store.reserved_days(platform,raw['account_id']) | calendars[platform,raw['account_id']]
    final = plan_slots([requested],occupied=occupied)[0]
    require_coverage(inventory,final)
    payload['requested_publish_at'] = requested.isoformat()
    now = datetime.now(timezone.utc)
    job = NativeJob(id=raw['id'],brand=brand.nombre,platform=platform,account_id=raw['account_id'],
        media_hash=hash_value(media_hashes(payload)),content_hash=hash_value(payload),
        publish_at=final,timezone_name=ZONE_NAME,dispatch_after=dispatch,
        route=raw['route'],created_at=now,updated_at=now)
    return job,payload


def run(operation):
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
        send_native(root,brand,args)
        return None
    return cargar_brand(root,brand)


@native_app.command()
def prepare(manifest: str, brand: str = typer.Option(...,'--brand'),
            root: Path = typer.Option(DEFAULT_ROOT), dry_run: bool = typer.Option(False,'--dry-run'),
            persist: bool = typer.Option(False,'--persist'), approval_digest: str | None = typer.Option(None,'--approval-digest')):
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
        typer.echo(json.dumps(document,indent=2,ensure_ascii=False))
        typer.echo(f'Final local: {job.publish_at.astimezone(ZONE).isoformat()} {ZONE_NAME}')
        typer.echo(f'Final UTC: {job.publish_at.isoformat()}')
        typer.echo(f'Delivery: {job.dispatch_after.isoformat()}')
        typer.echo(f'Overflow: requested {payload["requested_publish_at"]}; final {job.publish_at.isoformat()}; item preserved')
        typer.echo(f'Approval digest: {hash_value(document)}')
    run(operation)


@native_app.command()
def approve(job_id: str, approval_digest: str = typer.Option(...,'--approval-digest'),
            brand: str = typer.Option(...,'--brand'),root: Path = typer.Option(DEFAULT_ROOT)):
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
    run(operation)


@native_app.command()
def status(brand: str = typer.Option(...,'--brand'),root: Path = typer.Option(DEFAULT_ROOT)):
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
    run(operation)



def artifact(chosen, name):
    component(name)
    path=(chosen.raiz/name).resolve()
    if not path.is_relative_to(chosen.raiz.resolve()):
        raise ValueError('Artifact must remain inside brand root')
    return path


def output(value):
    text=json.dumps(value,indent=2,ensure_ascii=False,default=str)
    reject_secrets(text)
    typer.echo(text)


@native_app.command()
def dispatch(job_id: str, brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), yes: bool = typer.Option(False,'--yes')):
    def operation():
        from .dispatcher import dispatch_ready
        component(job_id)
        chosen=selected(root,brand,['dispatch',job_id] + (['--yes'] if yes else []))
        if chosen is None: return
        store=NativeStore(chosen.raiz,brand=brand)
        if store.get(job_id) is None or not store.get(job_id).approval_digest: raise ValueError('Explicit approval required; --yes cannot bypass approval')
        verify_approval(store.get(job_id),store.get_payload(job_id))
        dispatch_ready(datetime.now(timezone.utc),brand,store=store,job_id=job_id)
        output({'job_id':job_id,'state':store.get(job_id).state,'notices':store.get_notices(job_id)})
    run(operation)


@native_app.command()
def handoff(job_id: str, brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT)):
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
    run(operation)


@native_app.command('submit-marker')
def submit_marker(job_id: str, brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT)):
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
    run(operation)


@native_app.command()
def reconcile(job_id: str, observation: str | None = typer.Option(None,'--observation'), brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT), yes: bool = typer.Option(False,'--yes')):
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
    run(operation)


def _register_action(kind):
    def command(job_id: str, yes: bool = typer.Option(False,'--yes'), publish_at: str | None = typer.Option(None,'--publish-at'), dry_run: bool = typer.Option(False,'--dry-run'), approval_digest: str | None = typer.Option(None,'--approval-digest'), brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT)):
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
            when=aware(datetime.fromisoformat(publish_at.replace('Z','+00:00'))) if publish_at else None
            if dry_run: output(action_preview(job_id,kind,store=store,publish_at=when))
            else: output(approve_action(job_id,approval_digest,kind=kind,store=store,publish_at=when))
        run(operation)
    native_app.command(kind)(command)


for _operation in ('cancel','reschedule'):
    _register_action(_operation)


@native_app.command('migration-prepare')
def migration_prepare(brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT),
                      manifest: str = typer.Option('native-migration.json','--manifest'),
                      dry_run: bool = typer.Option(False,'--dry-run')):
    def operation():
        from .migration import prepare_migration
        component(manifest)
        if not dry_run: raise ValueError('Migration preparation requires --dry-run')
        chosen=selected(root,brand,['migration-prepare','--manifest',manifest,'--dry-run'])
        if chosen is not None: output(prepare_migration(brand,root=root,manifest=manifest))
    run(operation)


@native_app.command('migration-apply')
def migration_apply(digest: str = typer.Option(...,'--digest'), brand: str = typer.Option(...,'--brand'),
                    root: Path = typer.Option(DEFAULT_ROOT), manifest: str = typer.Option('native-migration.json','--manifest')):
    def operation():
        from .migration import apply_migration
        component(manifest)
        chosen=selected(root,brand,['migration-apply','--manifest',manifest,'--digest',digest])
        if chosen is not None: output(apply_migration(digest,brand=brand,root=root,manifest=manifest))
    run(operation)


@native_app.command('migration-recover')
def migration_recover(digest: str = typer.Option(...,'--digest'), brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT)):
    def operation():
        from .migration import recover_migration
        chosen=selected(root,brand,['migration-recover','--digest',digest])
        if chosen is not None: output(recover_migration(digest,brand=brand,root=root))
    run(operation)


@native_app.command('deliver')
def deliver(brand: str = typer.Option(...,'--brand'), root: Path = typer.Option(DEFAULT_ROOT)):
    """Periodic worker: approved jobs only; no graphical session on the server."""
    def operation():
        from .dispatcher import dispatch_ready
        chosen=selected(root,brand,['deliver'])
        if chosen is None: return
        store=NativeStore(chosen.raiz,brand=brand)
        delivered=dispatch_ready(datetime.now(timezone.utc),brand,store=store)
        output({'delivered':delivered,'awaiting_ui':[j.id for j in store.list_jobs() if j.state=='awaiting_ui'],
                'instruction':'Official UI handoffs require an authorized operator session; API routes remain closed unless capability is enabled.'})
    run(operation)
