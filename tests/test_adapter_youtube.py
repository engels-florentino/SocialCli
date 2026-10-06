import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from socialctl.adapters.youtube import YouTubeAdapter
from socialctl.brands import crear_brand
from socialctl.models import MediaAsset, MediaKind, Platform, PlatformPost, PostStatus


@pytest.fixture
def brand(tmp_path):
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.YOUTUBE,
        {"access_token": "t", "refresh_token": "r", "expira_en": time.time() + 3600},
    )
    return b


@pytest.fixture
def post(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"contenido-de-video")
    return PlatformPost(
        platform=Platform.YOUTUBE,
        title="La primera ciudad de America",
        body="Descripcion larga",
        hashtags=["historia"],
        media=[MediaAsset(path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
                          duration_s=45.0, size_bytes=18)],
    )


@respx.mock
def test_publica_y_devuelve_la_url(brand, post):
    respx.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
    ).mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, json={"id": "ABC123"})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "ABC123"
    assert resultado.url == "https://www.youtube.com/watch?v=ABC123"


@respx.mock
def test_envia_titulo_descripcion_y_tags(brand, post):
    ruta_init = respx.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
    ).mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, json={"id": "ABC123"})
    )

    with httpx.Client() as client:
        YouTubeAdapter().publish(post, brand, client)

    enviado = ruta_init.calls[0].request
    import json
    cuerpo = json.loads(enviado.content)
    assert cuerpo["snippet"]["title"] == "La primera ciudad de America"
    assert cuerpo["snippet"]["tags"] == ["historia"]
    # El fixture `post` no indica `privacy`: por decisión explícita del
    # proyecto (el canal tiene ~5000 suscriptores), el valor por defecto es
    # "private", nunca "public" -ver formatter.privacidad_efectiva-.
    assert cuerpo["status"]["privacyStatus"] == "private"
    assert enviado.headers["authorization"] == "Bearer t"


# --- Campo de privacidad configurable (post.yml → platforms.youtube.privacy)


@respx.mock
def test_privacidad_por_defecto_es_private_cuando_no_se_indica(brand, post):
    assert post.privacy is None
    ruta_init = respx.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
    ).mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, json={"id": "ABC123"})
    )

    with httpx.Client() as client:
        YouTubeAdapter().publish(post, brand, client)

    import json
    cuerpo = json.loads(ruta_init.calls[0].request.content)
    assert cuerpo["status"]["privacyStatus"] == "private"


@pytest.mark.parametrize("valor", ["public", "unlisted", "private"])
@respx.mock
def test_envia_el_privacystatus_indicado_en_el_post(brand, post, valor):
    post_con_privacidad = post.model_copy(update={"privacy": valor})
    ruta_init = respx.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
    ).mock(
        return_value=httpx.Response(
            200, headers={"Location": "https://upload.example/sesion"}
        )
    )
    respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, json={"id": "ABC123"})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post_con_privacidad, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    import json
    cuerpo = json.loads(ruta_init.calls[0].request.content)
    assert cuerpo["status"]["privacyStatus"] == valor


def test_validate_rechaza_un_valor_de_privacidad_no_admitido_por_la_api(brand, post):
    post_invalido = post.model_copy(update={"privacy": "publico-total"})
    errores = YouTubeAdapter().validate(post_invalido, brand)
    errores_privacy = [e for e in errores if e.campo == "privacy"]
    assert len(errores_privacy) == 1
    assert "no es un valor de privacidad válido" in errores_privacy[0].motivo



@respx.mock
def test_envia_las_cabeceras_obligatorias_del_upload_resumible(brand, post):
    """Hallazgo 1 (auditoría 2026-09-07): la documentación del flujo
    resumible marca `X-Upload-Content-Length`/`X-Upload-Content-Type` como
    "Required" en el POST inicial (Step 1 - Start a resumable session), y
    `Content-Type` como "Required" en el PUT final (Step 3 - Upload the
    video file), coincidiendo con lo declarado en el POST. Antes del arreglo
    ninguna de las tres se enviaba.
    """
    ruta_init = respx.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
    ).mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    ruta_put = respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, json={"id": "ABC123"})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO

    tamano_real = post.media[0].path.stat().st_size
    peticion_post = ruta_init.calls[0].request
    assert peticion_post.headers["x-upload-content-length"] == str(tamano_real)
    tipo_declarado = peticion_post.headers["x-upload-content-type"]
    assert tipo_declarado

    peticion_put = ruta_put.calls[0].request
    assert peticion_put.headers["content-type"] == tipo_declarado


@respx.mock
def test_error_de_la_api_se_devuelve_como_resultado_no_como_excepcion(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(403, json={"error": {"message": "cuota superada"}})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "cuota superada" in resultado.error


# --- Hallazgo 1: ninguna excepción puede escapar de publish() ---------------


@respx.mock
def test_fallo_de_conexion_al_iniciar_la_subida_no_lanza_excepcion(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "conectar" in resultado.error.lower()


@respx.mock
def test_timeout_al_iniciar_la_subida_no_lanza_excepcion(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "tiempo de espera" in resultado.error.lower()


@respx.mock
def test_fallo_de_conexion_al_subir_los_bytes_no_lanza_excepcion(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    respx.put("https://upload.example/sesion").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "conectar" in resultado.error.lower()


@respx.mock
def test_timeout_al_subir_los_bytes_no_lanza_excepcion(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    respx.put("https://upload.example/sesion").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "tiempo de espera" in resultado.error.lower()


@respx.mock
def test_respuesta_200_sin_id_no_lanza_excepcion(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, json={"kind": "youtube#video"})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    # Frase propia del `except KeyError`, no una subcadena suelta: "id" por sí
    # sola también aparece por accidente en "ha ocurr-id-o", el mensaje del
    # resguardo genérico de `publish()`, así que este test seguía en verde
    # aunque se eliminara el `except KeyError` real. Esta frase completa solo
    # la emite ese `except` específico.
    assert "respondió sin el id del vídeo" in resultado.error.lower()


@respx.mock
def test_respuesta_200_con_cuerpo_no_json_no_lanza_excepcion(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, text="esto no es json")
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    # Frase propia del `except json.JSONDecodeError`, no una subcadena suelta:
    # "json" por sí sola también aparece por accidente en el nombre del tipo
    # de excepción del resguardo genérico ("JSONDecodeError"), así que este
    # test seguía en verde aunque se eliminara el `except` real. Esta frase
    # completa solo la emite ese `except` específico.
    assert "cuerpo que no es json válido" in resultado.error.lower()


@respx.mock
def test_location_con_puerto_no_numerico_no_lanza_excepcion(brand, post):
    """Si el `Location` que devuelve el POST inicial no es una URL parseable
    por `httpx` (aquí, un puerto no numérico), `httpx.InvalidURL` salta al
    construir la petición del PUT, antes de tocar la red. Esa excepción no
    hereda de `httpx.HTTPError` ni de `RequestError`, así que necesita su
    propio `except` explícito para no escapar de `publish()`.
    """
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(
            200, headers={"Location": "https://upload.example:abc/sesion"}
        )
    )
    # No hace falta mockear el PUT: httpx rechaza la URL antes de llegar a la red.

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "url" in resultado.error.lower()
    assert "no válida" in resultado.error.lower()


@respx.mock
def test_location_desmesuradamente_larga_no_lanza_excepcion(brand, post):
    """Mismo caso que el anterior, pero con una URL demasiado larga en vez de
    con un puerto no numérico: es otra forma habitual de disparar
    `httpx.InvalidURL`.
    """
    location_larga = "https://upload.example/" + ("a" * 100_000)
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": location_larga})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "no válida" in resultado.error.lower()


@respx.mock
def test_archivo_sin_permisos_de_lectura_no_lanza_excepcion(brand, post):
    """Un archivo existente pero sin permisos de lectura lanza `PermissionError`,
    que es un `OSError` distinto de `FileNotFoundError`: el `except` original
    (solo `FileNotFoundError`) no lo veía.
    """
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    video_path = post.media[0].path
    permisos_originales = video_path.stat().st_mode
    video_path.chmod(0o000)
    try:
        with httpx.Client() as client:
            resultado = YouTubeAdapter().publish(post, brand, client)
    finally:
        # Restaura los permisos para que pytest pueda limpiar `tmp_path`.
        video_path.chmod(permisos_originales)

    assert resultado.status is PostStatus.ERROR
    # Frase completa del `except PermissionError`, no la subcadena suelta
    # "permiso": el propio `tmp_path` de pytest incluye el nombre de este
    # test ("...sin_permisos_de_lectura...") en su ruta, y esa ruta se
    # interpola en el mensaje de error, así que "permiso" (contenida en
    # "permisos") aparecía igual aunque se eliminara el `except` real y el
    # fallo cayera en el resguardo genérico. La frase completa solo la emite
    # el `except` específico.
    assert "no hay permisos de lectura" in resultado.error.lower()


@respx.mock
def test_resguardo_final_no_lanza_ni_filtra_el_token(monkeypatch, brand, post):
    """El resguardo genérico de `publish()` debe capturar cualquier excepción
    no prevista y jamás filtrar el token, aunque la excepción lleve la
    petición real colgada (como hace `httpx` con sus `RequestError`).

    Se simula un fallo interno inesperado (ni `httpx.HTTPError`, ni `OSError`,
    ni `httpx.InvalidURL`: nada de lo que `_publicar()` ya sabe manejar) que
    solo puede llegar a atraparse en el `except Exception` de `publish()`. La
    excepción se construye a mano con la petición real colgada de
    `.request` —igual que hace `httpx` con sus propias excepciones de
    red— para comprobar que el mensaje final no la vuelca.
    """
    token_reconocible = "TOKEN-CANARIO-RESGUARDO-99887766"
    # Reutilizamos el fixture `brand`, pero le fijamos un token reconocible propio.
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": token_reconocible,
            "refresh_token": "r",
            "expira_en": time.time() + 3600,
        },
    )

    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )

    peticion_capturada: dict[str, httpx.Request] = {}

    def put_con_fallo_inesperado(self, url, **kwargs):
        peticion = httpx.Request("PUT", url, headers=kwargs.get("headers"))
        peticion_capturada["valor"] = peticion
        exc = RuntimeError("fallo interno inesperado del transporte")
        exc.request = peticion  # tal y como httpx cuelga la petición de sus RequestError
        raise exc

    monkeypatch.setattr(httpx.Client, "put", put_con_fallo_inesperado)

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "inesperado" in resultado.error.lower()
    assert "RuntimeError" in resultado.error
    assert token_reconocible not in resultado.error

    # La petición que provocó el fallo sí llevaba el token: si no lo llevara,
    # la aserción anterior sería trivial y no probaría nada.
    assert (
        peticion_capturada["valor"].headers["authorization"]
        == f"Bearer {token_reconocible}"
    )


@respx.mock
def test_archivo_borrado_antes_de_subir_no_lanza_excepcion(brand, post):
    """El tamaño real del fichero (para `X-Upload-Content-Length`, hallazgo 1
    de la auditoría 2026-09-07) se calcula antes de la primera petición de
    red, así que un fichero ya borrado se detecta ahí mismo, sin llegar a
    contactar con YouTube: no hace falta mockear ninguna ruta (si el código
    intentara la red de todas formas, `respx` lo dejaría sin resolver y el
    test fallaría igualmente, solo que con un error distinto).
    """
    post.media[0].path.unlink()

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "no existe" in resultado.error.lower()


def test_media_vacia_no_lanza_excepcion(brand, post):
    post_sin_media = post.model_copy(update={"media": []})

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post_sin_media, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "vídeo" in resultado.error.lower()


def test_varios_archivos_fallan_antes_de_auth_o_http(brand, post):
    segundo = post.media[0].model_copy(update={"path": Path("segundo.mp4")})
    post_con_dos = post.model_copy(update={"media": [post.media[0], segundo]})
    requests = []

    def no_admite_peticiones(request):
        requests.append(request)
        raise AssertionError("no debe haber peticiones HTTP")

    with httpx.Client(transport=httpx.MockTransport(no_admite_peticiones)) as client:
        resultado = YouTubeAdapter().publish(post_con_dos, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "1 archivo" in resultado.error
    assert requests == []


@respx.mock
def test_error_de_conexion_no_filtra_el_token(tmp_path):
    """El token viaja en la cabecera Authorization; un fallo de red no debe filtrarlo.

    `httpx` adjunta la petición completa (con sus cabeceras, incluida
    `Authorization`) a la excepción de red vía su `request_context` interno
    —lo comprobamos aquí leyendo esa cabecera directamente de la petición
    capturada por `respx`—, así que el riesgo de fuga es real si algún día
    alguien interpolase la excepción "a lo bruto". Este test fija un token
    reconocible y comprueba que ni `str()` de la excepción ni el mensaje que
    devuelve `publish()` lo contienen.
    """
    token_reconocible = "TOKEN-CANARIO-9f8e7d6c5b4a3210"
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": token_reconocible,
            "refresh_token": "r",
            "expira_en": time.time() + 3600,
        },
    )
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"contenido-de-video")
    post_local = PlatformPost(
        platform=Platform.YOUTUBE,
        title="La primera ciudad de America",
        body="Descripcion larga",
        hashtags=["historia"],
        media=[
            MediaAsset(
                path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
                duration_s=45.0, size_bytes=18,
            )
        ],
    )

    ruta_init = respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post_local, b, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in resultado.error

    # Confirma que la petición realmente llevaba el token (si no lo llevara,
    # la aserción anterior sería trivial y no probaría nada).
    peticion_enviada = ruta_init.calls[0].request
    assert peticion_enviada.headers["authorization"] == f"Bearer {token_reconocible}"


# --- Hallazgo 2: post.media vacío no debe lanzar IndexError ------------------
# (cubierto arriba por test_media_vacia_no_lanza_excepcion)


# --- Hallazgo 3: el vídeo se sube en streaming, no se carga entero en RAM ----


@respx.mock
def test_sube_el_video_en_streaming_con_content_length_y_sin_chunked(brand, post, monkeypatch):
    """Comprueba que el vídeo se sube en streaming, sin cargarlo entero en RAM.

    No basta con comparar cabeceras y el cuerpo recibido por el PUT: ambas
    cosas son idénticas si alguien revierte la implementación a
    `content=video.path.read_bytes()` (el revisor lo confirmó reproduciendo
    justo esa reversión con la versión anterior de este test, que seguía en
    verde). Aquí se espía `Path.read_bytes` con `monkeypatch` para que
    cualquier llamada durante la subida haga fallar el test de inmediato, lo
    cual sí distingue "streaming" de "cargado entero en memoria".

    (La lectura de verificación al final usa `open(...).read()` en vez de
    `Path.read_bytes`, precisamente para no disparar el espía.)
    """
    video_path = post.media[0].path
    tamano_real = video_path.stat().st_size
    with video_path.open("rb") as f:
        contenido_real = f.read()

    def read_bytes_prohibido(self, *args, **kwargs):
        pytest.fail(
            "el vídeo se cargó entero en memoria con Path.read_bytes(); "
            "debe subirse en streaming (objeto de fichero abierto como "
            "`content`, nunca `path.read_bytes()`)"
        )

    monkeypatch.setattr(Path, "read_bytes", read_bytes_prohibido)

    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(200, headers={"Location": "https://upload.example/sesion"})
    )
    ruta_put = respx.put("https://upload.example/sesion").mock(
        return_value=httpx.Response(200, json={"id": "ABC123"})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO

    peticion_put = ruta_put.calls[0].request
    assert peticion_put.headers["content-length"] == str(tamano_real)
    assert "transfer-encoding" not in peticion_put.headers
    assert peticion_put.content == contenido_real


# --- Hallazgo 4: _mensaje_de_error conserva diagnóstico cuando el cuerpo no
#     tiene el formato {"error": {"message": ...}} --------------------------


@respx.mock
def test_mensaje_de_error_conserva_cuerpo_cuando_no_es_el_formato_esperado(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(
            502, text="502 Bad Gateway: el proxy amont perdió la conexión"
        )
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "502" in resultado.error
    assert "proxy amont perdió la conexión" in resultado.error


@respx.mock
def test_mensaje_de_error_recorta_cuerpos_muy_largos(brand, post):
    cuerpo_largo = "x" * 5000
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(500, text=cuerpo_largo)
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert len(resultado.error) < len(cuerpo_largo)


@respx.mock
def test_mensaje_de_error_con_message_no_textual_da_mensaje_util(brand, post):
    """Hallazgo (menor) de la última revisión: `_mensaje_de_error` hacía
    `respuesta.json()["error"]["message"]` sin comprobar que el valor
    fuera una cadena de texto. Si la API responde con un `message` no
    textual (aquí, un entero, con HTTP 400), ese valor llegaba tal cual a
    `_error()` → `PostResult(error=...)`; como `PostResult.error` está
    tipado `str | None`, pydantic v2 no coacciona el entero y lanza
    `ValidationError` -que solo atrapa el resguardo genérico de
    `publish()`-, perdiendo el mensaje específico de la API justo cuando
    más falta hace. El `status` ya era `ERROR` en ambos casos, así que no
    cambia el desenlace; lo que se pierde es el mensaje.

    Verificado por reversión: sin la comprobación `isinstance(mensaje,
    str)` en `_mensaje_de_error`, `resultado.error` empieza por
    `"ha ocurrido un error inesperado en YouTube (ValidationError): ..."`
    -el resguardo genérico de este adaptador SÍ interpola `str(exc)`, así
    que el propio `12345` reaparece dentro del texto de pydantic
    (`input_value=12345`); por eso la aserción distintiva de este test no
    es la presencia de "12345" (no discrimina entre el caso roto y el
    arreglado aquí), sino la presencia de "HTTP 400" -que solo aparece en
    el mensaje de respaldo de `_mensaje_de_error`, nunca en el texto de un
    `ValidationError`- y la ausencia del resguardo genérico y del nombre
    de la excepción.
    """
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(400, json={"error": {"message": 12345}})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "HTTP 400" in resultado.error
    assert "ha ocurrido un error inesperado en YouTube" not in resultado.error
    assert "ValidationError" not in resultado.error


# --- Hallazgo I6 (revisión final): los cuatro `_mensaje_de_error` ya habían
# divergido -tres redes aceptaban `message: ""` como un mensaje válido (un
# "error" sin ningún motivo) y solo TikTok lo rechazaba-. Unificados en
# `socialctl/adapters/errores.py` con el criterio de TikTok (rechazar la
# cadena vacía) como el bueno.


@respx.mock
def test_message_vacio_no_da_un_error_sin_motivo(brand, post):
    respx.post("https://www.googleapis.com/upload/youtube/v3/videos").mock(
        return_value=httpx.Response(400, json={"error": {"message": ""}})
    )

    with httpx.Client() as client:
        resultado = YouTubeAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    # Antes de unificar, esto daba `PostResult(error="")`: un "error" sin
    # ningún motivo. El criterio unificado (el de TikTok) rechaza la cadena
    # vacía y cae al mensaje de respaldo con el código HTTP.
    assert resultado.error
    assert "HTTP 400" in resultado.error


def _publish_with_readback(brand, post, readback, *, upload_resource=None):
    requests = []

    def transport(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"Location": "https://upload.example/sesion"},
                request=request,
            )
        if request.method == "PUT":
            payload = {"id": "R4cUGeaKrfU"}
            if upload_resource:
                payload.update(upload_resource)
            return httpx.Response(200, json=payload, request=request)
        if isinstance(readback, Exception):
            readback.request = request
            raise readback
        if isinstance(readback, bytes):
            return httpx.Response(200, content=readback, request=request)
        return httpx.Response(200, json=readback, request=request)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = YouTubeAdapter().publish(post, brand, client)

    assert sum(request.method == "POST" for request in requests) == 1
    assert sum(request.method == "PUT" for request in requests) == 1
    assert sum(request.method == "GET" for request in requests) == 1
    get = next(request for request in requests if request.method == "GET")
    assert get.url.params["id"] == "R4cUGeaKrfU"
    assert get.url.params["part"] == "status,processingDetails"
    return result


def test_id_recibido_seguido_de_timeout_conserva_subida_y_no_es_reintentable(brand, post):
    result = _publish_with_readback(
        brand,
        post,
        httpx.ReadTimeout("readback timeout"),
    )

    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "R4cUGeaKrfU"
    assert result.requested_privacy == "private"
    assert result.observed_privacy is None
    assert result.riesgo_duplicado is False
    assert result.error is None
    assert any("visibilidad" in warning.lower() for warning in result.warnings)


@pytest.mark.parametrize(
    ("readback", "privacy", "publish_at", "processing"),
    [
        (
            {"items": [{"status": {"privacyStatus": "private"}}]},
            "private",
            None,
            None,
        ),
        (
            {
                "items": [
                    {
                        "status": {"privacyStatus": "public"},
                        "processingDetails": {"processingStatus": "succeeded"},
                    }
                ]
            },
            "public",
            None,
            "succeeded",
        ),
        (
            {
                "items": [
                    {
                        "status": {
                            "privacyStatus": "private",
                            "publishAt": "2026-09-14T20:00:00Z",
                        }
                    }
                ]
            },
            "private",
            "2026-09-14T20:00:00Z",
            None,
        ),
    ],
)
def test_readback_observa_visibilidad_y_procesamiento_una_sola_vez(
    brand, post, readback, privacy, publish_at, processing
):
    result = _publish_with_readback(brand, post, readback)

    assert result.status is PostStatus.PUBLICADO
    assert result.requested_privacy == "private"
    assert result.observed_privacy == privacy
    assert result.observed_publish_at == publish_at
    assert result.observed_processing_status == processing
    observed = datetime.fromisoformat(result.visibility_observed_at.replace("Z", "+00:00"))
    assert observed.utcoffset() is not None


def test_respuesta_sin_campos_no_inventa_estado_observado(brand, post):
    result = _publish_with_readback(brand, post, {"items": [{"id": "R4cUGeaKrfU"}]})

    assert result.status is PostStatus.PUBLICADO
    assert result.requested_privacy == "private"
    assert result.observed_privacy is None
    assert result.observed_processing_status is None
    assert result.visibility_observed_at is None


def test_estado_del_upload_es_fallback_respaldado_si_falla_readback(brand, post):
    result = _publish_with_readback(
        brand,
        post,
        httpx.ReadTimeout("readback timeout"),
        upload_resource={
            "status": {"privacyStatus": "unlisted"},
            "processingDetails": {"processingStatus": "processing"},
        },
    )

    assert result.status is PostStatus.PUBLICADO
    assert result.observed_privacy == "unlisted"
    assert result.observed_processing_status == "processing"
    observed = datetime.fromisoformat(result.visibility_observed_at.replace("Z", "+00:00"))
    assert observed.utcoffset() is not None


def test_readback_con_bytes_invalidos_no_revierte_subida_confirmada(brand, post):
    result = _publish_with_readback(brand, post, b"\xff")

    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "R4cUGeaKrfU"
    assert result.url.endswith("R4cUGeaKrfU")
    assert result.error is None
    assert result.riesgo_duplicado is False
    assert any("visibilidad" in warning.lower() for warning in result.warnings)


@pytest.mark.parametrize(
    "upload_resource",
    [
        {"status": []},
        {"status": "unexpected"},
        {"processingDetails": ["unexpected"]},
        {
            "status": {"privacyStatus": {"unexpected": True}},
            "processingDetails": {"processingStatus": 123},
        },
    ],
)
def test_metadatos_malformados_del_upload_con_id_no_pierden_la_subida(
    brand, post, upload_resource
):
    result = _publish_with_readback(
        brand,
        post,
        b"\xff",
        upload_resource=upload_resource,
    )

    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "R4cUGeaKrfU"
    assert result.error is None
    assert result.riesgo_duplicado is False
    assert result.observed_privacy is None


@pytest.mark.parametrize("readback", [None, [], ["unexpected"]])
def test_readback_con_raiz_json_inesperada_conserva_subida(brand, post, readback):
    result = _publish_with_readback(brand, post, readback)

    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "R4cUGeaKrfU"
    assert result.error is None
    assert result.riesgo_duplicado is False
    assert result.observed_privacy is None
    assert any("visibilidad" in warning.lower() for warning in result.warnings)


def test_readback_solo_processing_conserva_snapshot_de_visibilidad_del_upload(
    brand, post, monkeypatch
):
    import socialctl.adapters.youtube as youtube_module

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            return datetime(2026, 9, 13, 20, 0, cls.calls, tzinfo=timezone.utc)

    monkeypatch.setattr(youtube_module, "datetime", Clock)
    result = _publish_with_readback(
        brand,
        post,
        {
            "items": [
                {"processingDetails": {"processingStatus": "succeeded"}}
            ]
        },
        upload_resource={
            "status": {
                "privacyStatus": "private",
                "publishAt": "2026-09-14T20:00:00Z",
            },
            "processingDetails": {"processingStatus": "processing"},
        },
    )

    assert result.observed_privacy == "private"
    assert result.observed_publish_at == "2026-09-14T20:00:00Z"
    assert result.observed_processing_status == "succeeded"
    assert result.visibility_observed_at == "2026-09-13T20:00:01+00:00"
    assert Clock.calls == 1


@pytest.mark.parametrize(
    ("initial_status", "readback_status"),
    [
        (
            {"privacyStatus": "public"},
            {"privacyStatus": "private", "publishAt": "fecha-invalida"},
        ),
        (
            {"privacyStatus": "public"},
            {"privacyStatus": "private", "publishAt": "2026-09-14T20:00:00"},
        ),
        (
            {
                "privacyStatus": "private",
                "publishAt": "2026-09-14T20:00:00Z",
            },
            {"privacyStatus": "private", "publishAt": {"unexpected": True}},
        ),
        (
            {
                "privacyStatus": "private",
                "publishAt": "2026-09-14T20:00:00Z",
            },
            {"privacyStatus": "private"},
        ),
    ],
)
def test_privacidad_nueva_valida_reemplaza_snapshot_aunque_publishat_no_sea_valido(
    brand, post, monkeypatch, initial_status, readback_status
):
    import socialctl.adapters.youtube as youtube_module

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            return datetime(2026, 9, 13, 20, 0, cls.calls, tzinfo=timezone.utc)

    monkeypatch.setattr(youtube_module, "datetime", Clock)
    result = _publish_with_readback(
        brand,
        post,
        {"items": [{"status": readback_status}]},
        upload_resource={"status": initial_status},
    )

    assert result.status is PostStatus.PUBLICADO
    assert result.platform_id == "R4cUGeaKrfU"
    assert result.observed_privacy == "private"
    assert result.observed_publish_at is None
    assert result.visibility_observed_at == "2026-09-13T20:00:02+00:00"
    assert Clock.calls == 2
