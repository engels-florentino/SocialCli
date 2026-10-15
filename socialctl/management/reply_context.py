"""Explicit mapping from an observed reply to its actual thread write target."""


def verify_reply_context(client, context, *, media_id, parent_id, thread_id=None):
    platform = 'youtube' if thread_id is not None else client.platform.value
    account = client.configured_channel_id if platform == 'youtube' else client.account_id
    if platform == 'youtube':
        from socialctl.management.youtube_resources import ResourceConflict as ContextError
    else:
        from socialctl.management.meta_comments import CommentError as ContextError
    if (context.get('platform'), context.get('account_id'), context.get('media_id'),
            context.get('parent_id'), context.get('thread_id')) != (
            platform, account, media_id, parent_id, thread_id):
        raise ContextError('reply context does not match the explicit parent mapping')
    if platform == 'youtube':
        raw = client.find_comment(media_id, thread_id, context['comment_id'], parent_id=parent_id)
        snippet = raw['snippet']
        fresh = (raw['id'], (snippet.get('authorChannelId') or {}).get('value'),
            snippet.get('textOriginal', snippet.get('textDisplay')))
    else:
        raw = client.find_comment(media_id, context['comment_id'], parent_id=parent_id)
        fresh = (raw['id'], raw['author_id'], raw['text'])
    if fresh != (context.get('comment_id'), context.get('author_id'), context.get('text')):
        raise ContextError('reply context changed since observation; sync and review again')
