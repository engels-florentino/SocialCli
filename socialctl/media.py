"""Read and validate supplied media without altering it."""

from __future__ import annotations

import json
import math
import subprocess
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


def _ffprobe(path: Path) -> dict:
    try:
        salida = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height:stream_tags=rotate"
                ":stream_side_data=rotation:format=format_name,duration",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
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
    return json.loads(salida.stdout)


def _rotacion_grados(stream: dict) -> int:
    """Extract video stream rotation in degrees when present."""
    tags = stream.get("tags") or {}
    rotate = tags.get("rotate")
    if rotate is not None:
        try:
            return int(rotate)
        except ValueError:
            pass

    for side_data in stream.get("side_data_list") or []:
        rotation = side_data.get("rotation")
        if rotation is not None:
            try:
                return int(rotation)
            except ValueError:
                continue

    return 0


def leer_media(path: Path, *, ruta_relativa: str = "") -> MediaAsset:
    """Read supplied media metadata."""
    if not path.exists():
        raise MediaNoEncontrada(f"file does not exist: {path}")

    datos = _ffprobe(path)
    streams = datos.get("streams") or [{}]
    stream = streams[0]
    duracion = datos.get("format", {}).get("duration")

    kind = MediaKind.IMAGE if path.suffix.lower() in EXTENSIONES_IMAGEN else MediaKind.VIDEO

    width = stream.get("width")
    height = stream.get("height")
    if width and height and _rotacion_grados(stream) % 180 == 90:
        # Rotación de 90/270 grados: width/height codificados no coinciden
        # con las dimensiones de visualización, así que se intercambian.
        width, height = height, width

    return MediaAsset(
        path=path,
        kind=kind,
        width=width,
        height=height,
        duration_s=float(duracion) if duracion and kind is MediaKind.VIDEO else None,
        size_bytes=path.stat().st_size,
        ruta_relativa=ruta_relativa,
    )


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

        if spec.max_bytes and asset.size_bytes > spec.max_bytes:
            errores.append(
                ValidationError(
                    platform=post.platform,
                    campo="media",
                    motivo=(
                        f"{asset.path.name} is {asset.size_bytes / 1024**2:.0f} MB and "
                        f"{post.platform.value} allows {spec.max_bytes / 1024**2:.0f} MB"
                    ),
                )
            )

        if asset.width and asset.height and spec.aspect_ratios:
            ratio = _ratio(asset.width, asset.height)
            if not _ratio_compatible(ratio, spec.aspect_ratios):
                errores.append(
                    ValidationError(
                        platform=post.platform,
                        campo="media",
                        motivo=(
                            f"{asset.path.name} is {ratio} ({asset.width}x{asset.height}) "
                            f"y {post.platform.value} allows {', '.join(spec.aspect_ratios)}"
                        ),
                    )
                )

    return errores
