'Importing this package registers all four adapters in ADAPTADORES.'

from __future__ import annotations

from socialctl.adapters import facebook, instagram, tiktok, youtube  # noqa: F401
from socialctl.adapters.base import ADAPTADORES, Adapter  # noqa: F401
