"""Interfaz común de los adaptadores de red."""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from socialctl.brands import Brand
from socialctl.formatter import validar_privacidad, validar_texto
from socialctl.media import validar_media
from socialctl.models import Platform, PlatformPost, PostResult, ValidationError

_SIN_VALOR = object()


def _sigue_siendo_abstracta(cls: type) -> bool:
    """Indica si a `cls` le queda algún método abstracto (propio o heredado) sin implementar.

    No usa `cls.__abstractmethods__` porque, dentro de `__init_subclass__`, ese
    atributo todavía refleja el estado de la clase base (ABCMeta lo recalcula
    para `cls` justo después de invocar `__init_subclass__`, no antes). En su
    lugar se resuelve cada atributo vía la MRO ya establecida y se comprueba
    `__isabstractmethod__` directamente, que sí está disponible en ese momento.
    """
    for nombre in dir(cls):
        try:
            miembro = getattr(cls, nombre)
        except AttributeError:
            continue
        if getattr(miembro, "__isabstractmethod__", False):
            return True
    return False


class Adapter(ABC):
    """Todas las redes se publican a través de esta interfaz.

    Convención de instanciación: quien consume `ADAPTADORES` construye el
    adaptador con `ADAPTADORES[platform]()`, sin argumentos. Por tanto, toda
    subclase concreta debe poder instanciarse sin argumentos: si define un
    `__init__` propio, sus parámetros deben ser opcionales (con valor por
    defecto). Esta interfaz no impone eso en tiempo de definición —solo
    queda documentado aquí—, así que revísalo al escribir cada adaptador.
    """

    platform: Platform

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Exige que toda subclase concreta declare `platform` correctamente.

        La comprobación se hace al **definir** la clase (aquí, en
        `__init_subclass__`), no al instanciarla, para que un adaptador
        incompleto falle de inmediato y con un mensaje claro, en vez de
        fallar mucho después con un `AttributeError` a mitad de un
        `publish()` ya en curso.

        Se considera que la subclase declara `platform` cuando ella misma,
        o alguno de sus ancestros que no sea `Adapter`, le asignó un
        **valor** real (`getattr` lo encuentra recorriendo la MRO); la mera
        anotación de tipo heredada de `Adapter` (`platform: Platform`, sin
        valor) no cuenta, porque no deja nada accesible en tiempo de
        ejecución.

        Limitación conocida: si algún día hiciera falta una subclase
        intermedia abstracta (que no fije `platform` porque delega esa
        decisión en una subclase posterior), esta comprobación se salta
        únicamente mientras esa subclase intermedia siga teniendo algún
        método abstracto sin implementar (hoy, mientras no implemente
        `publish`). Si una subclase intermedia implementara ya todos los
        métodos abstractos pero aun así quisiera posponer la declaración de
        `platform`, esta comprobación la rechazaría; no es un caso que
        exista hoy.
        """
        super().__init_subclass__(**kwargs)

        if _sigue_siendo_abstracta(cls):
            return

        valor = getattr(cls, "platform", _SIN_VALOR)

        if valor is _SIN_VALOR:
            raise TypeError(
                f"{cls.__name__} no declara 'platform': toda subclase concreta de "
                "Adapter debe fijarlo a un valor de Platform. La anotación de tipo "
                "heredada de Adapter no basta, hace falta asignarle un valor "
                "(por ejemplo, 'platform = Platform.TIKTOK')."
            )

        if not isinstance(valor, Platform):
            raise TypeError(
                f"{cls.__name__} declara 'platform = {valor!r}', que no es un "
                "miembro de Platform. Usa uno de los valores de Platform "
                "(por ejemplo, Platform.TIKTOK)."
            )

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        """Comprueba que el post cumple los límites de la red. No hace red.

        Esta implementación base ya cubre los límites genéricos de
        plataforma: texto (`validar_texto`), media (`validar_media`) y
        privacidad (`validar_privacidad`), según `PLATFORM_SPECS`. Un
        adaptador concreto solo debería sobrescribir este método si su red
        exige alguna comprobación adicional propia que no encaje en
        `PLATFORM_SPECS`; en ese caso debe **añadir** sus propios errores a
        los de la base (por ejemplo, llamando a `super().validate(post,
        brand)` y concatenando los suyos), nunca reemplazarlos.
        """
        return validar_texto(post) + validar_media(post) + validar_privacidad(post)

    @abstractmethod
    def publish(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        """Publica el post. Solo se llama si validate() no devolvió errores."""


ADAPTADORES: dict[Platform, type[Adapter]] = {}
"""Registro de clases de adaptador por plataforma.

Guarda **clases**, no instancias: quien lo consuma instancia con
`ADAPTADORES[platform]()`, sin argumentos. Por eso todo adaptador que se
registre aquí debe poder construirse sin argumentos —cualquier parámetro
propio (por ejemplo, tiempos de espera o número de reintentos) necesita un
valor por defecto—; nada en este módulo lo hace cumplir automáticamente.
"""
