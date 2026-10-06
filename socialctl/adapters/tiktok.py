"""Publicación en TikTok (Content Posting API v2).

Dos modos tras la misma interfaz, elegidos por ``tiktok.mode`` en
``accounts.yml``:

  inbox  -> ``POST .../inbox/video/init/`` sube el vídeo al buzón del
            creador; el usuario lo confirma en la app. Resultado:
            ``PENDIENTE_CONFIRMACION``.
  direct -> ``POST .../video/init/`` publica directamente. EXIGE que la
            app tenga la auditoría de TikTok aprobada. Resultado:
            ``PUBLICADO``.

En modo direct, ANTES de ``video/init/`` (y después de la salvaguarda de
auditoría) se llama a ``POST .../creator_info/query/``
(``_consultar_creator_info``, usada por
``TikTokAdapter._resolver_privacidad_y_duracion``). Confirmado con Context7
(Content Posting API Reference - Query Creator Info y - Direct Post): esa
consulta es obligatoria para saber qué opciones de ``privacy_level`` admite
la cuenta ("the chosen value must align with the privacy options returned
by the creator info query API") y cuál es su duración máxima de vídeo
propia (``max_video_post_duration_sec``, que puede ser MENOR que el límite
estático de ``PLATFORM_SPECS``). Si la cuenta no admite el nivel de
privacidad que este proyecto espera (``PUBLIC_TO_EVERYONE``) o el vídeo
supera esa duración máxima por creador, se aborta sin publicar -nunca se
publica con un nivel de privacidad distinto del esperado-.

Salvaguarda crítica (la razón de ser de este adaptador): si ``mode`` es
``direct`` y ``auditada`` no es ``true``, se aborta ANTES de llamar a la
API (``AuditoriaRequerida``, atrapada en ``_publicar`` y convertida en un
``PostResult`` de error). Publicar en directo sin auditoría deja el vídeo
en privado de forma PERMANENTE: aprobar la auditoría después no lo hace
público retroactivamente. Es un fallo silencioso e irreversible, así que
se trata como error duro con un mensaje que explica exactamente qué
hacer, en vez de dejar que la API lo rechace (o, peor, lo acepte y lo
publique privado sin más aviso).

Chunking real (Media Transfer Guide de TikTok, vigente a fecha de esta
implementación, verificado con Context7 contra la documentación oficial):

  - Cada chunk debe medir entre 5 MB y 64 MB, salvo el último, que puede
    llegar a 128 MB.
  - Los ficheros de menos de 5 MB (o, en general, de 64 MB o menos: un
    único chunk de hasta 64 MB ya es válido) se suben en una sola pieza
    (``total_chunk_count = 1``, ``chunk_size = video_size``).
  - Los ficheros de más de 64 MB exigen varios chunks.
  - Máximo 1000 chunks por subida, enviados en secuencia.

Esto NO es un límite de contenido (no depende de lo que el usuario quiera
publicar, sino de cómo transfiere los bytes la API de subida de TikTok),
así que vive aquí y no en ``PLATFORM_SPECS``/``formatter.py`` -que sí
reúne los límites de contenido de cada red (duración, aspect ratio,
tamaño máximo de fichero que TikTok acepta)-: ``_calcular_chunking``
decide el protocolo de transporte de un fichero que ya pasó esa
validación de contenido, nunca la sustituye ni la duplica.

Dato adicional confirmado con Context7: ``inbox/video/init/`` está
limitado a 6 peticiones por minuto y token de acceso. Este módulo no
implementa un limitador de tasa (ningún otro adaptador de este proyecto
lo hace tampoco); se deja constancia aquí para quien programe reintentos
o publicación en lote más arriba en la pila.

Ningún fallo de esta red puede abortar la publicación en las demás: por
diseño, ``TikTokAdapter.publish()`` jamás deja escapar una excepción. Todo
fallo previsto (de red, de E/S del fichero de vídeo, de formato de la
respuesta, o la propia salvaguarda de auditoría) se traduce en un
``PostResult(status=PostStatus.ERROR, ...)`` con un mensaje en español
que explica, de forma accionable, qué ha pasado. Como resguardo de
última instancia frente a cualquier fallo *no* previsto, ``publish()``
envuelve además todo el proceso en un ``except Exception`` genérico que
también devuelve un ``PostResult`` de error.

Riesgo de duplicar publicaciones: a diferencia de Instagram o Facebook
(donde el momento de riesgo es la respuesta de la llamada que confirma
la publicación), en TikTok el ``publish_id`` se valida ANTES de subir
ningún byte -no cuesta nada validarlo ahí, y así se elimina esa clase de
riesgo por completo, porque en ese punto TikTok solo ha reservado un id,
no ha recibido contenido-. El verdadero punto sin retorno es la subida
del ÚLTIMO chunk: un ``200``/``201``/``206`` en esa respuesta confirma
que TikTok ya tiene el vídeo completo (y, en modo ``direct``, puede
empezar a publicarlo). Si esa respuesta se pierde por un timeout o un
fallo de conexión -no un HTTP explícito, que TikTok documenta como
seguro de reintentar-, no hay forma de saber si TikTok llegó a recibirlo:
``_subir_chunks`` distingue ese caso concreto (solo para el último chunk),
avisa explícitamente de no reintentar sin comprobar antes la cuenta, Y
marca ``riesgo_duplicado=True`` en el ``PostResult`` resultante -el campo
estructurado (``socialctl/models.py``) que ``retry`` (``socialctl/cli.py``)
consulta para negarse a reintentar esta red en automático, en vez de
buscar la palabra "duplicar" dentro del mensaje.

Seguridad: el token viaja SOLO en la cabecera ``Authorization`` (nunca en
el cuerpo ni como parámetro de consulta, a diferencia de Facebook e
Instagram), así que ningún mensaje de error de este módulo interpola el
cuerpo o las cabeceras de la petición; el único texto libre que se
interpola procede del CUERPO DE LA RESPUESTA de TikTok (vía
``mensaje_de_error``, en ``socialctl/adapters/errores.py``) o de datos que
ya vienen de ``accounts.yml`` (nunca del token). El resguardo genérico de
``publish()`` tampoco interpola ``str(exc)``, solo el nombre del tipo de
excepción, por si una excepción realmente no prevista arrastrara la
petición que falló.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import httpx

from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.adapters.errores import mensaje_de_error
from socialctl.auth import obtener_token
from socialctl.brands import Brand
from socialctl.formatter import PLATFORM_SPECS, componer_caption
from socialctl.models import (
    MediaAsset,
    Platform,
    PlatformPost,
    PostResult,
    PostStatus,
    ValidationError,
)

API = "https://open.tiktokapis.com/v2/post/publish"

# --- Reglas de chunking del Media Transfer Guide de TikTok: protocolo de
# subida, no límite de contenido (ver docstring del módulo).
CHUNK_MIN_BYTES = 5 * 1024 * 1024
CHUNK_MAX_BYTES = 64 * 1024 * 1024
CHUNK_MAX_FINAL_BYTES = 128 * 1024 * 1024
TOTAL_CHUNKS_MAXIMO = 1000

# El nivel de privacidad que este proyecto espera al publicar en modo
# direct: contenido público. Ver docstring de
# TikTokAdapter._resolver_privacidad_y_duracion sobre por qué es un valor
# único hoy (no configurable por marca) y qué pasa si la cuenta no lo
# admite.
_PRIVACY_LEVEL_DESEADO = "PUBLIC_TO_EVERYONE"


_VALOR_AUDITADA_VERDADERO = "true"
_VALOR_AUDITADA_FALSO = "false"


def _interpretar_auditada(valor: object) -> tuple[bool, bool]:
    """Interpreta de forma estricta el valor bruto (ya deserializado por
    YAML) de ``auditada``. Devuelve ``(auditada, reconocido)``.

    ``auditada`` es ``True`` única y exclusivamente cuando ``valor`` es el
    booleano ``True`` o la cadena ``"true"`` (sin distinguir mayúsculas y
    tolerando espacios alrededor). Cualquier otra cosa -incluidos el
    booleano ``False``, ``None``/clave ausente, y las cadenas "false",
    "no", "si", "yes", "1"- es ``False``. Ante la duda, no se publica.

    NUNCA se usa ``bool()`` sobre este valor: es la causa del hallazgo
    crítico de la revisión de la Task 10. Si ``accounts.yml`` contiene
    ``auditada: "false"`` o ``auditada: "no"`` ENTRECOMILLADOS -un hábito
    muy común al escribir YAML a mano-, ``yaml.safe_load`` devuelve la
    CADENA ``"false"``/``"no"``, no un booleano, y ``bool("false")`` es
    ``True`` en Python (cualquier cadena no vacía es "verdadera" para
    ``bool()``). Con ``mode: direct``, eso hacía que el adaptador llamara
    a la API y publicara sin que la app tuviera la auditoría aprobada:
    exactamente el desastre irreversible que la salvaguarda existe para
    impedir (ver docstring del módulo y de ``AuditoriaRequerida``) -TikTok
    deja el vídeo en privado de forma PERMANENTE, y aprobar la auditoría
    después no lo rescata-. Por eso aquí se compara explícitamente contra
    las formas reconocidas, nunca la veracidad de Python del valor bruto.

    ``reconocido`` distingue dos motivos de "no auditada", para que quien
    configuró la cuenta entienda cuál tiene: ``True`` si ``valor`` es una
    de las formas canónicas esperadas (``True``, ``False``, ``None``, la
    clave ausente, o las cadenas "true"/"false"); ``False`` si ``valor``
    es algo que no encaja en ninguna forma reconocida (p. ej. "si", "yes",
    1, "1", "quizás") y probablemente sea un error de configuración, no
    una app deliberadamente no auditada. En ambos casos el resultado
    seguro (no publicar) es el mismo; solo cambia el mensaje que se
    muestra.
    """
    if valor is True:
        return True, True
    if valor is False or valor is None:
        return False, True
    if isinstance(valor, str):
        normalizado = valor.strip().lower()
        if normalizado == _VALOR_AUDITADA_VERDADERO:
            return True, True
        if normalizado == _VALOR_AUDITADA_FALSO:
            return False, True
        return False, False
    # Cualquier otro tipo (int, float, list, dict...) -incluidos 1 y 0- no
    # es una forma reconocida.
    return False, False


class AuditoriaRequerida(Exception):
    """``mode`` es ``direct`` pero la app no tiene la auditoría de TikTok aprobada.

    Publicar en directo sin auditoría deja el vídeo en privado de forma
    PERMANENTE: aprobar la auditoría después no lo rescata. Por eso esta
    excepción se levanta y se atrapa ANTES de llamar a la API -nunca tras
    un intento fallido-, para que la salvaguarda sea efectiva incluso si
    algún día alguien reordena el código de ``_publicar``.
    """


class ChunkingImposible(Exception):
    """El vídeo no cabe en el máximo de 1000 chunks que admite la subida de TikTok."""


def _calcular_chunking(tamano_bytes: int) -> tuple[int, int]:
    """Calcula ``(chunk_size, total_chunk_count)`` para un fichero de ``tamano_bytes``.

    Reglas (Media Transfer Guide de TikTok, ver docstring del módulo):
    cada chunk regular mide entre 5 MB y 64 MB; el último puede llegar a
    128 MB; los ficheros de 64 MB o menos se suben en una sola pieza;
    máximo 1000 chunks.

    El valor devuelto de ``chunk_size`` describe el tamaño de los chunks
    REGULARES (todos menos el último): el último chunk, en la práctica,
    puede medir más (hasta ``CHUNK_MAX_FINAL_BYTES``) y su longitud real
    se calcula en el momento de la subida (``tamano_bytes - chunk_size *
    (total_chunk_count - 1)``), nunca aquí. La única excepción es cuando
    ``total_chunk_count`` acaba siendo 1: en ese caso no hay chunks
    "regulares" de por medio, así que ``chunk_size`` debe ser igual al
    tamaño real del fichero -igual que en el ejemplo de "subida de una
    sola pieza" de la propia documentación de TikTok-, o TikTok
    rechazaría la subida por una discrepancia entre el ``chunk_size``
    declarado y los bytes realmente enviados.

    Reequilibrado (hallazgo 2 de la revisión de la Task 10): para
    ficheros de más de 64 MB, en vez de fijar ``chunk_size`` en
    ``CHUNK_MAX_BYTES`` y calcular cuántos chunks hacen falta, se calcula
    primero cuántos chunks hacen falta (``total_chunk_count``) y LUEGO se
    reparte ``tamano_bytes`` en esa cantidad de partes iguales
    (redondeando hacia arriba). La implementación anterior fijaba
    ``chunk_size`` y dejaba que el último chunk absorbiera el resto de la
    división entera; para ficheros de entre 64 MB + 1 byte y unos 69 MB,
    ese resto quedaba por debajo del mínimo de 5 MB, y la implementación
    colapsaba entonces a un solo chunk con el fichero entero -incumpliendo
    la regla, que el propio módulo afirma cumplir, de que "los ficheros de
    más de 64 MB requieren múltiples chunks" (Media Transfer Guide de
    TikTok, confirmado con Context7)-.

    Con el reparto equilibrado, para cualquier ``tamano_bytes`` que caiga
    en esta rama (estrictamente mayor que ``CHUNK_MAX_BYTES``):

    - ``total_chunk_count = ceil(tamano_bytes / CHUNK_MAX_BYTES) >= 2``
      SIEMPRE (nunca hace falta reducirlo después: un fichero de más de
      64 MB nunca puede necesitar menos de 2 chunks con este cálculo), lo
      que ya garantiza por construcción "más de 64 MB implica varios
      chunks", sin necesidad de un caso especial aparte.
    - ``chunk_size = ceil(tamano_bytes / total_chunk_count)``. Por cómo
      se eligió ``total_chunk_count``, ``tamano_bytes / total_chunk_count
      <= CHUNK_MAX_BYTES`` (con números reales), así que su redondeo
      hacia arriba tampoco puede superar ``CHUNK_MAX_BYTES`` (redondear
      hacia arriba un número que ya es <= a un entero no lo supera). El
      caso más ajustado posible es ``total_chunk_count == 2`` con
      ``tamano_bytes`` justo por encima de ``CHUNK_MAX_BYTES``, que deja
      ``chunk_size`` en, como mínimo, la mitad de ``CHUNK_MAX_BYTES``
      (32 MB): muy por encima del mínimo de 5 MB, así que ``chunk_size``
      nunca puede quedar por debajo de él.
    - El último chunk (``tamano_bytes - chunk_size * (total_chunk_count -
      1)``) nunca supera a ``chunk_size`` -de la misma desigualdad de
      arriba se deduce que es, como mucho, igual- y siempre es
      estrictamente positivo, así que también respeta el límite de 128 MB
      (con margen de sobra) y nunca deja huecos ni solapes: todos los
      chunks suman, exactamente, ``tamano_bytes``.

    Verificado por aritmética (no solo por lectura del código) con un
    barrido de varios millones de tamaños -incluyendo, byte a byte, la
    ventana de 64 a 69 MB donde colapsaba la implementación anterior, los
    múltiplos exactos de 64 MB, y el entorno del límite de 1000 chunks-;
    ver también el barrido equivalente en
    ``tests/test_adapter_tiktok.py``.
    """
    if tamano_bytes <= CHUNK_MAX_BYTES:
        # Cubre tanto "menos de 5 MB" como "entre 5 y 64 MB": en ambos
        # casos un único chunk que mida el fichero entero ya respeta las
        # reglas (es, como mucho, de 64 MB, y como pieza única no está
        # sujeto al mínimo de 5 MB que sí aplica a un chunk regular).
        return tamano_bytes, 1

    # Ceiling division entera (sin pasar por floats, para no arriesgar
    # ninguna imprecisión de coma flotante con ficheros de decenas de GB).
    total_chunk_count = -(-tamano_bytes // CHUNK_MAX_BYTES)

    if total_chunk_count > TOTAL_CHUNKS_MAXIMO:
        raise ChunkingImposible(
            f"el vídeo pesa {tamano_bytes / 1024**3:.2f} GB y no cabe en el "
            f"máximo de {TOTAL_CHUNKS_MAXIMO} chunks que admite la subida de "
            f"TikTok (harían falta {total_chunk_count}); no se puede subir "
            "con este mecanismo."
        )

    chunk_size = -(-tamano_bytes // total_chunk_count)

    return chunk_size, total_chunk_count


class _ErrorDeSubida(NamedTuple):
    """Un chunk (o el fichero) ha fallado al subir: el mensaje accionable y
    si ese fallo concreto conlleva riesgo de publicación duplicada.

    ``riesgo_duplicado`` solo es ``True`` para el caso ambiguo del ÚLTIMO
    chunk descrito en el docstring del módulo: un timeout o un fallo de
    conexión ahí no permite saber si TikTok llegó a recibir el vídeo
    completo. El resto de fallos de este ayudante (E/S local, un chunk que
    no es el último, un HTTP explícito) son siempre seguros de reintentar y
    dejan el valor por defecto, ``False``.
    """

    mensaje: str
    riesgo_duplicado: bool = False


def _subir_chunks(
    client: httpx.Client,
    upload_url: str,
    path: Path,
    tamano_bytes: int,
    chunk_size: int,
    total_chunk_count: int,
) -> _ErrorDeSubida | None:
    """Sube ``path`` a ``upload_url`` en chunks secuenciales.

    No carga el fichero entero en memoria: abre el fichero una sola vez
    y, para cada chunk, hace ``seek`` + ``read`` solo de esos bytes antes
    de enviarlos -este proyecto publica documentales que pueden pesar
    varios GB-.

    Devuelve ``None`` si todos los chunks se han subido bien, o un
    ``_ErrorDeSubida`` (mensaje que identifica en cuál ha fallado, y de
    cuántos, más si conlleva riesgo de duplicado) si alguno no se pudo
    subir. Nunca lanza: cualquier fallo de E/S o de red se traduce en el
    valor devuelto, nunca en una excepción -este ayudante lo usa
    ``_publicar``, que ya está fuera del resguardo genérico de
    ``publish()`` y necesita un mensaje específico, no un "ha ocurrido un
    error inesperado" opaco-.

    Un timeout o un fallo de conexión al subir el ÚLTIMO chunk es el
    único caso realmente ambiguo (ver docstring del módulo): no hay
    forma de saber si TikTok llegó a recibirlo antes de que la conexión
    se perdiera, así que ese mensaje concreto advierte de no reintentar
    sin comprobar antes la cuenta y marca ``riesgo_duplicado=True``. Un
    HTTP explícito (4xx/5xx) no es ambiguo -TikTok documenta el 5xx como
    "retry recomendado"-, así que no lleva ese aviso.
    """
    try:
        with path.open("rb") as fichero:
            inicio = 0
            for numero in range(total_chunk_count):
                es_ultimo = numero == total_chunk_count - 1
                fin = (tamano_bytes - 1) if es_ultimo else (inicio + chunk_size - 1)
                longitud = fin - inicio + 1

                fichero.seek(inicio)
                trozo = fichero.read(longitud)
                if len(trozo) != longitud:
                    return _ErrorDeSubida(
                        "el archivo de vídeo cambió de tamaño durante la subida "
                        f"(el chunk {numero + 1}/{total_chunk_count} esperaba "
                        f"{longitud} bytes y solo se han podido leer {len(trozo)})"
                    )

                try:
                    respuesta = client.put(
                        upload_url,
                        content=trozo,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(longitud),
                            "Content-Range": f"bytes {inicio}-{fin}/{tamano_bytes}",
                        },
                        timeout=None,
                    )
                except httpx.InvalidURL:
                    # upload_url la devuelve TikTok en la respuesta de init:
                    # si viniera mal formada, httpx la rechaza antes de
                    # tocar la red. No hereda de httpx.HTTPError.
                    return _ErrorDeSubida("TikTok ha devuelto una URL de subida no válida")
                except httpx.TimeoutException:
                    # Subclase de httpx.HTTPError: debe ir antes que ese
                    # except para no quedar inalcanzable.
                    if es_ultimo:
                        return _ErrorDeSubida(
                            "se agotó el tiempo de espera subiendo el último "
                            f"chunk ({numero + 1}/{total_chunk_count}) a "
                            "TikTok: no se puede confirmar si TikTok llegó a "
                            "recibirlo y empezar a publicar el vídeo. NO "
                            "reintentes la subida sin comprobar antes la "
                            "cuenta, para no arriesgarte a duplicar la "
                            "publicación.",
                            riesgo_duplicado=True,
                        )
                    return _ErrorDeSubida(
                        "se agotó el tiempo de espera subiendo el chunk "
                        f"{numero + 1}/{total_chunk_count} a TikTok"
                    )
                except httpx.HTTPError:
                    if es_ultimo:
                        return _ErrorDeSubida(
                            "no se pudo conectar con TikTok al subir el "
                            f"último chunk ({numero + 1}/{total_chunk_count}): "
                            "no se puede confirmar si TikTok llegó a "
                            "recibirlo y empezar a publicar el vídeo. NO "
                            "reintentes la subida sin comprobar antes la "
                            "cuenta, para no arriesgarte a duplicar la "
                            "publicación.",
                            riesgo_duplicado=True,
                        )
                    return _ErrorDeSubida(
                        "no se pudo conectar con TikTok para subir el chunk "
                        f"{numero + 1}/{total_chunk_count}"
                    )

                if respuesta.status_code not in (200, 201, 206):
                    return _ErrorDeSubida(
                        f"TikTok rechazó el chunk {numero + 1}/{total_chunk_count} "
                        f"de la subida (HTTP {respuesta.status_code})"
                    )

                inicio = fin + 1
    except FileNotFoundError:
        return _ErrorDeSubida(f"el archivo de vídeo ya no existe en disco: {path}")
    except IsADirectoryError:
        return _ErrorDeSubida(
            f"la ruta del vídeo apunta a un directorio, no a un archivo: {path}"
        )
    except PermissionError:
        return _ErrorDeSubida(f"no hay permisos de lectura sobre el archivo de vídeo: {path}")
    except OSError as exc:
        # Cualquier otro fallo de E/S al leer el fichero (disco dañado,
        # demasiados descriptores abiertos, etc.).
        return _ErrorDeSubida(f"no se pudo leer el archivo de vídeo en disco ({path}): {exc}")

    return None


def _consultar_creator_info(
    client: httpx.Client, token: str
) -> tuple[dict | None, str | None]:
    """Llama a ``POST /v2/post/publish/creator_info/query/``.

    Devuelve ``(datos, None)`` con el objeto ``data`` de la respuesta si
    todo fue bien, o ``(None, mensaje)`` si hay que abortar. Nunca lanza:
    quien la llama (``TikTokAdapter._resolver_privacidad_y_duracion``) ya
    está fuera del resguardo genérico de ``publish()`` y necesita un
    mensaje específico, no un "ha ocurrido un error inesperado" opaco.

    Confirmado con Context7 (content-posting-api-reference-query-creator-info):
    la petición no lleva cuerpo, solo las cabeceras ``Authorization`` y
    ``Content-Type``; la respuesta trae ``privacy_level_options``,
    ``comment_disabled``, ``duet_disabled``, ``stitch_disabled`` y
    ``max_video_post_duration_sec`` dentro de ``data``. El token viaja solo
    en la cabecera ``Authorization`` (igual que el resto del módulo), así
    que ningún mensaje de aquí puede arrastrarlo.
    """
    try:
        respuesta = client.post(
            f"{API}/creator_info/query/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
    except httpx.TimeoutException:
        return None, "se agotó el tiempo de espera consultando creator_info en TikTok"
    except httpx.HTTPError:
        return None, "no se pudo conectar con TikTok para consultar creator_info"

    if respuesta.status_code != 200:
        return None, mensaje_de_error(respuesta, token)

    try:
        cuerpo = respuesta.json()
    except json.JSONDecodeError:
        return None, "TikTok respondió con un cuerpo que no es JSON válido"

    datos = cuerpo.get("data") if isinstance(cuerpo, dict) else None
    if not isinstance(datos, dict):
        return None, "TikTok respondió sin datos de creator_info (falta el campo 'data')"

    return datos, None


class TikTokAdapter(Adapter):
    """Publica en TikTok mediante la Content Posting API v2, en modo inbox o direct."""

    platform = Platform.TIKTOK

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        """Añade, a los del base, los problemas de configuración de cuenta
        Y la salvaguarda de auditoría.

        Hallazgo de revisión (I4): con ``mode: direct`` y ``auditada:
        false``, el preview decía "Problemas detectados: 0" y el usuario
        aprobaba una publicación que el sistema ya sabía que iba a
        rechazar -la salvaguarda de auditoría (ver
        ``_comprobar_auditoria``) solo se comprobaba dentro de
        ``_publicar()``, DESPUÉS de la aprobación-. Ninguna de las
        comprobaciones de este método hace red (leen ``brand.cuentas``, ya
        cargado en memoria; `_comprobar_auditoria` tampoco), así que
        encajan en `validate()` sin romper su contrato ("No hace red"):
        la consulta a ``creator_info/query/`` SÍ hace red (confirma qué
        `privacy_level` admite la cuenta) y por eso se queda,
        deliberadamente, fuera de aquí y solo dentro de `_publicar()`.

        La salvaguarda se mantiene TAMBIÉN en `_publicar` (no se quita de
        ahí, y sigue siendo la que de verdad impide publicar): `publish()`
        no puede confiar en que alguien haya llamado a `validate()` antes.
        """
        errores = super().validate(post, brand)
        cuenta = brand.cuentas.get("tiktok") or {}
        open_id = cuenta.get("open_id")
        modo = cuenta.get("mode", "inbox")
        # No se convierte con bool(): ver docstring de _interpretar_auditada.
        valor_auditada = cuenta.get("auditada")

        if not open_id:
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=f"falta open_id en {brand.raiz / 'accounts.yml'}",
                )
            )

        if modo not in ("inbox", "direct"):
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=(
                        f"modo de TikTok desconocido: '{modo}' en "
                        f"{brand.raiz / 'accounts.yml'}. Valores válidos: "
                        "inbox, direct"
                    ),
                )
            )

        try:
            self._comprobar_auditoria(brand, modo, valor_auditada)
        except AuditoriaRequerida as exc:
            errores.append(
                ValidationError(
                    platform=self.platform, campo="auditoria", motivo=str(exc)
                )
            )

        return errores

    def publish(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Publica el post. Nunca lanza: cualquier fallo vuelve como ``PostResult`` de error.

        Todos los pasos concretos viven en ``_publicar()``. Este método es
        solo el resguardo de última instancia: si ``_publicar()`` deja
        escapar una excepción que nadie previó, aquí se atrapa igualmente,
        para que un fallo de TikTok nunca pueda abortar la publicación en
        las demás redes.

        El mensaje de este resguardo genérico NUNCA interpola ``str(exc)``:
        solo el nombre del tipo de excepción, por si una excepción
        verdaderamente no prevista arrastrara en su propio mensaje la
        petición que falló (y, con ella, el token).
        """
        spec = PLATFORM_SPECS[self.platform]
        if len(post.media) > spec.max_media:
            return self._error(
                f"esta ruta admite como máximo {spec.max_media} archivo; "
                "no publica carruseles"
            )

        try:
            return self._publicar(post, brand, client)
        except Exception as exc:
            return self._error(
                f"ha ocurrido un error inesperado en TikTok ({type(exc).__name__})"
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Cuerpo real de la publicación, con manejo específico de cada fallo previsto."""
        cuenta = brand.cuentas.get("tiktok") or {}
        open_id = cuenta.get("open_id")
        modo = cuenta.get("mode", "inbox")
        # No se convierte con bool(): ver docstring de _interpretar_auditada.
        valor_auditada = cuenta.get("auditada")

        # La Content Posting API v2 no exige open_id en el cuerpo de
        # init/subida (el access_token ya identifica a la cuenta), pero su
        # ausencia en accounts.yml es señal de una cuenta a medio
        # configurar -igual que page_id en facebook.py o ig_user_id en
        # instagram.py-, así que se valida aquí aunque no viaje en ninguna
        # petición de este módulo.
        if not open_id:
            return self._error(f"falta open_id en {brand.raiz / 'accounts.yml'}")

        if modo not in ("inbox", "direct"):
            return self._error(
                f"modo de TikTok desconocido: '{modo}' en {brand.raiz / 'accounts.yml'}. "
                "Valores válidos: inbox, direct"
            )

        try:
            self._comprobar_auditoria(brand, modo, valor_auditada)
        except AuditoriaRequerida as exc:
            return self._error(str(exc))

        if not post.media:
            return self._error(
                "no hay ningún vídeo que publicar: el post no tiene media adjunta"
            )
        asset = post.media[0]

        try:
            token = obtener_token(brand, self.platform, client)
        except Exception as exc:
            return self._error(str(exc))

        # En modo direct, privacy_level debe coincidir con una opción que
        # ESTA cuenta admita de verdad (ver _resolver_privacidad_y_duracion).
        # La llamada queda DESPUÉS de la salvaguarda de auditoría (ya
        # aplicada más arriba) y ANTES de video/init/: si la cuenta no
        # admite el nivel que este proyecto espera, se aborta aquí, sin
        # haber gastado ninguna llamada de subida.
        privacy_level = None
        if modo == "direct":
            privacy_level, error_creador = self._resolver_privacidad_y_duracion(
                token, asset, client
            )
            if error_creador is not None:
                return error_creador

        try:
            tamano_bytes = asset.path.stat().st_size
        except FileNotFoundError:
            return self._error(f"el archivo de vídeo ya no existe en disco: {asset.path}")
        except OSError as exc:
            return self._error(
                f"no se pudo leer el archivo de vídeo en disco ({asset.path}): {exc}"
            )

        try:
            chunk_size, total_chunk_count = _calcular_chunking(tamano_bytes)
        except ChunkingImposible as exc:
            return self._error(str(exc))

        titulo = componer_caption(post.body, post.hashtags)

        cuerpo: dict = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": tamano_bytes,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            }
        }
        if modo == "direct":
            endpoint = f"{API}/video/init/"
            cuerpo["post_info"] = {
                "title": titulo,
                # Confirmado más arriba contra creator_info/query/: no un
                # valor fijo sin consultar (ver _resolver_privacidad_y_duracion).
                "privacy_level": privacy_level,
            }
        else:
            endpoint = f"{API}/inbox/video/init/"

        try:
            inicio = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {token}"},
                json=cuerpo,
            )
        except httpx.TimeoutException:
            return self._error(
                "se agotó el tiempo de espera al contactar con TikTok para "
                "iniciar la subida"
            )
        except httpx.HTTPError:
            return self._error("no se pudo conectar con TikTok para iniciar la subida")

        if inicio.status_code != 200:
            return self._error(mensaje_de_error(inicio, token))

        try:
            cuerpo_respuesta = inicio.json()
        except json.JSONDecodeError:
            return self._error(
                "TikTok respondió con un cuerpo que no es JSON válido al "
                "iniciar la subida"
            )

        datos = cuerpo_respuesta.get("data") if isinstance(cuerpo_respuesta, dict) else None
        if not isinstance(datos, dict):
            return self._error(
                "TikTok respondió sin datos de subida (falta el campo 'data')"
            )

        upload_url = datos.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            return self._error("TikTok no devolvió una URL de subida válida")

        publish_id = datos.get("publish_id")
        # Se valida el tipo AQUÍ, antes de subir ningún byte: en este punto
        # TikTok solo ha reservado un id de seguimiento, no ha recibido
        # contenido, así que fallar ahora (en vez de después de subir) es
        # siempre seguro de reintentar -ver el aviso de duplicados en el
        # docstring del módulo-.
        if publish_id is not None and not isinstance(publish_id, str):
            return self._error(
                "TikTok respondió con un publish_id inesperado (se esperaba "
                f"una cadena de texto y llegó {type(publish_id).__name__})"
            )

        error_subida = _subir_chunks(
            client, upload_url, asset.path, tamano_bytes, chunk_size, total_chunk_count
        )
        if error_subida is not None:
            return self._error(
                error_subida.mensaje, riesgo_duplicado=error_subida.riesgo_duplicado
            )

        if modo == "direct":
            return PostResult(
                platform=self.platform,
                status=PostStatus.PUBLICADO,
                platform_id=publish_id,
            )

        return PostResult(
            platform=self.platform,
            status=PostStatus.PENDIENTE_CONFIRMACION,
            platform_id=publish_id,
            error="pendiente: abre la app de TikTok y confirma la publicación",
        )

    def _resolver_privacidad_y_duracion(
        self, token: str, asset: MediaAsset, client: httpx.Client
    ) -> tuple[str | None, PostResult | None]:
        """Solo en modo direct: consulta ``creator_info/query/`` y confirma
        que ESTA cuenta admite el nivel de privacidad que este proyecto
        espera, y que el vídeo no supera la duración máxima de ESTE
        creador. Devuelve ``(privacy_level, None)`` si se puede continuar, o
        ``(None, PostResult)`` si hay que abortar sin publicar.

        Obligatoria en modo direct (Content Posting API Reference - Query
        Creator Info, confirmado con Context7): "Applications must invoke
        this API when rendering the Export to TikTok page to display the
        correct account information, available privacy level options, and
        interaction settings" -y "Body > post_info > privacy_level" (misma
        doc): "the chosen value must align with the privacy options
        returned by the creator info query API". No todas las cuentas
        admiten ``PUBLIC_TO_EVERYONE`` (una cuenta privada o de menor, por
        ejemplo, no lo admite): publicar con un nivel no admitido lo
        rechazaría la API, y publicar con un nivel DISTINTO al que este
        proyecto espera (p. ej. degradar en silencio a
        ``SELF_ONLY``) sería peor -contenido que el usuario cree público y
        no lo es-. Por eso, si el nivel esperado no está entre las opciones
        de esta cuenta, se aborta sin publicar en vez de elegir otro nivel
        por su cuenta.

        Este proyecto siempre espera publicar como contenido público
        (``PUBLIC_TO_EVERYONE``, ``_PRIVACY_LEVEL_DESEADO``): no hay hoy
        ninguna opción en ``accounts.yml`` para pedir otra cosa. Si algún
        día una marca necesitara un nivel distinto, esa preferencia debería
        vivir en ``accounts.yml`` (decisión editorial de la marca), nunca
        fijada aquí; hasta que exista esa necesidad real, mantener un único
        valor esperado es más simple y no hay ninguna marca configurada hoy
        que lo necesite.

        La misma consulta devuelve ``max_video_post_duration_sec``: la
        duración máxima que ESTE creador admite, que puede ser MENOR que el
        límite estático de ``PLATFORM_SPECS`` (p. ej. el ejemplo oficial de
        la propia doc de TikTok devuelve 300s, no los 600s que asume este
        proyecto). Es un valor dinámico por creador, no un límite de
        contenido universal, así que se comprueba aquí -junto al resto de
        la respuesta de esta misma llamada- y no en ``PLATFORM_SPECS``.

        Como cualquier llamada de red de este módulo, nunca lanza: cualquier
        fallo (de red, de formato de la respuesta, o de que la cuenta no
        admita lo que se necesita) se traduce en el ``PostResult`` de error
        devuelto, nunca en una excepción -esta llamada queda dentro del
        ``except Exception`` genérico de ``publish()`` solo como resguardo
        de última instancia, igual que el resto del módulo-.
        """
        datos, error_creador = _consultar_creator_info(client, token)
        if error_creador is not None:
            return None, self._error(
                f"no se pudo consultar creator_info en TikTok (obligatorio "
                f"en mode: direct antes de publicar): {error_creador}"
            )

        opciones = datos.get("privacy_level_options")
        if not isinstance(opciones, list) or not all(
            isinstance(o, str) for o in opciones
        ):
            return None, self._error(
                "TikTok respondió sin una lista válida de "
                "privacy_level_options en creator_info: no se puede "
                "confirmar qué nivel de privacidad admite esta cuenta"
            )

        if _PRIVACY_LEVEL_DESEADO not in opciones:
            return None, self._error(
                "la cuenta de TikTok no admite publicar con "
                f"privacy_level={_PRIVACY_LEVEL_DESEADO!r} (el nivel que "
                "este proyecto espera para mode: direct); las opciones que "
                f"sí admite esta cuenta son {opciones!r}. Es probable que "
                "sea una cuenta privada o de un menor: revisa la "
                "configuración de privacidad de la cuenta en la app de "
                "TikTok, o usa mode: inbox si prefieres confirmar la "
                "publicación manualmente. No se publica con un nivel de "
                "privacidad distinto del esperado."
            )

        maximo = datos.get("max_video_post_duration_sec")
        duracion = asset.duration_s
        if (
            isinstance(maximo, (int, float))
            and duracion is not None
            and duracion > maximo
        ):
            return None, self._error(
                f"el vídeo dura {duracion:.0f}s y supera los {maximo:.0f}s "
                "que esta cuenta admite como máximo para un post directo "
                "(max_video_post_duration_sec de creator_info, un límite "
                "por creador que puede ser menor que el límite general de "
                "TikTok); usa mode: inbox o recorta el vídeo."
            )

        return _PRIVACY_LEVEL_DESEADO, None

    def _comprobar_auditoria(self, brand: Brand, modo: str, valor_auditada: object) -> None:
        """Aplica la salvaguarda de auditoría. Ver docstring del módulo, de
        ``_interpretar_auditada`` y de ``AuditoriaRequerida``."""
        if modo != "direct":
            return

        auditada, reconocido = _interpretar_auditada(valor_auditada)
        if auditada:
            return

        ruta = brand.raiz / "accounts.yml"
        if reconocido:
            raise AuditoriaRequerida(
                f"tiktok.mode es 'direct' pero la app no está auditada "
                f"(auditada: {valor_auditada!r} en {ruta}). Publicar así "
                "dejaría el vídeo en privado de forma PERMANENTE: aprobar "
                "la auditoría después no lo rescata. Cambia a mode: inbox, "
                "o espera a que TikTok apruebe la auditoría de tu app y "
                "entonces pon auditada: true."
            )
        # Valor no reconocido (ni true/false canónico, ni booleano, ni
        # ausente): mensaje distinto para que quien configuró la cuenta
        # note el error de tecleo, en vez de creer que ya dejó la cuenta
        # marcada como "no auditada" a propósito.
        raise AuditoriaRequerida(
            f"tiktok.mode es 'direct' pero el valor de 'auditada' en {ruta} "
            f"no se reconoce ({valor_auditada!r}): se trata como NO "
            "auditada por seguridad, para no arriesgarse a dejar el vídeo "
            "en privado de forma PERMANENTE (aprobar la auditoría después "
            "no lo rescata). Si la auditoría de tu app ya está aprobada, "
            "escribe literalmente auditada: true (el booleano, sin "
            "comillas) en accounts.yml."
        )

    def _error(self, mensaje: str, *, riesgo_duplicado: bool = False) -> PostResult:
        return PostResult(
            platform=self.platform,
            status=PostStatus.ERROR,
            error=mensaje,
            riesgo_duplicado=riesgo_duplicado,
        )


ADAPTADORES[Platform.TIKTOK] = TikTokAdapter
