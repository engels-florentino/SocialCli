"""Bounded read-only identity checks; stored metadata is never permission proof."""
from __future__ import annotations

import re
import time

import httpx

from socialctl.brands import Brand
from socialctl.models import Platform

YOUTUBE = "https://www.googleapis.com/youtube/v3"
GRAPH = "https://graph.facebook.com/v26.0"
SUPPORTED = (Platform.YOUTUBE, Platform.FACEBOOK, Platform.INSTAGRAM)


class ReadError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def remote_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise ReadError("invalid_remote_id")
    return value


def configured_account(brand: Brand, platform: Platform) -> str:
    if platform not in SUPPORTED:
        raise ReadError("unsupported_platform")
    key = {Platform.YOUTUBE: "channel_id", Platform.FACEBOOK: "page_id",
           Platform.INSTAGRAM: "ig_user_id"}[platform]
    account = brand.cuentas.get(platform.value)
    if not isinstance(account, dict) or not account.get(key):
        raise ReadError("configuration_missing")
    return remote_id(account[key])


def credential_metadata(secret):
    scopes = secret.get("granted_scopes", secret.get("scope"))
    if isinstance(scopes, str):
        scopes = scopes.replace(",", " ").split()
    known = isinstance(scopes, list) and all(isinstance(s, str) and s.strip() for s in scopes)
    expiry = secret.get("expira_en")
    expiry_known = type(expiry) in {int, float} and 0 < expiry < float("inf")
    return {
        "granted_scopes": {"state": "known" if known else "unknown",
                           "values": sorted(set(scopes)) if known else None,
                           "source": "stored_token_response" if known else None},
        "expiry": {"state": "known" if expiry_known else "unknown",
                   "unix_seconds": expiry if expiry_known else None,
                   "expired": expiry <= time.time() if expiry_known else None},
        "write_permissions_verified": False,
        "quota_balance": {"state": "unknown", "value": None},
    }


def record_granted_scopes(secret, response):
    """Keep only scopes returned for this token, never requested OAuth scopes."""
    secret.pop("scope", None)
    secret.pop("granted_scopes", None)
    granted = response.get("scope")
    if isinstance(granted, str):
        secret["granted_scopes"] = sorted(set(granted.replace(",", " ").split()))


class IdentityClient:
    """Only fixed provider hosts, GET, bearer headers, and explicit account IDs."""
    def __init__(self, brand: Brand, platform: Platform, client: httpx.Client):
        self.brand, self.platform, self.client = brand, platform, client
        self.account_id = configured_account(brand, platform)
        try:
            secret = brand.leer_secreto(platform)
            self.metadata = credential_metadata(secret)
            if secret.get("auth_mode") == "broker":
                from socialctl.connections.client import broker_access_token
                self._token = broker_access_token(brand, platform, client, refresh=False)
            else:
                self._token = secret.get("access_token")
        except Exception:
            raise ReadError("credentials_invalid") from None
        if not isinstance(self._token, str) or not self._token:
            raise ReadError("credentials_missing")
        if self.metadata["expiry"]["expired"]:
            # Status/sync/doctor never invoke refresh POST or interactive auth.
            raise ReadError("credentials_expired")
        self.identity = None

    def get(self, endpoint: str, params: dict):
        allowed = {"channels", "playlistItems", "videos"} if self.platform is Platform.YOUTUBE else {
            "me", f"{self.account_id}/videos", f"{self.account_id}/media"}
        if endpoint not in allowed:
            raise ReadError("endpoint_not_allowed")
        base = YOUTUBE if self.platform is Platform.YOUTUBE else GRAPH
        try:
            response = self.client.get(f"{base}/{endpoint}", params=params,
                headers={"Authorization": f"Bearer {self._token}"}, timeout=30,
                follow_redirects=False)
        except httpx.TimeoutException:
            raise ReadError("timeout") from None
        except Exception:
            raise ReadError("transport_error") from None
        if response.status_code == 429:
            raise ReadError("rate_limited")
        if response.status_code in {401, 403}:
            # YouTube quota exhaustion commonly uses HTTP403.
            try:
                reasons = [e["reason"] for e in response.json().get("error", {}).get("errors", [])
                           if isinstance(e, dict) and isinstance(e.get("reason"), str)]
            except Exception:
                reasons = []
            if set(reasons) & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}:
                raise ReadError("rate_limited")
            raise ReadError("permission_denied")
        if response.status_code == 404:
            raise ReadError("not_found_or_inaccessible")
        if not response.is_success:
            raise ReadError("provider_error")
        try:
            data = response.json()
            if not isinstance(data, dict) or "error" in data:
                raise ValueError()
            return data
        except Exception:
            raise ReadError("invalid_response") from None

    def verify(self):
        configured_page = None
        if self.platform is Platform.YOUTUBE:
            data = self.get("channels", {"part": "id,contentDetails", "mine": "true", "maxResults": "50"})
            items = data.get("items")
            if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict) or data.get("nextPageToken"):
                raise ReadError("identity_ambiguous")
            row = items[0]
            authenticated = row.get("id")
            details = row.get("contentDetails") or {}
            related = details.get("relatedPlaylists") if isinstance(details, dict) else None
            self.uploads_playlist = related.get("uploads") if isinstance(related, dict) else None
            page = None
        else:
            fields = "id,instagram_business_account" if self.platform is Platform.INSTAGRAM else "id"
            row = self.get("me", {"fields": fields})
            page = row.get("id")
            configured_page = configured_account(self.brand, Platform.FACEBOOK)
            linked = row.get("instagram_business_account") or {}
            authenticated = (linked.get("id") if isinstance(linked, dict) else None) if self.platform is Platform.INSTAGRAM else page
        # Only safe IDs may enter public status; raw provider errors are never printed.
        def public_id(value):
            if value == self._token:
                return None
            try:
                return remote_id(value)
            except ReadError:
                return None
        authenticated, page = public_id(authenticated), public_id(page)
        self.identity = {"configured_account_id": self.account_id, "authenticated_account_id": authenticated,
                         "configured_page_id": configured_page, "authenticated_page_id": page,
                         "verified": authenticated == self.account_id and page == configured_page}
        if not self.identity["verified"]:
            raise ReadError("identity_mismatch")
        return self.identity


def auth_status(brand, platform, client):
    result = {"version": 1, "brand": brand.nombre, "platform": platform.value,
              "failure_class": None, "identity": {"verified": False}, **credential_metadata({})}
    probe = None
    try:
        result.update(credential_metadata(brand.leer_secreto(platform)))
        probe = IdentityClient(brand, platform, client)
        result["identity"] = probe.verify()
    except ReadError as exc:
        result["failure_class"] = exc.code
        if probe is not None and probe.identity is not None:
            result["identity"] = probe.identity
    except Exception:
        result["failure_class"] = "credentials_invalid"
    return result
