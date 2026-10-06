import pytest

from socialctl.rutas import validar_componente_de_ruta, validar_ruta_relativa


class _MiError(Exception):
    pass


# --- Hallazgo I6/deuda de duplicación (revisión final): `_validar_nombre_de_
# marca` (brands.py), `_validar_slug` (postfile.py y publisher.py) y
# `_validar_nombre_de_media` (postfile.py) compartían el mismo criterio,
# triplicado. Se unificaron aquí; estos tests cubren el helper compartido de
# forma directa, además de los que ya lo ejercitan a través de cada
# llamador (`tests/test_brands.py`, `tests/test_postfile.py`,
# `tests/test_publisher.py`).


def test_valor_valido_devuelve_la_ruta_dentro_de_la_raiz(tmp_path):
    resultado = validar_componente_de_ruta(tmp_path, "algo", _MiError, "nombre")
    assert resultado == tmp_path / "algo"


def test_rechaza_valor_no_textual(tmp_path):
    with pytest.raises(_MiError, match="cadena de texto"):
        validar_componente_de_ruta(tmp_path, 123, _MiError, "nombre")


def test_rechaza_vacio(tmp_path):
    with pytest.raises(_MiError):
        validar_componente_de_ruta(tmp_path, "", _MiError, "nombre")


def test_rechaza_solo_espacios(tmp_path):
    with pytest.raises(_MiError):
        validar_componente_de_ruta(tmp_path, "   ", _MiError, "nombre")


def test_rechaza_ruta_absoluta(tmp_path):
    with pytest.raises(_MiError, match="ruta absoluta"):
        validar_componente_de_ruta(tmp_path, "/etc/passwd", _MiError, "nombre")


def test_rechaza_separador_de_ruta(tmp_path):
    with pytest.raises(_MiError):
        validar_componente_de_ruta(tmp_path, "sub/carpeta", _MiError, "nombre")


def test_rechaza_punto(tmp_path):
    with pytest.raises(_MiError):
        validar_componente_de_ruta(tmp_path, ".", _MiError, "nombre")


def test_rechaza_punto_punto(tmp_path):
    with pytest.raises(_MiError):
        validar_componente_de_ruta(tmp_path, "..", _MiError, "nombre")


def test_rechaza_enlace_simbolico_que_escapa(tmp_path, tmp_path_factory):
    externo = tmp_path_factory.mktemp("fuera")
    (tmp_path / "enlace").symlink_to(externo, target_is_directory=True)

    with pytest.raises(_MiError, match="enlace simbólico"):
        validar_componente_de_ruta(tmp_path, "enlace", _MiError, "nombre")


def test_el_mensaje_incluye_el_sustantivo_y_el_valor_rechazado(tmp_path):
    with pytest.raises(_MiError, match=r"slug inválido: '/etc'"):
        validar_componente_de_ruta(tmp_path, "/etc", _MiError, "slug")


# --- validar_ruta_relativa: como validar_componente_de_ruta, pero admite ---
# --- subcarpetas (varios componentes separados por '/'). La usa el nombre ---
# --- de media de post.yml, que ahora puede ser "short/S2.mp4". ---


def test_ruta_relativa_valor_simple_devuelve_la_ruta_dentro_de_la_raiz(tmp_path):
    resultado = validar_ruta_relativa(tmp_path, "algo.mp4", _MiError, "nombre de media")
    assert resultado == tmp_path / "algo.mp4"


def test_ruta_relativa_con_subcarpeta_devuelve_la_ruta_dentro_de_la_raiz(tmp_path):
    (tmp_path / "short").mkdir()
    resultado = validar_ruta_relativa(tmp_path, "short/S2.mp4", _MiError, "nombre de media")
    assert resultado == tmp_path / "short" / "S2.mp4"


def test_ruta_relativa_con_varias_subcarpetas_anidadas(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    resultado = validar_ruta_relativa(tmp_path, "a/b/c.mp4", _MiError, "nombre de media")
    assert resultado == tmp_path / "a" / "b" / "c.mp4"


def test_ruta_relativa_rechaza_valor_no_textual(tmp_path):
    with pytest.raises(_MiError, match="cadena de texto"):
        validar_ruta_relativa(tmp_path, 123, _MiError, "nombre de media")


def test_ruta_relativa_rechaza_vacio(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, "", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_solo_espacios(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, "   ", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_segmento_solo_espacios(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, "short/   /S2.mp4", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_segmento_vacio_por_doble_barra(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, "short//S2.mp4", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_ruta_absoluta(tmp_path):
    with pytest.raises(_MiError, match="ruta absoluta"):
        validar_ruta_relativa(tmp_path, "/etc/passwd", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_ruta_absoluta_con_subcarpeta(tmp_path):
    with pytest.raises(_MiError, match="ruta absoluta"):
        validar_ruta_relativa(tmp_path, "/short/S2.mp4", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_backslash(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, "short\\S2.mp4", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_byte_nulo(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, "short/S2.mp4\x00.mov", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_punto_suelto(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, ".", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_componente_punto(tmp_path):
    with pytest.raises(_MiError):
        validar_ruta_relativa(tmp_path, "short/./S2.mp4", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_punto_punto_suelto(tmp_path):
    # El `match` fija el mensaje de la rama de comprobación de FORMA
    # ("ningún segmento..."), distinto del que emite la rama de resolución
    # de symlinks ("resuelve fuera de..."). Ver revisión de seguridad,
    # hallazgo M-1: sin este `match`, el test pasaría igual aunque se
    # borrara el chequeo de forma de '..', porque la resolución de
    # symlinks atraparía el caso igualmente (con OTRO mensaje) y nadie se
    # enteraría de que la defensa en profundidad desapareció.
    with pytest.raises(_MiError, match="ningún segmento"):
        validar_ruta_relativa(tmp_path, "..", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_punto_punto_al_principio(tmp_path):
    with pytest.raises(_MiError, match="ningún segmento"):
        validar_ruta_relativa(
            tmp_path, "../.secrets/youtube.json", _MiError, "nombre de media"
        )


def test_ruta_relativa_rechaza_punto_punto_en_medio(tmp_path):
    with pytest.raises(_MiError, match="ningún segmento"):
        validar_ruta_relativa(
            tmp_path, "short/../../.secrets/x", _MiError, "nombre de media"
        )


def test_ruta_relativa_rechaza_punto_punto_repetido(tmp_path):
    with pytest.raises(_MiError, match="ningún segmento"):
        validar_ruta_relativa(tmp_path, "a/../../b", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_punto_punto_aunque_no_escape_de_la_raiz(tmp_path):
    """``a/../b`` no escapa de ``raiz``: ``Path.resolve()`` colapsa el '..' y
    el resultado es ``raiz / 'b'``, que sigue dentro. Si el chequeo de forma
    sobre '..' no existiera, la comprobación de contención tras resolver
    symlinks NO tendría motivo para rechazar este valor -no hay ningún
    enlace simbólico en juego y el resultado final está dentro de ``raiz``-,
    así que ``validar_ruta_relativa`` devolvería una ruta sin más. Es decir:
    a diferencia de los tests de arriba (donde el '..' SÍ escapa y, si se
    quitara el chequeo de forma, la resolución de symlinks lo atraparía
    igual, aunque con otro mensaje), este caso demuestra que el chequeo de
    forma no es redundante en absoluto: es la ÚNICA defensa que rechaza un
    '..' que no llega a escapar. Si se borrara, esta llamada no lanzaría
    ninguna excepción."""
    with pytest.raises(_MiError, match="ningún segmento"):
        validar_ruta_relativa(tmp_path, "a/../b", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_enlace_simbolico_que_escapa(tmp_path, tmp_path_factory):
    externo = tmp_path_factory.mktemp("fuera")
    (tmp_path / "enlace").symlink_to(externo, target_is_directory=True)

    with pytest.raises(_MiError, match="enlace simbólico"):
        validar_ruta_relativa(tmp_path, "enlace/S2.mp4", _MiError, "nombre de media")


def test_ruta_relativa_rechaza_enlace_simbolico_en_subcarpeta_que_escapa(
    tmp_path, tmp_path_factory
):
    """El enlace que escapa puede estar dos niveles por debajo de la raíz, no
    solo en el primer nivel: ``short/enlace`` (no ``enlace`` a secas) debe
    detectarse igual, porque ``.resolve()`` sigue TODOS los enlaces de la
    ruta, no solo el último componente.
    """
    externo = tmp_path_factory.mktemp("fuera")
    (tmp_path / "short").mkdir()
    (tmp_path / "short" / "enlace").symlink_to(externo, target_is_directory=True)

    with pytest.raises(_MiError, match="enlace simbólico"):
        validar_ruta_relativa(
            tmp_path, "short/enlace/S2.mp4", _MiError, "nombre de media"
        )


def test_ruta_relativa_admite_espacios_y_acentos_en_un_segmento(tmp_path):
    (tmp_path / "clips raros").mkdir()
    resultado = validar_ruta_relativa(
        tmp_path, "clips raros/vídeo día.mp4", _MiError, "nombre de media"
    )
    assert resultado == tmp_path / "clips raros" / "vídeo día.mp4"


# --- Caso legítimo prometido por el docstring (revisión de seguridad, ---
# --- hallazgo M-2): un symlink que apunta DENTRO de ``raiz`` no se ---
# --- resuelve en el valor devuelto y debe aceptarse -por ejemplo, un ---
# --- usuario que organiza ``media/short`` como un enlace a otra carpeta o ---
# --- a un disco externo-. Hasta ahora ningún test cubría esto: todos los ---
# --- tests de symlink existentes eran de escape. ---


def test_ruta_relativa_admite_enlace_simbolico_que_apunta_dentro_de_la_raiz(tmp_path):
    destino = tmp_path / "destino_real"
    destino.mkdir()
    fichero = destino / "S2.mp4"
    fichero.write_bytes(b"contenido de prueba")

    (tmp_path / "enlace_legitimo").symlink_to(destino, target_is_directory=True)

    resultado = validar_ruta_relativa(
        tmp_path, "enlace_legitimo/S2.mp4", _MiError, "nombre de media"
    )

    assert resultado.read_bytes() == b"contenido de prueba"


def test_ruta_relativa_admite_enlace_simbolico_en_subcarpeta_que_apunta_dentro_de_la_raiz(
    tmp_path,
):
    """El enlace legítimo puede vivir dentro de una subcarpeta (no solo en
    la raíz de ``media/``) y puede apuntar a un fichero en OTRA subcarpeta,
    no necesariamente a un hermano directo."""
    (tmp_path / "otros").mkdir()
    real = tmp_path / "otros" / "clip.mp4"
    real.write_bytes(b"clip real")

    (tmp_path / "short").mkdir()
    (tmp_path / "short" / "enlace").symlink_to(real)

    resultado = validar_ruta_relativa(
        tmp_path, "short/enlace", _MiError, "nombre de media"
    )

    assert resultado.read_bytes() == b"clip real"
