"""Pure origin policy: declarations are never inferred from titles or other posts."""

from socialctl.models import MediaKind, Platform, PlatformPost, ValidationError


def expected_long_comment(video_id: str) -> str:
    return f"🎥 Video completo en https://www.youtube.com/watch?v={video_id}"


def validate_derivation(post: PlatformPost, *, require_origin: bool) -> list[ValidationError]:
    errors = []

    def error(field: str, message: str) -> None:
        errors.append(ValidationError(platform=post.platform, campo=field, motivo=message))

    meta = post.platform in {Platform.FACEBOOK, Platform.INSTAGRAM}
    video = any(asset.kind is MediaKind.VIDEO for asset in post.media)
    if post.content_origin is None:
        if post.source_video_id is not None:
            error("content_origin", "hay un origen de YouTube sin clasificar el contenido")
        elif meta and video and require_origin:
            error("content_origin", "declara standalone o youtube_long antes de publicar")
        return errors
    if post.content_origin == "standalone":
        if post.source_video_id is not None:
            error("source_video_id", "standalone no admite un largo de origen")
        return errors
    if post.source_video_id is None:
        error("source_video_id", "falta el ID exacto del largo de origen")
    elif meta and post.first_comment != expected_long_comment(post.source_video_id):
        error("first_comment", "falta el comentario exacto hacia el largo de origen")
    return errors
