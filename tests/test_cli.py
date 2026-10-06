import base64
import hashlib
import json
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import httpx
import pytest
import respx
import yaml
from typer.testing import CliRunner

import socialctl.cli as cli
from socialctl.adapters.base import ADAPTADORES, Adapter
from socialctl.adapters.instagram import InstagramAdapter
from socialctl.brands import crear_brand
from socialctl.cli import _esperar_codigo, _pagina_para, app
from socialctl.models import Platform, PostResult, PostStatus

runner = CliRunner()


def _puerto_libre() -> int:
    """Un puerto TCP libre en localhost, para los tests de `_esperar_codigo`
    que necesitan un servidor real (no monkeypatcheado)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


class AdaptadorOK(Adapter):
    platform = Platform.FACEBOOK

    def publish(self, post, brand, client):
        return PostResult(platform=self.platform, status=PostStatus.PUBLICADO,
                          url="https://facebook.com/1")


@pytest.fixture
def social(tmp_path):
    """Una raiz Social/ con la marca Histopast y un post de solo texto.

    Fija `facebook.page_id` (la plantilla en blanco de `crear_brand` lo deja
    vacío): varios tests de este fichero usan el `FacebookAdapter` real, sin
    monkeypatchear, solo para ver el preview o la confirmación -nunca llegan
    a publicar de verdad-, y desde que `FacebookAdapter.validate()` también
    comprueba la configuración de la cuenta (hallazgo I4 de la revisión
    final), un `page_id` vacío haría fallar esa validación por un motivo
    ajeno a lo que cada test intenta comprobar.
    """
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text(
        "facebook:\n  page_id: '12345'\n", encoding="utf-8"
    )
    carpeta = brand.dir_posts / "2026-09-07-prueba"
    carpeta.mkdir(parents=True)
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {"facebook": {"body": "Dato historico", "hashtags": ["historia"]}},
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return tmp_path


def test_brand_new_crea_la_carpeta(tmp_path):
    resultado = runner.invoke(app, ["brand", "new", "Nueva", "--root", str(tmp_path)])
    assert resultado.exit_code == 0
    assert (tmp_path / "Nueva" / "brand.md").exists()


def test_brand_new_marca_ya_existente_da_error_util(tmp_path):
    """Hallazgo de revisión: el `except (BrandYaExiste, NombreDeMarcaInvalido)`
    de `brand_new` no tenía ningún test."""
    runner.invoke(app, ["brand", "new", "Nueva", "--root", str(tmp_path)])

    resultado = runner.invoke(app, ["brand", "new", "Nueva", "--root", str(tmp_path)])

    assert resultado.exit_code == 1
    assert "ya existe una carpeta" in resultado.stdout
    assert "Traceback" not in resultado.output


def test_brand_new_nombre_invalido_da_error_util(tmp_path):
    """Hallazgo de revisión: el `except (BrandYaExiste, NombreDeMarcaInvalido)`
    de `brand_new` no tenía ningún test."""
    resultado = runner.invoke(app, ["brand", "new", "../fuera-de-la-raiz", "--root", str(tmp_path)])

    assert resultado.exit_code == 1
    assert "no puede contener separadores de ruta" in resultado.stdout
    assert "Traceback" not in resultado.output


def test_dry_run_muestra_el_preview_y_no_publica(social, monkeypatch):
    llamadas = []

    class Espia(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas.append(1)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Espia)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--dry-run",
    ])

    assert resultado.exit_code == 0
    assert "FACEBOOK" in resultado.stdout
    assert "Dato historico" in resultado.stdout
    assert llamadas == []


def test_sin_confirmar_no_publica(social, monkeypatch):
    llamadas = []

    class Espia(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas.append(1)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Espia)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social),
    ], input="n\n")

    assert llamadas == []
    assert "cancelado" in resultado.stdout.lower()


def test_ctrl_c_en_la_confirmacion_termina_con_130_y_dice_cancelado(social, monkeypatch):
    """Hallazgo de revisión: con Ctrl+C el CLI termina con código 130 sin
    traza y sin publicar -correcto, y no se toca-, pero no imprimía nada,
    a diferencia de los demás caminos de cancelación (responder 'n', o no
    confirmar), que sí dicen "Cancelado"."""
    llamadas = []

    class Espia(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas.append(1)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Espia)

    def _input_interrumpido(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _input_interrumpido)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social),
    ])

    assert resultado.exit_code == 130
    assert llamadas == []
    assert "cancelado" in resultado.stdout.lower()
    assert "Traceback" not in resultado.output


def test_confirmar_con_si_en_espanol_publica(social, monkeypatch):
    """La confirmacion es en espanol ('s' para si), no el 'y' de typer.confirm."""
    llamadas = []

    class Espia(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas.append(1)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Espia)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social),
    ], input="s\n")

    assert resultado.exit_code == 0
    assert llamadas == [1]
    assert "[y/N]" not in resultado.stdout
    assert "[s/N]" in resultado.stdout


def test_con_yes_publica_y_escribe_resultado(social, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--yes",
    ])

    assert resultado.exit_code == 0
    assert "facebook: publicación confirmada https://facebook.com/1" in resultado.stdout
    destino = social / "Histopast" / "posts" / "2026-09-07-prueba" / "resultado.json"
    datos = json.loads(destino.read_text(encoding="utf-8"))
    assert datos["resultados"][0]["status"] == "publicado"


def test_errores_de_validacion_abortan_sin_publicar(social, monkeypatch):
    """Hallazgo de revisión: este test seguía en verde aunque se quitara el
    `if any(errores.values()): raise typer.Exit(1)` de `_publicar_impl`,
    por dos motivos que no tienen nada que ver con ese aborto: la línea
    "PROBLEMA [media]: ..." la imprime `render_preview()` haya o no aborto
    (aparece siempre que hay errores, se publique o no), y sin el aborto el
    código seguiría adelante y llamaría al adaptador REAL de Instagram, que
    fallaría igual -por otro motivo: sin `media_url_base`/`ig_user_id` en
    accounts.yml- dejando el exit code en 1 por una causa distinta a la que
    el test dice comprobar. Lo que de verdad hay que pinzar es "con errores
    de validación no se publica nada": un espía sobre el adaptador que
    registre las llamadas, igual que en `test_dry_run_muestra_el_preview_y_
    no_publica` y `test_sin_confirmar_no_publica`.

    Verificado por reversión: quitando el aborto de `_publicar_impl`,
    `llamadas` pasa a valer `[1]` (el espía sí llega a invocarse) y la
    aserción de más abajo falla.
    """
    # Instagram sin media: no debe llegar a publicar nada.
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {"instagram": {"body": "hola"}},
        }, allow_unicode=True),
        encoding="utf-8",
    )

    llamadas = []

    class EspiaInstagram(InstagramAdapter):
        def publish(self, post, brand, client):
            llamadas.append(1)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagram)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--yes",
    ])

    assert resultado.exit_code == 1
    assert "exige imagen o video" in resultado.stdout
    assert llamadas == []


def test_marca_inexistente_da_error_util(social):
    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "NoExiste", "--root", str(social),
    ])
    assert resultado.exit_code == 1
    assert "Histopast" in resultado.stdout  # lista las marcas disponibles


# --- Cobertura adicional: avisos, errores de las demas excepciones y `retry` ---


def test_aviso_de_slug_no_coincide_se_muestra_en_el_preview(social):
    """cargar_post emite un UserWarning cuando 'slug' en post.yml no coincide
    con la carpeta real. El filtro por defecto de Python deduplica avisos
    dentro de un mismo proceso y los manda a stderr, asi que el CLI debe
    capturarlo el mismo y mostrarlo el por stdout, junto al preview."""
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    datos = yaml.safe_load((carpeta / "post.yml").read_text(encoding="utf-8"))
    datos["slug"] = "otro-slug-que-no-es-la-carpeta"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8"
    )

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--dry-run",
    ])

    assert resultado.exit_code == 0
    assert "no coincide con el nombre real de la carpeta" in resultado.stdout


def test_nombre_de_marca_invalido_da_error_util(social):
    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "../fuera-de-la-raiz",
        "--root", str(social),
    ])
    assert resultado.exit_code == 1
    assert "no puede contener separadores de ruta" in resultado.stdout


def test_accounts_invalido_da_error_util(social):
    (social / "Histopast" / "accounts.yml").write_text("- uno\n- dos\n", encoding="utf-8")

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social),
    ])

    assert resultado.exit_code == 1
    assert "un mapping (clave: valor) de cuentas" in resultado.stdout


def test_error_de_persistencia_no_revienta_con_traza(social, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    permisos_originales = carpeta.stat().st_mode
    carpeta.chmod(0o500)
    try:
        resultado = runner.invoke(app, [
            "publish", "2026-09-07-prueba", "--brand", "Histopast",
            "--root", str(social), "--yes",
        ])
    finally:
        carpeta.chmod(permisos_originales)

    assert resultado.exit_code == 1
    assert "no se pudo guardar" in resultado.stdout.lower()
    assert "Traceback" not in resultado.output


def test_retry_sin_intento_previo(social):
    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social),
    ])
    assert resultado.exit_code == 1
    assert "no hay un intento previo" in resultado.stdout.lower()


def test_retry_fichero_corrupto(social):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "resultado.json").write_text("{esto no es json valido", encoding="utf-8")

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social),
    ])
    assert resultado.exit_code == 1
    assert "no se pudo interpretar" in resultado.stdout.lower()


def test_retry_sin_redes_fallidas(social):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    resultado_previo = {
        "slug": "2026-09-07-prueba", "marca": "Histopast", "campana": "post-imagen",
        "actualizado": "2026-01-01T00:00:00",
        "resultados": [
            {"platform": "facebook", "status": "publicado", "url": "https://facebook.com/1",
             "fecha": "2026-01-01T00:00:00"},
        ],
    }
    (carpeta / "resultado.json").write_text(json.dumps(resultado_previo), encoding="utf-8")

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social),
    ])
    assert resultado.exit_code == 0
    assert "no hay redes fallidas que reintentar" in resultado.stdout.lower()


def test_retry_republica_solo_las_fallidas_y_preserva_las_demas(social, monkeypatch):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {
                "facebook": {"body": "Dato historico", "hashtags": ["historia"]},
                "instagram": {"body": "Dato en instagram"},
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )
    resultado_previo = {
        "slug": "2026-09-07-prueba", "marca": "Histopast", "campana": "post-imagen",
        "actualizado": "2026-01-01T00:00:00",
        "resultados": [
            {"platform": "facebook", "status": "publicado", "url": "https://facebook.com/viejo",
             "fecha": "2026-01-01T00:00:00"},
            {"platform": "instagram", "status": "error", "error": "fallo de red",
             "fecha": "2026-01-01T00:00:00"},
        ],
    }
    (carpeta / "resultado.json").write_text(json.dumps(resultado_previo), encoding="utf-8")

    llamadas_facebook = []
    llamadas_instagram = []

    class EspiaFacebook(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas_facebook.append(1)
            return super().publish(post, brand, client)

    class EspiaInstagram(Adapter):
        platform = Platform.INSTAGRAM

        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            llamadas_instagram.append(1)
            return PostResult(platform=self.platform, status=PostStatus.PUBLICADO,
                               url="https://instagram.com/nuevo")

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, EspiaFacebook)
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagram)

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])

    assert resultado.exit_code == 0, resultado.stdout
    assert llamadas_facebook == []
    assert llamadas_instagram == [1]

    # Hallazgo Menor 4 (revisión final): facebook queda fuera de este
    # reintento porque ya estaba publicado, no porque el usuario haya
    # escrito `--only` (aquí ni siquiera existe esa opción); el preview de
    # `retry` no debe atribuir la exclusión a `--only`.
    assert "no se reintenta en este intento" in resultado.stdout
    assert "--only" not in resultado.stdout

    datos = json.loads((carpeta / "resultado.json").read_text(encoding="utf-8"))
    por_red = {r["platform"]: r for r in datos["resultados"]}
    assert por_red["facebook"]["url"] == "https://facebook.com/viejo"
    assert por_red["instagram"]["status"] == "publicado"
    assert por_red["instagram"]["url"] == "https://instagram.com/nuevo"


def test_retry_avisa_si_post_yml_ya_no_incluye_una_red_fallida(social):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    # el post.yml del fixture "social" solo tiene facebook: tiktok ya no esta.
    resultado_previo = {
        "slug": "2026-09-07-prueba", "marca": "Histopast", "campana": "post-imagen",
        "actualizado": "2026-01-01T00:00:00",
        "resultados": [
            {"platform": "tiktok", "status": "error", "error": "fallo de red",
             "fecha": "2026-01-01T00:00:00"},
        ],
    }
    (carpeta / "resultado.json").write_text(json.dumps(resultado_previo), encoding="utf-8")

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])

    assert resultado.exit_code == 0
    assert "tiktok" in resultado.stdout
    assert "ya no incluye" in resultado.stdout.lower()


def test_retry_no_reintenta_automaticamente_tras_aviso_de_duplicado(social, monkeypatch):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {"instagram": {"body": "Dato en instagram"}},
        }, allow_unicode=True),
        encoding="utf-8",
    )
    resultado_previo = {
        "slug": "2026-09-07-prueba", "marca": "Histopast", "campana": "post-imagen",
        "actualizado": "2026-01-01T00:00:00",
        "resultados": [
            {"platform": "instagram", "status": "error",
             "error": ("Instagram respondio con un identificador de publicacion "
                       "inesperado; la publicacion probablemente ya esta hecha, "
                       "NO la reintentes: volver a publicar puede duplicarla."),
             "riesgo_duplicado": True,
             "fecha": "2026-01-01T00:00:00"},
        ],
    }
    (carpeta / "resultado.json").write_text(json.dumps(resultado_previo), encoding="utf-8")

    llamadas = []

    class EspiaInstagram(Adapter):
        platform = Platform.INSTAGRAM

        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            llamadas.append(1)
            return PostResult(platform=self.platform, status=PostStatus.PUBLICADO, url="x")

    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagram)

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])

    assert llamadas == []
    assert resultado.exit_code == 1
    assert "riesgo de publicación duplicada" in resultado.stdout


def test_retry_no_reintenta_si_riesgo_duplicado_aunque_el_mensaje_no_diga_duplicar(
    social, monkeypatch
):
    """Hallazgo de revisión: la decisión de `retry` depende del campo
    estructurado `riesgo_duplicado`, no de buscar la palabra "duplicar" en
    el mensaje. Un adaptador futuro (o uno existente, tras un cambio de
    wording) que marque el campo sin usar esa palabra debe bloquearse
    igual."""
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {"instagram": {"body": "Dato en instagram"}},
        }, allow_unicode=True),
        encoding="utf-8",
    )
    resultado_previo = {
        "slug": "2026-09-07-prueba", "marca": "Histopast", "campana": "post-imagen",
        "actualizado": "2026-01-01T00:00:00",
        "resultados": [
            {"platform": "instagram", "status": "error",
             "error": "la red respondio de forma ambigua tras aceptar el contenido",
             "riesgo_duplicado": True,
             "fecha": "2026-01-01T00:00:00"},
        ],
    }
    (carpeta / "resultado.json").write_text(json.dumps(resultado_previo), encoding="utf-8")

    llamadas = []

    class EspiaInstagram(Adapter):
        platform = Platform.INSTAGRAM

        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            llamadas.append(1)
            return PostResult(platform=self.platform, status=PostStatus.PUBLICADO, url="x")

    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagram)

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])

    assert llamadas == []
    assert resultado.exit_code == 1
    assert "riesgo de publicación duplicada" in resultado.stdout


def test_retry_reintenta_si_falta_el_campo_riesgo_duplicado_en_un_resultado_antiguo(
    social, monkeypatch
):
    """Un resultado.json escrito antes de que existiera `riesgo_duplicado`
    no tiene ese campo en sus entradas. Su ausencia se interpreta como
    `False` (no arriesgada) -igual que el valor por defecto del propio
    modelo `PostResult`-, así que una red que quedó en error por un motivo
    corriente (no un aviso de duplicado) sigue reintentándose en
    automático, no queda bloqueada solo por venir de un fichero antiguo."""
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {"instagram": {"body": "Dato en instagram"}},
        }, allow_unicode=True),
        encoding="utf-8",
    )
    resultado_previo = {
        "slug": "2026-09-07-prueba", "marca": "Histopast", "campana": "post-imagen",
        "actualizado": "2026-01-01T00:00:00",
        "resultados": [
            {"platform": "instagram", "status": "error", "error": "fallo de red",
             "fecha": "2026-01-01T00:00:00"},
        ],
    }
    (carpeta / "resultado.json").write_text(json.dumps(resultado_previo), encoding="utf-8")

    llamadas = []

    class EspiaInstagram(Adapter):
        platform = Platform.INSTAGRAM

        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            llamadas.append(1)
            return PostResult(platform=self.platform, status=PostStatus.PUBLICADO, url="x")

    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagram)

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])

    assert resultado.exit_code == 0, resultado.stdout
    assert llamadas == [1]


def test_retry_fuerza_error_si_queda_una_red_pendiente_de_revision_manual(social, monkeypatch):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {
                "facebook": {"body": "Dato historico", "hashtags": ["historia"]},
                "instagram": {"body": "Dato en instagram"},
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )
    resultado_previo = {
        "slug": "2026-09-07-prueba", "marca": "Histopast", "campana": "post-imagen",
        "actualizado": "2026-01-01T00:00:00",
        "resultados": [
            {"platform": "facebook", "status": "error", "error": "fallo de red",
             "fecha": "2026-01-01T00:00:00"},
            {"platform": "instagram", "status": "error",
             "error": "... NO la reintentes: volver a publicar puede duplicarla.",
             "riesgo_duplicado": True,
             "fecha": "2026-01-01T00:00:00"},
        ],
    }
    (carpeta / "resultado.json").write_text(json.dumps(resultado_previo), encoding="utf-8")

    llamadas_facebook = []
    llamadas_instagram = []

    class EspiaFacebook(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas_facebook.append(1)
            return super().publish(post, brand, client)

    class EspiaInstagram(Adapter):
        platform = Platform.INSTAGRAM

        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            llamadas_instagram.append(1)
            return PostResult(platform=self.platform, status=PostStatus.PUBLICADO, url="x")

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, EspiaFacebook)
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagram)

    resultado = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])

    assert llamadas_facebook == [1]
    assert llamadas_instagram == []
    assert resultado.exit_code == 1


def test_retry_relee_riesgo_duplicado_desde_resultado_json_no_de_memoria(social, monkeypatch):
    """El campo `riesgo_duplicado` debe sobrevivir el viaje por
    resultado.json: `guardar_resultado` lo serializa y `retry` lo lee del
    fichero en disco, no de los `PostResult` en memoria de la ejecución que
    los produjo (que ya no existen en una invocación posterior de la
    CLI). Se comprueba con un round-trip real, sin JSON escrito a mano: un
    `publish` persiste un resultado con el campo marcado, y una invocación
    de `retry` completamente aparte -que no comparte ningún estado en
    memoria con la anterior- debe negarse a reintentar esa red."""
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {"instagram": {"body": "Dato en instagram"}},
        }, allow_unicode=True),
        encoding="utf-8",
    )

    class EspiaInstagramFalla(Adapter):
        platform = Platform.INSTAGRAM

        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            return PostResult(
                platform=self.platform, status=PostStatus.ERROR,
                error="la red respondio de forma ambigua tras aceptar el contenido",
                riesgo_duplicado=True,
            )

    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagramFalla)

    primero = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])
    assert primero.exit_code == 1  # la red quedo en ERROR

    datos = json.loads((carpeta / "resultado.json").read_text(encoding="utf-8"))
    assert datos["resultados"][0]["riesgo_duplicado"] is True

    llamadas = []

    class EspiaInstagramSegundoIntento(Adapter):
        platform = Platform.INSTAGRAM

        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            llamadas.append(1)
            return PostResult(platform=self.platform, status=PostStatus.PUBLICADO, url="x")

    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagramSegundoIntento)

    segundo = runner.invoke(app, [
        "retry", "2026-09-07-prueba", "--brand", "Histopast", "--root", str(social), "--yes",
    ])

    assert llamadas == []
    assert segundo.exit_code == 1
    assert "riesgo de publicación duplicada" in segundo.stdout


# --- _esperar_codigo: servidor local real (no monkeypatcheado) ---


def test_esperar_codigo_recibe_code_y_state_y_libera_el_puerto():
    puerto = _puerto_libre()

    def _cliente():
        time.sleep(0.15)
        with urllib.request.urlopen(
            f"http://localhost:{puerto}/callback?code=abc123&state=xyz789", timeout=5
        ) as resp:
            resp.read()

    hilo = threading.Thread(target=_cliente)
    hilo.start()
    recibido = _esperar_codigo(puerto, timeout_s=5)
    hilo.join(timeout=5)

    assert recibido == {"code": "abc123", "state": "xyz789"}

    # El servidor se cierra tras la unica peticion: el puerto vuelve a estar
    # libre de inmediato. Se comprueba volviendo a llamar a la misma funcion
    # (el camino real: un segundo `socialctl auth` sobre el mismo puerto),
    # en vez de un bind crudo, que en BSD/macOS puede toparse con el
    # TIME_WAIT de la conexion ya cerrada aunque el puerto este realmente
    # libre para un nuevo `HTTPServer` (que sí fija SO_REUSEADDR).
    assert _esperar_codigo(puerto, timeout_s=0.3) == {}


def test_esperar_codigo_agota_el_plazo_sin_bloquear_para_siempre():
    puerto = _puerto_libre()
    inicio = time.monotonic()

    recibido = _esperar_codigo(puerto, timeout_s=0.3)

    duracion = time.monotonic() - inicio
    assert recibido == {}
    assert duracion < 5.0  # nunca se queda escuchando indefinidamente


def test_esperar_codigo_puerto_ocupado_da_error_util():
    puerto = _puerto_libre()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ocupado:
        ocupado.bind(("localhost", puerto))
        ocupado.listen(1)

        with pytest.raises(RuntimeError) as exc:
            _esperar_codigo(puerto, timeout_s=1)

    assert str(puerto) in str(exc.value)


# --- _pagina_para: Hallazgo 2 de la revisión de la Task 14. La página que ve
# el usuario al volver del navegador decía siempre "Listo" aunque la
# redirección trajera un `error` o un `state` que no coincide. Las
# aserciones comprueban tanto qué página se elige como que ninguna de las
# tres contiene el dato real recibido (no se puede confiar en lo que trae
# la redirección: no se refleja de vuelta). ---


def test_pagina_para_exito_cuando_el_state_coincide():
    pagina = _pagina_para({"code": "c", "state": "abc"}, "abc")
    assert pagina == cli._PAGINA_LISTO


def test_pagina_para_error_del_proveedor_no_dice_listo():
    pagina = _pagina_para({"error": "access_denied"}, "abc")
    assert pagina == cli._PAGINA_ERROR_AUTORIZACION
    assert pagina != cli._PAGINA_LISTO
    # Estática: nunca vuelca el valor real de `error`.
    assert "access_denied" not in pagina


def test_pagina_para_state_no_coincidente_no_dice_listo():
    pagina = _pagina_para({"code": "c", "state": "otro-valor"}, "abc")
    assert pagina == cli._PAGINA_ESTADO_INVALIDO
    assert pagina != cli._PAGINA_LISTO
    # Estática: nunca vuelca ni el state recibido ni el esperado.
    assert "otro-valor" not in pagina
    assert "abc" not in pagina


def test_pagina_para_state_ausente_no_dice_listo():
    pagina = _pagina_para({"code": "c"}, "abc")
    assert pagina == cli._PAGINA_ESTADO_INVALIDO


def test_pagina_para_error_tiene_prioridad_sobre_el_state():
    """Si la redirección trae ambos -`error` y un `state` que además no
    coincide-, se muestra la página de error, no la de state inválido: es
    la explicación más específica de lo que pasó de verdad (el proveedor
    rechazó la autorización; nunca se llegó a comprobar el state)."""
    pagina = _pagina_para({"error": "access_denied", "state": "otro-valor"}, "abc")
    assert pagina == cli._PAGINA_ERROR_AUTORIZACION


def test_pagina_para_sin_estado_esperado_se_salta_la_distincion():
    """`estado_esperado=None` (nadie lo pidió) se salta la comprobación del
    state y muestra éxito salvo que haya `error`."""
    assert _pagina_para({"code": "c", "state": "lo-que-sea"}, None) == cli._PAGINA_LISTO
    assert _pagina_para({"error": "access_denied"}, None) == cli._PAGINA_ERROR_AUTORIZACION


# --- _esperar_codigo + _pagina_para: extremo a extremo con servidor real ---


def test_esperar_codigo_con_error_del_proveedor_devuelve_la_pagina_de_error():
    puerto = _puerto_libre()
    cuerpo: dict[str, bytes] = {}

    def _cliente():
        time.sleep(0.15)
        with urllib.request.urlopen(
            f"http://localhost:{puerto}/callback?error=access_denied&"
            "error_description=VALOR-DEL-PROVEEDOR-1234",
            timeout=5,
        ) as resp:
            cuerpo["valor"] = resp.read()

    hilo = threading.Thread(target=_cliente)
    hilo.start()
    recibido = _esperar_codigo(puerto, "el-state-esperado", timeout_s=5)
    hilo.join(timeout=5)

    assert recibido["error"] == "access_denied"
    pagina = cuerpo["valor"].decode("utf-8")
    assert pagina == cli._PAGINA_ERROR_AUTORIZACION
    assert "Listo" not in pagina
    # La página nunca interpola lo que trae la redirección.
    assert "VALOR-DEL-PROVEEDOR-1234" not in pagina
    assert "access_denied" not in pagina


def test_esperar_codigo_con_state_no_coincidente_devuelve_la_pagina_de_aviso():
    puerto = _puerto_libre()
    cuerpo: dict[str, bytes] = {}

    def _cliente():
        time.sleep(0.15)
        with urllib.request.urlopen(
            f"http://localhost:{puerto}/callback?code=abc123&state=UN-STATE-DISTINTO",
            timeout=5,
        ) as resp:
            cuerpo["valor"] = resp.read()

    hilo = threading.Thread(target=_cliente)
    hilo.start()
    recibido = _esperar_codigo(puerto, "el-state-esperado", timeout_s=5)
    hilo.join(timeout=5)

    assert recibido["state"] == "UN-STATE-DISTINTO"
    pagina = cuerpo["valor"].decode("utf-8")
    assert pagina == cli._PAGINA_ESTADO_INVALIDO
    assert "Listo" not in pagina
    assert "UN-STATE-DISTINTO" not in pagina
    assert "el-state-esperado" not in pagina


# --- auth: wiring general ---


def test_auth_marca_desconocida_da_error_util(tmp_path):
    resultado = runner.invoke(app, ["auth", "youtube", "--brand", "Nadie", "--root", str(tmp_path)])

    assert resultado.exit_code == 1
    assert "no existe la marca" in resultado.stdout
    assert "Traceback" not in resultado.output


def test_auth_red_desconocida_da_error_util(tmp_path):
    crear_brand(tmp_path, "Histopast")

    resultado = runner.invoke(
        app, ["auth", "mastodon", "--brand", "Histopast", "--root", str(tmp_path)]
    )

    assert resultado.exit_code == 1
    assert "red desconocida" in resultado.stdout
    assert "Traceback" not in resultado.output


# --- auth youtube / tiktok: flujo de redireccion (navegador y servidor local
# monkeypatcheados; el resto -URL, PKCE, state, canje- corre de verdad) ---


@respx.mock
def test_auth_youtube_guarda_las_credenciales_con_pkce_y_state_correctos(tmp_path, monkeypatch):
    crear_brand(tmp_path, "Histopast")
    capturado = {}

    def _fake_open(url):
        capturado["url"] = url
        return True

    monkeypatch.setattr(cli.webbrowser, "open", _fake_open)

    def _fake_esperar(puerto, estado_esperado=None, timeout_s=cli.TIMEOUT_CALLBACK_S):
        parametros = dict(urllib.parse.parse_qsl(capturado["url"].split("?", 1)[1]))
        return {"code": "codigo-de-prueba", "state": parametros["state"]}

    monkeypatch.setattr(cli, "_esperar_codigo", _fake_esperar)

    ruta = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        })
    )

    resultado = runner.invoke(app, [
        "auth", "youtube", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="cid\ncsecret\n")

    assert resultado.exit_code == 0, resultado.stdout
    assert "Credenciales de youtube guardadas" in resultado.stdout
    assert "csecret" not in resultado.stdout

    secreto = json.loads((tmp_path / "Histopast" / ".secrets" / "youtube.json").read_text())
    assert secreto["access_token"] == "at"
    assert secreto["refresh_token"] == "rt"
    assert secreto["client_id"] == "cid"
    assert secreto["client_secret"] == "csecret"

    # El code_verifier mandado en el canje debe ser el mismo cuyo challenge
    # (base64url, el estandar que usa Google) se mando en la URL de
    # autorizacion: si no coincidieran, el canje real de Google fallaria.
    enviado = dict(httpx.QueryParams(ruta.calls.last.request.content.decode()))
    verifier = enviado["code_verifier"]
    challenge_esperado = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    parametros_url = dict(urllib.parse.parse_qsl(capturado["url"].split("?", 1)[1]))
    assert parametros_url["code_challenge"] == challenge_esperado
    assert parametros_url["code_challenge_method"] == "S256"
    assert parametros_url["access_type"] == "offline"


@respx.mock
def test_auth_youtube_state_no_coincidente_aborta_sin_guardar_nada(tmp_path, monkeypatch):
    """Hallazgo de revisión (sexta vez que aparece este patrón en el
    proyecto): dos de las aserciones originales de este test -`exit_code ==
    1` y `"state" in resultado.stdout.lower()`- pasarían igual aunque no
    existiera la verificación del `state`. La URL de autorización, que
    siempre se imprime antes de cualquier comprobación, ya contiene
    "state=..." (el parámetro, no la palabra "state" sobre un mensaje de
    error), y un canje rechazado por cualquier otro motivo también daría
    exit_code 1. La frase completa del mensaje de `EstadoInvalido` ("no
    coincide con el generado") solo la emite esa comprobación en concreto,
    y `not ruta_token.called` es la aserción que de verdad pincha: sin la
    verificación del `state`, el código seguiría adelante y canjearía el
    código de todas formas.
    """
    crear_brand(tmp_path, "Histopast")

    monkeypatch.setattr(cli.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(cli, "_esperar_codigo", lambda puerto, estado_esperado=None, timeout_s=cli.TIMEOUT_CALLBACK_S: {
        "code": "codigo-de-prueba", "state": "un-estado-que-no-coincide",
    })

    ruta_token = respx.post("https://oauth2.googleapis.com/token")

    resultado = runner.invoke(app, [
        "auth", "youtube", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="cid\ncsecret\n")

    assert resultado.exit_code == 1
    # Frase propia de EstadoInvalido, no la subcadena suelta "state": la URL
    # de autorización (impresa siempre, se verifique o no el state) ya
    # contiene "state=...", así que esa subcadena aparecía igual aunque se
    # quitara la comprobación real.
    assert "no coincide con el generado" in resultado.stdout
    assert not ruta_token.called  # la aserción que de verdad pincha: nunca se llega a canjear el codigo
    assert not (tmp_path / "Histopast" / ".secrets" / "youtube.json").exists()


@respx.mock
def test_auth_tiktok_usa_client_key_y_challenge_hexadecimal(tmp_path, monkeypatch):
    crear_brand(tmp_path, "Histopast")
    capturado = {}

    monkeypatch.setattr(cli.webbrowser, "open", lambda url: capturado.__setitem__("url", url))

    def _fake_esperar(puerto, estado_esperado=None, timeout_s=cli.TIMEOUT_CALLBACK_S):
        parametros = dict(urllib.parse.parse_qsl(capturado["url"].split("?", 1)[1]))
        return {"code": "codigo-de-prueba", "state": parametros["state"]}

    monkeypatch.setattr(cli, "_esperar_codigo", _fake_esperar)

    ruta = respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
        })
    )

    resultado = runner.invoke(app, [
        "auth", "tiktok", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="ck\ncsecret\n")

    assert resultado.exit_code == 0, resultado.stdout

    secreto = json.loads((tmp_path / "Histopast" / ".secrets" / "tiktok.json").read_text())
    assert secreto["access_token"] == "at"
    assert secreto["client_key"] == "ck"
    assert "client_id" not in secreto

    parametros_url = dict(urllib.parse.parse_qsl(capturado["url"].split("?", 1)[1]))
    assert parametros_url["client_key"] == "ck"

    enviado = dict(httpx.QueryParams(ruta.calls.last.request.content.decode()))
    verifier = enviado["code_verifier"]
    challenge_esperado_hex = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    assert parametros_url["code_challenge"] == challenge_esperado_hex


# --- auth tiktok: obtiene y guarda el open_id automáticamente ---


def _auth_tiktok_con_respuesta(tmp_path, monkeypatch, cuerpo_respuesta: dict):
    """Ejecuta `auth tiktok` con el navegador y el servidor local
    monkeypatcheados (igual que el resto de tests de este bloque) y
    `cuerpo_respuesta` como cuerpo JSON del canje de token."""
    capturado = {}
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: capturado.__setitem__("url", url))

    def _fake_esperar(puerto, estado_esperado=None, timeout_s=cli.TIMEOUT_CALLBACK_S):
        parametros = dict(urllib.parse.parse_qsl(capturado["url"].split("?", 1)[1]))
        return {"code": "codigo-de-prueba", "state": parametros["state"]}

    monkeypatch.setattr(cli, "_esperar_codigo", _fake_esperar)

    respx.post("https://open.tiktokapis.com/v2/oauth/token/").mock(
        return_value=httpx.Response(200, json=cuerpo_respuesta)
    )

    resultado = runner.invoke(app, [
        "auth", "tiktok", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="ck\ncsecret\n")

    return resultado, capturado


@respx.mock
def test_auth_tiktok_pide_tambien_el_scope_user_info_basic(tmp_path, monkeypatch):
    crear_brand(tmp_path, "Histopast")

    _resultado, capturado = _auth_tiktok_con_respuesta(tmp_path, monkeypatch, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
        "open_id": "oid-scope-1",
    })

    parametros_url = dict(urllib.parse.parse_qsl(capturado["url"].split("?", 1)[1]))
    assert parametros_url["scope"] == "user.info.basic,user.info.stats,video.upload,video.publish,video.list"


@respx.mock
def test_auth_tiktok_guarda_el_open_id_automaticamente_en_accounts_yml(tmp_path, monkeypatch):
    crear_brand(tmp_path, "Histopast")

    resultado, _capturado = _auth_tiktok_con_respuesta(tmp_path, monkeypatch, {
        "access_token": "at-secreto-reconocible-1", "refresh_token": "rt",
        "expires_in": 86400, "open_id": "oid-real-1",
    })

    assert resultado.exit_code == 0, resultado.stdout
    assert "open_id" in resultado.stdout
    assert "accounts.yml" in resultado.stdout
    # El token de acceso es un secreto: nunca debe aparecer en la salida,
    # ni siquiera en el aviso nuevo de que se ha guardado el open_id.
    assert "at-secreto-reconocible-1" not in resultado.stdout

    texto_accounts = (tmp_path / "Histopast" / "accounts.yml").read_text(encoding="utf-8")
    assert 'open_id: "oid-real-1"' in texto_accounts
    # Los comentarios de la plantilla deben seguir intactos.
    assert "IDs de las cuentas de esta marca" in texto_accounts

    # El open_id vive solo en accounts.yml, no duplicado en el secreto.
    secreto = json.loads((tmp_path / "Histopast" / ".secrets" / "tiktok.json").read_text())
    assert "open_id" not in secreto
    assert secreto["access_token"] == "at-secreto-reconocible-1"


@respx.mock
def test_auth_tiktok_sin_open_id_en_la_respuesta_avisa_y_no_falla(tmp_path, monkeypatch):
    crear_brand(tmp_path, "Histopast")

    resultado, _capturado = _auth_tiktok_con_respuesta(tmp_path, monkeypatch, {
        "access_token": "at-secreto-reconocible-2", "refresh_token": "rt",
        "expires_in": 86400,
        # Sin "open_id": simula una respuesta que no lo incluye.
    })

    # La autenticación no debe fallar por esto: el token ya se obtuvo y es
    # lo importante.
    assert resultado.exit_code == 0, resultado.stdout
    assert "Credenciales de tiktok guardadas" in resultado.stdout
    assert "open_id" in resultado.stdout  # el aviso menciona qué falta
    assert "at-secreto-reconocible-2" not in resultado.stdout

    secreto = json.loads((tmp_path / "Histopast" / ".secrets" / "tiktok.json").read_text())
    assert secreto["access_token"] == "at-secreto-reconocible-2"

    # accounts.yml no debe haberse tocado: sigue con el valor vacío de la plantilla.
    texto_accounts = (tmp_path / "Histopast" / "accounts.yml").read_text(encoding="utf-8")
    assert 'open_id: ""' in texto_accounts


@respx.mock
def test_auth_tiktok_con_open_id_distinto_avisa_del_cambio(tmp_path, monkeypatch):
    brand = crear_brand(tmp_path, "Histopast")
    brand.guardar_open_id_tiktok("oid-antiguo")

    resultado, _capturado = _auth_tiktok_con_respuesta(tmp_path, monkeypatch, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 86400,
        "open_id": "oid-nuevo",
    })

    assert resultado.exit_code == 0, resultado.stdout
    assert "oid-antiguo" in resultado.stdout
    assert "oid-nuevo" in resultado.stdout

    texto_accounts = (tmp_path / "Histopast" / "accounts.yml").read_text(encoding="utf-8")
    assert 'open_id: "oid-nuevo"' in texto_accounts


# --- auth facebook / instagram: token de usuario -> token de pagina ---


def _marca_con_page_id(tmp_path, page_id: str = "111"):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text(
        yaml.safe_dump({"facebook": {"page_id": page_id}}, allow_unicode=True),
        encoding="utf-8",
    )
    return brand


def test_auth_facebook_sin_page_id_da_error_util(tmp_path):
    crear_brand(tmp_path, "Histopast")  # accounts.yml de plantilla: page_id vacio

    resultado = runner.invoke(app, [
        "auth", "facebook", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="app-id\napp-secret\ntoken-usuario\n")

    assert resultado.exit_code == 1
    assert "facebook.page_id" in resultado.stdout


@respx.mock
def test_auth_facebook_guarda_el_token_de_pagina(tmp_path):
    _marca_con_page_id(tmp_path, "111")

    ruta_exchange = respx.get("https://graph.facebook.com/v26.0/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "token-usuario-largo"})
    )
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        return_value=httpx.Response(200, json={
            "data": [
                {"id": "111", "name": "Histopast", "access_token": "token-pagina-111"},
                {"id": "222", "name": "Otra", "access_token": "token-pagina-222"},
            ]
        })
    )

    resultado = runner.invoke(app, [
        "auth", "facebook", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="app-id\napp-secret\ntoken-usuario-corto\n")

    assert resultado.exit_code == 0, resultado.stdout
    assert "token-usuario-corto" not in resultado.stdout
    assert "token-pagina-111" not in resultado.stdout

    secreto = json.loads((tmp_path / "Histopast" / ".secrets" / "facebook.json").read_text())
    assert secreto == {"access_token": "token-pagina-111"}

    enviado = dict(ruta_exchange.calls.last.request.url.params)
    assert enviado["fb_exchange_token"] == "token-usuario-corto"


@respx.mock
def test_auth_instagram_reutiliza_el_mismo_flujo_y_guarda_su_propio_secreto(tmp_path):
    _marca_con_page_id(tmp_path, "111")

    respx.get("https://graph.facebook.com/v26.0/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "token-usuario-largo"})
    )
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        return_value=httpx.Response(200, json={
            "data": [{"id": "111", "name": "Histopast", "access_token": "token-pagina-111"}]
        })
    )

    resultado = runner.invoke(app, [
        "auth", "instagram", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="app-id\napp-secret\ntoken-usuario-corto\n")

    assert resultado.exit_code == 0, resultado.stdout
    secreto = json.loads((tmp_path / "Histopast" / ".secrets" / "instagram.json").read_text())
    assert secreto == {"access_token": "token-pagina-111"}
    # Se guarda por separado del de facebook: no debe existir todavia.
    assert not (tmp_path / "Histopast" / ".secrets" / "facebook.json").exists()


@respx.mock
def test_auth_facebook_pagina_no_encontrada_lista_las_disponibles(tmp_path):
    _marca_con_page_id(tmp_path, "999-no-existe")

    respx.get("https://graph.facebook.com/v26.0/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "token-usuario-largo"})
    )
    respx.get("https://graph.facebook.com/v26.0/me/accounts").mock(
        return_value=httpx.Response(200, json={
            "data": [{"id": "111", "name": "Histopast", "access_token": "token-pagina-111"}]
        })
    )

    resultado = runner.invoke(app, [
        "auth", "facebook", "--brand", "Histopast", "--root", str(tmp_path),
    ], input="app-id\napp-secret\ntoken-usuario-corto\n")

    assert resultado.exit_code == 1
    assert "Histopast" in resultado.stdout  # lista la pagina disponible
    assert "token-pagina-111" not in resultado.stdout
    assert not (tmp_path / "Histopast" / ".secrets" / "facebook.json").exists()


# --- progreso durante publish: senal de vida por red ---


def test_publish_avisa_en_que_red_esta_trabajando(social, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--yes",
    ])

    assert resultado.exit_code == 0, resultado.stdout
    assert "Publicando en facebook" in resultado.stdout


# --- Hallazgo I3 (revisión final): `--only` no dejaba publicar en las redes
# que sí cumplen si otra red (no solicitada) tenía errores de validación.
# `SKILL.md` prometía justo lo contrario.


def test_only_publica_la_red_valida_aunque_otra_tenga_errores_de_validacion(
    social, monkeypatch
):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {
                "facebook": {"body": "Dato historico", "hashtags": ["historia"]},
                "instagram": {"body": "hola"},  # sin media: Instagram la exige
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )

    llamadas = []

    class Espia(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas.append(1)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, Espia)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--only", "facebook", "--yes",
    ])

    assert resultado.exit_code == 0, resultado.stdout
    assert llamadas == [1]
    # El preview sigue mostrando el problema de Instagram (fidelidad): no se
    # oculta solo porque no se vaya a publicar ahí.
    assert "exige imagen o video" in resultado.stdout


def test_only_sigue_bloqueando_si_la_propia_red_solicitada_tiene_errores(
    social, monkeypatch
):
    """La garantía de fondo no se debilita: `--only` nunca publica una red
    que SÍ está entre las solicitadas y tiene errores, aunque otra red del
    post (no solicitada) esté perfectamente sana."""
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {
                "facebook": {"body": "Dato historico"},
                "instagram": {"body": "hola"},  # sin media
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )

    llamadas_facebook = []
    llamadas_instagram = []

    class EspiaFacebook(AdaptadorOK):
        def publish(self, post, brand, client):
            llamadas_facebook.append(1)
            return super().publish(post, brand, client)

    class EspiaInstagram(InstagramAdapter):
        def publish(self, post, brand, client):
            llamadas_instagram.append(1)
            return super().publish(post, brand, client)

    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, EspiaFacebook)
    monkeypatch.setitem(ADAPTADORES, Platform.INSTAGRAM, EspiaInstagram)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--only", "instagram", "--yes",
    ])

    assert resultado.exit_code == 1
    assert llamadas_facebook == []
    assert llamadas_instagram == []


def test_preview_marca_las_redes_excluidas_por_only(social):
    carpeta = social / "Histopast" / "posts" / "2026-09-07-prueba"
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "post-imagen",
            "platforms": {
                "facebook": {"body": "Dato historico"},
                "instagram": {"body": "hola"},
            },
        }, allow_unicode=True),
        encoding="utf-8",
    )

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--only", "facebook", "--dry-run",
    ])

    assert resultado.exit_code == 0, resultado.stdout
    assert "instagram" in resultado.stdout.lower()
    assert "no solicitada" in resultado.stdout.lower()


def test_sin_only_el_preview_no_marca_ninguna_exclusion(social, monkeypatch):
    monkeypatch.setitem(ADAPTADORES, Platform.FACEBOOK, AdaptadorOK)

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(social), "--dry-run",
    ])

    assert resultado.exit_code == 0
    assert "no solicitada" not in resultado.stdout.lower()


# --- Hallazgo I4 (revisión final): la salvaguarda de auditoría de TikTok
# solo se comprobaba al publicar; con mode: direct y auditada: false, el
# preview de --dry-run decía "Problemas detectados: 0" y el fallo solo
# aparecía después de que el usuario aprobara. Ahora TikTokAdapter.validate()
# también la comprueba, así que aparece ya en el preview, antes de aprobar.


def test_preview_de_tiktok_muestra_falta_de_auditoria_antes_de_aprobar(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text(
        "tiktok:\n  open_id: 'oid'\n  mode: direct\n  auditada: false\n",
        encoding="utf-8",
    )
    video = brand.raiz / "media" / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1080x1920:rate=30:duration=4",
         "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True,
    )
    carpeta = brand.dir_posts / "2026-09-07-prueba"
    carpeta.mkdir(parents=True)
    (carpeta / "post.yml").write_text(
        yaml.safe_dump({
            "slug": "2026-09-07-prueba",
            "campaign": "clip-vertical",
            "platforms": {"tiktok": {"body": "hola", "media": ["clip.mp4"]}},
        }, allow_unicode=True),
        encoding="utf-8",
    )

    resultado = runner.invoke(app, [
        "publish", "2026-09-07-prueba", "--brand", "Histopast",
        "--root", str(tmp_path), "--dry-run",
    ])

    assert "Problemas detectados: 0" not in resultado.stdout
    assert "no está auditada" in resultado.stdout
    # No se publica nada (aborta antes de llegar al --dry-run): la
    # aprobación nunca llega a pedirse sobre un post condenado a fallar.
    assert resultado.exit_code == 1
