"""Link measured items to publishing posts using resultado.json. Only confirmed publications qualify; unmatched external items retain slug None."""

from __future__ import annotations

import json
import re
import warnings

from socialctl.brands import Brand
from socialctl.metricas.modelos import Snapshot
from socialctl.models import Platform, PostStatus

_ID_DE_VIDEO_TIKTOK = re.compile(r"/video/(\d+)")


def _id_de_pieza(red: str, entrada: dict) -> str | None:
    """Resolve ID used by metrics reader. TikTok publish_id is not video ID; extract actual ID from shared video URL or return None."""
    if red == Platform.TIKTOK.value:
        encontrado = _ID_DE_VIDEO_TIKTOK.search(entrada.get("url") or "")
        return encontrado.group(1) if encontrado else None

    id_pieza = entrada.get("platform_id")
    return id_pieza if isinstance(id_pieza, str) and id_pieza else None


def mapa_de_slugs(brand: Brand) -> dict[tuple[str, str], str]:
    """Build (platform, item ID) to slug mapping from post results; silently skip corrupt historical files because linking is optional enrichment."""
    mapa: dict[tuple[str, str], str] = {}
    carpeta = brand.dir_posts
    if not carpeta.is_dir():
        return mapa

    for fichero in sorted(carpeta.glob("*/resultado.json")):
        try:
            datos = json.loads(fichero.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(datos, dict):
            continue
        entradas = datos.get("resultados")
        if not isinstance(entradas, list):
            continue

        # El slug es el nombre de la CARPETA, no el campo `slug` del JSON:
        # la carpeta es lo que el gestor tiene que abrir después.
        slug = fichero.parent.name

        for entrada in entradas:
            if not isinstance(entrada, dict):
                continue
            if entrada.get("status") != PostStatus.PUBLICADO.value:
                continue
            red = entrada.get("platform")
            if not isinstance(red, str):
                continue
            id_pieza = _id_de_pieza(red, entrada)
            if id_pieza is None:
                continue
            # Si dos posts reclamaran la misma pieza (un reintento que
            # duplicó, por ejemplo), gana el primero en orden alfabético de
            # carpeta, que es estable entre ejecuciones. Si ocurre, avisa.
            clave = (red, id_pieza)
            anterior = mapa.get(clave)
            if anterior is None:
                mapa[clave] = slug
            else:
                warnings.warn(
                    f"resultado.json: two posts claim the same pair "
                    f"(red={red!r}, id={id_pieza!r}): post with slug {anterior!r} "
                    f"is kept; post with slug {slug!r} is discarded. Review "
                    "those two folders in posts/ to determine which is the "
                    "actual publication.",
                    stacklevel=3,
                )

    return mapa


def asignar_slugs(brand: Brand, snapshot: Snapshot) -> None:
    """Assign matching publishing post slug to snapshot items without overwriting any supplied slug."""
    mapa = mapa_de_slugs(brand)
    if not mapa:
        return

    for platform, lectura in snapshot.redes.items():
        for pieza in lectura.piezas:
            if pieza.slug:
                continue
            pieza.slug = mapa.get((platform.value, pieza.id))
