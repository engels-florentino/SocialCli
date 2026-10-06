"""Render daily snapshot as readable Markdown at <Brand>/metricas/resumen.md; detailed data remains in JSON."""

from __future__ import annotations

from pathlib import Path

from socialctl.brands import Brand
from socialctl.metricas.almacen import (
    dir_metricas,
    escribir_atomico,
    snapshot_anterior,
)
from socialctl.metricas.modelos import EstadoLectura, Metricas, Pieza, Snapshot
from socialctl.metricas.piezas import cargar_piezas
from socialctl.metricas.youtube import CLAVE_ENRIQUECIMIENTO_FALLIDO
from socialctl.models import Platform

COLUMNAS: dict[Platform, tuple[tuple[str, str], ...]] = {
    # YouTube: retención y suscriptores, que es lo que mide el objetivo de
    # los largos y el de crecer en suscriptores.
    Platform.YOUTUBE: (
        ("% viewed", "porcentaje_visto"),
        ("Subs", "suscriptores_ganados"),
    ),
    # Instagram: el spec (§7) mide su objetivo -alcance- con vistas,
    # compartidos y guardados. Sin estas dos columnas, el gestor tendría que
    # bajar al JSON para lo que en esta red es lo central.
    Platform.INSTAGRAM: (("Shares", "compartidos"), ("Saves", "guardados")),
    # TikTok: **no hay columna de guardados, y no es un olvido.** La Display
    # API de TikTok no expone esa métrica -ver el docstring de
    # `socialctl/metricas/tiktok.py`-, verificado contra la cuenta real de
    # la marca el 2026-09-11, no supuesto. Su lector nunca rellena
    # `guardados` (queda en `None` por la regla de oro de
    # `socialctl/metricas/modelos.py`), así que esta columna saldría en
    # blanco en todas las filas, siempre, y nadie entendería por qué.
    #
    # No se sustituye por `likes` ni `comentarios` -las dos vivas en su
    # lector-: ninguna mide alcance mejor que `compartidos` sola, así que
    # añadir una sería relleno, no una columna que aporte (mismo criterio
    # que `METRICAS_MUERTAS` en `socialctl/metricas/facebook.py`, que
    # tampoco sustituye `alcance` por una métrica parecida). Si alguien
    # reintenta esto: la Display API sigue sin dar guardados; compruébalo
    # tú mismo contra la cuenta real antes de asumir que cambió.
    Platform.TIKTOK: (("Shares", "compartidos"),),
    # Facebook no da guardados, y desde la Graph API v26.0 tampoco da alcance
    # por publicación (ver `METRICAS_MUERTAS` en `metricas/facebook.py`). Lo
    # que sí da, y mide mejor el objetivo, es la retención media: cuántos
    # segundos aguantan de media, en segundos ya convertidos.
    Platform.FACEBOOK: (("Shares", "compartidos"), ("Retention s", "retencion_media_seg")),
}
"""Use at most two platform-specific columns alongside the five common columns."""

#: Cuántos caracteres del motivo de un enriquecimiento fallido caben en la
#: celda de "Aviso" antes de recortar. La tabla tiene que seguir leyéndose de
#: un vistazo; el motivo completo -ya redactado, sin secretos- sigue estando
#: en el JSON del snapshot del día para quien necesite el texto entero.
LARGO_AVISO = 80

#: Cuántos caracteres del título de una pieza caben en su celda. Mismo
#: motivo que `LARGO_AVISO`: la tabla tiene que leerse de un vistazo.
LARGO_TITULO = 48


def _recortar(texto: str, largo: int) -> str:
    """Truncate to largo characters at word boundaries, marking truncation with an ellipsis."""
    if len(texto) <= largo:
        return texto
    recortado = texto[: largo - 1].rstrip()
    espacio = recortado.rfind(" ")
    if espacio > 0:
        recortado = recortado[:espacio].rstrip()
    return recortado + "…"


def _escapar(celda: str) -> str:
    """Escape pipes and replace line breaks so text fits a Markdown table cell."""
    return celda.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _valor(pieza: Pieza | None, clave: str) -> object | None:
    """Look up clave in common metrics first, then platform-specific metrics."""
    if pieza is None:
        return None
    if clave in Metricas.model_fields:
        return getattr(pieza.acumulado, clave)
    return pieza.especificas.get(clave)


def _variacion(actual: object, previo: object) -> str:
    """Format signed delta, or empty text when unchanged or unavailable."""
    if not isinstance(actual, (int, float)) or not isinstance(previo, (int, float)):
        return ""
    diferencia = actual - previo
    if isinstance(diferencia, float):
        diferencia = round(diferencia, 1)
        if diferencia == int(diferencia):
            diferencia = int(diferencia)
    if diferencia == 0:
        return ""
    return f"+{diferencia}" if diferencia > 0 else str(diferencia)


def _celda(valor: object) -> str:
    return "—" if valor is None else str(valor)


def _celda_comparada(actual: object, previo: object) -> str:
    """Format value with delta when an earlier matching item exists."""
    base = _celda(actual)
    delta = _variacion(actual, previo)
    return f"{base} ({delta})" if delta else base


def _aviso(pieza: Pieza) -> str:
    """Show enrichment failure reason or an em dash; never infer missing data as zero."""
    motivo = pieza.especificas.get(CLAVE_ENRIQUECIMIENTO_FALLIDO)
    if not motivo:
        return "—"
    return f"⚠ {_recortar(str(motivo), LARGO_AVISO)}"


def render_resumen(
    snapshot: Snapshot, anterior: Snapshot | None, piezas: list[dict]
) -> str:
    editoriales = {(p.get("red"), p.get("id")): p for p in piezas}
    lineas = [
        f"# Metrics for {snapshot.marca}",
        "",
        f"**Snapshot:** {snapshot.fecha.isoformat()}",
    ]

    if anterior is None:
        lineas.append(
            "**Change:** no previous snapshot available for comparison "
            "(first snapshot)."
        )
    else:
        lineas.append(f"**Compared with:** {anterior.fecha.isoformat()}")
    lineas.append("")

    for platform, lectura in snapshot.redes.items():
        lineas.append(f"## {platform.value}")
        lineas.append("")
        if lectura.estado is not EstadoLectura.OK:
            lineas += [
                f"**Status:** `{lectura.estado.value}` — {lectura.error or 'no details'}",
                "",
            ]
            # El aviso de estado SÍ, pero la tabla también si hay piezas. Con
            # `--only`, `_fusionar_snapshot_del_dia` (`socialctl/cli.py`)
            # conserva los últimos números buenos de una red que hoy falla y
            # le pone el `estado` de hoy; ese arreglo prometía que "el resumen
            # y `piezas.yml` siguen viendo los últimos números buenos en vez
            # de un hueco", y aquí no se cumplía: un `continue` incondicional
            # borraba de `resumen.md` las piezas conservadas, la línea de
            # seguidores y la tabla entera, justo en el escenario que el
            # arreglo existe para evitar. Sin piezas no hay nada que enseñar
            # y se sigue saltando la tabla, que es el caso de una red que
            # falló sin dato previo.
            if not lectura.piezas:
                continue

        if lectura.cuenta is not None:
            lineas += [
                f"Followers: {_celda(lectura.cuenta.seguidores)} · "
                f"Items: {_celda(lectura.cuenta.total_piezas)} · "
                f"Total views: {_celda(lectura.cuenta.total_vistas)}",
                "",
            ]

        previas = {}
        if anterior is not None and platform in anterior.redes:
            previas = {p.id: p for p in anterior.redes[platform].piezas}

        # `COLUMNAS` cubre las cuatro redes de `Platform`, que son todas las
        # que puede traer un snapshot: no hay caso por defecto que atender.
        columnas = COLUMNAS[platform]
        titulos = ["Published", "Item", "Pillar", "Hook", "Views"]
        titulos += [titulo for titulo, _ in columnas]
        titulos.append("Notice")
        lineas += [
            "| " + " | ".join(titulos) + " |",
            "|" + "---|" * len(titulos),
        ]
        for pieza in sorted(lectura.piezas, key=lambda p: p.publicado_el, reverse=True):
            editorial = editoriales.get((platform.value, pieza.id), {})
            previa = previas.get(pieza.id)
            celdas = [
                pieza.publicado_el.date().isoformat(),
                _recortar(pieza.titulo, LARGO_TITULO),
                _celda(editorial.get("pilar")),
                _celda(editorial.get("gancho")),
                _celda_comparada(
                    pieza.acumulado.vistas,
                    previa.acumulado.vistas if previa else None,
                ),
            ]
            for _, clave in columnas:
                celdas.append(
                    _celda_comparada(
                        _valor(pieza, clave),
                        _valor(previa, clave) if previa else None,
                    )
                )
            celdas.append(_aviso(pieza))
            lineas.append("| " + " | ".join(_escapar(c) for c in celdas) + " |")
        lineas.append("")
        if platform is Platform.YOUTUBE:
            reach_rows = [(pieza, pieza.especificas.get("thumbnail_reach_28d"))
                          for pieza in lectura.piezas]
            reach_rows = [(pieza, reach) for pieza, reach in reach_rows
                          if isinstance(reach, dict) and reach.get("impressions") is not None]
            if reach_rows:
                lineas += ["### Thumbnail reach (28 days · YouTube Reporting)", "",
                           "| Item | Impressions | CTR | Through | Report |",
                           "|---|---:|---:|---|---|"]
                for pieza, reach in sorted(reach_rows,
                                           key=lambda item: item[1]["impressions"],
                                           reverse=True)[:10]:
                    ctr = reach.get("ctr")
                    ctr_text = f"{ctr:.1f}%" if isinstance(ctr, (int, float)) else "—"
                    cells = (_recortar(pieza.titulo, LARGO_TITULO),
                             str(reach["impressions"]), ctr_text,
                             reach.get("through", "—"),
                             ", ".join(reach.get("report_ids", [])))
                    lineas.append("| " + " | ".join(_escapar(value) for value in cells) + " |")
                lineas.append("")
            else:
                lineas += ["**Thumbnails:** no imported reach report for these items.", ""]

    lineas += [
        "---",
        "",
        "Generated by `socialcli stats`. Full detail — retention curve,",
        "traffic sources, audience — is in the JSON for the same day.",
        "Thumbnail impressions and CTR come from YouTube Reporting when",
        "an imported reach report exists; missing data does not equal zero.",
        "",
    ]
    return "\n".join(lineas)


def escribir_resumen(brand: Brand, snapshot: Snapshot) -> Path:
    texto = render_resumen(
        snapshot,
        anterior=snapshot_anterior(brand, antes_de=snapshot.fecha),
        piezas=cargar_piezas(brand),
    )
    return escribir_atomico(dir_metricas(brand) / "resumen.md", texto)
