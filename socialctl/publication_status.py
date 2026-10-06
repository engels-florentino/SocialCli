"""Human-readable publication status without inferring remote visibility."""

from __future__ import annotations

from datetime import datetime

from socialctl.models import Platform, PostResult, PostStatus


def _aware_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.utcoffset() is not None else None


def describe_result(result: PostResult) -> str:
    """Describe what is known, keeping requested and observed state separate."""
    if result.status is not PostStatus.PUBLICADO:
        return {PostStatus.PENDIENTE_CONFIRMACION: "pending_confirmation", PostStatus.OMITIDO: "skipped", PostStatus.ERROR: "error"}.get(result.status, "published")
    if result.platform is not Platform.YOUTUBE:
        return "publication confirmed"
    observed = _aware_datetime(result.visibility_observed_at)
    if observed is None or not result.observed_privacy:
        return "upload confirmed; visibility unverified"
    if result.observed_privacy == "private":
        planned = _aware_datetime(result.observed_publish_at)
        if planned is not None and planned > observed:
            return "upload confirmed; scheduled, still private"
        return "upload confirmed; private"
    if result.observed_privacy == "unlisted":
        return "upload confirmed; unlisted"
    if result.observed_privacy == "public":
        if result.observed_processing_status == "succeeded":
            return "upload confirmed; public"
        return (
            'upload confirmed; public privacy observed, processing unverified'
        )
    return "upload confirmed; visibility unverified"


def describe_result_with_observation(result: PostResult) -> str:
    """Add the observation instant so persisted reports cannot look current."""
    description = describe_result(result)
    has_observation = any(
        (
            result.observed_privacy,
            result.observed_publish_at,
            result.observed_processing_status,
        )
    )
    if (
        result.platform is Platform.YOUTUBE
        and has_observation
        and _aware_datetime(result.visibility_observed_at) is not None
    ):
        return f"{description} (observed {result.visibility_observed_at})"
    return description


def describe_native_job(job, payload: dict) -> str:
    """Keep native lifecycle and dependent follow-up work distinct."""
    stamp = job.observation_watermark.isoformat() if job.observation_watermark else 'never'
    result = f'{job.state}; requested {job.publish_at.isoformat()}; observed {stamp}'
    if payload.get('first_comment'):
        result += '; first comment: pending manual/server follow-up after public verification'
    if payload.get('options', {}).get('related_video_id'):
        result += '; related-video link: pending separate verification'
    return result
