from socialctl.formatter import (
    PLATFORM_SPECS,
    componer_caption,
    privacidad_efectiva,
    validar_privacidad,
    validar_texto,
)
from socialctl.models import Platform, PlatformPost


def _post(platform, **kwargs):
    base = dict(platform=platform, body="cuerpo", hashtags=[], media=[], title=None, link=None)
    base.update(kwargs)
    return PlatformPost(**base)


def test_hay_spec_para_cada_red():
    assert set(PLATFORM_SPECS) == set(Platform)


def test_todas_las_redes_declaran_max_body_y_max_hashtags():
    # max_body y max_hashtags no tienen default en PlatformSpec: si una red
    # deja de declararlos explícitamente en PLATFORM_SPECS, este test falla
    # (o la propia construcción de PlatformSpec revienta antes de llegar aquí,
    # ya que `int | None` sin `= None` en pydantic v2 sigue siendo un campo
    # obligatorio: acepta `None` como valor, pero no aceptar que falte).
    #
    # YouTube es la única red sin tope documentado de *número* de hashtags:
    # su max_hashtags es `None` explícito (no un olvido de quien escribió
    # PLATFORM_SPECS). El límite real de snippet.tags es agregado en
    # caracteres, cubierto por max_tags_chars (ver
    # test_youtube_max_tags_chars_es_el_limite_agregado_documentado).
    esperados = {
        Platform.YOUTUBE: {"max_body": 5000, "max_hashtags": None},
        Platform.FACEBOOK: {"max_body": 63206, "max_hashtags": 30},
        Platform.INSTAGRAM: {"max_body": 2200, "max_hashtags": 30},
        Platform.TIKTOK: {"max_body": 2200, "max_hashtags": 30},
    }
    assert set(esperados) == set(Platform)
    for platform, limites in esperados.items():
        spec = PLATFORM_SPECS[platform]
        assert spec.max_body == limites["max_body"]
        assert spec.max_hashtags == limites["max_hashtags"]


def test_youtube_rechaza_titulo_de_mas_de_100():
    errores = validar_texto(_post(Platform.YOUTUBE, title="x" * 101))
    assert len(errores) == 1
    assert errores[0].campo == "title"
    assert "100" in errores[0].motivo


def test_youtube_acepta_titulo_de_100():
    assert validar_texto(_post(Platform.YOUTUBE, title="x" * 100)) == []


def test_youtube_exige_titulo():
    errores = validar_texto(_post(Platform.YOUTUBE, title=None))
    assert any(e.campo == "title" for e in errores)


def test_instagram_rechaza_caption_de_mas_de_2200():
    errores = validar_texto(_post(Platform.INSTAGRAM, body="x" * 2201))
    assert any(e.campo == "body" for e in errores)


def test_instagram_avisa_si_no_hay_gancho_en_los_primeros_125():
    # Un cuerpo que empieza con relleno pierde el gancho al truncarse el feed.
    errores = validar_texto(_post(Platform.INSTAGRAM, body=" " * 130 + "el gancho"))
    assert any(e.campo == "gancho" for e in errores)


def test_facebook_es_la_unica_que_acepta_solo_texto():
    solo_texto = [p for p in Platform if PLATFORM_SPECS[p].acepta_solo_texto]
    assert solo_texto == [Platform.FACEBOOK]


def test_componer_caption_pone_los_hashtags_al_final():
    assert componer_caption("Historia", ["histopast", "historia"]) == (
        "Historia\n\n#histopast #historia"
    )


def test_componer_caption_sin_hashtags_no_deja_lineas_sueltas():
    assert componer_caption("Historia", []) == "Historia"


# --- Hallazgo 2 (auditoría 2026-09-07): max_bytes de YouTube subió a 256GB --


def test_youtube_max_bytes_es_256gb():
    # developers.google.com/youtube/v3/docs/videos/insert: "Uploaded files
    # must not exceed 256GB" (subido desde 128GB en abril de 2022). El valor
    # viejo (128 * 1024**3) era el límite vigente hasta esa fecha, no hoy.
    assert PLATFORM_SPECS[Platform.YOUTUBE].max_bytes == 256 * 1024**3


# --- Hallazgo 3 (auditoría 2026-09-07): YouTube mide el cuerpo en bytes ----
# UTF-8, no en caracteres Python. Con la medida vieja (len() en caracteres)
# la aserción de error de este test fallaría (validar_texto devolvía []);
# con la medida nueva (bytes UTF-8) debe devolver un error de "body".


def test_youtube_cuerpo_con_acentos_supera_bytes_aunque_no_caracteres():
    # Título de un documental histórico en español: cada tilde/eñe ocupa 2
    # bytes en UTF-8 pero 1 solo carácter Python. Este fragmento, repetido,
    # se queda por debajo de los 5000 caracteres (límite viejo, mal medido)
    # pero supera los 5000 bytes UTF-8 que documenta realmente la API para
    # snippet.description (developers.google.com/youtube/v3/docs/videos:
    # "The video description is limited to 5000 bytes").
    fragmento = "La caída del Imperio incaico: crónica de la conquista española. " * 75

    # Confirma primero las dos medidas por separado: por debajo del límite
    # en caracteres, por encima en bytes. Si cualquiera de estas dos
    # aserciones dejara de cumplirse, el resto del test no probaría nada.
    assert len(fragmento) <= 5000
    assert len(fragmento.encode("utf-8")) > 5000

    errores = validar_texto(_post(Platform.YOUTUBE, title="La caída del Imperio incaico", body=fragmento))
    errores_body = [e for e in errores if e.campo == "body"]
    assert len(errores_body) == 1
    assert "bytes" in errores_body[0].motivo
    # El mensaje no debe mezclar dos unidades (bytes y caracteres a la vez):
    # confundiría más de lo que aclara.
    assert "caracteres" not in errores_body[0].motivo


def test_youtube_cuerpo_ascii_dentro_de_limite_no_falla_por_bytes():
    # Contraprueba: un cuerpo sin acentos donde caracteres y bytes UTF-8
    # coinciden no debe fallar solo por medir en bytes.
    errores = validar_texto(
        _post(Platform.YOUTUBE, title="La primera ciudad de America", body="x" * 5000)
    )
    assert not any(e.campo == "body" for e in errores)


def test_instagram_no_mide_el_cuerpo_en_bytes():
    # Instagram sí está confirmado en caracteres ("Captions are limited to
    # 2200 characters"): un cuerpo con acentos que se pasa de caracteres
    # debe seguir fallando por caracteres, no de repente exigir bytes.
    assert PLATFORM_SPECS[Platform.INSTAGRAM].unidad_max_body == "caracteres"


# --- Hallazgo 5 (auditoría 2026-09-07): max_hashtags=15 de YouTube no era un
# límite real de la Data API; snippet.tags tiene un tope agregado de 500
# caracteres (comas incluidas), no un tope de número de elementos. -------


def test_youtube_max_tags_chars_es_el_limite_agregado_documentado():
    spec = PLATFORM_SPECS[Platform.YOUTUBE]
    assert spec.max_hashtags is None
    assert spec.max_tags_chars == 500


def test_youtube_acepta_mas_de_15_hashtags_si_el_agregado_cabe():
    # Con la medida vieja (conteo de 15 elementos) esto fallaría: son 20
    # hashtags. El agregado en caracteres (20 tags cortos + comas) se queda
    # muy por debajo de 500, así que con el límite real no debe dar error.
    hashtags = [f"etiqueta{i}" for i in range(20)]
    errores = validar_texto(
        _post(Platform.YOUTUBE, title="La primera ciudad de America", hashtags=hashtags)
    )
    assert not any(e.campo == "hashtags" for e in errores)


def test_youtube_rechaza_tags_cuyo_agregado_supera_500_caracteres():
    hashtags = ["x" * 50] * 11  # 11 * 50 + 10 comas = 560 caracteres agregados
    errores = validar_texto(
        _post(Platform.YOUTUBE, title="La primera ciudad de America", hashtags=hashtags)
    )
    errores_hashtags = [e for e in errores if e.campo == "hashtags"]
    assert len(errores_hashtags) == 1
    assert "agregados" in errores_hashtags[0].motivo
    assert "560" in errores_hashtags[0].motivo


# --- Hallazgo 1 (auditoría fix-meta-tiktok, 2026-09-07): Instagram publica ---
# siempre como Reels; el límite real de tamaño de Reels es 300MB, no 1GB.


def test_instagram_max_bytes_es_300mb_como_los_reels():
    # developers.facebook.com/.../ig-user/media (Reel Specifications):
    # "maximum file size of 300MB". El valor viejo (1 * 1024**3, 1GB)
    # triplicaba el límite real de Reels, el único tipo de vídeo que este
    # adaptador publica (instagram.py fija media_type="REELS" siempre).
    assert PLATFORM_SPECS[Platform.INSTAGRAM].max_bytes == 300 * 1024**2


# --- Hallazgo 6 (auditoría fix-meta-tiktok): TikTok mide post_info.title en --
# unidades UTF-16, una tercera unidad distinta de caracteres Python (len())
# y de bytes UTF-8. Un emoji fuera del BMP (la mayoría de los "recientes")
# es 1 solo carácter Python pero 2 unidades UTF-16 (par subrogado): con la
# medida vieja (caracteres) un cuerpo con muchos de esos emoji podía quedar
# por debajo del límite mientras la unidad real (UTF-16) ya lo superaba.


def test_tiktok_unidad_max_body_es_utf16():
    assert PLATFORM_SPECS[Platform.TIKTOK].unidad_max_body == "unidades_utf16"


def test_tiktok_cuerpo_con_acentos_y_emoji_supera_utf16_aunque_no_caracteres():
    # Texto de marca de un documental: tildes (BMP, 1 unidad UTF-16 cada
    # una, igual que en caracteres Python) + emoji fuera del BMP (1
    # carácter Python pero 2 unidades UTF-16 -par subrogado-). 1000 "é" +
    # 700 emoji quedan en 1700 caracteres Python (por debajo de 2200, el
    # límite viejo mal medido no daría error) pero en 2400 unidades UTF-16
    # (por encima de 2200, el límite real).
    cuerpo = "é" * 1000 + "🎬" * 700

    # Confirma primero las dos medidas por separado, igual que en el
    # hallazgo equivalente de YouTube (bytes UTF-8): si cualquiera de estas
    # dos aserciones dejara de cumplirse, el resto del test no probaría
    # nada de lo que dice probar.
    assert len(cuerpo) <= 2200
    assert len(cuerpo.encode("utf-16-le")) // 2 > 2200

    errores = validar_texto(_post(Platform.TIKTOK, body=cuerpo))
    errores_body = [e for e in errores if e.campo == "body"]
    assert len(errores_body) == 1
    assert "UTF-16" in errores_body[0].motivo


def test_tiktok_cuerpo_con_acentos_y_emoji_dentro_del_limite_utf16_no_falla():
    # Contraprueba: el mismo tipo de texto (tildes + emoji fuera del BMP),
    # pero con un recuento en unidades UTF-16 que sí cabe en el límite, no
    # debe fallar. Evita que medir en UTF-16 introduzca falsos positivos
    # para cuerpos que de verdad caben.
    cuerpo = "é" * 1000 + "🎬" * 500
    assert len(cuerpo.encode("utf-16-le")) // 2 <= 2200

    errores = validar_texto(_post(Platform.TIKTOK, body=cuerpo))
    assert not any(e.campo == "body" for e in errores)


# --- Campo de privacidad configurable (YouTube) -----------------------------
#
# `PlatformPost.privacy` es genérico (vive en `socialctl/models.py`), pero
# hoy solo YouTube declara `valores_privacidad` en su `PlatformSpec`: es la
# única red con un `status.privacyStatus` configurable por post.yml (valores
# confirmados con Context7 en
# developers.google.com/youtube/v3/guides/uploading_a_video: "public",
# "private", "unlisted"). TikTok resuelve su propia privacidad consultando
# `creator_info` (`TikTokAdapter._resolver_privacidad_y_duracion`), no a
# través de este campo; Facebook e Instagram no tienen equivalente.


def test_youtube_declara_los_tres_valores_de_privacidad_documentados():
    spec = PLATFORM_SPECS[Platform.YOUTUBE]
    assert spec.valores_privacidad == ["public", "unlisted", "private"]


def test_solo_youtube_declara_valores_de_privacidad():
    # Ninguna de las otras tres redes admite el campo hoy: si alguna
    # empezara a declararlo, este test lo señalaría como cambio deliberado
    # a revisar, no como un olvido silencioso.
    con_privacidad = [p for p in Platform if PLATFORM_SPECS[p].valores_privacidad is not None]
    assert con_privacidad == [Platform.YOUTUBE]


def test_privacidad_efectiva_por_defecto_es_private_cuando_no_se_indica():
    # Decisión explícita del proyecto: nada sale público salvo que se pida.
    post = _post(Platform.YOUTUBE, title="t")
    assert post.privacy is None
    assert privacidad_efectiva(post) == "private"


def test_privacidad_efectiva_respeta_el_valor_indicado():
    post = _post(Platform.YOUTUBE, title="t", privacy="unlisted")
    assert privacidad_efectiva(post) == "unlisted"


def test_privacidad_efectiva_es_none_en_una_red_sin_soporte():
    post = _post(Platform.FACEBOOK)
    assert privacidad_efectiva(post) is None


def test_validar_privacidad_acepta_public():
    post = _post(Platform.YOUTUBE, title="t", privacy="public")
    assert validar_privacidad(post) == []


def test_validar_privacidad_acepta_unlisted():
    post = _post(Platform.YOUTUBE, title="t", privacy="unlisted")
    assert validar_privacidad(post) == []


def test_validar_privacidad_acepta_private():
    post = _post(Platform.YOUTUBE, title="t", privacy="private")
    assert validar_privacidad(post) == []


def test_validar_privacidad_no_falla_si_no_se_indica_nada():
    post = _post(Platform.YOUTUBE, title="t", privacy=None)
    assert validar_privacidad(post) == []


def test_validar_privacidad_rechaza_valor_no_soportado_por_la_api():
    post = _post(Platform.YOUTUBE, title="t", privacy="publico")
    errores = validar_privacidad(post)
    assert len(errores) == 1
    assert errores[0].campo == "privacy"
    # Frase propia de la rama "valor no admitido", no una subcadena que
    # también apareciera en la rama "red sin soporte" (ambas mencionan
    # "privacy" y el nombre de la red): se comprueba la lista de valores
    # válidos, que solo aparece en este mensaje.
    assert "public, unlisted, private" in errores[0].motivo
    assert "no es un valor de privacidad válido" in errores[0].motivo


def test_validar_privacidad_rechaza_el_campo_en_una_red_que_no_lo_soporta():
    post = _post(Platform.TIKTOK, privacy="private")
    errores = validar_privacidad(post)
    assert len(errores) == 1
    assert errores[0].campo == "privacy"
    # Frase propia de la rama "red sin soporte", distinta de la de "valor no
    # admitido" (esa otra rama nunca menciona "no admite el campo").
    assert "tiktok no admite el campo 'privacy'" in errores[0].motivo


def test_validar_privacidad_rechaza_el_campo_en_facebook_e_instagram_tambien():
    for platform in (Platform.FACEBOOK, Platform.INSTAGRAM):
        post = _post(platform, privacy="public")
        errores = validar_privacidad(post)
        assert len(errores) == 1
        assert errores[0].campo == "privacy"

