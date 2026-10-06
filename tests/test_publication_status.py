import pytest

from socialctl.models import Platform, PostResult, PostStatus
from socialctl.publication_status import describe_result, describe_result_with_observation


def test_subida_privada_no_se_presenta_como_publica():
    result = PostResult(
        platform=Platform.YOUTUBE,
        status=PostStatus.PUBLICADO,
        platform_id="R4cUGeaKrfU",
        requested_privacy="private",
    )

    assert describe_result(result) == 'upload confirmed; visibility unverified'


def test_estado_privado_observado():
    result = PostResult(
        platform=Platform.YOUTUBE,
        status=PostStatus.PUBLICADO,
        observed_privacy="private",
        visibility_observed_at="2026-09-13T20:00:00Z",
    )

    assert describe_result(result) == 'upload confirmed; private'


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            PostResult(platform=Platform.YOUTUBE, status=PostStatus.ERROR),
            "error",
        ),
        (
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PENDIENTE_CONFIRMACION,
            ),
            "pending_confirmation",
        ),
        (
            PostResult(platform=Platform.FACEBOOK, status=PostStatus.PUBLICADO),
            "publication confirmed",
        ),
        (
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PUBLICADO,
                observed_privacy="unlisted",
                visibility_observed_at="2026-09-13T20:00:00+00:00",
            ),
            "upload confirmed; unlisted",
        ),
        (
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PUBLICADO,
                observed_privacy="public",
                observed_processing_status="succeeded",
                visibility_observed_at="2026-09-13T20:00:00Z",
            ),
            "upload confirmed; public",
        ),
        (
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PUBLICADO,
                observed_privacy="public",
                visibility_observed_at="2026-09-13T20:00:00Z",
            ),
            "upload confirmed; public privacy observed, processing unverified",
        ),
        (
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PUBLICADO,
                observed_privacy="private",
                observed_publish_at="2026-09-14T20:00:00Z",
                visibility_observed_at="2026-09-13T20:00:00-04:00",
            ),
            "upload confirmed; scheduled, still private",
        ),
        (
            PostResult(
                platform=Platform.YOUTUBE,
                status=PostStatus.PUBLICADO,
                observed_privacy="private",
                observed_publish_at="2026-09-12T20:00:00Z",
                visibility_observed_at="2026-09-13T20:00:00Z",
            ),
            "upload confirmed; private",
        ),
    ],
)
def test_etiquetas_del_estado(result, expected):
    assert describe_result(result) == expected


def test_observacion_sin_timestamp_no_cuenta_como_verificada():
    result = PostResult(
        platform=Platform.YOUTUBE,
        status=PostStatus.PUBLICADO,
        observed_privacy="public",
        observed_processing_status="succeeded",
    )

    assert describe_result(result) == 'upload confirmed; visibility unverified'


def test_timestamp_sin_zona_no_cuenta_como_observacion():
    result = PostResult(
        platform=Platform.YOUTUBE,
        status=PostStatus.PUBLICADO,
        observed_privacy="private",
        visibility_observed_at="2026-09-13T20:00:00",
    )

    assert describe_result(result) == 'upload confirmed; visibility unverified'
    assert '(observed' not in describe_result_with_observation(result)


def test_informe_identifica_el_momento_historico_de_la_observacion():
    result = PostResult(
        platform=Platform.YOUTUBE,
        status=PostStatus.PUBLICADO,
        observed_privacy="private",
        visibility_observed_at="2026-09-13T20:00:00Z",
    )

    assert describe_result_with_observation(result) == (
        'upload confirmed; private (observed 2026-09-13T20:00:00Z)'
    )


def test_resultado_antiguo_carga_sin_inventar_observacion():
    result = PostResult.model_validate(
        {
            "platform": "youtube",
            "status": "publicado",
            "platform_id": "R4cUGeaKrfU",
        }
    )

    assert result.requested_privacy is None
    assert result.observed_privacy is None
    assert result.visibility_observed_at is None
    assert describe_result(result) == 'upload confirmed; visibility unverified'
