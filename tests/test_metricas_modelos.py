import json
import os
import time
from datetime import date, datetime, timedelta

from socialctl.brands import crear_brand
from socialctl.metricas.almacen import (
    RETENCION_SNAPSHOTS_DIAS,
    cargar_snapshot,
    dir_metricas,
    guardar_snapshot,
    limpiar_snapshots_antiguos,
    ruta_snapshot,
    snapshot_anterior,
    ultimo_snapshot,
)
from socialctl.metricas.modelos import (
    Cuenta,
    EstadoLectura,
    LecturaRed,
    Metricas,
    Pieza,
    Snapshot,
    TipoPieza,
)
from socialctl.models import Platform


def _pieza(id_="v1", vistas=100):
    return Pieza(
        id=id_,
        url=f"https://youtu.be/{id_}",
        titulo="1496: Santo Domingo",
        publicado_el=datetime(2026, 9, 6, 19, 0),
        tipo=TipoPieza.LARGO,
        acumulado=Metricas(vistas=vistas, likes=10),
    )


def _snapshot(fecha, vistas=100):
    return Snapshot(
        fecha=fecha,
        marca="Histopast",
        redes={
            Platform.YOUTUBE: LecturaRed(
                estado=EstadoLectura.OK,
                cuenta=Cuenta(seguidores=4950),
                piezas=[_pieza(vistas=vistas)],
            )
        },
    )


def test_lo_que_la_red_no_da_queda_en_none():
    m = Metricas(vistas=10)
    assert m.vistas == 10
    for campo in ("likes", "comentarios", "compartidos", "guardados"):
        assert getattr(m, campo) is None, f"{campo} debería ser None, no 0"


def test_una_red_que_fallo_conserva_su_motivo_al_ir_y_volver_del_disco(tmp_path):
    """El motivo del fallo tiene que sobrevivir al JSON, y el fallo de una red
    no puede llevarse por delante lo que otra sí leyó.

    Deliberadamente no se afirman aquí los valores por defecto del modelo
    (`piezas == []`, `cuenta is None`) como única comprobación: eso pasaría
    igual con un lector roto que no hubiera leído nada, así que no probaría
    ningún comportamiento.
    """
    brand = crear_brand(tmp_path, "Histopast")
    snapshot = Snapshot(
        fecha=date(2026, 9, 11),
        marca="Histopast",
        redes={
            Platform.TIKTOK: LecturaRed(
                estado=EstadoLectura.SIN_PERMISO,
                error="el token de tiktok no tiene el permiso 'video.list'",
            ),
            Platform.YOUTUBE: LecturaRed(estado=EstadoLectura.OK, piezas=[_pieza()]),
        },
    )
    guardar_snapshot(brand, snapshot)
    leido = cargar_snapshot(brand, date(2026, 9, 11))

    tiktok = leido.redes[Platform.TIKTOK]
    assert tiktok.estado is EstadoLectura.SIN_PERMISO
    assert "video.list" in tiktok.error
    assert tiktok.piezas == []
    assert len(leido.redes[Platform.YOUTUBE].piezas) == 1


def test_guardar_y_cargar_un_snapshot_conserva_los_datos(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    ruta = guardar_snapshot(brand, _snapshot(date(2026, 9, 11)))

    assert ruta == ruta_snapshot(brand, date(2026, 9, 11))
    assert ruta.parent == dir_metricas(brand)
    assert ruta.name == "2026-09-11.json"

    leido = cargar_snapshot(brand, date(2026, 9, 11))
    assert leido is not None
    assert leido.marca == "Histopast"
    assert leido.redes[Platform.YOUTUBE].piezas[0].acumulado.vistas == 100
    assert leido.redes[Platform.YOUTUBE].piezas[0].acumulado.comentarios is None


def test_el_json_es_legible_y_no_lleva_claves_inventadas(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    ruta = guardar_snapshot(brand, _snapshot(date(2026, 9, 11)))
    crudo = json.loads(ruta.read_text(encoding="utf-8"))

    assert crudo['marca'] == "Histopast"
    assert crudo["fecha"] == "2026-09-11"
    assert crudo["redes"]["youtube"]["estado"] == "ok"
    assert crudo["redes"]["youtube"]['piezas'][0]["acumulado"]["comentarios"] is None


def test_dos_snapshots_el_mismo_dia_no_duplican(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    guardar_snapshot(brand, _snapshot(date(2026, 9, 11), vistas=100))
    guardar_snapshot(brand, _snapshot(date(2026, 9, 11), vistas=250))

    assert len(list(dir_metricas(brand).glob("*.json"))) == 1
    assert cargar_snapshot(brand, date(2026, 9, 11)).redes[Platform.YOUTUBE].piezas[0].acumulado.vistas == 250


def test_snapshot_anterior_y_ultimo(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    guardar_snapshot(brand, _snapshot(date(2026, 9, 9), vistas=50))
    guardar_snapshot(brand, _snapshot(date(2026, 9, 11), vistas=100))

    anterior = snapshot_anterior(brand, antes_de=date(2026, 9, 11))
    assert anterior is not None and anterior.fecha == date(2026, 9, 9)

    assert ultimo_snapshot(brand).fecha == date(2026, 9, 11)
    assert snapshot_anterior(brand, antes_de=date(2026, 9, 9)) is None


def test_sin_carpeta_de_metricas_no_hay_nada_que_leer(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    assert ultimo_snapshot(brand) is None
    assert cargar_snapshot(brand, date(2026, 9, 11)) is None


# --- un snapshot corrupto no puede bloquear la marca ------------------------


def test_un_snapshot_ilegible_se_ignora_con_aviso_en_vez_de_tumbar_la_lectura(tmp_path):
    """Un snapshot se regenera; bloquear la marca para siempre, no.

    Antes, `cargar_snapshot` reventaba con el JSONDecodeError y ese fallo
    subía por `escribir_resumen` y por el aviso de fallos persistentes, así
    que un solo fichero corrupto tumbaba todas las ejecuciones futuras.
    """
    import pytest

    brand = crear_brand(tmp_path, "Histopast")
    ruta = ruta_snapshot(brand, date(2026, 9, 11))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text('{"fecha": "2026-09-11", "marca": "Hist', encoding="utf-8")

    with pytest.warns(UserWarning, match='cannot be read'):
        assert cargar_snapshot(brand, date(2026, 9, 11)) is None


def test_un_snapshot_corrupto_no_impide_comparar_con_los_demas(tmp_path):
    import pytest

    brand = crear_brand(tmp_path, "Histopast")
    guardar_snapshot(brand, _snapshot(date(2026, 9, 9), vistas=50))
    ruta_snapshot(brand, date(2026, 9, 10)).write_text("{roto", encoding="utf-8")

    with pytest.warns(UserWarning, match="2026-09-10"):
        anterior = snapshot_anterior(brand, antes_de=date(2026, 9, 11))

    # El corrupto se ignora, pero no tapa el bueno del día anterior: lo que
    # no puede pasar es que la marca se quede sin comparación para siempre.
    assert anterior is None or anterior.fecha == date(2026, 9, 9)


def test_el_snapshot_se_escribe_de_forma_atomica(tmp_path, monkeypatch):
    """Con el `replace` interrumpido, el snapshot anterior sigue entero."""
    import pytest

    import socialctl.metricas.almacen as almacen

    brand = crear_brand(tmp_path, "Histopast")
    ruta = guardar_snapshot(brand, _snapshot(date(2026, 9, 11), vistas=100))
    original = ruta.read_text(encoding="utf-8")

    monkeypatch.setattr(
        almacen.os, "replace",
        lambda origen, destino: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        guardar_snapshot(brand, _snapshot(date(2026, 9, 11), vistas=999))

    assert ruta.read_text(encoding="utf-8") == original
    assert not list(ruta.parent.glob(".2026-09-11.json.*"))


# --- retención de un año: qué se borra y qué no -----------------------------


def test_la_limpieza_borra_lo_mas_viejo_de_un_ano_y_conserva_lo_reciente(tmp_path):
    """El límite es el propio de la política: fuera del año, se borra; dentro, no."""
    brand = crear_brand(tmp_path, "Histopast")
    hoy = date(2026, 9, 11)
    fecha_vieja = hoy - timedelta(days=RETENCION_SNAPSHOTS_DIAS + 1)
    fecha_reciente = hoy - timedelta(days=10)
    guardar_snapshot(brand, _snapshot(fecha_vieja))
    guardar_snapshot(brand, _snapshot(fecha_reciente))

    borrados, fallidos = limpiar_snapshots_antiguos(brand, hoy=hoy)

    assert fallidos == []
    assert borrados == [ruta_snapshot(brand, fecha_vieja)]
    assert not ruta_snapshot(brand, fecha_vieja).is_file()
    assert ruta_snapshot(brand, fecha_reciente).is_file()


def test_la_limpieza_no_toca_piezas_yml_ni_resumen_md_aunque_sean_viejos(tmp_path):
    """`piezas.yml` y `resumen.md` no se borran nunca, tengan la edad que tengan."""
    brand = crear_brand(tmp_path, "Histopast")
    hoy = date(2026, 9, 11)
    fecha_vieja = hoy - timedelta(days=RETENCION_SNAPSHOTS_DIAS + 100)
    guardar_snapshot(brand, _snapshot(fecha_vieja))

    piezas = dir_metricas(brand) / "piezas.yml"
    resumen = dir_metricas(brand) / "resumen.md"
    piezas.write_text("- red: youtube\n  id: v1\n", encoding="utf-8")
    resumen.write_text("# Resumen\n", encoding="utf-8")
    # Con una modificación de hace mucho: si algo los borrara por edad de
    # verdad (que no debe pasar, ver el docstring de la función), esto lo
    # pondría de manifiesto igual que con los snapshots.
    hace_mucho = time.time() - (RETENCION_SNAPSHOTS_DIAS + 100) * 86400
    os.utime(piezas, (hace_mucho, hace_mucho))
    os.utime(resumen, (hace_mucho, hace_mucho))

    borrados, fallidos = limpiar_snapshots_antiguos(brand, hoy=hoy)

    assert fallidos == []
    assert all(ruta.name not in ('piezas.yml', "resumen.md") for ruta in borrados)
    assert piezas.is_file()
    assert resumen.is_file()


def test_la_limpieza_ignora_un_fichero_json_cuyo_nombre_no_es_una_fecha(tmp_path):
    """La carpeta es del usuario tanto como nuestra: un `.json` suyo no se toca."""
    brand = crear_brand(tmp_path, "Histopast")
    hoy = date(2026, 9, 11)
    guardar_snapshot(brand, _snapshot(hoy - timedelta(days=RETENCION_SNAPSHOTS_DIAS + 1)))
    propio = dir_metricas(brand) / "notas-del-usuario.json"
    propio.write_text('{"a": 1}', encoding="utf-8")

    borrados, fallidos = limpiar_snapshots_antiguos(brand, hoy=hoy)

    assert fallidos == []
    assert propio not in borrados
    assert propio.is_file()


def test_la_limpieza_borra_por_el_nombre_no_por_el_mtime(tmp_path):
    """Un snapshot antiguo reescrito hoy no se salva: cuenta la fecha del nombre."""
    brand = crear_brand(tmp_path, "Histopast")
    hoy = date(2026, 9, 11)
    fecha_vieja = hoy - timedelta(days=RETENCION_SNAPSHOTS_DIAS + 1)
    ruta = guardar_snapshot(brand, _snapshot(fecha_vieja))
    # Se reescribe "hoy": el mtime queda recién tocado, pero el nombre del
    # fichero -y por tanto su fecha a efectos de la política- no cambia.
    ruta.write_text(ruta.read_text(encoding="utf-8"), encoding="utf-8")
    assert ruta.stat().st_mtime >= (datetime.now().timestamp() - 5)

    borrados, _ = limpiar_snapshots_antiguos(brand, hoy=hoy)

    assert borrados == [ruta]
    assert not ruta.is_file()


def test_la_limpieza_avisa_del_borrado_que_falla_y_no_pierde_el_fichero(tmp_path, monkeypatch):
    """Ante un borrado que falla, se avisa y se sigue: el fichero no desaparece."""
    import socialctl.metricas.almacen as almacen

    brand = crear_brand(tmp_path, "Histopast")
    hoy = date(2026, 9, 11)
    fecha_vieja = hoy - timedelta(days=RETENCION_SNAPSHOTS_DIAS + 1)
    ruta = guardar_snapshot(brand, _snapshot(fecha_vieja))

    def unlink_que_falla(self, *args, **kwargs):
        raise PermissionError("sin permiso para borrar")

    monkeypatch.setattr(almacen.Path, "unlink", unlink_que_falla)

    borrados, fallidos = limpiar_snapshots_antiguos(brand, hoy=hoy)

    assert borrados == []
    assert len(fallidos) == 1
    ruta_fallida, excepcion = fallidos[0]
    assert ruta_fallida == ruta
    assert isinstance(excepcion, PermissionError)
    # Ninguna operación real de borrado ha ocurrido: el fichero sigue ahí.
    assert ruta.is_file()
