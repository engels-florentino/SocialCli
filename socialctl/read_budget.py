"""Cancellable wall-clock budgets for bounded metadata reads, never uploads."""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from contextlib import contextmanager

import httpx

MAX_READ_BYTES = 8 * 1024 * 1024


class ReadDeadlineExceeded(httpx.TimeoutException):
    def __init__(self):
        super().__init__('Read operation exceeded its total time limit; results may be incomplete')


class ReadResponseTooLarge(httpx.ReadError):
    pass


def transport_label(exc):
    """Identify failures using fixed labels, never provider text or URLs."""
    for kind in (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
                 httpx.PoolTimeout, httpx.ProxyError, httpx.ConnectError,
                 httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError):
        if isinstance(exc, kind):
            return kind.__name__
    return 'TransportError'


class ReadBudget:
    def __init__(self, seconds: float, *, clock=time.monotonic):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('--timeout must be a finite positive number of seconds')
        self.clock = clock
        self.deadline = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise ReadDeadlineExceeded()

    @contextmanager
    def share(self, remaining_providers: int):
        """Reserve equal remaining time for sequential providers, within the total."""
        if type(remaining_providers) is not int or remaining_providers < 1:
            raise ValueError('remaining providers must be a positive integer')
        self.check()
        original = self.deadline
        self.deadline = min(original, self.clock() + self.remaining() / remaining_providers)
        try:
            yield self
        finally:
            self.deadline = original


def retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get('retry-after', '')
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    return min(2, .25 * 2 ** attempt)


class BudgetTransport(httpx.BaseTransport):
    """Own one async loop so an entire headers/body read can be cancelled."""
    def __init__(self, inner: httpx.AsyncBaseTransport, budget: ReadBudget,
                 progress: Callable[[], None] | None = None):
        self.inner, self.budget, self.progress = inner, budget, progress
        self.runner = asyncio.Runner()
        self.closed = False

    async def _read(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(3):
            self.budget.check()
            if self.progress:
                self.progress()
            extensions = dict(request.extensions)
            extensions.pop('socialcli_read_budget', None)
            existing = extensions.get('timeout', {})
            extensions['timeout'] = {key:min(value if value is not None else 30, self.budget.remaining())
                                     for key,value in {**dict.fromkeys(('connect','read','write','pool'),30), **existing}.items()}
            async_request = httpx.Request(request.method, request.url, headers=request.headers,
                                          content=request.content, extensions=extensions)
            try:
                async with asyncio.timeout(self.budget.remaining()):
                    response = await self.inner.handle_async_request(async_request)
                    try:
                        raw = bytearray()
                        if response.is_stream_consumed:
                            raw.extend(response.content)
                        else:
                            async for chunk in response.aiter_bytes():
                                self.budget.check()
                                raw.extend(chunk)
                                if len(raw) > MAX_READ_BYTES:
                                    raise ReadResponseTooLarge('Read response exceeds the 8 MiB metadata limit')
                        if len(raw) > MAX_READ_BYTES:
                            raise ReadResponseTooLarge('Read response exceeds the 8 MiB metadata limit')
                        headers = [(k,v) for k,v in response.headers.multi_items() if k.lower() not in {'content-encoding','content-length'}]
                        result = httpx.Response(response.status_code, headers=headers,
                                                content=bytes(raw), extensions=response.extensions)
                    finally:
                        await response.aclose()
            except TimeoutError:
                raise ReadDeadlineExceeded() from None
            except (ReadDeadlineExceeded, ReadResponseTooLarge):
                raise
            except httpx.TransportError:
                if request.method != 'GET' or attempt == 2:
                    raise
                await self._wait(.25 * 2 ** attempt)
                continue
            if request.method != 'GET' or result.status_code not in {408,429,500,502,503,504} or attempt == 2:
                return result
            await self._wait(retry_delay(result, attempt))
        raise AssertionError('unreachable')

    async def _wait(self, delay: float) -> None:
        if delay >= self.budget.remaining():
            raise ReadDeadlineExceeded()
        await asyncio.sleep(delay)
        self.budget.check()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.budget.check()
        request.read()
        original = self.budget
        nested = request.extensions.get('socialcli_read_budget')
        if nested is not None:
            nested.check()
            self.budget = ReadBudget(min(original.remaining(), nested.remaining()))
        try:
            return self.runner.run(self._read(request))
        finally:
            self.budget = original

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self.runner.run(self.inner.aclose())
            finally:
                self.runner.close()


class _EnvironmentTransport(httpx.AsyncBaseTransport):
    """Keep HTTPX's public proxy/NO_PROXY/CA routing inside the cancellable loop."""
    def __init__(self):
        try:
            self.client = httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=True)
        except (ValueError, OSError, ImportError) as exc:
            raise LocalConfigurationError('Check local proxy, CA and HTTP transport configuration; no request was sent.') from None

    async def handle_async_request(self, request):
        return await self.client.send(request, stream=True, follow_redirects=False)

    async def aclose(self):
        await self.client.aclose()


def read_client(budget: ReadBudget, *, progress=None) -> httpx.Client:
    return httpx.Client(transport=BudgetTransport(_EnvironmentTransport(), budget, progress),
                        timeout=30, follow_redirects=False, trust_env=False)


# Context is opt-in for command-level reads; apply/publication paths keep their transports.
from contextvars import ContextVar

active_read_budget: ContextVar[ReadBudget | None] = ContextVar('socialcli_read_budget', default=None)


@contextmanager
def bounded_read(seconds: float):
    budget = ReadBudget(seconds)
    token = active_read_budget.set(budget)
    try:
        yield budget
    finally:
        active_read_budget.reset(token)


class LocalConfigurationError(ValueError):
    """Local HTTP configuration failed before a request could be sent."""


class ReadProgress:
    """At most one stderr update per five seconds, even for fast pagination."""
    def __init__(self, label, *, clock=time.monotonic):
        self.label, self.clock = label, clock
        self.count, self.last = 0, None

    def __call__(self):
        import typer
        self.count += 1
        now = self.clock()
        if self.last is None or now - self.last >= 5:
            typer.echo(f'Reading {self.label}: {self.count} requests...', err=True)
            self.last = now
