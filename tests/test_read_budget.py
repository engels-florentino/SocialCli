"""Whole-operation deadlines include streamed bodies and retry waits."""
import asyncio
import importlib.util
import time

import httpx
import pytest


def budget_module():
    assert importlib.util.find_spec('socialctl.read_budget') is not None, 'whole-operation read budget is missing'
    from socialctl import read_budget
    return read_budget


@pytest.mark.parametrize('seconds', [0, -1, float('nan'), float('inf')])
def test_budget_rejects_invalid_duration(seconds):
    module = budget_module()
    with pytest.raises(ValueError):
        module.ReadBudget(seconds)


def test_budget_accounts_for_elapsed_time_and_expires():
    module = budget_module()
    now = [10.0]
    budget = module.ReadBudget(2, clock=lambda:now[0])
    now[0] = 11.5
    assert budget.remaining() == .5
    now[0] = 12
    with pytest.raises(module.ReadDeadlineExceeded):
        budget.check()


def test_deadline_interrupts_slow_response_headers():
    module = budget_module()
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json={})
    start = time.monotonic()
    with httpx.Client(transport=module.BudgetTransport(httpx.MockTransport(handler), module.ReadBudget(.05))) as client:
        with pytest.raises(module.ReadDeadlineExceeded):
            client.get('https://social.example/read')
    assert time.monotonic() - start < .5


def test_deadline_interrupts_slow_stream_body():
    module = budget_module()
    closed = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'first'
            await asyncio.sleep(10)
            yield b'last'
        async def aclose(self):
            closed.append(True)
    async def handler(request):
        return httpx.Response(200, stream=Stream())
    start = time.monotonic()
    with httpx.Client(transport=module.BudgetTransport(httpx.MockTransport(handler), module.ReadBudget(.05))) as client:
        with pytest.raises(module.ReadDeadlineExceeded):
            client.get('https://social.example/read')
    assert time.monotonic() - start < .5
    assert closed


def test_retry_after_cannot_exceed_total_budget():
    module = budget_module()
    calls = []
    async def handler(request):
        calls.append(request.method)
        return httpx.Response(429, headers={'Retry-After':'60'}, json={'error':'rate limited'})
    start = time.monotonic()
    with httpx.Client(transport=module.BudgetTransport(httpx.MockTransport(handler), module.ReadBudget(.05))) as client:
        with pytest.raises(module.ReadDeadlineExceeded):
            client.get('https://social.example/read')
    assert calls == ['GET']
    assert time.monotonic() - start < .5


def test_write_is_not_retried_and_get_has_bounded_retries():
    module = budget_module()
    calls = []
    async def handler(request):
        calls.append(request.method)
        return httpx.Response(503, json={})
    with httpx.Client(transport=module.BudgetTransport(httpx.MockTransport(handler), module.ReadBudget(5))) as client:
        assert client.post('https://social.example/action', json={}).status_code == 503
        assert calls == ['POST']
        assert client.get('https://social.example/read').status_code == 503
        assert calls == ['POST', 'GET', 'GET', 'GET']


def test_compressed_body_limit_is_applied_after_decoding(monkeypatch):
    import gzip
    module=budget_module()
    monkeypatch.setattr(module,'MAX_READ_BYTES',50)
    payload=gzip.compress(b'x'*100)
    assert len(payload)<50
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield payload
    calls=[]
    async def handler(request):
        calls.append(request.method)
        return httpx.Response(200,headers={'Content-Encoding':'gzip'},stream=Stream())
    with httpx.Client(transport=module.BudgetTransport(httpx.MockTransport(handler),module.ReadBudget(5))) as client:
        with pytest.raises(httpx.ReadError):
            client.get('https://social.example/read')
    assert calls == ['GET']
