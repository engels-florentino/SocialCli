"""Read and write post.yml files."""

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
    """No post.yml exists for the requested slug."""


class SlugInvalido(ValueError):
    """The post slug is not a safe single path component."""


class NombreDeMediaInvalido(ValueError):
    """A supplied media filename or relative path is unsafe."""


class PostInvalido(ValueError):
    """post.yml cannot be interpreted as a Post."""


def _validar_slug(dir_posts: Path, slug: str) -> Path:
    """Validate a slug and return its directory within the posts root."""
    return validar_componente_de_ruta(dir_posts, slug, SlugInvalido, "slug")


def _validar_nombre_de_media(dir_media: Path, nombre_fichero: object) -> Path:
    """Validate and resolve a supplied media path, including safe subdirectories."""
    return validar_ruta_relativa(
        dir_media, nombre_fichero, NombreDeMediaInvalido, "media name"
    )


def _ruta(brand: Brand, slug: str) -> Path:
    carpeta = _validar_slug(brand.dir_posts, slug)
    return carpeta / "post.yml"


def _cargar_datos(fichero: Path) -> dict:
    """Read and decode YAML, requiring a mapping at the root."""
    try:
        contenido = fichero.read_text(encoding="utf-8")
    except OSError as exc:
        raise PostInvalido(f"cannot read {fichero}: {exc}") from exc

    try:
        datos = yaml.safe_load(contenido)
    except yaml.YAMLError as exc:
        raise PostInvalido(f"'{fichero}' is not valid YAML: {exc}") from exc

    if datos is None:
        return {}

    if not isinstance(datos, dict):
        raise PostInvalido(
            f"'{fichero}' must contain a mapping (key: value) at the root; "
            f"received {type(datos).__name__}"
        )

    return datos


def _construir_platform_post(
    platform: Platform, bloque: object, dir_media: Path, fichero: Path
) -> PlatformPost:
    if not isinstance(bloque, dict):
        raise PostInvalido(
            f"the block for '{platform.value}' in {fichero} must be a mapping (key: value); received {type(bloque).__name__}"
        )

    body = bloque.get("body")
    if not isinstance(body, str):
        raise PostInvalido(
            f"'{platform.value}' in {fichero} has no string 'body' "
            "(the field is missing or is not a string)"
        )

    hashtags = bloque.get("hashtags", [])
    if not isinstance(hashtags, list):
        raise PostInvalido(
            f"'hashtags' for '{platform.value}' in {fichero} must be a list; "
            f"received {type(hashtags).__name__}"
        )

    media_nombres = bloque.get("media", [])
    if not isinstance(media_nombres, list):
        raise PostInvalido(
            f"'media' for '{platform.value}' in {fichero} must be a list; "
            f"received {type(media_nombres).__name__}"
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
            f"the block for '{platform.value}' in {fichero} is invalid: {exc}"
        ) from exc


def cargar_post(brand: Brand, slug: str) -> Post:
    """Load post.yml and resolve supplied media to absolute paths with metadata."""
    fichero = _ruta(brand, slug)
    if not fichero.exists():
        raise PostNoEncontrado(f"no post exists with slug '{slug}' in {fichero}")

    datos = _cargar_datos(fichero)

    slug_declarado = datos.get("slug")
    if isinstance(slug_declarado, str) and slug_declarado != slug:
        warnings.warn(
            f"'slug' in {fichero} is {slug_declarado!r}, but does not match the actual directory name ({slug!r}); ignored because the directory name is always the source of truth",
            stacklevel=2,
        )

    if "platforms" not in datos:
        raise PostInvalido(
            f"'platforms' is missing in {fichero}: a post needs at least one platform to publish on"
        )

    platforms_datos = datos["platforms"]
    if not isinstance(platforms_datos, dict):
        raise PostInvalido(
            f"'platforms' in {fichero} must be a mapping (key: value); "
            f"received {type(platforms_datos).__name__}"
        )

    if not platforms_datos:
        raise PostInvalido(
            f"'platforms' in {fichero} is empty: a post needs at least one platform to publish on"
        )

    dir_media = brand.raiz / "media"
    platforms: dict[Platform, PlatformPost] = {}
    for nombre, bloque in platforms_datos.items():
        try:
            platform = Platform(nombre)
        except ValueError:
            validas = ", ".join(p.value for p in Platform)
            raise PostInvalido(
                f"unknown platform '{nombre}' in {fichero}. Valid platforms: {validas}"
            ) from None

        platforms[platform] = _construir_platform_post(platform, bloque, dir_media, fichero)

    if "campaign" not in datos:
        validas = ", ".join(c.value for c in CampaignType)
        raise PostInvalido(
            f"'campaign' is missing in {fichero}. Valid campaigns: {validas}"
        )

    campaign_valor = datos["campaign"]
    try:
        campaign = CampaignType(campaign_valor)
    except ValueError:
        validas = ", ".join(c.value for c in CampaignType)
        raise PostInvalido(
            f'unknown campaign {campaign_valor!r} in {fichero}. Valid campaigns: {validas}'
        ) from None

    try:
        return Post(
            slug=slug,
            brand=brand.nombre,
            campaign=campaign,
            platforms=platforms,
        )
    except PydanticValidationError as exc:
        raise PostInvalido(f"{fichero} is not a valid post: {exc}") from exc


def guardar_post(brand: Brand, post: Post) -> Path:
    """Write post.yml from an in-memory Post."""
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
        "# 'slug' is informational and ignored when loading.\n"
        "# The containing directory determines the slug; editing this value has no effect.\n"
    )
    contenido = yaml.safe_dump(datos, allow_unicode=True, sort_keys=False)
    fichero.write_text(comentario + contenido, encoding="utf-8")
    return fichero
