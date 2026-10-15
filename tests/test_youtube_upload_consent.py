"""Upload approval includes the owner's content certification, never inferred from login."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import pytest
from socialctl.brands import Brand
from socialctl.models import CampaignType, Platform, PlatformPost, Post
from socialctl.publisher import render_preview
from socialctl.scheduler import approval_hash, approval_review_reason
from socialctl.native_schedule.approval import approval_document, hash_value, media_hashes, verify_approval
from socialctl.native_schedule.models import NativeJob


def post():
    return Post(slug='example',brand='Example',campaign=CampaignType.POST_IMAGEN,
        platforms={Platform.YOUTUBE:PlatformPost(platform=Platform.YOUTUBE,title='Example',body='Description')})


def test_youtube_preview_requires_owner_certification_but_excluded_destination_does_not():
    value=post()
    preview=render_preview(value,{Platform.YOUTUBE:[]})
    assert 'By approving this upload, you certify' in preview
    assert 'https://www.youtube.com/howyoutubeworks/policies/community-guidelines/' in preview
    assert 'By approving this upload' not in render_preview(value,{Platform.YOUTUBE:[]},destinos=[])


def test_schedule_rejects_approval_that_predates_upload_certification(tmp_path):
    brand=Brand(nombre='Example',raiz=tmp_path,cuentas={'youtube':{'channel_id':'fictional'}})
    value=post()
    old={'account_identity':{'account':brand.cuentas['youtube'],'brand':'Example','platform':'youtube'},
         'payload':{'body':'Description','first_comment':None,'hashtags':[],'link':None,'media':[],
                    'platform':'youtube','privacy':None,'title':'Example'},'schema':'socialctl.schedule.approval.v2'}
    old_digest='v2:'+hashlib.sha256(json.dumps(old,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
    assert approval_review_reason(old_digest,value,brand,Platform.YOUTUBE) is not None
    assert approval_review_reason(approval_hash(value,brand,Platform.YOUTUBE),value,brand,Platform.YOUTUBE) is None


def test_native_upload_cannot_reuse_digest_without_certification(tmp_path):
    media=tmp_path/'original.mp4';media.write_bytes(b'fictional original supplied by user')
    payload={'copy':'Description','content_origin':'standalone','visibility':'private','first_comment':None,
             'options':{'format':'video'},'media':[{'path':str(media),'origin':'user'}]}
    now=datetime(2035,1,1,tzinfo=timezone.utc)
    job=NativeJob(id='example',brand='Example',platform='youtube',account_id='fictional',
        publish_at=now+timedelta(days=1),dispatch_after=now,timezone_name='UTC',schedule_mode='exact',route='api',created_at=now,updated_at=now,
        content_hash=hash_value(payload),media_hash=hash_value(media_hashes(payload)))
    document=approval_document(job,payload)
    old=dict(document);old.pop('uploader_certification',None)
    with pytest.raises(ValueError,match='Approval digest'):
        verify_approval(job,payload,hash_value(old))
    assert verify_approval(job,payload,hash_value(document))==hash_value(document)
