import shutil
import subprocess
from pathlib import Path

import pytest

from socialctl.media import (
    MediaInvalida,
    MediaNoEncontrada,
    leer_media,
    ruta_relativa_efectiva,
    validar_media,
)
from socialctl.models import MediaAsset, MediaKind, Platform, PlatformPost


@pytest.fixture(scope="session")
def _clip_vertical_compartido(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Codifica el clip 1080x1920 de 4s una única vez por sesión (ver el mismo
    hallazgo de rendimiento -I7 de la revisión final- en `tests/test_postfile.py`):
    este módulo solo lo lee (nunca lo modifica), así que reencodearlo en cada
    test que lo usa es trabajo innecesario.
    """
    destino = tmp_path_factory.mktemp("clip-vertical-compartido") / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "testsrc=size=1080x1920:rate=30:duration=4",
            "-pix_fmt", "yuv420p", str(destino),
        ],
        check=True,
        capture_output=True,
    )
    return destino


@pytest.fixture
def clip_vertical(tmp_path, _clip_vertical_compartido) -> Path:
    """Una COPIA, propia de este test, del clip generado una vez por sesión."""
    destino = tmp_path / "clip.mp4"
    shutil.copyfile(_clip_vertical_compartido, destino)
    return destino


@pytest.fixture
def clip_horizontal_rotado_90(tmp_path) -> Path:
    """Genera un clip horizontal 1920x1080 con metadatos de rotación de 90 grados.

    Simula lo que graba un móvil: los píxeles codificados quedan en horizontal
    (1920x1080) pero el contenedor lleva una matriz de visualización que indica
    una rotación de 90 grados, de modo que el vídeo debe *verse* en vertical
    (1080x1920).

    No basta con `-metadata:s:v rotate=90` al codificar: en la versión de
    ffmpeg de este entorno (9.0.1) esa opción no se traduce en la matriz de
    visualización del contenedor (se comprobó con ffprobe que el archivo
    resultante no llevaba metadato alguno). La vía que sí funciona aquí es
    generar primero el clip base sin rotación y, en un segundo paso, remuxarlo
    con `-display_rotation` (opción de entrada) y `-c copy`, que fija la
    rotación de visualización sin decodificar ni tocar los píxeles.
    """
    base = tmp_path / "clip_base.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "testsrc=size=1920x1080:rate=30:duration=1",
            "-pix_fmt", "yuv420p", str(base),
        ],
        check=True,
        capture_output=True,
    )

    destino = tmp_path / "clip_rotado.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-display_rotation:v", "90",
            "-i", str(base),
            "-c", "copy", str(destino),
        ],
        check=True,
        capture_output=True,
    )

    # Verificación de que el archivo generado realmente lleva el metadato de
    # rotación antes de usarlo en el test (si no lo llevara, el test de
    # rotación no estaría probando nada real).
    comprobacion = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=width,height:stream_side_data=rotation",
            "-of", "json", str(destino),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert '"rotation"' in comprobacion.stdout, (
        "el clip generado no lleva metadato de rotación; "
        f"salida de ffprobe: {comprobacion.stdout}"
    )

    return destino


def test_leer_media_extrae_dimensiones_y_duracion(clip_vertical):
    asset = leer_media(clip_vertical)
    assert asset.kind is MediaKind.VIDEO
    assert asset.width == 1080
    assert asset.height == 1920
    assert asset.duration_s == pytest.approx(4, abs=0.5)
    assert asset.size_bytes > 0


def test_leer_media_guarda_la_ruta_relativa_indicada(clip_vertical):
    asset = leer_media(clip_vertical, ruta_relativa="short/S2.mp4")
    assert asset.ruta_relativa == "short/S2.mp4"


def test_leer_media_sin_ruta_relativa_la_deja_vacia(clip_vertical):
    asset = leer_media(clip_vertical)
    assert asset.ruta_relativa == ""


def test_leer_media_falla_si_no_existe(tmp_path):
    with pytest.raises(MediaNoEncontrada):
        leer_media(tmp_path / "no-existe.mp4")


def test_leer_media_falla_si_el_archivo_esta_corrupto(tmp_path):
    """Un archivo que existe pero no es media legible debe dar un error claro.

    Se comprobó a mano con `ffprobe` (versión 9.0.1) que, sobre un .mp4 que en
    realidad es texto plano, la ruta absoluta pasada como argumento reaparece
    tal cual en stderr (p. ej. ".../corrupto.mp4: Invalid data found when
    processing input") y que la causa específica y estable que antecede a esa
    línea genérica es "moov atom not found".
    """
    corrupto = tmp_path / "corrupto.mp4"
    corrupto.write_text("esto no es un video, es texto plano")

    with pytest.raises(MediaInvalida) as info:
        leer_media(corrupto)

    mensaje = str(info.value)
    assert mensaje.count(corrupto.name) == 1
    assert str(tmp_path) not in mensaje
    assert "moov atom not found" in mensaje


def test_leer_media_respeta_rotacion_de_video_vertical(clip_horizontal_rotado_90):
    """Un clip codificado en horizontal con rotación de 90° debe leerse en vertical."""
    asset = leer_media(clip_horizontal_rotado_90)
    assert asset.width == 1080
    assert asset.height == 1920


def test_video_demasiado_corto_para_tiktok(clip_vertical):
    corto = MediaAsset(
        path=clip_vertical, kind=MediaKind.VIDEO,
        width=1080, height=1920, duration_s=1.0, size_bytes=1000,
    )
    post = PlatformPost(platform=Platform.TIKTOK, body="x", media=[corto])
    errores = validar_media(post)
    assert any('lasts' in e.motivo for e in errores)


def test_video_valido_para_tiktok_no_da_errores(clip_vertical):
    asset = leer_media(clip_vertical)
    post = PlatformPost(platform=Platform.TIKTOK, body="x", media=[asset])
    assert validar_media(post) == []


def test_aspect_ratio_incorrecto_para_tiktok(clip_vertical):
    horizontal = MediaAsset(
        path=clip_vertical, kind=MediaKind.VIDEO,
        width=1920, height=1080, duration_s=10.0, size_bytes=1000,
    )
    post = PlatformPost(platform=Platform.TIKTOK, body="x", media=[horizontal])
    errores = validar_media(post)
    assert any(e.campo == "media" and "16:9" in e.motivo for e in errores)


def test_aspect_ratio_sin_divisor_comun_muestra_dimensiones_reales(clip_vertical):
    """Con dimensiones que no comparten divisor común, `_ratio` no simplifica
    (p. ej. 1081:1000) y el mensaje debe seguir siendo legible gracias a que
    incluye también las dimensiones reales del archivo, no solo el ratio.
    """
    rara = MediaAsset(
        path=clip_vertical, kind=MediaKind.VIDEO,
        width=1081, height=1000, duration_s=10.0, size_bytes=1000,
    )
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[rara])
    errores = validar_media(post)
    assert any(
        e.campo == "media" and "1081x1000" in e.motivo for e in errores
    )


def test_instagram_sin_media_da_error():
    post = PlatformPost(platform=Platform.INSTAGRAM, body="solo texto", media=[])
    errores = validar_media(post)
    assert any('requires' in e.motivo for e in errores)


def test_facebook_sin_media_es_valido():
    post = PlatformPost(platform=Platform.FACEBOOK, body="solo texto", media=[])
    assert validar_media(post) == []


@pytest.mark.parametrize("platform", list(Platform))
def test_no_descarta_un_segundo_archivo(platform):
    assets = [
        MediaAsset(
            path=Path(name),
            kind=MediaKind.VIDEO,
            width=1080,
            height=1920,
            duration_s=30,
            size_bytes=1024,
        )
        for name in ("a.mp4", "b.mp4")
    ]
    post = PlatformPost(
        platform=platform,
        body="Texto",
        title="Título",
        media=assets,
    )

    errors = validar_media(post)

    assert any(
        error.campo == "media" and '1 file' in error.motivo
        for error in errors
    )


# --- ruta_relativa_efectiva: el punto único que resuelve el "sin subcarpeta" ---


def test_ruta_relativa_efectiva_devuelve_la_ruta_relativa_si_la_hay(clip_vertical):
    asset = leer_media(clip_vertical, ruta_relativa="short/S2.mp4")
    assert ruta_relativa_efectiva(asset) == "short/S2.mp4"


def test_ruta_relativa_efectiva_usa_el_nombre_de_archivo_si_no_hay_ruta_relativa(
    clip_vertical,
):
    asset = leer_media(clip_vertical)
    assert ruta_relativa_efectiva(asset) == clip_vertical.name
