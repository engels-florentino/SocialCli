import json
import time
from datetime import date

import httpx
import pytest
import respx

from socialctl.brands import crear_brand
from socialctl.metricas.base import SinPermiso, leer_red
from socialctl.metricas.modelos import EstadoLectura, TipoPieza
from socialctl.metricas.tiktok import (
    CLAVE_ENRIQUECIMIENTO_FALLIDO,
    TikTokLector,
)
from socialctl.models import Platform

USER_INFO = "https://open.tiktokapis.com/v2/user/info/"
VIDEO_LIST = "https://open.tiktokapis.com/v2/video/list/"

#: Largo a propósito: `socialctl/adapters/errores.py` solo redacta secretos de
#: 8 caracteres o más, así que con un `"t"` el test de fuga no probaría nada.
TOKEN = "act.token-de-prueba-largo-como-uno-real"


@pytest.fixture
def brand(tmp_path):
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.TIKTOK,
        {"access_token": TOKEN, "refresh_token": "r", "expira_en": time.time() + 3600},
    )
    return b


@respx.mock
def test_lee_la_cuenta_y_los_videos(brand):
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {"user": {"follower_count": 210, "likes_count": 5400, "video_count": 12}},
        "error": {"code": "ok", "message": ""},
    }))
    respx.post(VIDEO_LIST).mock(return_value=httpx.Response(200, json={
        "data": {
            "videos": [{
                "id": "7080213458555737986",
                "title": "Una línea en un mapa",
                "create_time": 1757529600,  # 2026-09-10 19:20 UTC aprox
                "view_count": 15400, "like_count": 980,
                "comment_count": 33, "share_count": 71,
                "share_url": "https://www.tiktok.com/@histopast/video/7080213458555737986",
            }],
            "cursor": 1757529600000,
            "has_more": False,
        },
        "error": {"code": "ok", "message": ""},
    }))

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, None)

    assert lectura.cuenta.seguidores == 210
    assert lectura.cuenta.total_piezas == 12

    pieza = lectura.piezas[0]
    assert pieza.id == "7080213458555737986"
    assert pieza.tipo is TipoPieza.VERTICAL  # en TikTok todo es vertical
    assert pieza.acumulado.vistas == 15400
    assert pieza.acumulado.likes == 980
    assert pieza.acumulado.comentarios == 33
    assert pieza.acumulado.compartidos == 71
    assert pieza.acumulado.guardados is None  # TikTok no lo da
    assert pieza.especificas == {}


@respx.mock
def test_pagina_hasta_agotar_has_more(brand):
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {"user": {"follower_count": 210, "likes_count": 1, "video_count": 2}},
        "error": {"code": "ok", "message": ""},
    }))
    respuestas = [
        httpx.Response(200, json={
            "data": {"videos": [{"id": "a", "title": "A", "create_time": 1757529600,
                                 "share_url": "https://tiktok.com/a"}],
                     "cursor": 111, "has_more": True},
            "error": {"code": "ok", "message": ""},
        }),
        httpx.Response(200, json={
            "data": {"videos": [{"id": "b", "title": "B", "create_time": 1757529600,
                                 "share_url": "https://tiktok.com/b"}],
                     "cursor": 222, "has_more": False},
            "error": {"code": "ok", "message": ""},
        }),
    ]
    respx.post(VIDEO_LIST).mock(side_effect=respuestas)

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, None)

    assert [p.id for p in lectura.piezas] == ["a", "b"]


@respx.mock
def test_la_paginacion_reenvia_el_cursor_de_la_pagina_anterior(brand):
    """Protege que la segunda petición lleve el cursor real, no que el mock responda en orden fijo sin mirarlo."""
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {"user": {"follower_count": 210, "likes_count": 1, "video_count": 2}},
        "error": {"code": "ok", "message": ""},
    }))

    CURSOR_PAGINA_1 = 1757529600000
    cuerpos_recibidos = []

    def responder(request):
        cuerpo = json.loads(request.content)
        cuerpos_recibidos.append(cuerpo)
        if "cursor" not in cuerpo:
            return httpx.Response(200, json={
                "data": {"videos": [{"id": "a", "title": "A", "create_time": 1757529600,
                                     "share_url": "https://tiktok.com/a"}],
                         "cursor": CURSOR_PAGINA_1, "has_more": True},
                "error": {"code": "ok", "message": ""},
            })
        return httpx.Response(200, json={
            "data": {"videos": [{"id": "b", "title": "B", "create_time": 1757529660,
                                 "share_url": "https://tiktok.com/b"}],
                     "cursor": 1757529660000, "has_more": False},
            "error": {"code": "ok", "message": ""},
        })

    respx.post(VIDEO_LIST).mock(side_effect=responder)

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, None)

    assert "cursor" not in cuerpos_recibidos[0]
    assert cuerpos_recibidos[1].get("cursor") == CURSOR_PAGINA_1
    assert [p.id for p in lectura.piezas] == ["a", "b"]


@respx.mock
def test_un_campo_que_la_app_sin_auditar_no_da_queda_en_none(brand):
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {"user": {"follower_count": 210}},  # sin likes_count ni video_count
        "error": {"code": "ok", "message": ""},
    }))
    respx.post(VIDEO_LIST).mock(return_value=httpx.Response(200, json={
        "data": {"videos": [{"id": "a", "title": "A", "create_time": 1757529600,
                             "share_url": "https://tiktok.com/a"}],
                 "cursor": 1, "has_more": False},
        "error": {"code": "ok", "message": ""},
    }))

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, None)

    assert lectura.cuenta.seguidores == 210
    assert lectura.cuenta.total_piezas is None
    assert lectura.piezas[0].acumulado.vistas is None


@respx.mock
def test_la_cuenta_vacia_sale_ok_con_ceros_que_son_dato_no_ausencia(brand):
    """Protege que una cuenta real sin actividad sea `ok` con cero piezas, y que esos ceros -dato de la API- no se confundan con el `None` de un campo ausente del test de arriba."""
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {"user": {"follower_count": 0, "likes_count": 0, "video_count": 0}},
        "error": {"code": "ok", "message": ""},
    }))
    respx.post(VIDEO_LIST).mock(return_value=httpx.Response(200, json={
        "data": {"videos": [], "has_more": False},
        "error": {"code": "ok", "message": ""},
    }))

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, None)

    assert lectura.estado is EstadoLectura.OK
    assert lectura.piezas == []
    # Ceros que la API devolvió de verdad: dato ("cero seguidores"), no ausencia.
    assert lectura.cuenta.seguidores == 0
    assert lectura.cuenta.total_piezas == 0


@respx.mock
def test_desde_descarta_lo_anterior(brand):
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {"user": {"follower_count": 210}}, "error": {"code": "ok", "message": ""},
    }))
    respx.post(VIDEO_LIST).mock(return_value=httpx.Response(200, json={
        "data": {"videos": [{"id": "viejo", "title": "V", "create_time": 1704067200,  # 2024-01-01
                             "share_url": "https://tiktok.com/v"}],
                 "cursor": 1, "has_more": False},
        "error": {"code": "ok", "message": ""},
    }))

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, date(2026, 1, 1))

    assert lectura.piezas == []


@respx.mock
def test_la_paginacion_no_se_corta_por_una_pagina_entera_descartada_por_fecha(brand):
    """Protege que una página entera fuera de `desde` no se confunda con el fin de la paginación."""
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {"user": {"follower_count": 210}}, "error": {"code": "ok", "message": ""},
    }))
    respuestas = [
        httpx.Response(200, json={
            "data": {"videos": [{"id": "viejo", "title": "V", "create_time": 1704067200,  # 2024-01-01
                                 "share_url": "https://tiktok.com/viejo"}],
                     "cursor": 1, "has_more": True},
            "error": {"code": "ok", "message": ""},
        }),
        httpx.Response(200, json={
            "data": {"videos": [{"id": "nuevo", "title": "N", "create_time": 1757529600,  # 2025-09-10
                                 "share_url": "https://tiktok.com/nuevo"}],
                     "cursor": 2, "has_more": False},
            "error": {"code": "ok", "message": ""},
        }),
    ]
    respx.post(VIDEO_LIST).mock(side_effect=respuestas)

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, date(2025, 1, 1))

    assert [p.id for p in lectura.piezas] == ["nuevo"]


@respx.mock
def test_error_de_scope_dice_cual_falta(brand):
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {}, "error": {"code": "scope_not_authorized",
                              "message": "The scope user.info.stats is not authorized"},
    }))

    with httpx.Client() as client:
        with pytest.raises(SinPermiso) as excinfo:
            TikTokLector().leer(brand, client, None)

    mensaje = str(excinfo.value)
    assert "user.info.stats" in mensaje
    assert "socialctl auth tiktok" in mensaje


@respx.mock
def test_un_403_de_scope_tambien_es_sin_permiso_y_no_un_http_generico(brand):
    """Lo habitual cuando falta un scope no es un 200 con `error`, sino un 403.

    Si se comprobara el código HTTP antes que el cuerpo, este caso -el más
    frecuente- saldría como `HTTPStatusError` y el usuario vería el 403 opaco
    que el spec prohíbe.
    """
    respx.get(USER_INFO).mock(return_value=httpx.Response(403, json={
        "error": {"code": "scope_not_authorized",
                  "message": "The scope user.info.stats is not authorized"},
    }))

    with httpx.Client() as client:
        with pytest.raises(SinPermiso) as excinfo:
            TikTokLector().leer(brand, client, None)

    assert "user.info.stats" in str(excinfo.value)


@respx.mock
def test_un_error_http_sin_cuerpo_json_no_se_disfraza_de_scope(brand):
    respx.get(USER_INFO).mock(return_value=httpx.Response(
        502, text="<html>bad gateway</html>",
    ))

    with httpx.Client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            TikTokLector().leer(brand, client, None)


@respx.mock
def test_el_token_de_tiktok_no_llega_a_lecturared_error(brand):
    """Test de fuga, el cuarto: uno por red.

    Aquí el token viaja en la cabecera, así que la URL no lo lleva; la vía por
    la que podría colarse es el CUERPO, si un intermediario reflejara la
    petición fallida en su respuesta de error. Este test afirma que tampoco
    por ahí: `LecturaRed.error` acaba en el snapshot, que se versiona.
    """
    respx.get(USER_INFO).mock(return_value=httpx.Response(200, json={
        "data": {},
        "error": {
            "code": "internal_error",
            "message": f"upstream falló con Authorization: Bearer {TOKEN}",
        },
    }))

    with httpx.Client() as client:
        lectura = leer_red(Platform.TIKTOK, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert TOKEN not in (lectura.error or "")
    assert "internal_error" in lectura.error


# --- degradar en vez de tirar lo ya leído ----------------------------------


def _usuario_ok():
    return httpx.Response(200, json={
        "data": {"user": {"follower_count": 210, "likes_count": 1, "video_count": 3}},
        "error": {"code": "ok", "message": ""},
    })


def _pagina(id_, has_more, cursor=111):
    return httpx.Response(200, json={
        "data": {"videos": [{"id": id_, "title": id_.upper(),
                             "create_time": 1757529600,
                             "share_url": f"https://tiktok.com/{id_}"}],
                 "cursor": cursor, "has_more": has_more},
        "error": {"code": "ok", "message": ""},
    })


@respx.mock
def test_un_fallo_en_una_pagina_posterior_conserva_lo_ya_leido(brand):
    """TikTok era el único lector que tiraba lo leído cuando fallaba a medias.

    Se conserva lo de las páginas buenas y se marca cada pieza con la misma
    señal que usan YouTube y Meta (`CLAVE_ENRIQUECIMIENTO_FALLIDO`), que es
    lo que `resumen.md` enseña en su columna "Aviso".
    """
    respx.get(USER_INFO).mock(return_value=_usuario_ok())
    respx.post(VIDEO_LIST).mock(side_effect=[
        _pagina("a", True),
        _pagina("b", True, cursor=222),
        httpx.Response(500, json={"error": {"code": "internal_error",
                                            "message": "se cayó"}}),
    ])

    with httpx.Client() as client:
        lectura = TikTokLector().leer(brand, client, None)

    assert lectura.estado is EstadoLectura.OK
    assert [p.id for p in lectura.piezas] == ["a", "b"]
    for pieza in lectura.piezas:
        aviso = pieza.especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO]
        assert "página 3" in aviso
        assert TOKEN not in aviso


@respx.mock
def test_si_falla_la_primera_pagina_la_red_queda_en_error(brand):
    """Sin nada leído no hay lectura parcial que salvar: es un fallo de la red."""
    respx.get(USER_INFO).mock(return_value=_usuario_ok())
    respx.post(VIDEO_LIST).mock(return_value=httpx.Response(
        500, json={"error": {"code": "internal_error", "message": "se cayó"}}
    ))

    with httpx.Client() as client:
        lectura = leer_red(Platform.TIKTOK, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert TOKEN not in (lectura.error or "")


@respx.mock
def test_un_scope_que_falta_a_media_paginacion_sigue_siendo_sin_permiso(brand):
    """Un permiso que falta no es una lectura parcial: se dice cuál falta."""
    respx.get(USER_INFO).mock(return_value=_usuario_ok())
    respx.post(VIDEO_LIST).mock(side_effect=[
        _pagina("a", True),
        httpx.Response(403, json={"error": {"code": "scope_not_authorized",
                                            "message": "scope no autorizado"}}),
    ])

    with httpx.Client() as client:
        with pytest.raises(SinPermiso):
            TikTokLector().leer(brand, client, None)
