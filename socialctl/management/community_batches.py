"""Immutable supervised community batches; existing child stores own write safety."""
from __future__ import annotations
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from socialctl.management.changes import ChangeError, now_utc
from socialctl.management.resource_changes import ResourceStore
from socialctl.models import Platform


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class BatchChild(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider: Literal['youtube-community','meta-comments','meta-management']
    change_id: str
    fingerprint: str
    account_id: str
    proposal: dict
    limitations: list[str]


class CommunityBatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version: Literal[1]=1
    kind: Literal['community-batch']='community-batch'
    id: str
    brand: str
    brand_root: str
    accounts: dict
    children: list[BatchChild]=Field(min_length=1,max_length=100)
    created_at: str
    fingerprint: str=''
    status: Literal['proposed','applying','partial','verified']='proposed'
    outcomes: dict[str,dict]=Field(default_factory=dict)


def batch_fingerprint(batch):
    return digest(batch.model_dump(mode='json',exclude={'fingerprint','status','outcomes'}))


class CommunityBatchStore(ResourceStore):
    model_type=CommunityBatch
    fingerprint_for=staticmethod(batch_fingerprint)
    def __init__(self,root):
        self.root=Path(root)/'.socialctl'/'community-batches'


def child_store(brand,provider):
    if provider=='youtube-community':
        from socialctl.management.community_changes import CommunityStore
        return CommunityStore(brand.raiz)
    if provider=='meta-comments':
        from socialctl.management.meta_comments import CommentStore
        return CommentStore(brand.raiz)
    if provider=='meta-management':
        from socialctl.management.meta_changes import MetaStore
        return MetaStore(brand.raiz)
    raise ChangeError('unsupported community batch provider')


def child_details(brand,provider,child):
    if provider=='youtube-community':
        if child.edit['action'] not in {'reply','rate'}: raise ChangeError('batch supports YouTube reply/video rating only')
        name,root,account=child.target_brand,child.brand_root,child.target_account
        expected=(brand.cuentas.get('youtube') or {}).get('channel_id')
        target=child.edit.get('parent_id') or child.edit.get('video_id')
        operation=[provider,account,child.edit['action'],target]
        limitations=child.effects
    elif provider=='meta-comments':
        if child.action not in {'add','reply'}: raise ChangeError('batch supports Meta add/reply only')
        name,root,account=child.brand,child.brand_root,child.account_id
        field='page_id' if child.platform=='facebook' else 'ig_user_id'
        expected=(brand.cuentas.get(child.platform) or {}).get(field)
        operation=[provider,child.platform,account,child.parent_id or child.media_id]
        limitations=['Exact supplied text and target only. Clickability/pinning are not promised. Uncertain writes are not repeated.',
            'Requires actual creator grants and applicable actor/object eligibility; app settings alone do not prove permission.']
    else:
        if child.platform!='facebook' or child.edit['action'] not in {'like','unlike'}: raise ChangeError('batch supports Facebook like/unlike only')
        name,root,account=child.brand,child.brand_root,child.identity['account_id']
        expected=(brand.cuentas.get('facebook') or {}).get('page_id')
        operation=[provider,account,child.edit['target_id']]
        limitations=['Facebook Page token, pages_manage_engagement and eligible object/Page task required. No rollback or cross-provider transaction.']
    if (name,root,account)!=(brand.nombre,str(brand.raiz.resolve()),expected):
        raise ChangeError('child brand/account binding differs from current brand')
    return account,operation,limitations


def prepare_community_batch(brand,child_refs):
    if not isinstance(child_refs,list) or not 1<=len(child_refs)<=100: raise ChangeError('batch needs 1–100 explicit children')
    children,seen=[],set()
    for ref in child_refs:
        if not isinstance(ref,dict) or set(ref)!={'provider','change_id'}: raise ChangeError('child reference requires provider/change_id only')
        store=child_store(brand,ref['provider'])
        child=store.load(ref['change_id'])
        if child.status!='proposed': raise ChangeError('prepare batch from proposed children only')
        account,key,limits=child_details(brand,ref['provider'],child)
        key=digest(key)
        if key in seen: raise ChangeError('duplicate/conflicting community batch target')
        seen.add(key)
        children.append(BatchChild(**ref,fingerprint=child.fingerprint,account_id=account,
            proposal=child.model_dump(mode='json'),limitations=limits))
    batch=CommunityBatch(id=str(uuid.uuid4()),brand=brand.nombre,brand_root=str(brand.raiz.resolve()),
        accounts=brand.cuentas,children=children,created_at=now_utc().isoformat())
    batch.fingerprint=batch_fingerprint(batch)
    CommunityBatchStore(brand.raiz).save(batch)
    return batch


def http_client():
    from socialctl.read_budget import ReadBudget,read_client
    return read_client(ReadBudget(120))


def dispatch(brand,reference,*,reconcile=False):
    store=child_store(brand,reference.provider)
    with http_client() as http:
        if reference.provider=='youtube-community':
            from socialctl.management.youtube_community import YouTubeCommunityClient
            from socialctl.management.community_changes import apply_community,reconcile_community
            client=YouTubeCommunityClient(brand,http)
            apply,read=apply_community,reconcile_community
        elif reference.provider=='meta-comments':
            from socialctl.management.meta_comments import MetaCommentsClient,apply_comment,reconcile_comment
            client=MetaCommentsClient(brand,Platform(reference.proposal['platform']),http)
            apply,read=apply_comment,reconcile_comment
        else:
            from socialctl.management.meta_client import MetaClient
            from socialctl.management.meta_changes import apply_meta,reconcile_meta
            client=MetaClient(brand,Platform.FACEBOOK,http)
            apply,read=apply_meta,reconcile_meta
        if reconcile: return read(client,store,reference.change_id)
        return apply(client,store,reference.change_id,reference.fingerprint)


def validate_batch(brand,batch):
    if (batch.brand,batch.brand_root,batch.accounts)!=(brand.nombre,str(brand.raiz.resolve()),brand.cuentas):
        raise ChangeError('batch brand/account configuration changed; prepare a new preview')
    for ref in batch.children:
        child=child_store(brand,ref.provider).load(ref.change_id)
        child_details(brand,ref.provider,child)
        if not hmac.compare_digest(child.fingerprint,ref.fingerprint):
            raise ChangeError('child proposal changed; batch approval is invalid')


def apply_community_batch(brand,batch_id,approval_digest):
    store=CommunityBatchStore(brand.raiz)
    with store.apply_lock(batch_id):
        batch=store.load(batch_id)
        if not isinstance(approval_digest,str) or not hmac.compare_digest(batch.fingerprint,approval_digest):
            raise ChangeError('approval must match the exact full batch fingerprint')
        validate_batch(brand,batch)
        for ref in batch.children:
            child=child_store(brand,ref.provider).load(ref.change_id)
            if child.status=='verified':
                batch.outcomes[ref.change_id]={'status':'verified'}
                continue
            batch.status='applying'
            batch.outcomes[ref.change_id]={'status':'applying'}
            store.save(batch)
            try:
                result=dispatch(brand,ref)
                batch.outcomes[ref.change_id]={'status':result.status}
            except ChangeError as exc:
                from socialctl.read_reports import safe_error
                batch.outcomes[ref.change_id]={'status':'blocked','error':safe_error(str(exc))}
                batch.status='partial';store.save(batch);return batch
            except BaseException:
                batch.status='partial';store.save(batch);raise
            if result.status!='verified':
                batch.status='partial';store.save(batch);return batch
            store.save(batch)
        batch.status='verified';store.save(batch);return batch


def reconcile_community_batch(brand,batch_id):
    store=CommunityBatchStore(brand.raiz)
    with store.apply_lock(batch_id):
        batch=store.load(batch_id);validate_batch(brand,batch)
        for ref in batch.children:
            if ref.change_id not in batch.outcomes: continue
            child=child_store(brand,ref.provider).load(ref.change_id)
            if child.status in {'applying','uncertain','partial','accepted_unverifiable','applied_unverified'}:
                try:
                    child=dispatch(brand,ref,reconcile=True)
                except ChangeError:
                    continue
            batch.outcomes[ref.change_id]={'status':child.status}
            store.save(batch)
        batch.status='verified' if all(batch.outcomes.get(ref.change_id,{}).get('status')=='verified' for ref in batch.children) else 'partial'
        store.save(batch);return batch
