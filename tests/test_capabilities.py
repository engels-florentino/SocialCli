from pathlib import Path

from socialctl.capabilities import capability_report, load_capabilities


ROOT = Path(__file__).resolve().parents[1]


def test_registry_has_exact_discovery_counts_and_versioned_sources():
    registry = load_capabilities()
    assert registry["version"] == 1
    methods = registry["capabilities"]
    for platform, expected in [("youtube", 83), ("youtubeAnalytics", 8), ("youtubereporting", 8)]:
        rows = [r for r in methods if r["api"] == platform]
        assert len(rows) == expected
    assert len({r["id"] for r in methods}) == len(methods)
    assert all(r["source"]["url"].startswith("https://") and r["source"]["observed_at"] in {"2026-09-12", "2026-09-13"} for r in methods)


def test_each_normal_method_has_concrete_owner_and_implementation_evidence():
    for row in load_capabilities()["capabilities"]:
        assert row["availability"] in {"api", "ui_only", "unavailable", "unknown"}
        assert row["field_scope"]
        for implemented in row["implemented_scopes"]:
            assert implemented["fields"] and implemented["command"]
            assert (ROOT / implemented["handler"].split(":")[0]).is_file()
            assert implemented["tests"]
            for evidence in implemented["tests"]:
                file, _, symbol = evidence.partition("::")
                assert (ROOT / file).is_file()
                assert not symbol or f"def {symbol}(" in (ROOT / file).read_text()
        if row["delivery"] == "normal" and row["availability"] == "api":
            assert row["implemented_scopes"] or row["remaining"]
            for remaining in row["remaining"]:
                assert remaining["task"] in range(8, 15)
                assert remaining["scope"] and remaining["acceptance"]
        if row["availability"] in {"unknown", "unavailable", "ui_only"}:
            assert not row["implemented_scopes"]
            assert row["enabled"] is False


def test_classification_coverage_does_not_claim_implementation_percentage():
    report = capability_report()
    assert report["classification"]["records"] >= 99
    assert report["classification"]["records"] > report["implementation"]["methods_with_scoped_handlers"]
    assert report["implementation"]["full_api_coverage_percent"] is None
    assert report["implementation"]["remaining_scopes"] == len(report["implementation"]["remaining"])
    assert all(item["id"] and item["task"] in range(8, 15) for item in report["implementation"]["remaining"])
    by_id = {r["id"]: r for r in report["capabilities"]}
    assert by_id["youtube.tests.insert"]["availability"] == "unknown"
    assert by_id["youtube.comments.markAsSpam"]["availability"] == "unavailable"
    assert by_id["youtube.videos.batchGetStats"]["implemented_scopes"][0]["handler"].endswith("AnalyticsClient.batch_stats")
    assert by_id["youtube.videos.delete"]["implemented_scopes"][0]["handler"] == "socialctl/management/owned_changes.py:apply_owned"
    assert by_id["youtube.search.list"]["implemented_scopes"][0]["handler"].endswith("YouTubeCommunityClient.search")


def test_native_scheduling_routes_fail_closed_with_structured_evidence():
    rows = {r["id"]: r for r in load_capabilities()["capabilities"]}
    ids = {
        "youtube.native-scheduling",
        "facebook.feed.native-scheduling",
        "facebook.photos.native-scheduling",
        "facebook.reels.native-scheduling",
        "facebook.video.native-scheduling-ui",
        "instagram.native-scheduling-ui",
        "tiktok.native-scheduling-ui",
    }
    selected = [rows[id_] for id_ in ids]
    assert all(not row["enabled"] and row["checked_at"] == "2026-09-20" for row in selected)
    assert all(row["route"] in {"api", "ui", "blocked"} for row in selected)
    for row in selected:
        for field in ("supported_account", "verification_surface", "window_min", "window_max", "precision"):
            assert field in row
        if None in (row["window_min"], row["window_max"], row["precision"]):
            assert row["route"] != "api"
    for id_ in ("youtube.native-scheduling", "facebook.feed.native-scheduling",
                "facebook.photos.native-scheduling", "facebook.reels.native-scheduling"):
        assert rows[id_]["route"] == "blocked"
        assert rows[id_]["availability"] == "unknown"
        assert rows[id_]["endpoint"] is None
        assert rows[id_]["candidate_api"]["create_source_url"].startswith("https://")
    assert rows["facebook.feed.native-scheduling"]["candidate_api"]["create_endpoint"].endswith("/feed")
    photo_candidate = rows["facebook.photos.native-scheduling"]["candidate_api"]
    assert photo_candidate["create_endpoint"].endswith("/photos")
    assert photo_candidate["finalize_endpoint"].endswith("/feed")
    assert rows["facebook.reels.native-scheduling"]["candidate_api"]["read_endpoint"] is None
    assert rows["facebook.reels.native-scheduling"]["candidate_api"]["read_source_url"] is None
    assert rows["tiktok.native-scheduling-ui"]["supported_account"] is True
    assert rows["instagram.native-scheduling-ui"]["supported_account"] is None


def test_video_methods_describe_their_actual_inputs_and_outputs():
    rows = {r["id"]: r for r in load_capabilities()["capabilities"]}
    required = {
        "rate": ["id", "rating=like|dislike|none", "204", "no response body"],
        "getRating": ["id", "items[].videoId", "items[].rating"],
        "reportAbuse": ["videoId", "reasonId", "secondaryReasonId", "comments", "language", "204"],
        "batchGetStats": ["id", "part", "statistics.viewCount", "summary.failedVideoIds", "contentDetails.durationMillis"],
        "delete": ["id", "204", "no response body"],
        "insert": ["supplied video bytes", "snippet", "status", "returned video id"],
        "update": ["id", "part", "snippet", "localizations", "status", "recordingDetails.recordingDate"],
        "list": ["id", "part", "processingDetails", "contentDetails"],
    }
    for method, fields in required.items():
        scope = rows[f"youtube.videos.{method}"]["field_scope"]
        for field in fields:
            assert field.lower() in scope.lower(), (method, field, scope)
    for method in ("rate", "getRating", "reportAbuse", "delete"):
        scope = rows[f"youtube.videos.{method}"]["field_scope"]
        assert "processingDetails" not in scope and "localizations" not in scope


def test_task9_methods_have_exact_scoped_evidence_without_blanket_claim():
    rows = {r["id"]: r for r in load_capabilities()["capabilities"]}
    methods = [f"{resource}.{method}" for resource in ("playlists", "playlistItems", "channelSections", "playlistImages") for method in ("list", "insert", "update", "delete")]
    methods += ["channels.list", "channels.update", "channelBanners.insert", "watermarks.set", "watermarks.unset", "videos.list", "videos.update", "videos.delete"]
    for method in methods:
        row = rows["youtube." + method]
        assert row["enabled"] and row["availability"] == "api"
        assert any("youtube-owned" in item["command"] for item in row["implemented_scopes"]), method
        assert not any(rem["task"] == 9 for rem in row["remaining"]), method
    assert "ETag" in rows["youtube.playlistImages.update"]["limitation"]
    assert "parent" in rows["youtube.playlistImages.list"]["field_scope"]
    assert "defaultAudioLanguage" in rows["youtube.videos.update"]["limitation"]


def test_task10_exact_scopes_and_honest_disabled_ui_and_discovery_methods():
    rows = {r["id"]: r for r in load_capabilities()["capabilities"]}
    assert not any(rem["task"] == 10 for row in rows.values() for rem in row["remaining"])
    methods = ["i18nRegions.list", "videoCategories.list", "search.list", "i18nLanguages.list",
        "subscriptions.list", "subscriptions.delete", "subscriptions.insert", "activities.list",
        "comments.setModerationStatus", "comments.update", "comments.list", "comments.insert", "comments.delete",
        "videoAbuseReportReasons.list", "videos.reportAbuse", "videos.getRating", "videos.rate", "commentThreads.list", "commentThreads.insert"]
    for method in methods:
        row = rows["youtube." + method]
        assert row["enabled"] and row["implemented_scopes"]
        assert all("youtube-community" in s["command"] for s in row["implemented_scopes"])
    assert "original-author-only" in rows["youtube.comments.delete"]["field_scope"]
    assert rows["youtube.abuseReports.insert"]["availability"] == "unknown"
    assert not rows["youtube.abuseReports.insert"]["enabled"]
    for feature in ("comment-pin", "comment-heart", "community-polls", "cards", "end-screens", "shorts-related-video"):
        row = rows["youtube.ui." + feature]
        assert row["availability"] == "ui_only" and not row["enabled"] and not row["implemented_scopes"]


def test_task11_exact_method_handlers_and_documented_limits():
    rows = load_capabilities()["capabilities"]
    assert not any(r["task"] == 11 for row in rows for r in row["remaining"])
    selected = [r for r in rows if r["api"] in {"youtubeAnalytics", "youtubereporting"} or r["id"] == "youtube.videos.batchGetStats"]
    assert len(selected) == 17
    for row in selected:
        assert row["enabled"] and row["implemented_scopes"]
        assert any("youtube-analytics" in s["command"] for s in row["implemented_scopes"])
        assert row["limitation"]
    by_id = {r["id"]: r for r in selected}
    assert "title only" in by_id["youtubeAnalytics.groups.update"]["field_scope"]
    assert "youtube.force-ssl" in by_id["youtubeAnalytics.groups.update"]["limitation"]
    assert "youtube.readonly" in by_id["youtubeAnalytics.reports.query"]["eligibility"]
    assert "replace" in by_id["youtubereporting.media.download"]["limitation"]
    assert "summary can be omitted" in by_id["youtube.videos.batchGetStats"]["limitation"]
    assert "unaccounted IDs" in by_id["youtube.videos.batchGetStats"]["limitation"]


def test_meta_inventory_diagnostics_profiles_and_content_insights_are_closed():
    rows = {r["id"]: r for r in load_capabilities()["capabilities"]}
    delivered = [
        "facebook.content.list", "facebook.posts.list", "facebook.comments.list",
        "facebook.profile.get", "facebook.insights.get", "instagram.content.list",
        "instagram.comments.list", "instagram.profile.get", "instagram.insights.get",
        "instagram.stories.list",
    ]
    for method in delivered:
        row = rows[method]
        assert row["enabled"] and row["implemented_scopes"], method
        assert not any(item["task"] == 12 for item in row["remaining"]), method
    assert any("meta insights" in item["command"] for item in rows["facebook.insights.get"]["implemented_scopes"])
    assert any("include-moderation" in item["command"] for item in rows["facebook.comments.list"]["implemented_scopes"])
