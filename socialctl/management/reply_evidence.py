"""Scoped platform observations, independent of local reply write journals."""
from socialctl.management.changes import now_utc
from socialctl.management.meta_comments import CommentError


def reply_evidence(platform, account_id, media_id, parent_id, *, reply_ids=(), complete=False, thread_id=None):
    ids = sorted(set(reply_ids))
    return {'platform':platform, 'account_id':account_id, 'media_id':media_id,
        'parent_id':parent_id, 'thread_id':thread_id, 'reply_ids':ids,
        'state':'owner_reply_found' if ids else 'no_owner_reply_found' if complete else 'unknown',
        'complete':bool(complete), 'observed_at':now_utc().isoformat()}


def bound_evidence(row):
    evidence = row.get('reply_evidence')
    if not evidence:
        return None
    # Evidence belongs to the direct parent only, never every external sibling.
    if row.get('parent_id') or any(evidence.get(k) != row.get(k) for k in ('platform', 'account_id', 'media_id', 'thread_id')) or evidence.get('parent_id') != row.get('comment_id'):
        raise ValueError('reply evidence differs from the observed account/media/parent')
    ids = evidence.get('reply_ids')
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('invalid reply evidence IDs')
    expected = 'owner_reply_found' if ids else 'no_owner_reply_found' if evidence.get('complete') is True else 'unknown'
    if evidence.get('state') != expected:
        raise ValueError('invalid reply evidence state')
    return evidence


def retain_positive(previous, current):
    """A failed/partial traversal cannot erase previously verified owner replies."""
    if previous and previous.get('state') == 'owner_reply_found' and (not current or not current.get('complete')):
        if current and current.get('reply_ids'):
            return {**current, 'reply_ids':sorted(set(previous['reply_ids'] + current['reply_ids']))}
        return previous
    return current


def youtube_reply_evidence(client, video_id, thread_id, parent_id, report):
    ids = []
    complete = report.get('complete') is True
    for row in report.get('items', []):
        snippet = row['snippet']
        author = (snippet.get('authorChannelId') or {}).get('value')
        if not isinstance(author, str) or not author:
            complete = False
        if author == client.configured_channel_id:
            ids.append(row['id'])
    return reply_evidence('youtube', client.configured_channel_id, video_id, parent_id,
        thread_id=thread_id, reply_ids=ids, complete=complete)


def meta_reply_evidence(client, media_id, parent_id, listing, *, from_start=True):
    from socialctl.management.meta_comment_inbox import verified_inbox_rows
    rows, skipped = verified_inbox_rows(listing['data'], client.platform, media_id, parent_id)
    authors = {ident:(raw.get('from') or {}).get('id') for ident, raw in rows.items()}
    return reply_evidence(client.platform.value, client.account_id, media_id, parent_id,
        reply_ids=[ident for ident, author in authors.items() if author == client.account_id],
        complete=listing.get('complete') is True and from_start and not skipped and all(authors.values()))


class ReplyCoverageError(CommentError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def check_reply_coverage(client, row):
    """Require a fresh selected-parent traversal for both inbox draft entrypoints."""
    if row['platform'] == 'youtube':
        report = client.list_replies(row['media_id'], row['thread_id'], row['comment_id'])
        evidence = youtube_reply_evidence(client, row['media_id'], row['thread_id'], row['comment_id'], report)
    else:
        report = client.list_comments(row['media_id'], parent_id=row['comment_id'])
        evidence = meta_reply_evidence(client, row['media_id'], row['comment_id'], report)
    if evidence['state'] == 'owner_reply_found':
        raise ReplyCoverageError('existing_platform_reply', 'Verified owner reply exists; refresh the inbox and inspect before another draft')
    if evidence['state'] != 'no_owner_reply_found':
        raise ReplyCoverageError('reply_coverage_unknown', 'Owner reply coverage is incomplete or authors unavailable; refresh/reconcile before drafting')
    return evidence
