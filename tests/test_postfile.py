import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from socialctl.brands import crear_brand
from socialctl.media import MediaNoEncontrada
from socialctl.models import CampaignType, Platform
from socialctl.postfile import (
    NombreDeMediaInvalido,
    PostInvalido,
    PostNoEncontrado,
    SlugInvalido,
    cargar_post,
    guardar_post,
)


@pytest.fixture(scope="session")
def _clip_de_prueba_compartido(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Codifica un único clip de prueba (1080x1920, 4s, 30fps) una vez por sesión.

    Hallazgo de revisión (I7): la fixture `brand`, con scope de función,
    invocaba a ffmpeg para generar este mismo clip -byte a byte idéntico-
    en cada uno de los ~34 tests de este fichero (~11s de reencodeo
    estrictamente innecesario: el 68% del tiempo total de toda la suite).
    El proyecto garantiza que socialctl nunca modifica un archivo de media
    -solo lo lee, vía `ffprobe` (ver `socialctl/media.py`)-, así que este
    clip compartido es de solo lectura y puede generarse una única vez por
    sesión sin que ningún test dependa de que otro lo haya tocado antes ni
    pueda mutarlo para los demás.
    """
    ruta = tmp_path_factory.mktemp("clip-compartido") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1080x1920:rate=30:duration=4",
         "-pix_fmt", "yuv420p", str(ruta)],
        check=True, capture_output=True,
    )
    return ruta


@pytest.fixture
def brand(tmp_path, _clip_de_prueba_compartido):
    """Una marca nueva por test, con una COPIA del clip compartido de sesión.

    Sigue siendo una marca propia y aislada por test -nada de estado
    mutable compartido entre tests, ni dependencia del orden en que se
    ejecuten-: solo se ahorra el reencodeo repetido de un contenido
    idéntico, copiando el fichero ya generado en vez de invocar a ffmpeg
    de nuevo (ver `_clip_de_prueba_compartido`).
    """
    b = crear_brand(tmp_path, "Histopast")
    shutil.copyfile(_clip_de_prueba_compartido, b.raiz / "media" / "clip.mp4")
    return b


def _escribir_post(brand, contenido: dict, slug="2026-09-07-prueba"):
    carpeta = brand.dir_posts / slug
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "post.yml").write_text(
        yaml.safe_dump(contenido, allow_unicode=True), encoding="utf-8"
    )
    return slug


def _escribir_texto(brand, texto: str, slug="2026-09-07-prueba"):
    carpeta = brand.dir_posts / slug
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "post.yml").write_text(texto, encoding="utf-8")
    return slug


# --- Tests del brief (interfaz base) ---


def test_cargar_post_resuelve_la_media_y_lee_sus_metadatos(brand):
    slug = _escribir_post(brand, {
        "slug": "2026-09-07-prueba",
        "campaign": "clip-vertical",
        "platforms": {
            "tiktok": {"body": "hola", "hashtags": ["historia"], "media": ["clip.mp4"]},
        },
    })

    post = cargar_post(brand, slug)

    assert post.campaign is CampaignType.CLIP_VERTICAL
    asset = post.platforms[Platform.TIKTOK].media[0]
    assert asset.path.is_absolute()
    assert asset.width == 1080
    assert asset.height == 1920


def test_cargar_post_resuelve_media_en_subcarpeta(brand, _clip_de_prueba_compartido):
    """El usuario organiza su media en subcarpetas (p. ej. `short/` para
    vídeos verticales): `media: [short/S2.mp4]` debe resolver al fichero
    real dentro de `<Marca>/media/short/`, no a `<Marca>/media/S2.mp4`.
    """
    carpeta_short = brand.raiz / "media" / "short"
    carpeta_short.mkdir()
    shutil.copyfile(_clip_de_prueba_compartido, carpeta_short / "S2.mp4")

    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["short/S2.mp4"]}},
    })

    post = cargar_post(brand, slug)

    asset = post.platforms[Platform.TIKTOK].media[0]
    assert asset.path == carpeta_short / "S2.mp4"
    assert asset.width == 1080
    assert asset.height == 1920


def test_cargar_post_inexistente_falla(brand):
    with pytest.raises(PostNoEncontrado):
        cargar_post(brand, "no-existe")


def test_media_que_no_esta_en_la_carpeta_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "slug": "2026-09-07-prueba",
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["fantasma.mp4"]}},
    })

    with pytest.raises(MediaNoEncontrada) as exc:
        cargar_post(brand, slug)
    assert "fantasma.mp4" in str(exc.value)


def test_red_desconocida_en_el_yaml_falla(brand):
    slug = _escribir_post(brand, {
        "slug": "2026-09-07-prueba",
        "campaign": "clip-vertical",
        "platforms": {"twitter": {"body": "hola"}},
    })

    with pytest.raises(ValueError) as exc:
        cargar_post(brand, slug)
    assert "twitter" in str(exc.value)


def test_guardar_y_recargar_conserva_el_contenido(brand):
    slug = _escribir_post(brand, {
        "slug": "2026-09-07-prueba",
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "hashtags": ["h"], "media": ["clip.mp4"]}},
    })
    post = cargar_post(brand, slug)

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)

    assert recargado.platforms[Platform.TIKTOK].body == "hola"
    assert recargado.platforms[Platform.TIKTOK].hashtags == ["h"]


def test_guardar_post_conserva_la_subcarpeta_de_media(brand, _clip_de_prueba_compartido):
    """El viaje de ida y vuelta (cargar → guardar → recargar) no debe perder
    la subcarpeta de un fichero de media: `guardar_post` escribía antes
    solo `asset.path.name` (p. ej. 'S2.mp4'), lo que al recargar apuntaría
    a `<Marca>/media/S2.mp4` -un fichero que no existe- en vez de
    `<Marca>/media/short/S2.mp4`.
    """
    carpeta_short = brand.raiz / "media" / "short"
    carpeta_short.mkdir()
    shutil.copyfile(_clip_de_prueba_compartido, carpeta_short / "S2.mp4")

    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["short/S2.mp4"]}},
    })
    post = cargar_post(brand, slug)

    fichero = guardar_post(brand, post)
    contenido = fichero.read_text(encoding="utf-8")
    assert "short/S2.mp4" in contenido

    recargado = cargar_post(brand, slug)
    asset = recargado.platforms[Platform.TIKTOK].media[0]
    assert asset.path == carpeta_short / "S2.mp4"


# --- Seguridad de rutas: el slug no puede escapar de dir_posts ---


def test_cargar_post_rechaza_slug_con_escape(brand):
    with pytest.raises(SlugInvalido):
        cargar_post(brand, "../../etc")


def test_cargar_post_rechaza_slug_absoluto(brand):
    with pytest.raises(SlugInvalido):
        cargar_post(brand, "/etc/passwd")


def test_cargar_post_rechaza_slug_con_separador(brand):
    with pytest.raises(SlugInvalido):
        cargar_post(brand, "subcarpeta/slug")


def test_guardar_post_rechaza_slug_con_escape(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })
    post = cargar_post(brand, slug)
    post_malicioso = post.model_copy(update={"slug": "../../fuera-de-posts"})

    with pytest.raises(SlugInvalido):
        guardar_post(brand, post_malicioso)


def test_cargar_post_rechaza_enlace_simbolico_de_slug_que_escapa(brand, tmp_path_factory):
    externo = tmp_path_factory.mktemp("fuera")
    (brand.dir_posts / "enlace").symlink_to(externo, target_is_directory=True)

    with pytest.raises(SlugInvalido):
        cargar_post(brand, "enlace")


# --- Seguridad de rutas: un nombre de media no puede escapar de <Marca>/media/ ---


def test_cargar_post_rechaza_nombre_de_media_con_escape(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {
            "tiktok": {"body": "hola", "media": ["../../.secrets/tiktok.json"]},
        },
    })

    with pytest.raises(NombreDeMediaInvalido) as exc:
        cargar_post(brand, slug)
    assert ".secrets" in str(exc.value)


def test_cargar_post_rechaza_nombre_de_media_absoluto(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["/etc/passwd"]}},
    })

    with pytest.raises(NombreDeMediaInvalido):
        cargar_post(brand, slug)


def test_cargar_post_rechaza_nombre_de_media_que_no_es_texto(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": [123]}},
    })

    with pytest.raises(NombreDeMediaInvalido):
        cargar_post(brand, slug)


def test_cargar_post_rechaza_enlace_simbolico_de_media_que_escapa(brand, tmp_path_factory):
    externo = tmp_path_factory.mktemp("fuera")
    secreto = externo / "secreto.mp4"
    secreto.write_bytes(b"nada-de-interes-aqui")
    (brand.raiz / "media" / "enlace.mp4").symlink_to(secreto)

    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["enlace.mp4"]}},
    })

    with pytest.raises(NombreDeMediaInvalido):
        cargar_post(brand, slug)


# --- Seguridad de rutas: subcarpetas de media, sin abrir la vía del escape ---


def test_cargar_post_rechaza_nombre_de_media_con_escape_en_subcarpeta(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {
            "tiktok": {"body": "hola", "media": ["short/../../.secrets/tiktok.json"]},
        },
    })

    with pytest.raises(NombreDeMediaInvalido) as exc:
        cargar_post(brand, slug)
    assert ".secrets" in str(exc.value)


def test_cargar_post_rechaza_nombre_de_media_con_backslash(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["short\\S2.mp4"]}},
    })

    with pytest.raises(NombreDeMediaInvalido):
        cargar_post(brand, slug)


def test_cargar_post_rechaza_enlace_simbolico_de_subcarpeta_de_media_que_escapa(
    brand, tmp_path_factory
):
    """El enlace que escapa puede ser la propia subcarpeta (`short/`), no
    solo el fichero final: `short/secreto.mp4` debe rechazarse igual que
    `enlace.mp4` cuando `short` en sí es un symlink hacia fuera.
    """
    externo = tmp_path_factory.mktemp("fuera")
    secreto = externo / "secreto.mp4"
    secreto.write_bytes(b"nada-de-interes-aqui")
    (brand.raiz / "media" / "short").symlink_to(externo, target_is_directory=True)

    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["short/secreto.mp4"]}},
    })

    with pytest.raises(NombreDeMediaInvalido):
        cargar_post(brand, slug)


# --- Errores comprensibles ante un post.yml mal formado ---


def test_yaml_invalido_da_error_con_mensaje_claro(brand):
    slug = _escribir_texto(brand, "platforms: [sin cerrar")

    with pytest.raises(PostInvalido, match="post.yml"):
        cargar_post(brand, slug)


def test_raiz_que_no_es_mapping_da_error_claro(brand):
    slug = _escribir_texto(brand, "- solo\n- una\n- lista\n")

    with pytest.raises(PostInvalido, match='at the root'):
        cargar_post(brand, slug)


def test_platforms_que_no_es_diccionario_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": ["youtube", "tiktok"],
    })

    with pytest.raises(PostInvalido, match="'platforms'"):
        cargar_post(brand, slug)


def test_platforms_ausente_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {"campaign": "clip-vertical"})

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "'platforms' is missing" in str(exc.value)


def test_platforms_vacio_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {"campaign": "clip-vertical", "platforms": {}})

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "'platforms' in" in str(exc.value)
    assert 'empty' in str(exc.value)


def test_bloque_de_red_que_no_es_mapping_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": "solo un texto suelto"},
    })

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "the block for 'tiktok'" in str(exc.value)


def test_campaign_desconocida_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "campaign": "no-existe-esta-campana",
        "platforms": {"tiktok": {"body": "hola"}},
    })

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert 'unknown campaign' in str(exc.value)


def test_campaign_ausente_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "'campaign' is missing" in str(exc.value)
    assert "clip-vertical" in str(exc.value)


def test_hashtags_como_cadena_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {
            "tiktok": {"body": "hola", "hashtags": "historia", "media": ["clip.mp4"]},
        },
    })

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "'hashtags' for 'tiktok'" in str(exc.value)
    assert 'list' in str(exc.value)


def test_media_como_cadena_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": "clip.mp4"}},
    })

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "'media' for 'tiktok'" in str(exc.value)
    assert 'list' in str(exc.value)


def test_body_ausente_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"hashtags": ["h"], "media": ["clip.mp4"]}},
    })

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "string 'body'" in str(exc.value)


def test_body_no_textual_falla_con_mensaje_claro(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": 12345}},
    })

    with pytest.raises(PostInvalido) as exc:
        cargar_post(brand, slug)
    assert "string 'body'" in str(exc.value)


# --- Ida y vuelta: campos opcionales y listas vacías ---


def test_guardar_post_conserva_title_y_link_ausentes(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })
    post = cargar_post(brand, slug)
    assert post.platforms[Platform.TIKTOK].title is None
    assert post.platforms[Platform.TIKTOK].link is None

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)

    assert recargado.platforms[Platform.TIKTOK].title is None
    assert recargado.platforms[Platform.TIKTOK].link is None


def test_guardar_post_conserva_first_comment_de_facebook(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {
            "facebook": {
                "title": "Titulo de Facebook",
                "body": "Texto principal",
                "hashtags": ["historia"],
                "media": ["clip.mp4"],
                "first_comment": "Comentario exacto con 🏛️",
            },
        },
    })
    post = cargar_post(brand, slug)
    facebook = post.platforms[Platform.FACEBOOK]
    assert facebook.first_comment == "Comentario exacto con 🏛️"

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)
    facebook = recargado.platforms[Platform.FACEBOOK]

    assert facebook.first_comment == "Comentario exacto con 🏛️"
    assert facebook.title == "Titulo de Facebook"
    assert facebook.body == "Texto principal"
    assert facebook.media[0].path.name == "clip.mp4"


def test_guardar_post_conserva_first_comment_ausente_de_facebook(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {
            "facebook": {
                "title": "Titulo de Facebook",
                "body": "Texto principal",
                "media": ["clip.mp4"],
            },
        },
    })
    post = cargar_post(brand, slug)
    assert post.platforms[Platform.FACEBOOK].first_comment is None

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)
    facebook = recargado.platforms[Platform.FACEBOOK]

    assert facebook.first_comment is None
    assert facebook.title == "Titulo de Facebook"
    assert facebook.body == "Texto principal"
    assert facebook.media[0].path.name == "clip.mp4"


def test_cargar_post_lee_el_privacy_de_youtube(brand):
    slug = _escribir_post(brand, {
        "campaign": "lanzamiento-video-largo",
        "platforms": {
            "youtube": {
                "title": "Un titulo",
                "body": "hola",
                "media": ["clip.mp4"],
                "privacy": "unlisted",
            },
        },
    })
    post = cargar_post(brand, slug)
    assert post.platforms[Platform.YOUTUBE].privacy == "unlisted"


def test_cargar_post_sin_privacy_lo_deja_en_none(brand):
    slug = _escribir_post(brand, {
        "campaign": "lanzamiento-video-largo",
        "platforms": {
            "youtube": {"title": "Un titulo", "body": "hola", "media": ["clip.mp4"]},
        },
    })
    post = cargar_post(brand, slug)
    assert post.platforms[Platform.YOUTUBE].privacy is None


def test_guardar_post_conserva_privacy_ausente(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })
    post = cargar_post(brand, slug)
    assert post.platforms[Platform.TIKTOK].privacy is None

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)

    assert recargado.platforms[Platform.TIKTOK].privacy is None


def test_guardar_post_conserva_privacy_presente(brand):
    slug = _escribir_post(brand, {
        "campaign": "lanzamiento-video-largo",
        "platforms": {
            "youtube": {
                "title": "Un titulo",
                "body": "hola",
                "media": ["clip.mp4"],
                "privacy": "public",
            },
        },
    })
    post = cargar_post(brand, slug)

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)

    assert recargado.platforms[Platform.YOUTUBE].privacy == "public"


def test_guardar_post_conserva_title_y_link_presentes(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {
            "youtube": {
                "title": "Un titulo",
                "body": "hola",
                "hashtags": ["h"],
                "media": ["clip.mp4"],
                "link": "https://youtu.be/abc",
            },
        },
    })
    post = cargar_post(brand, slug)

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)

    assert recargado.platforms[Platform.YOUTUBE].title == "Un titulo"
    assert recargado.platforms[Platform.YOUTUBE].link == "https://youtu.be/abc"


def test_guardar_post_conserva_listas_vacias(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "hashtags": [], "media": []}},
    })
    post = cargar_post(brand, slug)
    assert post.platforms[Platform.TIKTOK].hashtags == []
    assert post.platforms[Platform.TIKTOK].media == []

    guardar_post(brand, post)
    recargado = cargar_post(brand, slug)

    assert recargado.platforms[Platform.TIKTOK].hashtags == []
    assert recargado.platforms[Platform.TIKTOK].media == []


def test_cargar_post_ignora_el_slug_declarado_dentro_del_yaml(brand):
    """El 'slug' embebido en el YAML no debe poder mandar sobre la carpeta real.

    Si `cargar_post` confiara en el campo 'slug' del propio fichero para
    construir `Post.slug`, un post.yml con un 'slug' malicioso podría luego
    hacer que `guardar_post` escribiera fuera de `dir_posts`. La fuente de
    verdad es siempre el parámetro `slug` (la carpeta real desde la que se
    cargó), nunca el contenido del fichero.
    """
    slug = _escribir_post(brand, {
        "slug": "../../fuera-de-posts",
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })

    with pytest.warns(UserWarning, match='does not match the actual directory name') as warnings:
        post = cargar_post(brand, slug)

    assert post.slug == slug
    assert len(warnings) == 1


# --- El 'slug' del fichero es informativo: se avisa si no coincide ---


def test_cargar_post_avisa_si_el_slug_del_fichero_no_coincide_con_la_carpeta(brand):
    slug = _escribir_post(brand, {
        "slug": "otro-slug-distinto",
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })

    with pytest.warns(UserWarning, match='does not match'):
        post = cargar_post(brand, slug)

    assert post.slug == slug


def test_cargar_post_no_avisa_si_el_slug_del_fichero_coincide_con_la_carpeta(brand, recwarn):
    slug = _escribir_post(brand, {
        "slug": "2026-09-07-prueba",
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })

    cargar_post(brand, slug)

    assert len(recwarn) == 0


def test_guardar_post_deja_un_comentario_explicando_que_el_slug_es_informativo(brand):
    slug = _escribir_post(brand, {
        "campaign": "clip-vertical",
        "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
    })
    post = cargar_post(brand, slug)

    fichero = guardar_post(brand, post)

    contenido = fichero.read_text(encoding="utf-8")
    assert 'informational' in contenido
    # El comentario no debe romper la carga posterior del fichero.
    recargado = cargar_post(brand, slug)
    assert recargado.slug == slug


@pytest.mark.parametrize('fields', [{}, {'content_origin': 'standalone'},
    {'content_origin': 'youtube_long', 'source_video_id': 'R4cUGeaKrfU'}])
def test_origin_roundtrip_preserves_absence_and_explicit_values(tmp_path, fields):
    brand = crear_brand(tmp_path, 'Histopast')
    slug = _escribir_post(brand, {'campaign': 'clip-vertical', 'platforms': {
        'facebook': {'body': 'Documental', **fields}}})
    post = cargar_post(brand, slug)
    written = guardar_post(brand, post)
    block = yaml.safe_load(written.read_text())['platforms']['facebook']
    for field in ('content_origin', 'source_video_id'):
        assert (field in block) == (field in fields)
        assert getattr(cargar_post(brand, slug).platforms[Platform.FACEBOOK], field) == fields.get(field)


def test_sources_do_not_leak_between_brands(tmp_path):
    for name, source in [('Histopast', 'R4cUGeaKrfU'), ('OtraMarca', 'Vhb3l5-KmEg')]:
        brand = crear_brand(tmp_path, name)
        slug = _escribir_post(brand, {'campaign': 'clip-vertical', 'platforms': {
            'facebook': {'body': 'Mismo título', 'content_origin': 'youtube_long',
                         'source_video_id': source}}})
        post = cargar_post(brand, slug)
        guardar_post(brand, post)
        assert post.brand == name
        assert cargar_post(brand, slug).platforms[Platform.FACEBOOK].source_video_id == source


def test_invalid_source_in_yaml_is_readable_error(tmp_path):
    brand = crear_brand(tmp_path, 'Histopast')
    slug = _escribir_post(brand, {'campaign': 'clip-vertical', 'platforms': {
        'facebook': {'body': 'x', 'source_video_id': 'bad'}}})
    with pytest.raises(PostInvalido, match='source_video_id'):
        cargar_post(brand, slug)
