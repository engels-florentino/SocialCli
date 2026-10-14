"""Checks for ordinary post.yml routes; no repair or provider access."""
from socialctl.models import MediaKind,Platform

CHROMA_420 = {'yuv420p','yuvj420p','nv12','nv21'} | {
    f'yuv420p{depth}{endian}' for depth in (9,10,12,14,16) for endian in ('le','be')}

SOURCES={
    'instagram':'https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media',
    'tiktok':'https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide',
}


def technical_issues(platform,asset):
    issues=[]
    def issue(code,message):issues.append({'code':code,'action':'supply_compatible_media','retryable':False,'message':message})
    if platform in {Platform.YOUTUBE,Platform.TIKTOK} and asset.kind is MediaKind.IMAGE:
        issue('video_required','This SocialCli route uploads videos only; supply an existing video.')
    if platform is Platform.INSTAGRAM and asset.width and asset.height:
        ratio=(asset.probe or {}).get('display_ratio',asset.width/asset.height)
        low,high=(0.8,1.91) if asset.kind is MediaKind.IMAGE else (0.01,10)
        if ratio is None or not low <= ratio <= high:
            issue('aspect_ratio','Instagram requires an image ratio from 4:5 to 1.91:1, or a Reel ratio from 0.01:1 to 10:1.')
    if asset.probe is None:
        return issues
    meta=asset.probe
    codec=meta['video_codec']
    formats=set((meta['container'] or '').split(','))
    if platform is Platform.INSTAGRAM and asset.kind is MediaKind.IMAGE:
        if codec!='mjpeg' or asset.path.suffix.lower() not in {'.jpg','.jpeg'} or not formats & {'image2','jpeg_pipe'}:
            issue('image_format','Instagram image publishing requires an existing JPEG image.')
    if asset.kind is MediaKind.VIDEO and platform in {Platform.INSTAGRAM,Platform.TIKTOK}:
        allowed={'h264','hevc'} if platform is Platform.INSTAGRAM else {'h264','hevc','vp8','vp9'}
        if codec not in allowed:issue('video_codec','Supply video encoded with '+', '.join(sorted(allowed))+'.')
        # format_name is a demuxer alias list, not proof of a container subtype.
        brand=(meta.get('container_brand') or '').strip()
        iso_brands={'isom','iso2','iso3','iso4','iso5','iso6','iso7','iso8','iso9','mp41','mp42','avc1','M4V','qt'}
        verified_iso=bool(formats & {'mov','mp4'}) and brand in iso_brands and asset.path.suffix.lower() in {'.mov','.mp4'}
        if not verified_iso:
            issue('video_container','Container identity is unsupported or unverified; supply an existing verified MOV/MP4. Ambiguous Matroska/WebM identification is not accepted by this local check.')
        if meta['fps'] is None or not 23 <= meta['fps'] <= 60:
            issue('frame_rate','This publishing route requires a verified frame rate between 23 and 60 FPS.')
        if platform is Platform.TIKTOK and any(not 360 <= v <= 4096 for v in (asset.width,asset.height)):
            issue('dimensions','TikTok requires both video dimensions between 360 and 4096 pixels.')
        if platform is Platform.INSTAGRAM:
            if asset.width>1920:issue('dimensions','Instagram Reels allow at most 1920 horizontal pixels.')
            if meta['pixel_format'] not in CHROMA_420:
                issue('pixel_format','This route requires verified 4:2:0 chroma sampling.')
            for audio in meta['audio']:
                try:valid=audio['codec']=='aac' and 0<int(audio['sample_rate'])<=48000 and audio['channels'] in {1,2}
                except (TypeError,ValueError):valid=False
                if not valid:issue('audio_format','Instagram Reels require AAC audio, at most 48 kHz and one or two channels.')
    return issues
