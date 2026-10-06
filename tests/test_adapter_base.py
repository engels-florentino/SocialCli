import httpx
import pytest

from socialctl.adapters.base import Adapter
from socialctl.brands import crear_brand
from socialctl.models import Platform, PlatformPost, PostResult, PostStatus


class AdaptadorDePrueba(Adapter):
    platform = Platform.TIKTOK

    def publish(self, post, brand, client):
        return PostResult(platform=self.platform, status=PostStatus.PUBLICADO, url="https://x")


def test_no_se_puede_instanciar_la_clase_abstracta():
    with pytest.raises(TypeError):
        Adapter()


def test_validate_combina_errores_de_texto_y_de_media(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    # Sin media (TikTok la exige) y con demasiados hashtags.
    post = PlatformPost(
        platform=Platform.TIKTOK,
        body="hola",
        hashtags=[f"h{i}" for i in range(31)],
        media=[],
    )
    errores = AdaptadorDePrueba().validate(post, brand)
    campos = {e.campo for e in errores}
    assert "hashtags" in campos
    assert "media" in campos


def test_validate_sin_problemas_devuelve_lista_vacia(tmp_path):
    brand = crear_brand(tmp_path, "Histopast")
    post = PlatformPost(platform=Platform.FACEBOOK, body="hola", media=[])
    class FB(AdaptadorDePrueba):
        platform = Platform.FACEBOOK
    assert FB().validate(post, brand) == []


def test_validate_rechaza_privacy_en_una_red_que_no_lo_soporta(tmp_path):
    """`validate()` (implementación base, ver `socialctl/adapters/base.py`)
    combina también `validar_privacidad`: TikTok no declara
    `valores_privacidad` en su `PlatformSpec`, así que un `post.privacy`
    puesto ahí debe rechazarse -no ignorarse en silencio, a diferencia de
    `link`-.
    """
    brand = crear_brand(tmp_path, "Histopast")
    post = PlatformPost(
        platform=Platform.TIKTOK, body="hola", media=[], privacy="private"
    )
    errores = AdaptadorDePrueba().validate(post, brand)
    assert any(e.campo == "privacy" for e in errores)


def test_subclase_sin_platform_lanza_typeerror_al_definirse():
    with pytest.raises(TypeError, match="SinPlataforma"):

        class SinPlataforma(Adapter):
            def publish(self, post, brand, client):
                return PostResult(platform=Platform.TIKTOK, status=PostStatus.PUBLICADO, url="https://x")


def test_subclase_con_platform_invalido_lanza_typeerror_al_definirse():
    with pytest.raises(TypeError, match="PlataformaInvalida"):

        class PlataformaInvalida(Adapter):
            platform = "tiktok"  # str, no un miembro de Platform

            def publish(self, post, brand, client):
                return PostResult(platform=Platform.TIKTOK, status=PostStatus.PUBLICADO, url="https://x")


def test_subclase_correcta_se_define_e_instancia_sin_problemas():
    class Correcta(Adapter):
        platform = Platform.YOUTUBE

        def publish(self, post, brand, client):
            return PostResult(platform=self.platform, status=PostStatus.PUBLICADO, url="https://x")

    adaptador = Correcta()
    assert adaptador.platform is Platform.YOUTUBE
