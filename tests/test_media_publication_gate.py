import json
import threading

import pytest

from socialctl.models import CampaignType, Platform, PlatformPost, Post, PostResult, PostStatus
from socialctl.publisher import publicar
from socialctl.scheduler import ScheduleStore
from tests.test_media_ingress import setup, ingress, wire
from tests.test_youtube_metadata_parts import isolated


def post_for(brand, platform):
    return Post(slug="clip", brand=brand.nombre, campaign=CampaignType.CLIP_VERTICAL,
        platforms={platform: PlatformPost(platform=platform, body="approved text")})


@pytest.mark.parametrize("platform", [Platform.YOUTUBE, Platform.FACEBOOK])
def test_live_publish_gate_blocks_ingress_and_allows_queue_callback(tmp_path, monkeypatch, platform):
    local, manifest, server, public = setup(tmp_path)
    path = server.dir_posts / "clip/post.yml"
    path.parent.mkdir()
    path.write_bytes(b"approved text")
    entered, release = threading.Event(), threading.Event()
    outcomes = []
    class Adapter:
        def validate(self, post, brand):
            return []

        def publish(self, post, brand, client):
            entered.set()
            assert release.wait(5)
            return PostResult(platform=platform, status=PostStatus.PUBLICADO, platform_id="fake-id")
    monkeypatch.setitem(__import__("socialctl.publisher", fromlist=["ADAPTADORES"]).ADAPTADORES, platform, Adapter)
    def callback(result):
        # Real queue mutation inside publication gate must not deadlock ingress.
        ScheduleStore(server.raiz).save([])
    thread = threading.Thread(target=lambda: outcomes.extend(publicar(post_for(server, platform), server, on_media_result=callback)))
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ValueError, match='running concurrently'):
            ingress().receive(server, wire(local, manifest), preview_update=True)
        assert path.read_bytes() == b"approved text"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and outcomes[0].status is PostStatus.PUBLICADO
    # Even before guardar_resultado, a completed/uncertain start is non-draft.
    assert not (path.parent / "resultado.json").exists()
    with pytest.raises(ValueError, match='publication'):
        ingress().receive(server, wire(local, manifest), preview_update=True)


def test_publication_marker_persistence_failure_blocks_adapter(tmp_path, monkeypatch):
    local, manifest, server, public = setup(tmp_path)
    from socialctl import media_registry, publisher
    effects = []
    class Adapter:
        def validate(self, post, brand):
            return []

        def publish(self, *args):
            effects.append("write")
            return PostResult(platform=Platform.YOUTUBE, status=PostStatus.PUBLICADO)
    monkeypatch.setitem(publisher.ADAPTADORES, Platform.YOUTUBE, Adapter)
    monkeypatch.setattr(media_registry, "write_json", lambda *args: (_ for _ in ()).throw(OSError("SECRET")))
    result = publicar(post_for(server, Platform.YOUTUBE), server)
    assert effects == []
    assert result[0].status is PostStatus.ERROR and result[0].riesgo_duplicado is False
    assert "SECRET" not in result[0].error


def test_marker_does_not_block_explicit_fresh_publish(tmp_path, monkeypatch):
    local, manifest, server, public = setup(tmp_path)
    from socialctl import publisher
    effects = []
    class Adapter:
        def validate(self, post, brand):
            return []

        def publish(self, *args):
            effects.append("write")
            return PostResult(platform=Platform.YOUTUBE, status=PostStatus.PUBLICADO)
    monkeypatch.setitem(publisher.ADAPTADORES, Platform.YOUTUBE, Adapter)
    publicar(post_for(server, Platform.YOUTUBE), server)
    publicar(post_for(server, Platform.YOUTUBE), server)
    assert effects == ["write", "write"]
    marker = json.loads((server.raiz / ".socialctl/media-publication-started/clip.json").read_bytes())
    assert marker["brand"] == "MarcaA" and marker["slug"] == "clip"
    assert marker["platforms"] == ["youtube"]


def test_transport_exit_failure_after_effect_is_not_reported_as_safe_to_retry(tmp_path, monkeypatch):
    local, manifest, server, public = setup(tmp_path)
    from socialctl import publisher
    effects = []
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            raise OSError("client close failed after publication")
    class Adapter:
        def validate(self, post, brand):
            return []

        def publish(self, *args):
            effects.append("write")
            return PostResult(platform=Platform.YOUTUBE, status=PostStatus.PUBLICADO)
    monkeypatch.setattr(publisher.httpx, "Client", Client)
    monkeypatch.setitem(publisher.ADAPTADORES, Platform.YOUTUBE, Adapter)
    with pytest.raises(OSError):
        publicar(post_for(server, Platform.YOUTUBE), server)
    assert effects == ["write"]
