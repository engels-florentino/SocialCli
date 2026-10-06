'Extract actionable provider error text while redacting known credentials.\n\nPrefer the provider error.message field when it is a nonempty string. Otherwise\nretain a bounded response-body excerpt. Redact literal and URL-encoded known\nsecrets of at least eight characters, including proxy responses that reflect\nrequest URLs or bodies. This keeps diagnostics useful without disclosing tokens.'

from __future__ import annotations

import urllib.parse

import httpx

# Recorte razonable para no volcar cuerpos de respuesta gigantes (p. ej. una
# página de error HTML de un proxy intermedio) en un mensaje de error.
_LONGITUD_MAXIMA_CUERPO_ERROR = 500

_MARCA_REDACCION = '[TOKEN REDACTED]'

# Ver docstring del módulo: evita que un "secreto" de relleno (típicamente
# muy corto en los tests que no versan sobre seguridad, p. ej. la cadena
# "t") acabe redactando subcadenas normales de un mensaje legítimo. Ningún
# token real de las cuatro redes que publica este proyecto es tan corto.
_LONGITUD_MINIMA_SECRETO_REDACTABLE = 8


def _redactar_secretos(texto: str, secretos: tuple[str, ...]) -> str:
    'Redact literal and URL-encoded occurrences of each sufficiently long secret.'
    for secreto in secretos:
        if not secreto or len(secreto) < _LONGITUD_MINIMA_SECRETO_REDACTABLE:
            continue
        texto = texto.replace(secreto, _MARCA_REDACCION)
        codificado = urllib.parse.quote(secreto, safe="")
        if codificado != secreto:
            texto = texto.replace(codificado, _MARCA_REDACCION)
    return texto


def mensaje_de_error(respuesta: httpx.Response, *secretos: str) -> str:
    'Return bounded provider error text with known credentials redacted.'
    try:
        mensaje = respuesta.json()["error"]["message"]
        if isinstance(mensaje, str) and mensaje:
            return _redactar_secretos(mensaje, secretos)
    except Exception:
        pass

    try:
        cuerpo = respuesta.text.strip()
    except Exception:
        cuerpo = ""

    cuerpo = _redactar_secretos(cuerpo, secretos)

    if not cuerpo:
        return f"HTTP {respuesta.status_code}"

    if len(cuerpo) > _LONGITUD_MAXIMA_CUERPO_ERROR:
        cuerpo = cuerpo[:_LONGITUD_MAXIMA_CUERPO_ERROR] + "…"

    return f"HTTP {respuesta.status_code}: {cuerpo}"
