import time

import httpx
import pytest
import respx

from socialctl.auth import AuthError, obtener_token
from socialctl.brands import crear_brand
from socialctl.models import Platform


@pytest.fixture
def brand(tmp_path):
    return crear_brand(tmp_path, "Histopast")


def test_token_vigente_se_devuelve_sin_llamar_a_la_red(brand):
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {"access_token": "vigente", "refresh_token": "r", "expira_en": time.time() + 3600},
    )
    with httpx.Client() as client:
        assert obtener_token(brand, Platform.YOUTUBE, client) == "vigente"


@respx.mock
def test_token_caducado_se_refresca_y_se_persiste(brand):
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": "viejo",
            "refresh_token": "r",
            "client_id": "cid",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )
    ruta = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "nuevo", "expires_in": 3600}
        )
    )

    with httpx.Client() as client:
        assert obtener_token(brand, Platform.YOUTUBE, client) == "nuevo"

    assert brand.leer_secreto(Platform.YOUTUBE)["access_token"] == "nuevo"

    # YouTube identifica al cliente con client_id/client_secret (no
    # client_key, que es el campo de TikTok): confundirlos es un error
    # fácil y silencioso, así que se pinza el nombre exacto de cada campo.
    enviado = dict(httpx.QueryParams(ruta.calls.last.request.content.decode()))
    assert enviado == {
        "grant_type": "refresh_token",
        "refresh_token": "r",
        "client_id": "cid",
        "client_secret": "cs",
    }


def test_sin_credenciales_pide_autenticar(brand):
    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.TIKTOK, client)
    assert 'socialcli auth' in str(exc.value)


@respx.mock
def test_refresh_rechazado_pide_reautenticar_sin_filtrar_el_token(brand):
    brand.guardar_secreto(
        Platform.TIKTOK,
        {
            "access_token": "viejo",
            "refresh_token": "SECRETO-r",
            "client_key": "ck",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )
    ruta = respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.TIKTOK, client)

    mensaje = str(exc.value)
    assert 'socialcli auth tiktok' in mensaje
    assert "SECRETO-r" not in mensaje

    # TikTok identifica al cliente con client_key/client_secret (no
    # client_id, que es el campo de YouTube): se pinza el nombre exacto,
    # aunque el refresco haya sido rechazado por el proveedor.
    enviado = dict(httpx.QueryParams(ruta.calls.last.request.content.decode()))
    assert enviado == {
        "grant_type": "refresh_token",
        "refresh_token": "SECRETO-r",
        "client_key": "ck",
        "client_secret": "cs",
    }


def test_facebook_caducado_pide_reautenticar(brand):
    brand.guardar_secreto(
        Platform.FACEBOOK,
        {"access_token": "viejo", "expira_en": time.time() - 10},
    )
    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.FACEBOOK, client)
    assert 'socialcli auth facebook' in str(exc.value)


@respx.mock
def test_expira_en_corrupto_pide_reautenticar_sin_reventar(brand):
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": "viejo",
            "refresh_token": "r",
            "client_id": "cid",
            "client_secret": "cs",
            "expira_en": "no-es-un-numero",
        },
    )
    # No se registra ninguna ruta: si el fix fuera incorrecto y se llegara
    # a intentar el refresco contra la red, respx lo señalaría con su
    # propio error en vez de dejar pasar una llamada real.
    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.YOUTUBE, client)

    mensaje = str(exc.value)
    assert 'socialcli auth youtube' in mensaje
    assert "no-es-un-numero" not in mensaje


@respx.mock
def test_refresco_con_200_sin_access_token_pide_reautenticar(brand):
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": "viejo",
            "refresh_token": "r",
            "client_id": "cid",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200,
            json={"expires_in": 3600, "otro_campo": "FUGA-CUERPO-1"},
        )
    )

    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.YOUTUBE, client)

    mensaje = str(exc.value)
    assert 'socialcli auth youtube' in mensaje
    assert "FUGA-CUERPO-1" not in mensaje


@respx.mock
def test_refresco_con_200_no_json_pide_reautenticar(brand):
    brand.guardar_secreto(
        Platform.TIKTOK,
        {
            "access_token": "viejo",
            "refresh_token": "r",
            "client_key": "ck",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )
    respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        return_value=httpx.Response(
            200, content=b"esto no es JSON, contiene FUGA-CUERPO-2 {"
        )
    )

    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.TIKTOK, client)

    mensaje = str(exc.value)
    assert 'socialcli auth tiktok' in mensaje
    assert "FUGA-CUERPO-2" not in mensaje


@respx.mock
def test_refresco_sin_expires_in_deja_de_tratarse_como_caducado(brand):
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": "viejo",
            "refresh_token": "r",
            "client_id": "cid",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )
    ruta = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "nuevo-sin-exp"})
    )

    with httpx.Client() as client:
        assert obtener_token(brand, Platform.YOUTUBE, client) == "nuevo-sin-exp"
        assert brand.leer_secreto(Platform.YOUTUBE)["expira_en"] is None

        # Si el fix no estuviera, expira_en conservaría el valor viejo (ya
        # caducado) y esta segunda llamada volvería a llamar a la red.
        assert obtener_token(brand, Platform.YOUTUBE, client) == "nuevo-sin-exp"

    assert ruta.call_count == 1


# --- _refrescar: fallos de red reales (Hallazgo 1 de la revisión de la Task
# 14: el mismo hueco que en socialctl/authflow.py existía aquí desde antes -
# solo se capturaba "status != 200" y JSON mal formado; una caída de red real
# escapaba como excepción cruda en vez del AuthError limpio de este módulo) ---


@respx.mock
def test_refresco_timeout_pide_reautenticar_sin_traza(brand):
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": "viejo",
            "refresh_token": "SECRETO-TIMEOUT",
            "client_id": "cid",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )
    respx.post("https://oauth2.googleapis.com/token").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.YOUTUBE, client)

    mensaje = str(exc.value)
    assert 'timed out' in mensaje.lower()
    assert 'socialcli auth youtube' in mensaje
    assert "SECRETO-TIMEOUT" not in mensaje


@respx.mock
def test_refresco_fallo_de_conexion_pide_reautenticar_sin_traza(brand):
    brand.guardar_secreto(
        Platform.TIKTOK,
        {
            "access_token": "viejo",
            "refresh_token": "SECRETO-CONEXION",
            "client_key": "ck",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )
    respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.TIKTOK, client)

    mensaje = str(exc.value)
    assert 'connect' in mensaje.lower()
    assert 'socialcli auth tiktok' in mensaje
    assert "SECRETO-CONEXION" not in mensaje


def test_refresco_url_invalida_no_filtra_el_refresh_token(brand, monkeypatch):
    """`httpx.InvalidURL` no hereda de `httpx.HTTPError`, así que necesita su
    propio `except`. La URL de refresco es una constante del módulo (no se
    construye con datos del usuario), así que esta excepción no se puede
    disparar de verdad con un secreto manipulado: se simula monkeypatcheando
    `httpx.Client.post` directamente, colgando de la excepción la petición
    real -con el refresh_token y el client_secret dentro-, igual que hace
    `httpx` con sus propias excepciones de red, para comprobar que el
    mensaje final no la vuelca.
    """
    brand.guardar_secreto(
        Platform.YOUTUBE,
        {
            "access_token": "viejo",
            "refresh_token": "SECRETO-REFRESH-URL",
            "client_id": "cid",
            "client_secret": "SECRETO-CLIENTE-URL",
            "expira_en": time.time() - 10,
        },
    )
    peticion_capturada: dict[str, httpx.Request] = {}

    def post_con_url_invalida(self, url, **kwargs):
        peticion = httpx.Request("POST", str(url), data=kwargs.get("data"))
        peticion_capturada["valor"] = peticion
        exc = httpx.InvalidURL("URL no válida")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "post", post_con_url_invalida)

    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.YOUTUBE, client)

    mensaje = str(exc.value)
    assert 'invalid' in mensaje.lower()
    assert "SECRETO-REFRESH-URL" not in mensaje
    assert "SECRETO-CLIENTE-URL" not in mensaje
    # La petición que provocó el fallo sí llevaba los secretos: si no los
    # llevara, la aserción anterior sería trivial y no probaría nada.
    assert b"SECRETO-REFRESH-URL" in peticion_capturada["valor"].content


def test_refresco_resguardo_final_no_filtra_el_refresh_token(brand, monkeypatch):
    """El `except Exception` final debe capturar cualquier fallo no previsto
    -aquí, uno que ni siquiera es de `httpx`- y jamás filtrar el secreto,
    aunque la excepción lleve la petición real colgada (como hace `httpx`
    con sus propias excepciones)."""
    brand.guardar_secreto(
        Platform.TIKTOK,
        {
            "access_token": "viejo",
            "refresh_token": "SECRETO-REFRESH-INESPERADO",
            "client_key": "ck",
            "client_secret": "cs",
            "expira_en": time.time() - 10,
        },
    )

    def post_con_fallo_inesperado(self, url, **kwargs):
        peticion = httpx.Request("POST", str(url), data=kwargs.get("data"))
        exc = ValueError("fallo interno inesperado del transporte")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "post", post_con_fallo_inesperado)

    with httpx.Client() as client:
        with pytest.raises(AuthError) as exc:
            obtener_token(brand, Platform.TIKTOK, client)

    mensaje = str(exc.value)
    assert 'unexpected' in mensaje.lower()
    assert 'socialcli auth tiktok' in mensaje
    assert "SECRETO-REFRESH-INESPERADO" not in mensaje
    assert "ValueError" not in mensaje
