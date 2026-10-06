import json
from datetime import date

import pytest
from typer.testing import CliRunner

from socialctl.brands import crear_brand
from socialctl.cli import app
from socialctl.metricas.modelos import (
    Cuenta,
    EstadoLectura,
    LecturaRed,
    Metricas,
    Pieza,
    TipoPieza,
)
from socialctl.models import Platform

runner = CliRunner()


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    crear_brand(tmp_path, "Histopast")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _lectura_ok():
    from datetime import datetime

    return LecturaRed(
        estado=EstadoLectura.OK,
        cuenta=Cuenta(seguidores=4950),
        piezas=[Pieza(
            id="vid1", url="https://youtu.be/vid1", titulo="1496: Santo Domingo",
            publicado_el=datetime(2026, 9, 6, 19, 0), tipo=TipoPieza.LARGO,
            acumulado=Metricas(vistas=254),
        )],
    )


def _falsear(monkeypatch, por_red: dict):
    """Sustituye `leer_red` en el CLI por una tabla de respuestas por red."""
    def falso(platform, brand, client, desde):
        return por_red[platform]

    monkeypatch.setattr("socialctl.cli.leer_red", falso)


def test_escribe_snapshot_piezas_y_resumen(raiz, monkeypatch):
    _falsear(monkeypatch, {
        Platform.YOUTUBE: _lectura_ok(),
        Platform.FACEBOOK: _lectura_ok(),
        Platform.INSTAGRAM: _lectura_ok(),
        Platform.TIKTOK: _lectura_ok(),
    })

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )

    assert resultado.exit_code == 0, resultado.output
    carpeta = raiz / "Histopast" / "metricas"
    assert (carpeta / f"{date.today().isoformat()}.json").is_file()
    assert (carpeta / "piezas.yml").is_file()
    assert (carpeta / "resumen.md").is_file()

    crudo = json.loads((carpeta / f"{date.today().isoformat()}.json").read_text(encoding="utf-8"))
    assert set(crudo["redes"]) == {"youtube", "facebook", "instagram", "tiktok"}


def test_una_red_que_falla_no_aborta_las_demas(raiz, monkeypatch):
    _falsear(monkeypatch, {
        Platform.YOUTUBE: _lectura_ok(),
        Platform.FACEBOOK: LecturaRed(estado=EstadoLectura.ERROR, error="se cayó"),
        Platform.INSTAGRAM: _lectura_ok(),
        Platform.TIKTOK: LecturaRed(
            estado=EstadoLectura.SIN_PERMISO,
            error="el token de tiktok no tiene el permiso 'video.list'",
        ),
    })

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )

    assert resultado.exit_code == 0
    assert "youtube: ok" in resultado.output
    assert "facebook: error" in resultado.output
    assert "video.list" in resultado.output


def test_si_fallan_todas_sale_con_error(raiz, monkeypatch):
    fallo = LecturaRed(estado=EstadoLectura.SIN_CREDENCIALES, error="no hay credenciales")
    _falsear(monkeypatch, {p: fallo for p in Platform})

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )
    assert resultado.exit_code == 1


def test_only_limita_las_redes(raiz, monkeypatch):
    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--only", "youtube", "--root", str(raiz)]
    )

    assert resultado.exit_code == 0
    crudo = json.loads(
        (raiz / "Histopast" / "metricas" / f"{date.today().isoformat()}.json").read_text(encoding="utf-8")
    )
    assert set(crudo["redes"]) == {"youtube"}


def test_dos_ejecuciones_el_mismo_dia_no_duplican(raiz, monkeypatch):
    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})

    runner.invoke(app, ["stats", "--brand", "Histopast", "--root", str(raiz)])
    runner.invoke(app, ["stats", "--brand", "Histopast", "--root", str(raiz)])

    carpeta = raiz / "Histopast" / "metricas"
    assert len(list(carpeta.glob("[0-9]*.json"))) == 1


def test_funciona_desde_una_subcarpeta_con_root(tmp_path, monkeypatch):
    """Como `publish`, `retry` y `auth`: la raíz se indica con `--root`.

    Sin esta opción, `stats` solo funcionaría desde la raíz de `Social/`,
    mientras que los otros cuatro comandos funcionan desde cualquier sitio.
    """
    crear_brand(tmp_path, "Histopast")
    subcarpeta = tmp_path / "Histopast" / "posts"
    subcarpeta.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(subcarpeta)
    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(tmp_path)]
    )

    assert resultado.exit_code == 0, resultado.output
    assert (tmp_path / "Histopast" / "metricas" / f"{date.today().isoformat()}.json").is_file()


def test_only_con_una_red_desconocida_lo_dice_en_espanol(raiz, monkeypatch):
    """Un `Enum` de typer daría aquí el error de click, en inglés, e
    incumpliría la primera restricción global del plan."""
    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--only", "twitter", "--root", str(raiz)]
    )

    assert resultado.exit_code == 1
    assert "Redes válidas" in resultado.output
    assert "youtube" in resultado.output


def test_el_slug_llega_a_piezas_yml(raiz, monkeypatch):
    """`asignar_slugs` tiene que correr ANTES de `actualizar_piezas`."""
    import json

    import yaml

    carpeta_post = raiz / "Histopast" / "posts" / "2026-09-06-santo-domingo"
    carpeta_post.mkdir(parents=True)
    (carpeta_post / "resultado.json").write_text(json.dumps({
        "slug": "2026-09-06-santo-domingo",
        "marca": "Histopast",
        "campana": "lanzamiento-video-largo",
        "actualizado": "2026-09-06T19:05:11",
        "resultados": [{
            "platform": "youtube", "status": "publicado",
            "url": "https://www.youtube.com/watch?v=vid1",
            "platform_id": "vid1", "error": None,
            "riesgo_duplicado": False, "fecha": "2026-09-06T19:05:11",
        }],
    }), encoding="utf-8")

    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})
    runner.invoke(
        app, ["stats", "--brand", "Histopast", "--only", "youtube", "--root", str(raiz)]
    )

    piezas = yaml.safe_load(
        (raiz / "Histopast" / "metricas" / "piezas.yml").read_text(encoding="utf-8")
    )
    assert piezas[0]["slug"] == "2026-09-06-santo-domingo"


def test_only_fusiona_conservando_las_demas_redes(raiz, monkeypatch):
    """El caso que motivó el arreglo: tras leer las cuatro redes, un
    `--only` de una sola no puede tirar las otras tres -perdían su cuota de
    API ya gastada, que no se recupera hasta el día siguiente-."""
    _falsear(monkeypatch, {
        Platform.YOUTUBE: _lectura_ok(),
        Platform.FACEBOOK: _lectura_ok(),
        Platform.INSTAGRAM: _lectura_ok(),
        Platform.TIKTOK: LecturaRed(estado=EstadoLectura.OK, cuenta=Cuenta(seguidores=10), piezas=[]),
    })
    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )
    assert resultado.exit_code == 0, resultado.output

    from datetime import datetime

    nueva_instagram = LecturaRed(
        estado=EstadoLectura.OK,
        cuenta=Cuenta(seguidores=5001),
        piezas=[Pieza(
            id="ig-nuevo", url="https://instagram.com/p/ig-nuevo",
            titulo="Nuevo reel", publicado_el=datetime(2026, 9, 11, 12, 0),
            tipo=TipoPieza.VERTICAL, acumulado=Metricas(vistas=999),
        )],
    )
    _falsear(monkeypatch, {Platform.INSTAGRAM: nueva_instagram})

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--only", "instagram", "--root", str(raiz)]
    )
    assert resultado.exit_code == 0, resultado.output

    crudo = json.loads(
        (raiz / "Histopast" / "metricas" / f"{date.today().isoformat()}.json").read_text(encoding="utf-8")
    )
    assert set(crudo["redes"]) == {"youtube", "facebook", "instagram", "tiktok"}
    # instagram: actualizada con la lectura nueva.
    assert crudo["redes"]["instagram"]["piezas"][0]["id"] == "ig-nuevo"
    assert crudo["redes"]["instagram"]["cuenta"]["seguidores"] == 5001
    # las otras tres: intactas, tal y como quedaron en la lectura completa.
    assert crudo["redes"]["youtube"]["piezas"][0]["id"] == "vid1"
    assert crudo["redes"]["facebook"]["piezas"][0]["id"] == "vid1"
    assert crudo["redes"]["tiktok"]["estado"] == "ok"
    assert crudo["redes"]["tiktok"]["piezas"] == []


def test_sin_only_sigue_sobrescribiendo(raiz, monkeypatch):
    """Sin `--only` el snapshot se sigue sobrescribiendo entero: una red que
    hoy falla no puede quedarse con el dato bueno de una ejecución anterior
    del mismo día -eso solo pasa con `--only` (ver el test de arriba)."""
    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})
    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )
    assert resultado.exit_code == 0, resultado.output

    _falsear(monkeypatch, {
        Platform.YOUTUBE: _lectura_ok(),
        Platform.FACEBOOK: LecturaRed(estado=EstadoLectura.ERROR, error="se cayó en la segunda pasada"),
        Platform.INSTAGRAM: _lectura_ok(),
        Platform.TIKTOK: _lectura_ok(),
    })
    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )
    assert resultado.exit_code == 0, resultado.output

    crudo = json.loads(
        (raiz / "Histopast" / "metricas" / f"{date.today().isoformat()}.json").read_text(encoding="utf-8")
    )
    assert set(crudo["redes"]) == {"youtube", "facebook", "instagram", "tiktok"}
    # facebook: la lectura completa manda tal cual, no se conserva el dato
    # bueno de la ejecución anterior del mismo día.
    assert crudo["redes"]["facebook"]["estado"] == "error"
    assert crudo["redes"]["facebook"]["piezas"] == []


def test_only_de_una_red_que_hoy_falla_conserva_el_dato_bueno_y_marca_el_fallo(raiz, monkeypatch):
    """Decisión: una lectura fallida no es un dato mejor que uno bueno de
    hace una hora, así que se conservan `cuenta`/`piezas` del último dato
    bueno; pero tampoco se puede fingir que la red va bien, así que `estado`
    y `error` son los de HOY, no los de antes."""
    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})
    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )
    assert resultado.exit_code == 0, resultado.output

    _falsear(monkeypatch, {
        Platform.INSTAGRAM: LecturaRed(
            estado=EstadoLectura.SIN_CREDENCIALES, error="el token de instagram ha caducado"
        ),
    })
    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--only", "instagram", "--root", str(raiz)]
    )
    # instagram era la única red pedida y ha fallado: el comando sí debe
    # terminar en error (código de salida 1), aunque el snapshot conserve
    # sus piezas buenas de antes.
    assert resultado.exit_code == 1, resultado.output

    crudo = json.loads(
        (raiz / "Histopast" / "metricas" / f"{date.today().isoformat()}.json").read_text(encoding="utf-8")
    )
    instagram = crudo["redes"]["instagram"]
    assert instagram["estado"] == "sin_credenciales"
    assert instagram["error"] == "el token de instagram ha caducado"
    # el dato bueno de hace un rato no se ha tirado.
    assert instagram["piezas"][0]["id"] == "vid1"
    assert instagram["cuenta"]["seguidores"] == 4950
    # las demás redes, intactas.
    assert crudo["redes"]["youtube"]["estado"] == "ok"
    assert crudo["redes"]["facebook"]["estado"] == "ok"
    assert crudo["redes"]["tiktok"]["estado"] == "ok"


def test_only_sin_snapshot_previo_funciona_igual(raiz, monkeypatch):
    """`--only` cuando todavía no hay snapshot de hoy no debe fallar: no hay
    nada que fusionar, así que se guarda tal cual lo leído."""
    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--only", "facebook", "--root", str(raiz)]
    )

    assert resultado.exit_code == 0, resultado.output
    crudo = json.loads(
        (raiz / "Histopast" / "metricas" / f"{date.today().isoformat()}.json").read_text(encoding="utf-8")
    )
    assert set(crudo["redes"]) == {"facebook"}
    assert crudo["redes"]["facebook"]["estado"] == "ok"


def test_avisa_si_una_red_lleva_tres_dias_seguidos_fallando(raiz, monkeypatch):
    """Spec §5, tercer punto de la cadencia: un token caducado no puede pasar
    semanas en silencio.

    La red que falla hoy pero leyó bien hace dos días no debe aparecer en el
    aviso: si bastara con el fallo de hoy, el aviso sonaría cada vez que se
    cae la red un rato y dejaría de significar nada.
    """
    from datetime import timedelta

    from socialctl.brands import cargar_brand
    from socialctl.metricas.almacen import guardar_snapshot
    from socialctl.metricas.modelos import Snapshot

    marca = cargar_brand(raiz, "Histopast")
    fallo = LecturaRed(
        estado=EstadoLectura.SIN_CREDENCIALES, error="no hay credenciales"
    )
    for dias in (1, 2):
        guardar_snapshot(marca, Snapshot(
            fecha=date.today() - timedelta(days=dias),
            marca="Histopast",
            redes={Platform.TIKTOK: fallo, Platform.FACEBOOK: _lectura_ok()},
        ))

    _falsear(monkeypatch, {
        Platform.YOUTUBE: _lectura_ok(),
        Platform.FACEBOOK: LecturaRed(estado=EstadoLectura.ERROR, error="se cayó hoy"),
        Platform.INSTAGRAM: _lectura_ok(),
        Platform.TIKTOK: fallo,
    })

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )

    assert resultado.exit_code == 0, resultado.output
    assert "AVISO: tiktok lleva 3 días seguidos" in resultado.output
    assert "socialctl auth tiktok" in resultado.output
    assert "AVISO: facebook" not in resultado.output, (
        "facebook solo falló hoy: avisar de eso vaciaría de significado el aviso"
    )


def test_un_piezas_yml_ilegible_lo_dice_en_espanol_y_no_lo_pisa(raiz, monkeypatch):
    """Ni una traza, ni perder lo editorial: qué se guardó y qué no."""
    import yaml as _yaml  # noqa: F401  (el fichero roto se escribe a mano)

    from socialctl.brands import cargar_brand
    from socialctl.metricas.piezas import ruta_piezas

    _falsear(monkeypatch, {
        Platform.YOUTUBE: _lectura_ok(),
        Platform.FACEBOOK: _lectura_ok(),
        Platform.INSTAGRAM: _lectura_ok(),
        Platform.TIKTOK: _lectura_ok(),
    })
    marca = cargar_brand(raiz, "Histopast")
    ruta = ruta_piezas(marca)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    roto = "- red: youtube\n  id: vid1\n  pilar: 'sin cerrar\n"
    ruta.write_text(roto, encoding="utf-8")

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )

    assert resultado.exit_code == 1
    assert "piezas.yml" in resultado.output
    assert "Sí se guardó" in resultado.output and "No se guardó" in resultado.output
    assert "resumen.md" in resultado.output
    assert "Traceback" not in resultado.output
    # Lo editorial sigue intacto: es lo único que no se puede regenerar.
    assert ruta.read_text(encoding="utf-8") == roto
    # Y el snapshot del día, que sí se había escrito antes del fallo, está.
    assert list((marca.raiz / "metricas").glob("*.json"))


def test_un_snapshot_corrupto_no_bloquea_la_marca_para_siempre(raiz, monkeypatch):
    """Un snapshot se regenera: se ignora con aviso, no tumba la ejecución."""
    from socialctl.brands import cargar_brand

    _falsear(monkeypatch, {
        Platform.YOUTUBE: _lectura_ok(),
        Platform.FACEBOOK: _lectura_ok(),
        Platform.INSTAGRAM: _lectura_ok(),
        Platform.TIKTOK: _lectura_ok(),
    })
    marca = cargar_brand(raiz, "Histopast")
    carpeta = marca.raiz / "metricas"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "2020-01-01.json").write_text('{"fecha": "2020-01', encoding="utf-8")

    with pytest.warns(UserWarning, match=r"snapshot .* no se puede leer") as warnings:
        resultado = runner.invoke(
            app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
        )

    assert resultado.exit_code == 0, resultado.output
    assert (carpeta / "resumen.md").is_file()
    assert len(warnings) == 1


def test_stats_limpia_snapshots_de_mas_de_un_ano_y_lo_dice(raiz, monkeypatch):
    """Retención (spec §3): corre con cada `stats` y dice qué ha borrado.

    `piezas.yml` y `resumen.md` -escritos en esta misma ejecución- tienen
    que sobrevivir aunque sean "viejos" a efectos de la política: se
    regeneran/editan, nunca se borran por edad.
    """
    from datetime import timedelta

    from socialctl.brands import cargar_brand
    from socialctl.metricas.almacen import RETENCION_SNAPSHOTS_DIAS, guardar_snapshot, ruta_snapshot
    from socialctl.metricas.modelos import Snapshot

    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})
    marca = cargar_brand(raiz, "Histopast")

    fecha_vieja = date.today() - timedelta(days=RETENCION_SNAPSHOTS_DIAS + 1)
    fecha_reciente = date.today() - timedelta(days=10)
    for fecha in (fecha_vieja, fecha_reciente):
        guardar_snapshot(marca, Snapshot(fecha=fecha, marca="Histopast", redes={}))

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )

    assert resultado.exit_code == 0, resultado.output
    assert "Limpieza: borrados 1 snapshot" in resultado.output
    assert fecha_vieja.isoformat() in resultado.output
    assert not ruta_snapshot(marca, fecha_vieja).is_file()
    # Dentro del año, se conserva; y lo escrito hoy, también.
    assert ruta_snapshot(marca, fecha_reciente).is_file()
    assert ruta_snapshot(marca, date.today()).is_file()
    carpeta = marca.raiz / "metricas"
    assert (carpeta / "piezas.yml").is_file()
    assert (carpeta / "resumen.md").is_file()


def test_stats_avisa_si_un_borrado_falla_y_no_aborta(raiz, monkeypatch):
    """Un fallo al borrar un snapshot viejo (permisos, por ejemplo) se avisa
    y se sigue: nunca aborta `stats`, que ya ha gastado cuota de API."""
    from datetime import timedelta

    import socialctl.metricas.almacen as almacen
    from socialctl.brands import cargar_brand
    from socialctl.metricas.almacen import RETENCION_SNAPSHOTS_DIAS, guardar_snapshot
    from socialctl.metricas.modelos import Snapshot

    _falsear(monkeypatch, {p: _lectura_ok() for p in Platform})
    marca = cargar_brand(raiz, "Histopast")

    fecha_vieja = date.today() - timedelta(days=RETENCION_SNAPSHOTS_DIAS + 1)
    guardar_snapshot(marca, Snapshot(fecha=fecha_vieja, marca="Histopast", redes={}))

    def unlink_que_falla(self, *args, **kwargs):
        raise PermissionError("sin permiso para borrar")

    monkeypatch.setattr(almacen.Path, "unlink", unlink_que_falla)

    resultado = runner.invoke(
        app, ["stats", "--brand", "Histopast", "--root", str(raiz)]
    )

    assert resultado.exit_code == 0, resultado.output
    assert "AVISO: no se pudo borrar" in resultado.output
    assert fecha_vieja.isoformat() in resultado.output
    # El snapshot de HOY, que ya ha costado cuota de API, se guarda igual.
    assert (marca.raiz / "metricas" / f"{date.today().isoformat()}.json").is_file()
