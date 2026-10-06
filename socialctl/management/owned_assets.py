"""Bounded supplied PNG/JPEG inspection; no generation, decoding or conversion."""
from __future__ import annotations

import os
import stat
import struct
from pathlib import Path

import httpx

from socialctl.management.resource_changes import digest
from socialctl.management.youtube_resources import ResourceError


def dimensions(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33 and data[12:16] == b"IHDR" and data[-8:] == b"IEND\xaeB`\x82":
        return "image/png", *struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD9, 0xDA}:
                break
            if marker in {0x01, *range(0xD0, 0xD9)}:
                continue
            if offset + 2 > len(data):
                break
            size = int.from_bytes(data[offset:offset + 2], "big")
            if size < 2 or offset + size > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and size >= 8:
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                return "image/jpeg", width, height
            offset += size
    raise ResourceError("se requieren bytes PNG/JPEG suministrados con dimensiones verificables")


def inspect_owned_asset(path, action):
    maximum = {"banner-upload": 6, "watermark-set": 10, "image-insert": 2, "image-update": 50}.get(action)
    if maximum is None:
        raise ResourceError("la acción no admite archivo")
    maximum *= 1024 * 1024
    path = Path(path).absolute()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
                raise ResourceError(f"archivo no regular, vacío o superior al límite local {maximum} bytes")
            data = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size:
                raise ResourceError("el archivo cambió al leerlo")
    except OSError:
        raise ResourceError("no se pudo leer el archivo regular sin symlinks") from None
    mime, width, height = dimensions(data)
    if not 0 < width <= 2147483647 or not 0 < height <= 2147483647:
        raise ResourceError("dimensiones vacías")
    if action == "banner-upload" and (width < 2048 or height < 1152 or width * 9 != height * 16):
        raise ResourceError("banner exige 16:9 y mínimo 2048x1152")
    if action.startswith("image-") and width != height:
        raise ResourceError("la imagen de playlist debe ser cuadrada")
    return {"path": str(path), "sha256": digest(data), "size": len(data), "mime": mime,
        "width": width, "height": height, "local_max_bytes": maximum,
        "local_format_validation": "container_header_and_dimensions/provider_validation_required"}, data


def banner_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise ResourceError("URL de banner no válida")
    try:
        url = httpx.URL(value)
    except Exception:
        raise ResourceError("URL de banner no válida") from None
    if (url.scheme != "https" or url.userinfo or url.fragment or url.port not in {None, 443}
        or not url.host or not any(url.host == host or url.host.endswith("." + host) for host in ("googleusercontent.com", "ggpht.com"))):
        raise ResourceError("URL de banner fuera de los hosts HTTPS de imágenes de YouTube")
    return value
