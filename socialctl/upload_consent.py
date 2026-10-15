"""Owner certification included in, and bound to, every YouTube upload preview."""

YOUTUBE_GUIDELINES = 'https://www.youtube.com/howyoutubeworks/policies/community-guidelines/'
YOUTUBE_CERTIFICATION = (
    'By approving this upload, you certify that this content complies with the '
    'YouTube Community Guidelines: ' + YOUTUBE_GUIDELINES +
    ' Your assistant must show this statement with the complete preview and obtain '
    'your explicit approval; connecting an account does not provide this certification.'
)


def youtube_certification():
    return {'version': '2026-10-15', 'statement': YOUTUBE_CERTIFICATION}
