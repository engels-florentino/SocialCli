import httpx
import pytest

from tests.test_meta_management import service


def metric(values):
    return {'id': '123_789/insights/post_video_views/day',
            'name': 'post_video_views', 'period': 'day', 'values': values}


def test_insights_follow_fixed_endpoint_time_pages_and_merge_values(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    calls = []

    def handler(request):
        if not request.url.path.endswith('/insights'):
            return graph(request)
        calls.append(request)
        assert 'access_token' not in request.url.params
        if len(calls) == 1:
            return httpx.Response(200, json={'data': [metric([{'value': 3, 'end_time': '2026-10-01'}])],
                'paging': {'next': 'https://graph.facebook.com/v26.0/123_789/insights?since=2&until=3&access_token=SECRET'}})
        assert request.url.params['since'] == '2'
        assert request.url.params['until'] == '3'
        assert request.url.params['metric'] == 'post_video_views'
        return httpx.Response(200, json={'data': [metric([{'value': 4, 'end_time': '2026-10-02'}])]})

    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = client.insights('123_789', ['post_video_views'], period='day', since=1, until=2)
    assert result['complete'] is True
    assert len(result['data']) == 1
    assert [v['value'] for v in result['data'][0]['values']] == [3, 4]
    assert len(calls) == 2


@pytest.mark.parametrize('next_url', [
    'https://evil.example/v26.0/123_789/insights?since=2&until=3',
    'https://graph.facebook.com/v26.0/999/insights?since=2&until=3',
    'https://graph.facebook.com/v26.0/123_789/insights?since=2&until=3&metric=other',
    'https://graph.facebook.com/v26.0/123_789/insights?since=2&since=4&until=5',
    'https://graph.facebook.com/v26.0/123_789/insights?since=1&until=2',
])
def test_untrusted_or_stuck_time_page_is_partial_without_another_request(tmp_path, next_url):
    _, _, graph, client, _ = service(tmp_path)
    calls = []
    def handler(request):
        if not request.url.path.endswith('/insights'):
            return graph(request)
        calls.append(request)
        return httpx.Response(200, json={'data': [metric([{'value': 3}])], 'paging': {'next': next_url}})
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = client.insights('123_789', ['post_video_views'], period='day', since=1, until=2)
    assert result['complete'] is False
    assert result['data'][0]['values'] == [{'value': 3}]
    assert len(calls) == 1


@pytest.mark.parametrize('first,second,complete,values', [
    ([{'value': 3}], [{'value': 3}], True, [{'value': 3}]),
    ([{'value': 3}], [{'value': 4}], False, [{'value': 3}]),
    ([{'value': 3, 'end_time': '2026-10-01'}],
     [{'value': 4, 'end_time': '2026-10-01'}], False,
     [{'value': 3, 'end_time': '2026-10-01'}]),
])
def test_repeated_metric_values_are_neither_summed_nor_overwritten(tmp_path, first, second, complete, values):
    _, _, graph, client, _ = service(tmp_path)
    calls = []
    def handler(request):
        if not request.url.path.endswith('/insights'):
            return graph(request)
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={'data': [metric(first)], 'paging': {
                'next': 'https://graph.facebook.com/v26.0/123_789/insights?since=2&until=3'}})
        return httpx.Response(200, json={'data': [metric(second)]})
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = client.insights('123_789', ['post_video_views'], period='day', since=1, until=2)
    assert result['complete'] is complete
    assert result['data'][0]['values'] == values
    assert len(calls) == 2

@pytest.mark.parametrize('second_value,complete', [(3, True), (4, False)])
def test_no_id_metric_pages_still_deduplicate_and_detect_conflicts(tmp_path, second_value, complete):
    _, _, graph, client, _ = service(tmp_path)
    calls = []
    def handler(request):
        if not request.url.path.endswith('/insights'):
            return graph(request)
        calls.append(request)
        row = metric([{'value': 3 if len(calls) == 1 else second_value, 'end_time': '2026-10-01'}])
        del row['id']
        payload = {'data': [row]}
        if len(calls) == 1:
            payload['paging'] = {'next': 'https://graph.facebook.com/v26.0/123_789/insights?since=2&until=3'}
        return httpx.Response(200, json=payload)
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = client.insights('123_789', ['post_video_views'], period='day', since=1, until=2)
    assert result['complete'] is complete
    assert len(result['data']) == 1
    assert result['data'][0]['values'] == [{'value': 3, 'end_time': '2026-10-01'}]

@pytest.mark.parametrize('missing_first', [True, False])
def test_optional_metric_id_can_be_omitted_on_either_time_page(tmp_path, missing_first):
    _, _, graph, client, _ = service(tmp_path)
    calls = []
    def handler(request):
        if not request.url.path.endswith('/insights'):
            return graph(request)
        calls.append(request)
        row = metric([{'value': 3}])
        if (len(calls) == 1) == missing_first:
            del row['id']
        payload = {'data': [row]}
        if len(calls) == 1:
            payload['paging'] = {'next': 'https://graph.facebook.com/v26.0/123_789/insights?since=2&until=3'}
        return httpx.Response(200, json=payload)
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = client.insights('123_789', ['post_video_views'], period='day', since=1, until=2)
    assert result['complete'] is True
    assert len(result['data']) == 1
    assert result['data'][0]['values'] == [{'value': 3}]
