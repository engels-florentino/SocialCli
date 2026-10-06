import hashlib

import httpx
import pytest

from socialctl.hosted_media import HostedMediaError, attest_public_media


def test_full_public_bytes_are_attested(tmp_path):
    raw = b"supplied-video-bytes"
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / f"{digest}.mp4"
    path.write_bytes(raw)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=raw, headers={"Content-Type": "video/mp4"})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = attest_public_media(path, f"https://media.test/{path.name}", client=client)
    assert result["sha256"] == digest
    assert result["size"] == len(raw)
    assert len(calls) == 1 and calls[0].method == "GET"


@pytest.mark.parametrize("url,status,body,mime", [
    ("http://media.test/a", 200, b"good", "video/mp4"),
    ("https://user:secret@media.test/a", 200, b"good", "video/mp4"),
    ("https://media.test/a?token=secret", 200, b"good", "video/mp4"),
    ("https://media.test/a", 302, b"good", "video/mp4"),
    ("https://media.test/a", 200, b"evil", "video/mp4"),
    ("https://media.test/a", 200, b"good", "text/html"),
    ("https://media.test/a", 404, b"good", "video/mp4"),
])
def test_public_preflight_fails_closed(tmp_path, url, status, body, mime):
    path = tmp_path / (hashlib.sha256(b"good").hexdigest() + ".mp4")
    path.write_bytes(b"good")
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, content=body, headers={"Content-Type": mime}))) as client:
        with pytest.raises(HostedMediaError):
            attest_public_media(path, url, client=client)


def test_preflight_never_forwards_caller_auth_headers_or_cookies(tmp_path):
    raw = b"good"
    path = tmp_path / (hashlib.sha256(raw).hexdigest() + ".mp4")
    path.write_bytes(raw)
    def respond(request):
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "x-api-key" not in request.headers
        return httpx.Response(200, content=raw, headers={"content-type": "video/mp4"})
    with httpx.Client(transport=httpx.MockTransport(respond), auth=("user", "secret"), headers={"Authorization": "secret", "X-API-Key": "secret"}, cookies={"secret": "value"}) as client:
        attest_public_media(path, f"https://media.test/{path.name}", client=client)


def test_stream_overrun_stops_without_reading_rest(tmp_path):
    path = tmp_path / (hashlib.sha256(b"good").hexdigest() + ".mp4")
    path.write_bytes(b"good")
    seen = []
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            for chunk in (b"good", b"excess", b"should never read"):
                seen.append(chunk)
                yield chunk
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Chunks(), headers={"content-type": "video/mp4"}))) as client:
        with pytest.raises(HostedMediaError):
            attest_public_media(path, f"https://media.test/{path.name}", client=client)
    assert seen == [b"good", b"excess"]


def test_total_deadline_detected_between_small_chunks(tmp_path, monkeypatch):
    path = tmp_path / (hashlib.sha256(b"good").hexdigest() + ".mp4")
    path.write_bytes(b"good")
    clock = iter([0, 2])
    monkeypatch.setattr("socialctl.hosted_media.time.monotonic", lambda: next(clock))
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"good", headers={"content-type": "video/mp4"}))) as client:
        with pytest.raises(HostedMediaError, match="tiempo"):
            attest_public_media(path, f"https://media.test/{path.name}", client=client, timeout_s=1)
