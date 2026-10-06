"""Dónde viven los snapshots de una marca y cómo se leen y escriben.

Un fichero por día, con la fecha en el nombre: así el histórico se ordena
solo, se versiona en git y se puede leer a mano sin ninguna herramienta.
Dos ejecuciones el mismo día sobrescriben, no duplican.
"""

from __future__ import annotations

import json
import os
import tempfile
import warnings
from datetime import date, timedelta
from pathlib import Path

from socialctl.brands import Brand
from socialctl.metricas.modelos import Snapshot


def escribir_atomico(ruta: Path, texto: str) -> Path:
    """Escribe `texto` en `ruta` sin que exista nunca un fichero a medias.

    Un `ruta.write_text(...)` trunca el fichero ANTES de escribir el
    contenido nuevo: si el proceso muere ahí -una interrupción, el disco
    lleno, un crash-, lo que queda en disco es el fichero viejo truncado. Con
    `piezas.yml` eso significa perder para siempre lo editorial que el gestor
    y el usuario escribieron a mano, que es lo único de `<Marca>/metricas/`
    que no se puede volver a pedir a la API.

    Aquí se escribe primero un temporal **en el mismo directorio** -no en
    `/tmp`, porque `os.replace` solo es atómico dentro del mismo sistema de
    ficheros- y solo cuando ese temporal está completo y volcado a disco se
    hace `os.replace`, que sustituye el destino en un solo paso: en todo
    momento `ruta` es o el contenido viejo entero o el nuevo entero, nunca
    medio fichero.

    El `flush` + `fsync` no son adorno: sin ellos `os.replace` puede publicar
    un nombre que apunta a datos que el sistema todavía no ha escrito, y un
    corte de corriente dejaría exactamente el fichero truncado que esto viene
    a evitar.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(
        dir=ruta.parent, prefix=f".{ruta.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as fichero:
            fichero.write(texto)
            fichero.flush()
            os.fsync(fichero.fileno())
        os.replace(temporal, ruta)
    except BaseException:
        # Si algo falló antes del `replace`, el destino sigue intacto; lo
        # único que sobra es el temporal, que no debe quedarse en la carpeta
        # del usuario.
        Path(temporal).unlink(missing_ok=True)
        raise
    return ruta


def dir_metricas(brand: Brand) -> Path:
    return brand.raiz / "metricas"


def ruta_snapshot(brand: Brand, fecha: date) -> Path:
    return dir_metricas(brand) / f"{fecha.isoformat()}.json"


def guardar_snapshot(brand: Brand, snapshot: Snapshot) -> Path:
    """Escribe el snapshot del día, de forma atómica (ver `escribir_atomico`)."""
    return escribir_atomico(
        ruta_snapshot(brand, snapshot.fecha),
        snapshot.model_dump_json(indent=2) + "\n",
    )


def cargar_snapshot(brand: Brand, fecha: date) -> Snapshot | None:
    """Carga el snapshot de `fecha`, o `None` si no existe **o no se puede leer**.

    Un snapshot ilegible -JSON truncado, un fichero que alguien editó a
    mano, una forma que ya no valida- se trata como si no estuviera, y se
    avisa con un `UserWarning` que dice qué fichero es y por qué se ignora.

    Por qué aquí sí se ignora y en `piezas.yml` no (ver
    `socialctl/metricas/piezas.py`): **un snapshot se regenera**. Sus
    números vuelven a pedirse a la API en la siguiente ejecución de `stats`,
    así que lo único que cuesta ignorarlo es perder la comparación con ese
    día. Lo que no se puede permitir es lo que pasaba antes: esta función
    se llama desde `escribir_resumen` y desde `_avisar_de_fallos_persistentes`
    (`socialctl/cli.py`), las dos sin protección, así que **un solo fichero
    corrupto tumbaba todas las ejecuciones futuras de la marca** -y encima
    después de haber escrito ya el snapshot de hoy y `piezas.yml`, dejando
    el estado a medias-. Lo editorial de `piezas.yml`, en cambio, no se
    regenera: por eso allí un fichero ilegible para el comando en vez de
    ignorarse.

    El aviso no es decorativo: dice la ruta exacta, para que el usuario
    pueda borrar ese fichero (o arreglarlo) en vez de verse una traza que no
    le dice qué tocar.
    """
    ruta = ruta_snapshot(brand, fecha)
    if not ruta.is_file():
        return None
    try:
        return Snapshot.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - cualquier forma ilegible cuenta
        warnings.warn(
            f"el snapshot {ruta} no se puede leer ({type(exc).__name__}: {exc}); "
            "se ignora, así que hoy no habrá comparación con ese día. Un "
            "snapshot se regenera: bórralo y vuelve a ejecutar stats para la "
            "fecha de hoy, o arréglalo a mano si te importa ese histórico.",
            stacklevel=2,
        )
        return None


def _fechas(brand: Brand) -> list[date]:
    """Fechas de los snapshots existentes, de más antigua a más reciente.

    Ignora cualquier `.json` cuyo nombre no sea una fecha: la carpeta es del
    usuario tanto como nuestra, y un fichero suyo no debe romper la lectura.
    """
    carpeta = dir_metricas(brand)
    if not carpeta.is_dir():
        return []
    fechas = []
    for fichero in carpeta.glob("*.json"):
        try:
            fechas.append(date.fromisoformat(fichero.stem))
        except ValueError:
            continue
    return sorted(fechas)


def ultimo_snapshot(brand: Brand) -> Snapshot | None:
    fechas = _fechas(brand)
    return cargar_snapshot(brand, fechas[-1]) if fechas else None


def snapshot_anterior(brand: Brand, antes_de: date) -> Snapshot | None:
    previas = [f for f in _fechas(brand) if f < antes_de]
    return cargar_snapshot(brand, previas[-1]) if previas else None


#: Cuánto histórico de snapshots se conserva. Decisión del usuario, literal:
#: un año, sin adelgazar los ficheros intermedios -160 MB anuales es un
#: coste aceptable y la simplicidad vale más que ahorrar unos megas-.
#:
#: **Por qué 365 días corridos y no el año natural** (1 de enero a 31 de
#: diciembre): el año natural haría que el tamaño de la carpeta dependiera
#: de qué día del año es hoy -el 2 de enero se quedaría con apenas dos
#: días de histórico, aunque ayer mismo hubiera un año entero-, que es
#: justo lo contrario de predecible para quien mire la carpeta. Con una
#: ventana corrida, la carpeta contiene siempre (ejecutando `stats` a
#: diario) el mismo número de ficheros aproximado y el mismo peso
#: aproximado, sea hoy el día que sea del calendario; y "un año" significa
#: lo mismo que en la frase del usuario: 365 días hacia atrás desde hoy,
#: sin sorpresas de fin de año.
RETENCION_SNAPSHOTS_DIAS = 365


def limpiar_snapshots_antiguos(
    brand: Brand, hoy: date | None = None
) -> tuple[list[Path], list[tuple[Path, OSError]]]:
    """Borra los snapshots de más de un año; nunca toca `piezas.yml` ni `resumen.md`.

    Dónde vive esta política: aquí, no en `cli.py`. `almacen.py` es quien ya
    conoce las rutas de `<Marca>/metricas/` y quien ya tiene `_fechas`, la
    función que decide qué `.json` de la carpeta es un snapshot por su
    nombre -ignorando cualquier otro fichero que el usuario haya dejado ahí-.
    Repetir ese criterio en `cli.py` con otro recorrido del directorio sería
    duplicar la única fuente de verdad sobre "qué es un snapshot" y
    arriesgarse a que las dos copias diverjan con el tiempo. Lo que sí es
    trabajo de `cli.py` es decidir CUÁNDO se llama (con cada `stats`, spec
    §5) y CÓMO se cuenta lo que pasó por pantalla: esta función es una
    primitiva de almacén -como `guardar_snapshot`- que ni imprime ni conoce
    `typer`, y devuelve lo que hizo para que el llamador decida qué decir.

    Por qué `piezas.yml` y `resumen.md` no necesitan un caso especial: esta
    función solo borra fechas que salen de `_fechas`, que solo mira
    `*.json` cuyo nombre entero es una fecha ISO. Ninguno de los dos
    ficheros es `.json`, así que ninguno de los dos entra jamás en la lista
    a borrar -no es una excepción que haya que recordar mantener, es una
    consecuencia de reutilizar `_fechas` tal cual está-.

    Se borra por la fecha del NOMBRE, nunca por la fecha de modificación:
    un snapshot antiguo que alguien hubiera reescrito hoy (a mano, o porque
    `--only` lo fusionó) seguiría teniendo el nombre de su fecha original y
    se borraría igual si esa fecha ya pasó el límite. Mirar el `mtime` en
    vez del nombre premiaría con más vida a un fichero que se tocó por
    casualidad, que es exactamente lo que la regla del usuario prohíbe.

    Ante un fichero que no se puede borrar (permisos, por ejemplo): esta
    función NO lanza. Sigue con el resto y devuelve el fallo en la segunda
    lista, junto con la excepción, para que `cli.py` lo cuente. Abortar
    `stats` por esto sería el peor de los dos desenlaces: la limpieza es un
    housekeeping de fin de ejecución, y el propio snapshot de hoy -que ya
    ha costado cuota de API en las cuatro redes- se guarda ANTES de llegar
    aquí (ver `stats` en `cli.py`); tirar esa lectura ya pagada porque un
    fichero de hace tres años tiene un permiso raro sería cambiar un
    problema de espacio en disco, que se arregla borrando a mano ese
    fichero, por uno de datos perdidos, que no se arregla. Avisar y seguir
    dice el problema sin agravarlo.

    Sin opción para desactivarla: la retención es una decisión ya tomada
    por el usuario ("conservar un año y borrar lo que pase de ahí"), fija y
    sin matices -no "por defecto, salvo que..."-, y en este proyecto una
    opción que nadie ha pedido todavía es sobreconstrucción. Si el usuario
    cambia de opinión sobre el plazo, `RETENCION_SNAPSHOTS_DIAS` es el único
    número que hay que tocar.
    """
    hoy = hoy if hoy is not None else date.today()
    corte = hoy - timedelta(days=RETENCION_SNAPSHOTS_DIAS)

    borrados: list[Path] = []
    fallidos: list[tuple[Path, OSError]] = []
    for fecha in _fechas(brand):
        if fecha >= corte:
            continue
        ruta = ruta_snapshot(brand, fecha)
        try:
            ruta.unlink()
        except OSError as exc:
            fallidos.append((ruta, exc))
        else:
            borrados.append(ruta)
    return borrados, fallidos
