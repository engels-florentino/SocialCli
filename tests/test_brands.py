import shutil
import stat

import pytest
import yaml

from socialctl.brands import (
    AccountsInvalido,
    BrandNoEncontrada,
    BrandYaExiste,
    NombreDeMarcaInvalido,
    cargar_brand,
    crear_brand,
)
from socialctl.models import Platform


def test_crear_brand_genera_la_estructura(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    assert (brand.raiz / "brand.md").exists()
    assert (brand.raiz / "accounts.yml").exists()
    assert (brand.raiz / "media").is_dir()
    assert (brand.raiz / "posts").is_dir()
    assert (brand.raiz / ".secrets").is_dir()


def test_crear_brand_dos_veces_falla(tmp_path):
    crear_brand(tmp_path, "Histopast")
    with pytest.raises(BrandYaExiste):
        crear_brand(tmp_path, "Histopast")


def test_cargar_brand_inexistente_falla(tmp_path):
    with pytest.raises(BrandNoEncontrada):
        cargar_brand(tmp_path, "NoExiste")


def test_cargar_brand_lee_voz_y_cuentas(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "brand.md").write_text("Voz documental en espanol.", encoding="utf-8")
    (brand.raiz / "accounts.yml").write_text(
        yaml.safe_dump({"tiktok": {"mode": "inbox", "open_id": "abc"}}),
        encoding="utf-8",
    )

    cargada = cargar_brand(tmp_path, "Histopast")
    assert "documental" in cargada.voz
    assert cargada.cuentas["tiktok"]["mode"] == "inbox"


def test_los_secretos_se_guardan_con_permisos_600(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    brand.guardar_secreto(Platform.YOUTUBE, {"refresh_token": "secreto"})

    fichero = brand.raiz / ".secrets" / "youtube.json"
    modo = stat.S_IMODE(fichero.stat().st_mode)
    assert modo == 0o600
    assert brand.leer_secreto(Platform.YOUTUBE)["refresh_token"] == "secreto"


def test_leer_secreto_inexistente_devuelve_vacio(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    assert brand.leer_secreto(Platform.FACEBOOK) == {}


def test_dos_marcas_no_comparten_secretos(tmp_path):
    histopast = crear_brand(tmp_path, "Histopast")
    otra = crear_brand(tmp_path, "OtraMarca")
    histopast.guardar_secreto(Platform.YOUTUBE, {"refresh_token": "de-histopast"})

    assert otra.leer_secreto(Platform.YOUTUBE) == {}


# --- Hallazgo 1: el nombre de marca no se valida y permite salir de la raiz ---

NOMBRES_INVALIDOS = [
    pytest.param("/etc", id="ruta-absoluta"),
    pytest.param("../Escapada", id="escape-con-puntos"),
    pytest.param("a/b", id="separador"),
    pytest.param("", id="vacio"),
    pytest.param("   ", id="solo-espacios"),
]


@pytest.mark.parametrize("nombre", NOMBRES_INVALIDOS)
def test_cargar_brand_rechaza_nombres_invalidos(tmp_path, nombre):
    with pytest.raises(NombreDeMarcaInvalido):
        cargar_brand(tmp_path, nombre)


@pytest.mark.parametrize("nombre", NOMBRES_INVALIDOS)
def test_crear_brand_rechaza_nombres_invalidos(tmp_path, nombre):
    with pytest.raises(NombreDeMarcaInvalido):
        crear_brand(tmp_path, nombre)

    # No debe haberse creado ni tocado nada fuera (ni dentro) de tmp_path.
    assert list(tmp_path.iterdir()) == []


def test_mensaje_de_nombre_invalido_indica_el_nombre_rechazado(tmp_path):
    with pytest.raises(NombreDeMarcaInvalido, match=r"/etc"):
        cargar_brand(tmp_path, "/etc")


def test_cargar_brand_rechaza_enlace_simbolico_que_escapa(tmp_path, tmp_path_factory):
    # La comprobacion por forma no basta: un nombre "Enlace" sin separadores
    # ni ".." puede, aun asi, apuntar fuera de la raiz social via symlink.
    externo = tmp_path_factory.mktemp("fuera")
    (tmp_path / "Enlace").symlink_to(externo, target_is_directory=True)

    with pytest.raises(NombreDeMarcaInvalido):
        cargar_brand(tmp_path, "Enlace")


def test_crear_brand_rechaza_enlace_simbolico_que_escapa(tmp_path, tmp_path_factory):
    externo = tmp_path_factory.mktemp("fuera")
    (tmp_path / "Enlace").symlink_to(externo, target_is_directory=True)

    with pytest.raises(NombreDeMarcaInvalido):
        crear_brand(tmp_path, "Enlace")

    assert list(externo.iterdir()) == []


# --- Hallazgo 2: .secrets/ puede quedar con permisos laxos ---


def test_guardar_secreto_recrea_secrets_con_permisos_700_si_no_existia(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    shutil.rmtree(brand.dir_secretos)

    brand.guardar_secreto(Platform.YOUTUBE, {"refresh_token": "secreto"})

    modo_dir = stat.S_IMODE(brand.dir_secretos.stat().st_mode)
    assert modo_dir == 0o700

    fichero = brand.dir_secretos / "youtube.json"
    modo_fichero = stat.S_IMODE(fichero.stat().st_mode)
    assert modo_fichero == 0o600


# --- Hallazgo 3: ventana TOCTOU al escribir el secreto ---


def test_guardar_secreto_corrige_fichero_preexistente_con_permisos_laxos(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    fichero = brand.dir_secretos / "youtube.json"
    fichero.write_text("{}", encoding="utf-8")
    fichero.chmod(0o644)

    brand.guardar_secreto(Platform.YOUTUBE, {"refresh_token": "secreto"})

    modo = stat.S_IMODE(fichero.stat().st_mode)
    assert modo == 0o600
    assert brand.leer_secreto(Platform.YOUTUBE)["refresh_token"] == "secreto"


# --- Hallazgo 4: accounts.yml mal formado revienta sin contexto ---


def test_cargar_brand_yaml_invalido_da_error_con_contexto(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text("clave: [sin cerrar", encoding="utf-8")

    with pytest.raises(AccountsInvalido, match="accounts.yml"):
        cargar_brand(tmp_path, "Histopast")


def test_cargar_brand_yaml_no_es_mapping_da_error_con_contexto(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text("solo-un-escalar", encoding="utf-8")

    with pytest.raises(AccountsInvalido, match="mapping"):
        cargar_brand(tmp_path, "Histopast")


def test_cargar_brand_accounts_vacio_sigue_funcionando(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text("", encoding="utf-8")

    cargada = cargar_brand(tmp_path, "Histopast")
    assert cargada.cuentas == {}


# --- guardar_open_id_tiktok: sustitución dirigida que conserva comentarios ---


def test_guardar_open_id_tiktok_escribe_el_valor_y_conserva_los_comentarios(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")  # accounts.yml de la plantilla, con comentarios

    anterior = brand.guardar_open_id_tiktok("oid-nuevo-123")

    assert anterior is None  # la plantilla trae open_id: "" -> no había ninguno

    texto = (brand.raiz / "accounts.yml").read_text(encoding="utf-8")
    assert 'open_id: "oid-nuevo-123"' in texto

    # Los comentarios explicativos del fichero (de otras secciones y de la
    # propia sección tiktok) deben seguir intactos: una reescritura con
    # yaml.safe_dump los habría borrado todos.
    assert 'Account identifiers for this brand' in texto
    assert 'Requires a professional account' in texto
    assert "auth tiktok" in texto  # comentario propio de open_id en la plantilla
    assert 'inbox  -> upload to inbox' in texto

    # El resto de la sección tiktok no debe haberse tocado.
    assert "mode: inbox" in texto
    assert "auditada: false" in texto

    # Y el valor debe quedar disponible recargando la marca (accounts.yml
    # sigue siendo un YAML válido tras la sustitución).
    recargada = cargar_brand(tmp_path, "Histopast")
    assert recargada.cuentas["tiktok"]["open_id"] == "oid-nuevo-123"


def test_guardar_open_id_tiktok_devuelve_el_anterior_si_cambia(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    brand.guardar_open_id_tiktok("oid-viejo")

    brand = cargar_brand(tmp_path, "Histopast")  # recargar para que .cuentas refleje el cambio
    anterior = brand.guardar_open_id_tiktok("oid-nuevo")

    assert anterior == "oid-viejo"
    texto = (brand.raiz / "accounts.yml").read_text(encoding="utf-8")
    assert 'open_id: "oid-nuevo"' in texto


def test_guardar_open_id_tiktok_no_avisa_si_el_valor_no_cambia(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    brand.guardar_open_id_tiktok("oid-igual")

    brand = cargar_brand(tmp_path, "Histopast")
    anterior = brand.guardar_open_id_tiktok("oid-igual")

    assert anterior is None


def test_guardar_open_id_tiktok_sin_seccion_tiktok_lanza_accounts_invalido(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    (brand.raiz / "accounts.yml").write_text(
        "youtube:\n  channel_id: \"\"\n", encoding="utf-8"
    )
    brand = cargar_brand(tmp_path, "Histopast")

    with pytest.raises(AccountsInvalido):
        brand.guardar_open_id_tiktok("oid-cualquiera")

    # No debe haber escrito nada a medias.
    assert (brand.raiz / "accounts.yml").read_text(encoding="utf-8") == (
        "youtube:\n  channel_id: \"\"\n"
    )


def test_crear_brand_incluye_la_plantilla_de_estrategia(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")

    estrategia = brand.raiz / "estrategia.md"
    assert estrategia.exists()

    texto = estrategia.read_text(encoding="utf-8")
    for seccion in (
        "## Goal",
        "## Cadence by platform",
        "## Content pillars",
        "## Upcoming plan",
        "## Lessons",
        "## Settled decisions",
    ):
        assert seccion in texto, f'section is missing {seccion}'


def test_la_plantilla_de_estrategia_no_menciona_ninguna_marca_concreta(tmp_path):
    brand = crear_brand(tmp_path, "OtraMarca")
    texto = brand.raiz / "estrategia.md"
    assert "Histopast" not in texto.read_text(encoding="utf-8")
