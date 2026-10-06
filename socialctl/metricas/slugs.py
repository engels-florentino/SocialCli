"""Enlaza cada pieza medida con el post de `<Marca>/posts/` que la publicó.

Una pieza sin `slug` es un número suelto: se sabe cuánto rindió, no qué era.
Con el slug, el gestor de redes puede abrir `<Marca>/posts/<slug>/` y leer el
`post.yml`, el guion y el plan de cortes para deducir el `pilar` y el
`gancho` en vez de preguntárselos al usuario.

El cruce se hace contra los `resultado.json` que escribe
`socialctl/publisher.py`: una entrada por red, con `status` y `platform_id`
(ver `PostResult` en `socialctl/models.py`). Solo cuenta `status ==
"publicado"`: `"pendiente_confirmacion"` -el modo *inbox* de TikTok- significa
que el usuario todavía no ha confirmado la publicación, así que no hay pieza
publicada que medir.

Nada de esto es obligatorio para que `stats` funcione: una pieza publicada
fuera de `socialctl` -los largos que ya existían- simplemente se queda con
`slug: None`, que es lo que el spec (§9) declara.
"""

from __future__ import annotations

import json
import re
import warnings

from socialctl.brands import Brand
from socialctl.metricas.modelos import Snapshot
from socialctl.models import Platform, PostStatus

_ID_DE_VIDEO_TIKTOK = re.compile(r"/video/(\d+)")


def _id_de_pieza(red: str, entrada: dict) -> str | None:
    """Id con el que la red identifica la pieza **al leerla**.

    Para `youtube`, `facebook` e `instagram` es `platform_id` tal cual: el id
    del vídeo, el de la publicación y el del media, que son exactamente los
    que devuelven los lectores de las Tasks 3 y 4.

    Para `tiktok` NO lo es: ahí `platform_id` guarda el `publish_id`, que
    identifica el envío y no el vídeo, y `video/list` nunca devolverá ese
    valor. El id real solo aparece dentro de la URL compartida
    (`.../video/7080213458555737986`), así que se extrae de ahí; si no hay
    URL -hoy el adaptador de TikTok no la guarda-, se devuelve `None` y esa
    pieza se queda sin slug, que es la respuesta honesta.
    """
    if red == Platform.TIKTOK.value:
        encontrado = _ID_DE_VIDEO_TIKTOK.search(entrada.get("url") or "")
        return encontrado.group(1) if encontrado else None

    id_pieza = entrada.get("platform_id")
    return id_pieza if isinstance(id_pieza, str) and id_pieza else None


def mapa_de_slugs(brand: Brand) -> dict[tuple[str, str], str]:
    """`(red, id de la pieza) -> slug`, a partir de `<Marca>/posts/*/resultado.json`.

    Cualquier `resultado.json` ilegible o con una forma que no se reconoce se
    **salta en silencio**: esto es un enriquecimiento opcional del snapshot, y
    un fichero corrupto de un post antiguo no puede impedir que se lea el
    resto ni tumbar el comando entero.
    """
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
                    f"resultado.json: dos posts reclaman la misma pareja "
                    f"(red={red!r}, id={id_pieza!r}): el de slug {anterior!r} "
                    f"se mantiene, el de slug {slug!r} se descarta. Revisa "
                    "esas dos carpetas en posts/ para entender cuál es la "
                    "publicación real.",
                    stacklevel=3,
                )

    return mapa


def asignar_slugs(brand: Brand, snapshot: Snapshot) -> None:
    """Pone a cada pieza del snapshot el slug del post que la publicó.

    Muta el snapshot en el sitio. No pisa un `slug` que la pieza ya trajera
    -hoy ningún lector lo rellena, pero si alguno llega a hacerlo, él sabe
    más que este cruce-, y deja en `None` lo que no encuentre.
    """
    mapa = mapa_de_slugs(brand)
    if not mapa:
        return

    for platform, lectura in snapshot.redes.items():
        for pieza in lectura.piezas:
            if pieza.slug:
                continue
            pieza.slug = mapa.get((platform.value, pieza.id))
