"""Preserve editorial attributes in <Brand>/metricas/piezas.yml while refreshing technical API/post data. Unknown user keys survive; malformed or duplicate entries warn and degrade gracefully."""

from __future__ import annotations

import warnings
from pathlib import Path

import yaml

from socialctl.brands import Brand
from socialctl.metricas.almacen import dir_metricas, escribir_atomico
from socialctl.metricas.modelos import Snapshot


class PiezasIlegibles(Exception):
    """Existing piezas.yml cannot be read and must not be overwritten, since editorial data cannot be regenerated from APIs."""

CAMPOS_EDITORIALES = ("episodio", "pilar", "gancho", "notas")
"""Editorial fields are initialized empty by stats and never overwritten. Duration is technical and refreshed only when provider supplies it."""


def ruta_piezas(brand: Brand) -> Path:
    return dir_metricas(brand) / "piezas.yml"


def cargar_piezas(brand: Brand) -> list[dict]:
    """Load piezas.yml or return an empty list if absent; reject unreadable or non-list YAML to preserve editorial data."""
    ruta = ruta_piezas(brand)
    if not ruta.is_file():
        return []
    texto = ruta.read_text(encoding="utf-8")
    try:
        datos = yaml.safe_load(texto)
    except yaml.YAMLError as exc:
        raise PiezasIlegibles(
            f"{ruta} is not valid YAML ({exc.__class__.__name__}); it is not "
            "modified to avoid deleting editorial data. Repair it manually "
            "—or move it aside to start over—and "
            "run stats."
        ) from exc
    if datos is None and not texto.strip():
        # Un fichero vacío del todo: no hay nada escrito que perder.
        return []
    if not isinstance(datos, list):
        raise PiezasIlegibles(
            f"{ruta} should be a list of items but is "
            f"{type(datos).__name__}; left unchanged to avoid deleting editorial data "
            "it contains. Repair it manually and rerun stats."
        )
    return datos


def fusionar(existentes: list[dict], snapshot: Snapshot) -> list[dict]:
    """Pure merge of provider data and existing editorial entries. Discard malformed entries with warnings; later duplicate identities win, with warnings."""
    por_clave: dict[tuple, dict] = {}
    for posicion, entrada_cruda in enumerate(existentes):
        if not isinstance(entrada_cruda, dict):
            warnings.warn(
                f"piezas.yml: entry number {posicion + 1} is not a "
                f"dictionary (type {type(entrada_cruda).__name__}: "
                f"{entrada_cruda!r}); ignored. Repair it manually in "
                "file and rerun stats.",
                stacklevel=2,
            )
            continue

        clave = (entrada_cruda.get("red"), entrada_cruda.get("id"))
        anterior = por_clave.get(clave)
        if anterior is not None:
            identidad = (
                "without 'red' or 'id'"
                if clave == (None, None)
                else f"red={clave[0]!r}, id={clave[1]!r}"
            )
            warnings.warn(
                f"piezas.yml: two entries share the same identity "
                f"({identidad}): item titled {anterior.get('titulo')!r} is "
                f"discarded in favor of item titled "
                f"{entrada_cruda.get('titulo')!r}. Assign distinct 'red' and 'id' values "
                "in the file to distinguish them.",
                stacklevel=2,
            )
        por_clave[clave] = dict(entrada_cruda)

    for platform, lectura in snapshot.redes.items():
        for pieza in lectura.piezas:
            clave = (platform.value, pieza.id)
            entrada = por_clave.get(clave, {campo: None for campo in CAMPOS_EDITORIALES})
            entrada.update({
                "red": platform.value,
                "id": pieza.id,
                "tipo": pieza.tipo.value,
                "publicado_el": pieza.publicado_el.isoformat(),
                "titulo": pieza.titulo,
            })
            if pieza.slug is not None:
                # El cruce con `posts/` lo resolvió: manda el cruce.
                entrada["slug"] = pieza.slug
            else:
                # No se resolvió: una pieza publicada fuera de `socialctl`, o
                # un TikTok cuyo `resultado.json` no guarda la URL, que es el
                # caso habitual. Se conserva lo que hubiera, porque puede
                # haberlo escrito el gestor a mano. Misma regla asimétrica
                # que `duracion_seg`, y por el mismo motivo: lo que esta
                # ejecución no trae, no se toca.
                entrada.setdefault("slug", None)
            if pieza.duracion_seg is not None:
                # La red la da: manda la red.
                entrada["duracion_seg"] = pieza.duracion_seg
            else:
                # La red no la da (TikTok, Instagram): se conserva lo que
                # hubiera, que puede haberlo escrito el gestor a mano.
                entrada.setdefault("duracion_seg", None)
            for campo in CAMPOS_EDITORIALES:
                entrada.setdefault(campo, None)
            por_clave[clave] = entrada

    return sorted(
        por_clave.values(),
        key=lambda e: (e.get("publicado_el") or "", e.get("red") or ""),
    )


def guardar_piezas(brand: Brand, piezas: list[dict]) -> Path:
    """Atomically rewrite piezas.yml with supplied entries. YAML comments are lost; preserve annotations in notas and retain all user-defined keys."""
    return escribir_atomico(
        ruta_piezas(brand),
        yaml.safe_dump(piezas, allow_unicode=True, sort_keys=False),
    )


def actualizar_piezas(brand: Brand, snapshot: Snapshot) -> Path:
    return guardar_piezas(brand, fusionar(cargar_piezas(brand), snapshot))
