"""Conservative local YouTube API data expiry, with replay-blocking tombstones.

Only enumerated application stores are managed. User files and Studio imports
are excluded. No process can erase files while its host is stopped.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from socialctl.migration_files import write_json
from socialctl.brands import AccountsInvalido


class YouTubeDataCleanupError(AccountsInvalido, ValueError):
    """Managed provider data could not be removed; access remains blocked."""

MAX_AGE_DAYS = 6
JOURNALS = ('changes', 'metadata-batches', 'youtube-resources', 'youtube-owned',
            'youtube-community', 'community-batches', 'youtube-analytics-changes')


def expired(value, now):
    """Unknown ages are not permission to keep API data indefinitely."""
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp <= now - timedelta(days=MAX_AGE_DAYS)
    except (AttributeError, TypeError, ValueError):
        return True


def _age(data):
    return data.get('created_at') or data.get('first_observed_at') or data.get('observed_at')


def _safe(path, root):
    path.relative_to(root)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Symlink in managed data path; cleanup blocked')
        if part == root:
            break
    return path


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _tombstone(data):
    # Supplied editable text/file paths survive, but no before/after/API receipt.
    result = {'data_lifecycle':'purged', 'replay_blocked':True,
            'id':data.get('id'), 'platform':'youtube',
            'prior_state':data.get('status', data.get('state', 'unknown')),
            'fingerprint':data.get('fingerprint'),
            'user_input':{k:data[k] for k in ('edit','patch','file','slug','text') if k in data}}
    if 'children' in data:
        result['preserved_other_platforms']=[r for r in data['children'] if r.get('provider')!='youtube-community']
        ids={r.get('change_id') for r in result['preserved_other_platforms']}
        result['preserved_outcomes']={k:v for k,v in data.get('outcomes',{}).items() if k in ids}
    return result



def _native_cleanup(path, *, now, purge):
    """Use one write transaction; keep occupied slots while removing receipts."""
    touched=[]
    with sqlite3.connect(path, timeout=1) as db:
        db.execute('PRAGMA secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return []
        for ident,raw in db.execute("SELECT id,data FROM jobs WHERE platform='youtube'").fetchall():
            data=json.loads(raw)
            has_receipt=bool(data.get('remote_id'))
            if 'attempts' in tables:
                has_receipt=has_receipt or bool(db.execute('SELECT 1 FROM attempts WHERE job_id=?',(ident,)).fetchone())
            if 'observations' in tables:
                observations=db.execute('SELECT rowid,data FROM observations WHERE job_id=?',(ident,)).fetchall()
                has_receipt=has_receipt or bool(observations)
                for rowid,observation in observations:
                    if purge or expired(json.loads(observation).get('observed_at'),now):
                        db.execute('DELETE FROM observations WHERE rowid=?',(rowid,));touched.append(ident)
            if 'attempts' in tables:
                columns={row[1] for row in db.execute('PRAGMA table_info(attempts)')}
                stamp_column='started_at' if 'started_at' in columns else 'NULL'
                for rowid,stamp,checkpoint in db.execute('SELECT rowid,'+stamp_column+',checkpoint FROM attempts WHERE job_id=?',(ident,)).fetchall():
                    prior=json.loads(checkpoint)
                    if prior.get('data_lifecycle')!='purged' and (purge or expired(stamp or data.get('created_at'),now)):
                        db.execute('UPDATE attempts SET checkpoint=? WHERE rowid=?',(json.dumps({'data_lifecycle':'purged','replay_blocked':True}),rowid));touched.append(ident)
            for table in ('actions','action_events'):
                if table not in tables:continue
                for rowid,raw_action in db.execute('SELECT rowid,data FROM '+table+' WHERE job_id=?',(ident,)).fetchall():
                    action=json.loads(raw_action)
                    if action.get('data_lifecycle')=='purged':continue
                    prior_job=json.loads(action['job']) if isinstance(action.get('job'),str) else {}
                    stamp=action.get('created_at') or action.get('submitted_at') or prior_job.get('updated_at')
                    if purge or expired(stamp,now):
                        if table=='action_events':db.execute('DELETE FROM action_events WHERE rowid=?',(rowid,))
                        else:db.execute('UPDATE actions SET data=? WHERE rowid=?',(json.dumps({'data_lifecycle':'purged','replay_blocked':True,'status':'expired'}),rowid))
                        touched.append(ident)
            if 'notices' in tables:
                for rowid,stamp in db.execute('SELECT rowid,created_at FROM notices WHERE job_id=?',(ident,)).fetchall():
                    if purge or expired(stamp,now):db.execute('DELETE FROM notices WHERE rowid=?',(rowid,));touched.append(ident)
            if not purge and not has_receipt:continue  # Supplied future intent is not an API cache.
            if data.get('state')=='blocked' and data.get('route')=='blocked' and data.get('remote_id') is None:continue
            if not (purge or expired(data.get('observation_watermark') or data.get('created_at'),now)):continue
            data.update(state='blocked',route='blocked',approval_digest=None,remote_id=None,
                        observation_watermark=None,reconcile_after=None)
            db.execute('UPDATE jobs SET data=? WHERE id=?',(json.dumps(data),ident))
            if 'attempts' in tables:
                db.execute('UPDATE attempts SET checkpoint=? WHERE job_id=?',(json.dumps({'data_lifecycle':'purged','replay_blocked':True}),ident))
            for table in ('observations','actions','action_events','notices'):
                if table in tables:db.execute('DELETE FROM '+table+' WHERE job_id=?',(ident,))
            touched.append(ident)
        if 'calendar_inventory' in tables:
            for digest,raw in db.execute('SELECT digest,data FROM calendar_inventory').fetchall():
                data=json.loads(raw)
                if purge or expired(data.get('observed_at'),now):
                    old=json.dumps(data,sort_keys=True)
                    data['objects']=[r for r in data.get('objects',[]) if r.get('platform')!='youtube']
                    for account in data.get('accounts',[]):
                        if account.get('platform')=='youtube':account['complete']=False
                    if json.dumps(data,sort_keys=True)!=old:
                        db.execute('UPDATE calendar_inventory SET data=? WHERE digest=?',(json.dumps(data),digest));touched.append('calendar')
                        if 'external_reservations' in tables:
                            rows=db.execute("SELECT rowid,remote_ref FROM external_reservations WHERE platform='youtube' AND inventory_digest=?",(digest,)).fetchall()
                            for rowid,remote in rows:
                                if not remote.startswith('purged:'):
                                    db.execute('UPDATE external_reservations SET remote_ref=? WHERE rowid=?',('purged:'+hashlib.sha256(remote.encode()).hexdigest(),rowid))
        db.commit()
        if touched:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
    return touched



def _legacy_rows(value, *, now, purge, inherited=False, parent_stamp=None):
    """Retain supplied plans and other platforms, invalidate old YouTube receipts."""
    if isinstance(value,list):
        return [_legacy_rows(v,now=now,purge=purge,inherited=inherited,parent_stamp=parent_stamp) for v in value]
    if not isinstance(value,dict):return value
    platform=value.get('platform')
    youtube=(platform=='youtube') if platform is not None else inherited
    # An unsent schedule is user intent, not an API observation. Its age alone
    # must never cancel approval, alter supplied content, or stop future delivery.
    # Explicit disconnect/purge still blocks every YouTube execution path.
    receipt_fields=('platform_id','remote_id','last_error','receipt','observed',
                    'before','after','result','error','remote_ref')
    has_receipt=(any(value.get(field) for field in receipt_fields)
                 or bool(value.get('attempts'))
                 or value.get('status',value.get('state')) in {
                     'running','sending','uncertain','published','native_scheduled',
                     'submitted','confirmed','failed'})
    if (youtube and not purge and not has_receipt
            and ('scheduled_at' in value or 'publish_at' in value)):
        return value
    inventory=value.get('inventory',{})
    proposal=value.get('proposal',{})
    previous_inventory=proposal.get('inventory',{}) if isinstance(proposal,dict) else {}
    stamp=(_age(value) or (inventory.get('observed_at') if isinstance(inventory,dict) else None)
           or (previous_inventory.get('observed_at') if isinstance(previous_inventory,dict) else None) or parent_stamp)
    stale=purge or expired(stamp,now)
    result={k:_legacy_rows(v,now=now,purge=purge,inherited=youtube,parent_stamp=stamp) for k,v in value.items()}
    if youtube and stale:
        for field in ('platform_id','remote_id','url','last_error','receipt','observed','before','after','result','error','remote_ref'):
            if field in result:result[field]=None
        if 'status' in result:result['status']='blocked'
        if 'state' in result:result['state']='blocked'
        for field in ('content_hash','approval_digest'):
            if field in result:result[field]=None
    return result


def _legacy_database(path, *, now, purge):
    changed=False
    with sqlite3.connect(path,timeout=1) as db:
        db.execute('PRAGMA secure_delete=ON');db.execute('BEGIN IMMEDIATE')
        for position,raw in db.execute('SELECT position,payload FROM entries').fetchall():
            data=json.loads(raw);cleaned=_legacy_rows(data,now=now,purge=purge)
            if cleaned!=data:
                db.execute('UPDATE entries SET payload=? WHERE position=?',(json.dumps(cleaned),position));changed=True
        db.commit()
        if changed:db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.execute('VACUUM')
    return changed


def cleanup_youtube_data(brand_root, *, now=None, purge=False):
    """Expire managed API data for one explicit brand; purge ignores the age.

    Failures are returned individually, never mistaken for successful deletion.
    Uncertain journals become unreadable to execution paths, preserving a guard.
    """
    root = Path(brand_root).absolute()
    now = now or datetime.now(timezone.utc)
    marker = root/'.socialctl/youtube-purge-required.json'
    result = {'version':1,'complete':True,'changed':[], 'errors':[], 'manual_review':[],
              'max_age_days':MAX_AGE_DAYS, 'purge':purge,
              'limits':'Runs only while this client/cleanup job runs. External exports, chats, user files and independent backups are outside this managed store.'}
    def fail(path, exc):
        result['complete'] = False
        result['errors'].append({'path':str(path.relative_to(root)), 'error':type(exc).__name__})
    try:
        _safe(marker,root)
    except ValueError as exc:
        fail(marker,exc)
        return result
    purge=purge or marker.exists()
    result['purge']=purge
    def changed(path):
        result['changed'].append(str(path.relative_to(root)))
    def read(path):
        return json.loads(_safe(path, root).read_text())
    def save(path, data):
        write_json(_safe(path, root), data); changed(path)
    def remove(path):
        if _safe(path,root).exists(): path.unlink(); changed(path)
    def files(directory, pattern):
        try:
            _safe(directory,root)
            return list(directory.glob(pattern)) if directory.exists() else []
        except (OSError,ValueError) as exc:
            fail(directory,exc); return []
    def run(path, operation):
        try: operation()
        except (OSError,ValueError,KeyError,TypeError,AttributeError,sqlite3.Error,yaml.YAMLError) as exc: fail(path,exc)

    metrics = root/'metricas'
    for path in files(metrics,'????-??-??.json'):
        def snapshot():
            data=read(path); row=data.get('redes',{}).get('youtube')
            if row is not None and (purge or expired(row.get('observed_at') or data.get('fecha'),now)):
                del data['redes']['youtube']; save(path,data)
        run(path,snapshot)
    path=metrics/'piezas.yml'
    if path.exists() or path.is_symlink():
        def pieces():
            rows=yaml.safe_load(_safe(path,root).read_text())
            if not isinstance(rows,list): raise ValueError('Invalid editorial store')
            altered=False
            for row in rows:
                if isinstance(row,dict) and row.get('red')=='youtube' and (purge or expired(row.get('api_observed_at'),now)):
                    for field in ('id','titulo','tipo','publicado_el','duracion_seg','api_observed_at'):
                        if field in row: del row[field]; altered=True
                    row['api_data_removed']=True
            if altered:
                from socialctl.metricas.almacen import escribir_atomico
                escribir_atomico(path,yaml.safe_dump(rows,allow_unicode=True,sort_keys=False)); changed(path)
        run(path,pieces)
    path=metrics/'resumen.md'
    if path.exists() or path.is_symlink():
        def summary():
            text=_safe(path,root).read_text()
            # Generated summaries do not record per-section provenance; remove the
            # YouTube section on every lifecycle pass and regenerate on fresh stats.
            cleaned=re.sub(r'^## youtube\n.*?(?=^## |\Z)', '', text, flags=re.M|re.S)
            if cleaned!=text:
                from socialctl.metricas.almacen import escribir_atomico
                escribir_atomico(path,cleaned);changed(path)
        run(path,summary)

    path=root/'.socialctl/unified-inbox/state.json'
    if path.exists() or path.is_symlink():
        def inbox():
            with _lock(_safe(root/'.socialctl/comment-inbox/state.lock',root)):
                data=read(path); old=json.dumps(data,sort_keys=True)
                data['items']={k:r for k,r in data['items'].items() if r.get('platform')!='youtube' or not (purge or expired(r.get('first_observed_at'),now))}
                data['watermarks']={k:v for k,v in data['watermarks'].items() if json.loads(k)[0]!='youtube' or not (purge or expired(v,now))}
                if json.dumps(data,sort_keys=True)!=old: save(path,data)
        run(path,inbox)
    for path in files(root/'.socialctl/inventory/youtube','*/snapshot.json'):
        def inventory():
            with _lock(_safe(path.with_name('sync.lock'),root)):
                data=read(path); old=json.dumps(data,sort_keys=True)
                data['items']={k:r for k,r in data.get('items',{}).items() if not (purge or expired(r.get('first_observed_at'),now))}
                sync=data.get('sync')
                if sync and (purge or expired(sync.get('started_at'),now)): data['sync']=None
                if json.dumps(data,sort_keys=True)!=old: save(path,data)
        run(path,inventory)

    observations=root/'.socialctl/youtube-observations'
    def observation_store():
        import uuid
        lock_name=str(uuid.uuid5(uuid.NAMESPACE_URL,'youtube-observations-index'))+'.lock'
        # Match ingestion's lock across file removal and index replacement, so a
        # concurrent supplied Studio import cannot lose its committed index entry.
        with _lock(_safe(observations/lock_name,root)):
            removed=set()
            for path in files(observations,'*.json'):
                if path.name=='index.json': continue
                def observation():
                    data=read(path); provenance=data.get('provenance',{})
                    if provenance.get('source')=='youtubeStudio':return
                    if purge or expired(provenance.get('observed_at'),now):
                        remove(path.with_suffix('.csv'));remove(path);removed.add(path.stem)
                run(path,observation)
            index=observations/'index.json'
            if removed and index.exists():
                def checkpoint():
                    data=read(index)
                    data['imports']={k:v for k,v in data['imports'].items() if k not in removed}
                    data['active']={k:v for k,v in data['active'].items() if v not in removed}
                    save(index,data)
                run(index,checkpoint)
            for path in files(observations,'*.csv'):
                if not path.with_suffix('.json').exists():
                    # Unclassified orphan bytes could be supplied Studio input: fail closed.
                    fail(path,ValueError('Orphan CSV requires provenance review'))
    if observations.exists() or observations.is_symlink():run(observations,observation_store)

    for folder in (*JOURNALS,'publications'):
        for path in files(root/'.socialctl'/folder,'*.json'):
            def journal():
                with _lock(_safe(path.with_suffix('.lock'),root)):
                    data=read(path)
                    if data.get('data_lifecycle')=='purged': return
                    if folder=='publications' and data.get('platform')!='youtube':return
                    if folder=='community-batches' and not any(r.get('provider')=='youtube-community' for r in data.get('children',[])):return
                    if purge or expired(_age(data),now):
                        save(path,_tombstone(data));result['manual_review'].append(str(path.relative_to(root)))
                        remove(path.with_suffix('.caption-original'))
            run(path,journal)
    for path in files(root/'.socialctl/youtube-resources','*.caption-original'):
        if not path.with_suffix('.json').exists(): run(path,lambda:remove(path))

    # Result files are mixed-platform retry evidence. Keep a permanent uncertain
    # YouTube row after removing remote IDs, URLs, errors and observations.
    for path in files(root/'posts','*/resultado.json'):
        def publication_result():
            data=read(path); altered=False
            if data is None:return  # No provider data; existing ambiguity guard remains.
            if isinstance(data,list):rows=data;stamp=None
            elif isinstance(data,dict):
                rows=data.get('resultados',data.get('results',[data] if 'platform' in data else []));stamp=data.get('actualizado')
            else:raise ValueError('Invalid result evidence')
            for i,row in enumerate(rows):
                if not isinstance(row,dict):raise ValueError('Invalid result row')
                if row.get('platform')=='youtube' and not row.get('api_data_removed') and (purge or expired(row.get('fecha') or stamp,now)):
                    clean={'platform':'youtube','status':'pendiente_confirmacion','riesgo_duplicado':True,
                        'api_data_removed':True,'error':'API data expired; reconcile manually before any new upload.'}
                    if rows==[data] and isinstance(data,dict) and 'platform' in data:data=clean
                    else:rows[i]=clean
                    altered=True
            if altered:save(path,data)
        run(path,publication_result)
    path=root/'historial.md'
    if path.exists() or path.is_symlink():
        def history():
            text=_safe(path,root).read_text()
            cleaned=re.sub(r'^- youtube:.*?(?=^- (?:youtube|facebook|instagram|tiktok):|^## |\Z)', '- youtube: API receipt removed; do not infer permission to repeat the upload.\n',text,flags=re.M|re.S)
            if cleaned!=text:
                from socialctl.metricas.almacen import escribir_atomico
                escribir_atomico(path,cleaned);changed(path)
        run(path,history)
    # Enumerated migration backups remain protected against replay: altered
    # fingerprints fail recovery rather than restoring erased provider records.
    archives=[]
    for directory in (root/'.socialctl/native-migrations',root/'.socialctl/migrations'):
        archives.extend(files(directory,'**/*.json'))
    for name in ('schedules.json','legacy-tombstones.json'):
        current=root/'.socialctl'/name
        if current.exists() or current.is_symlink():archives.append(current)
    for path in archives:
        def archive():
            data=read(path);cleaned=_legacy_rows(data,now=now,purge=purge)
            if cleaned!=data:
                with _lock(_safe(root/'.socialctl/schedules.lock',root)):
                    data=read(path);cleaned=_legacy_rows(data,now=now,purge=purge)
                    if cleaned!=data:save(path,cleaned);result['manual_review'].append(str(path.relative_to(root)))
        run(path,archive)
    databases=[]
    for directory in (root/'.socialctl/native-migrations',root/'.socialctl/migrations'):
        databases.extend(files(directory,'**/schedules.sqlite3'))
    current=root/'.socialctl/schedules.sqlite3'
    if current.exists() or current.is_symlink():databases.append(current)
    for path in databases:
        def database():
            with sqlite3.connect(_safe(path,root).as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM entries')]
            if not any(row!=_legacy_rows(row,now=now,purge=purge) for row in rows):return
            with _lock(_safe(root/'.socialctl/schedules.lock',root)):
                if _legacy_database(_safe(path,root),now=now,purge=purge):
                    changed(path);result['manual_review'].append(str(path.relative_to(root)))
        run(path,database)
    path=root/'native-schedules.sqlite3'
    if path.exists() or path.is_symlink():
        def native():
            if _native_cleanup(_safe(path,root),now=now,purge=purge):
                changed(path);result['manual_review'].append(str(path.relative_to(root)))
        run(path,native)
    if result['complete'] and marker.exists(): marker.unlink()
    return result


def request_youtube_purge(brand_root):
    root=Path(brand_root).absolute()
    marker=_safe(root/'.socialctl/youtube-purge-required.json',root)
    marker.parent.mkdir(parents=True,exist_ok=True)
    write_json(marker,{'version':1,'purge_required':True})
    return cleanup_youtube_data(root,purge=True)


def enforce_youtube_data(brand_root):
    result=cleanup_youtube_data(brand_root)
    if not result['complete']:
        paths=', '.join(item['path'] for item in result['errors'])
        raise YouTubeDataCleanupError('YouTube local data cleanup is incomplete (corrupt/unreadable cache or journal): '+paths+'. Run data-cleanup --brand BRAND --root WORKSPACE and resolve these paths before reading or writing this brand.')
    return result


def register(app):
    import typer
    @app.command('data-cleanup')
    def data_cleanup(brand: str=typer.Option(...,'--brand'), root:Path=typer.Option(...,'--root'),
                     purge_youtube:bool=typer.Option(False,'--purge-youtube',help='Remove all managed YouTube API data now; preserve supplied originals and replay guards.')):
        from socialctl.brands import cargar_brand
        selected=cargar_brand(root,brand,cleanup=False)
        result=request_youtube_purge(selected.raiz) if purge_youtube else cleanup_youtube_data(selected.raiz)
        typer.echo(json.dumps(result,ensure_ascii=False))
        if not result['complete']:raise typer.Exit(1)


def require_fresh_api_data(stamp):
    if expired(stamp,datetime.now(timezone.utc)):
        raise ValueError('YouTube API data expired; run data-cleanup for this brand before accessing this store.')
