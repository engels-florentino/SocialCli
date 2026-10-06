from datetime import date, datetime

import pytest
import yaml

from socialctl.brands import crear_brand
from socialctl.metricas.modelos import (
    EstadoLectura,
    LecturaRed,
    Metricas,
    Pieza,
    Snapshot,
    TipoPieza,
)
from socialctl.metricas.piezas import (
    PiezasIlegibles,
    actualizar_piezas,
    cargar_piezas,
    fusionar,
    guardar_piezas,
    ruta_piezas,
)
from socialctl.models import Platform


def _snapshot(id_="vid1", slug=None, titulo="1496: Santo Domingo", duracion=None):
    return Snapshot(
        fecha=date(2026, 9, 11),
        marca="Histopast",
        redes={
            Platform.YOUTUBE: LecturaRed(
                estado=EstadoLectura.OK,
                piezas=[Pieza(
                    id=id_,
                    url=f"https://youtu.be/{id_}",
                    titulo=titulo,
                    publicado_el=datetime(2026, 9, 6, 19, 0),
                    tipo=TipoPieza.LARGO,
                    slug=slug,
                    duracion_seg=duracion,
                    acumulado=Metricas(vistas=254),
                )],
            )
        },
    )


def test_una_pieza_nueva_entra_con_los_editoriales_vacios():
    piezas = fusionar([], _snapshot())

    assert len(piezas) == 1
    entrada = piezas[0]
    assert entrada["red"] == "youtube"
    assert entrada["id"] == "vid1"
    assert entrada["tipo"] == "largo"
    assert entrada["publicado_el"] == "2026-09-06T19:00:00"
    for campo in ("episodio", "pilar", "gancho", "notas"):
        assert entrada[campo] is None, f"{campo} debería nacer vacío"


def test_la_duracion_la_pone_la_red_cuando_la_da():
    """`duracion_seg` es técnico: si la API lo dio, no se le pregunta al usuario."""
    piezas = fusionar([], _snapshot(duracion=980))
    assert piezas[0]['duracion_seg'] == 980


def test_la_duracion_escrita_a_mano_sobrevive_si_la_red_no_la_da():
    """TikTok e Instagram no dan duración: lo que el gestor escribiera ahí no
    puede quedar borrado por una lectura posterior."""
    existentes = [{
        "red": "youtube", "id": "vid1", "slug": None, "tipo": "largo",
        "publicado_el": "2026-09-06T19:00:00", "titulo": "t",
        "duracion_seg": 25,
        "episodio": None, "pilar": None, "gancho": None, "notas": None,
    }]

    piezas = fusionar(existentes, _snapshot(duracion=None))
    assert piezas[0]['duracion_seg'] == 25


def test_no_pisa_los_campos_editoriales_ya_rellenados():
    existentes = [{
        "red": "youtube", "id": "vid1", "slug": None, "tipo": "largo",
        "publicado_el": "2026-09-06T19:00:00", "titulo": "titulo viejo",
        "episodio": "1496-santo-domingo", "pilar": "mapas-fronteras-idiomas",
        "gancho": "consecuencia", "duracion_seg": 980, "notas": "el bueno",
    }]

    piezas = fusionar(existentes, _snapshot(titulo="titulo nuevo"))

    entrada = piezas[0]
    assert entrada["episodio"] == "1496-santo-domingo"
    assert entrada["pilar"] == "mapas-fronteras-idiomas"
    assert entrada["gancho"] == "consecuencia"
    assert entrada["notas"] == "el bueno"
    # `duracion_seg` es técnico, pero esta lectura no trae duración: se
    # conserva la que había.
    assert entrada['duracion_seg'] == 980
    # Lo técnico sí se refresca desde la red.
    assert entrada["titulo"] == "titulo nuevo"


def test_el_slug_se_refresca_cuando_aparece():
    existentes = [{
        "red": "youtube", "id": "vid1", "slug": None, "tipo": "largo",
        "publicado_el": "2026-09-06T19:00:00", "titulo": "t",
        "episodio": None, "pilar": None, "gancho": None,
        "duracion_seg": None, "notas": None,
    }]

    piezas = fusionar(existentes, _snapshot(slug="2026-09-06-santo-domingo"))
    assert piezas[0]["slug"] == "2026-09-06-santo-domingo"


def test_el_slug_escrito_a_mano_sobrevive_si_el_cruce_no_lo_resuelve():
    """Misma regla asimétrica que `duracion_seg`, y por el mismo motivo.

    Una pieza de TikTok cuyo `resultado.json` no guarda la URL no se cruza
    (ver Task 7), así que llega con `slug: None` en cada ejecución. Si eso
    pisara el fichero, `stats` borraría cada día el slug que el gestor
    hubiera escrito a mano.
    """
    existentes = [{
        "red": "youtube", "id": "vid1", "slug": "el-que-escribi-a-mano",
        "tipo": "largo", "publicado_el": "2026-09-06T19:00:00", "titulo": "t",
        "episodio": None, "pilar": None, "gancho": None,
        "duracion_seg": None, "notas": None,
    }]

    piezas = fusionar(existentes, _snapshot(slug=None))
    assert piezas[0]["slug"] == "el-que-escribi-a-mano"


def test_una_pieza_que_deja_de_aparecer_se_conserva():
    """Un vídeo borrado de la red sigue siendo historia editorial."""
    existentes = [{
        "red": "tiktok", "id": "borrado", "slug": None, "tipo": "vertical",
        "publicado_el": "2026-01-01T10:00:00", "titulo": "el que se borró",
        "episodio": "1492", "pilar": None, "gancho": "fecha",
        "duracion_seg": 30, "notas": None,
    }]

    piezas = fusionar(existentes, _snapshot())
    ids = {(p["red"], p["id"]) for p in piezas}
    assert ("tiktok", "borrado") in ids
    assert ("youtube", "vid1") in ids


def test_campos_desconocidos_escritos_a_mano_se_conservan():
    """El fichero es del usuario tanto como nuestro."""
    existentes = [{
        "red": "youtube", "id": "vid1", "slug": None, "tipo": "largo",
        "publicado_el": "2026-09-06T19:00:00", "titulo": "t",
        "episodio": None, "pilar": None, "gancho": None,
        "duracion_seg": None, "notas": None,
        "mi_campo": "algo que escribí yo",
    }]

    piezas = fusionar(existentes, _snapshot())
    assert piezas[0]["mi_campo"] == "algo que escribí yo"


def test_guardar_y_cargar_ida_y_vuelta(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    ruta = guardar_piezas(brand, fusionar([], _snapshot()))

    assert ruta == ruta_piezas(brand)
    assert ruta.name == 'piezas.yml'
    assert cargar_piezas(brand)[0]["id"] == "vid1"

    crudo = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    assert isinstance(crudo, list)


def test_sin_fichero_no_hay_piezas(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    assert cargar_piezas(brand) == []


def test_actualizar_dos_veces_no_duplica(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    actualizar_piezas(brand, _snapshot())
    actualizar_piezas(brand, _snapshot())

    assert len(cargar_piezas(brand)) == 1


def test_una_entrada_que_no_es_un_diccionario_se_ignora_con_aviso():
    """Un renglón suelto en piezas.yml no tumba stats: se descarta y se avisa."""
    with pytest.warns(UserWarning, match='is not a dictionary'):
        piezas = fusionar(["esto no es un dict"], _snapshot())

    assert len(piezas) == 1
    assert piezas[0]["id"] == "vid1"


def test_una_entrada_que_no_es_un_diccionario_no_impide_leer_las_demas():
    """Degradar es mejor que abortar: las entradas buenas se fusionan igual."""
    existentes = [
        "esto no es un dict",
        {
            "red": "tiktok", "id": "borrado", "slug": None, "tipo": "vertical",
            "publicado_el": "2026-01-01T10:00:00", "titulo": "el que se borró",
            "episodio": "1492", "pilar": None, "gancho": "fecha",
            "duracion_seg": 30, "notas": None,
        },
    ]

    with pytest.warns(UserWarning, match='is not a dictionary'):
        piezas = fusionar(existentes, _snapshot())

    ids = {(p["red"], p["id"]) for p in piezas}
    assert ("tiktok", "borrado") in ids
    assert ("youtube", "vid1") in ids


def test_dos_entradas_sin_red_ni_id_no_se_pierden_en_silencio():
    """La colisión de dos entradas sin identidad avisa en vez de desaparecer callada."""
    existentes = [
        {"titulo": "primera", "episodio": None, "pilar": None,
         "gancho": None, "notas": None},
        {"titulo": "segunda", "episodio": None, "pilar": None,
         "gancho": None, "notas": None},
    ]
    snapshot_vacio = Snapshot(fecha=date(2026, 9, 11), marca="Histopast", redes={})

    with pytest.warns(UserWarning, match="share the same identity"):
        piezas = fusionar(existentes, snapshot_vacio)

    # Sin una clave que las distinga no hay forma de conservar las dos, pero
    # el usuario se entera de cuál se descartó y por qué (ver el aviso).
    assert len(piezas) == 1


def test_dos_entradas_con_la_misma_red_e_id_tambien_avisan():
    """La misma regla cubre un duplicado real, no solo el caso sin identidad."""
    existentes = [
        {"red": "youtube", "id": "vid1", "titulo": "version vieja",
         "episodio": None, "pilar": None, "gancho": None, "notas": None},
        {"red": "youtube", "id": "vid1", "titulo": "version duplicada",
         "episodio": None, "pilar": None, "gancho": None, "notas": None},
    ]
    snapshot_vacio = Snapshot(fecha=date(2026, 9, 11), marca="Histopast", redes={})

    with pytest.warns(UserWarning, match="share the same identity"):
        piezas = fusionar(existentes, snapshot_vacio)

    assert len(piezas) == 1


# --- `piezas.yml` es el único fichero que no se regenera --------------------


def test_un_piezas_yml_ilegible_no_se_sobrescribe_y_lo_dice(tmp_path):
    """Devolver `[]` borraría lo editorial: la lectura falla en vez de eso."""
    brand = crear_brand(tmp_path, "Histopast")
    ruta = ruta_piezas(brand)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    roto = "- red: youtube\n  id: vid1\n  notas: 'sin cerrar\n"
    ruta.write_text(roto, encoding="utf-8")

    with pytest.raises(PiezasIlegibles, match='piezas.yml'):
        cargar_piezas(brand)

    # Y lo importante: `actualizar_piezas` tampoco lo toca.
    with pytest.raises(PiezasIlegibles):
        actualizar_piezas(brand, _snapshot())
    assert ruta.read_text(encoding="utf-8") == roto


def test_un_piezas_yml_que_no_es_una_lista_tampoco_se_sobrescribe(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    ruta = ruta_piezas(brand)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("red: youtube\nid: vid1\n", encoding="utf-8")

    with pytest.raises(PiezasIlegibles, match="list"):
        cargar_piezas(brand)


def test_un_piezas_yml_vacio_del_todo_si_es_lista_vacia(tmp_path):
    """No hay nada escrito que perder: el caso de un fichero recién creado."""
    brand = crear_brand(tmp_path, "Histopast")
    ruta = ruta_piezas(brand)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n", encoding="utf-8")

    assert cargar_piezas(brand) == []


def test_guardar_piezas_escribe_por_temporal_y_replace(tmp_path, monkeypatch):
    """Nunca existe un estado en el que `piezas.yml` esté truncado.

    Se simula la interrupción en el peor momento posible: justo antes del
    `os.replace`, con el temporal ya escrito. El fichero bueno tiene que
    seguir entero, que es lo que un `write_text` directo no garantiza.
    """
    brand = crear_brand(tmp_path, "Histopast")
    guardar_piezas(brand, [{"red": "youtube", "id": "vid1", "pilar": "mapas"}])
    ruta = ruta_piezas(brand)
    original = ruta.read_text(encoding="utf-8")

    import socialctl.metricas.almacen as almacen

    def replace_que_muere(origen, destino):
        raise KeyboardInterrupt("el usuario cortó la ejecución")

    monkeypatch.setattr(almacen.os, "replace", replace_que_muere)

    with pytest.raises(KeyboardInterrupt):
        guardar_piezas(brand, [{"red": "youtube", "id": "vid1", "pilar": "otro"}])

    assert ruta.read_text(encoding="utf-8") == original, (
        "el fichero bueno debe sobrevivir intacto a una interrupción"
    )
    assert not list(ruta.parent.glob('.piezas.yml.*')), (
        "el temporal no puede quedarse tirado en la carpeta del usuario"
    )
