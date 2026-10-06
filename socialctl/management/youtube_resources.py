"""Fixed-host supplied thumbnail/caption transport. No video download or conversion."""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import quote

import httpx

from socialctl.auth import obtener_token
from socialctl.management.changes import ChangeError
from socialctl.management.youtube import YouTubeManagementClient, YouTubeManagementError
from socialctl.models import Platform

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
MAX_CAPTION = 100 * 1024 * 1024


class ResourceError(ChangeError):
    def __init__(self, message, *, http_status=None, provider_reason=None):
        super().__init__(message)
        self.http_status = http_status
        self.provider_reason = provider_reason


class ResourceRejected(ResourceError):
    pass


class ResourceConflict(ResourceRejected):
    pass


class ResourceUncertain(ResourceError):
    pass


# Only fixed public machine labels may enter diagnostics/journals. Provider
# messages, locations, request data and unknown reason strings are never echoed.
SAFE_REASONS = frozenset({"manualSortRequired", "channelSectionNotFound",
    "channelSectionForbidden", "channelNotFound", "idInvalid", "invalidCriteria",
    "forbidden", "insufficientPermissions", "quotaExceeded", "notFound",
    "playlistNotFound", "playlistItemNotFound", "videoNotFound", "invalidValue"})


def provider_reason(response, token):
    try:
        data = bytearray()
        for chunk in response.iter_bytes(chunk_size=16385):
            if len(data) + len(chunk) > 16384:
                return None
            data.extend(chunk)
        error = json.loads(data).get("error")
        if not isinstance(error, dict) or error.get("code", response.status_code) != response.status_code:
            return None
        errors = error.get("errors")
        if not isinstance(errors, list) or len(errors) != 1 or not isinstance(errors[0], dict):
            return None
        reason = errors[0].get("reason")
        return reason if isinstance(reason, str) and reason in SAFE_REASONS and reason != token else None
    except Exception:
        return None


def resource_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_=-]{1,512}", value):
        raise ResourceError("Invalid YouTube ID; URLs and paths are not accepted")
    return value


class _FixedOAuth:
    def __init__(self, client):
        self.client = client

    def post(self, url, **kwargs):
        if url != "https://oauth2.googleapis.com/token":
            raise ResourceError("OAuth endpoint not allowed")
        return self.client.post(url, follow_redirects=False, **kwargs)


class YouTubeResourcesClient(YouTubeManagementClient):
    """Reuse exact authenticated-channel/video ownership, with redirect-safe reads."""

    def __init__(self, brand, client):
        super().__init__(brand, client)
        self._access_token = None

    @property
    def configured_channel_id(self):
        try:
            return resource_id(super().configured_channel_id)
        except YouTubeManagementError:
            raise ResourceError("brand configuration lacks a valid YouTube channel") from None

    def _authenticated_channel(self, token):
        payload = self._json(self._request("GET", f"{API}/channels", params={"part": "id", "mine": "true"}))
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ResourceError("ambiguous identity: exactly one authenticated channel was not returned")
        # channels.list is paginated. One item on this page is not evidence of
        # one authenticated channel unless totalResults confirms it. The API
        # reports resultsPerPage as capacity (observed: 5 for one result), not
        # necessarily the number of returned items. Its documented maximum is 50.
        # Token presence (including malformed/empty values) cannot be accepted
        # as a complete first and only page; no pagination is needed to reject it.
        page = payload.get("pageInfo")
        if (any(key in payload for key in ("nextPageToken", "prevPageToken"))
            or not isinstance(page, dict)
            or type(page.get("totalResults")) is not int or page["totalResults"] != 1
            or type(page.get("resultsPerPage")) is not int
            or not len(items) <= page["resultsPerPage"] <= 50):
            raise ResourceError("incomplete or ambiguous channel identity: requires totalResults=1 and valid resultsPerPage capacity (1–50)")
        return resource_id(items[0].get("id"))

    def _token(self):
        # Pin the credential for this client so ownership and effects use one token.
        if self._access_token is None:
            try:
                self._access_token = obtener_token(self.brand, Platform.YOUTUBE, _FixedOAuth(self.client))
            except Exception:
                raise ResourceError("YouTube credentials unavailable; refresh unconfirmed") from None
        return self._access_token

    def inspect(self, video_id):
        try:
            return super().inspect(resource_id(video_id))
        except YouTubeManagementError as exc:
            raise ResourceError(str(exc)) from None

    def _request(self, method, url, *, headers=None, max_response=4 * 1024 * 1024, **kwargs):
        # All call sites use literal known endpoints; reject future accidental expansion.
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or parsed.host != "www.googleapis.com" or parsed.port not in {None, 443} or parsed.userinfo or parsed.query or not self._endpoint_allowed(method, parsed.path):
            raise ResourceError("YouTube host or endpoint not allowed")
        writing = method != "GET"
        try:
            token = self._token()
        except YouTubeManagementError:
            raise ResourceError("YouTube credentials unavailable") from None
        try:
            with self.client.stream(method, url,
                headers={**(headers or {}), "Authorization": f"Bearer {token}", "Accept-Encoding": "identity"},
                follow_redirects=False, **kwargs) as response:
                if response.status_code == 412 and writing:
                    raise ResourceConflict("conflict: YouTube rejected If-Match (412)")
                if not response.is_success:
                    error = ((ResourceUncertain if response.is_redirect or response.status_code >= 500
                              else ResourceRejected) if writing else ResourceError)
                    reason = provider_reason(response, token)
                    detail = f"; reason={reason}" if reason else ""
                    raise error(f"YouTube HTTP {response.status_code}{detail}; check specific permissions, operation unconfirmed",
                        http_status=response.status_code, provider_reason=reason)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > max_response:
                        raise (ResourceUncertain if writing else ResourceError)("YouTube response exceeds byte limit")
                    data.extend(chunk)
                response_headers = {k: v for k, v in response.headers.items()
                                    if k.lower() not in {"content-encoding", "content-length"}}
                return httpx.Response(response.status_code, headers=response_headers,
                                      content=bytes(data), request=response.request)
        except ResourceError:
            raise
        except Exception:
            raise (ResourceUncertain if writing else ResourceError)(
                "YouTube: transport interrupted; outcome unconfirmed") from None

    def _endpoint_allowed(self, method, path):
        paths = {"/youtube/v3/channels", "/youtube/v3/videos", "/youtube/v3/captions",
                 "/upload/youtube/v3/captions", "/upload/youtube/v3/thumbnails/set"}
        download = method == "GET" and re.fullmatch(r"/youtube/v3/captions/[A-Za-z0-9_=%-]+", path)
        return path in paths or bool(download)

    def _json(self, response, *, writing=False):
        try:
            payload = response.json()
            if not isinstance(payload, dict) or "error" in payload:
                raise ValueError()
            return payload
        except Exception:
            raise (ResourceUncertain if writing else ResourceError)("YouTube: unverifiable response") from None

    def _get_json(self, url, token, *, params, operation):
        return self._json(self._request("GET", url, params=params))

    def list_captions(self, video_id):
        self.inspect(video_id)
        payload = self._json(self._request("GET", f"{API}/captions",
            params={"part": "snippet", "videoId": resource_id(video_id)}))
        # captions.list documents no pagination parameters or continuation schema.
        if payload.get("nextPageToken") or payload.get("next") or payload.get("pageInfo"):
            raise ResourceError("incomplete list: pagination is undocumented for captions.list")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ResourceError("invalid captions list")
        ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ResourceError("invalid caption")
            cid = resource_id(item.get("id"))
            snippet = item.get("snippet")
            if (cid in ids or not isinstance(snippet, dict) or snippet.get("videoId") != video_id
                or not isinstance(item.get("etag"), str) or not item["etag"]
                or type(snippet.get("isDraft")) is not bool
                or not isinstance(snippet.get("language"), str) or not snippet["language"]
                or not isinstance(snippet.get("name"), str)):
                raise ResourceError("caption lacks verifiable identity, ownership, ETag or status")
            ids.add(cid)
        return items

    def find_caption(self, video_id, track_id):
        resource_id(track_id)
        matches = [t for t in self.list_captions(video_id) if t["id"] == track_id]
        if len(matches) != 1:
            raise ResourceError("caption was not verified on owned video; absence or permissions inconclusive")
        return matches[0]

    def download_caption(self, video_id, track_id):
        self.find_caption(video_id, track_id)
        # No tfmt/tlang: keep exactly the API's original-format response bytes.
        response = self._request("GET", f"{API}/captions/{quote(resource_id(track_id), safe='')}", max_response=MAX_CAPTION)
        data = response.content
        if not data or len(data) > MAX_CAPTION:
            raise ResourceError("caption download is empty or exceeds 100MB")
        return data

    def mutate(self, change, data):
        """Exactly one request; caller has persisted intent before entering."""
        headers = {"If-Match": change.before["etag"]}
        if change.action == "thumbnail-set":
            response = self._request("POST", f"{UPLOAD}/thumbnails/set", headers={
                **headers, "Content-Type": change.asset["mime"]},
                params={"videoId": change.video_id, "uploadType": "media"}, content=data)
        elif change.action == "caption-delete":
            response = self._request("DELETE", f"{API}/captions", headers=headers,
                params={"id": change.track_id})
            if response.status_code != 204:
                raise ResourceUncertain("captions.delete did not return 204; uncertain outcome")
            return {"deleted_id": change.track_id, "http_status": 204}
        else:
            inserting = change.action == "caption-insert"
            body = ({"snippet": {"videoId": change.video_id, "language": change.language,
                    "name": change.name, "isDraft": change.draft}} if inserting else {"id": change.track_id})
            if not inserting and change.draft_changed:
                body["snippet"] = {"isDraft": change.draft}
            params = {"part": "snippet" if "snippet" in body else "id"}
            if inserting:
                # A video ETag is not a caption-insert precondition supported by the API.
                headers = {}
            if data is None:
                response = self._request("PUT", f"{API}/captions", headers=headers, params=params, json=body)
            else:
                boundary = "socialctl_" + uuid.uuid4().hex
                while boundary.encode() in data:
                    boundary = "socialctl_" + uuid.uuid4().hex
                content = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                    + json.dumps(body, ensure_ascii=False).encode("utf-8")
                    + f"\r\n--{boundary}\r\nContent-Type: {change.asset['mime']}\r\n\r\n".encode()
                    + data + f"\r\n--{boundary}--\r\n".encode())
                response = self._request("POST" if inserting else "PUT", f"{UPLOAD}/captions",
                    headers={**headers, "Content-Type": f"multipart/related; boundary={boundary}"},
                    params={**params, "uploadType": "multipart"}, content=content)
        return self._json(response, writing=True)
