import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.adapters.facebook import GRAFO as GRAFO_FACEBOOK
from socialctl.adapters.facebook import FacebookAdapter
from socialctl.adapters.instagram import GRAFO as GRAFO_INSTAGRAM
from socialctl.adapters.instagram import InstagramAdapter
from socialctl.brands import cargar_brand, crear_brand
from socialctl.formatter import componer_caption
from socialctl.models import (
    CampaignType,
    MediaAsset,
    MediaKind,
    Platform,
    PlatformPost,
    Post,
    PostResult,
    PostStatus,
)
from socialctl.publisher import (
    PersistenciaError,
    SlugInvalido,
    anexar_historial,
    guardar_resultado,
    publicar,
    render_preview,
    validar_todo,
)


class AdaptadorOK(Adapter):
    platform = Platform.FACEBOOK

    def publish(self, post, brand, client):
        return PostResult(platform=self.platform, status=PostStatus.PUBLICADO,
                          url="https://facebook.com/1", platform_id="1")


class AdaptadorQueRevienta(Adapter):
    platform = Platform.INSTAGRAM

    def validate(self, post, brand):
        return []

    def publish(self, post, brand, client):
        raise RuntimeError("la red se cayo")


class AdaptadorCuyoValidateRevienta(Adapter):
    platform = Platform.FACEBOOK

    def validate(self, post, brand):
        raise RuntimeError("el validador tiene un bug")

    def publish(self, post, brand, client):
        raise AssertionError("no deberia llegar a publicarse en este test")


class AdaptadorCuyaConstruccionRevienta(Adapter):
    platform = Platform.FACEBOOK

    def __init__(self):
        raise RuntimeError("el constructor tiene un bug")

    def validate(self, post, brand):
        raise AssertionError("no deberia llegar a validar en este test")

    def publish(self, post, brand, client):
        raise AssertionError("no deberia llegar a publicarse en este test")


@pytest.fixture
def brand(tmp_path):
    """Una marca con `facebook.page_id` configurado.

    Desde que `FacebookAdapter.validate()` también comprueba la
    configuración de la cuenta (hallazgo I4 de la revisión final: esas
    comprobaciones deben verse en el preview, no solo al publicar), la
    plantilla en blanco de `crear_brand` (page_id vacío) haría que
    `validar_todo` marcara Facebook con un error por un motivo ajeno a lo
    que cada test de este fichero pretende comprobar.
    """
    b = crear_brand(tmp_path, "Histopast")
    (b.raiz / "accounts.yml").write_text(
        "facebook:\n  page_id: '12345'\n", encoding="utf-8"
    )
    return cargar_brand(tmp_path, "Histopast")


@pytest.fixture
def post():
    return Post(
        slug="2026-09-07-santo-domingo",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[]),
            Platform.INSTAGRAM: PlatformPost(platform=Platform.INSTAGRAM, body="hola", media=[]),
        },
    )


@pytest.fixture
def post_completo():
    """Un post con las cuatro redes, para los tests de fusion de resultado.json."""
    return Post(
        slug="2026-09-07-post-completo",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.YOUTUBE: PlatformPost(platform=Platform.YOUTUBE, body="hola", media=[]),
            Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[]),
            Platform.INSTAGRAM: PlatformPost(platform=Platform.INSTAGRAM, body="hola", media=[]),
            Platform.TIKTOK: PlatformPost(platform=Platform.TIKTOK, body="hola", media=[]),
        },
    )


def test_validar_todo_devuelve_errores_por_red(post, brand):
    errores = validar_todo(post, brand)
    # Instagram exige media y no la tiene; Facebook acepta solo texto.
    assert errores[Platform.INSTAGRAM]
    assert errores[Platform.FACEBOOK] == []


def test_preview_muestra_las_redes_y_los_problemas(post, brand):
    texto = render_preview(post, validar_todo(post, brand))
    assert "facebook" in texto.lower()
    assert "instagram" in texto.lower()
    assert "exige imagen o video" in texto


def test_preview_muestra_la_subcarpeta_completa_de_la_media(tmp_path, brand):
    """El usuario organiza su media en subcarpetas (p. ej. `short/` para
    vídeos verticales): el preview debe mostrar `short/S2.mp4`, no solo
    `S2.mp4`, para que se vea de qué carpeta sale de verdad -y no confiar
    en que el usuario recuerde dónde puso cada archivo.
    """
    video = tmp_path / "S2.mp4"
    video.write_bytes(b"bytes")
    post = Post(
        slug="2026-09-07-con-subcarpeta",
        brand="Histopast",
        campaign=CampaignType.CLIP_VERTICAL,
        platforms={
            Platform.TIKTOK: PlatformPost(
                platform=Platform.TIKTOK,
                body="hola",
                media=[MediaAsset(
                    path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
                    duration_s=10.0, size_bytes=5, ruta_relativa="short/S2.mp4",
                )],
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    assert "Media: short/S2.mp4 " in texto
    assert str(video) not in texto


def test_preview_youtube_no_incrusta_hashtags_en_el_texto_y_los_muestra_como_etiquetas(brand):
    """adapters/youtube.py publica `body` tal cual como descripcion y manda
    los hashtags aparte, como `snippet.tags` (metadatos internos que nunca
    aparecen en el texto de la descripcion). El preview tiene que reflejar
    eso: nada de pegar los hashtags al texto con `componer_caption` como se
    hacia antes, que hacia aprobar al usuario un texto que YouTube jamas
    iba a publicar tal cual.
    """
    body = "Un documental sobre la fundacion de Roma"
    hashtags = ["historia", "roma"]
    post = Post(
        slug="2026-09-07-roma",
        brand="Histopast",
        campaign=CampaignType.LANZAMIENTO_VIDEO_LARGO,
        platforms={
            Platform.YOUTUBE: PlatformPost(
                platform=Platform.YOUTUBE,
                title="La fundacion de Roma",
                body=body,
                hashtags=hashtags,
                media=[],
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    # El texto compuesto (cuerpo + hashtags pegados con "\n\n#...") no debe
    # aparecer en ningun sitio: eso es justo lo que YouTube NO publica.
    assert componer_caption(body, hashtags) not in texto
    assert "#historia" not in texto
    assert "#roma" not in texto
    # El cuerpo real (sin hashtags) si tiene que aparecer, tal cual se publica.
    assert body in texto
    # Y los hashtags deben verse, pero como lo que son: un metadato aparte,
    # nunca como parte del texto publicado.
    assert (
        "Etiquetas (metadato de la red; NO aparecen en el texto anterior): "
        "historia, roma" in texto
    )


# --- Campo de privacidad configurable (YouTube): el preview debe mostrarlo,
# porque es lo unico que el usuario aprueba y este campo cambia radicalmente
# lo que ocurre al publicar (ver PLATFORM_SPECS.valores_privacidad).


def test_preview_youtube_muestra_private_como_valor_por_defecto(brand):
    post = Post(
        slug="2026-09-07-roma",
        brand="Histopast",
        campaign=CampaignType.LANZAMIENTO_VIDEO_LARGO,
        platforms={
            Platform.YOUTUBE: PlatformPost(
                platform=Platform.YOUTUBE,
                title="La fundacion de Roma",
                body="cuerpo",
                media=[],
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    assert "Privacidad: private" in texto
    assert "PÚBLICO" not in texto


def test_preview_youtube_avisa_con_claridad_si_va_a_salir_publico(brand):
    post = Post(
        slug="2026-09-07-roma",
        brand="Histopast",
        campaign=CampaignType.LANZAMIENTO_VIDEO_LARGO,
        platforms={
            Platform.YOUTUBE: PlatformPost(
                platform=Platform.YOUTUBE,
                title="La fundacion de Roma",
                body="cuerpo",
                media=[],
                privacy="public",
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    assert "Privacidad: public" in texto
    assert "PÚBLICO" in texto


def test_preview_youtube_muestra_unlisted_sin_aviso_de_publico(brand):
    post = Post(
        slug="2026-09-07-roma",
        brand="Histopast",
        campaign=CampaignType.LANZAMIENTO_VIDEO_LARGO,
        platforms={
            Platform.YOUTUBE: PlatformPost(
                platform=Platform.YOUTUBE,
                title="La fundacion de Roma",
                body="cuerpo",
                media=[],
                privacy="unlisted",
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    assert "Privacidad: unlisted" in texto
    assert "PÚBLICO" not in texto


def test_preview_de_otras_redes_no_muestra_linea_de_privacidad(brand):
    post = Post(
        slug="2026-09-07-otras-redes",
        brand="Histopast",
        campaign=CampaignType.CLIP_VERTICAL,
        platforms={
            Platform.FACEBOOK: PlatformPost(
                platform=Platform.FACEBOOK, body="cuerpo", media=[]
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    assert "Privacidad:" not in texto


def test_preview_de_las_demas_redes_sigue_mostrando_los_hashtags_compuestos(brand):
    """Facebook, Instagram y TikTok si incrustan los hashtags en el propio
    texto publicado (ver `componer_caption` en cada adaptador): para ellas
    el preview no cambia con el arreglo de YouTube.
    """
    body = "Nuevo video del canal"
    hashtags = ["historia", "documental"]
    post = Post(
        slug="2026-09-07-otras-redes",
        brand="Histopast",
        campaign=CampaignType.CLIP_VERTICAL,
        platforms={
            Platform.FACEBOOK: PlatformPost(
                platform=Platform.FACEBOOK, body=body, hashtags=hashtags, media=[]
            ),
            Platform.INSTAGRAM: PlatformPost(
                platform=Platform.INSTAGRAM, body=body, hashtags=hashtags, media=[]
            ),
            Platform.TIKTOK: PlatformPost(
                platform=Platform.TIKTOK, body=body, hashtags=hashtags, media=[]
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    # El texto compuesto (cuerpo + hashtags) tiene que aparecer una vez por
    # cada una de las tres redes, exactamente como lo publica cada adaptador.
    esperado = componer_caption(body, hashtags)
    assert texto.count(esperado) == 3
    # Ninguna de las tres necesita la linea de "etiquetas aparte": sus
    # hashtags ya estan dentro del texto anterior.
    assert "Etiquetas (metadato de la red" not in texto


def test_preview_facebook_incluye_el_enlace_al_final_del_texto_si_lo_hay(brand):
    """adapters/facebook.py añade `post.link` (si lo hay) al final del texto
    compuesto -es la unica de las cuatro redes que lee ese campo-. El
    preview tenia el mismo problema de fondo que YouTube (mostraba un texto
    distinto del que de verdad se publica), solo que en sentido contrario:
    se quedaba corto, sin mostrar el enlace que si iba a aparecer.
    """
    body = "Nuevo articulo del blog"
    hashtags = ["historia"]
    link = "https://histopast.example/articulo"
    post = Post(
        slug="2026-09-07-con-enlace",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(
                platform=Platform.FACEBOOK,
                body=body,
                hashtags=hashtags,
                media=[],
                link=link,
            ),
        },
    )

    texto = render_preview(post, validar_todo(post, brand))

    esperado = f"{componer_caption(body, hashtags)}\n\n{link}"
    assert esperado in texto


# --- Hallazgo I3 (revisión final): el preview debe reflejar con honestidad
# que `--only` va a dejar redes fuera, sin dejar de mostrarlas íntegras.


def test_preview_marca_las_redes_que_only_deja_fuera(brand):
    post_dos_redes = Post(
        slug="2026-09-07-solo-una",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[]),
            Platform.INSTAGRAM: PlatformPost(platform=Platform.INSTAGRAM, body="hola", media=[]),
        },
    )
    errores = validar_todo(post_dos_redes, brand)

    texto = render_preview(post_dos_redes, errores, destinos=[Platform.FACEBOOK])

    assert "no solicitada" in texto.lower()
    # Ambas redes siguen apareciendo íntegras (fidelidad): la exclusión se
    # marca, no se oculta el contenido de la red descartada.
    assert "facebook" in texto.lower()
    assert "instagram" in texto.lower()
    assert "exige imagen o video" in texto  # el problema de Instagram sigue visible


def test_preview_sin_destinos_no_marca_ninguna_exclusion(post, brand):
    texto = render_preview(post, validar_todo(post, brand))
    assert "no solicitada" not in texto.lower()


def test_preview_con_destinos_igual_a_todas_las_redes_no_marca_exclusion(post, brand):
    """Si `destinos` cubre TODAS las redes del post (--only con todas), no
    hay ninguna exclusión real que anunciar."""
    texto = render_preview(post, validar_todo(post, brand), destinos=list(post.platforms))
    assert "no solicitada" not in texto.lower()


# --- Hallazgo Menor 4 (revisión final del arreglo de --only): `retry`
# reutiliza `destinos`, pero el motivo real de su exclusión no es "--only"
# (que el usuario puede no haber escrito nunca): `render_preview` debe usar
# el motivo que le pasen, no uno fijo.


def test_preview_admite_un_motivo_de_exclusion_personalizado(brand):
    post_dos_redes = Post(
        slug="2026-09-07-solo-una",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[]),
            Platform.INSTAGRAM: PlatformPost(platform=Platform.INSTAGRAM, body="hola", media=[]),
        },
    )
    errores = validar_todo(post_dos_redes, brand)

    texto = render_preview(
        post_dos_redes, errores, destinos=[Platform.FACEBOOK],
        motivo_exclusion="no se reintenta en este intento",
    )

    # El motivo pasado explícitamente aparece tanto en la cabecera como en
    # la marca de la red concreta...
    assert "no se reintenta en este intento" in texto
    # ...y el texto fijo de --only, que aquí sería falso (el usuario de un
    # retry no tiene por qué haber escrito nunca esa opción), no aparece.
    assert "--only" not in texto


def test_preview_sin_motivo_explicito_sigue_usando_el_de_only_por_defecto(brand):
    """Sin `motivo_exclusion`, el comportamiento no cambia para quien ya
    llamaba a `render_preview` solo con `destinos` (el caso de `publish`)."""
    post_dos_redes = Post(
        slug="2026-09-07-solo-una",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[]),
            Platform.INSTAGRAM: PlatformPost(platform=Platform.INSTAGRAM, body="hola", media=[]),
        },
    )
    errores = validar_todo(post_dos_redes, brand)

    texto = render_preview(post_dos_redes, errores, destinos=[Platform.FACEBOOK])

    assert "no solicitada con --only" in texto


# --- Hallazgo Menor 5: "Problemas detectados: N" ya no es necesariamente el
# número de problemas que bloquean la publicación cuando `--only` deja
# alguno de ellos fuera (los de una red no solicitada no bloquean).


def test_preview_desglosa_problemas_bloqueantes_si_only_excluye_una_red_con_errores(brand):
    post_dos_redes = Post(
        slug="2026-09-07-solo-una",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[]),
            Platform.INSTAGRAM: PlatformPost(platform=Platform.INSTAGRAM, body="hola", media=[]),
        },
    )
    # Instagram (sin media, sin ig_user_id ni media_url_base en accounts.yml)
    # tiene varios errores; Facebook (con page_id configurado), ninguno.
    errores = validar_todo(post_dos_redes, brand)
    total = len(errores[Platform.FACEBOOK]) + len(errores[Platform.INSTAGRAM])
    assert len(errores[Platform.FACEBOOK]) == 0
    assert total > 0  # si esto deja de cumplirse, el test no prueba nada

    # --only facebook: todos los problemas reales quedan en la red excluida,
    # así que 0 problemas bloquean, aunque el total no sea 0.
    texto = render_preview(post_dos_redes, errores, destinos=[Platform.FACEBOOK])

    assert f"Problemas detectados: {total} (0 en las redes que se van a publicar)" in texto


def test_preview_no_desglosa_si_el_total_coincide_con_lo_bloqueante(brand):
    post_dos_redes = Post(
        slug="2026-09-07-solo-una",
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[]),
            Platform.INSTAGRAM: PlatformPost(platform=Platform.INSTAGRAM, body="hola", media=[]),
        },
    )
    errores = validar_todo(post_dos_redes, brand)
    total = len(errores[Platform.FACEBOOK]) + len(errores[Platform.INSTAGRAM])
    assert len(errores[Platform.FACEBOOK]) == 0
    assert total > 0  # si esto deja de cumplirse, el test no prueba nada

    # --only instagram: todos los problemas reales (los de Instagram) SÍ
    # están entre las redes que se van a publicar, así que total y
    # bloqueantes coinciden y no hace falta desglosar.
    texto = render_preview(post_dos_redes, errores, destinos=[Platform.INSTAGRAM])

    assert f"Problemas detectados: {total}" in texto
    assert "en las redes que se van a publicar" not in texto


def test_un_fallo_no_impide_publicar_en_las_demas(post, brand, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, AdaptadorQueRevienta)

    resultados = {r.platform: r for r in publicar(post, brand)}

    assert resultados[Platform.FACEBOOK].status is PostStatus.PUBLICADO
    assert resultados[Platform.INSTAGRAM].status is PostStatus.ERROR
    assert resultados[Platform.INSTAGRAM].riesgo_duplicado is True
    assert "la red se cayo" not in resultados[Platform.INSTAGRAM].error
    assert resultados[Platform.INSTAGRAM].riesgo_duplicado is True


def test_solo_publica_las_redes_indicadas(post, brand, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, AdaptadorQueRevienta)

    resultados = publicar(post, brand, solo=[Platform.FACEBOOK])

    assert [r.platform for r in resultados] == [Platform.FACEBOOK]


def test_on_progreso_se_llama_para_cada_red_antes_de_publicar(post, brand, monkeypatch):
    """El usuario no ve ninguna senal de vida mientras se publica (hallazgo
    de revision): `on_progreso` es el hook que permite al CLI avisar en que
    red esta trabajando, llamado justo ANTES de intentar publicar en ella."""
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, AdaptadorQueRevienta)

    avisos = []

    class EspiaFacebook(AdaptadorOK):
        def publish(self, post, brand, client):
            assert avisos[-1] == Platform.FACEBOOK  # ya avisado antes de publicar
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, EspiaFacebook)

    publicar(post, brand, on_progreso=avisos.append)

    assert avisos == [Platform.FACEBOOK, Platform.INSTAGRAM]


def test_on_progreso_solo_se_llama_para_las_redes_publicadas(post, brand, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, AdaptadorQueRevienta)

    avisos = []
    publicar(post, brand, solo=[Platform.FACEBOOK], on_progreso=avisos.append)

    assert avisos == [Platform.FACEBOOK]


def test_on_progreso_ausente_no_rompe_la_publicacion(post, brand, monkeypatch):
    """`on_progreso` es opcional (valor por defecto `None`): el codigo
    existente que llama a `publicar()` sin ese argumento debe seguir
    funcionando exactamente igual que antes."""
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)

    resultados = publicar(post, brand, solo=[Platform.FACEBOOK])

    assert resultados[0].status is PostStatus.PUBLICADO


def test_on_progreso_que_revienta_no_impide_publicar(post, brand, monkeypatch):
    """Un fallo en el propio callback (p. ej. al escribir en una consola
    cerrada) no debe impedir que la publicacion se intente igualmente."""
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)

    def _revienta(platform):
        raise RuntimeError("la consola ha muerto")

    resultados = publicar(post, brand, solo=[Platform.FACEBOOK], on_progreso=_revienta)

    assert resultados[0].status is PostStatus.PUBLICADO


def test_guardar_resultado_escribe_el_json(post, brand):
    resultados = [PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                             url="https://facebook.com/1")]
    destino = guardar_resultado(post, brand, resultados)

    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["slug"] == post.slug
    assert datos["resultados"][0]["url"] == "https://facebook.com/1"


def test_guardar_resultado_persiste_riesgo_duplicado(post, brand):
    """Hallazgo de revisión: `retry` decide si puede reintentar una red a
    partir de `riesgo_duplicado`, leyéndolo de resultado.json en disco -no
    de los `PostResult` en memoria de la ejecución que los produjo, que ya
    no existen en una invocación posterior-. Si `guardar_resultado` no
    serializara este campo (o `PostResult.model_dump()` lo omitiera), el
    campo se perdería en el viaje y el arreglo del hallazgo no serviría de
    nada: por eso este test relee el JSON de disco, no el objeto en
    memoria que se le pasó a `guardar_resultado`."""
    resultados = [
        PostResult(
            platform=Platform.INSTAGRAM, status=PostStatus.ERROR,
            error="la red respondio de forma ambigua tras aceptar el contenido",
            riesgo_duplicado=True,
        ),
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
    ]
    destino = guardar_resultado(post, brand, resultados)

    datos = json.loads(destino.read_text(encoding="utf-8"))
    por_red = {r["platform"]: r for r in datos["resultados"]}
    assert por_red["instagram"]["riesgo_duplicado"] is True
    # El valor por defecto (False) también debe persistirse de forma
    # explícita, no quedar ausente: `retry` solo trata como "no
    # arriesgada" una entrada donde este campo sea `False` o esté
    # ausente (resultado.json anterior a este campo), nunca una entrada
    # ambigua a medio serializar.
    assert por_red["facebook"]["riesgo_duplicado"] is False


def test_guardar_resultado_persiste_observacion_youtube_sin_confundir_intencion(post, brand):
    result = PostResult(
        platform=Platform.YOUTUBE,
        status=PostStatus.PUBLICADO,
        platform_id="R4cUGeaKrfU",
        requested_privacy="public",
        observed_privacy="private",
        observed_publish_at="2026-09-14T20:00:00Z",
        observed_processing_status="processing",
        visibility_observed_at="2026-09-13T20:00:00Z",
    )

    destino = guardar_resultado(post, brand, [result])
    stored = json.loads(destino.read_text(encoding="utf-8"))["resultados"][0]

    assert stored["requested_privacy"] == "public"
    assert stored["observed_privacy"] == "private"
    assert stored["observed_publish_at"] == "2026-09-14T20:00:00Z"
    assert stored["observed_processing_status"] == "processing"
    assert stored["visibility_observed_at"] == "2026-09-13T20:00:00Z"


def test_historial_anota_una_linea_por_publicacion(post, brand):
    resultados = [PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                             url="https://facebook.com/1")]
    anexar_historial(post, brand, resultados)

    historial = (brand.raiz / "historial.md").read_text(encoding="utf-8")
    assert post.slug in historial
    assert "https://facebook.com/1" in historial


def test_historial_youtube_etiqueta_estado_y_momento_de_observacion(post, brand):
    anexar_historial(
        post,
        brand,
        [
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PUBLICADO,
                url="https://www.youtube.com/watch?v=R4cUGeaKrfU",
                observed_privacy="private",
                visibility_observed_at="2026-09-13T20:00:00Z",
            )
        ],
    )

    historial = (brand.raiz / "historial.md").read_text(encoding="utf-8")
    assert "subida confirmada; privado (observado 2026-09-13T20:00:00Z)" in historial


def test_historial_youtube_sin_lectura_no_presenta_intencion_como_observacion(post, brand):
    anexar_historial(
        post,
        brand,
        [
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PUBLICADO,
                requested_privacy="public",
                url="https://www.youtube.com/watch?v=R4cUGeaKrfU",
            )
        ],
    )

    historial = (brand.raiz / "historial.md").read_text(encoding="utf-8")
    assert "subida confirmada; visibilidad no verificada" in historial
    assert "(observado" not in historial


# --- Robustez adicional: aislamiento de fallos en validar_todo/publicar,
# persistencia (directorio ausente, sin permisos, historial con contenido
# previo), honestidad de PENDIENTE_CONFIRMACION y no filtrado de secretos. ---


def test_validar_todo_aisla_el_fallo_de_validate_de_una_red(post, brand, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorCuyoValidateRevienta)

    errores = validar_todo(post, brand)

    # Facebook: el propio validate() revienta, pero eso no debe impedir ver
    # los problemas de Instagram (que sigue evaluandose con normalidad).
    assert errores[Platform.FACEBOOK]
    assert "validacion" in {e.campo for e in errores[Platform.FACEBOOK]}
    assert errores[Platform.INSTAGRAM]  # Instagram exige media y no la tiene


def test_validar_todo_sin_adaptador_registrado_no_revienta(post, brand, monkeypatch):
    monkeypatch.delitem(ADAPTADORES, Platform.INSTAGRAM)

    errores = validar_todo(post, brand)

    assert errores[Platform.FACEBOOK] == []
    motivos = [e.motivo for e in errores[Platform.INSTAGRAM]]
    assert any("no hay ningun adaptador registrado" in m for m in motivos)


def test_publicar_sin_adaptador_registrado_da_resultado_de_error(post, brand, monkeypatch):
    monkeypatch.delitem(ADAPTADORES, Platform.FACEBOOK)

    resultados = publicar(post, brand, solo=[Platform.FACEBOOK])

    assert resultados[0].platform is Platform.FACEBOOK
    assert resultados[0].status is PostStatus.ERROR
    assert "no hay ningun adaptador registrado" in resultados[0].error
    assert resultados[0].riesgo_duplicado is False


def test_pendiente_confirmacion_no_se_confunde_con_publicado_en_historial(post, brand):
    resultados = [
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
        PostResult(platform=Platform.INSTAGRAM, status=PostStatus.PENDIENTE_CONFIRMACION,
                   url="https://instagram.com/pendiente"),
    ]
    anexar_historial(post, brand, resultados)

    lineas = (brand.raiz / "historial.md").read_text(encoding="utf-8").splitlines()
    linea_facebook = next(l for l in lineas if l.startswith("- facebook"))
    linea_instagram = next(l for l in lineas if l.startswith("- instagram"))

    assert linea_facebook == (
        "- facebook: OK publicación confirmada https://facebook.com/1"
    )
    assert linea_instagram == (
        "- instagram: PENDIENTE pendiente_confirmacion "
        "https://instagram.com/pendiente"
    )


def test_guardar_resultado_conserva_el_estado_pendiente_de_confirmacion(post, brand):
    resultados = [PostResult(platform=Platform.INSTAGRAM,
                             status=PostStatus.PENDIENTE_CONFIRMACION,
                             url="https://instagram.com/pendiente")]
    destino = guardar_resultado(post, brand, resultados)

    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["resultados"][0]["status"] == "pendiente_confirmacion"


def test_anexar_historial_conserva_entradas_previas(post, brand):
    anexar_historial(post, brand, [
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
    ])
    otro_post = post.model_copy(update={"slug": "2026-09-08-otro-post"})
    anexar_historial(otro_post, brand, [
        PostResult(platform=Platform.INSTAGRAM, status=PostStatus.PUBLICADO,
                   url="https://instagram.com/2"),
    ])

    historial = (brand.raiz / "historial.md").read_text(encoding="utf-8")
    assert "# Historial de Histopast" in historial  # cabecera de crear_brand
    assert post.slug in historial
    assert otro_post.slug in historial
    assert "https://facebook.com/1" in historial
    assert "https://instagram.com/2" in historial


def test_nunca_se_escribe_un_secreto_en_resultado_json(post, brand):
    """Hallazgo C1/M1 de la revisión final: la versión anterior de este test
    guardaba un token en `.secrets/` y luego pasaba a `guardar_resultado` un
    `PostResult` que NUNCA había contenido ese token -la aserción no podía
    fallar por construcción, así que no habría detectado la fuga real-.

    Este test reproduce el escenario de verdad: un 502 de un proxy que
    refleja la URL del sondeo de Instagram (token de query incluido, cuerpo
    NO JSON) durante una publicación real contra `InstagramAdapter`. El
    `PostResult` que produce ese fallo es el que se persiste.

    Verificado por reversión: sin el arreglo de C1 (redactar el token en
    `socialctl/adapters/errores.py`), este test falla -el token aparece
    tanto en `resultado.error` como en el fichero-.
    """
    token_reconocible = "TOKEN-SECRETO-C1-PROXY-975318642"
    brand.guardar_secreto(
        Platform.INSTAGRAM,
        {"access_token": token_reconocible, "expira_en": time.time() + 3600},
    )
    (brand.raiz / "accounts.yml").write_text(
        "instagram:\n  ig_user_id: '999'\n"
        "  media_url_base: 'https://cdn.example/histopast'\n",
        encoding="utf-8",
    )
    brand_local = cargar_brand(brand.raiz.parent, brand.nombre)

    video = brand.raiz.parent / "clip.mp4"
    video.write_bytes(b"bytes")
    post_instagram = PlatformPost(
        platform=Platform.INSTAGRAM,
        body="hola",
        media=[MediaAsset(path=video, kind=MediaKind.VIDEO, width=1080, height=1920,
                          duration_s=10.0, size_bytes=5)],
    )

    with respx.mock:
        respx.head("https://cdn.example/histopast/clip.mp4").mock(
            return_value=httpx.Response(200)
        )
        respx.post(f"{GRAFO_INSTAGRAM}/999/media").mock(
            return_value=httpx.Response(200, json={"id": "cont1"})
        )

        def _proxy_que_refleja_la_url(request):
            return httpx.Response(502, text=f"<html>Bad gateway for {request.url}</html>")

        respx.get(f"{GRAFO_INSTAGRAM}/cont1").mock(side_effect=_proxy_que_refleja_la_url)

        with httpx.Client() as client:
            resultado = InstagramAdapter(espera_s=0).publish(
                post_instagram, brand_local, client
            )

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in (resultado.error or "")

    post_completo = Post(
        slug="2026-09-07-fuga-c1",
        brand=brand_local.nombre,
        campaign=CampaignType.CLIP_VERTICAL,
        platforms={Platform.INSTAGRAM: post_instagram},
    )
    destino = guardar_resultado(post_completo, brand_local, [resultado])

    assert token_reconocible not in destino.read_text(encoding="utf-8")


def test_nunca_se_escribe_un_secreto_en_historial(brand):
    """Igual que el test anterior, pero para `historial.md` y con la otra
    superficie de fuga (el CUERPO de la petición, no la query): un cuerpo de
    respuesta JSON que no tiene la forma de error de la Graph API -aquí, un
    diagnóstico de un WAF que ecoa parte del cuerpo de la petición fallida
    de Facebook- tampoco debe poder colar el token en el historial.

    Verificado por reversión: sin el arreglo, el token aparece en
    `historial.md`.
    """
    token_reconocible = "TOKEN-SECRETO-C1-WAF-135792468"
    brand.guardar_secreto(
        Platform.FACEBOOK,
        {"access_token": token_reconocible, "expira_en": time.time() + 3600},
    )

    post_facebook = PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[])

    with respx.mock:
        def _waf_que_refleja_el_cuerpo(request):
            return httpx.Response(
                400, json={"blocked_by": "waf", "debug_echo": request.content.decode()}
            )

        respx.post(f"{GRAFO_FACEBOOK}/12345/feed").mock(side_effect=_waf_que_refleja_el_cuerpo)

        with httpx.Client() as client:
            resultado = FacebookAdapter().publish(post_facebook, brand, client)

    assert resultado.status is PostStatus.ERROR
    assert token_reconocible not in (resultado.error or "")

    post_completo = Post(
        slug="2026-09-07-fuga-c1-historial",
        brand=brand.nombre,
        campaign=CampaignType.POST_IMAGEN,
        platforms={Platform.FACEBOOK: post_facebook},
    )
    anexar_historial(post_completo, brand, [resultado])

    historial = (brand.raiz / "historial.md").read_text(encoding="utf-8")
    assert token_reconocible not in historial


def test_guardar_resultado_falla_con_error_claro_si_no_hay_permisos_de_escritura(post, brand):
    permisos_originales = brand.dir_posts.stat().st_mode
    brand.dir_posts.chmod(0o000)
    try:
        with pytest.raises(PersistenciaError) as exc:
            guardar_resultado(post, brand, [])
    finally:
        brand.dir_posts.chmod(permisos_originales)

    assert "no se pudo guardar" in str(exc.value)


def test_anexar_historial_falla_con_error_claro_si_no_hay_permisos_de_escritura(post, brand):
    fichero = brand.raiz / "historial.md"
    permisos_originales = fichero.stat().st_mode
    fichero.chmod(0o444)
    try:
        with pytest.raises(PersistenciaError) as exc:
            anexar_historial(post, brand, [])
    finally:
        fichero.chmod(permisos_originales)

    assert "no se pudo anexar" in str(exc.value)


# --- Hallazgo 1: guardar_resultado debe fusionar por plataforma, no
# sobrescribir el fichero entero, para que un reintento parcial no borre del
# disco el rastro de las redes que ya se publicaron. ---


def test_guardar_resultado_primer_guardado_sin_fichero_previo(post_completo, brand):
    """Sin resultado.json previo, simplemente se escriben las entradas recibidas."""
    resultados = [
        PostResult(platform=Platform.YOUTUBE, status=PostStatus.PUBLICADO,
                   url="https://youtube.com/1"),
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
    ]
    destino = guardar_resultado(post_completo, brand, resultados)

    datos = json.loads(destino.read_text(encoding="utf-8"))
    por_plataforma = {r["platform"]: r for r in datos["resultados"]}
    assert len(datos["resultados"]) == 2
    assert por_plataforma["youtube"]["url"] == "https://youtube.com/1"
    assert por_plataforma["facebook"]["url"] == "https://facebook.com/1"


def test_guardar_resultado_reintento_parcial_conserva_las_redes_ya_publicadas(post_completo, brand):
    """El escenario del hallazgo: se publican las cuatro, falla instagram, se
    reintenta solo instagram. Las otras tres deben SEGUIR en el fichero con
    sus URLs intactas tras el reintento."""
    guardar_resultado(post_completo, brand, [
        PostResult(platform=Platform.YOUTUBE, status=PostStatus.PUBLICADO,
                   url="https://youtube.com/1"),
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
        PostResult(platform=Platform.INSTAGRAM, status=PostStatus.ERROR,
                   error="la red se cayo"),
        PostResult(platform=Platform.TIKTOK, status=PostStatus.PUBLICADO,
                   url="https://tiktok.com/1"),
    ])

    # Reintento: solo instagram, y esta vez tiene exito.
    destino = guardar_resultado(post_completo, brand, [
        PostResult(platform=Platform.INSTAGRAM, status=PostStatus.PUBLICADO,
                   url="https://instagram.com/2"),
    ])

    datos = json.loads(destino.read_text(encoding="utf-8"))
    por_plataforma = {r["platform"]: r for r in datos["resultados"]}

    assert len(datos["resultados"]) == 4
    assert por_plataforma["youtube"]["url"] == "https://youtube.com/1"
    assert por_plataforma["youtube"]["status"] == "publicado"
    assert por_plataforma["facebook"]["url"] == "https://facebook.com/1"
    assert por_plataforma["facebook"]["status"] == "publicado"
    assert por_plataforma["tiktok"]["url"] == "https://tiktok.com/1"
    assert por_plataforma["tiktok"]["status"] == "publicado"
    assert por_plataforma["instagram"]["url"] == "https://instagram.com/2"
    assert por_plataforma["instagram"]["status"] == "publicado"


def test_fusion_conserva_resultado_youtube_antiguo_sin_inventar_observacion(
    post_completo, brand
):
    carpeta = brand.dir_posts / post_completo.slug
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / "resultado.json"
    destino.write_text(
        json.dumps(
            {
                "resultados": [
                    {
                        "platform": "youtube",
                        "status": "publicado",
                        "platform_id": "R4cUGeaKrfU",
                        "fecha": "2026-09-01T10:00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    guardar_resultado(
        post_completo,
        brand,
        [
            PostResult(
                platform=Platform.FACEBOOK,
                status=PostStatus.PUBLICADO,
                url="https://facebook.com/nuevo",
            )
        ],
    )

    stored = {
        result["platform"]: result
        for result in json.loads(destino.read_text(encoding="utf-8"))["resultados"]
    }
    assert "requested_privacy" not in stored["youtube"]
    assert "observed_privacy" not in stored["youtube"]
    assert "visibility_observed_at" not in stored["youtube"]


def test_guardar_resultado_mantiene_orden_estable_de_platform_tras_fusionar(post_completo, brand):
    """El orden de las entradas es siempre el de Platform, sin importar en que
    orden llegaron las llamadas a guardar_resultado."""
    guardar_resultado(post_completo, brand, [
        PostResult(platform=Platform.TIKTOK, status=PostStatus.PUBLICADO,
                   url="https://tiktok.com/1"),
        PostResult(platform=Platform.YOUTUBE, status=PostStatus.PUBLICADO,
                   url="https://youtube.com/1"),
    ])
    destino = guardar_resultado(post_completo, brand, [
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
    ])

    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert [r["platform"] for r in datos["resultados"]] == ["youtube", "facebook", "tiktok"]


def test_guardar_resultado_con_fichero_previo_corrupto_no_pierde_lo_nuevo(post, brand):
    """Un resultado.json previo con JSON mal formado no puede tirar la
    publicacion nueva ni reventar; se guarda aparte y el guardado nuevo
    tiene exito con normalidad."""
    carpeta = brand.dir_posts / post.slug
    carpeta.mkdir(parents=True)
    destino = carpeta / "resultado.json"
    contenido_corrupto = "{esto no es json valido"
    destino.write_text(contenido_corrupto, encoding="utf-8")

    nuevo = guardar_resultado(post, brand, [
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
    ])

    datos = json.loads(nuevo.read_text(encoding="utf-8"))
    assert len(datos["resultados"]) == 1
    assert datos["resultados"][0]["url"] == "https://facebook.com/1"

    respaldo = carpeta / "resultado.json.corrupto"
    assert respaldo.read_text(encoding="utf-8") == contenido_corrupto


def test_guardar_resultado_con_fichero_previo_de_forma_inesperada_no_pierde_lo_nuevo(post, brand):
    """JSON valido pero que no tiene la forma esperada (aqui, una lista suelta
    en vez de un objeto con 'resultados') tambien se trata como corrupto: no
    se fusiona con datos en los que no se puede confiar, pero el guardado
    nuevo tiene exito igualmente."""
    carpeta = brand.dir_posts / post.slug
    carpeta.mkdir(parents=True)
    destino = carpeta / "resultado.json"
    destino.write_text(json.dumps(["no", "es", "el objeto esperado"]), encoding="utf-8")

    nuevo = guardar_resultado(post, brand, [
        PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO,
                   url="https://facebook.com/1"),
    ])

    datos = json.loads(nuevo.read_text(encoding="utf-8"))
    assert len(datos["resultados"]) == 1
    assert datos["resultados"][0]["url"] == "https://facebook.com/1"


# --- Hallazgo 2: el slug no puede usarse sin validar para construir una
# ruta. Mismos vectores que _validar_nombre_de_marca en brands.py. ---


def _post_con_slug(slug: str) -> Post:
    return Post(
        slug=slug,
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={Platform.FACEBOOK: PlatformPost(platform=Platform.FACEBOOK, body="hola")},
    )


def test_guardar_resultado_rechaza_slug_de_ruta_absoluta(brand):
    with pytest.raises(SlugInvalido):
        guardar_resultado(_post_con_slug("/etc/passwd"), brand, [])


def test_guardar_resultado_rechaza_slug_con_puntos_dobles(brand):
    with pytest.raises(SlugInvalido):
        guardar_resultado(_post_con_slug("../fuera-de-posts"), brand, [])


def test_guardar_resultado_rechaza_slug_con_separador_de_ruta(brand):
    with pytest.raises(SlugInvalido):
        guardar_resultado(_post_con_slug("subcarpeta/slug"), brand, [])


def test_guardar_resultado_rechaza_slug_vacio(brand):
    with pytest.raises(SlugInvalido):
        guardar_resultado(_post_con_slug("   "), brand, [])


# --- Hallazgo 3: el mensaje debe distinguir un fallo al CONSTRUIR el
# adaptador de un fallo dentro de validate(). ---


def test_validar_todo_distingue_fallo_de_construccion_del_de_validate(post, brand, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorCuyaConstruccionRevienta)

    errores = validar_todo(post, brand)

    motivos = [e.motivo for e in errores[Platform.FACEBOOK]]
    assert any("no se pudo crear el adaptador" in m for m in motivos)
    assert not any("la validacion" in m for m in motivos)


def test_publicar_revalidates_origin_before_adapter_upload(brand, monkeypatch):
    calls = []
    class Recording(AdaptadorOK):
        def publish(self, post, brand, client):
            calls.append(post)
            return super().publish(post, brand, client)
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Recording)
    pp = PlatformPost(platform=Platform.FACEBOOK, body='x', media=[
        MediaAsset(path='clip.mp4', kind=MediaKind.VIDEO)])
    post = Post(slug='new-video', brand=brand.nombre, campaign=CampaignType.CLIP_VERTICAL,
                platforms={pp.platform: pp})
    result = publicar(post, brand)
    assert calls == []
    assert result[0].status is PostStatus.ERROR
    assert result[0].riesgo_duplicado is False
    assert 'standalone' in result[0].error


def test_legacy_context_cannot_exempt_other_platform_or_adapter_validation(brand):
    pp = PlatformPost(platform=Platform.FACEBOOK, body='x', media=[
        MediaAsset(path='clip.mp4', kind=MediaKind.VIDEO)] * 2)
    post = Post(slug='legacy-video', brand=brand.nombre, campaign=CampaignType.CLIP_VERTICAL,
                platforms={pp.platform: pp})
    errors = validar_todo(post, brand, legacy_approved_platforms=frozenset({Platform.INSTAGRAM}))
    assert any(e.campo == 'content_origin' for e in errors[Platform.FACEBOOK])
    errors = validar_todo(post, brand, legacy_approved_platforms=frozenset({Platform.FACEBOOK}))
    assert not any(e.campo == 'content_origin' for e in errors[Platform.FACEBOOK])
    assert any(e.campo == 'media' for e in errors[Platform.FACEBOOK])
    result = publicar(post, brand, legacy_approved_platforms=frozenset({Platform.FACEBOOK}))
    assert result[0].status is PostStatus.ERROR
    assert 'archivo' in result[0].error
