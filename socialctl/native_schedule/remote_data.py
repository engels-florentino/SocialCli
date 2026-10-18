"""Validate native peer data before exposing approval or operation evidence."""
import json

from .approval import SCHEMA, hash_value, validate_payload
from .models import NativeJob, NativeObservation


def validate_data(rows, *, command, brand, jobs, request_args):
    by_id = {job['id']: job for job in jobs}
    specific = {'approve', 'dispatch', 'reconcile', 'handoff', 'submit-marker', 'cancel', 'reschedule'}
    expected = request_args[1] if command in specific and len(request_args) > 1 else None

    def shape(value, required, optional=()):
        if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
            raise ValueError('Invalid native data shape')

    def job(value):
        parsed = NativeJob.model_validate_json(value if isinstance(value, str) else json.dumps(value))
        if parsed.brand != brand or expected is not None and parsed.id != expected:
            raise ValueError('Mismatched native data identity')
        return parsed

    def target(identifier):
        if expected is not None and identifier != expected or identifier not in by_id:
            raise ValueError('Unknown native result target')
        return by_id[identifier]

    def approval(value):
        shape(value, ('schema', 'intent', 'payload', 'media'))
        if value['schema'] != SCHEMA:
            raise ValueError('Invalid native approval schema')
        intent = value['intent']
        shape(intent, ('brand','platform','account_id','legacy_entry_id','publish_at',
            'timezone_name','dispatch_after','route','media_hash','content_hash'), ('schedule_mode',))
        parsed = job(dict(intent, id=expected or 'remote-preview', created_at=intent['dispatch_after'], updated_at=intent['dispatch_after']))
        validate_payload(parsed.platform, value['payload'])
        if not isinstance(value['media'], list):
            raise ValueError('Invalid native media hashes')
        for media in value['media']:
            shape(media, ('path', 'sha256'))
        if hash_value(value['payload']) != parsed.content_hash or hash_value(value['media']) != parsed.media_hash:
            raise ValueError('Native approval content mismatch')

    def preview(value):
        shape(value, ('kind','job_id','remote_ref','account_id','platform','route','before','after','payload','consequence'))
        before, after = job(value['before']), job(value['after'])
        target(value['job_id'])
        if (before.id != after.id or before.id != value['job_id'] or before.account_id != after.account_id
                or before.platform != after.platform or before.remote_id != value['remote_ref']
                or before.account_id != value['account_id'] or before.platform != value['platform']
                or value['kind'] not in {'cancel','reschedule'} or value['route'] != 'official_ui'
                or hash_value(value['payload']) != before.content_hash or before.content_hash != after.content_hash):
            raise ValueError('Mismatched native action preview')
        if command in {'cancel','reschedule'} and value['kind'] != command:
            raise ValueError('Wrong native action kind')

    def action(value):
        shape(value, ('id','kind','digest','status','job','preview'), ('submitted_at','created_at'))
        parsed = job(value['job'])
        preview(value['preview'])
        if (value['status'] not in {'approved','submitted','confirmed'} or value['kind'] != value['preview']['kind']
                or value['digest'] != hash_value(value['preview']) or parsed.model_dump(mode='json') != value['preview']['after']):
            raise ValueError('Invalid native action')

    for annotated in rows:
        row = {k: v for k, v in annotated.items() if k != 'next_action'}
        if row.get('source') == 'checkpoint':
            shape(row, ('job_id','attempt_id','remote_id','source'))
            # Snapshots may include other jobs in the same explicit brand.
            known = by_id.get(row['job_id'])
            if known is None or known['attempt_id'] != row['attempt_id'] or not isinstance(row['remote_id'], str) or not row['remote_id'].strip():
                raise ValueError('Invalid checkpoint identity')
        elif command == 'prepare':
            approval(row)
        elif command in {'dispatch','reconcile'}:
            required = ('job_id','state','notices') + (('observations','instruction') if command == 'reconcile' else ())
            shape(row, required)
            known = target(row['job_id'])
            if row['state'] != known['state'] or not isinstance(row['notices'], list):
                raise ValueError('Mismatched native result state')
            for raw in row.get('observations', []):
                obs = NativeObservation.model_validate_json(json.dumps(raw))
                if obs.job_id != known['id'] or obs.platform != known['platform'] or obs.account_id != known['account_id']:
                    raise ValueError('Mismatched native observation')
        elif command in {'cancel','reschedule'}:
            if 'document' in row:
                shape(row, ('document','digest'))
                preview(row['document'])
                if row['digest'] != hash_value(row['document']):
                    raise ValueError('Mismatched action digest')
            else:
                action(row)
        elif command == 'handoff':
            if 'action' in row:
                shape(row, ('action','upload_allowed','instruction'))
                action(row['action'])
                if row['upload_allowed'] is not False:
                    raise ValueError('Action handoff must not upload')
            else:
                shape(row, ('schema','state','job','payload','schedule','approval_digest','content_hash','media_hash','media','media_integrity','delivery','operator_policy'))
                shape(row['job'], ('id','brand','platform','account_id'))
                known = target(row['job']['id'])
                if row['job'] != {k: known[k] for k in ('id','brand','platform','account_id')}:
                    raise ValueError('Mismatched handoff identity')
                for key in ('state','approval_digest','content_hash','media_hash'):
                    if row[key] != known[key]:
                        raise ValueError('Mismatched handoff state')
                from datetime import datetime
                from zoneinfo import ZoneInfo
                when = datetime.fromisoformat(known['publish_at'].replace('Z', '+00:00'))
                shape(row['schedule'], ('local','utc','timezone'))
                if (row['schema'] != 'socialctl.native-schedule.handoff.v1'
                        or row['schedule']['timezone'] != known['timezone_name']
                        or datetime.fromisoformat(row['schedule']['utc'].replace('Z', '+00:00')) != when
                        or row['schedule']['local'] != when.astimezone(ZoneInfo(known['timezone_name'])).isoformat()):
                    raise ValueError('Mismatched handoff schedule')
                if hash_value(row['payload']) != known['content_hash']:
                    raise ValueError('Mismatched handoff payload')
        elif command == 'deliver':
            shape(row, ('delivered','awaiting_ui','instruction'), ('needs_attention',))
            if not isinstance(row['awaiting_ui'], list) or any(target(i)['state'] != 'awaiting_ui' for i in row['awaiting_ui']):
                raise ValueError('Invalid delivered jobs')
            attention = row.get('needs_attention', [])
            if not isinstance(attention, list) or any(target(i)['state'] not in {'blocked', 'uncertain'} for i in attention):
                raise ValueError('Invalid jobs requiring attention')
        elif command == 'migration-prepare':
            shape(row, ('version','brand','brand_root','manifest','manifest_hash','inventory','legacy_entries','legacy_results','native_jobs','transfers','excluded','protections','instruction','digest'))
            if row['version'] != 1 or row['brand'] != brand or row['digest'] != hash_value({k:v for k,v in row.items() if k != 'digest'}):
                raise ValueError('Invalid migration proposal')
            for raw in row['native_jobs']:
                job(raw)
            for transfer in row['transfers']:
                job(transfer['job'])
                approval(transfer['native_preview'])
        elif command in {'migration-apply','migration-recover'}:
            shape(row, ('status','digest','publication_approved'), ('prepared','historical_guard_retained'))
            if row['status'] != 'committed' or row['publication_approved'] is not False or row['digest'] != request_args[request_args.index('--digest')+1]:
                raise ValueError('Invalid migration result')
        else:
            # status/approve/submit-marker emit snapshots only.
            raise ValueError('Unexpected native result variant')
