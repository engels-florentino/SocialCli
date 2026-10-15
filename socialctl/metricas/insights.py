"""Deterministic offline insights; absent evidence is never a zero or a promise."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from socialctl.brands import Brand
from socialctl.metricas.almacen import _fechas, ruta_snapshot
from socialctl.metricas.modelos import EstadoLectura, LecturaRed, Snapshot
from socialctl.models import Platform
from socialctl.read_reports import ReadReport

ACCOUNT_FIELDS = {'followers':'seguidores','content_count':'total_piezas','views':'total_vistas'}


def _error(code, action='inspect_snapshot', **fields):
    return {'code':code,'action':action,'retryable':False,**fields}


def _load(brand, day, errors):
    path=ruta_snapshot(brand,day)
    if not path.exists():
        return None
    try:
        snapshot=Snapshot.model_validate_json(path.read_text(encoding='utf-8'))
        if snapshot.marca!=brand.nombre or snapshot.fecha!=day:
            errors.append(_error('snapshot_identity_mismatch',snapshot_date=day.isoformat()))
            return None
        return snapshot
    except (OSError,ValueError):
        errors.append(_error('snapshot_unreadable',snapshot_date=day.isoformat()))
        return None


def _number(value):
    return value if isinstance(value,int) and value>=0 else None


def _comparison(current, baseline, current_day, baseline_day):
    result={'status':'unavailable','reason':None,'from_observed_at':None,
            'to_observed_at':current.observed_at.isoformat() if current.observed_at else None,
            'elapsed_seconds':None}
    if baseline is None or baseline.estado!=EstadoLectura.OK:
        result['reason']='baseline_unavailable'
    elif not (current.account_id or '').strip() or not (baseline.account_id or '').strip():
        result['reason']='account_identity_unknown'
    elif current.account_id!=baseline.account_id:
        result['reason']='account_changed'
    else:
        start,end=baseline.observed_at,current.observed_at
        result['from_observed_at']=start.isoformat() if start else None
        if (not start or not end or start.utcoffset() is None or end.utcoffset() is None
                or end<=start):
            result['reason']='observation_interval_unavailable'
        elif end.date()!=current_day or start.date()!=baseline_day:
            # Old snapshots do not record the collector's timezone. Keep the
            # observations, but never guess that a different date is fresh.
            result['reason']='observation_date_mismatch'
        else:
            result.update(status='available',elapsed_seconds=(end-start).total_seconds())
    return result


def _accounts(current, baseline, comparable):
    values={}
    for name,field in ACCOUNT_FIELDS.items():
        end=_number(getattr(current.cuenta,field,None))
        start=_number(getattr(baseline.cuenta,field,None)) if baseline and comparable else None
        delta=end-start if end is not None and start is not None else None
        values[name]={'current':end,'baseline':start,'change':delta,
                     'percent_change':round(delta/start*100,2) if delta is not None and start else None}
        if name=='views' and delta is not None and delta<0:
            values[name].update(change=None,percent_change=None,
                                counter_correction=True,observed_difference=delta)
    return values


def _unique(pieces):
    counts=Counter(piece.id for piece in pieces)
    return {p.id:p for p in pieces if counts[p.id]==1},sorted(k for k,v in counts.items() if v>1)


def _rank(current, baseline, comparable, top):
    latest,duplicates=_unique(current.piezas)
    previous,baseline_duplicates=_unique(baseline.piezas if baseline else [])
    lifetime,changes,corrections=[],[],[]
    without_baseline=0
    unknown=0;unknown_baseline=0
    for identifier,piece in latest.items():
        end=_number(piece.acumulado.vistas)
        if end is None:
            unknown+=1
            continue
        item={'id':identifier,'title':piece.titulo,'url':piece.url,'value':end}
        lifetime.append(item)
        start=_number(previous[identifier].acumulado.vistas) if identifier in previous else None
        if identifier in previous and start is None:
            unknown_baseline+=1
        if start is None:
            without_baseline+=1
        elif comparable:
            delta=end-start
            if delta<0:
                corrections.append(identifier)
            else:
                changes.append({**item,'value':delta})
    sort=lambda rows:sorted(rows,key=lambda p:(-p['value'],p['id']))[:top]
    return {'scope':'observed_items_only','lifetime_views':sort(lifetime),
            'view_change':sort(changes),'without_baseline':without_baseline,
            'unknown_views':unknown,'counter_corrections':sorted(corrections),
            'unknown_baseline_views':unknown_baseline,
            'duplicate_ids':duplicates,'baseline_duplicate_ids':baseline_duplicates}


def build_report(brand: Brand, days: int=7, as_of: date|None=None,
                 top: int=5, only: Platform|None=None) -> ReadReport:
    if not 1<=days<=365 or not 1<=top<=50:
        raise ValueError('days must be 1..365 and top must be 1..50')
    from socialctl.youtube_data import enforce_youtube_data, MAX_AGE_DAYS, YouTubeDataCleanupError
    as_of=as_of or date.today()
    data={'brand':brand.nombre,'requested_as_of':as_of.isoformat(),'days':days,
          'source':'saved_snapshots','coverage_scope':'platforms_in_latest_snapshot',
          'youtube_cache_max_days':MAX_AGE_DAYS,
          'latest_snapshot':None,'platforms':{}}
    try:
        enforce_youtube_data(brand.raiz)
    except YouTubeDataCleanupError:
        return ReadReport(status='error',data=data,errors=[_error('local_data_cleanup_required','run_data_cleanup')])
    errors=[]
    dates=[day for day in _fechas(brand) if day<=as_of]
    if not dates:
        return ReadReport(status='error',data=data,errors=[_error('snapshots_unavailable','collect_metrics')])
    latest_day=dates[-1]
    data['latest_snapshot']=latest_day.isoformat()
    latest=_load(brand,latest_day,errors)
    if latest is None:
        return ReadReport(status='error',data=data,errors=errors)
    baseline_day=latest_day-timedelta(days=days)
    previous_day=baseline_day-timedelta(days=days)
    baseline=_load(brand,baseline_day,errors)
    previous=_load(brand,previous_day,errors)
    data['baseline_snapshot']=baseline_day.isoformat()
    data['previous_baseline_snapshot']=previous_day.isoformat()
    platforms=[only] if only else sorted(latest.redes,key=lambda p:p.value)
    coverage=[];usable=0
    for platform in platforms:
        current=latest.redes.get(platform)
        old=baseline.redes.get(platform) if baseline else None
        before=previous.redes.get(platform) if previous else None
        covered={'platform':platform.value,'complete':False,'stale':latest_day<as_of,
                 'source_partial':False,'state':current.estado.value if current else 'unavailable'}
        coverage.append(covered)
        if current is None or current.estado!=EstadoLectura.OK:
            errors.append(_error('platform_read_unavailable','collect_metrics',platform=platform.value))
            continue
        usable+=1
        comparison=_comparison(current,old,latest_day,baseline_day)
        comparable=comparison['status']=='available'
        accounts=_accounts(current,old,comparable)
        rankings=_rank(current,old,comparable,top)
        partial=bool(current.warnings or any('enriquecimiento_fallido' in p.especificas for p in current.piezas))
        partial=partial or bool(old and (old.warnings or any('enriquecimiento_fallido' in p.especificas for p in old.piezas)))
        covered['source_partial']=partial
        covered['observed_at']=comparison['to_observed_at']
        covered['temporal_provenance']=comparison['reason'] if comparison['reason'] in {
            'observation_date_mismatch','observation_interval_unavailable'} else ('observed_interval' if comparable else 'unverified')
        covered['complete']=(comparable and not partial and not covered['stale']
            and not rankings['duplicate_ids'] and not rankings['baseline_duplicate_ids']
            and not rankings['unknown_views'] and not rankings['unknown_baseline_views']
            and not rankings['counter_corrections']
            and not any(v.get('counter_correction') for v in accounts.values())
            and any(v['change'] is not None for v in accounts.values()))
        prior=_comparison(old,before,baseline_day,previous_day) if old and old.estado==EstadoLectura.OK else {'status':'unavailable','reason':'baseline_unavailable'}
        if old and old.account_id!=current.account_id:
            prior.update(status='unavailable',reason='account_changed')
        prior['account']=_accounts(old,before,prior['status']=='available') if old and old.estado==EstadoLectura.OK else {}
        data['platforms'][platform.value]={'account_id':current.account_id,'account':accounts,
            'comparison':comparison,'previous_comparison':prior,'top_content':rankings,
            'best_posting_time':{'status':'unavailable','reason':'audience_online_hours_not_observed'}}
    status='error' if not usable else ('ok' if not errors and all(r['complete'] for r in coverage) else 'partial')
    return ReadReport(status=status,data=data,coverage=coverage,errors=errors)


def _plain(value):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))


def render_report(report: ReadReport) -> str:
    data=report.data
    lines=[f"SocialCli report — {_plain(data['brand'])}: {report.status}",
           f"Saved observations: {data['latest_snapshot'] or 'unavailable'}; requested daily window: {data['days']} days."]
    for platform,row in data['platforms'].items():
        lines.append(platform)
        for metric,value in row['account'].items():
            current=value['current'] if value['current'] is not None else 'unknown'
            delta=f"{value['change']:+d}" if value['change'] is not None else 'unavailable'
            lines.append(f"  {metric}: {current}; observed change: {delta}")
            if value.get('counter_correction'):
                lines.append(f"  {metric} counter correction: {value['observed_difference']:+d}; growth unavailable")
        comparison=row['comparison']
        if comparison['status']=='available':
            lines.append(f"  Observed interval: {comparison['from_observed_at']} → {comparison['to_observed_at']}")
        else:
            lines.append(f"  Comparison: {comparison['reason']}")
        prior=row['previous_comparison']
        if prior['status']=='available':
            lines.append(f"  Previous observed interval: {prior['from_observed_at']} → {prior['to_observed_at']}")
            for metric,value in prior['account'].items():
                delta=f"{value['change']:+d}" if value['change'] is not None else 'unavailable'
                lines.append(f"    {metric} observed change: {delta}")
        else:
            lines.append(f"  Previous comparison: {prior['reason']}")
        for name,label in [('view_change','Top observed view changes'),('lifetime_views','Top observed lifetime views')]:
            lines.append(f"  {label}:")
            for item in row['top_content'][name]:
                lines.append(f"    {item['value']} — {_plain(item['title'])} — {_plain(item['url'])}")
        ranking=row['top_content']
        lines.append(f"  Content coverage: without baseline: {ranking['without_baseline']}; unknown views: {ranking['unknown_views']}; unknown baseline views: {ranking['unknown_baseline_views']}; counter corrections: {len(ranking['counter_corrections'])}; ambiguous IDs: {len(ranking['duplicate_ids'])+len(ranking['baseline_duplicate_ids'])}")
        lines.append('  Best posting time: unavailable; audience online hours were not observed.')
    for covered in report.coverage:
        if not covered['complete']:
            lines.append(f"Coverage {covered['platform']}: incomplete; stale={covered['stale']}, source_partial={covered['source_partial']}")
    for error in report.errors:
        lines.append(f"{error['code']}: {error['action']}")
    lines.append(f"YouTube API cache expires after {data.get('youtube_cache_max_days', 6)} days; older comparisons may be unavailable.")
    lines.append('Rankings cover observed content only. Missing data is not zero; platforms are not combined.')
    return '\n'.join(lines)
