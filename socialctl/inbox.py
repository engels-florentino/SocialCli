"""Coverage-aware observations of owned-account comments; never writes to providers."""
from __future__ import annotations
import json
import re
from datetime import datetime,date,time,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

from socialctl.models import Platform
from socialctl.read_budget import read_client
from socialctl.read_reports import ReadReport,safe_error
from socialctl.management.meta_comment_inbox import CommentInboxStore,sync_inbox
from socialctl.migration_files import write_json


def resolve_since(value,timezone_name,now):
    zone=None
    if timezone_name:
        try:zone=ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError,ValueError):raise ValueError('invalid IANA timezone') from None
    if not isinstance(value,str):raise ValueError('--since requires a date or RFC3339 timestamp')
    if value.casefold() in {'ayer','yesterday'} or re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        if zone is None:raise ValueError('relative dates and calendar dates require --timezone or brand inbox.yml timezone')
        if now.tzinfo is None:raise ValueError('current observation time must include a timezone')
        day=now.astimezone(zone).date()-timedelta(days=1) if value.casefold() in {'ayer','yesterday'} else date.fromisoformat(value)
        local=datetime.combine(day,time.min,tzinfo=zone)
        if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=local.replace(tzinfo=None) or local.utcoffset()!=local.replace(fold=1).utcoffset():
            raise ValueError('ambiguous/nonexistent local midnight; supply explicit RFC3339 offset')
        return local.astimezone(timezone.utc)
    try:stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('--since requires yesterday/ayer, YYYY-MM-DD or RFC3339') from None
    if stamp.tzinfo is None:raise ValueError('--since timestamp must include its timezone offset')
    return stamp.astimezone(timezone.utc)


class UnifiedInboxStore:
    def __init__(self,root):
        self.root=Path(root)
        self.path=self.root/'.socialctl'/'unified-inbox'/'state.json'
    def load(self):
        if not self.path.exists():return {'version':1,'items':{},'watermarks':{}}
        try:
            data=json.loads(self.path.read_text())
            if data.get('version')!=1 or not isinstance(data.get('items'),dict) or not isinstance(data.get('watermarks'),dict):raise ValueError()
            return data
        except (OSError,ValueError):raise ValueError('unified inbox state is invalid; preserve it before recovery') from None
    @staticmethod
    def key(row):return json.dumps([row['platform'],row['account_id'],row['comment_id']],separators=(',',':'))
    def observe(self,rows,*,observed_at,scope=None,complete=False):
        changed=[]
        with CommentInboxStore(self.root).lock():
            data=self.load()
            for row in rows:
                key=self.key(row);old=data['items'].get(key)
                if old and (old['media_id'],old.get('parent_id'))!=(row['media_id'],row.get('parent_id')):
                    raise ValueError('comment identity changed media/parent; observation is ambiguous')
                history=list(old.get('history',[])) if old else []
                if old and old['text']!=row['text']:
                    history.append({'observed_at':observed_at,'before':old['text'],'after':row['text']});changed.append(key)
                data['items'][key]={**row,'original_text':old['original_text'] if old else row['text'],
                    'first_observed_at':old['first_observed_at'] if old else observed_at,
                    'last_observed_at':observed_at,'history':history}
            if scope and complete:data['watermarks'][scope]=observed_at
            self.path.parent.mkdir(parents=True,exist_ok=True)
            write_json(self.path,data)
        return changed


class InboxInterrupted(KeyboardInterrupt):
    def __init__(self, rows):
        self.rows = rows


def _youtube_rows(client, video_id):
    report = client.list_threads(video_id)
    rows, errors = [], []
    complete = report['complete']
    if report.get('error'):
        errors.append(report['error'])

    def observe(row, parent, thread_id):
        nonlocal complete
        snippet = row['snippet']
        author = (snippet.get('authorChannelId') or {}).get('value')
        if author == client.configured_channel_id:
            return
        text = snippet.get('textOriginal', snippet.get('textDisplay'))
        if not isinstance(author, str) or not author or not isinstance(text, str):
            complete = False
            errors.append('comment author/text unavailable')
            return
        created = snippet.get('publishedAt')
        if created is None:
            complete = False
            errors.append('comment creation time unavailable; interval membership unknown')
        rows.append({'platform': 'youtube', 'account_id': client.configured_channel_id,
            'media_id': video_id, 'thread_id': thread_id, 'parent_id': parent,
            'comment_id': row['id'], 'author_id': author, 'text': text,
            'created_time': created, 'provider_updated_at': snippet.get('updatedAt'),
            'status': 'observed'})

    try:
        for thread in report['items']:
            top = thread['snippet']['topLevelComment']
            observe(top, None, thread['id'])
            if report.get('interrupted'):
                continue
            try:
                replies = client.list_replies(video_id, thread['id'], top['id'])
            except Exception as exc:
                replies = {'items': [], 'complete': False, 'error': safe_error(str(exc))}
            complete = complete and replies['complete']
            if replies.get('error'):
                errors.append(replies['error'])
            for reply in replies['items']:
                observe(reply, top['id'], thread['id'])
            if replies.get('interrupted'):
                raise InboxInterrupted(rows)
    except KeyboardInterrupt:
        raise InboxInterrupted(rows) from None
    if report.get("interrupted"):
        raise InboxInterrupted(rows)
    return rows, complete, errors


def sync_brand_inbox(brand,*,since,budget):
    from socialctl.management.meta_client import MetaClient
    from socialctl.management.meta_comments import MetaCommentsClient
    from socialctl.management.youtube_community import YouTubeCommunityClient
    from socialctl.inventory import sync_content,InventoryStore
    from socialctl.management.changes import now_utc
    store=UnifiedInboxStore(brand.raiz);coverage=[];errors=[];edited=set();observed_keys=set()
    observed=now_utc().isoformat()
    fields={'youtube':'channel_id','facebook':'page_id','instagram':'ig_user_id','tiktok':'open_id'}
    with read_client(budget,progress=lambda:__import__('typer').echo('Reading inbox provider page...',err=True)) as http:
        for platform in Platform:
            account=(brand.cuentas.get(platform.value) or {}).get(fields[platform.value])
            row={'platform':platform.value,'account_id':account or None,'complete':False,'scopes':[]}
            coverage.append(row)
            if platform is Platform.TIKTOK:
                row['state']='unsupported';errors.append({'platform':'tiktok','message':'Comment inbox unsupported by the configured TikTok integration'});continue
            if not account:
                row['state']='unavailable';errors.append({'platform':platform.value,'message':'No account configured'});continue
            try:
                budget.check()
                if platform is Platform.YOUTUBE:
                    inventory_store=InventoryStore(brand,platform,account)
                    prior=inventory_store.load().get('sync') or {}
                    resume=not prior.get('complete',True) and prior.get('resumable',False)
                    inventory=sync_content(brand,platform,http,resume=resume)
                    if resume and inventory['failure_class'] in {'invalid_cursor','provider_error'}:
                        budget.check()
                        inventory=sync_content(brand,platform,http,resume=False)
                    media=[v['remote_id'] for v in InventoryStore(brand,platform,account).list_items() if v.get('last_sync_id')==inventory['sync_id']]
                    complete=inventory['complete'] and inventory['details_complete']
                    client=YouTubeCommunityClient(brand,http)
                    for ident in media:
                        budget.check()
                        try:items,ok,problems=_youtube_rows(client,ident)
                        except InboxInterrupted as exc:
                            store.observe(exc.rows,observed_at=observed)
                            observed_keys.update(store.key(i) for i in exc.rows)
                            row['scopes'].append({'media_id':ident,'filter':'published','complete':False})
                            raise KeyboardInterrupt() from None
                        except Exception as exc:
                            items,ok,problems=[],False,[safe_error(str(exc))]
                        scope=json.dumps([platform.value,account,ident,'published'])
                        edited.update(store.observe(items,observed_at=observed,scope=scope,complete=ok))
                        observed_keys.update(store.key(i) for i in items)
                        row['scopes'].append({'media_id':ident,'filter':'published','complete':ok})
                        complete=complete and ok
                        errors.extend({'platform':platform.value,'media_id':ident,'message':safe_error(str(e))} for e in problems)
                else:
                    meta=MetaClient(brand,platform,http)
                    listing=meta.content_list('feed' if platform is Platform.FACEBOOK else 'media')
                    complete=listing['complete'];client=MetaCommentsClient(brand,platform,http)
                    inbox=CommentInboxStore(brand.raiz)
                    for media in listing['data']:
                        budget.check();ident=media['id']
                        try:
                            result=sync_inbox(client,inbox,media_id=ident,since=since.isoformat(),resume=True)
                            results=[result]; problems=[];items=[]
                            def collect(observation):
                                records=[r for r in inbox.load().comments if r.platform==platform.value and r.account_id==account and r.media_id==ident and r.last_observed_at==observation['observed_at']]
                                fresh=[{'platform':r.platform,'account_id':r.account_id,'media_id':r.media_id,'parent_id':r.parent_id,
                                    'comment_id':r.comment_id,'author_id':r.author_id,'text':r.text,'created_time':r.created_time,
                                    'provider_updated_at':r.provider_updated_at,'status':r.status,'draft_change_id':r.change_id} for r in records]
                                edited.update(store.observe(fresh,observed_at=observed))
                                observed_keys.update(store.key(i) for i in fresh)
                                items.extend(fresh)
                            collect(result)
                            if result.get('interrupted'):
                                row['scopes'].append({'media_id':ident,'filter':'top_level_and_direct_replies','complete':False})
                                raise KeyboardInterrupt()
                            for parent_id in result['parent_ids']:
                                try:
                                    reply_result=sync_inbox(client,inbox,media_id=ident,parent_id=parent_id,since=since.isoformat(),resume=True)
                                    results.append(reply_result);collect(reply_result)
                                    if reply_result.get('interrupted'):raise KeyboardInterrupt()
                                except Exception as exc:
                                    problems.append(safe_error(str(exc)))
                                except KeyboardInterrupt:
                                    row['scopes'].append({'media_id':ident,'filter':'top_level_and_direct_replies','complete':False})
                                    raise
                            ok=not problems and all(r['complete'] for r in results)
                            problems.extend(r.get('error') or 'Meta traversal incomplete' for r in results if not r['complete'])
                            row['scopes'].append({'media_id':ident,'filter':'top_level_and_direct_replies','complete':ok})
                        except Exception as exc:
                            items,ok,problems=[],False,[safe_error(str(exc))]
                        scope=json.dumps([platform.value,account,ident,'top_level_and_direct_replies'])
                        edited.update(store.observe(items,observed_at=observed,scope=scope,complete=ok))
                        observed_keys.update(store.key(i) for i in items)
                        complete=complete and ok
                        errors.extend({'platform':platform.value,'media_id':ident,'message':safe_error(str(e))} for e in problems)
                row['complete']=complete;row['state']='ok' if complete else 'partial'
                if not complete and not any(e.get('platform')==platform.value for e in errors):errors.append({'platform':platform.value,'message':'Owned content enumeration incomplete'})
            except (Exception,KeyboardInterrupt) as exc:
                row['state']='partial' if row['scopes'] else 'error'
                errors.append({'platform':platform.value,'message':'Read interrupted' if isinstance(exc,KeyboardInterrupt) else safe_error(str(exc))})
                if isinstance(exc,KeyboardInterrupt):break
    covered={r['platform'] for r in coverage}
    for platform in Platform:
        if platform.value not in covered:
            coverage.append({'platform':platform.value,'account_id':(brand.cuentas.get(platform.value) or {}).get(fields[platform.value]),
                'complete':False,'scopes':[],'state':'not_attempted'})
            errors.append({'platform':platform.value,'message':'Not attempted after interruption'})
    data=store.load();items=[]
    for key in observed_keys:
        item=data['items'][key];created=item.get('created_time')
        try:inside=created is None or datetime.fromisoformat(created.replace('Z','+00:00')).astimezone(timezone.utc)>=since
        except (ValueError,AttributeError):inside=True
        if inside or key in edited:items.append({**item,'edited_this_sync':key in edited})
    status='ok' if coverage and all(r['complete'] for r in coverage) else 'partial' if any(r['scopes'] or r['complete'] for r in coverage) else 'error'
    return ReadReport(status=status,coverage=coverage,errors=errors,
        data={'brand':brand.nombre,'resolved_since':since.isoformat(),'observed_until':observed,
              'filter_semantics':'created at/after since, plus previously observed comments edited during this sync',
              'items':sorted(items,key=lambda r:(r['platform'],r['account_id'],r['comment_id'])),'watermarks':data['watermarks']})
