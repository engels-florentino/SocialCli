"""Extracción compartida del mensaje de error a partir de una respuesta HTTP.

Los cuatro adaptadores (`youtube.py`, `facebook.py`, `instagram.py`,
`tiktok.py`) necesitan lo mismo cuando una red responde con un HTTP que no es
de éxito: un mensaje accionable, en español, que explique qué ha pasado.
Antes de este módulo, cada adaptador tenía su propia copia de
``_mensaje_de_error``, escrita en momentos distintos de la misma rama -y ya
habían divergido: `youtube.py`, `facebook.py` e `instagram.py` (corregidos en
el commit `75271d6`) aceptaban ``message: ""`` como un mensaje válido
("" es una cadena de texto, así que ``isinstance(mensaje, str)`` no la
descarta), mientras que `tiktok.py` (escrito después, en `e1dbf64`) ya
exigía que además fuera no vacía. El resultado observable: con
``{"error": {"message": ""}}``, tres de las cuatro redes daban
``PostResult(error="")`` -un "error" sin ningún motivo, que el usuario ve en
pantalla y que queda igual de vacío en `resultado.json` y en
`historial.md`-, y solo TikTok daba un mensaje útil. Unificar aquí, con el
criterio de TikTok (rechazar la cadena vacía) como el bueno, hace que sea
literalmente imposible que las cuatro redes vuelvan a divergir en esto.

Hallazgo crítico de la revisión final (C1): Instagram manda el token como
parámetro de consulta al sondear el contenedor
(`GET .../{id}?...&access_token=<token>`), y tanto Instagram como Facebook lo
mandan en el CUERPO de sus peticiones de creación/publicación. Los
docstrings de esos módulos afirmaban que el mensaje de error "nunca puede
arrastrar el token" porque el texto interpolado procede siempre del cuerpo de
la RESPUESTA, nunca de la petición. Esa premisa es cierta casi siempre, pero
no protege de un intermediario (un proxy, un balanceador, un WAF) que, ante
un 502/504 u otro error, devuelva una página que refleje la URL o el cuerpo
de la petición que falló -un patrón real y conocido de nginx/varnish y
similares-. Si esa respuesta no tiene la forma ``{"error": {"message":
...}}`` de la Graph API/Content Posting API, el código de antes volcaba el
cuerpo crudo (hasta 500 caracteres) sin comprobar qué contenía, y el token
se colaba en `PostResult.error` -de ahí a `resultado.json` y a
`historial.md`, los dos sin `.gitignore`-.

Decisión de arreglo (dos alternativas posibles, se documenta la elegida y
por qué): la opción de "no volcar nunca cuerpos crudos de respuestas
inesperadas" se descartó porque tiraría, para las CUATRO redes, un
diagnóstico legítimo y ya cubierto por tests (`test_mensaje_de_error_
conserva_cuerpo_cuando_no_es_el_formato_esperado` en
`tests/test_adapter_youtube.py`, entre otros) -y ninguna de las dos redes sin
este riesgo (YouTube y TikTok, cuyo token viaja solo en la cabecera
``Authorization``) necesita perder ese diagnóstico para arreglar un problema
que no tienen-. En su lugar, `mensaje_de_error` acepta los secretos
conocidos de la llamada (hoy, el token de acceso) y los REDACTA de
cualquier texto que vaya a devolver -tanto del ``message`` de un error bien
formado como del cuerpo crudo de respaldo-, sea cual sea la superficie por
la que se hayan colado (cuerpo de la respuesta, o la URL/cuerpo de la
petición reflejados por un intermediario). Esto sí hace ciertos, ahora de
verdad, los docstrings de `instagram.py` y `facebook.py`: "el mensaje nunca
puede arrastrar el token" deja de depender de que la respuesta no refleje la
petición, y pasa a estar garantizado incluso si lo hace.

La redacción solo actúa sobre secretos de al menos
``_LONGITUD_MINIMA_SECRETO_REDACTABLE`` caracteres: los tokens reales de las
cuatro redes son cadenas largas (decenas o cientos de caracteres); un valor
más corto casi con toda seguridad no es un token real -en los tests de este
proyecto, por ejemplo, es habitual usar la cadena de un solo carácter "t"
como token de relleno cuando el test no versa sobre seguridad-, y redactar
una subcadena tan corta destrozaría cualquier mensaje que, por pura
coincidencia, contenga esos caracteres.
"""

from __future__ import annotations

import urllib.parse

import httpx

# Recorte razonable para no volcar cuerpos de respuesta gigantes (p. ej. una
# página de error HTML de un proxy intermedio) en un mensaje de error.
_LONGITUD_MAXIMA_CUERPO_ERROR = 500

_MARCA_REDACCION = "[TOKEN REDACTADO]"

# Ver docstring del módulo: evita que un "secreto" de relleno (típicamente
# muy corto en los tests que no versan sobre seguridad, p. ej. la cadena
# "t") acabe redactando subcadenas normales de un mensaje legítimo. Ningún
# token real de las cuatro redes que publica este proyecto es tan corto.
_LONGITUD_MINIMA_SECRETO_REDACTABLE = 8


def _redactar_secretos(texto: str, secretos: tuple[str, ...]) -> str:
    """Sustituye, dentro de ``texto``, cualquier aparición de un secreto conocido.

    Comprueba tanto el valor literal como su forma codificada para URL
    (``urllib.parse.quote``), porque el token puede reflejarse dentro de una
    URL que un proxy haya vuelto a mostrar en su página de error.
    """
    for secreto in secretos:
        if not secreto or len(secreto) < _LONGITUD_MINIMA_SECRETO_REDACTABLE:
            continue
        texto = texto.replace(secreto, _MARCA_REDACCION)
        codificado = urllib.parse.quote(secreto, safe="")
        if codificado != secreto:
            texto = texto.replace(codificado, _MARCA_REDACCION)
    return texto


def mensaje_de_error(respuesta: httpx.Response, *secretos: str) -> str:
    """Extrae un mensaje de error accionable del cuerpo de una respuesta HTTP.

    Prioriza el formato estándar de error de la Graph API/Content Posting API
    (``{"error": {"message": ...}}``). Si el cuerpo no encaja en ese formato
    -o si ``message`` está vacío o no es una cadena de texto-, no se descarta
    toda la información: se conserva un fragmento del cuerpo (recortado a una
    longitud razonable) en vez de reducir el mensaje a un escueto ``f"HTTP
    {status_code}"``.

    ``*secretos`` son los valores (típicamente, el token de acceso usado en
    la petición que produjo esta respuesta) que NUNCA deben aparecer en el
    resultado: se redactan tanto del ``message`` como del cuerpo crudo de
    respaldo antes de devolverlos (ver docstring del módulo). Cada adaptador
    pasa aquí el token que tenga en ámbito en ese momento; ninguno interpola
    nunca la petición ni sus cabeceras, así que esta redacción es una
    salvaguarda adicional para el caso en que la RESPUESTA -de un
    intermediario que refleje la petición que falló, no de la red de
    destino- arrastre el token por su cuenta.
    """
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
