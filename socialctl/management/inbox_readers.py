"""Command-local binding reuse for GET-only inbox scans.

These clients are never used for prepare/apply/reconcile. A pinned credential
and verified resource bindings last only for this scan, never across commands.
"""
from socialctl.management.youtube_community import YouTubeCommunityClient
from socialctl.management.youtube_resources import ResourceError
from socialctl.management.meta_comments import MetaCommentsClient, CommentError, _id
from socialctl.models import Platform


class YouTubeInboxClient(YouTubeCommunityClient):
    def __init__(self, brand, client):
        super().__init__(brand, client)
        self._scan_actor = None
        self._scan_videos = {}
        self._scan_threads = {}

    def _request(self, method, url, **kwargs):
        if method != 'GET':
            raise ResourceError('Inbox scan is read-only')
        return super()._request(method, url, **kwargs)

    def _update_parts(self, *args, **kwargs):
        raise ResourceError('Inbox scan is read-only')

    def _authenticated_channel(self, token):
        if self._scan_actor is None:
            self._scan_actor = super()._authenticated_channel(token)
        return self._scan_actor

    def inspect(self, video_id):
        if video_id not in self._scan_videos:
            self._scan_videos[video_id] = super().inspect(video_id)
        return self._scan_videos[video_id]

    def list_threads(self, video_id, **kwargs):
        report = super().list_threads(video_id, **kwargs)
        for row in report['items']:
            self._scan_threads[(video_id, row['id'])] = row
        return report

    def thread(self, video_id, thread_id):
        key = (video_id, thread_id)
        if key in self._scan_threads:
            return self._scan_threads[key]
        return super().thread(video_id, thread_id)


class MetaInboxClient(MetaCommentsClient):
    def __init__(self, brand, platform, client):
        super().__init__(brand, platform, client)
        self._scan_media = {}
        self._scan_parents = set()

    def _request(self, method, path, **kwargs):
        if method != 'GET':
            raise CommentError('Inbox scan is read-only')
        return super()._request(method, path, **kwargs)

    def inspect_media(self, media_id):
        media_id = _id(media_id)
        if media_id not in self._scan_media:
            self._scan_media[media_id] = super().inspect_media(media_id)
        return self._scan_media[media_id]

    def list_comments(self, media_id, *, parent_id=None, max_pages=100,
                      include_moderation=False, after=None):
        if type(include_moderation) is not bool:
            raise CommentError('include_moderation must be boolean')
        media_id = _id(media_id)
        self.inspect_media(media_id)
        if parent_id is not None:
            parent_id = _id(parent_id)
            if (media_id, parent_id) not in self._scan_parents:
                self.find_comment(media_id, parent_id)
        result = self._list(parent_id or media_id,
            'replies' if parent_id and self.platform is Platform.INSTAGRAM else 'comments',
            max_pages, include_moderation=include_moderation, after=after)
        if parent_id is None:
            # Keep only unambiguous top-level rows with verifiable text and
            # parent binding. Unknown parents still use the ordinary verifier.
            from socialctl.management.meta_comment_inbox import verified_inbox_rows
            rows, _ = verified_inbox_rows(result['data'], self.platform, media_id)
            self._scan_parents.difference_update((media_id, row.get('id')) for row in result['data'] if isinstance(row.get('id'),str))
            self._scan_parents.update((media_id, ident) for ident in rows)
        return result
