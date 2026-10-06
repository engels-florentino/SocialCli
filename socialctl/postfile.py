"""Lectura y escritura de post.yml.

El skill escribe post.yml a partir de lo que el usuario pide en
conversación; el CLI lo lee para publicar. Este módulo es la frontera
entre las dos mitades del sistema, así que un post.yml mal formado debe
dar un error comprensible en español (qué está mal y en qué fichero), no
una excepción opaca (`KeyError`, `AttributeError` o un `ValidationError`
de pydantic en crudo).

Tanto el slug de un post (para construir su carpeta dentro de
`<Marca>/posts/`) como cada nombre de fichero de `media` (para construir
su ruta dentro de `<Marca>/media/`) se usan para construir rutas en
disco, así que a ambos se les aplica el mismo criterio de seguridad que
`_validar_nombre_de_marca` aplica en `socialctl/brands.py` al nombre de
una marca (y que `_validar_slug` repite en `socialctl/publisher.py`):
ninguno puede escapar de la carpeta que le corresponde, ni por forma
(separadores de ruta, '.', '..', ruta absoluta) ni -tras resolver
enlaces simbólicos- por destino. Este proyecto guarda credenciales en
`<Marca>/.secrets/`, así que una fuga de ruta aquí permitiría leer esos
secretos a través de un post.yml manipulado.

El slug sigue admitiendo un ÚNICO componente (nunca subcarpetas: un post
vive siempre directamente en `<Marca>/posts/<slug>/`), así que usa
``socialctl.rutas.validar_componente_de_ruta``, compartido con
``brands.py`` (nombre de marca) y ``publisher.py`` (slug, de nuevo, al
persistir el resultado). El nombre de un fichero de `media`, en cambio, sí
puede tener subcarpetas legítimas -el usuario organiza su media en
`<Marca>/media/short/`, `<Marca>/media/video/`, etc.-, así que usa
``socialctl.rutas.validar_ruta_relativa``: mismo criterio de fondo (forma
del valor + resolución de enlaces simbólicos contra `dir_media`), pero
permitiendo `'/'` como separador entre subcarpetas. Cada módulo sigue
lanzando su propia clase de excepción -aquí, subclases de ``ValueError``
para que el CLI las capture junto al resto de errores de post.yml-, pero
el criterio y el mensaje de cada una están en un único sitio.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import yaml
from pydantic import ValidationError as PydanticValidationError

from socialctl.brands import Brand
from socialctl.media import leer_media, ruta_relativa_efectiva
from socialctl.models import CampaignType, Platform, PlatformPost, Post
from socialctl.rutas import validar_componente_de_ruta, validar_ruta_relativa


class PostNoEncontrado(Exception):
    """No hay un post.yml con ese slug."""


class SlugInvalido(ValueError):
    """El slug de un post no es un único componente de ruta seguro.

    Se lanza cuando el slug está vacío o solo tiene espacios, es una ruta
    absoluta, contiene separadores de ruta ('/' u ``os.sep``), es '.' o
    '..', o cuando -tras resolver enlaces simbólicos- la carpeta
    resultante queda fuera de la carpeta de posts de la marca
    (``brand.dir_posts``). Aplica tanto a ``cargar_post`` (el slug llega
    como argumento) como a ``guardar_post`` (llega en ``post.slug``, que
    podría proceder de un post.yml editado a mano).
    """


class NombreDeMediaInvalido(ValueError):
    """Un nombre de fichero de ``media`` en post.yml no es seguro.

    Se lanza cuando el nombre no es una cadena de texto, está vacío o solo
    tiene espacios, contiene un byte nulo, es una ruta absoluta, contiene
    un '\\' (el separador de subcarpetas es siempre '/', nunca '\\'), tiene
    algún segmento igual a '.' o '..' -en cualquier posición: tanto
    ``"../.secrets/tiktok.json"`` como ``"short/../../.secrets/tiktok.json"``
    se rechazan-, o cuando -tras resolver enlaces simbólicos, en cualquier
    nivel de subcarpeta- el fichero resultante queda fuera de
    ``<Marca>/media/``. Sin esta comprobación, un post.yml con
    ``media: ["../../.secrets/tiktok.json"]`` podría hacer que se lea un
    fichero de credenciales de la marca.

    A diferencia del slug de un post (un único componente, sin
    subcarpetas: ver ``SlugInvalido``), el nombre de un fichero de media SÍ
    puede tener subcarpetas -p. ej. ``"short/S2.mp4"``, para el vídeo
    vertical que el usuario guarda en ``<Marca>/media/short/``-, así que
    la validación (``socialctl.rutas.validar_ruta_relativa``) admite '/'
    como separador entre ellas, mientras sigue bloqueando cualquier
    intento de escapar de ``<Marca>/media/``.
    """


class PostInvalido(ValueError):
    """post.yml existe pero su contenido no se puede interpretar como un Post.

    Cubre: YAML mal formado; una raíz que no es un mapping (clave: valor);
    'platforms' que falta, no es un mapping o está vacío; el bloque de una
    red que no es un mapping; una red con un valor desconocido; 'hashtags'
    o 'media' que no son listas; un 'body' que falta o no es texto; y una
    'campaign' que falta o tiene un valor desconocido. Subclase de
    ``ValueError`` para que quien ya captura ``ValueError`` (el CLI, o el
    propio test de "red desconocida") siga funcionando sin conocer este
    nombre.
    """


def _validar_slug(dir_posts: Path, slug: str) -> Path:
    """Valida ``slug`` y devuelve la carpeta de ese post dentro de ``dir_posts``."""
    return validar_componente_de_ruta(dir_posts, slug, SlugInvalido, "slug")


def _validar_nombre_de_media(dir_media: Path, nombre_fichero: object) -> Path:
    """Valida una ruta de ``media`` (subcarpetas incluidas) y la resuelve en ``dir_media``."""
    return validar_ruta_relativa(
        dir_media, nombre_fichero, NombreDeMediaInvalido, "nombre de media"
    )


def _ruta(brand: Brand, slug: str) -> Path:
    carpeta = _validar_slug(brand.dir_posts, slug)
    return carpeta / "post.yml"


def _cargar_datos(fichero: Path) -> dict:
    """Lee y decodifica el YAML de ``fichero``, devolviendo siempre un mapping."""
    try:
        contenido = fichero.read_text(encoding="utf-8")
    except OSError as exc:
        raise PostInvalido(f"no se puede leer {fichero}: {exc}") from exc

    try:
        datos = yaml.safe_load(contenido)
    except yaml.YAMLError as exc:
        raise PostInvalido(f"'{fichero}' no es un YAML válido: {exc}") from exc

    if datos is None:
        return {}

    if not isinstance(datos, dict):
        raise PostInvalido(
            f"'{fichero}' debe contener un mapping (clave: valor) en la raíz; "
            f"contiene un {type(datos).__name__}"
        )

    return datos


def _construir_platform_post(
    platform: Platform, bloque: object, dir_media: Path, fichero: Path
) -> PlatformPost:
    if not isinstance(bloque, dict):
        raise PostInvalido(
            f"el bloque de '{platform.value}' en {fichero} debe ser un mapping "
            f"(clave: valor); contiene un {type(bloque).__name__}"
        )

    body = bloque.get("body")
    if not isinstance(body, str):
        raise PostInvalido(
            f"'{platform.value}' en {fichero} no tiene un 'body' de texto "
            "(el campo falta o no es una cadena)"
        )

    hashtags = bloque.get("hashtags", [])
    if not isinstance(hashtags, list):
        raise PostInvalido(
            f"'hashtags' de '{platform.value}' en {fichero} debe ser una lista; "
            f"contiene un {type(hashtags).__name__}"
        )

    media_nombres = bloque.get("media", [])
    if not isinstance(media_nombres, list):
        raise PostInvalido(
            f"'media' de '{platform.value}' en {fichero} debe ser una lista; "
            f"contiene un {type(media_nombres).__name__}"
        )

    media = []
    for nombre_fichero in media_nombres:
        ruta_absoluta = _validar_nombre_de_media(dir_media, nombre_fichero)
        # `ruta_absoluta` ya está validada como dentro de `dir_media` (ver
        # `_validar_nombre_de_media`), así que recuperar aquí la ruta
        # relativa con `relative_to` es seguro y, a la vez, normaliza el
        # valor tal como se escribió en post.yml (p. ej. una barra doble
        # se colapsa) para que `MediaAsset.ruta_relativa` -y quien la lea
        # después: el preview, `guardar_post`, la URL de Instagram- vea
        # siempre la forma canónica, con '/' como separador.
        ruta_relativa = ruta_absoluta.relative_to(dir_media).as_posix()
        media.append(leer_media(ruta_absoluta, ruta_relativa=ruta_relativa))

    try:
        return PlatformPost(
            platform=platform,
            title=bloque.get("title"),
            body=body,
            hashtags=hashtags,
            **({"tags": bloque["tags"]} if "tags" in bloque else {}),
            **({"visible_hashtags": bloque["visible_hashtags"]} if "visible_hashtags" in bloque else {}),
            media=media,
            link=bloque.get("link"),
            first_comment=bloque.get("first_comment"),
            content_origin=bloque.get("content_origin"),
            source_video_id=bloque.get("source_video_id"),
            privacy=bloque.get("privacy"),
        )
    except PydanticValidationError as exc:
        raise PostInvalido(
            f"el bloque de '{platform.value}' en {fichero} no es válido: {exc}"
        ) from exc


def cargar_post(brand: Brand, slug: str) -> Post:
    """Carga un post.yml y resuelve su media a rutas absolutas con metadatos.

    El slug pasado como argumento (nunca el campo 'slug' que pueda traer
    el propio YAML) es la única fuente de verdad para la carpeta del post
    y para ``Post.slug``: un post.yml con un 'slug' manipulado no puede
    hacer que un ``guardar_post`` posterior escriba en otro sitio. Si el
    'slug' del fichero no coincide con el nombre real de la carpeta, se
    avisa con un ``UserWarning`` (no se falla: el valor del fichero se
    ignora igualmente) porque lo más probable es que alguien lo haya
    editado a mano esperando que tuviera efecto.
    """
    fichero = _ruta(brand, slug)
    if not fichero.exists():
        raise PostNoEncontrado(f"no existe un post con slug '{slug}' en {fichero}")

    datos = _cargar_datos(fichero)

    slug_declarado = datos.get("slug")
    if isinstance(slug_declarado, str) and slug_declarado != slug:
        warnings.warn(
            f"'slug' en {fichero} es {slug_declarado!r}, pero no coincide con "
            f"el nombre real de la carpeta ({slug!r}); se ignora, ya que el "
            "nombre de la carpeta es siempre la fuente de verdad",
            stacklevel=2,
        )

    if "platforms" not in datos:
        raise PostInvalido(
            f"falta 'platforms' en {fichero}: un post necesita al menos una "
            "red en la que publicar"
        )

    platforms_datos = datos["platforms"]
    if not isinstance(platforms_datos, dict):
        raise PostInvalido(
            f"'platforms' en {fichero} debe ser un mapping (clave: valor); "
            f"contiene un {type(platforms_datos).__name__}"
        )

    if not platforms_datos:
        raise PostInvalido(
            f"'platforms' en {fichero} está vacío: un post necesita al menos "
            "una red en la que publicar"
        )

    dir_media = brand.raiz / "media"
    platforms: dict[Platform, PlatformPost] = {}
    for nombre, bloque in platforms_datos.items():
        try:
            platform = Platform(nombre)
        except ValueError:
            validas = ", ".join(p.value for p in Platform)
            raise PostInvalido(
                f"red desconocida '{nombre}' en {fichero}. Redes válidas: {validas}"
            ) from None

        platforms[platform] = _construir_platform_post(platform, bloque, dir_media, fichero)

    if "campaign" not in datos:
        validas = ", ".join(c.value for c in CampaignType)
        raise PostInvalido(
            f"falta 'campaign' en {fichero}. Campañas válidas: {validas}"
        )

    campaign_valor = datos["campaign"]
    try:
        campaign = CampaignType(campaign_valor)
    except ValueError:
        validas = ", ".join(c.value for c in CampaignType)
        raise PostInvalido(
            f"campaña desconocida {campaign_valor!r} en {fichero}. "
            f"Campañas válidas: {validas}"
        ) from None

    try:
        return Post(
            slug=slug,
            brand=brand.nombre,
            campaign=campaign,
            platforms=platforms,
        )
    except PydanticValidationError as exc:
        raise PostInvalido(f"{fichero} no es un post válido: {exc}") from exc


def guardar_post(brand: Brand, post: Post) -> Path:
    """Escribe un post.yml a partir de un Post en memoria.

    El 'slug' que queda escrito en el fichero es puramente informativo:
    ``cargar_post`` nunca lo usa para decidir la carpeta ni ``Post.slug``
    (ver su docstring). Para que quien edite este fichero a mano -o el
    skill que lo escribe- no espere que cambiarlo tenga efecto, se
    antepone un comentario en el propio YAML que lo deja explícito.
    """
    fichero = _ruta(brand, post.slug)
    fichero.parent.mkdir(parents=True, exist_ok=True)

    datos = {
        "slug": post.slug,
        "campaign": post.campaign.value,
        "platforms": {
            platform.value: {
                **({"title": pp.title} if pp.title else {}),
                "body": pp.body,
                "hashtags": pp.hashtags,
                **({"tags": pp.tags} if pp.tags is not None else {}),
                **({"visible_hashtags": pp.visible_hashtags} if pp.visible_hashtags is not None else {}),
                **({"content_origin": pp.content_origin} if pp.content_origin is not None else {}),
                **({"source_video_id": pp.source_video_id} if pp.source_video_id is not None else {}),
                "media": [ruta_relativa_efectiva(asset) for asset in pp.media],
                **({"link": pp.link} if pp.link else {}),
                **({"first_comment": pp.first_comment} if pp.first_comment else {}),
                **({"privacy": pp.privacy} if pp.privacy else {}),
            }
            for platform, pp in post.platforms.items()
        },
    }

    comentario = (
        "# 'slug' es solo informativo: se ignora al cargar el post.\n"
        "# La carpeta que contiene este fichero es la que manda; cambiar\n"
        "# este valor a mano no tiene ningún efecto.\n"
    )
    contenido = yaml.safe_dump(datos, allow_unicode=True, sort_keys=False)
    fichero.write_text(comentario + contenido, encoding="utf-8")
    return fichero
