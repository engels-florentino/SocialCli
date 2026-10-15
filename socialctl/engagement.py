"""Join observed comments to existing immutable reply journals; never send replies."""
import uuid

from socialctl.inbox import UnifiedInboxStore
from socialctl.management.changes import ChangeError
from socialctl.management.community_batches import (CommunityBatchStore, child_store,
    child_details, prepare_community_batch)
from socialctl.models import Platform
from socialctl.read_budget import read_client

ACCOUNT_FIELDS={'youtube':'channel_id','facebook':'page_id','instagram':'ig_user_id'}
UNCERTAIN={'applying','uncertain','partial','accepted_unverifiable','applied_unverified'}


class DraftSelectionError(ChangeError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def draft_failure(exc, row, index, stage):
    # Fixed local reason codes and target IDs only. Provider/storage exception
    # text can contain credentials or private diagnostics and is not copied.
    exc.draft_failure = {'child_index': index, 'stage': stage,
        **{key: row[key] for key in ('platform', 'account_id', 'comment_id', 'media_id')
           if isinstance(row, dict) and isinstance(row.get(key), str)},
        'reason': getattr(exc, 'reason', 'target_validation_failed' if stage == 'selection' else 'draft_preparation_failed')}
    if isinstance(exc, DraftSelectionError):
        exc.draft_failure['message'] = str(exc)
    return exc


def http_client(budget):
    return read_client(budget)


def review_inbox(brand):
    data=UnifiedInboxStore(brand.raiz).load()
    links={};excluded_proposals=0
    for provider in ('youtube-community','meta-comments'):
        store=child_store(brand,provider)
        for path in sorted(store.root.glob('*.json')):
            child=store.load(path.stem)
            if provider=='youtube-community':
                if child.edit['action']!='reply':continue
                name,root,account=child.target_brand,child.brand_root,child.target_account
                platform,parent,media='youtube',child.edit['parent_id'],child.edit['video_id']
                text,remote=child.edit['text'],child.result_id
            else:
                if child.action!='reply':continue
                name,root,account=child.brand,child.brand_root,child.account_id
                platform,parent,media=child.platform,child.parent_id,child.media_id
                text,remote=child.text,child.remote_id
            if (name,root)!=(brand.nombre,str(brand.raiz.resolve())):
                raise ChangeError('reply journal belongs to another brand/workspace')
            if account!=(brand.cuentas.get(platform) or {}).get(ACCOUNT_FIELDS[platform]):
                excluded_proposals+=1;continue
            child_details(brand,provider,child)
            key=UnifiedInboxStore.key(dict(platform=platform,account_id=account,comment_id=parent))
            links.setdefault(key,[]).append(dict(provider=provider,change_id=child.id,
                fingerprint=child.fingerprint,status=child.status,media_id=media,text=text,remote_id=remote))
            context = child.before.get('reply_context')
            if context:
                if (context.get('platform'), context.get('account_id'), context.get('media_id'), context.get('parent_id')) != (platform, account, media, parent):
                    raise ChangeError('reply context differs from the stored parent binding')
                context_key = UnifiedInboxStore.key(context)
                links.setdefault(context_key, []).append({**links[key][-1], 'reply_parent_id':parent})
    items=[];excluded=0
    for key,row in sorted(data['items'].items()):
        field=ACCOUNT_FIELDS.get(row['platform'])
        if not field or row['account_id']!=(brand.cuentas.get(row['platform']) or {}).get(field):
            excluded+=1;continue
        if key!=UnifiedInboxStore.key(row):raise ChangeError('saved comment identity is inconsistent')
        if row.get('author_id') == row['account_id']:
            continue
        proposals=links.get(key,[])
        if any(p['media_id']!=row['media_id'] for p in proposals):raise ChangeError('reply journal target differs from observed media')
        states={p['status'] for p in proposals}
        state=('uncertain' if states & UNCERTAIN else 'replied' if 'verified' in states
               else 'draft' if 'proposed' in states else 'blocked' if states else 'observed')
        items.append({**row,'reply_state':state,'proposals':proposals})
    return {'version':1,'brand':brand.nombre,'status':'ok','items':items,
        'coverage':{'source':'saved_observations','live_verified':False,'complete':False,
            'excluded_accounts':excluded,'excluded_proposals':excluded_proposals,'watermarks':data['watermarks'],
            'limitations':['This is saved evidence, not a complete live inbox.',
                'Replied means a stored verified result; present remote visibility is not rechecked.',
                'TikTok comments are unsupported. Replying in an existing reply thread requires an explicit reply_parent_id mapping to its observed top-level comment; deeper nesting is not promised.']}}


def selected_replies(brand,raw):
    if not isinstance(raw,dict) or set(raw)!={'version','replies'} or type(raw['version']) is not int or raw['version']!=1:
        raise ChangeError('expected version 1 and explicit replies')
    replies=raw['replies']
    if not isinstance(replies,list) or not 1<=len(replies)<=100:raise ChangeError('select 1–100 explicit replies')
    rows={UnifiedInboxStore.key(row):row for row in review_inbox(brand)['items']}
    result=[];seen=set()
    for index, reply in enumerate(replies, 1):
        try:
            if (not isinstance(reply,dict) or set(reply) not in ({'platform','account_id','comment_id','text'}, {'platform','account_id','comment_id','text','reply_parent_id'})
                    or any(not isinstance(v,str) or not v.strip() for v in reply.values())
                    or reply['platform'] not in ACCOUNT_FIELDS or len(reply['text'])>10000):
                raise DraftSelectionError('invalid_reply_selection', 'reply requires supported platform, explicit account/comment and nonempty text')
            key=UnifiedInboxStore.key(reply)
            if key in seen:raise DraftSelectionError('duplicate_reply_target', 'duplicate reply target')
            seen.add(key)
            row=rows.get(key)
            if row is None:raise DraftSelectionError('target_not_observed', 'target must be observed for the currently selected account')
            if not row.get('author_id') and row['platform'] != 'facebook':
                raise DraftSelectionError('author_unavailable', 'target must have a verified external author')
            source = None
            if row.get('parent_id'):
                if not reply.get('reply_parent_id'):
                    raise DraftSelectionError('reply_parent_required',
                        'Nested context requires explicit reply_parent_id matching its observed top-level parent. The new reply will appear under that parent, not at a guessed deeper level.')
                if reply['reply_parent_id'] != row['parent_id']:
                    raise DraftSelectionError('reply_parent_mismatch', 'reply_parent_id differs from the observed parent')
                source = row
                parent_key = UnifiedInboxStore.key({**row, 'comment_id':reply['reply_parent_id']})
                row = rows.get(parent_key)
                if (not row or row.get('parent_id') or row['media_id'] != source['media_id']
                        or row.get('thread_id') != source.get('thread_id')):
                    raise DraftSelectionError('reply_parent_unavailable', 'The mapped top-level parent must be observed on the same media and thread')
                if parent_key in seen:
                    raise DraftSelectionError('duplicate_reply_parent', 'Multiple selected replies map to the same parent')
                seen.add(parent_key)
            elif reply.get('reply_parent_id'):
                raise DraftSelectionError('reply_parent_mismatch', 'reply_parent_id is only valid for an observed nested context')
            if row['reply_state'] in {'replied','uncertain'}:raise DraftSelectionError('existing_reply_state', 'existing replied/uncertain target; inspect or reconcile before another draft')
            if row.get('author_id') == row['account_id']:
                raise DraftSelectionError('own_comment', 'target is an own comment')
            if not row.get('author_id') and row['platform'] != 'facebook':
                raise DraftSelectionError('author_unavailable', 'target must have a verified external author')
            if row['platform']=='youtube' and not row.get('thread_id'):raise DraftSelectionError('thread_unavailable', 'observed YouTube thread is required')
            if source:
                row = {**row, 'reply_context': {key: source.get(key) for key in
                    ('platform', 'account_id', 'media_id', 'thread_id', 'comment_id', 'parent_id', 'author_id', 'text')}}
            result.append((row,reply['text']))
        except (Exception, KeyboardInterrupt) as exc:
            raise draft_failure(exc, reply, index, 'selection')
    return result


def same_parent(observed,fresh):
    if (fresh.get('id'),fresh.get('author_id'),fresh.get('text'))!=(observed['comment_id'],observed['author_id'],observed['text']):
        raise DraftSelectionError('comment_changed', 'comment changed since observation; sync and review before drafting')


def prepare_replies(brand,raw,*,budget,prepared,progress=None):
    """Append every completed immutable child ref even if a later preparation fails."""
    lock_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'socialcli:inbox-draft'))
    with CommunityBatchStore(brand.raiz).apply_lock(lock_id):
        selected=selected_replies(brand,raw)
        with http_client(budget) as http:
            for index, (row,text) in enumerate(selected, 1):
                try:
                    budget.check()
                    if progress is not None:
                        progress({'child_index':index, 'total':len(selected), 'platform':row['platform'],
                            'comment_id':row['comment_id'], 'status':'preparing'})
                    if row['platform']=='youtube':
                        from socialctl.management.youtube_community import YouTubeCommunityClient
                        from socialctl.management.community_changes import prepare_community
                        client=YouTubeCommunityClient(brand,http)
                        parent=client.thread(row['media_id'],row['thread_id'])['snippet']['topLevelComment']
                        snippet=parent['snippet']
                        same_parent(row,{'id':parent['id'],'author_id':(snippet.get('authorChannelId') or {}).get('value'),
                            'text':snippet.get('textOriginal',snippet.get('textDisplay'))})
                        child=prepare_community(client,child_store(brand,'youtube-community'),
                            dict(action='reply',video_id=row['media_id'],thread_id=row['thread_id'],parent_id=row['comment_id'],text=text),
                            reply_context=row.get('reply_context'))
                        provider='youtube-community'
                    else:
                        from socialctl.management.meta_comments import MetaCommentsClient
                        from socialctl.management.meta_comment_inbox import CommentInboxStore,prepare_inbox_draft
                        client=MetaCommentsClient(brand,Platform(row['platform']),http)
                        same_parent(row,client.find_comment(row['media_id'],row['comment_id']))
                        child,_=prepare_inbox_draft(client,child_store(brand,'meta-comments'),CommentInboxStore(brand.raiz),
                            media_id=row['media_id'],comment_id=row['comment_id'],text=text,
                            reply_context=row.get('reply_context'),
                            on_prepared=lambda child:prepared.append({'provider':'meta-comments','change_id':child.id}))
                        provider='meta-comments'
                    reference={'provider':provider,'change_id':child.id}
                    if reference not in prepared:prepared.append(reference)
                    # Recheck the exact baseline captured by preparation against the
                    # selected observation, not just the earlier network read.
                    parent=child.before['parent']
                    fresh=parent if provider=='meta-comments' else {'id':parent['id'],
                        'author_id':(parent['snippet'].get('authorChannelId') or {}).get('value'),
                        'text':parent['snippet'].get('textOriginal',parent['snippet'].get('textDisplay'))}
                    same_parent(row,fresh)
                    if progress is not None:
                        progress({'child_index':index, 'total':len(selected), 'platform':row['platform'],
                            'comment_id':row['comment_id'], 'status':'prepared'})
                except (Exception, KeyboardInterrupt) as exc:
                    raise draft_failure(exc, row, index, 'preparation')
        return prepare_community_batch(brand,prepared)
