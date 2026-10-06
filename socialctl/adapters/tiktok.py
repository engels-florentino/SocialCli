"TikTok Content Posting API v2 with inbox and audited direct modes.\n\nInbox sends a draft for the creator to finish in TikTok; it does not report a\npublic post. Direct mode is blocked unless auditada is explicitly true. Before\ndirect publication, query creator_info and validate the supported privacy level\nand creator-specific duration limit. This implementation fixes direct privacy\nto PUBLIC_TO_EVERYONE; additional public-product UX controls remain incomplete.\n\nFILE_UPLOAD streams sequential chunks: 5–64 MB except a final chunk up to\n128 MB, at most 1000 chunks. Small files use one upload. A lost final-chunk\nresponse may mean acceptance; mark duplicate risk and require remote verification\nbefore retrying. Credentials travel in Authorization headers and are never\nincluded in diagnostic output. Upload success and publication confirmation are\nseparate states. Audit approval does not automatically change an earlier post's\nprivacy settings."

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
    'Accept only bool True or the canonical text true as evidence of configured audit.\n\nFalse, None, empty text and canonical false are unaudited. Unknown values such\nas numeric 1 or noncanonical strings return None so callers can fail closed.\nThe configuration value never establishes actual provider approval by itself.'
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
    'Direct mode requires an approved TikTok application audit.'


class ChunkingImposible(Exception):
    'The video exceeds the 1000-chunk maximum supported by TikTok uploads.'


def _calcular_chunking(tamano_bytes: int) -> tuple[int, int]:
    'Return chunk_size and total_chunk_count for a file of tamano_bytes.\n\nUse one chunk for up to 64 MB; use sequential 64 MB chunks for larger files.\nEnforce TikTok minimum, maximum final-chunk size and 1000-chunk limit. The return\nvalue describes transfer protocol only, not content validation.'
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
            f"the video is {tamano_bytes / 1024**3:.2f} GB and exceeds the "
            f"maximum of {TOTAL_CHUNKS_MAXIMO} chunks supported by "
            f"TikTok uploads (would require {total_chunk_count}); cannot upload "
            'using this mechanism.'
        )

    chunk_size = -(-tamano_bytes // total_chunk_count)

    return chunk_size, total_chunk_count


class _ErrorDeSubida(NamedTuple):
    'A file or chunk upload failed, with actionable text and duplicate-risk state.'

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
    'Stream original file bytes to upload_url in sequential chunks.\n\nDetect changes in file size, I/O failures, invalid URLs and provider rejection.\nLost responses to the final chunk carry duplicate risk: TikTok may have received\nthe complete file. Return _ErrorDeSubida without exposing credentials.'
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
                        'the video file changed size during the upload '
                        f"(chunk {numero + 1}/{total_chunk_count} expected "
                        f"{longitud} bytes, but only read {len(trozo)})"
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
                    return _ErrorDeSubida('TikTok returned an invalid upload URL')
                except httpx.TimeoutException:
                    # Subclase de httpx.HTTPError: debe ir antes que ese
                    # except para no quedar inalcanzable.
                    if es_ultimo:
                        return _ErrorDeSubida(
                            'timed out uploading the final '
                            f"chunk ({numero + 1}/{total_chunk_count}) to "
                            'TikTok: cannot confirm whether TikTok '
                            'received it and started publishing the video. Do NOT '
                            'retry the upload without checking the '
                            'account first, to avoid duplicating the '
                            'post.',
                            riesgo_duplicado=True,
                        )
                    return _ErrorDeSubida(
                        'timed out uploading chunk '
                        f"{numero + 1}/{total_chunk_count} to TikTok"
                    )
                except httpx.HTTPError:
                    if es_ultimo:
                        return _ErrorDeSubida(
                            'could not connect to TikTok when uploading the '
                            f"final chunk ({numero + 1}/{total_chunk_count}): "
                            'cannot confirm whether TikTok '
                            'received it and started publishing the video. Do NOT '
                            'retry the upload without checking the '
                            'account first, to avoid duplicating the '
                            'post.',
                            riesgo_duplicado=True,
                        )
                    return _ErrorDeSubida(
                        'could not connect to TikTok to upload chunk '
                        f"{numero + 1}/{total_chunk_count}"
                    )

                if respuesta.status_code not in (200, 201, 206):
                    return _ErrorDeSubida(
                        f"TikTok rejected chunk {numero + 1}/{total_chunk_count} "
                        f"of the upload (HTTP {respuesta.status_code})"
                    )

                inicio = fin + 1
    except FileNotFoundError:
        return _ErrorDeSubida(f"the video file no longer exists on disk: {path}")
    except IsADirectoryError:
        return _ErrorDeSubida(
            f"the video path points to a directory, not a file: {path}"
        )
    except PermissionError:
        return _ErrorDeSubida(f"the video file is not readable: {path}")
    except OSError as exc:
        # Cualquier otro fallo de E/S al leer el fichero (disco dañado,
        # demasiados descriptores abiertos, etc.).
        return _ErrorDeSubida(f"could not read the video file from disk ({path}): {exc}")

    return None


def _consultar_creator_info(
    client: httpx.Client, token: str
) -> tuple[dict | None, str | None]:
    'Query /v2/post/publish/creator_info/query/ with the authenticated token.\n\nReturn creator data or a diagnostic error; this helper performs no publication.\nCallers validate privacy_level_options and max_video_post_duration_sec.'
    try:
        respuesta = client.post(
            f"{API}/creator_info/query/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
        )
    except httpx.TimeoutException:
        return None, 'timed out querying creator_info on TikTok'
    except httpx.HTTPError:
        return None, 'could not connect to TikTok to query creator_info'

    if respuesta.status_code != 200:
        return None, mensaje_de_error(respuesta, token)

    try:
        cuerpo = respuesta.json()
    except json.JSONDecodeError:
        return None, 'TikTok returned an invalid JSON response'

    datos = cuerpo.get("data") if isinstance(cuerpo, dict) else None
    if not isinstance(datos, dict):
        return None, "TikTok returned no creator_info data (missing 'data' field)"

    return datos, None


class TikTokAdapter(Adapter):
    'Publish to TikTok through Content Posting API v2 in inbox or direct mode.'

    platform = Platform.TIKTOK

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        'Add TikTok account, upload mode and audit checks without network access.'
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
                    motivo=f"missing open_id in {brand.raiz / 'accounts.yml'}",
                )
            )

        if modo not in ("inbox", "direct"):
            errores.append(
                ValidationError(
                    platform=self.platform,
                    campo="cuenta",
                    motivo=(
                        f"unknown TikTok mode: '{modo}' in "
                        f"{brand.raiz / 'accounts.yml'}. Valid values: "
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
        'Publish a validated post; convert every failure to an error PostResult.'
        spec = PLATFORM_SPECS[self.platform]
        if len(post.media) > spec.max_media:
            return self._error(
                f"this endpoint supports at most {spec.max_media} file; "
                'it does not publish carousels'
            )

        try:
            return self._publicar(post, brand, client)
        except Exception as exc:
            return self._error(
                f"an unexpected TikTok error occurred ({type(exc).__name__})"
            )

    def _publicar(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        'Execute publication with explicit handling of expected failures.'
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
            return self._error(f"missing open_id in {brand.raiz / 'accounts.yml'}")

        if modo not in ("inbox", "direct"):
            return self._error(
                f"unknown TikTok mode: '{modo}' in {brand.raiz / 'accounts.yml'}. "
                'Valid values: inbox, direct'
            )

        try:
            self._comprobar_auditoria(brand, modo, valor_auditada)
        except AuditoriaRequerida as exc:
            return self._error(str(exc))

        if not post.media:
            return self._error(
                'no video to publish: the post has no media attached'
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
            return self._error(f"the video file no longer exists on disk: {asset.path}")
        except OSError as exc:
            return self._error(
                f"could not read the video file from disk ({asset.path}): {exc}"
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
                'timed out contacting TikTok to '
                'initialize the upload'
            )
        except httpx.HTTPError:
            return self._error('could not connect to TikTok to initialize the upload')

        if inicio.status_code != 200:
            return self._error(mensaje_de_error(inicio, token))

        try:
            cuerpo_respuesta = inicio.json()
        except json.JSONDecodeError:
            return self._error(
                'TikTok returned an invalid JSON response when attempting to '
                'initialize the upload'
            )

        datos = cuerpo_respuesta.get("data") if isinstance(cuerpo_respuesta, dict) else None
        if not isinstance(datos, dict):
            return self._error(
                "TikTok returned no upload data (missing 'data' field)"
            )

        upload_url = datos.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            return self._error('TikTok did not return a valid upload URL')

        publish_id = datos.get("publish_id")
        # Se valida el tipo AQUÍ, antes de subir ningún byte: en este punto
        # TikTok solo ha reservado un id de seguimiento, no ha recibido
        # contenido, así que fallar ahora (en vez de después de subir) es
        # siempre seguro de reintentar -ver el aviso de duplicados en el
        # docstring del módulo-.
        if publish_id is not None and not isinstance(publish_id, str):
            return self._error(
                'TikTok returned an unexpected publish_id (expected '
                f"a string, received {type(publish_id).__name__})"
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
            error='pending: open TikTok and confirm the publication',
        )

    def _resolver_privacidad_y_duracion(
        self, token: str, asset: MediaAsset, client: httpx.Client
    ) -> tuple[str | None, PostResult | None]:
        'In direct mode, query creator info and check privacy and duration.\n\nThe configured PUBLIC_TO_EVERYONE level must appear in privacy_level_options.\nReject unsupported privacy rather than selecting a different level. The video\nmust fit the returned max_video_post_duration_sec. Return either the validated\nprivacy level or an error PostResult, without sending content.'
        datos, error_creador = _consultar_creator_info(client, token)
        if error_creador is not None:
            return None, self._error(
                f"could not query creator_info on TikTok (required "
                f"before publication in mode: direct): {error_creador}"
            )

        opciones = datos.get("privacy_level_options")
        if not isinstance(opciones, list) or not all(
            isinstance(o, str) for o in opciones
        ):
            return None, self._error(
                'TikTok returned no valid list of '
                'privacy_level_options in creator_info: cannot '
                'confirm which privacy levels this account supports'
            )

        if _PRIVACY_LEVEL_DESEADO not in opciones:
            return None, self._error(
                'the TikTok account does not support publishing with '
                f"privacy_level={_PRIVACY_LEVEL_DESEADO!r} (the level "
                'this project expects for mode: direct); this account '
                f"supports these options: {opciones!r}. It may "
                'be a private or underage account: review the '
                'account privacy settings in '
                'TikTok, or use mode: inbox to confirm the '
                'publication manually. No publication is made with a '
                'different privacy level from the expected one.'
            )

        maximo = datos.get("max_video_post_duration_sec")
        duracion = asset.duration_s
        if (
            isinstance(maximo, (int, float))
            and duracion is not None
            and duracion > maximo
        ):
            return None, self._error(
                f"the video is {duracion:.0f}s long and exceeds the {maximo:.0f}s "
                'maximum supported by this account for a direct post '
                '(max_video_post_duration_sec from creator_info, a '
                'creator-specific limit that may be lower than the general '
                'TikTok limit); use mode: inbox or provide a shorter video.'
            )

        return _PRIVACY_LEVEL_DESEADO, None

    def _comprobar_auditoria(self, brand: Brand, modo: str, valor_auditada: object) -> None:
        'Reject direct publication unless the audit configuration is explicitly true.'
        if modo != "direct":
            return

        auditada, reconocido = _interpretar_auditada(valor_auditada)
        if auditada:
            return

        ruta = brand.raiz / "accounts.yml"
        if reconocido:
            raise AuditoriaRequerida(
                f"tiktok.mode is 'direct', but the app is not audited "
                f"(auditada: {valor_auditada!r} in {ruta}). Publishing this way "
                'would restrict the video to private visibility; '
                'later audit approval does not automatically make it public. Use mode: inbox, '
                'or wait until TikTok approves your app audit and '
                'then set auditada: true.'
            )
        # Valor no reconocido (ni true/false canónico, ni booleano, ni
        # ausente): mensaje distinto para que quien configuró la cuenta
        # note el error de tecleo, en vez de creer que ya dejó la cuenta
        # marcada como "no auditada" a propósito.
        raise AuditoriaRequerida(
            f"tiktok.mode is 'direct', but the 'auditada' value in {ruta} "
            f"is not recognized ({valor_auditada!r}): treating the app as NOT "
            'audited for safety to avoid restricting the video '
            'to private visibility (later audit approval '
            'does not automatically make it public). If your app audit is approved, '
            'set auditada: true (a boolean, without '
            'quotes) in accounts.yml.'
        )

    def _error(self, mensaje: str, *, riesgo_duplicado: bool = False) -> PostResult:
        return PostResult(
            platform=self.platform,
            status=PostStatus.ERROR,
            error=mensaje,
            riesgo_duplicado=riesgo_duplicado,
        )


ADAPTADORES[Platform.TIKTOK] = TikTokAdapter
