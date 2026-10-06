"""Durable migration files and byte-preserving YAML scalar relocation."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import yaml

from socialctl.scheduler import ScheduleError, ScheduleStore


def durable_write(path: Path, data: bytes) -> None:
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        ScheduleStore._sync_directory(directory.parent)
    fd, temporary = tempfile.mkstemp(prefix=".migration-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        ScheduleStore._sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    durable_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def rewrite_media(raw: bytes, replacements: dict[str, dict[str, str]]) -> bytes:
    """Replace only media scalar tokens. Aliases/anchors/duplicate keys fail closed."""
    text = raw.decode("utf-8")
    if any(isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for token in yaml.scan(text)):
        raise ScheduleError("YAML aliases/anchors do not support safe migration")
    root = yaml.compose(text)
    def mapping(node):
        if not isinstance(node, yaml.MappingNode):
            raise ScheduleError("unsupported YAML structure")
        result = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.value in result or key.value == "<<":
                raise ScheduleError("ambiguous YAML keys")
            result[key.value] = value
            if isinstance(value, yaml.MappingNode):
                mapping(value)
        return result
    platforms = mapping(mapping(root)["platforms"])
    spans = []
    for platform, paths in replacements.items():
        media = mapping(platforms[platform]).get("media")
        if not isinstance(media, yaml.SequenceNode):
            raise ScheduleError("media must be an explicit list")
        for item in media.value:
            if not isinstance(item, yaml.ScalarNode) or item.tag != "tag:yaml.org,2002:str" or item.value not in paths:
                raise ScheduleError("ambiguous or modified YAML media")
            spans.append((item.start_mark.index, item.end_mark.index, json.dumps(paths[item.value])))
    for start, end, replacement in sorted(spans, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text.encode("utf-8")
