"""Narrow contract for approved API delivery; provider clients live elsewhere."""

from typing import Callable, Protocol

from .models import NativeJob


class DeliveryUncertain(RuntimeError):
    """A remote write may have been accepted; reconcile before another send."""


class DeliveryAdapter(Protocol):
    def deliver(self, job: NativeJob, payload: dict,
                checkpoint: Callable[[dict], None]) -> str:
        """Return the remote ID after sending the approved content."""
