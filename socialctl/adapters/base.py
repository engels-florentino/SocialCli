'Common interface for platform adapters.'

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from socialctl.brands import Brand
from socialctl.formatter import validar_privacidad, validar_texto
from socialctl.media import validar_media
from socialctl.models import Platform, PlatformPost, PostResult, ValidationError

_SIN_VALOR = object()


def _sigue_siendo_abstracta(cls: type) -> bool:
    'Return whether cls still has an unimplemented abstract method.'
    for nombre in dir(cls):
        try:
            miembro = getattr(cls, nombre)
        except AttributeError:
            continue
        if getattr(miembro, "__isabstractmethod__", False):
            return True
    return False


class Adapter(ABC):
    'Common validation and publication interface for every supported platform.'

    platform: Platform

    def __init_subclass__(cls, **kwargs: object) -> None:
        'Require every concrete adapter to declare a valid Platform value.'
        super().__init_subclass__(**kwargs)

        if _sigue_siendo_abstracta(cls):
            return

        valor = getattr(cls, "platform", _SIN_VALOR)

        if valor is _SIN_VALOR:
            raise TypeError(
                f"{cls.__name__} does not declare 'platform': every concrete "
                'Adapter must assign a Platform value. The type annotation '
                'inherited from Adapter is insufficient; assign a value '
                "(for example, 'platform = Platform.TIKTOK')."
            )

        if not isinstance(valor, Platform):
            raise TypeError(
                f"{cls.__name__} declares 'platform = {valor!r}', which is not a "
                'Platform member. Use a Platform value '
                '(for example, Platform.TIKTOK).'
            )

    def validate(self, post: PlatformPost, brand: Brand) -> list[ValidationError]:
        'Validate platform content limits without network access.'
        return validar_texto(post) + validar_media(post) + validar_privacidad(post)

    @abstractmethod
    def publish(self, post: PlatformPost, brand: Brand, client: httpx.Client) -> PostResult:
        'Publish the post only after validate() returns no errors.'


ADAPTADORES: dict[Platform, type[Adapter]] = {}
'Registry of adapter classes keyed by Platform. Consumers instantiate ADAPTADORES[platform]() without arguments; concrete adapters must not require constructor parameters.'
