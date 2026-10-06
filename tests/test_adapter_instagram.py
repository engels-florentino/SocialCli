import json
import time

import httpx
import pytest
import respx

from socialctl.adapters.instagram import GRAFO as GRAFO_DEL_ADAPTADOR
from socialctl.adapters.instagram import InstagramAdapter
from socialctl.brands import cargar_brand, crear_brand
from socialctl.models import MediaAsset, MediaKind, Platform, PlatformPost, PostStatus

GRAFO = "https://graph.facebook.com/v26.0"
URL_CLIP = "https://cdn.example/histopast/clip.mp4"


def _media_publicada():
    """El archivo esta subido al host y responde al HEAD."""
    respx.head(URL_CLIP).mock(return_value=httpx.Response(200))


@pytest.fixture
def brand(tmp_path):
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(Platform.INSTAGRAM, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text(
        "instagram:\n  ig_user_id: '999'\n  media_url_base: 'https://cdn.example/histopast'\n",
        encoding="utf-8",
    )
    return cargar_brand(tmp_path, "Histopast")


@pytest.fixture
def post(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"bytes")
    return PlatformPost(
        platform=Platform.INSTAGRAM,
        body="Gancho potente",
        hashtags=["historia"],
        media=[MediaAsset(path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
                          duration_s=45.0, size_bytes=5)],
    )


@pytest.fixture
def post_imagen(tmp_path):
    imagen = tmp_path / "foto.jpg"
    imagen.write_bytes(b"bytes")
    return PlatformPost(
        platform=Platform.INSTAGRAM,
        body="Gancho potente",
        hashtags=["historia"],
        media=[MediaAsset(path=imagen, kind=MediaKind.IMAGE, width=1080, height=1080,
                          size_bytes=5)],
    )


# --- Flujo básico: los tres pasos, en orden -----------------------------


@respx.mock
def test_flujo_completo_de_contenedor(brand, post):
    _media_publicada()
    crear = respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    publicar = respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert crear.called and publicar.called
    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"


@respx.mock
def test_envia_la_url_publica_de_la_media(brand, post):
    _media_publicada()
    crear = respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    with httpx.Client() as client:
        InstagramAdapter(espera_s=0).publish(post, brand, client)

    enviado = dict(httpx.QueryParams(crear.calls[0].request.content.decode()))
    assert enviado["video_url"] == URL_CLIP
    assert enviado["media_type"] == "REELS"
    assert "#historia" in enviado["caption"]


@respx.mock
def test_imagen_usa_image_url_y_sin_media_type(brand, post_imagen):
    """Una imagen usa `image_url` (no `video_url`) y no debe llevar
    `media_type`: ese parámetro solo tiene sentido -y solo se fija- para
    vídeos (REELS). Si se enviase para una imagen, la Graph API real lo
    rechazaría.
    """
    respx.head("https://cdn.example/histopast/foto.jpg").mock(
        return_value=httpx.Response(200)
    )
    crear = respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post_imagen, brand, client)

    enviado = dict(httpx.QueryParams(crear.calls[0].request.content.decode()))
    assert enviado["image_url"] == "https://cdn.example/histopast/foto.jpg"
    assert "media_type" not in enviado
    assert resultado.status is PostStatus.PUBLICADO


@respx.mock
def test_permalink_real_se_usa_como_url(brand, post):
    """`media_publish` no devuelve el shortcode público, solo un id interno:
    construir una URL a mano con ese id (p. ej. `instagram.com/<id>` o
    `instagram.com/p/<id>`) sería un enlace roto. Tras publicar, se pide el
    permalink real (`GET /{id}?fields=permalink`) y se usa tal cual como
    `PostResult.url`, con el token y el campo correcto en la petición.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )
    permalink = respx.get(f"{GRAFO}/post1").mock(
        return_value=httpx.Response(
            200, json={"permalink": "https://www.instagram.com/reel/ABC123/"}
        )
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"
    assert resultado.url == "https://www.instagram.com/reel/ABC123/"
    assert "/p/" not in resultado.url  # no es una URL inventada

    assert permalink.called
    enviado = dict(permalink.calls[0].request.url.params)
    assert enviado["fields"] == "permalink"
    assert enviado["access_token"] == "t"


@respx.mock
def test_media_url_base_con_barra_final_no_duplica_barras(tmp_path, post):
    b = crear_brand(tmp_path, "ConBarra")
    b.guardar_secreto(Platform.INSTAGRAM, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text(
        "instagram:\n  ig_user_id: '999'\n  media_url_base: 'https://cdn.example/histopast/'\n",
        encoding="utf-8",
    )
    brand_con_barra = cargar_brand(tmp_path, "ConBarra")

    _media_publicada()
    crear = respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    with httpx.Client() as client:
        InstagramAdapter(espera_s=0).publish(post, brand_con_barra, client)

    enviado = dict(httpx.QueryParams(crear.calls[0].request.content.decode()))
    assert enviado["video_url"] == URL_CLIP  # sin "//" duplicada


# --- La URL pública conserva la subcarpeta y codifica sus segmentos -----


@pytest.fixture
def post_en_subcarpeta(tmp_path):
    """Media guardada en una subcarpeta (p. ej. `short/` para verticales)."""
    carpeta = tmp_path / "short"
    carpeta.mkdir()
    video = carpeta / "S2.mp4"
    video.write_bytes(b"bytes")
    return PlatformPost(
        platform=Platform.INSTAGRAM,
        body="Gancho potente",
        hashtags=["historia"],
        media=[MediaAsset(
            path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
            duration_s=45.0, size_bytes=5, ruta_relativa="short/S2.mp4",
        )],
    )


@respx.mock
def test_la_url_de_media_conserva_la_subcarpeta(brand, post_en_subcarpeta):
    url_con_subcarpeta = "https://cdn.example/histopast/short/S2.mp4"
    respx.head(url_con_subcarpeta).mock(return_value=httpx.Response(200))
    crear = respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post_en_subcarpeta, brand, client)

    enviado = dict(httpx.QueryParams(crear.calls[0].request.content.decode()))
    assert enviado["video_url"] == url_con_subcarpeta
    assert resultado.status is PostStatus.PUBLICADO


@pytest.fixture
def post_con_espacio_y_acento(tmp_path):
    video = tmp_path / "vídeo día.mp4"
    video.write_bytes(b"bytes")
    return PlatformPost(
        platform=Platform.INSTAGRAM,
        body="Gancho potente",
        hashtags=["historia"],
        media=[MediaAsset(
            path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
            duration_s=45.0, size_bytes=5, ruta_relativa="vídeo día.mp4",
        )],
    )


@respx.mock
def test_la_url_de_media_codifica_espacios_y_acentos(brand, post_con_espacio_y_acento):
    url_codificada = "https://cdn.example/histopast/v%C3%ADdeo%20d%C3%ADa.mp4"
    respx.head(url_codificada).mock(return_value=httpx.Response(200))
    crear = respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post_con_espacio_y_acento, brand, client)

    enviado = dict(httpx.QueryParams(crear.calls[0].request.content.decode()))
    assert enviado["video_url"] == url_codificada
    assert resultado.status is PostStatus.PUBLICADO


# --- Sondeo del contenedor -----------------------------------------------


@respx.mock
def test_contenedor_en_error_no_publica(brand, post):
    """Fija el corte inmediato ante `status_code == "ERROR"`, no solo el
    resultado final: si se eliminase ese corte (dejando que el `ERROR` caiga
    en la rama de "sigue sondeando"), el bucle agotaría igualmente los
    `intentos` y acabaría en `PostStatus.ERROR` -este mismo test seguiría en
    verde con solo `not publicar.called` y el status-, pero con
    `sondeo.call_count == self.intentos` en vez de 1, y con el mensaje
    genérico de timeout en vez de mencionar el estado ERROR del contenedor.
    En producción, con `espera_s=5.0` real, esa regresión tardaría minutos en
    manifestarse en vez de fallar al instante. Verificado por reversión: al
    quitar el `if codigo == "ERROR": return ...` de `_esperar_procesado`,
    este test falla (ver informe de la tarea).
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    sondeo = respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "ERROR"})
    )
    publicar = respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert not publicar.called
    assert resultado.status is PostStatus.ERROR
    assert sondeo.call_count == 1
    assert 'is in ERROR status' in resultado.error


@respx.mock
def test_contenedor_expirado_corta_de_inmediato_en_vez_de_agotar_intentos(brand, post):
    """Hallazgo 3 (auditoría fix-meta-tiktok, 2026-09-07): antes de este
    arreglo, `EXPIRED` caía en la rama de "sigue sondeando" (el mismo camino
    que `IN_PROGRESS`), así que el bucle agotaba igualmente `self.intentos`
    -aquí, 3- antes de fallar con el mensaje GENÉRICO de timeout ("no
    terminó de procesarse a tiempo"), en vez de cortar en el primer sondeo
    con un mensaje que dice la causa real (EXPIRED, confirmado como estado
    terminal por Context7: "not published within 24 hours"). Con el
    arreglo, `sondeo.call_count` debe ser 1, no 3, y el mensaje debe mencionar
    EXPIRED de forma explícita, no el genérico de timeout.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    sondeo = respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "EXPIRED"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0, intentos=3).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert sondeo.call_count == 1
    assert "EXPIRED" in resultado.error
    assert "did not finish processing" not in resultado.error


@respx.mock
def test_contenedor_que_nunca_termina_da_error(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "IN_PROGRESS"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0, intentos=3).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "did not finish processing" in resultado.error


@respx.mock
def test_status_code_desconocido_se_sigue_sondeando_hasta_agotar_intentos(brand, post):
    """Un `status_code` que no sea ni FINISHED ni ERROR (aquí, uno que Meta no
    documenta hoy) debe tratarse igual que IN_PROGRESS: se sigue sondeando,
    no se interpreta como éxito ni como fallo inmediato.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    sondeo = respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "ALGO_NUEVO_DE_META"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0, intentos=3).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert sondeo.call_count == 3
    assert "did not finish processing" in resultado.error


@respx.mock
def test_el_sondeo_no_duerme_tras_el_ultimo_intento(brand, post, monkeypatch):
    """Con `intentos=3`, debe dormir como mucho 2 veces (entre intentos), no
    3: dormir tras el último intento agotado no sirve para nada y solo
    ralentiza cada publicación fallida.
    """
    import socialctl.adapters.instagram as modulo_instagram

    llamadas_sleep = []
    monkeypatch.setattr(modulo_instagram.time, "sleep", lambda s: llamadas_sleep.append(s))

    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "IN_PROGRESS"})
    )

    with httpx.Client() as client:
        InstagramAdapter(espera_s=2.5, intentos=3).publish(post, brand, client)

    assert llamadas_sleep == [2.5, 2.5]


@respx.mock
def test_sondeo_con_error_de_conexion_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(side_effect=httpx.ConnectError("Connection refused"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "connect" in resultado.error.lower()


@respx.mock
def test_sondeo_con_timeout_no_lanza_excepcion_y_da_mensaje_especifico(brand, post):
    """`httpx.TimeoutException` es subclase de `httpx.HTTPError`: si el
    `except httpx.HTTPError` estuviera antes (o fuera el único) en el
    sondeo, este caso caería ahí y el mensaje sería el genérico de "no se
    pudo conectar", nunca el de "tiempo de espera".
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(side_effect=httpx.TimeoutException("timed out"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "timed out" in resultado.error.lower()


@respx.mock
def test_sondeo_con_cuerpo_no_json_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(return_value=httpx.Response(200, text="esto no es json"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'invalid json response' in resultado.error.lower()


@respx.mock
def test_sondeo_sin_status_code_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    # Frase completa propia del `except KeyError` del sondeo: la subcadena
    # suelta "status_code" también podría colarse en un mensaje genérico
    # que simplemente listara el campo esperado, así que se comprueba la
    # frase entera que solo emite ese `except` en concreto.
    assert "returned no status_code" in resultado.error.lower()


@respx.mock
def test_sondeo_con_error_de_la_api_se_devuelve_como_resultado(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(400, json={"error": {"message": "contenedor no encontrado"}})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "contenedor no encontrado" in resultado.error


# --- Comprobación previa de la media (HEAD) -------------------------------


@respx.mock
def test_aborta_si_la_media_no_esta_subida_al_host(brand, post):
    respx.head(URL_CLIP).mock(return_value=httpx.Response(404))
    crear = respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(200, json={"id": "cont1"})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert not crear.called
    assert resultado.status is PostStatus.ERROR
    assert URL_CLIP in resultado.error
    assert "did not return HTTP 200" in resultado.error


@respx.mock
def test_fallo_de_conexion_comprobando_la_media_no_lanza_excepcion(brand, post):
    respx.head(URL_CLIP).mock(side_effect=httpx.ConnectError("Connection refused"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "accessible" in resultado.error.lower()


@respx.mock
def test_timeout_comprobando_la_media_no_lanza_excepcion_y_da_mensaje_especifico(brand, post):
    """Igual que en el sondeo: `httpx.TimeoutException` debe distinguirse del
    `httpx.HTTPError` genérico también en el HEAD previo.
    """
    respx.head(URL_CLIP).mock(side_effect=httpx.TimeoutException("timed out"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "timed out" in resultado.error.lower()


def test_media_url_base_con_caracter_invalido_produce_error_manejado(tmp_path, post):
    """Un `media_url_base` con un carácter no imprimible (p. ej. un salto de
    línea colado por un error de copia/pega) produce una URL que `httpx`
    rechaza con `httpx.InvalidURL` al construir el HEAD, antes de tocar la
    red. Esa excepción NO hereda de `httpx.HTTPError`, así que necesita su
    propio `except` explícito para no escapar de `publish()`.
    """
    b = crear_brand(tmp_path, "Rota")
    b.guardar_secreto(Platform.INSTAGRAM, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text(
        "instagram:\n  ig_user_id: '999'\n  media_url_base: \"https://cdn.example/hist\\nopast\"\n",
        encoding="utf-8",
    )
    brand_rota = cargar_brand(tmp_path, "Rota")
    assert "\n" in brand_rota.cuentas["instagram"]["media_url_base"]

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand_rota, client)

    assert resultado.status is PostStatus.ERROR
    assert "url" in resultado.error.lower()
    assert "invalid" in resultado.error.lower()


@respx.mock
def test_ig_user_id_con_caracter_invalido_produce_error_manejado(tmp_path, post):
    """Mismo caso que el anterior, pero con `ig_user_id`: el HEAD de
    comprobación de la media sí llega a hacerse (usa `media_url_base`, que
    aquí es válido), pero la URL de creación del contenedor
    (`GRAFO/{ig_user_id}/media`) es inválida.
    """
    b = crear_brand(tmp_path, "RotaId")
    b.guardar_secreto(Platform.INSTAGRAM, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text(
        "instagram:\n  ig_user_id: \"999\\n000\"\n"
        "  media_url_base: 'https://cdn.example/histopast'\n",
        encoding="utf-8",
    )
    brand_rota = cargar_brand(tmp_path, "RotaId")
    assert "\n" in brand_rota.cuentas["instagram"]["ig_user_id"]

    _media_publicada()

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand_rota, client)

    assert resultado.status is PostStatus.ERROR
    assert "url" in resultado.error.lower()
    assert "invalid" in resultado.error.lower()


# --- Creación del contenedor: errores de red / formato --------------------


@respx.mock
def test_fallo_de_conexion_creando_el_contenedor_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(side_effect=httpx.ConnectError("Connection refused"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "connect" in resultado.error.lower()


@respx.mock
def test_timeout_creando_el_contenedor_no_lanza_excepcion_y_da_mensaje_especifico(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(side_effect=httpx.TimeoutException("timed out"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "timed out" in resultado.error.lower()


@respx.mock
def test_creacion_con_error_de_la_api_se_devuelve_como_resultado(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(400, json={"error": {"message": "formato de video no admitido"}})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "formato de video no admitido" in resultado.error


@respx.mock
def test_creacion_con_cuerpo_no_json_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, text="esto no es json"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'invalid json response' in resultado.error.lower()


@respx.mock
def test_creacion_sin_id_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    # Frase completa propia del `except KeyError` de la creación del
    # contenedor, no la subcadena suelta "id": "id" también aparece por
    # accidente en "ha ocurr-id-o", el mensaje del resguardo genérico de
    # `publish()`, así que un test con solo "id" seguía en verde aunque se
    # eliminara el `except KeyError` real.
    assert 'returned no media container id' in resultado.error.lower()


@respx.mock
def test_contenedor_id_no_textual_da_error_claro(brand, post):
    """Hallazgo de revisión: un `id` de contenedor presente, con HTTP 200 y
    JSON válido, pero de un tipo que no es cadena de texto (aquí, un
    entero) no es cosmético como el permalink -sin un id de contenedor
    válido no hay forma de sondear su estado ni de publicar-, así que debe
    dar `ERROR` con un mensaje específico y accionable, en vez de dejar que
    ese valor siga circulando sin validar.

    Verificado por reversión: sin la comprobación `isinstance(contenedor_id,
    str)` en `_publicar()`, el código intentaría sondear
    `GET /{GRAFO}/12345` (la URL se construye igual con un int, por simple
    interpolación), una ruta que este test no mockea a propósito; `respx`
    responde con `AllMockedAssertionError` -que no hereda de
    `httpx.HTTPError`-, así que escapa de `_esperar_procesado()` y solo lo
    atrapa el resguardo genérico de `publish()`, dando
    `"ha ocurrido un error inesperado en Instagram (AllMockedAssertionError)"`
    en vez del mensaje específico que se comprueba aquí: la aserción de la
    frase completa falla en ese caso.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": 12345}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'unexpected media container ID' in resultado.error
    assert "ValidationError" not in resultado.error
    assert 'ha ocurrido un error inesperado in Instagram' not in resultado.error


# --- Publicación final: errores de red / formato ---------------------------


@respx.mock
def test_publicacion_con_error_de_la_api_se_devuelve_como_resultado(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(400, json={"error": {"message": "creation_id caducado"}})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "creation_id caducado" in resultado.error


@respx.mock
def test_fallo_de_conexion_publicando_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(side_effect=httpx.ConnectError("no conecta"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "connect" in resultado.error.lower()


@respx.mock
def test_timeout_publicando_no_lanza_excepcion_y_da_mensaje_especifico(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(side_effect=httpx.TimeoutException("timed out"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "timed out" in resultado.error.lower()


@respx.mock
def test_publicacion_con_cuerpo_no_json_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, text="esto no es json")
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'invalid json response' in resultado.error.lower()


@respx.mock
def test_publicacion_sin_id_no_lanza_excepcion(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'returned no post id' in resultado.error.lower()


@respx.mock
def test_post_id_no_textual_da_error_claro(brand, post):
    """Hallazgo de revisión: un `id` de publicación presente, con HTTP 200
    y JSON válido, pero de un tipo que no es cadena de texto (aquí, un
    entero) es un fallo real: en este punto Instagram YA aceptó la
    publicación (`media_publish` devolvió 200), pero sin un id de tipo
    correcto no se puede confirmar ni reportar esa publicación. Debe dar
    `ERROR` con un mensaje específico y accionable, no el resguardo
    genérico.

    Hallazgo de la última revisión: ese `ERROR` es indistinguible, por
    `status`, de cualquier otro fallo previo a publicar -y el proyecto
    tendrá más adelante un comando `retry` que vuelve a publicar
    precisamente lo que quedó en `ERROR`-. Pero aquí la publicación YA
    existe en Instagram; reintentar volvería a llamar a `media_publish`
    sobre contenido ya publicado, creando un duplicado visible en la
    cuenta. El mensaje, por tanto, no solo debe describir el tipo
    inesperado: debe decir que la publicación probablemente ya está hecha,
    advertir explícitamente de que reintentar puede duplicarla, y dar una
    acción concreta (comprobar la cuenta antes de volver a publicar) en vez
    de dejar que el `ERROR` invite implícitamente a reintentar como
    cualquier otro.

    Verificado por reversión: sin la comprobación `isinstance(post_id, str)`
    en `_publicar()`, el código llegaría a
    `PostResult(platform_id=post_id, ...)` con `post_id = 12345`; como
    `PostResult.platform_id` está tipado `str | None`, pydantic v2 no
    coacciona el entero y lanza `ValidationError` al construirlo, fuera de
    cualquier `try` propio de `_publicar()` -exactamente el hallazgo
    original, pero en `platform_id` en vez de en `url`-. Ese
    `ValidationError` solo lo atrapa el resguardo genérico de `publish()`,
    dando `"ha ocurrido un error inesperado en Instagram (ValidationError)"`
    en vez del mensaje específico que se comprueba aquí: todas las
    aserciones de este test fallan en ese caso (incluida la ausencia de
    "ValidationError").
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'unexpected post ID' in resultado.error
    # Frase distintiva que advierte de que la publicación probablemente ya
    # existe: ni aparece en el resguardo genérico
    # ("ha ocurrido un error inesperado en Instagram") ni en el nombre de
    # ninguna excepción implicada (ValidationError, AllMockedAssertionError).
    assert 'probably published on Instagram' in resultado.error
    # Advertencia explícita de que reintentar puede duplicar la publicación:
    # tampoco aparece en el resguardo genérico ni en esos nombres de excepción.
    assert "could duplicate it" in resultado.error
    assert 'Check the account before publishing again' in resultado.error
    assert "ValidationError" not in resultado.error
    assert 'ha ocurrido un error inesperado in Instagram' not in resultado.error
    # El campo estructurado que `retry` consulta para negarse a reintentar
    # esta red en automático (hallazgo de revisión: antes `retry` solo
    # miraba si la palabra "duplicar" aparecía en `error`, lo que un
    # adaptador futuro con otro wording no dispararía).
    assert resultado.riesgo_duplicado is True
    assert resultado.platform_id is None


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
    str)` en `_mensaje_de_error`, `resultado.error` es
    `"ha ocurrido un error inesperado en Instagram (ValidationError)"` en
    vez del mensaje de respaldo (con el cuerpo de la respuesta) que se
    comprueba aquí; las cuatro aserciones de más abajo fallan en ese caso.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(400, json={"error": {"message": 12345}})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert resultado.error is not None
    # El mensaje debe seguir el camino de respaldo de `_mensaje_de_error`
    # (el cuerpo de la respuesta, con el código HTTP), no el resguardo
    # genérico de `publish()` ni el nombre de la excepción que se
    # dispararía sin el arreglo.
    assert "HTTP 400" in resultado.error
    assert "12345" in resultado.error
    assert 'ha ocurrido un error inesperado in Instagram' not in resultado.error
    assert "ValidationError" not in resultado.error


# --- Hallazgo 1: permalink real tras publicar, con degradación con gracia --


@respx.mock
def test_fallo_http_pidiendo_el_permalink_degrada_a_url_none(brand, post):
    """La publicación ya tuvo éxito (media_publish devolvió 200): un error
    HTTP al pedir el permalink NUNCA convierte el resultado en ERROR. Se
    degrada a `url=None`, conservando `platform_id` y `status=PUBLICADO`.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )
    respx.get(f"{GRAFO}/post1").mock(
        return_value=httpx.Response(400, json={"error": {"message": "no autorizado"}})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"
    assert resultado.url is None
    assert resultado.error is None


@respx.mock
def test_permalink_sin_el_campo_degrada_a_url_none(brand, post):
    """Un cuerpo 200 que no trae el campo `permalink` (p. ej. porque Meta
    cambia el formato, o porque el token no tiene el permiso necesario para
    ese campo concreto) también degrada a `url=None`, nunca a ERROR.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )
    respx.get(f"{GRAFO}/post1").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"
    assert resultado.url is None
    assert resultado.error is None


@respx.mock
def test_permalink_con_cuerpo_no_json_degrada_a_url_none(brand, post):
    """Un cuerpo 200 que no es JSON válido (p. ej. una página de error HTML
    de un proxy intermedio) también degrada a `url=None`, nunca a ERROR.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )
    respx.get(f"{GRAFO}/post1").mock(return_value=httpx.Response(200, text="esto no es json"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"
    assert resultado.url is None
    assert resultado.error is None


@respx.mock
def test_excepcion_de_red_pidiendo_el_permalink_degrada_a_url_none(brand, post):
    """Una excepción de red (aquí, un `httpx.ConnectError`) al pedir el
    permalink también degrada a `url=None`, nunca a ERROR ni se propaga.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )
    respx.get(f"{GRAFO}/post1").mock(side_effect=httpx.ConnectError("Connection refused"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"
    assert resultado.url is None
    assert resultado.error is None


@respx.mock
def test_permalink_no_textual_degrada_a_url_none(brand, post):
    """Hallazgo de revisión: un cuerpo 200 con JSON válido y el campo
    `permalink` presente, pero de un tipo que no es cadena de texto (aquí,
    un entero, tal y como lo reprodujo el revisor) también degrada a
    `url=None`, nunca a `ERROR`: a diferencia de `post_id`/`contenedor_id`,
    un permalink con un tipo inesperado sigue siendo puramente cosmético.

    Verificado por reversión: sin la comprobación `isinstance(permalink,
    str)` en `_obtener_permalink()`, esa función devolvería `12345` tal
    cual (no lanza: un `return` normal, no una excepción), y `_publicar()`
    llegaría a `PostResult(url=12345, ...)`; como `PostResult.url` está
    tipado `str | None`, pydantic v2 no coacciona el entero y lanza
    `ValidationError` al construirlo, fuera de cualquier `try` propio de
    `_publicar()`. Ese `ValidationError` solo lo atrapa el resguardo
    genérico de `publish()`, convirtiendo esta publicación -que Instagram
    ya aceptó- en `ERROR` sin `platform_id`: la aserción de `status is
    PUBLICADO` falla en ese caso.
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )
    respx.get(f"{GRAFO}/post1").mock(return_value=httpx.Response(200, json={"permalink": 12345}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"
    assert resultado.url is None
    assert resultado.error is None


@respx.mock
def test_el_permalink_no_filtra_el_token_en_un_fallo_no_previsto(brand, post, monkeypatch):
    """Tercera superficie de fuga del token (además del cuerpo de la
    creación/publicación y el query param del sondeo): el permalink también
    lleva el token como parámetro de consulta. Un fallo no previsto aquí
    -aquí, una excepción cuyo propio mensaje vuelca la petición real, el
    peor caso posible- no debe filtrar el token en ningún campo del
    resultado, y -a diferencia del resguardo genérico de `publish()`, que sí
    convierte un fallo no previsto en ERROR- este debe seguir siendo
    PUBLICADO: la publicación ya tuvo éxito antes de llegar aquí.
    """
    token_reconocible = "TOKEN-CANARIO-PERMALINK-24681357"
    brand.guardar_secreto(
        Platform.INSTAGRAM,
        {"access_token": token_reconocible, "expira_en": time.time() + 3600},
    )

    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "post1"})
    )

    peticion_capturada: dict[str, httpx.Request] = {}
    get_original = httpx.Client.get

    def get_que_falla_solo_para_el_permalink(self, url, **kwargs):
        if str(url) == f"{GRAFO}/post1":
            peticion = httpx.Request("GET", url, params=kwargs.get("params"))
            peticion_capturada["valor"] = peticion
            raise RuntimeError(f"fallo interno; url real solicitada: {peticion.url}")
        return get_original(self, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", get_que_falla_solo_para_el_permalink)

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert resultado.platform_id == "post1"
    assert resultado.url is None
    assert resultado.error is None
    assert token_reconocible not in str(resultado)

    # La petición que provocó el fallo sí llevaba el token: si no lo llevara,
    # la aserción anterior sería trivial y no probaría nada.
    assert token_reconocible in str(peticion_capturada["valor"].url)


# --- Configuración y validaciones tempranas ---------------------------------


def test_sin_media_url_base_da_error_claro(tmp_path, post):
    b = crear_brand(tmp_path, "SinCDN")
    b.guardar_secreto(Platform.INSTAGRAM, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text("instagram:\n  ig_user_id: '999'\n", encoding="utf-8")
    brand = cargar_brand(tmp_path, "SinCDN")

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "media_url_base" in resultado.error


def test_sin_ig_user_id_da_error_claro(tmp_path, post):
    b = crear_brand(tmp_path, "SinIgUserId")
    b.guardar_secreto(Platform.INSTAGRAM, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text(
        "instagram:\n  media_url_base: 'https://cdn.example/histopast'\n", encoding="utf-8"
    )
    brand = cargar_brand(tmp_path, "SinIgUserId")

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "ig_user_id" in resultado.error


def test_sin_media_no_lanza_excepcion(brand, tmp_path):
    """`post.media[0]` con `media` vacía lanza `IndexError`: el adaptador
    debe comprobar la lista antes de indexarla y devolver un error claro,
    nunca dejar escapar el `IndexError`.
    """
    post_sin_media = PlatformPost(platform=Platform.INSTAGRAM, body="x", media=[])

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post_sin_media, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'no image or video' in resultado.error.lower()


def test_varios_archivos_fallan_antes_de_auth_o_http(brand, post):
    segundo = post.media[0].model_copy(
        update={"path": post.media[0].path.with_name("dos.mp4")}
    )
    post_con_dos = post.model_copy(update={"media": [post.media[0], segundo]})
    requests = []

    def no_admite_peticiones(request):
        requests.append(request)
        raise AssertionError("no debe haber peticiones HTTP")

    with httpx.Client(transport=httpx.MockTransport(no_admite_peticiones)) as client:
        resultado = InstagramAdapter(espera_s=0).publish(post_con_dos, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert '1 file' in resultado.error
    assert requests == []


def test_instanciable_sin_argumentos():
    """Convención de `ADAPTADORES[platform]()`: debe poder construirse sin
    argumentos, con valores por defecto razonables para `espera_s` e
    `intentos`.
    """
    adaptador = InstagramAdapter()
    assert adaptador.platform is Platform.INSTAGRAM
    assert adaptador.espera_s > 0
    assert adaptador.intentos > 0


# --- Hallazgo 1: ningún fallo no previsto puede escapar de publish() -------


@respx.mock
def test_fallo_inesperado_generico_no_escapa_de_publish(brand, post, monkeypatch):
    """Resguardo de última instancia: cualquier excepción no prevista (aquí,
    una que ningún `except` específico de `_publicar` está esperando) debe
    seguir devolviendo un `PostResult` de error, nunca propagarse.
    """
    import socialctl.adapters.instagram as modulo_instagram

    def explota(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(modulo_instagram, "componer_caption", explota)
    _media_publicada()

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "RuntimeError" in resultado.error


# --- Hallazgo 3 (seguridad): el token nunca debe aparecer en ningún mensaje -


@respx.mock
def test_error_de_conexion_creando_el_contenedor_no_filtra_el_token(tmp_path, post):
    """El token viaja en el CUERPO de la petición (`access_token`), igual que
    en Facebook: fácil de filtrar por accidente. Fija un token reconocible y
    comprueba que el mensaje de error de un fallo de conexión no lo
    contiene.
    """
    token_reconocible = "TOKEN-CANARIO-9f8e7d6c5b4a3210"
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.INSTAGRAM, {"access_token": token_reconocible, "expira_en": time.time() + 3600}
    )
    (b.raiz / "accounts.yml").write_text(
        "instagram:\n  ig_user_id: '999'\n  media_url_base: 'https://cdn.example/histopast'\n",
        encoding="utf-8",
    )
    brand_local = cargar_brand(tmp_path, "Histopast")

    _media_publicada()
    ruta = respx.post(f"{GRAFO}/999/media").mock(side_effect=httpx.ConnectError("Connection refused"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand_local, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in resultado.error

    # Confirma que la petición realmente llevaba el token en el cuerpo (si no
    # lo llevara, la aserción anterior sería trivial y no probaría nada).
    peticion_enviada = ruta.calls[0].request
    assert token_reconocible.encode() in peticion_enviada.content


@respx.mock
def test_resguardo_final_no_lanza_ni_filtra_el_token(brand, post, monkeypatch):
    """El resguardo genérico de `publish()` debe capturar cualquier excepción
    no prevista y jamás filtrar el token, ni siquiera cuando la propia
    excepción arrastra el cuerpo real de la petición (el peor caso posible,
    dado que aquí el token viaja en ese cuerpo).

    Se monkeypatchea `httpx.Client.post` para que construya la petición real
    (con el token ya codificado en el cuerpo, exactamente como lo haría
    `_publicar` al crear el contenedor) y lance una excepción cuyo propio
    mensaje incluye ese cuerpo -simulando el peor escenario: una librería de
    transporte que, en su mensaje de error, vuelca la petición que falló-.
    Si el resguardo genérico interpolase `str(exc)` a lo bruto, este test lo
    detectaría.
    """
    token_reconocible = "TOKEN-CANARIO-RESGUARDO-99887766"
    brand.guardar_secreto(
        Platform.INSTAGRAM,
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

    with respx.mock:
        _media_publicada()
        with httpx.Client() as client:
            resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "RuntimeError" in resultado.error
    assert token_reconocible not in resultado.error

    # La petición que provocó el fallo sí llevaba el token en el cuerpo: si
    # no lo llevara, la aserción anterior sería trivial y no probaría nada.
    assert token_reconocible.encode() in peticion_capturada["valor"].content


@respx.mock
def test_el_sondeo_no_filtra_el_token_en_un_fallo_no_previsto(brand, post, monkeypatch):
    """Durante el sondeo, el token viaja como parámetro de consulta (`GET
    .../{creation_id}?...&access_token=...`), no en el cuerpo: otra
    superficie distinta por la que podría filtrarse si algo no previsto
    fallase a mitad del bucle de sondeo.
    """
    token_reconocible = "TOKEN-CANARIO-SONDEO-13572468"
    brand.guardar_secreto(
        Platform.INSTAGRAM,
        {"access_token": token_reconocible, "expira_en": time.time() + 3600},
    )

    peticion_capturada: dict[str, httpx.Request] = {}

    def get_con_fallo_inesperado(self, url, **kwargs):
        peticion = httpx.Request("GET", url, params=kwargs.get("params"))
        peticion_capturada["valor"] = peticion
        raise RuntimeError(f"fallo interno; url real solicitada: {peticion.url}")

    monkeypatch.setattr(httpx.Client, "get", get_con_fallo_inesperado)

    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in resultado.error
    assert token_reconocible in str(peticion_capturada["valor"].url)


# --- Hallazgo 4 (auditoría fix-meta-tiktok, 2026-09-07): cadencia de sondeo --
# recomendada por Meta ("poll the container status once per minute for a
# maximum of 5 minutes", confirmado con Context7), no cada 5 segundos.


def test_la_cadencia_de_sondeo_por_defecto_seguido_la_recomendacion_de_meta():
    adaptador = InstagramAdapter()
    assert adaptador.espera_s == 60.0
    assert adaptador.intentos == 5


# --- Hallazgo 5 (auditoría fix-meta-tiktok, 2026-09-07): Graph API v21.0 -----
# caduca el 21 de enero de 2027 y ya iba 5 versiones por detrás de la
# vigente (v26.0, confirmado con Context7 contra el changelog de Meta).


def test_usa_la_version_vigente_de_la_graph_api():
    assert GRAFO_DEL_ADAPTADOR == "https://graph.facebook.com/v26.0"


# --- Hallazgo C1 (CRÍTICO, revisión final): un proxy que refleje la URL del
# sondeo (token de query incluido) no debe poder colar el token en el
# mensaje de error. Reproduce exactamente el escenario del informe: un 502
# de un intermediario cuyo cuerpo (HTML, no JSON) repite la URL pedida.


@respx.mock
def test_sondeo_502_que_refleja_la_url_no_filtra_el_token(brand, post):
    token_reconocible = "TOKEN-CANARIO-PROXY-24681357913579"
    brand.guardar_secreto(
        Platform.INSTAGRAM,
        {"access_token": token_reconocible, "expira_en": time.time() + 3600},
    )

    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))

    def _proxy_que_refleja_la_url(request):
        return httpx.Response(
            502, text=f"<html>Bad gateway for {request.url}</html>"
        )

    sondeo = respx.get(f"{GRAFO}/cont1").mock(side_effect=_proxy_que_refleja_la_url)

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in (resultado.error or "")

    # La petición real sí llevaba el token en la query (si no lo llevara, la
    # aserción de arriba sería trivial y no probaría nada): confirma que el
    # cuerpo de la respuesta reflejaba de verdad una URL con el token.
    peticion_real = sondeo.calls[0].request
    assert token_reconocible in str(peticion_real.url)
    # Y el propio cuerpo devuelto por el "proxy" (antes de redactar) sí lo
    # contenía: si no, tampoco probaría que la redacción hizo algo.
    assert token_reconocible in f"<html>Bad gateway for {peticion_real.url}</html>"


@respx.mock
def test_error_de_creacion_con_json_sin_forma_de_error_no_filtra_el_token(brand, post):
    """Cuerpo JSON pero SIN la forma ``{"error": {"message": ...}}`` -aquí,
    un diagnóstico de un WAF que ecoa parte del cuerpo de la petición
    fallida- tampoco debe poder colar el token.
    """
    token_reconocible = "TOKEN-CANARIO-WAF-13579246813579"
    brand.guardar_secreto(
        Platform.INSTAGRAM,
        {"access_token": token_reconocible, "expira_en": time.time() + 3600},
    )

    _media_publicada()

    def _waf_que_refleja_el_cuerpo(request):
        return httpx.Response(
            400,
            json={"blocked_by": "waf", "debug_echo": request.content.decode()},
        )

    creacion = respx.post(f"{GRAFO}/999/media").mock(side_effect=_waf_que_refleja_el_cuerpo)

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in (resultado.error or "")

    peticion_real = creacion.calls[0].request
    assert token_reconocible.encode() in peticion_real.content


# --- Hallazgo I1 (revisión final): `riesgo_duplicado` solo cubría 1 de los 5
# caminos posibles tras `media_publish` -el más probable de todos, un
# timeout, se quedaba sin marcar-. Ahora los 4 caminos restantes también lo
# marcan: si `media_publish` respondió, o si la petición pudo haber llegado
# antes de perderse la respuesta, Instagram pudo haber aceptado ya el
# contenido.


@respx.mock
def test_timeout_publicando_marca_riesgo_duplicado(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(side_effect=httpx.TimeoutException("timed out"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "timed out" in resultado.error.lower()
    assert resultado.riesgo_duplicado is True


@respx.mock
def test_fallo_de_conexion_publicando_marca_riesgo_duplicado(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(side_effect=httpx.ConnectError("no conecta"))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "connect" in resultado.error.lower()
    assert resultado.riesgo_duplicado is True


@respx.mock
def test_publicacion_con_cuerpo_no_json_marca_riesgo_duplicado(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(
        return_value=httpx.Response(200, text="esto no es json")
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'invalid json response' in resultado.error.lower()
    assert resultado.riesgo_duplicado is True


@respx.mock
def test_publicacion_sin_id_marca_riesgo_duplicado(brand, post):
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(return_value=httpx.Response(200, json={"id": "cont1"}))
    respx.get(f"{GRAFO}/cont1").mock(
        return_value=httpx.Response(200, json={"status_code": "FINISHED"})
    )
    respx.post(f"{GRAFO}/999/media_publish").mock(return_value=httpx.Response(200, json={"foo": "bar"}))

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert 'returned no post id' in resultado.error.lower()
    assert resultado.riesgo_duplicado is True


def test_riesgo_duplicado_sobrevive_a_resultado_json(post, tmp_path):
    """El campo debe seguir siendo `True` tras el viaje por `resultado.json`
    (`guardar_resultado` lo serializa; ver también `tests/test_publisher.py`
    para el mismo tipo de comprobación con el resto de adaptadores).
    """
    from socialctl.brands import cargar_brand
    from socialctl.models import CampaignType, Post, PostResult
    from socialctl.publisher import guardar_resultado

    b = crear_brand(tmp_path, "Histopast")
    (b.raiz / "accounts.yml").write_text(
        "facebook:\n  page_id: '12345'\n", encoding="utf-8"
    )
    brand = cargar_brand(tmp_path, "Histopast")

    resultado = PostResult(
        platform=Platform.INSTAGRAM,
        status=PostStatus.ERROR,
        error="se agotó el tiempo de espera publicando en Instagram",
        riesgo_duplicado=True,
    )
    post_completo = Post(
        slug="2026-09-07-riesgo",
        brand="Histopast",
        campaign=CampaignType.CLIP_VERTICAL,
        platforms={Platform.INSTAGRAM: post},
    )

    destino = guardar_resultado(post_completo, brand, [resultado])

    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["resultados"][0]["riesgo_duplicado"] is True


# --- Hallazgo I4 (revisión final): la falta de `ig_user_id`/`media_url_base`
# solo se comprobaba al publicar; ahora también en `validate()`, para que
# aparezca en el preview antes de la aprobación.


def test_validate_detecta_falta_de_ig_user_id(tmp_path, post):
    b = crear_brand(tmp_path, "SinIgUserIdValidate")
    (b.raiz / "accounts.yml").write_text(
        "instagram:\n  media_url_base: 'https://cdn.example/histopast'\n",
        encoding="utf-8",
    )
    brand_sin_id = cargar_brand(tmp_path, "SinIgUserIdValidate")

    errores = InstagramAdapter().validate(post, brand_sin_id)

    assert any("ig_user_id" in e.motivo for e in errores)


def test_validate_detecta_falta_de_media_url_base(tmp_path, post):
    b = crear_brand(tmp_path, "SinCDNValidate")
    (b.raiz / "accounts.yml").write_text("instagram:\n  ig_user_id: '999'\n", encoding="utf-8")
    brand_sin_cdn = cargar_brand(tmp_path, "SinCDNValidate")

    errores = InstagramAdapter().validate(post, brand_sin_cdn)

    assert any("media_url_base" in e.motivo for e in errores)


def test_validate_sin_problemas_de_cuenta_cuando_esta_bien_configurada(brand, post):
    errores = InstagramAdapter().validate(post, brand)

    assert not any(e.campo == "cuenta" for e in errores)


@respx.mock
def test_message_vacio_no_da_un_error_sin_motivo(brand, post):
    """Hallazgo I6 (revisión final): igual que en youtube.py y facebook.py,
    `message: ""` daba antes `PostResult(error="")` -un "error" sin ningún
    motivo-. Unificado con el criterio de TikTok (rechazar la cadena
    vacía).
    """
    _media_publicada()
    respx.post(f"{GRAFO}/999/media").mock(
        return_value=httpx.Response(400, json={"error": {"message": ""}})
    )

    with httpx.Client() as client:
        resultado = InstagramAdapter(espera_s=0).publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert resultado.error
    assert "HTTP 400" in resultado.error
