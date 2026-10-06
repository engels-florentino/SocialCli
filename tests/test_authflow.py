import base64
import hashlib
import re

import httpx
import pytest
import respx

from socialctl.authflow import (
    EstadoInvalido,
    calcular_code_challenge,
    canjear_codigo,
    construir_url_autorizacion,
    generar_code_verifier,
    generar_state,
    intercambiar_token_meta,
    obtener_paginas_meta,
    verificar_state,
)
from socialctl.models import Platform

REDIRECT = "http://localhost:8723/callback"
VERIFIER = "verificador-de-prueba-1234567890-abcdefghijklmnopqrstuvwxyz"


def _params(url: str) -> dict:
    return dict(httpx.QueryParams(url.split("?", 1)[1]))


# --- construir_url_autorizacion ---


def test_url_de_youtube_pide_el_scope_de_subida_y_lleva_pkce_y_state():
    url = construir_url_autorizacion(
        Platform.YOUTUBE, "cid", REDIRECT, state="est4do", code_verifier=VERIFIER
    )
    parametros = _params(url)

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert parametros["scope"] == (
        "https://www.googleapis.com/auth/youtube.upload"
        " https://www.googleapis.com/auth/yt-analytics.readonly"
        " https://www.googleapis.com/auth/youtube.readonly"
    )
    assert parametros["access_type"] == "offline"
    assert parametros["client_id"] == "cid"
    assert parametros["state"] == "est4do"
    assert parametros["code_challenge_method"] == "S256"

    esperado = base64.urlsafe_b64encode(
        hashlib.sha256(VERIFIER.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert parametros["code_challenge"] == esperado


def test_tiktok_authorization_requests_only_inbox_scopes():
    """The actual OAuth URL must match the two permissions under review."""
    url = construir_url_autorizacion(
        Platform.TIKTOK, "ck", REDIRECT, state="est4do", code_verifier=VERIFIER
    )
    parametros = _params(url)

    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/")
    assert parametros["scope"] == "user.info.basic,video.upload"
    assert parametros["client_key"] == "ck"
    assert "client_id" not in parametros
    assert parametros["state"] == "est4do"
    assert parametros["code_challenge_method"] == "S256"


def test_el_code_challenge_de_tiktok_es_hexadecimal_no_base64url():
    """Hallazgo del brief: TikTok codifica su code_challenge en hexadecimal,
    no en base64url (el estándar RFC 7636 que sí usa Google). Si se usara la
    codificación de Google también para TikTok, el challenge no coincidiría
    con el que TikTok recalcula y el canje del código fallaría."""
    url = construir_url_autorizacion(
        Platform.TIKTOK, "ck", REDIRECT, state="est4do", code_verifier=VERIFIER
    )
    challenge = _params(url)["code_challenge"]

    esperado_hex = hashlib.sha256(VERIFIER.encode("ascii")).hexdigest()
    esperado_base64url = base64.urlsafe_b64encode(
        hashlib.sha256(VERIFIER.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")

    assert challenge == esperado_hex
    assert challenge != esperado_base64url


def test_meta_no_tiene_flujo_de_redireccion():
    with pytest.raises(ValueError) as exc:
        construir_url_autorizacion(
            Platform.FACEBOOK, "cid", REDIRECT, state="e", code_verifier=VERIFIER
        )
    assert 'Explorer' in str(exc.value)


# --- PKCE: generación ---


def test_generar_code_verifier_tiene_longitud_y_alfabeto_validos():
    verifier = generar_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)


def test_generar_code_verifier_es_distinto_en_cada_llamada():
    assert generar_code_verifier() != generar_code_verifier()


def test_calcular_code_challenge_desconocido_lanza_value_error():
    with pytest.raises(ValueError):
        calcular_code_challenge(Platform.FACEBOOK, VERIFIER)


# --- state / CSRF ---


def test_generar_state_es_distinto_en_cada_llamada_y_no_trivial():
    a, b = generar_state(), generar_state()
    assert a != b
    assert len(a) >= 16


def test_verificar_state_coincidente_no_lanza():
    verificar_state("abc123", "abc123")


def test_verificar_state_no_coincidente_lanza_estado_invalido():
    with pytest.raises(EstadoInvalido):
        verificar_state("abc123", "otro-valor")


def test_verificar_state_ausente_lanza_estado_invalido():
    with pytest.raises(EstadoInvalido):
        verificar_state("abc123", None)


# --- canjear_codigo: YouTube ---


@respx.mock
def test_canjear_codigo_de_youtube_devuelve_los_tokens_y_manda_code_verifier():
    ruta = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        })
    )

    with httpx.Client() as client:
        datos = canjear_codigo(
            Platform.YOUTUBE, "codigo",
            {"client_id": "cid", "client_secret": "cs"}, REDIRECT, client,
            code_verifier=VERIFIER,
        )

    assert datos["access_token"] == "at"
    assert datos["refresh_token"] == "rt"
    assert datos["expira_en"] > 0
    # Las credenciales de la app se guardan para poder refrescar despues.
    assert datos["client_id"] == "cid"

    enviado = dict(httpx.QueryParams(ruta.calls.last.request.content.decode()))
    assert enviado == {
        "grant_type": "authorization_code",
        "code": "codigo",
        "redirect_uri": REDIRECT,
        "client_id": "cid",
        "client_secret": "cs",
        "code_verifier": VERIFIER,
    }


@respx.mock
def test_canje_rechazado_de_youtube_lanza_error_sin_filtrar_el_secreto():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            canjear_codigo(
                Platform.YOUTUBE, "codigo",
                {"client_id": "cid", "client_secret": "SECRETO"}, REDIRECT, client,
                code_verifier=VERIFIER,
            )

    assert "SECRETO" not in str(exc.value)


@respx.mock
def test_canje_con_200_pero_sin_access_token_no_filtra_el_cuerpo():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"otro_campo": "FUGA-CUERPO-1"})
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            canjear_codigo(
                Platform.YOUTUBE, "codigo",
                {"client_id": "cid", "client_secret": "cs"}, REDIRECT, client,
                code_verifier=VERIFIER,
            )

    assert "FUGA-CUERPO-1" not in str(exc.value)


# --- canjear_codigo: fallos de red reales (Hallazgo 1 de la revisión de la
# Task 14: antes solo se capturaba "status != 200" y JSON mal formado; una
# caída de red real -host inalcanzable, timeout, DNS- escapaba como
# excepción cruda en vez del RuntimeError limpio que promete el resto del
# módulo) ---


@respx.mock
def test_canjear_codigo_timeout_da_mensaje_limpio_sin_traza():
    respx.post("https://oauth2.googleapis.com/token").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            canjear_codigo(
                Platform.YOUTUBE, "codigo",
                {"client_id": "cid", "client_secret": "SECRETO-TIMEOUT"}, REDIRECT, client,
                code_verifier=VERIFIER,
            )

    mensaje = str(exc.value)
    assert 'timed out' in mensaje.lower()
    assert "auth" in mensaje.lower()
    assert "SECRETO-TIMEOUT" not in mensaje


@respx.mock
def test_canjear_codigo_fallo_de_conexion_da_mensaje_limpio_sin_traza():
    respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            canjear_codigo(
                Platform.TIKTOK, "codigo",
                {"client_key": "ck", "client_secret": "SECRETO-CONEXION"}, REDIRECT, client,
                code_verifier="VERIFIER-CONEXION-SECRETO",
            )

    mensaje = str(exc.value)
    assert 'connect' in mensaje.lower()
    assert "SECRETO-CONEXION" not in mensaje
    assert "VERIFIER-CONEXION-SECRETO" not in mensaje


def test_canjear_codigo_url_invalida_no_filtra_el_secreto(monkeypatch):
    """`httpx.InvalidURL` no hereda de `httpx.HTTPError`, así que necesita su
    propio `except`. La URL de canje es una constante del módulo (no se
    construye con datos del usuario), así que esta excepción no se puede
    disparar de verdad con un secreto manipulado: se simula monkeypatcheando
    `httpx.Client.post` directamente, colgando de la excepción la petición
    real -con el secreto dentro-, igual que hace `httpx` con sus propias
    excepciones de red, para comprobar que el mensaje final no la vuelca.
    """
    peticion_capturada: dict[str, httpx.Request] = {}

    def post_con_url_invalida(self, url, **kwargs):
        peticion = httpx.Request("POST", str(url), data=kwargs.get("data"))
        peticion_capturada["valor"] = peticion
        exc = httpx.InvalidURL("URL no válida")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "post", post_con_url_invalida)

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            canjear_codigo(
                Platform.YOUTUBE, "codigo",
                {"client_id": "cid", "client_secret": "SECRETO-URL-INVALIDA"}, REDIRECT, client,
                code_verifier=VERIFIER,
            )

    mensaje = str(exc.value)
    assert 'invalid' in mensaje.lower()
    assert "SECRETO-URL-INVALIDA" not in mensaje
    # La petición que provocó el fallo sí llevaba el secreto: si no lo
    # llevara, la aserción anterior sería trivial y no probaría nada.
    assert b"SECRETO-URL-INVALIDA" in peticion_capturada["valor"].content


def test_canjear_codigo_resguardo_final_no_filtra_el_secreto(monkeypatch):
    """El `except Exception` final debe capturar cualquier fallo no previsto
    -aquí, uno que ni siquiera es de `httpx`- y jamás filtrar el secreto,
    aunque la excepción lleve la petición real colgada (como hace `httpx`
    con sus propias excepciones)."""

    def post_con_fallo_inesperado(self, url, **kwargs):
        peticion = httpx.Request("POST", str(url), data=kwargs.get("data"))
        exc = ValueError("fallo interno inesperado del transporte")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "post", post_con_fallo_inesperado)

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            canjear_codigo(
                Platform.YOUTUBE, "codigo",
                {"client_id": "cid", "client_secret": "SECRETO-INESPERADO"}, REDIRECT, client,
                code_verifier="VERIFIER-INESPERADO-SECRETO",
            )

    mensaje = str(exc.value)
    assert 'unexpected' in mensaje.lower()
    assert "SECRETO-INESPERADO" not in mensaje
    assert "VERIFIER-INESPERADO-SECRETO" not in mensaje
    # Ni siquiera el tipo o el texto de la excepción original se interpolan.
    assert "ValueError" not in mensaje
    assert 'fallo interno unexpected del transporte' not in mensaje


# --- canjear_codigo: TikTok ---


@respx.mock
def test_canjear_codigo_de_tiktok_manda_client_key_y_code_verifier():
    ruta = respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
        })
    )

    with httpx.Client() as client:
        datos = canjear_codigo(
            Platform.TIKTOK, "codigo",
            {"client_key": "ck", "client_secret": "cs"}, REDIRECT, client,
            code_verifier=VERIFIER,
        )

    assert datos["access_token"] == "at"
    assert datos["client_key"] == "ck"

    enviado = dict(httpx.QueryParams(ruta.calls.last.request.content.decode()))
    assert enviado == {
        "grant_type": "authorization_code",
        "code": "codigo",
        "redirect_uri": REDIRECT,
        "client_key": "ck",
        "client_secret": "cs",
        "code_verifier": VERIFIER,
    }
    assert "client_id" not in enviado
    # Sin open_id en la respuesta, no debe aparecer inventado en el resultado.
    assert "open_id" not in datos


@respx.mock
def test_canjear_codigo_de_tiktok_devuelve_el_open_id_de_la_respuesta():
    """Confirmado con Context7 (developers.tiktok.com/docs/en/silent-login
    y /docs/en/oauth-user-access-token-management): la respuesta del canje
    de `/v2/oauth/token/` ya incluye el campo `open_id` del usuario
    directamente, junto a `access_token` y `scope`. No hace falta una
    llamada aparte a `/v2/user/info/` para obtenerlo."""
    respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
            "open_id": "oid-de-prueba-123",
            "scope": "user.info.basic,user.info.stats,video.upload,video.publish,video.list",
        })
    )

    with httpx.Client() as client:
        datos = canjear_codigo(
            Platform.TIKTOK, "codigo",
            {"client_key": "ck", "client_secret": "cs"}, REDIRECT, client,
            code_verifier=VERIFIER,
        )

    assert datos["open_id"] == "oid-de-prueba-123"


@respx.mock
def test_canje_rechazado_de_tiktok_no_filtra_el_code_verifier():
    respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            canjear_codigo(
                Platform.TIKTOK, "codigo",
                {"client_key": "ck", "client_secret": "cs"}, REDIRECT, client,
                code_verifier="VERIFIER-SECRETO",
            )

    assert "VERIFIER-SECRETO" not in str(exc.value)


def test_canjear_codigo_meta_lanza_value_error():
    with pytest.raises(ValueError) as exc:
        with httpx.Client() as client:
            canjear_codigo(
                Platform.FACEBOOK, "codigo", {}, REDIRECT, client, code_verifier="x",
            )
    assert 'Explorer' in str(exc.value)


# --- Meta: intercambio de token de usuario -> pagina ---


@respx.mock
def test_intercambiar_token_meta_devuelve_el_token_de_larga_duracion():
    ruta = respx.get("https://graph.facebook.com/v26.0/oauth/access_token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "token-largo", "token_type": "bearer", "expires_in": 5183944,
        })
    )

    with httpx.Client() as client:
        token = intercambiar_token_meta("app-id", "app-secret", "token-corto", client)

    assert token == "token-largo"
    enviado = dict(ruta.calls.last.request.url.params)
    assert enviado == {
        "grant_type": "fb_exchange_token",
        "client_id": "app-id",
        "client_secret": "app-secret",
        "fb_exchange_token": "token-corto",
    }


@respx.mock
def test_intercambiar_token_meta_rechazado_no_filtra_el_token_pegado():
    respx.get("https://graph.facebook.com/v26.0/oauth/access_token").mock(
        return_value=httpx.Response(400, json={"error": {"message": "token invalido"}})
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            intercambiar_token_meta("app-id", "app-secret", "TOKEN-CORTO-SECRETO", client)

    assert "TOKEN-CORTO-SECRETO" not in str(exc.value)


# --- intercambiar_token_meta: fallos de red reales (Hallazgo 1) ---


@respx.mock
def test_intercambiar_token_meta_timeout_da_mensaje_limpio_sin_traza():
    respx.get("https://graph.facebook.com/v26.0/oauth/access_token").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            intercambiar_token_meta("app-id", "SECRETO-APP", "TOKEN-CORTO-TIMEOUT", client)

    mensaje = str(exc.value)
    assert 'timed out' in mensaje.lower()
    assert "SECRETO-APP" not in mensaje
    assert "TOKEN-CORTO-TIMEOUT" not in mensaje


@respx.mock
def test_intercambiar_token_meta_fallo_de_conexion_da_mensaje_limpio_sin_traza():
    respx.get("https://graph.facebook.com/v26.0/oauth/access_token").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            intercambiar_token_meta("app-id", "SECRETO-APP-CONEXION", "token-corto", client)

    mensaje = str(exc.value)
    assert 'connect' in mensaje.lower()
    assert "SECRETO-APP-CONEXION" not in mensaje


def test_intercambiar_token_meta_url_invalida_no_filtra_el_token(monkeypatch):
    """Igual que en `canjear_codigo`: `httpx.InvalidURL` no hereda de
    `httpx.HTTPError` y la URL de este endpoint es una constante del módulo,
    así que se simula monkeypatcheando `httpx.Client.get` con la petición
    real -con el token pegado dentro- colgada de la excepción."""
    peticion_capturada: dict[str, httpx.Request] = {}

    def get_con_url_invalida(self, url, **kwargs):
        peticion = httpx.Request("GET", str(url), params=kwargs.get("params"))
        peticion_capturada["valor"] = peticion
        exc = httpx.InvalidURL("URL no válida")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "get", get_con_url_invalida)

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            intercambiar_token_meta("app-id", "app-secret", "TOKEN-CORTO-URL-INVALIDA", client)

    mensaje = str(exc.value)
    assert 'invalid' in mensaje.lower()
    assert "TOKEN-CORTO-URL-INVALIDA" not in mensaje
    assert "TOKEN-CORTO-URL-INVALIDA" in str(peticion_capturada["valor"].url)


def test_intercambiar_token_meta_resguardo_final_no_filtra_el_token(monkeypatch):
    def get_con_fallo_inesperado(self, url, **kwargs):
        peticion = httpx.Request("GET", str(url), params=kwargs.get("params"))
        exc = ValueError("fallo interno inesperado del transporte")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "get", get_con_fallo_inesperado)

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            intercambiar_token_meta("app-id", "app-secret", "TOKEN-CORTO-INESPERADO", client)

    mensaje = str(exc.value)
    assert 'unexpected' in mensaje.lower()
    assert "TOKEN-CORTO-INESPERADO" not in mensaje
    assert "ValueError" not in mensaje


@respx.mock
def test_obtener_paginas_meta_devuelve_la_lista_de_paginas():
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        return_value=httpx.Response(200, json={
            "data": [
                {"id": "111", "name": "Histopast", "access_token": "token-pagina-111"},
                {"id": "222", "name": "Otra Pagina", "access_token": "token-pagina-222"},
            ]
        })
    )

    with httpx.Client() as client:
        paginas = obtener_paginas_meta("token-usuario-largo", client)

    assert paginas == [
        {"id": "111", "name": "Histopast", "access_token": "token-pagina-111"},
        {"id": "222", "name": "Otra Pagina", "access_token": "token-pagina-222"},
    ]


@respx.mock
def test_obtener_paginas_meta_rechazado_no_filtra_el_token_de_usuario():
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        return_value=httpx.Response(400, json={"error": {"message": "sesion caducada"}})
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            obtener_paginas_meta("TOKEN-USUARIO-SECRETO", client)

    assert "TOKEN-USUARIO-SECRETO" not in str(exc.value)


@respx.mock
def test_obtener_paginas_meta_con_forma_inesperada_da_error_claro():
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        return_value=httpx.Response(200, json={"data": "no-es-una-lista"})
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError):
            obtener_paginas_meta("token", client)


# --- obtener_paginas_meta: fallos de red reales (Hallazgo 1) ---


@respx.mock
def test_obtener_paginas_meta_timeout_da_mensaje_limpio_sin_traza():
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        side_effect=httpx.TimeoutException("timed out")
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            obtener_paginas_meta("TOKEN-USUARIO-TIMEOUT", client)

    mensaje = str(exc.value)
    assert 'timed out' in mensaje.lower()
    assert "TOKEN-USUARIO-TIMEOUT" not in mensaje


@respx.mock
def test_obtener_paginas_meta_fallo_de_conexion_da_mensaje_limpio_sin_traza():
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            obtener_paginas_meta("TOKEN-USUARIO-CONEXION", client)

    mensaje = str(exc.value)
    assert 'connect' in mensaje.lower()
    assert "TOKEN-USUARIO-CONEXION" not in mensaje


def test_obtener_paginas_meta_url_invalida_no_filtra_el_token(monkeypatch):
    peticion_capturada: dict[str, httpx.Request] = {}

    def get_con_url_invalida(self, url, **kwargs):
        peticion = httpx.Request("GET", str(url), params=kwargs.get("params"))
        peticion_capturada["valor"] = peticion
        exc = httpx.InvalidURL("URL no válida")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "get", get_con_url_invalida)

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            obtener_paginas_meta("TOKEN-USUARIO-URL-INVALIDA", client)

    mensaje = str(exc.value)
    assert 'invalid' in mensaje.lower()
    assert "TOKEN-USUARIO-URL-INVALIDA" not in mensaje
    assert "TOKEN-USUARIO-URL-INVALIDA" in str(peticion_capturada["valor"].url)


def test_obtener_paginas_meta_resguardo_final_no_filtra_el_token(monkeypatch):
    def get_con_fallo_inesperado(self, url, **kwargs):
        peticion = httpx.Request("GET", str(url), params=kwargs.get("params"))
        exc = ValueError("fallo interno inesperado del transporte")
        exc.request = peticion
        raise exc

    monkeypatch.setattr(httpx.Client, "get", get_con_fallo_inesperado)

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as exc:
            obtener_paginas_meta("TOKEN-USUARIO-INESPERADO", client)

    mensaje = str(exc.value)
    assert 'unexpected' in mensaje.lower()
    assert "TOKEN-USUARIO-INESPERADO" not in mensaje
    assert "ValueError" not in mensaje
