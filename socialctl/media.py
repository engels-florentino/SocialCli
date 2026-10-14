"""Read and validate supplied media without altering it."""

from __future__ import annotations

import json
import math
import subprocess
import stat
from fractions import Fraction
from pathlib import Path

from socialctl.formatter import PLATFORM_SPECS
from socialctl.models import (
    MediaAsset,
    MediaKind,
    PlatformPost,
    ValidationError,
)

EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


class MediaNoEncontrada(Exception):
    """The specified media file does not exist."""


class MediaInvalida(Exception):
    """The file exists but cannot be read as media."""
    def __init__(self, message, *, code='media_unreadable'):
        super().__init__(message)
        self.code = code


def _ffprobe(path: Path) -> dict:
    try:
        salida = subprocess.run(
            [
                "ffprobe", "-v", "error", "-protocol_whitelist", "file",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,avg_frame_rate,pix_fmt,sample_aspect_ratio,sample_rate,channels:stream_disposition=attached_pic:stream_tags=rotate"
                ":stream_side_data=rotation:format=format_name,duration:format_tags=major_brand",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise MediaInvalida('ffprobe is required to inspect supplied media', code='probe_unavailable') from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaInvalida('media inspection exceeded 30 seconds', code='probe_timeout') from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        # ffprobe suele emitir varias líneas de contexto: primero la causa
        # concreta (p. ej. "moov atom not found") y después una genérica
        # ("Invalid data found when processing input"). Nos quedamos con
        # todas las líneas no vacías para no perder la información útil.
        #
        # ffprobe se invoca con str(path), y esa misma ruta (a menudo
        # absoluta) puede reaparecer como prefijo en cualquiera de las
        # líneas de stderr (p. ej. "/tmp/.../clip.mp4: Invalid data..."); la
        # quitamos para no repetirla ni filtrar rutas del sistema, ya que
        # nuestro propio mensaje ya identifica el archivo por su nombre.
        prefijo = f"{path}: "
        lineas = []
        for linea in stderr.splitlines():
            linea = linea.strip()
            if not linea:
                continue
            if linea.startswith(prefijo):
                linea = linea[len(prefijo):]
            lineas.append(linea)
        causa = "; ".join(lineas) if lineas else "ffprobe provided no further details"
        raise MediaInvalida(
            f"cannot read '{path.name}' as media: {causa}"
        ) from exc
    try:
        if len(salida.stdout) > 1_000_000:
            raise ValueError('probe output exceeds limit')
        data = json.loads(salida.stdout)
        if not isinstance(data, dict):
            raise ValueError('invalid probe shape')
        return data
    except (ValueError, TypeError) as exc:
        raise MediaInvalida('invalid media inspection response') from exc


def _rotacion_grados(stream: dict) -> int:
    """Extract video stream rotation in degrees when present."""
    tags = stream.get("tags") or {}
    rotate = tags.get("rotate")
    if rotate is not None:
        try:
            return int(rotate)
        except (ValueError, TypeError):
            pass

    for side_data in stream.get("side_data_list") or []:
        rotation = side_data.get("rotation")
        if rotation is not None:
            try:
                return int(rotation)
            except (ValueError, TypeError):
                continue

    return 0


def leer_media(path: Path, *, ruta_relativa: str = "") -> MediaAsset:
    """Read supplied media metadata."""
    if not path.exists():
        raise MediaNoEncontrada(f"file does not exist: {path}")

    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise MediaInvalida('supplied media must be a nonempty regular file')
    datos = _ffprobe(path)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise MediaInvalida('media changed during inspection', code='media_changed')
    kind = MediaKind.IMAGE if path.suffix.lower() in EXTENSIONES_IMAGEN else MediaKind.VIDEO
    streams = datos.get('streams')
    if not isinstance(streams, list):
        raise MediaInvalida('visual stream metadata unavailable')
    visual = [row for row in streams if isinstance(row, dict) and row.get('codec_type') == 'video'
              and (kind is MediaKind.IMAGE or not (row.get('disposition') or {}).get('attached_pic'))]
    if len(visual) != 1:
        raise MediaInvalida('exactly one visual stream required; cover art is not video')
    stream = visual[0]
    width, height = stream.get('width'), stream.get('height')
    if any(type(v) is not int or v <= 0 for v in (width, height)):
        raise MediaInvalida('positive visual dimensions required')
    try:
        duration = float(datos.get('format', {}).get('duration')) if kind is MediaKind.VIDEO else None
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise MediaInvalida('finite positive video duration required') from exc
    try:
        fps = float(Fraction(stream.get('avg_frame_rate', '0/1')))
        if not math.isfinite(fps) or fps <= 0: fps = None
    except (TypeError, ValueError, ZeroDivisionError):
        fps = None
    try:
        sar = float(Fraction(stream.get('sample_aspect_ratio', '1:1').replace(':', '/')))
        if not math.isfinite(sar) or sar <= 0: raise ValueError()
    except (TypeError, AttributeError, ValueError, ZeroDivisionError):
        sar = None
    display_ratio = width / height * sar if sar is not None else None
    if _rotacion_grados(stream) % 180 == 90:
        width, height = height, width
        if display_ratio is not None: display_ratio = 1 / display_ratio
    technical = {'video_codec': stream.get('codec_name'), 'container': datos.get('format', {}).get('format_name'),
        'container_brand': (datos.get('format', {}).get('tags') or {}).get('major_brand'),
        'fps': fps, 'pixel_format': stream.get('pix_fmt'), 'display_ratio': display_ratio,
        'audio': [{'codec': row.get('codec_name'), 'sample_rate': row.get('sample_rate'), 'channels': row.get('channels')}
                  for row in streams if isinstance(row, dict) and row.get('codec_type') == 'audio']}
    return MediaAsset(path=path, kind=kind, width=width, height=height,
        duration_s=duration, size_bytes=before.st_size, ruta_relativa=ruta_relativa, probe=technical)


def ruta_relativa_efectiva(asset: MediaAsset) -> str:
    """Return the asset's effective relative path, preserving subdirectories."""
    return asset.ruta_relativa or asset.path.name


def _ratio(width: int, height: int) -> str:
    """Return the simplified width:height aspect ratio."""
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _ratio_compatible(ratio: str, permitidos: list[str], tolerancia: float = 0.02) -> bool:
    """Return whether the ratio is close to an allowed ratio."""
    valor = float(Fraction(ratio.replace(":", "/")))
    for permitido in permitidos:
        objetivo = float(Fraction(permitido.replace(":", "/")))
        if abs(valor - objetivo) <= tolerancia * objetivo:
            return True
    return False


def validar_media(post: PlatformPost) -> list[ValidationError]:
    """Validate supplied media against the platform's limits."""
    spec = PLATFORM_SPECS[post.platform]
    errores: list[ValidationError] = []

    if not post.media:
        if spec.exige_media:
            errores.append(
                ValidationError(
                    platform=post.platform,
                    campo="media",
                    motivo=(
                        f'{post.platform.value} requires an image or video, but none was provided'
                    ),
                )
            )
        return errores

    if len(post.media) > spec.max_media:
        errores.append(
            ValidationError(
                platform=post.platform,
                campo="media",
                motivo=(
                    f'this route supports at most {spec.max_media} file; it does not publish carousels'
                ),
            )
        )

    for asset in post.media:
        if asset.duration_s is not None:
            if spec.min_video_s and asset.duration_s < spec.min_video_s:
                errores.append(
                    ValidationError(
                        platform=post.platform,
                        campo="media",
                        motivo=(
                            f"{asset.path.name} lasts {asset.duration_s:.1f}s and "
                            f"{post.platform.value} requires at least {spec.min_video_s}s"
                        ),
                    )
                )
            if spec.max_video_s and asset.duration_s > spec.max_video_s:
                errores.append(
                    ValidationError(
                        platform=post.platform,
                        campo="media",
                        motivo=(
                            f"{asset.path.name} lasts {asset.duration_s:.1f}s and "
                            f"{post.platform.value} allows at most {spec.max_video_s}s"
                        ),
                    )
                )

        maximum = (8 * 1000**2 if asset.kind is MediaKind.IMAGE else 300 * 1000**2) if post.platform.value == 'instagram' else spec.max_bytes
        if maximum and asset.size_bytes > maximum:
            errores.append(
                ValidationError(
                    platform=post.platform,
                    campo="media",
                    motivo=(
                        f"{asset.path.name} is {asset.size_bytes / 1024**2:.0f} MB and "
                        f"{post.platform.value} allows {maximum / 1024**2:.0f} MiB on this route"
                    ),
                )
            )

        if asset.width and asset.height and spec.aspect_ratios and post.platform.value != 'instagram':
            ratio = _ratio(asset.width, asset.height)
            if not _ratio_compatible(ratio, spec.aspect_ratios):
                errores.append(
                    ValidationError(
                        platform=post.platform,
                        campo="media",
                        motivo=(
                            f"{asset.path.name} is {ratio} ({asset.width}x{asset.height}) "
                            f"and this SocialCli {post.platform.value} route is configured for {', '.join(spec.aspect_ratios)}"
                        ),
                    )
                )

    from socialctl.media_requirements import technical_issues
    for asset in post.media:
        for issue in technical_issues(post.platform, asset):
            errores.append(ValidationError(platform=post.platform, campo='media', motivo=issue['message']))
    return errores
