"""Verified identity and server-owned access decisions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str
