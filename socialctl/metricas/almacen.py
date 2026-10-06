"""Store one brand snapshot per day; historical files can be read manually."""

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
    """Atomically replace file using a temporary sibling so interruptions never leave a partially written destination."""
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
    """Write daily snapshot atomically."""
    return escribir_atomico(
        ruta_snapshot(brand, snapshot.fecha),
        snapshot.model_dump_json(indent=2) + "\n",
    )


def cargar_snapshot(brand: Brand, fecha: date) -> Snapshot | None:
    """Load dated snapshot, or None if absent/unreadable; warn on corrupt files rather than abort all metrics."""
    ruta = ruta_snapshot(brand, fecha)
    if not ruta.is_file():
        return None
    try:
        return Snapshot.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - cualquier forma ilegible cuenta
        warnings.warn(
            f"snapshot {ruta} cannot be read ({type(exc).__name__}: {exc}); "
            "ignored, so no comparison with that day is available today. A "
            "snapshot can be regenerated: delete it and rerun stats for "
            "today's date, or repair it manually to preserve history.",
            stacklevel=2,
        )
        return None


def _fechas(brand: Brand) -> list[date]:
    """List existing snapshot dates oldest first; ignore non-date JSON filenames."""
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
    """Delete snapshots older than one year; leave piezas.yml and resumen.md unchanged."""
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
