"""Shared validation for externally supplied path components and relative paths."""

from __future__ import annotations

import os
from pathlib import Path


def validar_componente_de_ruta(
    raiz: Path,
    valor: object,
    excepcion: type[Exception],
    sustantivo: str,
) -> Path:
    """Validate a single safe path component within the root, including symbolic-link containment."""
    if not isinstance(valor, str):
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (must be a string)"
        )

    if not valor or not valor.strip():
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (is empty or contains only whitespace)"
        )

    candidato = Path(valor)
    if candidato.is_absolute():
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (is an absolute path; must be "
            "a name without path separators)"
        )

    if (
        "/" in valor
        or os.sep in valor
        or len(candidato.parts) != 1
        or valor in (".", "..")
    ):
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (cannot contain path separators "
            "or be '.' or '..')"
        )

    raiz_resuelta = raiz.resolve()
    resultado_resuelto = (raiz / valor).resolve()
    if resultado_resuelto.parent != raiz_resuelta:
        raise excepcion(
            f'{sustantivo} invalid: {valor!r} (resolves outside {raiz}, probably through a symbolic link)'
        )

    return raiz / valor


def validar_ruta_relativa(
    raiz: Path,
    valor: object,
    excepcion: type[Exception],
    sustantivo: str,
) -> Path:
    """Validate a relative path within the root, allowing safe subdirectories and rejecting symbolic-link escapes."""
    if not isinstance(valor, str):
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (must be a string)"
        )

    if "\x00" in valor:
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (cannot contain a null byte)"
        )

    if not valor or not valor.strip():
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (is empty or contains only whitespace)"
        )

    if "\\" in valor:
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (use '/' as the subdirectory separator; '\\' is not supported)"
        )

    if valor.startswith("/"):
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (is an absolute path; must be "
            "relative within the corresponding directory)"
        )

    segmentos = valor.split("/")
    if any(not segmento.strip() or segmento in (".", "..") for segmento in segmentos):
        raise excepcion(
            f"{sustantivo} invalid: {valor!r} (no segment may be "
            "empty, contain only whitespace, or be '.' or '..')"
        )

    resultado = raiz.joinpath(*segmentos)

    raiz_resuelta = raiz.resolve()
    resultado_resuelto = resultado.resolve()
    if raiz_resuelta not in resultado_resuelto.parents:
        raise excepcion(
            f'{sustantivo} invalid: {valor!r} (resolves outside {raiz}, probably through a symbolic link)'
        )

    return resultado
