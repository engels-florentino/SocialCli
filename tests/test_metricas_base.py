from datetime import date

import httpx
import pytest

import socialctl.metricas  # noqa: F401  (importarlo registra los lectores reales)
from socialctl.auth import AuthError
from socialctl.brands import crear_brand
from socialctl.metricas.base import LECTORES, Lector, SinPermiso, leer_red
from socialctl.metricas.modelos import Cuenta, EstadoLectura, LecturaRed
from socialctl.models import Platform


@pytest.fixture
def brand(tmp_path):
    return crear_brand(tmp_path, "Histopast")


def _falso(cls, platform):
    """Marca una clase de prueba con su plataforma SIN registrarla en `LECTORES`.

    `Lector.__init_subclass__` registra la subclase en el momento de
    **definirla**. Si una clase falsa de un test declarase `platform` en su
    cuerpo, quedaría registrada antes de que `monkeypatch.setitem` capturase
    el valor anterior, y el teardown restauraría la clase falsa en vez de la
    real: `LECTORES` quedaría contaminado para el resto de la sesión, y que
    hoy no rompa nada depende solo del orden alfabético de los ficheros de
    test. Asignando `platform` **después** de definir la clase, el registro
    no llega a ocurrir y `monkeypatch.setitem` sí puede deshacer lo suyo.
    `leer_red` sigue funcionando igual: solo hace `LECTORES[platform]()`.
    """
    cls.platform = platform
    return cls


def test_los_cuatro_lectores_estan_registrados():
    assert set(LECTORES) == {
        Platform.YOUTUBE,
        Platform.FACEBOOK,
        Platform.INSTAGRAM,
        Platform.TIKTOK,
    }
    for platform, cls in LECTORES.items():
        assert cls.platform is platform
        assert issubclass(cls, Lector)
        cls()  # debe poder instanciarse sin argumentos


def test_sin_permiso_dice_que_scope_falta_y_como_arreglarlo():
    e = SinPermiso(Platform.TIKTOK, "video.list")
    mensaje = str(e)
    assert "video.list" in mensaje
    assert 'socialcli auth tiktok' in mensaje


def test_leer_red_convierte_falta_de_credenciales_en_estado(brand, monkeypatch):
    class LectorQueFalla(Lector):
        def leer(self, brand, client, desde):
            raise AuthError("no hay credenciales de youtube para Histopast")

    _falso(LectorQueFalla, Platform.YOUTUBE)
    monkeypatch.setitem(LECTORES, Platform.YOUTUBE, LectorQueFalla)
    with httpx.Client() as client:
        lectura = leer_red(Platform.YOUTUBE, brand, client, None)

    assert lectura.estado is EstadoLectura.SIN_CREDENCIALES
    assert 'no hay credenciales' in lectura.error
    assert lectura.piezas == []


def test_leer_red_convierte_sin_permiso_en_estado(brand, monkeypatch):
    class LectorSinScope(Lector):
        def leer(self, brand, client, desde):
            raise SinPermiso(Platform.TIKTOK, "video.list")

    _falso(LectorSinScope, Platform.TIKTOK)
    monkeypatch.setitem(LECTORES, Platform.TIKTOK, LectorSinScope)
    with httpx.Client() as client:
        lectura = leer_red(Platform.TIKTOK, brand, client, None)

    assert lectura.estado is EstadoLectura.SIN_PERMISO
    assert "video.list" in lectura.error


def test_leer_red_convierte_cualquier_otro_fallo_en_error(brand, monkeypatch):
    class LectorRoto(Lector):
        def leer(self, brand, client, desde):
            raise httpx.ConnectError("se cayó la red")

    _falso(LectorRoto, Platform.FACEBOOK)
    monkeypatch.setitem(LECTORES, Platform.FACEBOOK, LectorRoto)
    with httpx.Client() as client:
        lectura = leer_red(Platform.FACEBOOK, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert "se cayó la red" in lectura.error


def test_leer_red_devuelve_tal_cual_una_lectura_correcta(brand, monkeypatch):
    class LectorBueno(Lector):
        def leer(self, brand, client, desde):
            return LecturaRed(estado=EstadoLectura.OK, cuenta=Cuenta(seguidores=1234))

    _falso(LectorBueno, Platform.INSTAGRAM)
    monkeypatch.setitem(LECTORES, Platform.INSTAGRAM, LectorBueno)
    with httpx.Client() as client:
        lectura = leer_red(Platform.INSTAGRAM, brand, client, date(2026, 1, 1))

    assert lectura.estado is EstadoLectura.OK
    assert lectura.cuenta.seguidores == 1234


def test_leer_red_no_arrastra_el_token_de_una_httpstatuserror_real(brand, monkeypatch):
    """Regresión del hallazgo de seguridad: la rama genérica de `leer_red`
    interpolaba `str(e)` sin sanear, y el mensaje por defecto de
    `httpx.HTTPStatusError` incluye la URL completa de la petición -con el
    token todavía en su parámetro de consulta, tal y como lo manda Meta.

    Construye la excepción de verdad (petición + respuesta reales de
    `httpx`, no una cadena simulada) para que el test falle si algún día se
    cambia el formato del mensaje de `httpx` y deja de mandar la URL, o si
    `leer_red` deja de sanear: cualquiera de los dos escenarios que este
    test debe detectar.
    """
    token_secreto = "EAAGtokenSECRETOMUYLARGOquenuncadeberiaversecompleto"

    class LectorQueFiltraLaUrl(Lector):
        def leer(self, brand, client, desde):
            peticion = httpx.Request(
                "GET",
                "https://graph.facebook.com/v26.0/123",
                params={"fields": "id,name", "access_token": token_secreto},
            )
            respuesta = httpx.Response(403, request=peticion, text="acceso denegado")
            respuesta.raise_for_status()  # lanza httpx.HTTPStatusError real
            raise AssertionError("raise_for_status debía haber lanzado")

    _falso(LectorQueFiltraLaUrl, Platform.FACEBOOK)
    monkeypatch.setitem(LECTORES, Platform.FACEBOOK, LectorQueFiltraLaUrl)
    with httpx.Client() as client:
        lectura = leer_red(Platform.FACEBOOK, brand, client, None)

    assert lectura.estado is EstadoLectura.ERROR
    assert token_secreto not in lectura.error
    assert '[TOKEN REDACTADO]' in lectura.error


def test_leer_red_sanea_pero_conserva_codigo_de_estado_y_motivo(brand, monkeypatch):
    """Un saneado que borrase toda la información no serviría de nada: el
    código de estado HTTP y el motivo del fallo tienen que seguir legibles
    en `LecturaRed.error` para que el usuario -o el gestor de redes- sepa
    qué pasó, aunque el token ya no esté.
    """

    class LectorQueFiltraLaUrl(Lector):
        def leer(self, brand, client, desde):
            peticion = httpx.Request(
                "GET",
                "https://graph.facebook.com/v26.0/123",
                params={"access_token": "t" * 40},
            )
            respuesta = httpx.Response(403, request=peticion, text="acceso denegado")
            respuesta.raise_for_status()
            raise AssertionError("raise_for_status debía haber lanzado")

    _falso(LectorQueFiltraLaUrl, Platform.INSTAGRAM)
    monkeypatch.setitem(LECTORES, Platform.INSTAGRAM, LectorQueFiltraLaUrl)
    with httpx.Client() as client:
        lectura = leer_red(Platform.INSTAGRAM, brand, client, None)

    assert "403" in lectura.error
    assert "Forbidden" in lectura.error
    assert "graph.facebook.com" in lectura.error


def test_leer_red_sanea_tambien_la_rama_de_sin_permiso(brand, monkeypatch):
    """`SinPermiso` también pasa por el saneado por patrón: un lector futuro
    podría construirlo interpolando una URL propia (p. ej. para explicar en
    qué llamada faltó el scope), y esa rama no debe ser menos segura que la
    genérica.
    """
    token_secreto = "EAAGotroTokenSecretoLargoQueNuncaDeberiaFiltrarse"
    url_con_token = (
        f"https://graph.facebook.com/v26.0/456?access_token={token_secreto}"
    )

    class LectorSinScopeQueFiltraUrl(Lector):
        def leer(self, brand, client, desde):
            raise SinPermiso(Platform.FACEBOOK, f"pages_read_engagement (url: {url_con_token})")

    _falso(LectorSinScopeQueFiltraUrl, Platform.FACEBOOK)
    monkeypatch.setitem(LECTORES, Platform.FACEBOOK, LectorSinScopeQueFiltraUrl)
    with httpx.Client() as client:
        lectura = leer_red(Platform.FACEBOOK, brand, client, None)

    assert lectura.estado is EstadoLectura.SIN_PERMISO
    assert token_secreto not in lectura.error
    assert "pages_read_engagement" in lectura.error
