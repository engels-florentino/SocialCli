"""Resolve explicit creator-owned source IDs before the existing approval flow."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import yaml

from socialctl.models import Platform


class StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError('source-videos.yml contains duplicate/non-string keys')
        result[key] = loader.construct_object(value_node)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _entry(brand, slug):
    path = brand.raiz / 'source-videos.yml'
    try:
        with path.open('rb') as handle:
            raw = handle.read(256 * 1024 + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise ValueError('cannot read source-videos.yml') from None
    try:
        if len(raw) > 256 * 1024:
            raise ValueError('source-videos.yml exceeds 256 KiB')
        data = yaml.load(raw.decode('utf-8'), Loader=StrictLoader)
        if (not isinstance(data, dict) or set(data) != {'version', 'clips'}
                or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['clips'], dict)):
            raise ValueError('source-videos.yml requires version: 1 and an explicit clips mapping')
        for key, row in data['clips'].items():
            if (not isinstance(key, str) or not key or '/' in key or '\\' in key
                    or key in {'.', '..'} or not isinstance(row, dict)
                    or not {'video_id'} <= set(row) <= {'video_id', 'series'}
                    or not isinstance(row['video_id'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{11}', row['video_id'])
                    or ('series' in row and (not isinstance(row['series'], str) or not row['series'].strip()))):
                raise ValueError('invalid exact clip mapping: use a quoted 11-character video_id and optional series')
        return data['clips'].get(slug)
    except (yaml.YAMLError, UnicodeError):
        raise ValueError('source-videos.yml is not valid strict YAML') from None


def resolve_source_link(brand, post_slug):
    entry = _entry(brand, post_slug)
    return f"https://www.youtube.com/watch?v={entry['video_id']}" if entry else None


def _digest(slug, entry):
    return hashlib.sha256(json.dumps({'version': 1, 'slug': slug, 'entry': entry},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _video_links(text):
    for raw in re.findall(r'(?:https?://|www\.)[^\s<>]+', text or ''):
        url = urlsplit(raw if '://' in raw else f'https://{raw}')
        host = (url.hostname or '').casefold()
        parts = url.path.strip('/').split('/')
        if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
            ids = parse_qs(url.query).get('v', []) if url.path == '/watch' else parts[1:2] if parts[0] in {'shorts', 'embed', 'live'} else []
            for ident in ids:
                yield ident
        elif host in {'youtu.be', 'www.youtu.be'}:
            yield parts[0]


def apply_source_mapping(brand, post, *, required=False):
    entry = _entry(brand, post.slug)
    if entry is None:
        if required:
            raise ValueError('source link unresolved: add this exact post slug to source-videos.yml')
        return post
    post = post.model_copy(deep=True)
    ident = entry['video_id']
    url = f'https://www.youtube.com/watch?v={ident}'
    generated = f'Full video: {url}'
    for platform, pp in post.platforms.items():
        if (pp.source_video_id not in {None, ident} or pp.content_origin not in {None, 'youtube_long'}
                or any(link != ident for field in (pp.body, pp.link, pp.first_comment)
                       for link in _video_links(field))):
            raise ValueError(f'{platform.value}: explicit source/link conflicts with source-videos.yml')
        pp.source_video_id, pp.content_origin = ident, 'youtube_long'
        if platform is Platform.TIKTOK:
            if pp.first_comment:
                raise ValueError('TikTok source linking uses the caption; first comments are unsupported')
            if ident not in set(_video_links(pp.body)):
                pp.body = f'{pp.body}\n\n{generated}'
        elif ident not in set(_video_links(pp.first_comment)):
            pp.first_comment = f'{pp.first_comment}\n{generated}' if pp.first_comment else generated
    post.source_mapping_digest = _digest(post.slug, entry)
    return post


def verify_source_binding(brand, slug, mapping_digest):
    if mapping_digest is not None:
        entry = _entry(brand, slug)
        if entry is None or _digest(slug, entry) != mapping_digest:
            raise ValueError('source mapping changed after preview; run --dry-run and approve again')


def verify_source_mapping(brand, post):
    verify_source_binding(brand, post.slug, post.source_mapping_digest)
