import time
from datetime import date, datetime

import httpx
import pytest
import respx
import yaml

from socialctl.brands import crear_brand
from socialctl.metricas.base import SinPermiso, leer_red
from socialctl.metricas.facebook import (
    CLAVE_ENRIQUECIMIENTO_FALLIDO,
    FacebookLector,
    fecha_meta,
)
from socialctl.metricas.instagram import InstagramLector
from socialctl.metricas.modelos import EstadoLectura, TipoPieza
from socialctl.models import Platform

GRAFO = "https://graph.facebook.com/v26.0"

#: Token largo a propósito, y reconocible: `socialctl/adapters/errores.py`
#: solo redacta secretos de 8 caracteres o más (ver
#: `_LONGITUD_MINIMA_SECRETO_REDACTABLE`), así que con un `"t"` de relleno los
#: tests de fuga de este fichero no probarían nada.
TOKEN = "EAAGtoken-de-prueba-largo-como-uno-real"


@pytest.fixture
def brand(tmp_path):
    b = crear_brand(tmp_path, "Histopast")
    (b.raiz / "accounts.yml").write_text(
        yaml.safe_dump({
            "facebook": {"page_id": "987654321098765"},
            "instagram": {"ig_user_id": "98765432109876543"},
        }),
        encoding="utf-8",
    )
    for platform in (Platform.FACEBOOK, Platform.INSTAGRAM):
        b.guardar_secreto(platform, {"access_token": TOKEN, "expira_en": time.time() + 3600})
    from socialctl.brands import cargar_brand
    return cargar_brand(tmp_path, "Histopast")


@pytest.fixture
def brand_sin_cuentas(tmp_path):
    """Una marca recién creada: `accounts.yml` trae `page_id: ""` e
    `ig_user_id: ""`, que es como nace con `socialctl brand new`."""
    b = crear_brand(tmp_path, "Nueva")
    (b.raiz / "accounts.yml").write_text(
        yaml.safe_dump({"facebook": {"page_id": ""}, "instagram": {"ig_user_id": ""}}),
        encoding="utf-8",
    )
    for platform in (Platform.FACEBOOK, Platform.INSTAGRAM):
        b.guardar_secreto(platform, {"access_token": TOKEN, "expira_en": time.time() + 3600})
    from socialctl.brands import cargar_brand
    return cargar_brand(tmp_path, "Nueva")


@respx.mock
def test_facebook_lee_un_post_de_video_con_los_valores_reales_de_la_cuenta(brand):
    """Respuesta grabada de la Página real el 2026-09-11 (Graph API v26.0).

    Los números no son redondos porque no son inventados: son los que
    devolvió una publicación de vídeo de `987654321098765`. Eso es lo que
    hace útil el test de la conversión de unidades: `8473` milisegundos son
    `8.5` segundos, y una constante redonda habría dejado pasar un factor
    1000 mal puesto.

    Una Página que publica vídeo tampoco puede quedar etiquetada como imagen:
    `piezas.yml` es con lo que el gestor correlaciona tipo y duración, y si
    todo entra como `imagen` la correlación es falsa desde el primer día.
    """
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(200, json={
        "followers_count": 9570, "id": "987654321098765",
    }))
    respx.get(f"{GRAFO}/987654321098765/posts").mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "604_2", "message": "El corte del mapa",
            "created_time": "2026-09-09T18:00:00+0000",
            "permalink_url": "https://facebook.com/604_2",
            "attachments": {"data": [{"media_type": "video", "target": {"id": "vid_9"}}]},
            "insights": {"data": [
                {"name": "post_reactions_by_type_total", "values": [{"value": {"like": 26, "love": 4}}]},
                {"name": "post_video_views", "values": [{"value": 127}]},
                {"name": "post_video_view_time", "values": [{"value": 3346978}]},
                {"name": "post_video_avg_time_watched", "values": [{"value": 8473}]},
            ]},
            "comments": {"summary": {"total_count": 7}},
            "shares": {"count": 3},
        }],
    }))
    respx.get(f"{GRAFO}/vid_9").mock(return_value=httpx.Response(200, json={
        "id": "vid_9", "length": 42.5,
    }))

    with httpx.Client() as client:
        lectura = FacebookLector().leer(brand, client, None)

    assert lectura.cuenta.seguidores == 9570
    pieza = lectura.piezas[0]
    assert pieza.id == "604_2"
    assert pieza.tipo is TipoPieza.VERTICAL  # 42 s
    assert pieza.duracion_seg == 42
    assert pieza.acumulado.vistas == 127
    assert pieza.acumulado.comentarios == 7
    assert pieza.acumulado.compartidos == 3
    assert pieza.especificas["reacciones"] == 30  # 26 like + 4 love
    # Milisegundos de Meta convertidos a los segundos del resto del proyecto.
    assert pieza.especificas["tiempo_visto_seg"] == 3347.0   # 3346978 ms
    assert pieza.especificas["retencion_media_seg"] == 8.5   # 8473 ms
    assert "alcance" not in pieza.especificas, (
        "en v26.0 no hay métrica de alcance por publicación en Facebook: "
        "el campo no existe, no es que valga None"
    )


@respx.mock
def test_facebook_un_post_que_no_es_de_video_deja_vistas_y_tiempos_en_none(brand):
    """Una foto no tiene vistas ni tiempo visto: la métrica no aplica.

    `None` y no cero, que el gestor de redes leería como «nadie lo vio» y es
    una conclusión falsa sobre una publicación que nunca pudo tener vistas.
    """
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(200, json={
        "followers_count": 9570, "id": "987654321098765",
    }))
    respx.get(f"{GRAFO}/987654321098765/posts").mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "604_1", "message": "Tordesillas",
            "created_time": "2026-08-31T18:00:00+0000",
            "permalink_url": "https://facebook.com/604_1",
            "attachments": {"data": [{"media_type": "photo"}]},
            # En una foto, las tres métricas de vídeo no vienen en la
            # respuesta: la Graph API solo devuelve las que aplican.
            "insights": {"data": [
                {"name": "post_reactions_by_type_total", "values": [{"value": {"like": 26, "love": 4}}]},
            ]},
            "comments": {"summary": {"total_count": 7}},
            "shares": {"count": 3},
        }],
    }))

    with httpx.Client() as client:
        lectura = FacebookLector().leer(brand, client, None)

    pieza = lectura.piezas[0]
    assert pieza.tipo is TipoPieza.IMAGEN  # lo dice attachments.media_type
    assert pieza.duracion_seg is None
    assert pieza.especificas["reacciones"] == 30
    assert pieza.acumulado.vistas is None
    assert pieza.especificas["tiempo_visto_seg"] is None
    assert pieza.especificas["retencion_media_seg"] is None


@respx.mock
def test_facebook_un_403_de_scope_dice_cual_falta(brand):
    """El mismo caso que Instagram ya cubría: Facebook también lo necesita."""
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(403, json={
        "error": {"message": "(#10) Application does not have permission for this action",
                  "type": "OAuthException", "code": 10},
    }))

    with httpx.Client() as client:
        with pytest.raises(SinPermiso) as excinfo:
            FacebookLector().leer(brand, client, None)

    mensaje = str(excinfo.value)
    assert "read_insights" in mensaje
    assert 'socialcli auth facebook' in mensaje


@respx.mock
def test_un_403_con_cuerpo_html_no_revienta_al_interpretarlo(brand):
    """Un proxy intermedio puede devolver HTML, no JSON: `comprobar_permiso`
    no puede caerse con un `JSONDecodeError` al mirarlo."""
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(
        403, text="<html><body>403 Forbidden</body></html>",
    ))

    with httpx.Client() as client:
        with pytest.raises(Exception) as excinfo:
            FacebookLector().leer(brand, client, None)

    assert "JSONDecodeError" not in type(excinfo.value).__name__


@respx.mock
def test_una_marca_sin_ig_user_id_lo_dice_con_claridad(brand_sin_cuentas):
    """`accounts.yml` nace con `ig_user_id: ""`: sin esta comprobación se
    pediría `GET {GRAFO}/` y el error sería opaco."""
    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as excinfo:
            InstagramLector().leer(brand_sin_cuentas, client, None)

    mensaje = str(excinfo.value)
    assert "instagram.ig_user_id" in mensaje
    assert "accounts.yml" in mensaje


@respx.mock
def test_una_marca_sin_page_id_lo_dice_con_claridad(brand_sin_cuentas):
    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as excinfo:
            FacebookLector().leer(brand_sin_cuentas, client, None)

    assert "facebook.page_id" in str(excinfo.value)


@respx.mock
def test_facebook_metrica_retirada_reintenta_sin_insights_y_no_tumba_la_red(brand):
    """El caso real, distinto del de huecos con 200: es el que ya pasó una vez
    y volverá a pasar.

    El 2026-09-11, `post_impressions_unique` y otros siete nombres que este
    plan daba por buenos no devolvían un 200 con huecos: hacían fallar la
    petición ENTERA del listado -`fields=...,insights.metric(a,b,c,d)`- con
    **400** y `(#100) The value must be a valid insights metric`. Sin el
    arreglo, ese `RuntimeError` sube sin capturar por `leer()` y `leer_red`
    convierte la red entera en `EstadoLectura.ERROR`, perdiendo también
    seguidores, publicaciones, likes y comentarios que no dependían de la
    métrica muerta.

    Con el arreglo, el lector reintenta la misma petición sin el bloque de
    insights y entrega las piezas con el resto de sus datos, más la señal de
    degradación (`CLAVE_ENRIQUECIMIENTO_FALLIDO`) para quien inspeccione el
    snapshot.
    """
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(200, json={
        "followers_count": 9570, "id": "987654321098765",
    }))
    respx.get(f"{GRAFO}/987654321098765/posts").mock(side_effect=[
        httpx.Response(400, json={
            "error": {
                "message": "(#100) The value must be a valid insights metric",
                "type": "OAuthException", "code": 100,
            },
        }),
        httpx.Response(200, json={
            "data": [{
                "id": "604_1", "message": "Tordesillas",
                "created_time": "2026-08-31T18:00:00+0000",
                "permalink_url": "https://facebook.com/604_1",
                "attachments": {"data": [{"media_type": "video", "target": {"id": "vid_9"}}]},
                "comments": {"summary": {"total_count": 7}},
                "shares": {"count": 3},
            }],
        }),
    ])
    respx.get(f"{GRAFO}/vid_9").mock(return_value=httpx.Response(200, json={
        "id": "vid_9", "length": 42.5,
    }))

    with httpx.Client() as client:
        lectura = FacebookLector().leer(brand, client, None)

    assert lectura.estado is EstadoLectura.OK  # nunca "error" por esto
    assert lectura.cuenta.seguidores == 9570  # la cuenta no se pierde
    assert len(lectura.piezas) == 1  # la lectura sigue viva
    pieza = lectura.piezas[0]
    assert pieza.id == "604_1"  # la pieza sigue entregándose entera
    # Lo que no dependía de la métrica muerta se sigue leyendo tal cual:
    assert pieza.acumulado.comentarios == 7
    assert pieza.acumulado.compartidos == 3
    assert pieza.tipo is TipoPieza.VERTICAL
    assert pieza.duracion_seg == 42
    # Lo que sí dependía queda en None, nunca en cero:
    assert pieza.especificas["reacciones"] is None
    assert pieza.especificas["tiempo_visto_seg"] is None
    assert pieza.especificas["retencion_media_seg"] is None
    assert pieza.acumulado.vistas is None  # es vídeo, pero la métrica no vino
    # La degradación es visible, con el mismo mecanismo que usa YouTube:
    assert CLAVE_ENRIQUECIMIENTO_FALLIDO in pieza.especificas
    assert "insights metric" in pieza.especificas[CLAVE_ENRIQUECIMIENTO_FALLIDO]


@respx.mock
def test_instagram_metrica_retirada_reintenta_sin_insights_y_no_tumba_la_red(brand):
    """El equivalente de Facebook para Instagram: mismo hallazgo, misma red
    de las dos que hoy no lo cubría.

    El listado de medias -`fields=...,insights.metric(a,b,c,d)`- también
    falla ENTERO con 400 si Meta retira el nombre de una métrica, no con un
    200 con huecos.
    """
    respx.get(f"{GRAFO}/98765432109876543").mock(return_value=httpx.Response(200, json={
        "followers_count": 18, "media_count": 9, "id": "98765432109876543",
    }))
    respx.get(f"{GRAFO}/98765432109876543/media").mock(side_effect=[
        httpx.Response(400, json={
            "error": {
                "message": "(#100) The value must be a valid insights metric",
                "type": "OAuthException", "code": 100,
            },
        }),
        httpx.Response(200, json={
            "data": [{
                "id": "ig_2", "caption": "Sin métricas",
                "media_type": "VIDEO", "timestamp": "2026-09-11T12:00:00+0000",
                "permalink": "https://instagram.com/p/xyz",
                "like_count": 4, "comments_count": 1,
            }],
        }),
    ])

    with httpx.Client() as client:
        lectura = InstagramLector().leer(brand, client, None)

    assert lectura.estado is EstadoLectura.OK
    assert lectura.cuenta.seguidores == 18  # la cuenta no se pierde
    assert len(lectura.piezas) == 1
    pieza = lectura.piezas[0]
    assert pieza.id == "ig_2"
    # Lo que no dependía de insights se sigue leyendo tal cual:
    assert pieza.acumulado.likes == 4
    assert pieza.acumulado.comentarios == 1
    # Lo que sí dependía queda en None, nunca en cero:
    assert pieza.acumulado.vistas is None
    assert pieza.acumulado.guardados is None
    assert pieza.acumulado.compartidos is None
    assert pieza.especificas["alcance"] is None
    assert pieza.especificas["interacciones_totales"] is None
    assert CLAVE_ENRIQUECIMIENTO_FALLIDO in pieza.especificas


@respx.mock
def test_un_400_que_no_es_de_metrica_invalida_sigue_tumbando_la_lectura(brand):
    """El arreglo no puede volverse tolerante con cualquier 400.

    Mismo código (100, "parámetro inválido") que el de una métrica retirada,
    pero otro texto: no debe confundirse con el caso que se degrada, y la
    lectura de la red sigue cayéndose entera, exactamente como antes de este
    arreglo.
    """
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(200, json={
        "followers_count": 9570, "id": "987654321098765",
    }))
    respx.get(f"{GRAFO}/987654321098765/posts").mock(return_value=httpx.Response(400, json={
        "error": {
            "message": "(#100) Invalid parameter",
            "type": "OAuthException", "code": 100,
        },
    }))

    with httpx.Client() as client:
        lectura = leer_red(Platform.FACEBOOK, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert 'Invalid parameter' in (lectura.error or "")


@respx.mock
def test_instagram_lee_cuenta_y_medias(brand):
    """Respuesta grabada de @histo.past el 2026-09-11 (Graph API v26.0).

    18 seguidores y 9 publicaciones son los de la cuenta real; los ceros de
    `saved` y `shares` son ceros de verdad devueltos por la API, no huecos, y
    por eso se afirman como `0` y no como `None`.
    """
    respx.get(f"{GRAFO}/98765432109876543").mock(return_value=httpx.Response(200, json={
        "followers_count": 18, "media_count": 9, "id": "98765432109876543",
    }))
    respx.get(f"{GRAFO}/98765432109876543/media").mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "ig_1", "caption": "Una línea en un mapa",
            "media_type": "VIDEO", "timestamp": "2026-09-10T19:00:00+0000",
            "permalink": "https://instagram.com/p/abc",
            "like_count": 2, "comments_count": 0,
            "insights": {"data": [
                {"name": "reach", "values": [{"value": 132}]},
                {"name": "saved", "values": [{"value": 0}]},
                {"name": "shares", "values": [{"value": 0}]},
                {"name": "total_interactions", "values": [{"value": 2}]},
                {"name": "views", "values": [{"value": 158}]},
            ]},
        }],
    }))

    with httpx.Client() as client:
        lectura = InstagramLector().leer(brand, client, None)

    assert lectura.cuenta.seguidores == 18
    assert lectura.cuenta.total_piezas == 9
    pieza = lectura.piezas[0]
    assert pieza.tipo is TipoPieza.VERTICAL
    assert pieza.acumulado.vistas == 158
    assert pieza.acumulado.likes == 2
    assert pieza.acumulado.comentarios == 0
    assert pieza.acumulado.guardados == 0
    assert pieza.acumulado.compartidos == 0
    assert pieza.especificas["alcance"] == 132  # `reach` sigue vivo en v26.0
    assert pieza.especificas["interacciones_totales"] == 2


@respx.mock
def test_instagram_desde_descarta_lo_anterior(brand):
    respx.get(f"{GRAFO}/98765432109876543").mock(return_value=httpx.Response(200, json={
        "followers_count": 18, "media_count": 9, "id": "98765432109876543",
    }))
    respx.get(f"{GRAFO}/98765432109876543/media").mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "ig_1", "caption": "viejo", "media_type": "IMAGE",
            "timestamp": "2026-01-01T19:00:00+0000",
            "permalink": "https://instagram.com/p/abc",
            "insights": {"data": []},
        }],
    }))

    with httpx.Client() as client:
        lectura = InstagramLector().leer(brand, client, date(2026, 9, 1))

    assert lectura.piezas == []


@respx.mock
def test_falta_de_permiso_dice_cual(brand):
    respx.get(f"{GRAFO}/98765432109876543").mock(return_value=httpx.Response(403, json={
        "error": {"message": "(#10) Application does not have permission for this action",
                  "type": "OAuthException", "code": 10},
    }))

    with httpx.Client() as client:
        with pytest.raises(SinPermiso) as excinfo:
            InstagramLector().leer(brand, client, None)

    mensaje = str(excinfo.value)
    assert "instagram_manage_insights" in mensaje
    assert 'socialcli auth instagram' in mensaje


@respx.mock
def test_el_token_de_facebook_no_llega_a_lecturared_error(brand):
    """Test de fuga, y es el que convierte esto en regresión imposible.

    Lo que se afirma es `LecturaRed.error`, no el texto de una excepción
    suelta: es el campo que `leer_red` guarda, que va al JSON del snapshot y
    a `resumen.md`, y los dos se versionan en git.

    Con `raise_for_status()` este test fallaría: el mensaje de
    `HTTPStatusError` lleva la URL entera, y la Graph API exige el
    `access_token` como parámetro de consulta. El cuerpo, además, refleja la
    petición fallida, que es lo que hace un proxy intermedio ante un 5xx.
    """
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(
        502, text=f"Bad Gateway — upstream: /987654321098765?access_token={TOKEN}",
    ))

    with httpx.Client() as client:
        lectura = leer_red(Platform.FACEBOOK, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert TOKEN not in (lectura.error or ""), (
        "el token no puede acabar en un snapshot versionado"
    )
    assert "502" in lectura.error


@respx.mock
def test_el_token_de_instagram_no_llega_a_lecturared_error(brand):
    """Mismo test de fuga para Instagram: mismo token, misma URL, mismo riesgo."""
    respx.get(f"{GRAFO}/98765432109876543").mock(return_value=httpx.Response(
        502, text=f"Bad Gateway — upstream: /98765432109876543?access_token={TOKEN}",
    ))

    with httpx.Client() as client:
        lectura = leer_red(Platform.INSTAGRAM, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert TOKEN not in (lectura.error or "")
    assert "502" in lectura.error


# --- la hora de Meta se convierte, no se descarta ---------------------------


def test_fecha_meta_convierte_el_huso_en_vez_de_tirarlo():
    """La Graph API devuelve `created_time` en la zona horaria de la app.

    Antes se hacía `.replace(tzinfo=None)`, que se queda con la hora local
    disfrazada de UTC: con `+0200` las 18:00 se guardaban como las 18:00 en
    vez de las 16:00, y con un offset negativo la fecha llegaba a cambiar de
    día. Eso descuadra `--desde` y la comparación con YouTube y TikTok, que
    sí están en UTC.
    """
    assert fecha_meta("2026-08-31T18:00:00+0000") == datetime(2026, 8, 31, 18, 0)
    assert fecha_meta("2026-08-31T18:00:00+0200") == datetime(2026, 8, 31, 16, 0)
    assert fecha_meta("2026-08-31T18:00:00-0700") == datetime(2026, 9, 1, 1, 0)


@respx.mock
def test_facebook_con_un_huso_distinto_de_cero_guarda_la_hora_en_utc(brand):
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(200, json={
        "followers_count": 9570,
    }))
    respx.get(f"{GRAFO}/987654321098765/posts").mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "604_9", "message": "tarde",
            "created_time": "2026-09-09T18:00:00+0200",
            "permalink_url": "https://facebook.com/604_9",
            "attachments": {"data": [{"media_type": "photo"}]},
        }],
    }))

    with httpx.Client() as client:
        lectura = FacebookLector().leer(brand, client, None)

    assert lectura.piezas[0].publicado_el == datetime(2026, 9, 9, 16, 0)


# --- Meta pagina: más de 100 publicaciones no desaparecen en silencio -------


@respx.mock
def test_facebook_sigue_paging_next_y_no_se_queda_en_la_primera_pagina(brand):
    """Antes solo se leía `data` una vez: a partir de la nº 100, nada."""
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(200, json={
        "followers_count": 9570,
    }))
    segunda = f"{GRAFO}/987654321098765/posts?after=cursor2"
    respx.get(f"{GRAFO}/987654321098765/posts", params__contains={"limit": "100"}).mock(
        return_value=httpx.Response(200, json={
            "data": [{
                "id": "604_1", "message": "primera página",
                "created_time": "2026-09-09T18:00:00+0000",
                "permalink_url": "https://facebook.com/604_1",
                "attachments": {"data": [{"media_type": "photo"}]},
            }],
            "paging": {"next": segunda},
        })
    )
    respx.get(segunda).mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "604_2", "message": "segunda página",
            "created_time": "2026-09-08T18:00:00+0000",
            "permalink_url": "https://facebook.com/604_2",
            "attachments": {"data": [{"media_type": "photo"}]},
        }],
        "paging": {},
    }))

    with httpx.Client() as client:
        lectura = FacebookLector().leer(brand, client, None)

    assert [p.id for p in lectura.piezas] == ["604_1", "604_2"]


@respx.mock
def test_instagram_tambien_pagina(brand):
    respx.get(f"{GRAFO}/98765432109876543").mock(return_value=httpx.Response(200, json={
        "followers_count": 1200, "media_count": 210,
    }))
    segunda = f"{GRAFO}/98765432109876543/media?after=cursor2"
    respx.get(f"{GRAFO}/98765432109876543/media", params__contains={"limit": "100"}).mock(
        return_value=httpx.Response(200, json={
            "data": [{
                "id": "ig_1", "caption": "uno", "media_type": "IMAGE",
                "timestamp": "2026-09-09T18:00:00+0000",
                "permalink": "https://instagram.com/p/1",
            }],
            "paging": {"next": segunda},
        })
    )
    respx.get(segunda).mock(return_value=httpx.Response(200, json={
        "data": [{
            "id": "ig_2", "caption": "dos", "media_type": "IMAGE",
            "timestamp": "2026-09-08T18:00:00+0000",
            "permalink": "https://instagram.com/p/2",
        }],
    }))

    with httpx.Client() as client:
        lectura = InstagramLector().leer(brand, client, None)

    assert [p.id for p in lectura.piezas] == ["ig_1", "ig_2"]


@respx.mock
def test_un_fallo_en_una_pagina_posterior_no_se_traga(brand):
    """Un error al paginar sube como cualquier otro error de esta red."""
    respx.get(f"{GRAFO}/987654321098765").mock(return_value=httpx.Response(200, json={
        "followers_count": 9570,
    }))
    segunda = f"{GRAFO}/987654321098765/posts?after=cursor2"
    respx.get(f"{GRAFO}/987654321098765/posts", params__contains={"limit": "100"}).mock(
        return_value=httpx.Response(200, json={
            "data": [{
                "id": "604_1", "message": "primera",
                "created_time": "2026-09-09T18:00:00+0000",
                "permalink_url": "https://facebook.com/604_1",
                "attachments": {"data": [{"media_type": "photo"}]},
            }],
            "paging": {"next": segunda},
        })
    )
    respx.get(segunda).mock(return_value=httpx.Response(500, json={
        "error": {"message": "algo se rompió"}
    }))

    with httpx.Client() as client:
        lectura = leer_red(Platform.FACEBOOK, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert TOKEN not in (lectura.error or "")
