from types import SimpleNamespace

from socialctl.capabilities import delivery_route


def test_api_route_requires_verified_exact_account():
    job = SimpleNamespace(account_id="page-1")
    evidence = {"route": "api", "verified_account": "page-1",
                "verified_format": "reel", "grant_verified": True,
                "remote_verified": True}
    assert delivery_route(job, evidence, "reel") == "api"
    assert delivery_route(job, evidence, "story") == "blocked"
    assert delivery_route(job, evidence | {"verified_account": "page-2"}, "reel") == "blocked"
    assert delivery_route(job, evidence | {"grant_verified": False}, "reel") == "blocked"
    assert delivery_route(job, evidence | {"remote_verified": False}, "reel") == "blocked"
    assert delivery_route(job, {"route": "api"}, "reel") == "blocked"


def test_auth_block_precedes_ui_and_unknown_routes_fail_closed():
    job = SimpleNamespace(account_id="page-1")
    assert delivery_route(job, {"route": "ui_required"}, "reel") == "ui"
    assert delivery_route(job, {"route": "ui_required", "account_status": "blocked_auth"}, "reel") == "blocked"
    assert delivery_route(job, {"route": "unsupported"}, "reel") == "blocked"
