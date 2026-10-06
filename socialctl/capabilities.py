"""Packaged declarative coverage; never a generic API dispatcher."""
from __future__ import annotations

from importlib.resources import files

import yaml

from socialctl.identity import ReadError


def delivery_route(job, capability: dict, content_format: str | None = None) -> str:
    """Select an approved job route only from account-specific evidence.

    The registry describes API methods, but cannot itself prove a brand's
    effective write permission. An unmatched or unknown account fails closed.
    """
    if capability.get("account_status") == "blocked_auth":
        return "blocked"
    route = capability.get("route")
    if (route == "api" and isinstance(content_format, str) and content_format
            and capability.get("verified_account") == job.account_id
            and capability.get("verified_format") == content_format
            and capability.get("grant_verified") is True
            and capability.get("remote_verified") is True):
        return "api"
    if route == "ui_required":
        return "ui"
    return "blocked"


def load_capabilities():
    registry = yaml.safe_load(files("socialctl").joinpath("capabilities.yml").read_text(encoding="utf-8"))
    if registry.get("version") != 1:
        raise ReadError("capability_version_unsupported")
    return registry


def capability_report(platform=None):
    registry = load_capabilities()
    valid = {row["platform"] for row in registry["capabilities"]}
    if platform is not None and platform not in valid:
        raise ReadError("unsupported_platform")
    rows = [row for row in registry["capabilities"] if platform is None or row["platform"] == platform]
    remaining = [
        {"id": row["id"], "task": item["task"], "scope": item["scope"], "acceptance": item["acceptance"]}
        for row in rows for item in row.get("remaining", [])
    ]
    return {"version": registry["version"], "observed_at": registry["observed_at"],
            "sources": registry["sources"], "capabilities": rows,
            "classification": {"records": len(rows),
                "by_availability": {key: sum(r["availability"] == key for r in rows)
                                    for key in ("api", "ui_only", "unavailable", "unknown")}},
            "implementation": {"methods_with_scoped_handlers": sum(bool(r["implemented_scopes"]) for r in rows),
                               "remaining_scopes": len(remaining),
                               "remaining": remaining,
                               "full_api_coverage_percent": None,
                               "meaning": "Evidence applies only to each named field scope, not the entire method or account grants."}}
