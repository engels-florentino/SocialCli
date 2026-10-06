"""Resolve user data independently of the installed package directory."""
import os
from pathlib import Path


def default_root() -> Path:
    configured = os.environ.get("SOCIALCLI_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path.cwd()
