"""Validate, preview, publish and persist approved content."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

import httpx

from socialctl.adapters import ADAPTADORES  # importar el paquete registra los cuatro
from socialctl.brands import Brand
from socialctl.formatter import PLATFORM_SPECS, privacidad_efectiva, texto_publicado, youtube_tags
from socialctl.media import ruta_relativa_efectiva
from socialctl.models import Platform, Post, PostResult, PostStatus, ValidationError
from socialctl.publication_policy import validate_derivation
from socialctl.publication_status import describe_result_with_observation
from socialctl.rutas import validar_componente_de_ruta

ICONO = {
    PostStatus.PUBLICADO: "OK",
    PostStatus.PENDIENTE_CONFIRMACION: "PENDING",
    PostStatus.ERROR: "ERROR",
    PostStatus.OMITIDO: "SKIPPED",
}


def validation_field_label(field: str) -> str:
    """Translate human field labels without changing serialized validation keys."""
    return {"cuenta": "account", "auditoria": "audit", "gancho": "hook",
            "privacidad": "privacy", "duracion": "duration", "formato": "format",
            "credenciales": "credentials", "origen": "source", "adaptador": "adapter",
            "persistencia": "persistence"}.get(field, field)


class PersistenciaError(Exception):
    """A publication result or history entry could not be saved."""


def validar_todo(
    post: Post, brand: Brand, *,
    legacy_approved_platforms: frozenset[Platform] = frozenset(),
) -> dict[Platform, list[ValidationError]]:
    """Validate every platform locally and collect failures without raising."""
    # Only run-due may supply compatibility after verifying the stored v2 hash.
    # It exempts missing origin only; adapter validation is always mandatory.
    errores: dict[Platform, list[ValidationError]] = {}
    for platform, pp in post.platforms.items():
        adaptador_cls = ADAPTADORES.get(platform)
        if adaptador_cls is None:
            errores[platform] = [
                ValidationError(
                    platform=platform,
                    campo="adaptador",
                    motivo=f"no adapter is registered for {platform.value}",
                )
            ]
            continue
        try:
            adaptador = adaptador_cls()
        except Exception as exc:
            # Fallo al CONSTRUIR el adaptador (p. ej. un __init__ con un bug):
            # no es un fallo de validate(), y el mensaje no debe sugerir que
            # lo es, o quien lo lea buscara el bug en el sitio equivocado.
            errores[platform] = [
                ValidationError(
                    platform=platform,
                    campo="adaptador",
                    motivo=(
                        f"could not create the adapter for {platform.value} "
                        f"({type(exc).__name__})"
                    ),
                )
            ]
            continue
        try:
            errores[platform] = adaptador.validate(pp, brand)
        except Exception as exc:
            errores[platform] = [
                ValidationError(
                    platform=platform,
                    campo="validacion",
                    motivo=(
                        f'validation for {platform.value} failed unexpectedly ({type(exc).__name__})'
                    ),
                )
            ]
    for platform, pp in post.platforms.items():
        errores[platform].extend(validate_derivation(
            pp, require_origin=platform not in legacy_approved_platforms))
    return errores


def render_preview(
    post: Post,
    errores: dict[Platform, list[ValidationError]],
    *,
    destinos: list[Platform] | None = None,
    motivo_exclusion: str = "not requested with --only",
) -> str:
    """Render the exact preview the user approves before publication, with exclusions clearly labeled."""
    lineas = [f"Post: {post.slug}   brand: {post.brand}   campaign: {post.campaign.value}", ""]

    if destinos is not None:
        excluidas = [p for p in post.platforms if p not in destinos]
        if excluidas:
            lineas.append(
                f"Will publish only on {', '.join((p.value for p in destinos))}. Excluded ({motivo_exclusion}): {', '.join((p.value for p in excluidas))}."
            )
            lineas.append("")

    for platform, pp in post.platforms.items():
        spec = PLATFORM_SPECS[platform]
        marca_exclusion = (
            f"  [WILL NOT BE PUBLISHED: {motivo_exclusion}]"
            if destinos is not None and platform not in destinos
            else ""
        )
        lineas.append(f"--- {platform.value.upper()} ---{marca_exclusion}")
        if pp.title:
            lineas.append(f"Title: {pp.title}")
        if spec.valores_privacidad is not None:
            valor_privacidad = privacidad_efectiva(pp)
            aviso_publico = (
                '  [WARNING: will become PUBLIC and visible to anyone as soon as the upload finishes]'
                if valor_privacidad == "public"
                else ""
            )
            lineas.append(f"Privacy: {valor_privacidad}{aviso_publico}")
        lineas.append(texto_publicado(pp))
        if pp.content_origin is not None:
            lineas.append(f"Source: {pp.content_origin}")
        if pp.source_video_id is not None:
            lineas.append(f"Source long-form video ID: {pp.source_video_id}")
        if pp.first_comment:
            lineas.append(f"First comment: {pp.first_comment}")
        if platform == Platform.YOUTUBE:
            lineas.append("Visible hashtags appended to description: " + ", ".join(pp.visible_hashtags or []))
            lineas.append("Internal tags: " + ", ".join(youtube_tags(pp)))
            if pp.tags is None:
                lineas.append("Legacy mode: post.yml hashtags are sent as internal YouTube tags.")
        if not spec.hashtags_en_texto and youtube_tags(pp):
            etiquetas = ", ".join(youtube_tags(pp))
            lineas.append(
                f"Tags (platform metadata; do NOT appear in the text above): {etiquetas}"
            )
        for asset in pp.media:
            medidas = f"{asset.width}x{asset.height}" if asset.width else "?"
            duracion = f", {asset.duration_s:.0f}s" if asset.duration_s else ""
            lineas.append(f"Media: {ruta_relativa_efectiva(asset)} ({medidas}{duracion})")
        if not pp.media:
            lineas.append("Media: (none)")
        for error in errores.get(platform, []):
            lineas.append(f"PROBLEM [{validation_field_label(error.campo)}]: {error.motivo}")
        lineas.append("")

    # Hallazgo de revision (Menor 5): con `destinos`, los problemas de las
    # redes excluidas no bloquean la publicacion (ver `_publicar_impl` en
    # `socialctl/cli.py`), asi que sumar TODOS los errores en un unico
    # numero podia leerse como "Problemas detectados: 1" seguido de una
    # publicacion con exito (exit 0): el numero no era el que decidia el
    # aborto. Cuando `destinos` deja alguno de esos errores fuera del
    # conteo que bloquea, se desglosan los dos numeros en vez de mostrar
    # solo el total; si coinciden (o no hay `destinos`), se muestra un
    # unico numero, como antes.
    total = sum(len(v) for v in errores.values())
    bloqueantes = (
        total if destinos is None
        else sum(len(v) for p, v in errores.items() if p in destinos)
    )
    if bloqueantes == total:
        lineas.append(f"Problems found: {total}")
    else:
        lineas.append(
            f"Problems found: {total} "
            f"({bloqueantes} on the platforms selected for publication)"
        )
    return "\n".join(lineas)


def publicar(
    post: Post,
    brand: Brand,
    solo: list[Platform] | None = None,
    *,
    on_progreso: Callable[[Platform], None] | None = None,
    on_media_result: Callable[[PostResult], None] | None = None,
    occurrence_ids: dict[Platform, str] | None = None,
    approval_provenance: str = "approved-publish-preview",
    retry_guard: bool = False,
    legacy_approved_platforms: frozenset[Platform] = frozenset(),
) -> list[PostResult]:
    """Execute an approved publication while excluding concurrent draft ingestion.

    Preview/confirmation remains outside this gate. A durable start marker prevents
    ingress replacing text after the gate releases but before CLI result persistence,
    including crashes and definite failed attempts. It never blocks fresh publishing.
    """
    from socialctl.media_registry import publication_gate, record_publication_start
    selected = [p for p in post.platforms if solo is None or p in solo]
    if not selected:
        return []
    with ExitStack() as stack:
        try:
            stack.enter_context(publication_gate(brand, post.slug))
        except (OSError, ValueError):
            return [PostResult(platform=p, status=PostStatus.ERROR,
                error="post is running concurrently or its lock is inaccessible; publication was not attempted", riesgo_duplicado=False) for p in selected]
        try:
            record_publication_start(brand, post, selected)
        except (OSError, ValueError):
            return [PostResult(platform=p, status=PostStatus.ERROR,
                error="could not persist publication start; publication was not attempted", riesgo_duplicado=False) for p in selected]
        # Do not classify an exception after adapter effects as safe to retry.
        return _publicar_locked(post, brand, solo, on_progreso=on_progreso,
            on_media_result=on_media_result, occurrence_ids=occurrence_ids,
            approval_provenance=approval_provenance, retry_guard=retry_guard,
            legacy_approved_platforms=legacy_approved_platforms)


def _publicar_locked(
    post: Post,
    brand: Brand,
    solo: list[Platform] | None = None,
    *,
    on_progreso: Callable[[Platform], None] | None = None,
    on_media_result: Callable[[PostResult], None] | None = None,
    occurrence_ids: dict[Platform, str] | None = None,
    approval_provenance: str = "approved-publish-preview",
    retry_guard: bool = False,
    legacy_approved_platforms: frozenset[Platform] = frozenset(),
) -> list[PostResult]:
    """Publish independently on each platform; one failure does not abort the others."""
    destinos = [p for p in post.platforms if solo is None or p in solo]
    resultados: list[PostResult] = []

    with httpx.Client(timeout=60) as client:
        for platform in destinos:
            if on_progreso is not None:
                try:
                    on_progreso(platform)
                except Exception:
                    pass
            adaptador_cls = ADAPTADORES.get(platform)
            if adaptador_cls is None:
                resultados.append(
                    PostResult(
                        platform=platform,
                        status=PostStatus.ERROR,
                        error=f"no adapter is registered for {platform.value}",
                    )
                )
                continue
            try:
                adaptador = adaptador_cls()
            except Exception as exc:
                resultados.append(
                    PostResult(
                        platform=platform,
                        status=PostStatus.ERROR,
                        error=f"could not create the adapter ({type(exc).__name__})",
                    )
                )
                continue
            # Revalidate immediately before effects; compatibility never skips the
            # adapter's limits, media count, account or privacy checks.
            try:
                errors = adaptador.validate(post.platforms[platform], brand)
                errors += validate_derivation(post.platforms[platform],
                    require_origin=platform not in legacy_approved_platforms)
            except Exception as exc:
                resultados.append(PostResult(platform=platform, status=PostStatus.ERROR,
                    error=f"validation for {platform.value} failed unexpectedly ({type(exc).__name__})"))
                continue
            if errors:
                resultados.append(PostResult(platform=platform, status=PostStatus.ERROR,
                    error="; ".join(e.motivo for e in errors)))
                continue
            try:
                if platform in {Platform.FACEBOOK, Platform.INSTAGRAM, Platform.YOUTUBE}:
                    from socialctl.publication_steps import publish_with_steps
                    resultados.append(publish_with_steps(
                        post, brand, platform, adaptador, client,
                        on_media_result=on_media_result,
                        occurrence_id=(occurrence_ids or {}).get(platform),
                        provenance=approval_provenance,
                        retry_guard=retry_guard,
                    ))
                    continue
                resultados.append(
                    adaptador.publish(post.platforms[platform], brand, client)
                )
            except Exception as exc:
                resultados.append(
                    PostResult(
                        platform=platform,
                        status=PostStatus.ERROR,
                        error=str(exc),
                        riesgo_duplicado=True,
                    )
                )

    return resultados


class SlugInvalido(Exception):
    """The post slug is not a safe single path component."""


def _validar_slug(dir_posts: Path, slug: str) -> Path:
    """Validate a slug and return its directory within the posts root."""
    return validar_componente_de_ruta(dir_posts, slug, SlugInvalido, "slug")


def _cargar_resultados_previos(destino: Path) -> dict[str, dict]:
    """Read previous resultado.json entries by platform, preserving an unreadable file for inspection."""
    try:
        contenido = destino.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        # No es "no existe" (ese caso ya se maneja arriba): es, por ejemplo,
        # un permiso denegado al leer. No hay nada que fusionar ni nada que
        # respaldar (no se pudo ni abrir), así que seguimos sin datos
        # previos; si el problema de fondo es de permisos de escritura,
        # volverá a aparecer -y se reportará con claridad- al escribir.
        return {}

    try:
        previo = json.loads(contenido)
        if not isinstance(previo, dict):
            raise ValueError("JSON root is not an object")
        entradas = previo["resultados"]
        if not isinstance(entradas, list):
            raise ValueError("'resultados' is not a list")
        indexadas: dict[str, dict] = {}
        for entrada in entradas:
            if not isinstance(entrada, dict) or not isinstance(
                entrada.get("platform"), str
            ):
                raise ValueError("a 'resultados' entry does not have the expected structure")
            indexadas[entrada["platform"]] = entrada
        return indexadas
    except (json.JSONDecodeError, KeyError, ValueError):
        respaldo = destino.with_name(destino.name + ".corrupto")
        try:
            destino.replace(respaldo)
        except OSError:
            pass  # si ni siquiera se puede respaldar, seguimos: prioridad es no perder lo nuevo
        return {}


def guardar_resultado(post: Post, brand: Brand, resultados: list[PostResult]) -> Path:
    """Write resultado.json, merging platform results without losing earlier successes."""
    carpeta = _validar_slug(brand.dir_posts, post.slug)
    destino = carpeta / "resultado.json"

    existentes = _cargar_resultados_previos(destino)

    fecha_actual = datetime.now().isoformat(timespec="seconds")
    for r in resultados:
        entrada = r.model_dump()
        entrada["platform"] = r.platform.value
        entrada["status"] = r.status.value
        entrada["fecha"] = fecha_actual
        existentes[r.platform.value] = entrada

    orden_conocido = [p.value for p in Platform]
    claves_ordenadas = sorted(
        existentes,
        key=lambda k: (orden_conocido.index(k) if k in orden_conocido else len(orden_conocido), k),
    )

    contenido = json.dumps(
        {
            "slug": post.slug,
            "marca": post.brand,
            "campana": post.campaign.value,
            "actualizado": fecha_actual,
            "resultados": [existentes[k] for k in claves_ordenadas],
        },
        indent=2,
        ensure_ascii=False,
    )

    try:
        carpeta.mkdir(parents=True, exist_ok=True)
        destino.write_text(contenido, encoding="utf-8")
    except OSError as exc:
        raise PersistenciaError(
            f"could not save the result for '{post.slug}' in {destino}: {exc}"
        ) from exc

    return destino


def anexar_historial(post: Post, brand: Brand, resultados: list[PostResult]) -> None:
    """Append a readable history entry without changing previous entries."""
    fichero = brand.raiz / "historial.md"
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

    lineas = [f"\n## {post.slug} — {fecha}\n"]
    for r in resultados:
        detalle = r.url or r.error or ""
        estado = describe_result_with_observation(r)
        lineas.append(f"- {r.platform.value}: {ICONO[r.status]} {estado} {detalle}".rstrip())
    contenido = "\n".join(lineas) + "\n"

    try:
        brand.raiz.mkdir(parents=True, exist_ok=True)
        with fichero.open("a", encoding="utf-8") as f:
            f.write(contenido)
    except OSError as exc:
        raise PersistenciaError(
            f"could not append the result for '{post.slug}' to {fichero}: {exc}"
        ) from exc
