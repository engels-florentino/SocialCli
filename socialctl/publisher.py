"""Orquestacion: validar todo, mostrar el preview, publicar y persistir.

publicar() NO pide confirmacion: se llama solo cuando el CLI ya la obtuvo. El
usuario aprueba una unica vez viendo render_preview() y, a partir de ahi, se
publica en las cuatro redes sin volver a preguntar.

Un fallo en una red no aborta las demas: cada red se publica de forma
independiente y, aunque los adaptadores ya estan disenados para no lanzar
nunca, publicar() atrapa igualmente cualquier excepcion (cinturon y
tirantes) y la convierte en un PostResult con status=ERROR y
riesgo_duplicado=True: una excepcion inesperada durante `publish()` no permite
saber si la red acepto el contenido antes de fallar.
"""

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
    PostStatus.PENDIENTE_CONFIRMACION: "PENDIENTE",
    PostStatus.ERROR: "ERROR",
    PostStatus.OMITIDO: "OMITIDO",
}


class PersistenciaError(Exception):
    """No se pudo guardar el resultado o anexarlo al historial.

    Se levanta en vez de dejar escapar el OSError/PermissionError original
    para que ninguna funcion publica de este modulo reviente con un error
    opaco. No implica perder informacion de lo publicado: `publicar()` ya
    ha devuelto la lista de PostResult a quien llama antes de que se
    intente persistir nada, asi que quien capture esta excepcion todavia
    tiene esos resultados en memoria y puede mostrarlos o reintentar el
    guardado.
    """


def validar_todo(
    post: Post, brand: Brand, *,
    legacy_approved_platforms: frozenset[Platform] = frozenset(),
) -> dict[Platform, list[ValidationError]]:
    """Valida todas las redes sin tocar la red. Nunca lanza.

    Un fallo al validar una red concreta (por ejemplo, un adaptador sin
    registrar, o un `validate()` de un adaptador con un bug) no impide ver
    los problemas de las demas: se convierte en un `ValidationError` propio
    de esa red, igual que `publicar()` aisla los fallos de publicacion.
    """
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
                    motivo=f"no hay ningun adaptador registrado para {platform.value}",
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
                        f"no se pudo crear el adaptador de {platform.value} "
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
                        f"la validacion de {platform.value} ha fallado "
                        f"inesperadamente ({type(exc).__name__})"
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
    motivo_exclusion: str = "no solicitada con --only",
) -> str:
    """Texto del preview: lo unico que el usuario aprueba antes de publicar.

    Debe reflejar fielmente lo que se va a publicar: el texto final de cada
    red tal como lo compone de verdad su adaptador (`texto_publicado()`, en
    `socialctl/formatter.py`), que archivo va a cada una con sus medidas y
    duracion, y los problemas de validacion detectados con su motivo.

    No todas las redes componen ese texto igual (ver `PLATFORM_SPECS`): tres
    de las cuatro incrustan los hashtags dentro del propio texto, pero
    YouTube los envia aparte, como metadatos en `tags`, y nunca aparecen en
    la descripcion publicada. El preview lo refleja mostrando esos hashtags
    en una linea separada, etiquetada explicitamente como metadato, en vez
    de pegarlos al texto que se publica -mostrarlos ahi seria mostrar algo
    que el usuario aprobaria sin que luego se cumpla-.

    `destinos`, si se da, es la lista de redes que de verdad se van a
    publicar (por ejemplo, tras filtrar con `--only`, o las redes vigentes
    de un `retry`). El preview NO deja de mostrar las demas redes del post
    -su texto, su media, sus problemas de validacion siguen ahi, integros,
    para que el usuario pueda ver por que las descarto o corregirlas mas
    tarde-, pero marca con claridad cuales quedan fuera de esta publicacion
    concreta y por que, para que nunca apruebe, sin darse cuenta, una
    publicacion distinta de la que de verdad va a ocurrir. `None` (el valor
    por defecto) significa "se publican todas las redes del post, sin
    filtrar": no se añade ninguna marca de exclusion.

    `motivo_exclusion` es el texto que explica esa marca. Por defecto asume
    que la exclusion viene de `--only` -el caso de `publish`-, pero no todo
    llamador que pasa `destinos` viene de ahi: `retry` tambien filtra
    `destinos` (a las redes que de verdad va a reintentar), y una red queda
    fuera de un reintento porque ya se publico antes o porque quedo marcada
    `riesgo_duplicado`, nunca porque el usuario haya escrito `--only` (que
    puede no haber escrito en la vida). Hallazgo de revision: antes el
    texto decia siempre "no solicitada con --only", incluso en el preview
    de un `retry`, lo cual describia mal el motivo real aunque no engañara
    sobre que se iba a publicar. Cada llamador pasa aqui el motivo que le
    corresponde de verdad (ver `_publicar_impl` en `socialctl/cli.py`).
    """
    lineas = [f"Post: {post.slug}   marca: {post.brand}   campana: {post.campaign.value}", ""]

    if destinos is not None:
        excluidas = [p for p in post.platforms if p not in destinos]
        if excluidas:
            lineas.append(
                f"Se publicará solo en {', '.join(p.value for p in destinos)}. "
                f"Quedan fuera ({motivo_exclusion}): "
                f"{', '.join(p.value for p in excluidas)}."
            )
            lineas.append("")

    for platform, pp in post.platforms.items():
        spec = PLATFORM_SPECS[platform]
        marca_exclusion = (
            f"  [NO SE PUBLICARÁ: {motivo_exclusion}]"
            if destinos is not None and platform not in destinos
            else ""
        )
        lineas.append(f"--- {platform.value.upper()} ---{marca_exclusion}")
        if pp.title:
            lineas.append(f"Titulo: {pp.title}")
        if spec.valores_privacidad is not None:
            valor_privacidad = privacidad_efectiva(pp)
            aviso_publico = (
                "  [ATENCIÓN: quedará PÚBLICO y visible para cualquiera en "
                "cuanto termine de subirse]"
                if valor_privacidad == "public"
                else ""
            )
            lineas.append(f"Privacidad: {valor_privacidad}{aviso_publico}")
        lineas.append(texto_publicado(pp))
        if pp.content_origin is not None:
            lineas.append(f"Origen: {pp.content_origin}")
        if pp.source_video_id is not None:
            lineas.append(f"ID del largo de origen: {pp.source_video_id}")
        if pp.first_comment:
            lineas.append(f"Primer comentario: {pp.first_comment}")
        if platform == Platform.YOUTUBE:
            lineas.append("Hashtags visibles añadidos a descripción: " + ", ".join(pp.visible_hashtags or []))
            lineas.append("Tags internos: " + ", ".join(youtube_tags(pp)))
            if pp.tags is None:
                lineas.append("Modo legacy: hashtags de post.yml se envía como tags internos de YouTube.")
        if not spec.hashtags_en_texto and youtube_tags(pp):
            etiquetas = ", ".join(youtube_tags(pp))
            lineas.append(
                f"Etiquetas (metadato de la red; NO aparecen en el texto anterior): {etiquetas}"
            )
        for asset in pp.media:
            medidas = f"{asset.width}x{asset.height}" if asset.width else "?"
            duracion = f", {asset.duration_s:.0f}s" if asset.duration_s else ""
            lineas.append(f"Media: {ruta_relativa_efectiva(asset)} ({medidas}{duracion})")
        if not pp.media:
            lineas.append("Media: (ninguna)")
        for error in errores.get(platform, []):
            lineas.append(f"PROBLEMA [{error.campo}]: {error.motivo}")
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
        lineas.append(f"Problemas detectados: {total}")
    else:
        lineas.append(
            f"Problemas detectados: {total} "
            f"({bloqueantes} en las redes que se van a publicar)"
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
                error="post con ejecución concurrente o bloqueo inaccesible; no se intentó publicar", riesgo_duplicado=False) for p in selected]
        try:
            record_publication_start(brand, post, selected)
        except (OSError, ValueError):
            return [PostResult(platform=p, status=PostStatus.ERROR,
                error="no se pudo persistir inicio de publicación; no se intentó publicar", riesgo_duplicado=False) for p in selected]
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
    """Publica en cada red de forma independiente. Un fallo no aborta las demas.

    Nunca pide confirmacion: se llama solo cuando el CLI ya la obtuvo. Con
    `solo=[...]` se republica unicamente esas redes (reintento).

    `on_progreso`, si se da, se llama con la `Platform` justo ANTES de
    empezar a publicar en ella (nunca despues, y nunca si esa red se
    salta por no estar en `solo`): es la unica senal de vida que tiene
    quien llama mientras dura la publicacion, que puede tardar varios
    minutos por red (el sondeo de Instagram, en particular). Un fallo en
    el propio callback (por ejemplo, un error al escribir en una consola
    cerrada) no debe impedir que la publicacion en esa red se intente
    igualmente, asi que se ignora con el mismo criterio de "cinturon y
    tirantes" que ya usa este modulo para los adaptadores.

    Un adaptador ausente o que no puede construirse falla antes de tocar la red
    y es retryable. Si `Adapter.publish()` llega a invocarse y deja escapar una
    excepcion, el resultado se marca con riesgo de duplicado: el punto remoto
    alcanzado es desconocido y un reintento automatico no es seguro.
    """
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
                        error=f"no hay ningun adaptador registrado para {platform.value}",
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
                        error=f"no se pudo crear el adaptador ({type(exc).__name__})",
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
                    error=f"la validacion de {platform.value} ha fallado inesperadamente ({type(exc).__name__})"))
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
    """El slug de un post no es un único componente de ruta seguro.

    Se lanza cuando el slug está vacío o solo tiene espacios, es una ruta
    absoluta, contiene separadores de ruta ('/' u ``os.sep``), es '.' o
    '..', o cuando -tras resolver enlaces simbólicos- la carpeta resultante
    queda fuera de la carpeta de posts de la marca (``brand.dir_posts``).

    Aplica a ``post.slug`` el mismo criterio que ``_validar_nombre_de_marca``
    (en ``socialctl/brands.py``) aplica al nombre de una marca: en ambos
    casos se usa un dato que puede venir de fuera (un fichero de post
    editado a mano) para construir una ruta en disco, y esa ruta no puede
    escapar de su raíz. El criterio en sí vive, compartido, en
    ``socialctl.rutas.validar_componente_de_ruta`` (ver ese módulo); esta
    clase sigue siendo propia de `publisher.py` -con su propio nombre y su
    propia jerarquía (no hereda de ``ValueError``, a diferencia de la
    homónima de `postfile.py`)- porque cada llamador conserva su propia
    excepción de dominio.
    """


def _validar_slug(dir_posts: Path, slug: str) -> Path:
    """Valida ``slug`` y devuelve la carpeta de ese post dentro de ``dir_posts``."""
    return validar_componente_de_ruta(dir_posts, slug, SlugInvalido, "slug")


def _cargar_resultados_previos(destino: Path) -> dict[str, dict]:
    """Lee las entradas de un `resultado.json` previo, indexadas por plataforma.

    Devuelve un diccionario vacío si el fichero todavía no existe (primer
    guardado) o si no se puede ni leer (por ejemplo, sin permisos): en
    ambos casos no hay nada fiable que fusionar, y quien llama sigue
    adelante con el guardado nuevo con normalidad. Si de verdad no hay
    permisos de escritura, el fallo aparecerá igualmente más abajo, al
    intentar escribir `destino`, y se envolverá en `PersistenciaError` como
    siempre; esta función nunca deja escapar un error "en bruto" por sí
    misma.

    Si el fichero SÍ se puede leer pero su contenido no se puede
    interpretar con confianza -JSON mal formado, una raíz que no es un
    objeto, un campo "resultados" que no es una lista, o una entrada de esa
    lista sin la forma esperada (un objeto con "platform")- se considera
    corrupto: se mueve aparte, a `resultado.json.corrupto`, en vez de
    perderse en silencio o de arrastrar a la fusión un dato en el que no se
    puede confiar. La prioridad siempre es no perder el resultado que se
    acaba de publicar; el contenido corrupto queda disponible aparte para
    inspección manual (si una fusión posterior vuelve a encontrar
    corrupción, ese respaldo se sobrescribe: se conserva solo el más
    reciente).
    """
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
            raise ValueError("la raíz del JSON no es un objeto")
        entradas = previo["resultados"]
        if not isinstance(entradas, list):
            raise ValueError("'resultados' no es una lista")
        indexadas: dict[str, dict] = {}
        for entrada in entradas:
            if not isinstance(entrada, dict) or not isinstance(
                entrada.get("platform"), str
            ):
                raise ValueError("una entrada de 'resultados' no tiene la forma esperada")
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
    """Escribe resultado.json en la carpeta del post, fusionando por plataforma.

    Los `PostResult` que llegan aqui son los que devuelven los adaptadores.
    La garantia de que ninguno de sus campos puede arrastrar un token vive
    en origen -en `socialctl/adapters/errores.py`, el helper compartido que
    construye todo mensaje de error de los cuatro adaptadores-, no aqui:
    este modulo persiste esos campos tal cual, sin repetir la comprobacion
    (ver los tests de fuga de secretos, que ejercitan la cadena completa
    adaptador -> este modulo -> resultado.json/historial.md).

    Un reintento parcial (`publicar(post, brand, solo=[...])`) solo trae
    resultados de las redes reintentadas. Si se sobrescribiera el fichero
    entero con esa lista parcial, se perdería el rastro de las redes que ya
    estaban publicadas (sus URLs incluidas). Por eso esta funcion lee primero
    el `resultado.json` existente (si lo hay) y solo actualiza, por
    plataforma, las entradas que llegan en esta llamada: las demas quedan
    tal como estaban.

    El fichero guarda una fecha por entrada (`fecha`, la del guardado en que
    esa plataforma se actualizo por ultima vez) ademas de una fecha global
    (`actualizado`, la de este guardado). Una unica fecha global para todo
    el fichero dejaria de ser coherente en cuanto una fusion mezclara redes
    guardadas en momentos distintos: parecería que YouTube se publicó en el
    instante del reintento de Instagram.

    El orden de las entradas es siempre el mismo (el de `Platform`:
    youtube, facebook, instagram, tiktok), no el orden en que se publicó o
    reintentó cada red, para que el fichero sea estable y predecible entre
    guardados.
    """
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
            f"no se pudo guardar el resultado de '{post.slug}' en {destino}: {exc}"
        ) from exc

    return destino


def anexar_historial(post: Post, brand: Brand, resultados: list[PostResult]) -> None:
    """Anota una entrada legible en historial.md sin tocar lo que ya hubiera.

    Usa modo apend ("a"): el historial acumula una entrada por publicacion,
    nunca se sobrescribe. El estado de cada red se refleja con honestidad
    (ICONO distingue PUBLICADO de PENDIENTE_CONFIRMACION): un video que
    TikTok en modo inbox todavia no ha hecho publico no puede aparecer aqui
    como si ya lo estuviera.
    """
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
            f"no se pudo anexar el resultado de '{post.slug}' a {fichero}: {exc}"
        ) from exc
