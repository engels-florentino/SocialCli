"""Read-only network metrics package; imports register available readers."""

from __future__ import annotations

from socialctl.metricas import facebook, instagram, tiktok, youtube  # noqa: F401
from socialctl.metricas.base import LECTORES, Lector, SinPermiso, leer_red  # noqa: F401
