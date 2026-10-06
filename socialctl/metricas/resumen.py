"""`<Marca>/metricas/resumen.md`: el snapshot en algo que se lee de un vistazo.

Existe para que el usuario pueda mirar cómo va su marca sin invocar a nadie,
y para que el gestor de redes tenga una entrada barata antes de bajar al
JSON. Lo que aporta sobre el JSON crudo son tres cosas: la **variación**
frente al snapshot anterior, el **pilar y el gancho** de cada pieza sacados
de `piezas.yml` —juntos, es donde se ve el patrón—, y unas **columnas por
red**: cada una muestra lo que mide su objetivo (retención y suscriptores en
YouTube; compartidos y guardados en Instagram; solo compartidos en TikTok,
que no da guardados —ver el comentario junto a `COLUMNAS`—), no las columnas
de YouTube para las cuatro.

Una pieza que no estaba en el snapshot anterior no muestra variación: no ha
"subido" desde cero, es que antes no existía.

Además de lo anterior, la tabla lleva una columna **Aviso**: cuando el
lector de una red no pudo enriquecer una pieza -cuota de YouTube agotada, o
una métrica que Meta retiró (ver `CLAVE_ENRIQUECIMIENTO_FALLIDO` en
`socialctl/metricas/youtube.py`)-, la marca con `enriquecimiento_fallido` en
sus `especificas`, y esa clave solo existe cuando algo falló. Sin esta
columna una lectura parcial se leería como completa, que es justo lo que esa
señal existe para evitar; por eso se muestra aquí aunque el brief original
de esta tarea no traía esta columna.
"""

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
        ("% visto", "porcentaje_visto"),
        ("Subs", "suscriptores_ganados"),
    ),
    # Instagram: el spec (§7) mide su objetivo -alcance- con vistas,
    # compartidos y guardados. Sin estas dos columnas, el gestor tendría que
    # bajar al JSON para lo que en esta red es lo central.
    Platform.INSTAGRAM: (("Compart.", "compartidos"), ("Guardados", "guardados")),
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
    Platform.TIKTOK: (("Compart.", "compartidos"),),
    # Facebook no da guardados, y desde la Graph API v26.0 tampoco da alcance
    # por publicación (ver `METRICAS_MUERTAS` en `metricas/facebook.py`). Lo
    # que sí da, y mide mejor el objetivo, es la retención media: cuántos
    # segundos aguantan de media, en segundos ya convertidos.
    Platform.FACEBOOK: (("Compart.", "compartidos"), ("Retención s", "retencion_media_seg")),
}
"""Hasta dos columnas propias de cada red, además de las cinco comunes.

Dos como máximo, nunca más: la tabla tiene que caber en una pantalla y
leerse de un vistazo; el detalle completo está en el JSON del día. Cada red
muestra lo que mide **su** objetivo, no las columnas de YouTube para las
cuatro -y TikTok, que solo tiene una métrica propia que de verdad aporte a
ese objetivo, se queda con una sola en vez de rellenar la segunda con algo
que no mide nada mejor-.
"""

#: Cuántos caracteres del motivo de un enriquecimiento fallido caben en la
#: celda de "Aviso" antes de recortar. La tabla tiene que seguir leyéndose de
#: un vistazo; el motivo completo -ya redactado, sin secretos- sigue estando
#: en el JSON del snapshot del día para quien necesite el texto entero.
LARGO_AVISO = 80

#: Cuántos caracteres del título de una pieza caben en su celda. Mismo
#: motivo que `LARGO_AVISO`: la tabla tiene que leerse de un vistazo.
LARGO_TITULO = 48


def _recortar(texto: str, largo: int) -> str:
    """Recorta a `largo` caracteres **por palabra** y marcando el corte con `…`.

    Antes se cortaba con un `[:48]` a secas, y el resultado era un título
    partido a mitad de palabra y sin ninguna señal de que faltaba algo
    (`…y fundiero`), que se lee como un error del fichero y no como un
    recorte. Se corta por el último espacio que quepa; si la primera palabra
    ya no cabe -un motivo de error sin espacios, por ejemplo-, se corta
    donde sea, porque lo que no puede es desbordar la celda.

    El `…` cuenta dentro de `largo`: el resultado nunca lo pasa.
    """
    if len(texto) <= largo:
        return texto
    recortado = texto[: largo - 1].rstrip()
    espacio = recortado.rfind(" ")
    if espacio > 0:
        recortado = recortado[:espacio].rstrip()
    return recortado + "…"


def _escapar(celda: str) -> str:
    """Deja una celda lista para una tabla markdown.

    Un título con `|` -«1519: Cortés | la conquista»- partía la fila en dos
    columnas de más y descuadraba la tabla entera a partir de ahí. Se escapa
    la barra, que es lo único que markdown interpreta dentro de una celda, y
    se aplanan los saltos de línea, que la romperían igual.
    """
    return celda.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _valor(pieza: Pieza | None, clave: str) -> object | None:
    """Busca `clave` primero en `Metricas` y luego en `especificas`.

    Las dos fuentes conviven en la misma tabla a propósito: al gestor le da
    igual si `guardados` es un campo del modelo común y `porcentaje_visto`
    una clave específica de YouTube; lo que quiere es la columna.
    """
    if pieza is None:
        return None
    if clave in Metricas.model_fields:
        return getattr(pieza.acumulado, clave)
    return pieza.especificas.get(clave)


def _variacion(actual: object, previo: object) -> str:
    """`+54`, `-20`, `+3.5`, o vacío si no hay con qué comparar o no cambió.

    Un `(+0)` en la celda no dice nada que la propia cifra no diga ya, y
    repetido en media tabla tapa las variaciones que sí importan: cuando el
    valor no se ha movido, la celda va limpia.
    """
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
    """`254 (+54)`, o solo `254` si no hay snapshot anterior con esa pieza.

    La variación va dentro de la misma celda y no en una columna aparte: con
    tres métricas comparadas, tres columnas `Δ` más harían la tabla
    ilegible, que es justo lo que este fichero viene a evitar.
    """
    base = _celda(actual)
    delta = _variacion(actual, previo)
    return f"{base} ({delta})" if delta else base


def _aviso(pieza: Pieza) -> str:
    """`⚠ <motivo>` si el enriquecimiento de `pieza` falló, `—` si no.

    `CLAVE_ENRIQUECIMIENTO_FALLIDO` solo existe en `especificas` cuando algo
    falló -cuota de YouTube agotada, o una métrica que Meta retiró-, así que
    su sola presencia ya es la señal (ver el docstring del módulo). Sin esta
    columna, una lectura parcial se vería en `resumen.md` exactamente igual
    que una completa.
    """
    motivo = pieza.especificas.get(CLAVE_ENRIQUECIMIENTO_FALLIDO)
    if not motivo:
        return "—"
    return f"⚠ {_recortar(str(motivo), LARGO_AVISO)}"


def render_resumen(
    snapshot: Snapshot, anterior: Snapshot | None, piezas: list[dict]
) -> str:
    editoriales = {(p.get("red"), p.get("id")): p for p in piezas}
    lineas = [
        f"# Métricas de {snapshot.marca}",
        "",
        f"**Snapshot:** {snapshot.fecha.isoformat()}",
    ]

    if anterior is None:
        lineas.append(
            "**Variación:** no hay snapshot anterior con el que comparar "
            "(primer snapshot)."
        )
    else:
        lineas.append(f"**Comparado con:** {anterior.fecha.isoformat()}")
    lineas.append("")

    for platform, lectura in snapshot.redes.items():
        lineas.append(f"## {platform.value}")
        lineas.append("")
        if lectura.estado is not EstadoLectura.OK:
            lineas += [
                f"**Estado:** `{lectura.estado.value}` — {lectura.error or 'sin detalle'}",
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
                f"Seguidores: {_celda(lectura.cuenta.seguidores)} · "
                f"Piezas: {_celda(lectura.cuenta.total_piezas)} · "
                f"Vistas totales: {_celda(lectura.cuenta.total_vistas)}",
                "",
            ]

        previas = {}
        if anterior is not None and platform in anterior.redes:
            previas = {p.id: p for p in anterior.redes[platform].piezas}

        # `COLUMNAS` cubre las cuatro redes de `Platform`, que son todas las
        # que puede traer un snapshot: no hay caso por defecto que atender.
        columnas = COLUMNAS[platform]
        titulos = ["Publicado", "Pieza", "Pilar", "Gancho", "Vistas"]
        titulos += [titulo for titulo, _ in columnas]
        titulos.append("Aviso")
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
                lineas += ["### Alcance de miniaturas (28 días · YouTube Reporting)", "",
                           "| Pieza | Impresiones | CTR | Hasta | Informe |",
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
                lineas += ["**Miniaturas:** sin informe de alcance importado para estas piezas.", ""]

    lineas += [
        "---",
        "",
        "Generado por `socialctl stats`. El detalle completo —curva de retención,",
        "fuentes de tráfico, audiencia— está en el JSON del mismo día.",
        "Impresiones y CTR de miniatura proceden de YouTube Reporting cuando",
        "hay un informe de alcance importado; un dato ausente no equivale a cero.",
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
