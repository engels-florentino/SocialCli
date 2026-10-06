"""Local authority handoff, never a remote write or a publication approval.

Prepare is read-only. Apply/recovery take executor then mutation locks. The old
recognized migration-active sentinel remains forever; old binaries refuse it.
An interrupted intent also blocks new legacy writers, even before the sentinel.
"""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re

from socialctl.brands import cargar_brand
from socialctl.migration_files import write_json, durable_write
from socialctl.scheduler import ScheduleStore, ScheduleEntry, ScheduleError, occurrence_id, now_utc
from socialctl.schedule_remote import DEFAULT_ROOT, is_remote, REMOTE_NOTICE
from .approval import hash_value, media_hashes, approval_document, reject_secrets, validate_payload
from .cadence import ZONE, ZONE_NAME, slot, plan_slots
from .models import NativeJob, aware
from .store import NativeStore
from .inventory import ACCOUNT_KEYS, INVENTORY_MAX_AGE, validate_inventory, require_coverage



def _checkpoint(point):
    """Crash-injection seam: called after every durable handoff boundary."""


def _date(value):
    return aware(datetime.fromisoformat(value.replace('Z','+00:00')))


def _path(brand_root, component):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',component):
        raise ValueError('Artifact must be a single safe component')
    path=(brand_root/component).resolve()
    if not path.is_relative_to(brand_root.resolve()):
        raise ValueError('Artifact escapes explicit brand root')
    return path


def _context(brand,root):
    from socialctl.brands import _validar_nombre_de_marca
    brand_root=_validar_nombre_de_marca(Path(root),brand)
    if is_remote(brand_root):
        raise ScheduleError(REMOTE_NOTICE)
    return cargar_brand(Path(root),brand)


def _digest(proposal):
    return hash_value({k:v for k,v in proposal.items() if k!='digest'})


def _intent_path(store,digest):
    if not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('Invalid migration digest')
    return store.root/'native-migrations'/digest/'intent.json'


def validate_committed_guard(store):
    """New capability recognizes only a fully durable native handoff guard."""
    try:
        guard=json.loads((store.root/'migration-active.json').read_bytes())
        if set(guard)!={'kind','digest'} or guard['kind']!='native-transfer-v1': return False
        intent=json.loads(_intent_path(store,guard['digest']).read_bytes())
        proposal=intent['proposal']
        if intent['status']!='committed' or _digest(proposal)!=guard['digest']: return False
        if proposal['brand_root']!=str(store.root.parent.resolve()): return False
        ledger=json.loads((store.root/'legacy-tombstones.json').read_bytes())
        store.tombstones()
        # Every completed batch remains protected after a later handoff replaces
        # the historical sentinel. Never validate only the most recent batch.
        for path in (store.root/'native-migrations').glob('*/intent.json'):
            older=json.loads(path.read_bytes())
            if older['status']!='committed': continue
            document=older['proposal']
            if (_digest(document)!=path.parent.name or document['brand_root']!=str(store.root.parent.resolve())
                    or not all(ledger['entries'].get(item['occurrence_id'],{}).get('entry')==item['original']
                               for item in document['protections'])):
                return False
        return True
    except (OSError,ValueError,TypeError,KeyError):
        return False


def _results(brand):
    """Keep local result bytes auditable; unreadable evidence must not allow upload."""
    found={}
    for path in sorted(brand.dir_posts.glob('*/resultado.json')):
        if not path.resolve().is_relative_to(brand.raiz.resolve()) or '.secrets' in path.resolve().parts:
            raise ValueError('Result evidence escapes brand root')
        data=json.loads(path.read_bytes())
        reject_secrets(data)
        found[str(path.relative_to(brand.raiz))]=data
    return found


def _has_result(results,slug,platform):
    path=f'posts/{slug}/resultado.json'
    if path not in results:
        return False
    data=results[path]
    # Presence is evidence even when JSON is null or its destination is unknown.
    # Never repair it here: absence and malformed evidence have different meaning.
    if isinstance(data,dict):
        containers=[key for key in ('resultados','results','platform') if key in data]
        if len(containers)!=1 or any(key in data for key in ACCOUNT_KEYS):
            return True  # Unknown/keyed/mixed historical shape: reconcile it.
        key=containers[0]
        rows=[data] if key=='platform' else data[key]
    elif isinstance(data,list):
        rows=data
    else:
        return True
    if not isinstance(rows,list):
        return True
    for row in rows:
        if (not isinstance(row,dict) or not isinstance(row.get('platform'),str)
                or row['platform'] not in ACCOUNT_KEYS):
            return True
        if row['platform']==platform:
            return True
    # Only unambiguously identified other destinations establish independence.
    return False


def prepare_migration(brand: str, *, root: Path=DEFAULT_ROOT,
                      manifest: str='native-migration.json') -> dict:
    chosen=_context(brand,root)
    old=ScheduleStore(chosen.raiz)
    old.assert_ready()
    raw=json.loads(_path(chosen.raiz,manifest).read_bytes())
    reject_secrets(raw)
    if set(raw)!={'version','brand','planned_at','inventory','candidates'} or raw['version']!=1 or raw['brand']!=brand:
        raise ValueError('Invalid migration manifest')
    now=now_utc()
    planned=_date(raw['planned_at'])
    inventory=raw['inventory']
    if planned>now:
        raise ValueError('Migration planning time is in the future')
    accounts=validate_inventory(inventory,accounts=chosen.cuentas,now=now)
    matches={item['occurrence_id'] for item in inventory['objects'] if item.get('occurrence_id')}
    entries=old.load()
    if any(e.brand!=brand for e in entries): raise ValueError('Queue brand mismatch')
    native=NativeStore(chosen.raiz,brand=brand)
    jobs=native.list_jobs()
    results=_results(chosen)
    indexed={(e.id,aware(e.created_at).isoformat()):e for e in entries}
    if len(indexed)!=len(entries): raise ValueError('Ambiguous legacy occurrence')
    candidates=[]; seen=set()
    for candidate in raw['candidates']:
        if set(candidate)!={'entry_id','created_at','route','payload'}: raise ValueError('Invalid migration candidate')
        identity=(candidate['entry_id'],_date(candidate['created_at']).isoformat())
        if identity in seen or identity not in indexed: raise ValueError('Missing or duplicate legacy occurrence')
        seen.add(identity); candidates.append((indexed[identity],candidate))
    # Preserve editorial grouping where available; each destination reserves independently.
    candidates.sort(key=lambda pair:(pair[0].scheduled_at,pair[0].slug,pair[0].platform,pair[0].created_at))
    transfers=[]; excluded=[]
    for entry,candidate in candidates:
        occurrence=occurrence_id(chosen.raiz,entry)
        original=entry.model_dump(mode='json')
        reason=None
        if entry.status!='approved' or entry.attempts or entry.platform_id: reason='legacy terminal/uncertain/attempted/remote reference'
        elif occurrence in matches or any(j.legacy_entry_id==occurrence for j in jobs): reason='already identified native object/job; reconcile only'
        elif _has_result(results,entry.slug,entry.platform): reason='existing result evidence; reconcile only'
        elif candidate['payload'].get('options',{}).get('video_id'): reason='existing payload remote target; reconcile only'
        if reason:
            excluded.append({'occurrence_id':occurrence,'original':original,'reason':reason}); continue
        p=entry.platform; account=(chosen.cuentas.get(p) or {}).get(ACCOUNT_KEYS.get(p,''))
        if (p,account) not in accounts: raise ValueError('Complete account calendar inventory required for every candidate')
        payload=json.loads(json.dumps(candidate['payload']))
        if not {'calendar','visibility','copy','options','first_comment','media'}<=set(payload): raise ValueError('Complete native payload required')
        if not all(isinstance(payload[k],str) and payload[k].strip() for k in ('calendar','visibility')) or not isinstance(payload['copy'],str): raise ValueError('Invalid calendar/copy/visibility')
        for asset in payload['media']:
            path=(chosen.raiz/asset['path']).resolve()
            if not path.is_relative_to(chosen.raiz.resolve()) or '.secrets' in path.parts: raise ValueError('Media escapes brand root')
            asset['path']=str(path)
        validate_payload(p,payload)
        if p in {'facebook','instagram'} and payload['options'].get('format')=='reel' and payload['options'].get('share_to_facebook_story') is not False:
            raise ValueError('Meta Reel requires explicit share_to_facebook_story=false; no extra destinations')
        reserved=accounts[p,account] | native.reserved_days(p,account)
        requested=slot(entry.scheduled_at.astimezone(ZONE).date())
        final=plan_slots([requested],occupied=reserved,now=now)[0]
        require_coverage(inventory,final)
        accounts[p,account].add(final.astimezone(ZONE).date().isoformat())
        payload['legacy_audit']={'occurrence_id':occurrence,'original_publish_at':entry.scheduled_at.isoformat(),'original_approval':entry.content_hash}
        job=NativeJob(id='migration-'+occurrence,legacy_entry_id=occurrence,brand=brand,platform=p,account_id=account,
            media_hash=hash_value(media_hashes(payload)),content_hash=hash_value(payload),publish_at=final,
            timezone_name=ZONE_NAME,dispatch_after=planned,route=candidate['route'],created_at=planned,updated_at=planned)
        document=approval_document(job,payload)
        transfers.append({'occurrence_id':occurrence,'original':original,'job':json.loads(job.model_dump_json()),'payload':payload,
            'native_preview':document,'native_approval_digest':hash_value(document),'final_local':final.astimezone(ZONE).isoformat(),
            'time_changed':entry.scheduled_at!=final})
    proposal={'version':1,'brand':brand,'brand_root':str(chosen.raiz.resolve()),'manifest':manifest,
        'manifest_hash':hash_value(raw),'inventory':inventory,'legacy_entries':[e.model_dump(mode='json') for e in entries],
        'legacy_results':results,'native_jobs':[json.loads(j.model_dump_json()) for j in jobs],
        'transfers':transfers,'excluded':excluded,
        'protections':[{'occurrence_id':occurrence_id(chosen.raiz,e),'original':e.model_dump(mode='json')} for e,_ in candidates],
        'instruction':'Transfer only: prepared, no reused legacy approval. Show complete native preview and obtain explicit new approval before delivery.'}
    proposal['digest']=_digest(proposal)
    return proposal


@contextmanager
def _locks(old):
    # Raw locks deliberately allow conservative recovery through an active guard.
    with old._file_lock(old.executor_lock_path,nonblocking=True), old._file_lock(old.mutation_lock_path,nonblocking=False):
        if is_remote(old.root.parent): raise ScheduleError(REMOTE_NOTICE)
        yield


def _preserve_committed_inventories(old, native):
    """Backfill all earlier committed batches when recovering/upgrading a brand."""
    for path in sorted((old.root/'native-migrations').glob('*/intent.json')):
        prior=json.loads(path.read_bytes())
        if prior['status']!='committed':
            continue
        document=prior['proposal']
        if (_digest(document)!=path.parent.name or document['brand']!=native.brand
                or document['brand_root']!=str(old.root.parent.resolve())):
            raise ValueError('Invalid prior inventory intent; retain protection and reconcile')
        native.record_inventory(document['inventory'])


def _complete(old, intent):
    proposal=intent['proposal']; digest=proposal['digest']
    intent_path=_intent_path(old,digest)
    guard=old.root/'migration-active.json'
    if guard.exists():
        previous=json.loads(guard.read_bytes())
        if previous!={'kind':'native-transfer-v1','digest':digest} and not validate_committed_guard(old):
            raise ValueError('Another unfinished guard owns this brand; do not remove it')
    write_json(guard,{'kind':'native-transfer-v1','digest':digest}); _checkpoint('guard')
    old.tombstones()
    ledger_path=old.root/'legacy-tombstones.json'
    ledger=json.loads(ledger_path.read_bytes()) if ledger_path.exists() else {'version':1,'entries':{}}
    for item in proposal['protections']:
        record={'entry':item['original'],'digest':digest}
        existing=ledger['entries'].get(item['occurrence_id'])
        if existing and existing['entry']!=record['entry']:
            raise ValueError('Tombstone occurrence changed; retain original protection')
        if not existing: ledger['entries'][item['occurrence_id']]=record
    write_json(ledger_path,ledger); _checkpoint('tombstones')
    native=NativeStore(old.root.parent,brand=proposal['brand'])
    # Preserve every batch before exposing any prepared jobs, including upgrades.
    _preserve_committed_inventories(old,native)
    native.record_inventory(proposal['inventory'])
    _checkpoint('reservations')
    for index,item in enumerate(proposal['transfers']):
        job=NativeJob.model_validate_json(json.dumps(item['job']))
        existing=native.get(job.id)
        if existing:
            if existing!=job or native.get_payload(job.id)!=item['payload']:
                raise ValueError('Native job advanced/changed; reconcile, never restore legacy')
        else:
            if hash_value(media_hashes(item['payload']))!=job.media_hash:
                raise ValueError('Media changed; keep tombstones and require operator review')
            native.prepare(job,payload=item['payload'],now=now_utc())
        _checkpoint(f'job:{index}')
    intent['status']='committed'
    write_json(intent_path,intent); _checkpoint('commit')
    return {'status':'committed','digest':digest,'prepared':[item['job']['id'] for item in proposal['transfers']],
            'publication_approved':False,'historical_guard_retained':True}


def apply_migration(digest: str, *, brand: str, root: Path=DEFAULT_ROOT,
                    manifest: str='native-migration.json') -> dict:
    chosen=_context(brand,root); old=ScheduleStore(chosen.raiz)
    with _locks(old):
        old.assert_ready()
        path=_intent_path(old,digest)
        if path.exists(): raise ValueError('Migration already attempted; use migration-recover')
        proposal=prepare_migration(brand,root=root,manifest=manifest)
        if proposal['digest']!=digest: raise ValueError('Preview changed; show complete new preview')
        if not proposal['protections']: raise ValueError('No occurrences selected for transfer/protection')
        # Consistent backups while both legacy locks are held. Native jobs are
        # independently CAS-protected and remain prepared throughout transfer.
        backup=path.parent/'backup'
        write_json(backup/'audit.json',proposal)
        if old.database_path.exists():
            from socialctl.schedule_sqlite import backup as sqlite_backup
            sqlite_backup(old.database_path,backup/'schedules.sqlite3')
            with (backup/'schedules.sqlite3').open('rb') as stream:
                import os
                os.fsync(stream.fileno())
            old._sync_directory(backup)
        elif old.path.exists(): durable_write(backup/'schedules.json',old.path.read_bytes())
        intent={'status':'applying','proposal':proposal}
        write_json(path,intent); _checkpoint('intent')
        return _complete(old,intent)


def recover_migration(digest: str, *, brand: str, root: Path=DEFAULT_ROOT) -> dict:
    chosen=_context(brand,root); old=ScheduleStore(chosen.raiz)
    with _locks(old):
        intent=json.loads(_intent_path(old,digest).read_bytes())
        proposal=intent['proposal']
        if _digest(proposal)!=digest or proposal['brand']!=brand or proposal['brand_root']!=str(chosen.raiz.resolve()):
            raise ValueError('Invalid recovery intent; keep writers stopped')
        if intent['status']=='committed':
            _preserve_committed_inventories(old,NativeStore(chosen.raiz,brand=brand))
            if not validate_committed_guard(old): raise ValueError('Committed protection missing; keep writers stopped')
            return {'status':'committed','digest':digest,'publication_approved':False}
        if intent['status']!='applying' or [e.model_dump(mode='json') for e in old.load()]!=proposal['legacy_entries']:
            raise ValueError('Legacy state changed; preserve guard/tombstones and reconcile')
        for item in proposal['transfers']:
            p=item['job']['platform']
            if (chosen.cuentas.get(p) or {}).get(ACCOUNT_KEYS[p])!=item['job']['account_id']:
                raise ValueError('Account changed; keep writers stopped')
        return _complete(old,intent)
