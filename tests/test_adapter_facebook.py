import time
from pathlib import Path

import httpx
import pytest
import respx

from socialctl.adapters.facebook import GRAFO as GRAFO_DEL_ADAPTADOR
from socialctl.adapters.facebook import FacebookAdapter
from socialctl.brands import cargar_brand, crear_brand
from socialctl.models import MediaAsset, MediaKind, Platform, PlatformPost, PostStatus

GRAFO = "https://graph.facebook.com/v26.0"


@pytest.fixture
def brand(tmp_path):
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(Platform.FACEBOOK, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text("facebook:\n  page_id: '12345'\n", encoding="utf-8")
    return cargar_brand(b.raiz.parent, "Histopast")


# --- Flujo básico: texto / imagen / vídeo van al endpoint correcto ----------


@respx.mock
def test_solo_texto_va_al_feed(brand):
    ruta = respx.post(f"{GRAFO}/12345/feed").mock(
        return_value=httpx.Response(200, json={"id": "12345_999"})
    )
    post = PlatformPost(platform=Platform.FACEBOOK, body="Dato historico", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert ruta.called
    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.url == "https://www.facebook.com/12345_999"


@respx.mock
def test_video_va_al_endpoint_de_videos(brand, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"bytes")
    ruta = respx.post(f"{GRAFO}/12345/videos").mock(
        return_value=httpx.Response(200, json={"id": "777"})
    )
    post = PlatformPost(
        platform=Platform.FACEBOOK,
        body="Clip",
        media=[MediaAsset(path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
                          duration_s=30.0, size_bytes=5)],
    )

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert ruta.called
    assert resultado.platform_id == "777"


@respx.mock
def test_imagen_va_al_endpoint_de_fotos(brand, tmp_path):
    imagen = tmp_path / "foto.jpg"
    imagen.write_bytes(b"bytes")
    ruta = respx.post(f"{GRAFO}/12345/photos").mock(
        return_value=httpx.Response(200, json={"id": "888"})
    )
    post = PlatformPost(
        platform=Platform.FACEBOOK,
        body="Foto",
        media=[MediaAsset(path=imagen, kind=MediaKind.IMAGE, width=1080, height=1080,
                          size_bytes=5)],
    )

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert ruta.called
    assert resultado.platform_id == "888"


def test_varios_archivos_fallan_antes_de_auth_o_http(brand, tmp_path):
    assets = [
        MediaAsset(
            path=tmp_path / name,
            kind=MediaKind.VIDEO,
            width=1080,
            height=1920,
            duration_s=30,
            size_bytes=5,
        )
        for name in ("uno.mp4", "dos.mp4")
    ]
    post = PlatformPost(platform=Platform.FACEBOOK, body="Clip", media=assets)
    requests = []

    def no_admite_peticiones(request):
        requests.append(request)
        raise AssertionError("no debe haber peticiones HTTP")

    with httpx.Client(transport=httpx.MockTransport(no_admite_peticiones)) as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert '1 file' in resultado.error
    assert requests == []


@respx.mock
def test_error_del_grafo_se_devuelve_como_resultado(brand):
    respx.post(f"{GRAFO}/12345/feed").mock(
        return_value=httpx.Response(400, json={"error": {"message": "permiso insuficiente"}})
    )
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "permiso insuficiente" in resultado.error
    assert resultado.riesgo_duplicado is False


@respx.mock
def test_mensaje_de_error_con_message_no_textual_da_mensaje_util(brand):
    """Hallazgo (menor) de la última revisión: `_mensaje_de_error` hacía
    `respuesta.json()["error"]["message"]` sin comprobar que el valor
    fuera una cadena de texto. Si el Grafo responde con un `message` no
    textual (aquí, un entero, con HTTP 400), ese valor llegaba tal cual a
    `_error()` → `PostResult(error=...)`; como `PostResult.error` está
    tipado `str | None`, pydantic v2 no coacciona el entero y lanza
    `ValidationError` -que solo atrapa el resguardo genérico de
    `publish()`-, perdiendo el mensaje específico de la API justo cuando
    más falta hace. El `status` ya era `ERROR` en ambos casos, así que no
    cambia el desenlace; lo que se pierde es el mensaje.

    Verificado por reversión: sin la comprobación `isinstance(mensaje,
    str)` en `_mensaje_de_error`, `resultado.error` es
    `"ha ocurrido un error inesperado en Facebook (ValidationError)"` en
    vez del mensaje de respaldo (con el cuerpo de la respuesta) que se
    comprueba aquí; las tres aserciones de más abajo fallan en ese caso.
    """
    respx.post(f"{GRAFO}/12345/feed").mock(
        return_value=httpx.Response(400, json={"error": {"message": 12345}})
    )
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    # El mensaje debe seguir el camino de respaldo de `_mensaje_de_error`
    # (el cuerpo de la respuesta, con el código HTTP), no el resguardo
    # genérico de `publish()` ni el nombre de la excepción que se
    # dispararía sin el arreglo.
    assert "HTTP 400" in resultado.error
    assert "12345" in resultado.error
    assert 'ha ocurrido un error inesperado in Facebook' not in resultado.error
    assert "ValidationError" not in resultado.error


def test_sin_page_id_configurado_da_error(tmp_path):
    b = crear_brand(tmp_path, "SinConfig")
    b.guardar_secreto(Platform.FACEBOOK, {"access_token": "t", "expira_en": time.time() + 3600})
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, b, client)

    assert resultado.status is PostStatus.ERROR
    assert "page_id" in resultado.error


# --- Hallazgo 1: ninguna excepción puede escapar de publish() ---------------


@respx.mock
def test_fallo_de_conexion_no_lanza_excepcion(brand):
    respx.post(f"{GRAFO}/12345/feed").mock(side_effect=httpx.ConnectError("Connection refused"))
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "connect" in resultado.error.lower()
    assert resultado.riesgo_duplicado is True


@respx.mock
def test_timeout_no_lanza_excepcion_y_da_mensaje_especifico(brand):
    """`httpx.TimeoutException` es subclase de `httpx.HTTPError`: si el
    `except httpx.HTTPError` estuviera antes en el código (o fuera el único),
    este caso caería ahí y el mensaje sería el genérico de "no se pudo
    conectar", nunca el de "tiempo de espera". Este test solo pasa si el
    `except` específico de timeout va primero.
    """
    respx.post(f"{GRAFO}/12345/feed").mock(side_effect=httpx.TimeoutException("timed out"))
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "timed out" in resultado.error.lower()
    assert resultado.riesgo_duplicado is True


@respx.mock
def test_respuesta_200_con_cuerpo_no_json_no_lanza_excepcion(brand):
    respx.post(f"{GRAFO}/12345/feed").mock(return_value=httpx.Response(200, text="esto no es json"))
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert resultado.riesgo_duplicado is True
    # Frase propia del `except json.JSONDecodeError`, no una subcadena suelta:
    # "json" por sí sola también aparece por accidente en el nombre del tipo
    # de excepción del resguardo genérico ("JSONDecodeError"), así que este
    # test seguía en verde aunque se eliminara el `except` real. Esta frase
    # completa solo la emite ese `except` específico.
    assert 'invalid json response' in resultado.error.lower()


@respx.mock
def test_respuesta_200_sin_id_no_lanza_excepcion(brand):
    respx.post(f"{GRAFO}/12345/feed").mock(return_value=httpx.Response(200, json={"foo": "bar"}))
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert resultado.riesgo_duplicado is True
    # Frase propia del `except KeyError`, no una subcadena suelta: "id" por sí
    # sola también aparece por accidente en "ha ocurr-id-o", el mensaje del
    # resguardo genérico de `publish()`, así que este test seguía en verde
    # aunque se eliminara el `except KeyError` real. Esta frase completa solo
    # la emite ese `except` específico.
    assert 'returned no post id' in resultado.error.lower()


@pytest.mark.parametrize("invalid_id", [None, "", "   ", 123])
def test_respuesta_200_con_id_invalido_es_incierta_y_no_intenta_comentario(
    brand, invalid_id
):
    """Catches accepting an unusable remote ID or using it for a comment URL."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": invalid_id})

    post = PlatformPost(
        platform=Platform.FACEBOOK,
        body="x",
        first_comment="No debe enviarse",
        media=[],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert resultado.platform_id is None
    assert resultado.riesgo_duplicado is True
    assert "post id" in (resultado.error or "").lower()
    assert [request.url.path for request in requests] == ["/v26.0/12345/feed"]


@respx.mock
def test_page_id_con_caracter_invalido_produce_error_manejado(tmp_path):
    """Un `page_id` con un carácter no imprimible (p. ej. un salto de línea
    colado por un error de copia/pega en `accounts.yml`) hace que `httpx`
    rechace la URL con `httpx.InvalidURL` al construir la petición, antes de
    tocar la red. Esa excepción NO hereda de `httpx.HTTPError`, así que
    necesita su propio `except` explícito para no escapar de `publish()`.
    """
    b = crear_brand(tmp_path, "Rota")
    b.guardar_secreto(Platform.FACEBOOK, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text(
        'facebook:\n  page_id: "123\\n45"\n', encoding="utf-8"
    )
    brand_rota = cargar_brand(tmp_path, "Rota")
    assert "\n" in brand_rota.cuentas["facebook"]["page_id"]

    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])
    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand_rota, client)

    assert resultado.status is PostStatus.ERROR
    assert "url" in resultado.error.lower()
    assert "invalid" in resultado.error.lower()
    assert resultado.riesgo_duplicado is False


def test_archivo_borrado_antes_de_publicar_no_lanza_excepcion(brand, tmp_path):
    imagen = tmp_path / "foto.jpg"
    imagen.write_bytes(b"bytes")
    post = PlatformPost(
        platform=Platform.FACEBOOK,
        body="x",
        media=[MediaAsset(path=imagen, kind=MediaKind.IMAGE, size_bytes=5)],
    )
    imagen.unlink()

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "no longer exists" in resultado.error.lower()


def test_ruta_de_media_es_un_directorio_no_lanza_excepcion(brand, tmp_path):
    """Un directorio (no un fichero) en `MediaAsset.path` lanza
    `IsADirectoryError`, que es un `OSError` distinto de `FileNotFoundError`:
    un `except FileNotFoundError` a secas no lo vería.
    """
    directorio = tmp_path / "no_es_un_fichero"
    directorio.mkdir()
    post = PlatformPost(
        platform=Platform.FACEBOOK,
        body="x",
        media=[MediaAsset(path=directorio, kind=MediaKind.IMAGE, size_bytes=0)],
    )

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "directory" in resultado.error.lower()


def test_archivo_sin_permisos_de_lectura_no_lanza_excepcion(brand, tmp_path):
    """Un fichero existente pero sin permisos de lectura lanza
    `PermissionError`, otro `OSError` que un `except FileNotFoundError` a
    secas no vería.
    """
    imagen = tmp_path / "foto.jpg"
    imagen.write_bytes(b"bytes")
    permisos_originales = imagen.stat().st_mode
    imagen.chmod(0o000)
    try:
        post = PlatformPost(
            platform=Platform.FACEBOOK,
            body="x",
            media=[MediaAsset(path=imagen, kind=MediaKind.IMAGE, size_bytes=5)],
        )
        with httpx.Client() as client:
            resultado = FacebookAdapter().publish(post, brand, client)
    finally:
        # Restaura los permisos para que pytest pueda limpiar `tmp_path`.
        imagen.chmod(permisos_originales)

    assert resultado.status is PostStatus.ERROR
    assert 'not readable' in resultado.error.lower()


def test_fallo_inesperado_generico_no_escapa_de_publish(brand, monkeypatch):
    """Resguardo de última instancia: cualquier excepción no prevista (aquí,
    una que nadie de los `except` específicos de `_publicar` está esperando)
    debe seguir devolviendo un `PostResult` de error, nunca propagarse.
    """
    import socialctl.adapters.facebook as modulo_facebook

    def explota(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(modulo_facebook, "componer_caption", explota)

    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])
    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "RuntimeError" in resultado.error
    assert resultado.riesgo_duplicado is True


# --- Hallazgo 2: streaming y cierre garantizado del fichero -----------------


@respx.mock
def test_el_fichero_se_sube_en_streaming_y_se_cierra_aunque_falle_la_peticion(
    brand, tmp_path, monkeypatch
):
    """Los vídeos de este proyecto pueden pesar varios GB: deben subirse en
    streaming (un fichero abierto, no `Path.read_bytes()`), y el fichero debe
    cerrarse pase lo que pase, incluso si la petición HTTP falla a mitad.
    """
    video = tmp_path / "clip.mp4"
    contenido = b"contenido-binario-del-video"
    video.write_bytes(contenido)

    def read_bytes_prohibido(self, *args, **kwargs):
        pytest.fail(
            "el fichero se cargó entero en memoria con Path.read_bytes(); "
            "debe subirse en streaming (fichero abierto, nunca read_bytes())"
        )

    monkeypatch.setattr(Path, "read_bytes", read_bytes_prohibido)

    ficheros_abiertos = []
    abrir_original = Path.open

    def abrir_y_registrar(self, *args, **kwargs):
        fichero = abrir_original(self, *args, **kwargs)
        ficheros_abiertos.append(fichero)
        return fichero

    monkeypatch.setattr(Path, "open", abrir_y_registrar)

    respx.post(f"{GRAFO}/12345/videos").mock(side_effect=httpx.ConnectError("no conecta"))
    post = PlatformPost(
        platform=Platform.FACEBOOK,
        body="x",
        media=[MediaAsset(path=video, kind=MediaKind.VIDEO, size_bytes=len(contenido))],
    )

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert ficheros_abiertos, 'se expected que el adaptador abriera el fichero de vídeo'
    assert ficheros_abiertos[-1].closed, (
        "el fichero debe cerrarse aunque la petición falle a mitad"
    )


# --- Hallazgo 3 (seguridad): el token nunca debe aparecer en ningún mensaje -


@respx.mock
def test_error_de_conexion_no_filtra_el_token(tmp_path):
    """El token de página viaja en el CUERPO de la petición (`access_token`),
    no en una cabecera como en YouTube: eso lo hace más fácil de filtrar por
    accidente. Fija un token reconocible y comprueba que ni el mensaje de
    error de un fallo de conexión lo contiene.
    """
    token_reconocible = "TOKEN-CANARIO-9f8e7d6c5b4a3210"
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.FACEBOOK, {"access_token": token_reconocible, "expira_en": time.time() + 3600}
    )
    (b.raiz / "accounts.yml").write_text("facebook:\n  page_id: '12345'\n", encoding="utf-8")
    brand_local = cargar_brand(tmp_path, "Histopast")

    ruta = respx.post(f"{GRAFO}/12345/feed").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand_local, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in resultado.error

    # Confirma que la petición realmente llevaba el token en el cuerpo (si no
    # lo llevara, la aserción anterior sería trivial y no probaría nada).
    peticion_enviada = ruta.calls[0].request
    assert token_reconocible.encode() in peticion_enviada.content


@respx.mock
def test_resguardo_final_no_lanza_ni_filtra_el_token(brand, monkeypatch):
    """El resguardo genérico de `publish()` debe capturar cualquier excepción
    no prevista y jamás filtrar el token, ni siquiera cuando la propia
    excepción arrastra el cuerpo real de la petición (el peor caso posible,
    dado que aquí el token viaja en ese cuerpo).

    Se monkeypatchea `httpx.Client.post` para que construya la petición real
    (con el token ya codificado en el cuerpo, exactamente como lo haría
    `_publicar`) y lance una excepción cuyo propio mensaje incluye ese cuerpo
    -simulando el peor escenario: una librería de transporte que, en su
    mensaje de error, vuelca la petición que falló-. Si el resguardo genérico
    interpolase `str(exc)` a lo bruto, este test lo detectaría.
    """
    token_reconocible = "TOKEN-CANARIO-RESGUARDO-99887766"
    brand.guardar_secreto(
        Platform.FACEBOOK,
        {"access_token": token_reconocible, "expira_en": time.time() + 3600},
    )

    peticion_capturada: dict[str, httpx.Request] = {}

    def post_con_fallo_inesperado(self, url, **kwargs):
        peticion = httpx.Request("POST", url, data=kwargs.get("data"))
        peticion_capturada["valor"] = peticion
        raise RuntimeError(
            f"fallo interno inesperado del transporte; cuerpo real de la "
            f"petición: {peticion.content!r}"
        )

    monkeypatch.setattr(httpx.Client, "post", post_con_fallo_inesperado)

    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "RuntimeError" in resultado.error
    assert token_reconocible not in resultado.error

    # La petición que provocó el fallo sí llevaba el token en el cuerpo: si
    # no lo llevara, la aserción anterior sería trivial y no probaría nada.
    assert token_reconocible.encode() in peticion_capturada["valor"].content


# --- Hallazgo 5 (auditoría fix-meta-tiktok, 2026-09-07): Graph API v21.0 -----
# caduca el 21 de enero de 2027 y ya iba 5 versiones por detrás de la
# vigente (v26.0, confirmado con Context7 contra el changelog de Meta).


def test_usa_la_version_vigente_de_la_graph_api():
    assert GRAFO_DEL_ADAPTADOR == "https://graph.facebook.com/v26.0"


# --- Hallazgo C1 (CRÍTICO, revisión final): un cuerpo de error inesperado
# -no JSON, o JSON sin la forma de error de la Graph API- que refleje la
# petición fallida (el token viaja en su CUERPO en este adaptador) no debe
# poder colar el token en el mensaje.


@respx.mock
def test_cuerpo_no_json_reflejando_el_token_no_lo_filtra(tmp_path):
    token_reconocible = "TOKEN-CANARIO-PROXY-NOJSON-97531"
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.FACEBOOK, {"access_token": token_reconocible, "expira_en": time.time() + 3600}
    )
    (b.raiz / "accounts.yml").write_text("facebook:\n  page_id: '12345'\n", encoding="utf-8")
    brand_local = cargar_brand(tmp_path, "Histopast")

    def _proxy_que_refleja_el_cuerpo(request):
        return httpx.Response(
            502, text=f"<html>Bad gateway; request body: {request.content.decode()}</html>"
        )

    ruta = respx.post(f"{GRAFO}/12345/feed").mock(side_effect=_proxy_que_refleja_el_cuerpo)
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand_local, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in (resultado.error or "")

    peticion_real = ruta.calls[0].request
    assert token_reconocible.encode() in peticion_real.content


@respx.mock
def test_json_sin_forma_de_error_de_grafo_no_filtra_el_token(tmp_path):
    """Cuerpo JSON (HTTP no-200) pero SIN la forma ``{"error": {"message":
    ...}}`` -aquí, un diagnóstico de un WAF que ecoa parte del cuerpo de la
    petición fallida-: cae por el mismo camino de respaldo que un cuerpo no
    JSON, y tampoco debe poder colar el token.
    """
    token_reconocible = "TOKEN-CANARIO-WAF-JSON-8642097531"
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.FACEBOOK, {"access_token": token_reconocible, "expira_en": time.time() + 3600}
    )
    (b.raiz / "accounts.yml").write_text("facebook:\n  page_id: '12345'\n", encoding="utf-8")
    brand_local = cargar_brand(tmp_path, "Histopast")

    def _waf_que_refleja_el_cuerpo(request):
        return httpx.Response(
            400,
            json={"blocked_by": "waf", "debug_echo": request.content.decode()},
        )

    ruta = respx.post(f"{GRAFO}/12345/feed").mock(side_effect=_waf_que_refleja_el_cuerpo)
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand_local, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in (resultado.error or "")

    peticion_real = ruta.calls[0].request
    assert token_reconocible.encode() in peticion_real.content


# --- Hallazgo I4 (revisión final): la falta de `page_id` solo se comprobaba
# al publicar; ahora también en `validate()`, para que aparezca en el
# preview antes de la aprobación.


def test_validate_detecta_falta_de_page_id(tmp_path):
    crear_brand(tmp_path, "SinPageIdValidate")  # plantilla: page_id vacío
    brand_sin_config = cargar_brand(tmp_path, "SinPageIdValidate")
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    errores = FacebookAdapter().validate(post, brand_sin_config)

    assert any("page_id" in e.motivo for e in errores)


def test_validate_sin_problemas_de_cuenta_cuando_esta_bien_configurada(brand):
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    errores = FacebookAdapter().validate(post, brand)

    assert not any(e.campo == "cuenta" for e in errores)


@respx.mock
def test_message_vacio_no_da_un_error_sin_motivo(brand):
    """Hallazgo I6 (revisión final): igual que en youtube.py e instagram.py,
    `message: ""` daba antes `PostResult(error="")` -un "error" sin ningún
    motivo-. Unificado con el criterio de TikTok (rechazar la cadena
    vacía).
    """
    respx.post(f"{GRAFO}/12345/feed").mock(
        return_value=httpx.Response(400, json={"error": {"message": ""}})
    )
    post = PlatformPost(platform=Platform.FACEBOOK, body="x", media=[])

    with httpx.Client() as client:
        resultado = FacebookAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert resultado.error
    assert "HTTP 400" in resultado.error
