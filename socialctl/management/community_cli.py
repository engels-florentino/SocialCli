"""Named community reads and exact approved intents; audit never writes."""
import json
from pathlib import Path

import httpx
import typer

from socialctl.read_budget import active_read_budget, bounded_read, read_client

from socialctl.management.changes import ChangeError
from socialctl.management.community_schema import load_edit
from socialctl.management.community_changes import CommunityStore, prepare_community, apply_community, reconcile_community
from socialctl.management.youtube_community import YouTubeCommunityClient


def make_http_client():
    budget = active_read_budget.get()
    if budget is not None:
        return read_client(budget, progress=lambda: typer.echo('Reading provider data...', err=True))
    return httpx.Client(timeout=60.0, follow_redirects=False)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def render_preview(change):
    return ("DRY-RUN — FULL PREVIEW of YouTube community\n" + _json(change.model_dump(mode="json"))
        + "\nExact approval of actor, target, text, reason and each displayed effect. No --yes option."
        + f"\nExact fingerprint for approval: {change.fingerprint}")


def register(content_app, default_root):
    from socialctl.management.cli import _brand, _fail
    community = typer.Typer(help="YouTube community: bounded reads and approved explicit interactions.")
    content_app.add_typer(community, name="youtube-community")

    def read(root, brand, callback, timeout, json_output):
        selected = _brand(root, brand)
        try:
            with bounded_read(timeout), make_http_client() as http:
                result = callback(YouTubeCommunityClient(selected, http))
        except (ChangeError, OSError, ValueError, httpx.HTTPError) as exc:
            from socialctl.read_reports import fail_read
            fail_read(exc, json_output=json_output)
        typer.echo(_json(result))
        if isinstance(result, dict) and result.get("complete") is False:
            raise typer.Exit(1)

    @community.command("threads-list")
    def threads_list(video_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                     moderation_status: str = typer.Option("published", "--moderation-status"), max_pages: int = typer.Option(100, "--max-pages", min=1, max=100), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.list_threads(video_id, moderation_status=moderation_status, max_pages=max_pages), timeout=timeout, json_output=json_output)

    @community.command("replies-list")
    def replies_list(video_id: str, thread_id: str, parent_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                     max_pages: int = typer.Option(100, "--max-pages", min=1, max=100), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.list_replies(video_id, thread_id, parent_id, max_pages=max_pages), timeout=timeout, json_output=json_output)

    @community.command("comment-show")
    def comment_show(video_id: str, thread_id: str, comment_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                     parent_id: str | None = typer.Option(None, "--parent-id"), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.find_comment(video_id, thread_id, comment_id, parent_id), timeout=timeout, json_output=json_output)

    @community.command("subscriptions-list")
    def subscriptions_list(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                           max_pages: int = typer.Option(100, "--max-pages", min=1, max=100), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.list_subscriptions(max_pages=max_pages), timeout=timeout, json_output=json_output)

    @community.command("rating-show")
    def rating_show(video_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.get_rating(video_id), timeout=timeout, json_output=json_output)

    @community.command("search")
    def search(query: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
               kind: str = typer.Option("video", "--type"), order: str = typer.Option("relevance", "--order"),
               channel_id: str | None = typer.Option(None, "--channel-id"), max_pages: int = typer.Option(100, "--max-pages", min=1, max=100), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.search(query, kind=kind, order=order, channel_id=channel_id, max_pages=max_pages), timeout=timeout, json_output=json_output)

    @community.command("activities-list")
    def activities_list(brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                        published_after: str | None = typer.Option(None, "--published-after"), published_before: str | None = typer.Option(None, "--published-before"),
                        max_pages: int = typer.Option(100, "--max-pages", min=1, max=100), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.activities(published_after=published_after, published_before=published_before, max_pages=max_pages), timeout=timeout, json_output=json_output)

    @community.command("catalog")
    def catalog(name: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                language: str = typer.Option("en", "--language"), region: str | None = typer.Option(None, "--region"), timeout: float = typer.Option(120, "--timeout"), json_output: bool = typer.Option(False, "--json", help="Explicit JSON mode (already the default).")):
        read(root, brand, lambda c: c.catalog(name, language=language, region=region), timeout=timeout, json_output=json_output)

    @community.command("prepare")
    def prepare(file: Path = typer.Option(..., "--file"), brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root"),
                dry_run: bool = typer.Option(False, "--dry-run")):
        selected = _brand(root, brand)
        try:
            edit = load_edit(file)
            with make_http_client() as http:
                change = prepare_community(YouTubeCommunityClient(selected, http), CommunityStore(selected.raiz), edit)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(render_preview(change))

    @community.command("status")
    def status(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            change = CommunityStore(selected.raiz).load(change_id)
        except ChangeError as exc:
            _fail(str(exc))
        typer.echo(_json(change.model_dump(mode="json")))

    @community.command("apply")
    def apply(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        store = CommunityStore(selected.raiz)
        try:
            change = store.load(change_id)
            typer.echo(render_preview(change))
            approval = typer.prompt("Enter the exact fingerprint to approve")
            if approval != change.fingerprint:
                _fail("approval does not match exact fingerprint")
            with make_http_client() as http:
                result = apply_community(YouTubeCommunityClient(selected, http), store, change_id, approval)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result.model_dump(mode="json")))
        if result.status != "verified":
            raise typer.Exit(1)

    @community.command("reconcile")
    def reconcile(change_id: str, brand: str = typer.Option(..., "--brand"), root: Path = typer.Option(default_root, "--root")):
        selected = _brand(root, brand)
        try:
            with make_http_client() as http:
                result = reconcile_community(YouTubeCommunityClient(selected, http), CommunityStore(selected.raiz), change_id)
        except (ChangeError, OSError) as exc:
            _fail(str(exc))
        typer.echo(_json(result.model_dump(mode="json")))
