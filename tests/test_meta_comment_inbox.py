from datetime import datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from socialctl.cli import app
from socialctl.management.meta_comment_inbox import (
    CommentInboxStore,
    apply_inbox_draft,
    classify_comment,
    grouped_inbox,
    prepare_inbox_draft,
    reconcile_inbox_draft,
    sync_inbox,
)
from socialctl.management.meta_comments import CommentError
from tests.test_meta_comments import service


runner = CliRunner()


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("live HTTP prohibited; use MockTransport")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


def remote_row(graph, ident, text, *, author="999", created="2026-09-15T12:00:00+00:00"):
    row = graph.row(ident, text, author=author)
    row["created_time" if graph.platform == "facebook" else "timestamp"] = created
    row["from"]["name" if graph.platform == "facebook" else "username"] = f"author-{author}"
    return row


@pytest.mark.parametrize(
    "text,language,kind",
    [
        ("¿Qué pasó después?", "es", "pregunta"),
        ("Gracias, excelente vídeo", "es", "elogio"),
        ("Actually, that date is wrong", "en", "correccion"),
        ("follow me https://a.example https://b.example", "en", "spam"),
        ("1492", "und", "otro"),
    ],
)
def test_deterministic_classification_never_creates_copy(text, language, kind):
    assert classify_comment(text) == (language, kind)


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_incremental_sync_deduplicates_remote_ids_and_groups_durably(tmp_path, platform):
    mod, _, graph, client, _ = service(tmp_path, platform)
    inbox = CommentInboxStore(client.brand.raiz)
    first = remote_row(graph, "801", "¿Qué ocurrió?", created="2026-09-15T12:00:00Z")
    second = remote_row(graph, "802", "Gracias por el vídeo", author="998",
                        created="2026-09-15T12:01:00Z")
    own = remote_row(graph, "803", "our own", author=client.account_id,
                     created="2026-09-15T12:02:00Z")
    graph.pages = lambda request: {"data": [first, first, second, own]}

    result = sync_inbox(client, inbox, media_id="789")

    assert result["new"] == 2
    assert result["deduplicated"] == 1
    assert result["skipped_own"] == 1
    assert result["next_since"] == "2026-09-15T12:01:00Z"
    assert len(result["groups"]) == 2
    assert {row.status for row in inbox.load().comments} == {"observado"}

    third = remote_row(graph, "804", "No fue así: hay un error", author="997",
                       created="2026-09-15T12:03:00Z")
    def next_page(request):
        assert request.url.params.get("after") is None
        return {"data": [first, third]}
    graph.pages = next_page
    again = sync_inbox(client, inbox, media_id="789")
    assert again["since_used"] == "2026-09-15T12:01:00Z"
    assert again["new"] == 1
    assert again["deduplicated"] == 1
    assert again["skipped_before_since"] == 0
    assert len(inbox.load().comments) == 3


def test_incomplete_incremental_read_does_not_advance_since(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    first = remote_row(graph, "801", "First", created="2026-09-15T12:00:00Z")
    graph.pages = lambda request: {"data": [first]}
    sync_inbox(client, inbox, media_id="789")
    later = remote_row(graph, "802", "Later", created="2026-09-15T13:00:00Z")
    graph.pages = lambda request: {"data": [later], "paging": {"next": "https://example.invalid"}}

    result = sync_inbox(client, inbox, media_id="789", max_pages=1)

    assert result["complete"] is False
    assert result["next_since"] == "2026-09-15T12:00:00Z"
    assert inbox.load().cursors[0].since == "2026-09-15T12:00:00Z"


def test_partial_page_cursor_is_durable_and_resumed(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    first = remote_row(graph, "801", "First", created="2026-09-15T12:00:00Z")
    second = remote_row(graph, "802", "Second", created="2026-09-15T12:01:00Z")
    seen = []
    def pages(request):
        cursor = request.url.params.get("after")
        seen.append(cursor)
        if cursor == "safe_cursor":
            return {"data": [second]}
        return {"data": [first], "paging": {"cursors": {"after": "safe_cursor"},
            "next": "https://graph.facebook.com/ignored"}}
    graph.pages = pages

    partial = sync_inbox(client, inbox, media_id="789", max_pages=1)
    resumed = sync_inbox(client, inbox, media_id="789", max_pages=1)

    assert partial["complete"] is False
    assert partial["provider_cursor"] == "safe_cursor"
    assert resumed["complete"] is True
    assert resumed["provider_cursor"] is None
    assert seen == [None, "safe_cursor"]
    assert {row.comment_id for row in inbox.load().comments} == {"801", "802"}


def test_rejected_saved_cursor_restarts_from_since_and_persists_new_cursor(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    first = remote_row(graph, "801", "First", created="2026-09-15T12:00:00Z")
    second = remote_row(graph, "802", "Second", created="2026-09-15T12:01:00Z")
    graph.pages = lambda request: {"data": [first], "paging": {
        "cursors": {"after": "stale_cursor"}, "next": "https://graph.facebook.com/ignored"}}
    sync_inbox(client, inbox, media_id="789", since="2026-09-15T00:00:00Z", max_pages=1)

    seen = []
    graph.pages = lambda request: {"data": [second], "paging": {
        "cursors": {"after": "fresh_cursor"}, "next": "https://graph.facebook.com/ignored"}}
    def rejecting_transport(request):
        if request.url.path.endswith("/789/comments"):
            cursor = request.url.params.get("after")
            seen.append(cursor)
            if cursor == "stale_cursor":
                return httpx.Response(400, json={"error": {
                    "code": 100, "message": "The After Cursor specified is invalid"}})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(rejecting_transport))

    result = sync_inbox(client, inbox, media_id="789", max_pages=1)

    assert seen == ["stale_cursor", None]
    assert result["cursor_recovered"] is True
    assert result["since_used"] == "2026-09-15T00:00:00Z"
    assert result["provider_cursor"] == "fresh_cursor"
    saved = inbox.load().cursors[0]
    assert saved.provider_cursor == "fresh_cursor"
    assert saved.since == "2026-09-15T00:00:00Z"


def test_explicit_since_starts_fresh_instead_of_sending_saved_cursor(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    first = remote_row(graph, "801", "First", created="2026-09-15T12:00:00Z")
    graph.pages = lambda request: {"data": [first], "paging": {
        "cursors": {"after": "stale_cursor"}, "next": "https://graph.facebook.com/ignored"}}
    sync_inbox(client, inbox, media_id="789", max_pages=1)

    seen = []
    second = remote_row(graph, "802", "Second", created="2026-09-15T13:00:00Z")
    def fresh_page(request):
        seen.append(request.url.params.get("after"))
        return {"data": [second]}
    graph.pages = fresh_page

    result = sync_inbox(client, inbox, media_id="789",
                        since="2026-09-15T12:30:00Z", max_pages=1)

    assert seen == [None]
    assert result["since_used"] == "2026-09-15T12:30:00Z"
    assert result["cursor_recovered"] is False
    assert inbox.load().cursors[0].provider_cursor is None


def test_explicit_since_never_regresses_behind_older_durable_comments(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    old = remote_row(graph, "801", "Old", created="2026-09-15T12:00:00Z")
    graph.pages = lambda request: {"data": [old]}
    sync_inbox(client, inbox, media_id="789")

    graph.pages = lambda request: {"data": []}
    cutoff = "2026-09-15T13:00:00Z"
    result = sync_inbox(client, inbox, media_id="789", since=cutoff)

    assert result["since_used"] == cutoff
    assert result["next_since"] == cutoff
    assert inbox.load().cursors[0].since == cutoff


def test_rejected_cursor_without_since_uses_bounded_overlap_window(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    first = remote_row(graph, "801", "First", created="2026-09-15T12:00:00Z")
    graph.pages = lambda request: {"data": [first], "paging": {
        "cursors": {"after": "stale_cursor"}, "next": "https://graph.facebook.com/ignored"}}
    sync_inbox(client, inbox, media_id="789", max_pages=1)
    saved_at = inbox.load().cursors[0].last_sync_at

    graph.pages = lambda request: {"data": [first]}
    def rejecting_transport(request):
        if (request.url.path.endswith("/789/comments") and
                request.url.params.get("after") == "stale_cursor"):
            return httpx.Response(400, json={"error": {
                "code": 100, "message": "Invalid cursor"}})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(rejecting_transport))

    result = sync_inbox(client, inbox, media_id="789", max_pages=1)

    recovered_since = datetime.fromisoformat(result["since_used"].replace("Z", "+00:00"))
    anchor = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
    assert recovered_since == anchor - timedelta(days=7)
    assert result["cursor_recovered"] is True


def test_rejected_cursor_fallback_never_regresses_behind_overlap_cutoff(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    old = remote_row(graph, "801", "Old", created="2000-01-01T00:00:00Z")
    graph.pages = lambda request: {"data": [old], "paging": {
        "cursors": {"after": "stale_cursor"}, "next": "https://graph.facebook.com/ignored"}}
    sync_inbox(client, inbox, media_id="789", max_pages=1)

    graph.pages = lambda request: {"data": [old]}
    def rejecting_transport(request):
        if (request.url.path.endswith("/789/comments") and
                request.url.params.get("after") == "stale_cursor"):
            return httpx.Response(400, json={"error": {
                "code": 100, "message": "Invalid cursor"}})
        return graph(request)
    client.client = httpx.Client(transport=httpx.MockTransport(rejecting_transport))

    result = sync_inbox(client, inbox, media_id="789", max_pages=1)

    assert result["cursor_recovered"] is True
    assert result["next_since"] == result["since_used"]
    assert inbox.load().cursors[0].since == result["since_used"]


def observed_comment(tmp_path, *, platform="facebook", comment_id="800", text="Question?"):
    mod, _, graph, client, comment_store = service(tmp_path, platform)
    graph.comments[comment_id] = remote_row(graph, comment_id, text)
    inbox = CommentInboxStore(client.brand.raiz)
    sync_inbox(client, inbox, media_id="789")
    return mod, graph, client, comment_store, inbox


def test_explicit_draft_is_idempotent_and_editable_before_approval(tmp_path):
    _, graph, client, comment_store, inbox = observed_comment(tmp_path)
    first, record = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Exact supplied text")
    repeated, same = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Exact supplied text")
    revised, changed = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Revised explicit text")

    assert repeated.id == first.id
    assert same.idempotency_key == record.idempotency_key
    assert revised.id != first.id
    assert changed.status == "draft"
    assert changed.draft_text == "Revised explicit text"
    assert not graph.writes


@pytest.mark.parametrize("platform", ["facebook", "instagram"])
def test_approved_reply_records_rate_policy_and_never_reposts(tmp_path, platform):
    _, graph, client, comment_store, inbox = observed_comment(tmp_path, platform=platform)
    change, _ = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Exact supplied text")
    statuses_during_post = []
    graph.after_write = lambda: statuses_during_post.append(inbox.load().comments[0].status)

    result = apply_inbox_draft(client, comment_store, inbox, media_id="789",
        comment_id="800", approval_digest=change.fingerprint,
        max_replies=3, window_seconds=600)
    repeated = apply_inbox_draft(client, comment_store, inbox, media_id="789",
        comment_id="800", approval_digest=change.fingerprint,
        max_replies=3, window_seconds=600)

    assert statuses_during_post == ["aprobado"]
    assert result.status == repeated.status == "respondido"
    assert result.response_id == "900"
    assert len(graph.writes) == 1
    intent = next(event for event in result.journal if event["event"] == "reply_write_intent")
    assert intent["max_replies"] == 3
    assert intent["window_seconds"] == 600


def test_uncertain_reply_blocks_replay_and_reconcile_is_get_only(tmp_path):
    _, graph, client, comment_store, inbox = observed_comment(tmp_path)
    change, _ = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Exact supplied text")
    graph.failure = "timeout"
    result = apply_inbox_draft(client, comment_store, inbox, media_id="789",
        comment_id="800", approval_digest=change.fingerprint)
    assert result.status == 'incierto'
    with pytest.raises(CommentError, match="reconcile-draft"):
        apply_inbox_draft(client, comment_store, inbox, media_id="789",
            comment_id="800", approval_digest=change.fingerprint)
    writes = len(graph.writes)
    graph.failure = None
    reconciled = reconcile_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800")
    assert reconciled.status == 'incierto'
    assert len(graph.writes) == writes == 1


def test_crash_after_saved_response_id_reconciles_without_second_post(tmp_path, monkeypatch):
    _, graph, client, comment_store, inbox = observed_comment(tmp_path)
    change, _ = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Exact supplied text")
    original = client.verify_comment
    monkeypatch.setattr(client, "verify_comment",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        apply_inbox_draft(client, comment_store, inbox, media_id="789",
            comment_id="800", approval_digest=change.fingerprint)
    assert inbox.load().comments[0].status == "aprobado"
    assert comment_store.load(change.id).remote_id == "900"
    monkeypatch.setattr(client, "verify_comment", original)

    result = reconcile_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800")
    assert result.status == "respondido"
    assert len(graph.writes) == 1


def test_read_error_is_sanitized_and_does_not_create_state(tmp_path):
    _, _, graph, client, _ = service(tmp_path)
    inbox = CommentInboxStore(client.brand.raiz)
    graph.failure = "read_permission"
    with pytest.raises(CommentError) as caught:
        sync_inbox(client, inbox, media_id="789")
    assert "SECRET" not in str(caught.value)
    assert inbox.load().comments == []


def test_configurable_frequency_limit_blocks_before_second_post(tmp_path):
    _, _, graph, client, comment_store = service(tmp_path)
    graph.comments["800"] = remote_row(graph, "800", "First?")
    graph.comments["801"] = remote_row(graph, "801", "Second?", author="998")
    inbox = CommentInboxStore(client.brand.raiz)
    sync_inbox(client, inbox, media_id="789")
    first, _ = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Exact supplied text")
    apply_inbox_draft(client, comment_store, inbox, media_id="789", comment_id="800",
                      approval_digest=first.fingerprint, max_replies=1, window_seconds=3600)
    second, _ = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="801", text="Another explicit answer")

    with pytest.raises(CommentError, match='frequency'):
        apply_inbox_draft(client, comment_store, inbox, media_id="789", comment_id="801",
                          approval_digest=second.fingerprint, max_replies=1, window_seconds=3600)
    assert len(graph.writes) == 1
    blocked = next(row for row in inbox.load().comments if row.comment_id == "801")
    assert blocked.status == "bloqueado"
    assert blocked.last_error == "frequency_limit"


def test_cli_draft_preview_and_exact_approval(tmp_path, monkeypatch):
    import socialctl.management.comments_cli as cli
    _, brand, graph, client, comment_store = service(tmp_path)
    (brand.raiz / "accounts.yml").write_text(
        "facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n", encoding="utf-8")
    graph.comments["800"] = remote_row(graph, "800", "Question?")
    factory = httpx.Client
    monkeypatch.setattr(cli, "make_http_client",
        lambda: factory(transport=httpx.MockTransport(graph)))
    common = ["--platform", "facebook", "--brand", "Example", "--root", str(tmp_path)]
    synced = runner.invoke(app, ["comments", "sync", "789", *common])
    assert synced.exit_code == 0, synced.output
    drafted = runner.invoke(app, ["comments", "draft", "789", "800",
        "--text", "Exact supplied text", "--dry-run", *common])
    assert drafted.exit_code == 0, drafted.output
    record = CommentInboxStore(brand.raiz).load().comments[0]
    for value in ("PREVIEW COMPLETO", "source_comment", "changeset", "Exact supplied text",
                  record.fingerprint):
        assert value in drafted.output
    bypass = runner.invoke(app, ["comments", "apply", record.change_id,
        "--brand", "Example", "--root", str(tmp_path)], input=record.fingerprint + "\n")
    assert bypass.exit_code != 0
    assert "apply-draft" in bypass.output
    assert not graph.writes
    rejected = runner.invoke(app, ["comments", "apply-draft", "789", "800", *common],
                             input="wrong\n")
    assert rejected.exit_code != 0
    assert not graph.writes
    applied = runner.invoke(app, ["comments", "apply-draft", "789", "800", *common],
                            input=record.fingerprint + "\n")
    assert applied.exit_code == 0, applied.output
    assert '"status": "respondido"' in applied.output
    assert len(graph.writes) == 1


def test_superseded_inbox_draft_cannot_post_through_legacy_apply(tmp_path, monkeypatch):
    import socialctl.management.comments_cli as cli
    _, brand, graph, client, comment_store = service(tmp_path)
    (brand.raiz / "accounts.yml").write_text(
        "facebook:\n  page_id: '123'\ninstagram:\n  ig_user_id: '456'\n", encoding="utf-8")
    graph.comments["800"] = remote_row(graph, "800", "Question?")
    inbox = CommentInboxStore(brand.raiz)
    sync_inbox(client, inbox, media_id="789")
    old, _ = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Old explicit reply")
    current, _ = prepare_inbox_draft(client, comment_store, inbox,
        media_id="789", comment_id="800", text="Current explicit reply")
    factory = httpx.Client
    monkeypatch.setattr(cli, "make_http_client",
        lambda: factory(transport=httpx.MockTransport(graph)))
    common = ["--brand", "Example", "--root", str(tmp_path)]

    bypass = runner.invoke(app, ["comments", "apply", old.id, *common],
                           input=old.fingerprint + "\n")

    assert bypass.exit_code != 0
    assert 'replaced' in bypass.output
    assert not graph.writes
    applied = runner.invoke(app, ["comments", "apply-draft", "789", "800",
        "--platform", "facebook", *common], input=current.fingerprint + "\n")
    assert applied.exit_code == 0, applied.output
    assert len(graph.writes) == 1
