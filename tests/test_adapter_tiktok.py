import json
import time

import httpx
import pytest
import respx

from socialctl.adapters.tiktok import (
    CHUNK_MAX_BYTES,
    CHUNK_MIN_BYTES,
    TOTAL_CHUNKS_MAXIMO,
    AuditoriaRequerida,
    ChunkingImposible,
    TikTokAdapter,
    _calcular_chunking,
)
from socialctl.brands import cargar_brand, crear_brand
from socialctl.models import MediaAsset, MediaKind, Platform, PlatformPost, PostStatus

API = "https://open.tiktokapis.com/v2/post/publish"

TOKEN_RECONOCIBLE = "TOKEN_SECRETO_UNICO_9f8a7b6c5d"


def _brand(tmp_path, mode: str, auditada: bool, token: str = "t"):
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.TIKTOK, {"access_token": token, "expira_en": time.time() + 3600}
    )
    (b.raiz / "accounts.yml").write_text(
        f"tiktok:\n  open_id: 'oid'\n  mode: {mode}\n  auditada: {str(auditada).lower()}\n",
        encoding="utf-8",
    )
    return cargar_brand(tmp_path, "Histopast")


@pytest.fixture
def post(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"bytes-de-video")
    return PlatformPost(
        platform=Platform.TIKTOK,
        body="La primera ciudad",
        hashtags=["historia"],
        media=[
            MediaAsset(
                path=video,
                kind=MediaKind.VIDEO,
                width=1080,
                height=1920,
                duration_s=45.0,
                size_bytes=14,
            )
        ],
    )


def _mock_creator_info(
    *,
    privacy_level_options=("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"),
    max_video_post_duration_sec=300,
):
    """Registra el mock de ``creator_info/query/`` que exige el modo direct
    (ver `_resolver_privacidad_y_duracion` en `tiktok.py`). Por defecto
    admite `PUBLIC_TO_EVERYONE` y una duración máxima amplia (300s, el
    mismo valor del ejemplo oficial de la doc de TikTok), para no romper
    los tests que no están probando específicamente estos límites.
    """
    return respx.post(f"{API}/creator_info/query/").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "creator_username": "histopast",
                    "creator_nickname": "Histopast",
                    "privacy_level_options": list(privacy_level_options),
                    "comment_disabled": False,
                    "duet_disabled": False,
                    "stitch_disabled": False,
                    "max_video_post_duration_sec": max_video_post_duration_sec,
                },
                "error": {"code": "ok", "message": "", "log_id": "x"},
            },
        )
    )


# --- Los dos modos y la salvaguarda de auditoría ---------------------------


@respx.mock
def test_modo_inbox_deja_el_post_pendiente_de_confirmacion(tmp_path, post):
    brand = _brand(tmp_path, "inbox", auditada=False)
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    respx.put("https://upload.example/x").mock(return_value=httpx.Response(201))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.PENDIENTE_CONFIRMACION
    assert resultado.platform_id == "pid"
    assert "app de TikTok" in resultado.error


@respx.mock
def test_modo_direct_con_auditoria_publica(tmp_path, post):
    brand = _brand(tmp_path, "direct", auditada=True)
    creator_info = _mock_creator_info()
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    respx.put("https://upload.example/x").mock(return_value=httpx.Response(201))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert creator_info.called
    cuerpo = json.loads(init.calls[0].request.content)
    assert cuerpo["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert "#historia" in cuerpo["post_info"]["title"]
    # Con un vídeo de 14 bytes (muy por debajo de 5 MB), debe subirse de una
    # sola pieza: chunk_size == video_size y total_chunk_count == 1.
    assert cuerpo["source_info"]["video_size"] == 14
    assert cuerpo["source_info"]["chunk_size"] == 14
    assert cuerpo["source_info"]["total_chunk_count"] == 1


# --- Hallazgo 2 (auditoría fix-meta-tiktok, 2026-09-07): privacy_level ------
# fijo sin consultar creator_info/query/. La cuenta puede no admitir
# PUBLIC_TO_EVERYONE (cuenta privada, de un menor, etc.): en ese caso el
# comportamiento correcto es NO publicar, nunca degradar en silencio a otro
# nivel de privacidad.


@respx.mock
def test_modo_direct_llama_a_creator_info_antes_de_video_init(tmp_path, post):
    """La consulta a creator_info/query/ es obligatoria en modo direct
    (Content Posting API Reference - Query Creator Info, confirmado con
    Context7) y debe hacerse ANTES de video/init/, no en paralelo ni
    después. Verificado por el orden real de llamadas capturado por respx,
    no solo por que ambas se hayan llamado.
    """
    brand = _brand(tmp_path, "direct", auditada=True)
    creator_info = _mock_creator_info()
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    respx.put("https://upload.example/x").mock(return_value=httpx.Response(201))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.PUBLICADO
    assert creator_info.called and init.called
    # `respx.calls` registra las peticiones en el orden real en que se
    # hicieron: confirma que creator_info se llamó ANTES que video/init/,
    # no solo que ambas se llamaron.
    urls_en_orden = [str(llamada.request.url) for llamada in respx.calls]
    assert urls_en_orden.index(f"{API}/creator_info/query/") < urls_en_orden.index(
        f"{API}/video/init/"
    )


@respx.mock
def test_modo_direct_sin_privacy_level_admitido_no_publica(tmp_path, post):
    brand = _brand(tmp_path, "direct", auditada=True)
    creator_info = _mock_creator_info(
        privacy_level_options=["MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
    )
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert creator_info.called
    # El punto central del hallazgo: si la cuenta no admite el nivel
    # esperado, NUNCA se llama a video/init/ con otro nivel distinto -se
    # aborta sin publicar-.
    assert not init.called
    assert resultado.status is PostStatus.ERROR
    assert "no admite" in resultado.error
    assert "PUBLIC_TO_EVERYONE" in resultado.error
    assert "MUTUAL_FOLLOW_FRIENDS" in resultado.error


@respx.mock
def test_modo_direct_con_duracion_mayor_que_el_maximo_del_creador_no_publica(
    tmp_path, post
):
    # El vídeo del fixture `post` dura 45s; si creator_info dice que esta
    # cuenta admite, como mucho, 30s para un post directo (un límite por
    # creador que puede ser menor que el estático de PLATFORM_SPECS, ver
    # docstring de _resolver_privacidad_y_duracion), debe abortar sin
    # publicar en vez de dejar que TikTok lo rechace después de subir los
    # bytes.
    brand = _brand(tmp_path, "direct", auditada=True)
    creator_info = _mock_creator_info(max_video_post_duration_sec=30)
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert creator_info.called
    assert not init.called
    assert resultado.status is PostStatus.ERROR
    assert "45" in resultado.error
    assert "30" in resultado.error


@respx.mock
def test_modo_direct_con_creator_info_caido_no_publica_y_no_lanza(tmp_path, post):
    brand = _brand(tmp_path, "direct", auditada=True)
    respx.post(f"{API}/creator_info/query/").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert not init.called
    assert resultado.status is PostStatus.ERROR
    assert "creator_info" in resultado.error


@respx.mock
def test_modo_inbox_no_llama_a_creator_info(tmp_path, post):
    # creator_info/query/ solo es obligatoria en modo direct (ver docstring
    # del módulo): en modo inbox no debe llamarse.
    brand = _brand(tmp_path, "inbox", auditada=False)
    creator_info = respx.post(f"{API}/creator_info/query/").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    respx.put("https://upload.example/x").mock(return_value=httpx.Response(201))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.PENDIENTE_CONFIRMACION
    assert not creator_info.called


def test_varios_archivos_fallan_antes_de_auth_o_http(tmp_path, post):
    brand = _brand(tmp_path, "inbox", auditada=False)
    segundo = post.media[0].model_copy(
        update={"path": post.media[0].path.with_name("dos.mp4")}
    )
    post_con_dos = post.model_copy(update={"media": [post.media[0], segundo]})
    requests = []

    def no_admite_peticiones(request):
        requests.append(request)
        raise AssertionError("no debe haber peticiones HTTP")

    with httpx.Client(transport=httpx.MockTransport(no_admite_peticiones)) as client:
        resultado = TikTokAdapter().publish(post_con_dos, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "1 archivo" in resultado.error
    assert requests == []


@respx.mock
def test_modo_direct_sin_auditoria_aborta_sin_llamar_a_la_api(tmp_path, post):
    brand = _brand(tmp_path, "direct", auditada=False)
    creator_info = _mock_creator_info()
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    # La salvaguarda de auditoría (intocable) debe seguir actuando ANTES de
    # cualquier llamada de red, incluida la nueva consulta a creator_info:
    # ni siquiera esa consulta -de por sí inocua- debe hacerse si la app no
    # está auditada.
    assert not creator_info.called
    assert not init.called
    assert resultado.status is PostStatus.ERROR
    assert "auditada" in resultado.error
    assert "privado" in resultado.error


def test_la_salvaguarda_de_auditoria_es_una_excepcion_propia():
    # AuditoriaRequerida forma parte de la interfaz que pide el brief: debe
    # existir como excepción propia, no reutilizar Exception a pelo.
    assert issubclass(AuditoriaRequerida, Exception)


def _brand_direct_con_auditada(tmp_path, linea_auditada: str, token: str = "t"):
    """Como ``_brand``, pero deja escribir la línea de ``auditada`` tal
    cual va a aparecer en ``accounts.yml`` (para poder colar valores como
    cadenas entrecomilladas, enteros o ``null`` que ``yaml.safe_load``
    deserializa de forma distinta a un booleano de Python)."""
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.TIKTOK, {"access_token": token, "expira_en": time.time() + 3600}
    )
    (b.raiz / "accounts.yml").write_text(
        f"tiktok:\n  open_id: 'oid'\n  mode: direct\n  {linea_auditada}\n",
        encoding="utf-8",
    )
    return cargar_brand(tmp_path, "Histopast")


# Hallazgo 1 (CRÍTICO) de la revisión de la Task 10: `bool(cuenta.get(
# "auditada", False))` se saltaba la salvaguarda cuando `auditada` venía
# entrecomillada en accounts.yml ("false", "no", ...), porque yaml.safe_load
# la deserializa como CADENA no vacía, y bool() de cualquier cadena no
# vacía es True en Python. Solo "true" (booleano o cadena, sin distinguir
# mayúsculas ni espacios alrededor) debe dejar pasar la publicación; todo
# lo demás -incluidos "false", "no", "si", "yes", 1, "1", 0, None y la
# clave ausente- debe abortar ANTES de llamar a la API.
CASOS_AUDITADA = [
    pytest.param('auditada: "false"', False, id="cadena_false_entrecomillada"),
    pytest.param('auditada: "no"', False, id="cadena_no_entrecomillada"),
    pytest.param('auditada: "si"', False, id="cadena_si_entrecomillada"),
    pytest.param('auditada: "yes"', False, id="cadena_yes_entrecomillada"),
    pytest.param('auditada: "true"', True, id="cadena_true_entrecomillada"),
    pytest.param('auditada: "True"', True, id="cadena_True_mayuscula"),
    pytest.param('auditada: " true "', True, id="cadena_true_con_espacios"),
    pytest.param("auditada: true", True, id="booleano_true"),
    pytest.param("auditada: false", False, id="booleano_false"),
    pytest.param('auditada: "1"', False, id="cadena_1"),
    pytest.param("auditada: 1", False, id="entero_1"),
    pytest.param("auditada: 0", False, id="entero_0"),
    pytest.param("auditada: null", False, id="null_explicito"),
    pytest.param("", False, id="clave_ausente"),
]


@respx.mock
@pytest.mark.parametrize("linea_auditada, se_espera_publicar", CASOS_AUDITADA)
def test_interpretacion_estricta_de_auditada(
    tmp_path, post, linea_auditada, se_espera_publicar
):
    brand = _brand_direct_con_auditada(tmp_path, linea_auditada)
    _mock_creator_info()
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    respx.put("https://upload.example/x").mock(return_value=httpx.Response(201))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    if se_espera_publicar:
        assert init.called
        assert resultado.status is PostStatus.PUBLICADO
    else:
        # El punto central del hallazgo 1: si el valor no es
        # inequívocamente verdadero, la API NUNCA debe llegar a llamarse.
        assert not init.called
        assert resultado.status is PostStatus.ERROR
        assert "auditada" in resultado.error


@respx.mock
def test_auditada_con_valor_no_reconocido_da_un_mensaje_distinto_de_no_auditada(
    tmp_path, post
):
    # "si" no es ni la forma canónica "true"/"false" ni un booleano de
    # Python: probablemente sea un error de tecleo (alguien que pensó que
    # "si" en español cuenta como verdadero), no una cuenta deliberadamente
    # no auditada. El mensaje debe distinguir ambos casos para que quien
    # configuró la cuenta note el error.
    brand_no_reconocido = _brand_direct_con_auditada(tmp_path, 'auditada: "si"')
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    with httpx.Client() as client:
        resultado_no_reconocido = TikTokAdapter().publish(post, brand_no_reconocido, client)
    assert not init.called
    assert resultado_no_reconocido.status is PostStatus.ERROR
    assert "no se reconoce" in resultado_no_reconocido.error


@respx.mock
def test_auditada_false_canonico_da_el_mensaje_estandar_de_no_auditada(tmp_path, post):
    brand_reconocido = _brand_direct_con_auditada(tmp_path, "auditada: false")
    init = respx.post(f"{API}/video/init/").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    with httpx.Client() as client:
        resultado_reconocido = TikTokAdapter().publish(post, brand_reconocido, client)
    assert not init.called
    assert resultado_reconocido.status is PostStatus.ERROR
    assert "no se reconoce" not in resultado_reconocido.error
    assert "no está auditada" in resultado_reconocido.error


@respx.mock
def test_fallo_en_la_subida_se_reporta(tmp_path, post):
    brand = _brand(tmp_path, "inbox", auditada=False)
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    respx.put("https://upload.example/x").mock(return_value=httpx.Response(500))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "chunk" in resultado.error


def test_modo_desconocido_da_error(tmp_path, post):
    brand = _brand(tmp_path, "sarasa", auditada=True)
    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)
    assert resultado.status is PostStatus.ERROR
    assert "sarasa" in resultado.error


def test_falta_open_id_da_error(tmp_path, post):
    brand = crear_brand(tmp_path, "Histopast")
    brand.guardar_secreto(
        Platform.TIKTOK, {"access_token": "t", "expira_en": time.time() + 3600}
    )
    (brand.raiz / "accounts.yml").write_text(
        "tiktok:\n  mode: inbox\n  auditada: false\n", encoding="utf-8"
    )
    brand = cargar_brand(tmp_path, "Histopast")
    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)
    assert resultado.status is PostStatus.ERROR
    assert "open_id" in resultado.error


# --- Registro de los cuatro adaptadores -------------------------------------


def test_hay_un_adaptador_registrado_por_cada_red():
    from socialctl.adapters.base import ADAPTADORES
    import socialctl.adapters  # noqa: F401  (registra los cuatro)

    assert set(ADAPTADORES) == set(Platform)


# --- El chunking real: el corazón de esta tarea -----------------------------


def test_chunking_fichero_pequeno_es_una_sola_pieza():
    assert _calcular_chunking(14) == (14, 1)


def test_chunking_justo_por_debajo_del_maximo_de_chunk_es_una_sola_pieza():
    assert _calcular_chunking(CHUNK_MAX_BYTES) == (CHUNK_MAX_BYTES, 1)


def test_chunking_fichero_mayor_que_el_maximo_de_chunk_necesita_varios():
    # Hallazgo 2 de la revisión de la Task 10: la implementación anterior
    # colapsaba a un solo chunk (con el fichero entero) cuando el resto
    # quedaba por debajo del mínimo de 5 MB, incumpliendo la regla de
    # TikTok de que "los ficheros de más de 64 MB requieren múltiples
    # chunks". Este test YA NO acepta ese colapso como válido: exige
    # varios chunks, ambos de tamaño reglamentario.
    tamano = CHUNK_MAX_BYTES + 1
    chunk_size, total = _calcular_chunking(tamano)
    assert total > 1
    ultimo = tamano - chunk_size * (total - 1)
    assert CHUNK_MIN_BYTES <= chunk_size <= CHUNK_MAX_BYTES
    assert 0 < ultimo <= 128 * 1024 * 1024
    assert chunk_size * (total - 1) + ultimo == tamano


def test_chunking_ventana_64_a_69_mb_nunca_colapsa_a_un_solo_chunk():
    # La ventana exacta señalada en el hallazgo 2: para tamaños entre
    # 64 MB + 1 byte y unos 69 MB, dos chunks de la misma medida (p. ej.
    # 33 MB + 33 MB para un fichero de 66 MB) son ambos válidos -entre 5 y
    # 64 MB-, así que colapsar a una sola pieza nunca hace falta.
    for delta in range(1, CHUNK_MIN_BYTES + 2):
        tamano = CHUNK_MAX_BYTES + delta
        chunk_size, total = _calcular_chunking(tamano)
        assert total > 1, (tamano, chunk_size, total)
        assert CHUNK_MIN_BYTES <= chunk_size <= CHUNK_MAX_BYTES, (tamano, chunk_size, total)
        ultimo = tamano - chunk_size * (total - 1)
        assert 0 < ultimo <= 128 * 1024 * 1024, (tamano, chunk_size, total, ultimo)
        assert chunk_size * (total - 1) + ultimo == tamano


def test_chunking_barrido_de_tamanos_frontera_cumple_todas_las_invariantes():
    """Barrido aritmético (no de lectura del código) de las invariantes de
    chunking exigidas por el Media Transfer Guide de TikTok (confirmado
    con Context7): cada chunk regular mide entre 5 y 64 MB; el último no
    supera los 128 MB; los ficheros de más de 64 MB siempre se reparten en
    varios chunks; los de 64 MB o menos van en una sola pieza; nunca hacen
    falta más de 1000 chunks; y los Content-Range cubren el fichero
    completo sin solapes ni huecos (la suma de todos los chunks es,
    exactamente, el tamaño del fichero).
    """
    MB = 1024 * 1024
    GB = 1024**3

    tamanos = set()
    # Justo por debajo/encima del mínimo y del máximo de chunk.
    for frontera in (CHUNK_MIN_BYTES, CHUNK_MAX_BYTES):
        tamanos.update({frontera - 1, frontera, frontera + 1})
    # Múltiplos exactos de 64 MB, y justo alrededor.
    for k in range(1, 12):
        tamanos.update(
            {CHUNK_MAX_BYTES * k - 1, CHUNK_MAX_BYTES * k, CHUNK_MAX_BYTES * k + 1}
        )
    # La ventana de 64-69 MB completa, byte a byte: es donde colapsaba la
    # implementación anterior (hallazgo 2).
    tamanos.update(range(CHUNK_MAX_BYTES, CHUNK_MAX_BYTES + 5 * MB + 2))
    # El entorno del límite de 1000 chunks.
    for k in (997, 998, 999, 1000, 1001, 1002):
        for delta in (-CHUNK_MIN_BYTES, -1, 0, 1, CHUNK_MIN_BYTES):
            tamanos.add(CHUNK_MAX_BYTES * k + delta)
    # Tamaños grandes representativos (documentales de varios GB).
    tamanos.update({gb * GB for gb in (1, 2, 4, 10, 30, 60)})
    tamanos = {t for t in tamanos if t >= 1}

    for tamano in sorted(tamanos):
        try:
            chunk_size, total = _calcular_chunking(tamano)
        except ChunkingImposible:
            # Solo es válido lanzar el error si de verdad no cabe en 1000
            # chunks de, como mucho, 64 MB cada uno.
            minimo_necesario = -(-tamano // CHUNK_MAX_BYTES)
            assert minimo_necesario > TOTAL_CHUNKS_MAXIMO, tamano
            continue

        assert 1 <= total <= TOTAL_CHUNKS_MAXIMO, (tamano, chunk_size, total)

        if tamano <= CHUNK_MAX_BYTES:
            assert total == 1, (tamano, chunk_size, total)
            assert chunk_size == tamano, (tamano, chunk_size, total)
            continue

        # Ficheros de más de 64 MB: SIEMPRE varios chunks.
        assert total > 1, (tamano, chunk_size, total)
        assert CHUNK_MIN_BYTES <= chunk_size <= CHUNK_MAX_BYTES, (tamano, chunk_size, total)
        ultimo = tamano - chunk_size * (total - 1)
        assert 0 < ultimo <= 128 * 1024 * 1024, (tamano, chunk_size, total, ultimo)
        # Cobertura exacta del fichero completo: ni huecos ni solapes.
        assert chunk_size * (total - 1) + ultimo == tamano, (tamano, chunk_size, total)


def test_chunking_fichero_grande_reparte_en_varios_chunks_de_tamano_valido():
    # ~130 MB: no cabe en un solo chunk de 64 MB, así que hacen falta varios.
    tamano = 130 * 1024 * 1024
    chunk_size, total = _calcular_chunking(tamano)

    assert total > 1
    assert CHUNK_MIN_BYTES <= chunk_size <= CHUNK_MAX_BYTES

    ultimo = tamano - chunk_size * (total - 1)
    assert 0 < ultimo <= 128 * 1024 * 1024
    # Todos los chunks (incluido el último) deben sumar exactamente el
    # tamaño del fichero: ni de más ni de menos.
    assert chunk_size * (total - 1) + ultimo == tamano


def test_chunking_no_supera_nunca_mil_chunks_para_tamanos_representables():
    # Barrido de tamaños "de verdad" (hasta unos 60 GB) para comprobar que el
    # cálculo nunca pide más de 1000 chunks ni deja restos fuera de rango.
    for gb in (1, 2, 4, 10, 30, 60):
        tamano = gb * 1024**3
        chunk_size, total = _calcular_chunking(tamano)
        assert total <= TOTAL_CHUNKS_MAXIMO
        ultimo = tamano - chunk_size * (total - 1)
        assert 0 < ultimo <= 128 * 1024 * 1024
        assert chunk_size * (total - 1) + ultimo == tamano


def test_chunking_fichero_demasiado_grande_da_un_error_claro_sin_intentarlo():
    # Más de 1000 chunks * 64 MB (+ el margen del último): no cabe.
    tamano = (TOTAL_CHUNKS_MAXIMO + 500) * CHUNK_MAX_BYTES
    with pytest.raises(ChunkingImposible, match="1.000|1000"):
        _calcular_chunking(tamano)


def _post_con_video(video_path, contenido: bytes) -> PlatformPost:
    video_path.write_bytes(contenido)
    return PlatformPost(
        platform=Platform.TIKTOK,
        body="La primera ciudad",
        hashtags=["historia"],
        media=[
            MediaAsset(
                path=video_path,
                kind=MediaKind.VIDEO,
                width=1080,
                height=1920,
                duration_s=45.0,
                size_bytes=len(contenido),
            )
        ],
    )


@respx.mock
def test_la_subida_multi_chunk_manda_los_content_range_correctos_en_secuencia(
    tmp_path, monkeypatch
):
    # Umbrales pequeños para poder ejercitar el camino multi-chunk con un
    # fichero de prueba minúsculo, en vez de escribir decenas de MB de
    # verdad: el algoritmo de chunking es el mismo, solo cambian las
    # constantes que lo parametrizan.
    monkeypatch.setattr("socialctl.adapters.tiktok.CHUNK_MAX_BYTES", 10)
    monkeypatch.setattr("socialctl.adapters.tiktok.CHUNK_MIN_BYTES", 3)

    contenido = bytes(range(1, 26))  # 25 bytes, únicos y ordenados
    post = _post_con_video(tmp_path / "grande.mp4", contenido)

    brand = _brand(tmp_path, "inbox", auditada=False)
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    subida = respx.put("https://upload.example/x").mock(
        return_value=httpx.Response(206)
    )

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    # 25 bytes con chunk_size=10 -> chunks de 10, 10 y 5 (3 llamadas).
    assert len(subida.calls) == 3
    reconstruido = b""
    fin_total = None
    for i, llamada in enumerate(subida.calls):
        rango = llamada.request.headers["Content-Range"]
        assert rango.startswith("bytes ")
        cuerpo = rango[len("bytes "):]
        limites, total = cuerpo.split("/")
        inicio_s, fin_s = limites.split("-")
        assert int(total) == 25
        assert int(llamada.request.headers["Content-Length"]) == len(llamada.request.content)
        reconstruido += llamada.request.content
        fin_total = int(fin_s)

    assert reconstruido == contenido
    assert fin_total == 24  # último byte, índice 0-based de 25 bytes
    # El adaptador acepta 200/201/206 en cualquier chunk (no distingue el
    # código exacto del último chunk): lo que importa es que los tres se
    # hayan mandado, en orden, con los rangos correctos.
    assert resultado.status is PostStatus.PENDIENTE_CONFIRMACION


@respx.mock
def test_un_chunk_fallido_corta_la_subida_e_indica_cual_fallo(tmp_path, monkeypatch):
    monkeypatch.setattr("socialctl.adapters.tiktok.CHUNK_MAX_BYTES", 10)
    monkeypatch.setattr("socialctl.adapters.tiktok.CHUNK_MIN_BYTES", 3)

    contenido = bytes(range(1, 26))  # 25 bytes -> 3 chunks (10, 10, 5)
    post = _post_con_video(tmp_path / "grande.mp4", contenido)

    brand = _brand(tmp_path, "inbox", auditada=False)
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )

    llamadas = {"n": 0}

    def _responder(request):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            return httpx.Response(500)
        return httpx.Response(206)

    respx.put("https://upload.example/x").mock(side_effect=_responder)

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    # El mensaje debe identificar el chunk que ha fallado (el 2º de 3).
    assert "2/3" in resultado.error
    # Y no debe haber seguido intentando el 3º tras el fallo del 2º.
    assert llamadas["n"] == 2
    # Un HTTP explícito (no un timeout/fallo de conexión en el último
    # chunk) no es ambiguo: no conlleva riesgo de duplicado.
    assert resultado.riesgo_duplicado is False


@respx.mock
def test_timeout_en_el_ultimo_chunk_advierte_de_riesgo_de_duplicado(tmp_path, post):
    # El vídeo de prueba (14 bytes) sube de una sola pieza: ese único chunk
    # es a la vez el primero y el último. Un timeout ahí es el caso
    # ambiguo de verdad: no se sabe si TikTok llegó a recibirlo entero y
    # empezar a publicar, así que el mensaje debe desaconsejar reintentar
    # sin comprobar antes la cuenta.
    brand = _brand(tmp_path, "inbox", auditada=False)
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"publish_id": "pid", "upload_url": "https://upload.example/x"}},
        )
    )
    respx.put("https://upload.example/x").mock(side_effect=httpx.TimeoutException("boom"))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "reintentes" in resultado.error
    assert "cuenta" in resultado.error
    # El campo estructurado que `retry` consulta para negarse a reintentar
    # esta red en automático (hallazgo de revisión: antes `retry` solo
    # miraba si la palabra "duplicar" aparecía en `error`).
    assert resultado.riesgo_duplicado is True


# --- Seguridad: el token nunca puede aparecer en un mensaje de error --------


@respx.mock
def test_el_token_nunca_aparece_en_un_mensaje_de_error(tmp_path, post):
    brand = _brand(tmp_path, "inbox", auditada=False, token=TOKEN_RECONOCIBLE)
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(401, json={"error": {"message": "token inválido"}})
    )

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert TOKEN_RECONOCIBLE not in (resultado.error or "")


@respx.mock
def test_el_token_nunca_aparece_ni_ante_un_fallo_de_red_inesperado(tmp_path, post):
    brand = _brand(tmp_path, "inbox", auditada=False, token=TOKEN_RECONOCIBLE)
    respx.post(f"{API}/inbox/video/init/").mock(side_effect=httpx.ConnectError("boom"))

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert TOKEN_RECONOCIBLE not in (resultado.error or "")


# --- Hallazgo I4 (revisión final): la salvaguarda de auditoría (y la falta
# de `open_id`/un `mode` desconocido) solo se comprobaban al publicar; con
# `mode: direct` y `auditada: false`, el preview decía "Problemas
# detectados: 0" y el usuario aprobaba una publicación condenada a fallar.
# Ahora `validate()` también las comprueba -sin tocar la red: la consulta a
# creator_info/query/ sigue siendo solo de `_publicar`-, y la salvaguarda
# SIGUE estando en `publish()` (no se ha quitado de ahí: `publish()` no
# puede confiar en que `validate()` se haya llamado antes).


def test_validate_detecta_falta_de_auditoria_en_modo_direct(tmp_path, post):
    brand = _brand(tmp_path, "direct", auditada=False)

    errores = TikTokAdapter().validate(post, brand)

    motivos = [e.motivo for e in errores]
    assert any("no está auditada" in m for m in motivos)
    # Con este `post` (vídeo 1080x1920, 45s: válido para TikTok) y una
    # cuenta por lo demás bien configurada, este es el ÚNICO problema: antes
    # del arreglo, `validate()` no veía ninguno (el preview diría "Problemas
    # detectados: 0" pese a que `publish()` iba a rechazar la publicación).
    assert len(errores) == 1


def test_validate_no_marca_error_de_auditoria_si_esta_auditada(tmp_path, post):
    brand = _brand(tmp_path, "direct", auditada=True)

    errores = TikTokAdapter().validate(post, brand)

    assert not any("auditada" in e.motivo for e in errores)


def test_validate_no_comprueba_auditoria_en_modo_inbox(tmp_path, post):
    brand = _brand(tmp_path, "inbox", auditada=False)

    errores = TikTokAdapter().validate(post, brand)

    assert errores == []


def test_validate_detecta_falta_de_open_id(tmp_path, post):
    b = crear_brand(tmp_path, "SinOpenIdValidate")
    b.guardar_secreto(Platform.TIKTOK, {"access_token": "t", "expira_en": time.time() + 3600})
    (b.raiz / "accounts.yml").write_text(
        "tiktok:\n  mode: inbox\n  auditada: false\n", encoding="utf-8"
    )
    brand_sin_open_id = cargar_brand(tmp_path, "SinOpenIdValidate")

    errores = TikTokAdapter().validate(post, brand_sin_open_id)

    assert any("open_id" in e.motivo for e in errores)


def test_validate_detecta_modo_desconocido(tmp_path, post):
    brand = _brand(tmp_path, "sarasa", auditada=True)

    errores = TikTokAdapter().validate(post, brand)

    assert any("modo de TikTok desconocido" in e.motivo for e in errores)
    # Un modo desconocido no es "direct": la salvaguarda de auditoría no
    # aplica (ver _comprobar_auditoria) y no debe añadir un segundo error
    # redundante por el mismo motivo.
    assert not any("auditada" in e.motivo for e in errores)


def test_publish_sigue_rechazando_la_falta_de_auditoria_aunque_nadie_llame_a_validate(
    tmp_path, post
):
    """La salvaguarda NO se ha quitado de `publish()`: sigue siendo
    infranqueable incluso si, por lo que sea, nadie llamó antes a
    `validate()` -exactamente el mismo escenario que ya cubren
    `test_modo_direct_sin_auditoria_aborta_sin_llamar_a_la_api` y los casos
    de `test_interpretacion_estricta_de_auditada`, repetido aquí para dejar
    explícito que este hallazgo vive en los DOS sitios, no en uno solo."""
    brand = _brand(tmp_path, "direct", auditada=False)

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert "no está auditada" in resultado.error


@respx.mock
def test_message_vacio_no_da_un_error_sin_motivo(tmp_path, post):
    """Hallazgo I6 (revisión final): TikTok ya rechazaba `message: ""` antes
    de unificar los cuatro `_mensaje_de_error` en
    `socialctl/adapters/errores.py` -era, de hecho, el criterio "bueno" que
    se generalizó a las otras tres redes-. Se deja aquí, junto a los mismos
    tests en youtube.py/facebook.py/instagram.py, para que la unificación
    siga cubierta por un test por adaptador.
    """
    brand = _brand(tmp_path, "inbox", auditada=False)
    respx.post(f"{API}/inbox/video/init/").mock(
        return_value=httpx.Response(400, json={"error": {"message": ""}})
    )

    with httpx.Client() as client:
        resultado = TikTokAdapter().publish(post, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert resultado.error
    assert "HTTP 400" in resultado.error
