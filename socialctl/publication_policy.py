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
            error("content_origin", "a YouTube source is present without a content classification")
        elif meta and video and require_origin:
            error("content_origin", "declare standalone or youtube_long before publishing")
        return errors
    if post.content_origin == "standalone":
        if post.source_video_id is not None:
            error("source_video_id", "standalone does not support a source long-form video")
        return errors
    if post.source_video_id is None:
        error("source_video_id", "the exact source long-form video ID is missing")
    elif meta and post.first_comment not in {
            expected_long_comment(post.source_video_id),
            f"Full video: https://www.youtube.com/watch?v={post.source_video_id}"}:
        error("first_comment", "the exact comment linking to the source long-form video is missing")
    return errors
