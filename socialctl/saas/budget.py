"""Cooperative maintenance deadlines; the process manager is the hard guard."""
from contextlib import contextmanager
import math
import time


class BudgetExpired(Exception):
    pass


class WorkBudget:
    def __init__(self, seconds, *, clock=time.monotonic):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Invalid maintenance budget.')
        self.clock = clock
        self.deadline = clock() + seconds

    def remaining(self):
        return max(0.0, self.deadline - self.clock())

    def expired(self):
        return self.remaining() <= 0

    def timeout(self, maximum):
        remaining = self.remaining()
        if remaining <= 0:
            raise BudgetExpired()
        return min(maximum, remaining)

    def portion(self, fraction):
        return WorkBudget(self.remaining() * fraction, clock=self.clock)


@contextmanager
def using_budget(budget, *adapters):
    """Only maintenance-owned synchronous adapters are temporarily constrained."""
    previous = []
    try:
        if budget is not None:
            for adapter in adapters:
                if adapter is not None:
                    previous.append((adapter, getattr(adapter, 'budget', None)))
                    adapter.budget = budget
        yield
    finally:
        for adapter, original in reversed(previous):
            adapter.budget = original


@contextmanager
def hard_deadline(seconds=230):
    """One-shot worker only: end the actual container process, not docker exec.

    Crash-safe durable leases recover on the next run. An SMTP DATA operation
    interrupted here stays uncertain and must never be replayed automatically.
    """
    import os
    import threading
    timer=threading.Timer(seconds,lambda:os._exit(124))
    timer.daemon=True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join()
