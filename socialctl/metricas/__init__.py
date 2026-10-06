"""Lectura de métricas de las redes. Solo lectura: nada de esto publica nada.

Importar este paquete registra en `LECTORES` los lectores que ya existan.
"""

from __future__ import annotations

from socialctl.metricas import facebook, instagram, tiktok, youtube  # noqa: F401
from socialctl.metricas.base import LECTORES, Lector, SinPermiso, leer_red  # noqa: F401
