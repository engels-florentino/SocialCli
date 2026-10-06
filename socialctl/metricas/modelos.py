"""Common metrics across four platforms. Missing provider fields are None, never zero; zero is an observed value."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from socialctl.models import Platform


class EstadoLectura(str, Enum):
    """Outcome of a platform metrics read."""

    OK = "ok"
    ERROR = "error"
    SIN_CREDENCIALES = "sin_credenciales"
    SIN_PERMISO = "sin_permiso"


class TipoPieza(str, Enum):
    LARGO = "largo"
    VERTICAL = "vertical"
    IMAGEN = "imagen"


class Metricas(BaseModel):
    vistas: int | None = None
    likes: int | None = None
    comentarios: int | None = None
    compartidos: int | None = None
    guardados: int | None = None


class Cuenta(BaseModel):
    seguidores: int | None = None
    total_piezas: int | None = None
    total_vistas: int | None = None


class Audiencia(BaseModel):
    """Channel audience breakdown; currently supplied only by YouTube."""

    paises: dict[str, float] = Field(default_factory=dict)
    edades: dict[str, float] = Field(default_factory=dict)


class Pieza(BaseModel):
    id: str
    url: str
    titulo: str
    publicado_el: datetime
    tipo: TipoPieza
    slug: str | None = None
    duracion_seg: int | None = None
    """Actual technical media duration in seconds when provider supplies it; editorial fallback may be entered only when absent."""
    acumulado: Metricas = Field(default_factory=Metricas)
    # `ultimos_28_dias` vivía aquí y se ha quitado: ningún lector lo
    # rellenaba, nadie lo leía (ni `resumen.md`, ni `piezas.yml`, ni el
    # skill) y salía como `null` en cada pieza de cada snapshot versionado,
    # sugiriendo un dato que no existe. Un campo que nadie escribe no
    # documenta un plan, documenta una promesa incumplida. Los snapshots
    # antiguos que lo llevan se siguen leyendo sin problema: pydantic ignora
    # las claves que el modelo ya no declara. Si algún día se quiere de
    # verdad -la ventana de 28 días que da la API de YouTube-, vuelve con el
    # lector que lo rellene, no antes.
    especificas: dict[str, Any] = Field(default_factory=dict)


class LecturaRed(BaseModel):
    estado: EstadoLectura
    error: str | None = None
    cuenta: Cuenta | None = None
    piezas: list[Pieza] = Field(default_factory=list)
    audiencia: Audiencia | None = None


class Snapshot(BaseModel):
    fecha: date
    marca: str
    redes: dict[Platform, LecturaRed] = Field(default_factory=dict)
