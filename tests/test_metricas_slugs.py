from datetime import date, datetime

import pytest

from socialctl.brands import crear_brand
from socialctl.metricas.modelos import (
    EstadoLectura,
    LecturaRed,
    Pieza,
    Snapshot,
    TipoPieza,
)
from socialctl.metricas.slugs import asignar_slugs, mapa_de_slugs
from socialctl.models import (
    CampaignType,
    Platform,
    PlatformPost,
    Post,
    PostResult,
    PostStatus,
)
from socialctl.publisher import guardar_resultado


@pytest.fixture
def brand(tmp_path):
    return crear_brand(tmp_path, "Histopast")


def _escribir_resultado(brand, slug: str, resultados: list[dict]) -> None:
    """Escribe un `resultado.json` con la función real que lo escribe.

    No se fabrica el JSON a mano a propósito: el cruce de `slugs.py` depende
    de la forma exacta que serializa `publisher.guardar_resultado`
    (`resultados`, `platform`, `status`, `platform_id`, `url`), y un test que
    inventa esa forma seguiría en verde el día que la escritura cambie, que
    es justo el día en que el cruce se rompe.
    """
    post = Post(
        slug=slug,
        brand="Histopast",
        campaign=CampaignType.POST_IMAGEN,
        platforms={
            Platform(entrada["platform"]): PlatformPost(
                platform=Platform(entrada["platform"]), body="hola", media=[]
            )
            for entrada in resultados
        },
    )
    guardar_resultado(post, brand, [
        PostResult(
            platform=Platform(entrada["platform"]),
            status=PostStatus(entrada["status"]),
            url=entrada.get("url"),
            platform_id=entrada.get("platform_id"),
            error=entrada.get("error"),
            riesgo_duplicado=entrada.get("riesgo_duplicado", False),
        )
        for entrada in resultados
    ])


def _resultado_real(brand, slug="2026-09-06-santo-domingo"):
    """Un `resultado.json` con la forma que escribe `publisher.guardar_resultado`."""
    _escribir_resultado(brand, slug, [
        {
            "platform": "youtube", "status": "publicado",
            "url": "https://www.youtube.com/watch?v=vid1",
            "platform_id": "vid1", "error": None,
            "riesgo_duplicado": False, "fecha": "2026-09-06T19:05:11",
        },
        {
            "platform": "instagram", "status": "publicado",
            "url": "https://instagram.com/p/abc",
            "platform_id": "ig_1", "error": None,
            "riesgo_duplicado": False, "fecha": "2026-09-06T19:05:11",
        },
        {
            "platform": "facebook", "status": "error",
            "url": None, "platform_id": None,
            "error": "Facebook respondió sin el id de la publicación",
            "riesgo_duplicado": False, "fecha": "2026-09-06T19:05:11",
        },
    ])
    return slug


def _snapshot(*piezas_por_red):
    return Snapshot(
        fecha=date(2026, 9, 11),
        marca="Histopast",
        redes={
            platform: LecturaRed(
                estado=EstadoLectura.OK,
                piezas=[Pieza(
                    id=id_,
                    url="https://ejemplo/x",
                    titulo="t",
                    publicado_el=datetime(2026, 9, 6, 19, 0),
                    tipo=TipoPieza.LARGO,
                )],
            )
            for platform, id_ in piezas_por_red
        },
    )


def test_el_mapa_sale_de_los_resultado_json_de_posts(brand):
    slug = _resultado_real(brand)

    mapa = mapa_de_slugs(brand)

    assert mapa[("youtube", "vid1")] == slug
    assert mapa[("instagram", "ig_1")] == slug
    # La red que quedó en error no publicó nada: no aporta ninguna pareja.
    assert not any(clave[0] == "facebook" for clave in mapa)


def test_una_pieza_publicada_por_socialctl_recibe_su_slug(brand):
    slug = _resultado_real(brand)
    snapshot = _snapshot((Platform.YOUTUBE, "vid1"))

    asignar_slugs(brand, snapshot)

    assert snapshot.redes[Platform.YOUTUBE].piezas[0].slug == slug


def test_una_pieza_publicada_fuera_se_queda_sin_slug(brand):
    _resultado_real(brand)
    snapshot = _snapshot((Platform.YOUTUBE, "otro_video"))

    asignar_slugs(brand, snapshot)

    assert snapshot.redes[Platform.YOUTUBE].piezas[0].slug is None


def test_el_id_no_se_cruza_entre_redes(brand):
    """Dos redes pueden dar el mismo id: la clave es la pareja (red, id)."""
    _escribir_resultado(brand, "2026-09-01-uno", [{
        "platform": "youtube", "status": "publicado",
        "url": "https://www.youtube.com/watch?v=comun",
        "platform_id": "comun", "error": None,
        "riesgo_duplicado": False, "fecha": "2026-09-01T10:00:00",
    }])
    snapshot = _snapshot((Platform.FACEBOOK, "comun"))

    asignar_slugs(brand, snapshot)

    assert snapshot.redes[Platform.FACEBOOK].piezas[0].slug is None


def test_pendiente_de_confirmacion_no_cuenta_como_publicado(brand):
    """El modo inbox de TikTok: el usuario aún no ha confirmado nada."""
    _escribir_resultado(brand, "2026-09-02-dos", [{
        "platform": "tiktok", "status": "pendiente_confirmacion",
        "url": None, "platform_id": "publish_abc",
        "error": "pendiente: abre la app de TikTok y confirma la publicación",
        "riesgo_duplicado": False, "fecha": "2026-09-02T10:00:00",
    }])

    assert mapa_de_slugs(brand) == {}


def test_tiktok_saca_el_id_del_video_de_la_url_no_del_publish_id(brand):
    _escribir_resultado(brand, "2026-09-03-tres", [{
        "platform": "tiktok", "status": "publicado",
        "url": "https://www.tiktok.com/@histopast/video/7080213458555737986",
        "platform_id": "publish_abc", "error": None,
        "riesgo_duplicado": False, "fecha": "2026-09-03T10:00:00",
    }])

    mapa = mapa_de_slugs(brand)

    assert mapa[("tiktok", "7080213458555737986")] == "2026-09-03-tres"
    assert ("tiktok", "publish_abc") not in mapa


def test_sin_carpeta_de_posts_el_mapa_esta_vacio(brand):
    assert mapa_de_slugs(brand) == {}


def test_una_carpeta_de_posts_vacia_no_rompe_nada(brand):
    brand.dir_posts.mkdir(parents=True, exist_ok=True)
    (brand.dir_posts / "2026-09-04-sin-publicar").mkdir()

    assert mapa_de_slugs(brand) == {}


def test_un_resultado_json_corrupto_no_tumba_la_lectura(brand):
    slug = _resultado_real(brand)
    carpeta = brand.dir_posts / "2026-09-05-roto"
    carpeta.mkdir(parents=True)
    (carpeta / "resultado.json").write_text("{esto no es JSON", encoding="utf-8")

    mapa = mapa_de_slugs(brand)

    assert mapa[("youtube", "vid1")] == slug  # el bueno se sigue leyendo


def test_asignar_slugs_no_pisa_un_slug_que_ya_traia_el_lector(brand):
    _resultado_real(brand)
    snapshot = _snapshot((Platform.YOUTUBE, "vid1"))
    snapshot.redes[Platform.YOUTUBE].piezas[0].slug = "el-que-ya-tenia"

    asignar_slugs(brand, snapshot)

    assert snapshot.redes[Platform.YOUTUBE].piezas[0].slug == "el-que-ya-tenia"


def test_colision_de_ids_descarta_el_segundo_con_aviso(brand):
    """Dos posts con el mismo (red, id) emiten aviso; gana el primero alfabético."""
    # "2026-09-01-" viene antes que "2026-09-02-", así que gana el primero
    _escribir_resultado(brand, "2026-09-01-primero", [{
        "platform": "youtube", "status": "publicado",
        "url": "https://www.youtube.com/watch?v=vid-duplicado",
        "platform_id": "vid-duplicado", "error": None,
        "riesgo_duplicado": False, "fecha": "2026-09-01T10:00:00",
    }])
    _escribir_resultado(brand, "2026-09-02-segundo", [{
        "platform": "youtube", "status": "publicado",
        "url": "https://www.youtube.com/watch?v=vid-duplicado",
        "platform_id": "vid-duplicado", "error": None,
        "riesgo_duplicado": False, "fecha": "2026-09-02T10:00:00",
    }])

    with pytest.warns(UserWarning, match=r"dos posts reclaman.*youtube.*vid-duplicado"):
        mapa = mapa_de_slugs(brand)

    # El primero en orden alfabético gana
    assert mapa[("youtube", "vid-duplicado")] == "2026-09-01-primero"
