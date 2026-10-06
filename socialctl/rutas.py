"""Validación compartida de componentes de ruta que vienen de fuera del código.

Tres sitios de este proyecto reciben un dato externo (un nombre de marca, el
slug de un post, un nombre de fichero de media) que usan para construir una
ruta en disco, y los tres necesitan la misma garantía: ese dato no puede
escapar de la carpeta que le corresponde. Antes de este módulo, el mismo
criterio vivía triplicado -``_validar_nombre_de_marca`` en ``brands.py``,
``_validar_slug`` en ``publisher.py``, y ``_validar_slug``/
``_validar_nombre_de_media`` en ``postfile.py``-, cada copia con su propia
clase de excepción y su propio mensaje.

La revisión final de la rama señaló que esa triplicación es deuda aceptable
mientras las copias sigan idénticas, pero que "idénticas hoy" no se sostiene
sola: los cuatro ``_mensaje_de_error`` de los adaptadores (ver
``socialctl/adapters/errores.py``) partieron también idénticos y divergieron
en silencio dentro de la misma rama. Aquí el coste de una divergencia
silenciosa sería peor que un mensaje feo: sería un agujero en el blindaje de
rutas. Por eso se extrae a un único punto.

Cada llamador sigue pudiendo lanzar SU PROPIA clase de excepción (con su
propio nombre y, si hace falta, su propia jerarquía -por ejemplo,
``postfile.SlugInvalido`` hereda de ``ValueError`` para que el CLI la
capture junto al resto de errores de post.yml, mientras que
``publisher.SlugInvalido`` no lo hace-): este módulo no impone una jerarquía
de excepciones, solo el criterio de validación y el texto del mensaje.

``validar_componente_de_ruta`` exige un ÚNICO componente (nada de '/'): eso
vale para un nombre de marca o un slug, que nunca tienen subcarpetas
legítimas. El nombre de un fichero de ``media`` en post.yml sí las tiene
-el usuario organiza su media en subcarpetas, p. ej. ``short/`` para
vídeos verticales-, así que necesita un criterio hermano, no el mismo: de
ahí ``validar_ruta_relativa``, más abajo. Se añade como función nueva en
vez de relajar ``validar_componente_de_ruta`` para que el nombre de marca y
el slug seguros hoy con "un solo componente, sin barras" no puedan, por un
descuido futuro, aceptar una barra que no deberían aceptar nunca -y para no
arriesgar los mensajes de error que ya comprueban los tests existentes de
esa función-. Comparten, aun así, la misma idea de fondo (forma del valor +
resolución de enlaces simbólicos contra la raíz), así que
``validar_ruta_relativa`` es una extensión deliberada del mismo criterio,
no uno inventado de cero.
"""

from __future__ import annotations

import os
from pathlib import Path


def validar_componente_de_ruta(
    raiz: Path,
    valor: object,
    excepcion: type[Exception],
    sustantivo: str,
) -> Path:
    """Valida ``valor`` como un único componente de ruta seguro dentro de ``raiz``.

    Devuelve ``raiz / valor`` si es válido. Lanza ``excepcion(mensaje)`` -con
    un mensaje que nombra el problema usando ``sustantivo`` (p. ej. "nombre
    de marca", "slug", "nombre de media")- si ``valor``:

    - no es una cadena de texto;
    - está vacío o solo tiene espacios;
    - es una ruta absoluta;
    - contiene separadores de ruta ('/' u ``os.sep``), o es '.' o '..';
    - o, tras resolver enlaces simbólicos, el resultado (``(raiz /
      valor).resolve()``) no queda como hijo directo de ``raiz.resolve()``.

    Este último paso es el que cierra la vía del enlace simbólico: un
    nombre puede pasar la comprobación por forma (sin separadores ni '..')
    y aun así apuntar fuera de ``raiz`` si es un symlink.
    """
    if not isinstance(valor, str):
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (debe ser una cadena de texto)"
        )

    if not valor or not valor.strip():
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (está vacío o solo tiene espacios)"
        )

    candidato = Path(valor)
    if candidato.is_absolute():
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (es una ruta absoluta; debe ser "
            "solo un nombre, sin separadores de ruta)"
        )

    if (
        "/" in valor
        or os.sep in valor
        or len(candidato.parts) != 1
        or valor in (".", "..")
    ):
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (no puede contener separadores "
            "de ruta ni ser '.' o '..')"
        )

    raiz_resuelta = raiz.resolve()
    resultado_resuelto = (raiz / valor).resolve()
    if resultado_resuelto.parent != raiz_resuelta:
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (resuelve fuera de {raiz}, "
            "probablemente por un enlace simbólico)"
        )

    return raiz / valor


def validar_ruta_relativa(
    raiz: Path,
    valor: object,
    excepcion: type[Exception],
    sustantivo: str,
) -> Path:
    """Valida ``valor`` como una ruta relativa segura dentro de ``raiz``, con subcarpetas.

    Hermana de ``validar_componente_de_ruta`` (mismo criterio de fondo,
    misma firma, mismo estilo de mensaje), pero para el único caso de este
    proyecto en el que un dato externo SÍ puede legítimamente tener
    subcarpetas: el nombre de un fichero de ``media`` en post.yml (p. ej.
    ``short/S2.mp4``, para el vídeo vertical que el usuario guarda en
    ``<Marca>/media/short/``). El separador es siempre ``'/'``, tanto si
    ``socialctl`` corre en macOS/Linux como en Windows: post.yml es un
    fichero de configuración, no una ruta del sistema operativo, así que no
    debe importar en qué máquina se ejecuta. Por eso un ``'\\'`` -que en
    Windows SÍ sería un separador de ruta- se rechaza siempre como
    carácter, en vez de admitirlo según el sistema operativo: admitirlo a
    veces abriría una vía de escape que solo existiría en Windows.

    Devuelve ``raiz.joinpath(*segmentos)`` si es válido (sin resolver
    enlaces simbólicos en el resultado: igual que
    ``validar_componente_de_ruta``, para no perder de vista el enlace en sí
    si apuntase DENTRO de ``raiz`` -un caso legítimo que no decide esta
    función-). Lanza ``excepcion(mensaje)`` si ``valor``:

    - no es una cadena de texto;
    - contiene un byte nulo (``'\\x00'``);
    - está vacío o solo tiene espacios;
    - contiene un ``'\\'`` (solo se admite ``'/'`` como separador);
    - es una ruta absoluta (empieza por ``'/'``);
    - tiene algún segmento vacío (p. ej. por una barra doble, `'a//b'`),
      compuesto solo de espacios, o igual a ``'.'`` o ``'..'`` -en
      CUALQUIER posición, no solo al principio: ``'a/../../b'`` se rechaza
      igual que ``'../a/b'``, sin necesidad de llegar a resolver la ruta
      para saber que es inválida;
    - o, tras resolver enlaces simbólicos, el resultado
      (``raiz.joinpath(*segmentos).resolve()``) no queda dentro de
      ``raiz.resolve()``. A diferencia de ``validar_componente_de_ruta``
      (que exige ser hijo DIRECTO de ``raiz``), aquí basta con quedar
      dentro a cualquier profundidad -ahora sí hay subcarpetas legítimas-,
      pero la comprobación sigue cerrando la vía del enlace simbólico en
      cualquier nivel intermedio (no solo en el último componente):
      ``short/enlace/S2.mp4`` se detecta igual que ``enlace/S2.mp4`` si
      ``short/enlace`` es un symlink que apunta fuera, porque
      ``.resolve()`` sigue TODOS los enlaces de la ruta.
    """
    if not isinstance(valor, str):
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (debe ser una cadena de texto)"
        )

    if "\x00" in valor:
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (no puede contener un byte nulo)"
        )

    if not valor or not valor.strip():
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (está vacío o solo tiene espacios)"
        )

    if "\\" in valor:
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (usa '/' como separador de "
            "subcarpetas; '\\' no está admitido)"
        )

    if valor.startswith("/"):
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (es una ruta absoluta; debe ser "
            "una ruta relativa dentro de la carpeta correspondiente)"
        )

    segmentos = valor.split("/")
    if any(not segmento.strip() or segmento in (".", "..") for segmento in segmentos):
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (ningún segmento puede estar "
            "vacío, ser solo espacios, ni ser '.' o '..')"
        )

    resultado = raiz.joinpath(*segmentos)

    raiz_resuelta = raiz.resolve()
    resultado_resuelto = resultado.resolve()
    if raiz_resuelta not in resultado_resuelto.parents:
        raise excepcion(
            f"{sustantivo} inválido: {valor!r} (resuelve fuera de {raiz}, "
            "probablemente por un enlace simbólico)"
        )

    return resultado
