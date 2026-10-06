from datetime import date

from socialctl.metricas.reach import attach_reach, reach_by_video_day, reach_for_video
from socialctl.metricas.resumen import render_resumen
from tests.test_metricas_resumen import _snapshot
from socialctl.models import Platform


def report(report_id, created, impressions, ctr, *, day='20260920', video_id='abcdefghijk'):
    return {
        'binding': {
            'source': 'youtubeReporting', 'report_type_id': 'channel_reach_basic_a1',
            'report': {'id': report_id, 'createTime': created},
        },
        'rows': [{
            'dimensions': {'date': day, 'video_id': video_id},
            'metrics': {
                'video_thumbnail_impressions': {'value': impressions},
                'video_thumbnail_impressions_ctr': {'value': ctr},
            },
        }],
    }


def test_newer_reach_report_replaces_same_video_day_without_double_count():
    observations = [
        report('old', '2026-09-21T00:00:00Z', 100, 2.0),
        report('new', '2026-09-22T00:00:00Z', 120, 3.0),
        report('next', '2026-09-22T01:00:00Z', 80, 6.0, day='20260921'),
    ]
    rows = reach_by_video_day(observations)
    value = reach_for_video(rows, 'abcdefghijk', date(2026, 9, 21), days=28)
    assert value['impressions'] == 200
    assert value['ctr'] == 4.2
    assert value['through'] == '2026-09-21'
    assert value['report_ids'] == ['new', 'next']


def test_missing_reach_is_unknown_not_zero():
    assert reach_for_video({}, 'abcdefghijk', date(2026, 9, 21)) is None
    text = render_resumen(_snapshot(date(2026, 9, 21), 254), anterior=None, piezas=[])
    assert 'no existen en la API' not in text
    assert 'sin informe de alcance' in text.lower()


def test_summary_shows_imported_reach_with_provenance():
    snapshot = _snapshot(date(2026, 9, 21), 254)
    snapshot.redes[Platform.YOUTUBE].piezas[0].especificas['thumbnail_reach_28d'] = {
        'impressions': 120, 'ctr': 3.0, 'from': '2026-08-25',
        'through': '2026-09-20', 'report_ids': ['reach-1'],
        'source': 'youtubeReporting',
    }
    text = render_resumen(snapshot, anterior=None, piezas=[])
    assert '120' in text and '3.0%' in text
    assert 'YouTube Reporting' in text
    assert 'reach-1' in text


def test_attach_reach_only_to_matching_video_and_28_day_window():
    snapshot = _snapshot(date(2026, 9, 21), 254)
    piece = snapshot.redes[Platform.YOUTUBE].piezas[0]
    piece.id = 'abcdefghijk'
    attach_reach([piece], [report('reach-1', '2026-09-22T00:00:00Z', 120, 3.0)],
                 date(2026, 9, 21))
    assert piece.especificas['thumbnail_reach_28d']['impressions'] == 120
    assert piece.especificas['thumbnail_reach_28d']['source'] == 'youtubeReporting'
    other = _snapshot(date(2026, 9, 21), 254).redes[Platform.YOUTUBE].piezas[0]
    attach_reach([other], [report('reach-1', '2026-09-22T00:00:00Z', 120, 3.0)],
                 date(2026, 9, 21))
    assert 'thumbnail_reach_28d' not in other.especificas
