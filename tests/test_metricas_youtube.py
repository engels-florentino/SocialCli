import time
from datetime import date

import httpx
import pytest
import respx

from socialctl.brands import crear_brand
from socialctl.metricas.base import SinPermiso, leer_red
from socialctl.metricas.modelos import EstadoLectura, TipoPieza
from socialctl.metricas.youtube import YouTubeLector
from socialctl.models import Platform

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
CANALES = "https://www.googleapis.com/youtube/v3/channels"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"

#: Token largo a propósito: `socialctl/adapters/errores.py` solo redacta
#: secretos de 8 caracteres o más (ver `_LONGITUD_MINIMA_SECRETO_REDACTABLE`),
#: así que con un `"t"` de relleno el test de fuga no probaría nada.
TOKEN = "ya29.token-de-prueba-largo-como-uno-real"


@pytest.fixture
def brand(tmp_path):
    b = crear_brand(tmp_path, "Histopast")
    b.guardar_secreto(
        Platform.YOUTUBE,
        {"access_token": TOKEN, "refresh_token": "r", "expira_en": time.time() + 3600},
    )
    return b


def _respuesta_analytics(request):
    """Devuelve la respuesta que toca según las dimensiones pedidas.

    `respx.get(ANALYTICS)` casa con cualquier query, así que si el lector se
    dejara un parámetro obligatorio el test grabado no se enteraría. Por eso
    el informe por vídeo se comprueba aquí dentro: es el único de los cuatro
    con requisitos propios, y la API responde 400 si faltan.
    """
    dims = request.url.params.get("dimensions", "")
    if dims == "video":
        params = request.url.params
        assert "maxResults" in params, (
            "el informe por vídeo exige maxResults (≤200); sin él la API da 400"
        )
        assert int(params["maxResults"]) <= 200
        assert params.get("sort"), (
            "el informe por vídeo exige sort; sin él la API da 400"
        )
        return httpx.Response(200, json={
            "columnHeaders": [
                {"name": "video"}, {"name": "views"}, {"name": "estimatedMinutesWatched"},
                {"name": "averageViewDuration"}, {"name": "averageViewPercentage"},
                {"name": "subscribersGained"}, {"name": "likes"}, {"name": "comments"},
                {"name": "shares"},
            ],
            "rows": [["vid1", 254, 1200, 480, 48.5, 12, 30, 4, 6]],
        })
    if dims == "elapsedVideoTimeRatio":
        return httpx.Response(200, json={
            "columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
            "rows": [[0.0, 1.0], [0.5, 0.62], [1.0, 0.31]],
        })
    if dims == "insightTrafficSourceType":
        return httpx.Response(200, json={
            "columnHeaders": [{"name": "insightTrafficSourceType"}, {"name": "views"}],
            "rows": [["YT_SEARCH", 120], ["SHORTS", 80], ["SUBSCRIBER", 54]],
        })
    if dims == "country":
        return httpx.Response(200, json={
            "columnHeaders": [{"name": "country"}, {"name": "views"}],
            "rows": [["MX", 600], ["ES", 400]],
        })
    if dims == "ageGroup":
        return httpx.Response(200, json={
            "columnHeaders": [{"name": "ageGroup"}, {"name": "viewerPercentage"}],
            "rows": [["age25-34", 41.2], ["age35-44", 30.0]],
        })
    raise AssertionError(f"dimensiones no previstas en el test: {dims!r}")


def _monta_respuestas():
    respx.get(CANALES).mock(return_value=httpx.Response(200, json={
        "items": [{
            "id": "UC123",
            "snippet": {"title": "Histopast"},
            "statistics": {"subscriberCount": "4950", "videoCount": "29", "viewCount": "180000"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
        }],
    }))
    respx.get(VIDEOS).mock(return_value=httpx.Response(200, json={
        "items": [{
            "id": "vid1",
            "snippet": {"title": "1496: Santo Domingo", "publishedAt": "2026-09-06T19:00:00Z"},
            "statistics": {"viewCount": "254", "likeCount": "30", "commentCount": "4"},
            "contentDetails": {"duration": "PT16M20S"},
        }],
    }))
    respx.get(ANALYTICS).mock(side_effect=_respuesta_analytics)


@respx.mock
def test_lee_la_cuenta_y_las_piezas(brand):
    with httpx.Client() as client:
        _monta_respuestas()
        lectura = YouTubeLector().leer(brand, client, None)

    assert lectura.cuenta.seguidores == 4950
    assert lectura.cuenta.total_piezas == 29
    assert lectura.cuenta.total_vistas == 180000

    assert len(lectura.piezas) == 1
    pieza = lectura.piezas[0]
    assert pieza.id == "vid1"
    assert pieza.url == "https://www.youtube.com/watch?v=vid1"
    assert pieza.titulo == "1496: Santo Domingo"
    assert pieza.tipo is TipoPieza.LARGO  # 16m20s
    assert pieza.duracion_seg == 980, "la duración la da la API: no se pregunta"
    assert pieza.acumulado.vistas == 254
    assert pieza.acumulado.likes == 30
    assert pieza.acumulado.comentarios == 4
    assert pieza.acumulado.guardados is None  # YouTube no lo mide


@respx.mock
def test_lector_incorpora_reporting_reach_del_canal_autenticado(brand, monkeypatch):
    from socialctl.metricas.analytics_ingest import ObservationStore
    from tests.test_youtube_reach import report

    (brand.raiz / '.socialctl' / 'youtube-observations').mkdir(parents=True)
    def active(self):
        assert self.account_id == 'UC123'
        return [report('reach-1', '2026-09-22T00:00:00Z', 120, 3.0,
                       video_id='vid1', day=date.today().strftime('%Y%m%d'))]
    monkeypatch.setattr(ObservationStore, 'active', active)
    with httpx.Client() as client:
        _monta_respuestas()
        lectura = YouTubeLector().leer(brand, client, None)
    reach = lectura.piezas[0].especificas['thumbnail_reach_28d']
    assert reach['impressions'] == 120
    assert reach['ctr'] == 3.0
    assert reach['report_ids'] == ['reach-1']


@respx.mock
def test_trae_la_curva_de_retencion_y_las_fuentes_de_trafico(brand):
    with httpx.Client() as client:
        _monta_respuestas()
        lectura = YouTubeLector().leer(brand, client, None)

    esp = lectura.piezas[0].especificas
    assert esp["minutos_vistos"] == 1200
    assert esp["duracion_media_seg"] == 480
    assert esp["porcentaje_visto"] == 48.5
    assert esp["suscriptores_ganados"] == 12

    assert esp["curva_retencion"] == [
        {"posicion": 0.0, "ratio": 1.0},
        {"posicion": 0.5, "ratio": 0.62},
        {"posicion": 1.0, "ratio": 0.31},
    ]
    assert esp["fuentes_trafico"] == {"YT_SEARCH": 120, "SHORTS": 80, "SUBSCRIBER": 54}


@respx.mock
def test_trae_la_audiencia_del_canal(brand):
    with httpx.Client() as client:
        _monta_respuestas()
        lectura = YouTubeLector().leer(brand, client, None)

    # Los países se normalizan a porcentaje de vistas, no vistas en bruto.
    assert lectura.audiencia.paises == {"MX": 60.0, "ES": 40.0}
    assert lectura.audiencia.edades == {"age25-34": 41.2, "age35-44": 30.0}


@respx.mock
def test_un_video_corto_y_vertical_es_vertical(brand):
    with httpx.Client() as client:
        respx.get(CANALES).mock(return_value=httpx.Response(200, json={
            "items": [{"id": "UC123", "snippet": {"title": "Histopast"},
                       "statistics": {"subscriberCount": "1", "videoCount": "1", "viewCount": "1"},
                       "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}],
        }))
        respx.get(VIDEOS).mock(return_value=httpx.Response(200, json={
            "items": [{"id": "vid1", "snippet": {"title": "S2", "publishedAt": "2026-09-10T19:00:00Z"},
                       "statistics": {"viewCount": "10"}, "contentDetails": {"duration": "PT25S"}}],
        }))
        respx.get(ANALYTICS).mock(side_effect=_respuesta_analytics)
        lectura = YouTubeLector().leer(brand, client, None)

    assert lectura.piezas[0].tipo is TipoPieza.VERTICAL


@respx.mock
def test_desde_descarta_las_piezas_anteriores(brand):
    with httpx.Client() as client:
        _monta_respuestas()
        lectura = YouTubeLector().leer(brand, client, date(2026, 9, 8))

    assert lectura.piezas == []  # vid1 se publicó el 2026-09-06


@respx.mock
def test_el_informe_por_video_pide_maxresults_y_sort(brand):
    """Sin los dos, la API responde 400 y el lector no devuelve ni una pieza."""
    vistas = []

    def espia(request):
        if request.url.params.get("dimensions") == "video":
            vistas.append(dict(request.url.params))
        return _respuesta_analytics(request)

    with httpx.Client() as client:
        _monta_respuestas()
        respx.get(ANALYTICS).mock(side_effect=espia)
        YouTubeLector().leer(brand, client, None)

    assert len(vistas) == 1, "el informe por vídeo se pide exactamente una vez"
    params = vistas[0]
    assert params["maxResults"] == "200"
    assert params["sort"] == "-views"


@respx.mock
def test_la_cuota_agotada_conserva_el_mensaje_de_la_api_y_no_el_token(brand):
    """El spec (§9) pide `error` con el texto de la API, no un 403 opaco."""
    respx.get(CANALES).mock(return_value=httpx.Response(403, json={
        "error": {
            "code": 403,
            "message": (
                "The request cannot be completed because you have exceeded "
                f"your quota. (token={TOKEN})"
            ),
            "status": "RESOURCE_EXHAUSTED",
            "errors": [{"reason": "quotaExceeded"}],
        },
    }))

    with httpx.Client() as client:
        with pytest.raises(RuntimeError) as excinfo:
            YouTubeLector().leer(brand, client, None)

    mensaje = str(excinfo.value)
    assert "exceeded your quota" in mensaje, "se perdió el mensaje de la API"
    assert TOKEN not in mensaje, "un mensaje de error nunca puede arrastrar el token"


@respx.mock
def test_un_403_de_scope_dice_que_permiso_falta(brand):
    with httpx.Client() as client:
        respx.get(CANALES).mock(return_value=httpx.Response(403, json={
            "error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                      "status": "PERMISSION_DENIED"},
        }))
        with pytest.raises(SinPermiso) as excinfo:
            YouTubeLector().leer(brand, client, None)

    mensaje = str(excinfo.value)
    assert "youtube.readonly" in mensaje
    assert "socialctl auth youtube" in mensaje


@respx.mock
def test_el_token_no_llega_a_lecturared_error(brand):
    """Test de fuga. Lo que se afirma no es el texto de una excepción suelta,
    sino `LecturaRed.error`: es el campo que `leer_red` guarda, que va al JSON
    del snapshot y a `resumen.md`, y los dos se versionan en git.

    El cuerpo de la respuesta refleja la petición fallida, que es lo que hace
    un proxy intermedio ante un 5xx (ver el docstring de
    `socialctl/adapters/errores.py`).
    """
    respx.get(CANALES).mock(return_value=httpx.Response(
        502, text=f"Bad Gateway — upstream: /youtube/v3/channels (token={TOKEN})",
    ))

    with httpx.Client() as client:
        lectura = leer_red(Platform.YOUTUBE, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert TOKEN not in (lectura.error or ""), (
        "el token no puede acabar en un snapshot versionado"
    )
    assert "502" in lectura.error


def _catalogo(*piezas):
    """`videos.list` con un ítem mínimo válido por cada `(id, publicado_el, vistas)`."""
    return httpx.Response(200, json={
        "items": [
            {
                "id": id_video,
                "snippet": {"title": f"Pieza {id_video}", "publishedAt": publicado},
                "statistics": {"viewCount": str(vistas)},
                "contentDetails": {"duration": "PT5M"},
            }
            for id_video, publicado, vistas in piezas
        ],
    })


@respx.mock
def test_falla_la_retencion_de_una_pieza_y_el_resto_va_bien(brand):
    """Aísla el enriquecimiento por pieza (hallazgo de robustez): un fallo
    transitorio en la retención de UNA pieza no puede tirar la cuenta ni las
    demás piezas, que ya se pagaron con el informe por vídeo."""
    respx.get(CANALES).mock(return_value=httpx.Response(200, json={
        "items": [{
            "id": "UC123", "snippet": {"title": "Histopast"},
            "statistics": {"subscriberCount": "4950", "videoCount": "2", "viewCount": "180000"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
        }],
    }))
    respx.get(VIDEOS).mock(return_value=_catalogo(
        ("vid1", "2026-09-06T19:00:00Z", 254),
        ("vid2", "2026-09-07T19:00:00Z", 100),
    ))

    def analytics(request):
        dims = request.url.params.get("dimensions", "")
        filtros = request.url.params.get("filters", "")
        if dims == "video":
            return httpx.Response(200, json={"rows": [
                ["vid1", 254, 1200, 480, 48.5, 12, 30, 4, 6],
                ["vid2", 100, 500, 200, 40.0, 5, 10, 1, 2],
            ]})
        if dims == "elapsedVideoTimeRatio":
            if "video==vid1" in filtros:
                # Fallo transitorio, no de cuota (no es 403): solo debe
                # aislar esta pieza, no tumbar la lectura ni frenar el resto.
                return httpx.Response(500, text="fallo transitorio de Analytics")
            return httpx.Response(200, json={"rows": [[0.0, 1.0], [1.0, 0.5]]})
        if dims == "insightTrafficSourceType":
            return httpx.Response(200, json={"rows": [["YT_SEARCH", 40]]})
        if dims in ("country", "ageGroup"):
            return httpx.Response(200, json={"rows": []})
        raise AssertionError(f"dimensiones no previstas en el test: {dims!r}")

    respx.get(ANALYTICS).mock(side_effect=analytics)

    with httpx.Client() as client:
        lectura = YouTubeLector().leer(brand, client, None)

    assert lectura.cuenta.seguidores == 4950
    assert len(lectura.piezas) == 2, "la pieza con la retención rota no se pierde"

    por_id = {p.id: p for p in lectura.piezas}

    rota = por_id["vid1"]
    assert rota.acumulado.vistas == 254, "las métricas base sobreviven al fallo"
    assert rota.especificas["minutos_vistos"] == 1200
    assert rota.especificas["curva_retencion"] == []
    assert rota.especificas["fuentes_trafico"] == {}

    sana = por_id["vid2"]
    assert sana.acumulado.vistas == 100
    assert sana.especificas["curva_retencion"] == [
        {"posicion": 0.0, "ratio": 1.0}, {"posicion": 1.0, "ratio": 0.5},
    ]
    assert sana.especificas["fuentes_trafico"] == {"YT_SEARCH": 40}


@respx.mock
def test_la_cuota_agotada_a_mitad_del_bucle_no_pierde_ninguna_pieza(brand):
    """Con la cuota agotada en la segunda de tres piezas: las que ya se
    enriquecieron conservan su curva, las posteriores no, y ninguna se
    pierde -ni siquiera se intenta pedir la retención de la tercera."""
    respx.get(CANALES).mock(return_value=httpx.Response(200, json={
        "items": [{
            "id": "UC123", "snippet": {"title": "Histopast"},
            "statistics": {"subscriberCount": "1", "videoCount": "3", "viewCount": "1"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
        }],
    }))
    respx.get(VIDEOS).mock(return_value=_catalogo(
        ("vid1", "2026-09-05T19:00:00Z", 10),
        ("vid2", "2026-09-06T19:00:00Z", 20),
        ("vid3", "2026-09-07T19:00:00Z", 30),
    ))

    llamadas_de_retencion = []

    def analytics(request):
        dims = request.url.params.get("dimensions", "")
        filtros = request.url.params.get("filters", "")
        if dims == "video":
            return httpx.Response(200, json={"rows": [
                ["vid1", 10, 100, 50, 30.0, 1, 1, 0, 0],
                ["vid2", 20, 200, 60, 35.0, 2, 2, 0, 0],
                ["vid3", 30, 300, 70, 40.0, 3, 3, 0, 0],
            ]})
        if dims == "elapsedVideoTimeRatio":
            llamadas_de_retencion.append(filtros)
            if "video==vid1" in filtros:
                return httpx.Response(200, json={"rows": [[0.0, 1.0]]})
            if "video==vid2" in filtros:
                # 403 sin texto de scope: por descarte, cuota agotada.
                return httpx.Response(403, json={
                    "error": {
                        "message": "Quota exceeded for quota metric 'Queries'.",
                        "status": "RESOURCE_EXHAUSTED",
                        "errors": [{"reason": "quotaExceeded"}],
                    },
                })
            raise AssertionError(f"no debió pedirse retención para {filtros!r}")
        if dims == "insightTrafficSourceType":
            if "video==vid1" in filtros:
                return httpx.Response(200, json={"rows": [["YT_SEARCH", 5]]})
            raise AssertionError(f"no debió pedirse tráfico para {filtros!r}")
        if dims in ("country", "ageGroup"):
            return httpx.Response(200, json={"rows": []})
        raise AssertionError(f"dimensiones no previstas en el test: {dims!r}")

    respx.get(ANALYTICS).mock(side_effect=analytics)

    with httpx.Client() as client:
        lectura = YouTubeLector().leer(brand, client, None)

    assert len(lectura.piezas) == 3, "ninguna pieza se pierde por la cuota agotada"
    por_id = {p.id: p for p in lectura.piezas}

    assert por_id["vid1"].especificas["curva_retencion"] == [{"posicion": 0.0, "ratio": 1.0}]
    assert por_id["vid1"].especificas["fuentes_trafico"] == {"YT_SEARCH": 5}

    assert por_id["vid2"].especificas["curva_retencion"] == []
    assert por_id["vid2"].especificas["fuentes_trafico"] == {}
    assert por_id["vid2"].acumulado.vistas == 20, "la métrica base de vid2 se conserva"

    assert por_id["vid3"].especificas["curva_retencion"] == []
    assert por_id["vid3"].especificas["fuentes_trafico"] == {}
    assert por_id["vid3"].acumulado.vistas == 30, "la métrica base de vid3 se conserva"

    assert len(llamadas_de_retencion) == 2, (
        "vid3 no debió ni intentarse: la cuota ya estaba agotada desde vid2"
    )


@respx.mock
def test_un_fallo_del_catalogo_antes_del_bucle_tumba_la_lectura_entera(brand):
    """El catálogo (`videos.list`) ocurre antes del bucle de enriquecimiento:
    sin él no hay ninguna pieza que salvar, así que el arreglo de aislar el
    bucle no debe volverse tan tolerante que se trague este fallo."""
    respx.get(CANALES).mock(return_value=httpx.Response(200, json={
        "items": [{
            "id": "UC123", "snippet": {"title": "Histopast"},
            "statistics": {"subscriberCount": "1", "videoCount": "1", "viewCount": "1"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
        }],
    }))
    respx.get(ANALYTICS).mock(side_effect=_respuesta_analytics)
    respx.get(VIDEOS).mock(return_value=httpx.Response(500, text="Internal Server Error"))

    with httpx.Client() as client:
        with pytest.raises(RuntimeError):
            YouTubeLector().leer(brand, client, None)

        lectura = leer_red(Platform.YOUTUBE, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert lectura.piezas == [], "sin catálogo no hay ninguna pieza que conservar"


@respx.mock
def test_la_pieza_con_enriquecimiento_fallido_lo_dice_sin_arrastrar_el_token(brand):
    """Mecanismo elegido para que una lectura parcial no parezca completa: la
    pieza afectada trae `enriquecimiento_fallido` en `especificas`, con el
    motivo ya redactado; una pieza sana ni siquiera tiene esa clave -su
    ausencia es la señal de que a ESA pieza no le falta nada."""
    respx.get(CANALES).mock(return_value=httpx.Response(200, json={
        "items": [{
            "id": "UC123", "snippet": {"title": "Histopast"},
            "statistics": {"subscriberCount": "1", "videoCount": "2", "viewCount": "1"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
        }],
    }))
    respx.get(VIDEOS).mock(return_value=_catalogo(
        ("vid1", "2026-09-06T19:00:00Z", 5),
        ("vid2", "2026-09-07T19:00:00Z", 6),
    ))

    def analytics(request):
        dims = request.url.params.get("dimensions", "")
        filtros = request.url.params.get("filters", "")
        if dims == "video":
            return httpx.Response(200, json={"rows": [
                ["vid1", 5, 50, 30, 20.0, 1, 0, 0, 0],
                ["vid2", 6, 60, 30, 20.0, 1, 0, 0, 0],
            ]})
        if dims == "elapsedVideoTimeRatio" and "video==vid1" in filtros:
            # Cuerpo que refleja la petición fallida, como haría un proxy
            # intermedio ante un 5xx (ver docstring de adapters/errores.py).
            return httpx.Response(
                502, text=f"Bad Gateway — upstream: /v2/reports (token={TOKEN})",
            )
        if dims == "elapsedVideoTimeRatio":
            return httpx.Response(200, json={"rows": [[0.0, 1.0]]})
        if dims == "insightTrafficSourceType":
            return httpx.Response(200, json={"rows": [["YT_SEARCH", 1]]})
        if dims in ("country", "ageGroup"):
            return httpx.Response(200, json={"rows": []})
        raise AssertionError(f"dimensiones no previstas en el test: {dims!r}")

    respx.get(ANALYTICS).mock(side_effect=analytics)

    with httpx.Client() as client:
        lectura = YouTubeLector().leer(brand, client, None)

    por_id = {p.id: p for p in lectura.piezas}

    rota = por_id["vid1"]
    assert "enriquecimiento_fallido" in rota.especificas
    aviso = rota.especificas["enriquecimiento_fallido"]
    assert TOKEN not in aviso, "el aviso de una pieza parcial no puede arrastrar el token"
    assert "502" in aviso

    sana = por_id["vid2"]
    assert "enriquecimiento_fallido" not in sana.especificas, (
        "una pieza sin problemas ni siquiera lleva la clave"
    )
