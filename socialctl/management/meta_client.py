"""Bounded Graph v26 methods with actual actor, Page and owned-node binding."""
from __future__ import annotations

import json
import re

from socialctl.identity import credential_metadata
from socialctl.management.meta_schema import MetaError, MetaRejected, MetaUncertain, meta_id, validate_edit
from socialctl.models import Platform

GRAPH = "https://graph.facebook.com/v26.0"
MAX_RESPONSE = 4 * 1024 * 1024
PROFILE_FIELDS = {
    "facebook": {"id", "name", "about", "bio", "description", "company_overview", "general_info", "mission", "phone", "website", "followers_count", "link"},
    "instagram": {"id", "name", "username", "biography", "website", "followers_count", "media_count", "profile_picture_url"},
}
CONTENT_FIELDS = {
    "post": "id,from,message,created_time,updated_time,is_published,scheduled_publish_time,permalink_url",
    "video": "id,from,title,description,created_time,updated_time,status,published,scheduled_publish_time,permalink_url",
    "media": "id,owner,caption,media_type,media_product_type,timestamp,permalink,comment_enabled,alt_text,is_ai_generated",
}

# Only metrics already observed in the project's current Graph API reads are
# accepted here.  This is intentionally a content-insights surface: account
# dashboards use the provider-specific `stats` readers and are not silently
# treated as interchangeable with object metrics.
INSIGHT_METRICS = {
    "facebook": frozenset({
        "post_reactions_by_type_total", "post_video_views",
        "post_video_view_time", "post_video_avg_time_watched",
    }),
    "instagram": frozenset({
        "reach", "saved", "shares", "total_interactions", "views",
    }),
}
INSIGHT_PERIODS = frozenset({"day", "week", "days_28", "lifetime"})


class MetaClient:
    def __init__(self, brand, platform, client):
        if platform not in {Platform.FACEBOOK, Platform.INSTAGRAM}:
            raise MetaError("plataforma Meta requerida")
        self.brand, self.platform, self.client = brand, platform, client
        self.page_id = meta_id((brand.cuentas.get("facebook") or {}).get("page_id"))
        self.account_id = self.page_id if platform is Platform.FACEBOOK else meta_id((brand.cuentas.get("instagram") or {}).get("ig_user_id"))
        try:
            secret = brand.leer_secreto(platform)
            self._token = secret.get("access_token")
            self.token_mode = secret.get("token_mode", "facebook_page")
            self.actor_id = secret.get("actor_id")
            expired = credential_metadata(secret)["expiry"]["expired"]
        except Exception:
            raise MetaError("Meta credentials unavailable") from None
        if not isinstance(self._token, str) or not self._token or expired:
            raise MetaError("Meta credentials missing/expired; not refreshed automatically")
        if self.token_mode not in {"facebook_page", "facebook_user"}:
            raise MetaError("unsupported token mode; Facebook Login required")

    def request(self, method, path, *, params=None, data=None, files=None):
        # Paths are built only by resource methods. No redirect/remote next URL.
        if method not in {"GET", "POST", "DELETE"} or not re.fullmatch(r"(?:me|[0-9]+(?:_[0-9]+)?)(?:/[a-z_]+)?", path):
            raise MetaError("endpoint/method not allowed")
        writing = method != "GET"
        try:
            with self.client.stream(method, f"{GRAPH}/{path}", params=params, data=data, files=files,
                    headers={"Authorization": f"Bearer {self._token}"}, timeout=30, follow_redirects=False) as response:
                if not response.is_success:
                    error = (MetaUncertain if response.status_code >= 500 or response.is_redirect or response.status_code in {408, 429}
                             else MetaRejected) if writing else MetaError
                    raise error(f"Meta HTTP {response.status_code}; check permissions/eligibility")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE:
                        raise ValueError()
                result = json.loads(raw)
                if not isinstance(result, dict) or "error" in result:
                    raise ValueError()
                return result
        except MetaError:
            raise
        except Exception:
            raise (MetaUncertain if writing else MetaError)("Meta: response/transport unverifiable") from None

    def listing(self, target, edge, *, fields, max_pages=10, params=None):
        meta_id(target, composite=True)
        if edge not in {"accounts", "permissions", "feed", "photos", "posts", "videos", "video_reels", "media", "stories", "comments", "reactions", "likes", "insights", "subscribed_apps", "scheduled_posts", "captions"}:
            raise MetaError("unsupported read edge")
        if type(max_pages) is not int or not 1 <= max_pages <= 50:
            raise MetaError("maximum pages must be between 1 and 50")
        return self._pages(f"{target}/{edge}", {"fields": fields, "limit": "100", **(params or {})}, max_pages)

    def _pages(self, path, params, maximum=10):
        rows, seen, ids = [], set(), set()
        for _ in range(maximum):
            result = self.request("GET", path, params=params)
            data = result.get("data")
            if not isinstance(data, list) or len(data) > 100 or any(not isinstance(row, dict) for row in data):
                raise MetaError("invalid/excessive Meta list")
            for row in data:
                if row.get("id") in ids:
                    raise MetaError("repeated IDs in paginated read")
                if "id" in row:
                    ids.add(meta_id(row["id"], composite=True))
            rows.extend(data)
            paging = result.get("paging", {})
            if not isinstance(paging, dict):
                raise MetaError("invalid pagination")
            if not paging.get("next"):
                return {"data": rows, "complete": True, "absence_proven": False}
            cursor = (paging.get("cursors") or {}).get("after")
            if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_=-]{1,2048}", cursor) or cursor in seen:
                break
            seen.add(cursor)
            params = {**params, "after": cursor}
        return {"data": rows, "complete": False, "absence_proven": False}

    def identity(self, *, user_required=False):
        if user_required and self.token_mode != "facebook_user":
            raise MetaError("Instagram DELETE requires Facebook USER token and instagram_basic + instagram_manage_contents; Page token is not substituted")
        me = self.request("GET", "me", params={"fields": "id,instagram_business_account"} if self.token_mode == "facebook_page" else {"fields": "id"})
        actor = meta_id(me.get("id"))
        if self.token_mode == "facebook_page":
            if actor != self.page_id:
                raise MetaError("authenticated Page does not match brand")
            selected = me
        else:
            if actor != meta_id(self.actor_id):
                raise MetaError("authenticated USER actor does not match configured actor")
            pages = self._pages("me/accounts", {"fields": "id,tasks,instagram_business_account", "limit": "100"})
            matches = [p for p in pages["data"] if p.get("id") == self.page_id]
            if not pages["complete"] or len(matches) != 1 or not matches[0].get("tasks"):
                raise MetaError("Page managed by USER actor was not verified")
            selected = matches[0]
            if user_required:
                permissions = self._pages("me/permissions", {"limit": "100"})
                granted = {p.get("permission") for p in permissions["data"] if p.get("status") == "granted"}
                if not permissions["complete"] or not {"instagram_basic", "instagram_manage_contents"} <= granted:
                    raise MetaError("granted instagram_basic + instagram_manage_contents permissions missing for DELETE")
        if self.platform is Platform.INSTAGRAM and (selected.get("instagram_business_account") or {}).get("id") != self.account_id:
            raise MetaError("linked Instagram account does not match brand")
        return {"actor_id": actor, "token_mode": self.token_mode, "page_id": self.page_id, "account_id": self.account_id}

    def profile(self, fields=None):
        self.identity()
        allowed = PROFILE_FIELDS[self.platform.value]
        fields = sorted(allowed) if fields is None else fields
        if not fields or set(fields) - allowed:
            raise MetaError("profile fields outside documented list")
        result = self.request("GET", self.account_id, params={"fields": ",".join(sorted(set(fields) | {"id"}))})
        if result.get("id") != self.account_id:
            raise MetaError("profile ID mismatch")
        return {k: result[k] for k in set(fields) | {"id"} if k in result}

    def content(self, target_id, kind=None):
        self.identity()
        kind = kind or ("media" if self.platform is Platform.INSTAGRAM else "post")
        if kind not in CONTENT_FIELDS or (kind == "media") != (self.platform is Platform.INSTAGRAM):
            raise MetaError("incompatible content type/platform")
        meta_id(target_id, composite=kind == "post")
        result = self.request("GET", target_id, params={"fields": CONTENT_FIELDS[kind]})
        owner = "owner" if kind == "media" else "from"
        if result.get("id") != target_id or (result.get(owner) or {}).get("id") != self.account_id:
            raise MetaError("exact content owner was not verified")
        return {k: result[k] for k in CONTENT_FIELDS[kind].split(",") if k in result}

    def content_list(self, edge=None, max_pages=10):
        self.identity()
        edge = edge or ("media" if self.platform is Platform.INSTAGRAM else "posts")
        valid = {"media", "stories"} if self.platform is Platform.INSTAGRAM else {"feed", "photos", "posts", "videos", "video_reels", "stories", "scheduled_posts"}
        if edge not in valid:
            raise MetaError("listing unsupported for platform")
        kind = "media" if self.platform is Platform.INSTAGRAM else ("video" if edge in {"videos", "video_reels"} else "post")
        result = self.listing(self.account_id, edge, fields=CONTENT_FIELDS[kind], max_pages=max_pages)
        owner = "owner" if kind == "media" else "from"
        for row in result["data"]:
            meta_id(row.get("id"), composite=kind == "post")
            if (row.get(owner) or {}).get("id") != self.account_id:
                raise MetaError("owner not verified in listing")
        return result

    def insights(self, target_id, metrics, *, period=None, since=None, until=None,
                 breakdown=None, max_pages=10):
        """Read explicitly selected, owned content insights.

        The account-level Graph insights catalogue changes independently from
        object metrics.  Requiring an explicit owned target keeps this method
        from turning an account metric name into a false zero or an invented
        compatibility claim; the account-wide snapshot remains `stats`.
        """
        self.identity()
        target_id = meta_id(target_id, composite=self.platform is Platform.FACEBOOK)
        self.content(target_id)
        if not isinstance(metrics, (list, tuple)) or not metrics:
            raise MetaError("select at least one content metric")
        names = list(metrics)
        if len(names) > 20 or len(set(names)) != len(names) or any(
            not isinstance(name, str) or name not in INSIGHT_METRICS[self.platform.value]
            for name in names
        ):
            raise MetaError("Meta metric not allowed or repeated for this platform")
        if period is not None and period not in INSIGHT_PERIODS:
            raise MetaError("undocumented Meta period")
        for value, label in ((since, "since"), (until, "until")):
            if value is not None and (type(value) is not int or value < 0):
                raise MetaError(f"{label} must be an integer Unix timestamp")
        if since is not None and until is not None and since >= until:
            raise MetaError("since must precede until")
        if breakdown is not None:
            if not isinstance(breakdown, (list, tuple)) or not breakdown or len(breakdown) > 3:
                raise MetaError("breakdown must contain between 1 and 3 values")
            if any(not isinstance(value, str) or not re.fullmatch(r"[a-z_]{1,64}", value) for value in breakdown):
                raise MetaError("breakdown contains an invalid value")
        params = {"metric": ",".join(names), "limit": "100"}
        if period is not None:
            params["period"] = period
        if since is not None:
            params["since"] = str(since)
        if until is not None:
            params["until"] = str(until)
        if breakdown is not None:
            params["breakdown"] = ",".join(breakdown)
        result = self._pages(f"{target_id}/insights", params, max_pages)
        for row in result["data"]:
            if not isinstance(row.get("name"), str) or row["name"] not in names:
                raise MetaError("insights response contains an unrequested metric")
            if not isinstance(row.get("values"), list) or any(not isinstance(value, dict) for value in row["values"]):
                raise MetaError("insights response lacks verifiable values")
        return {"target_id": target_id, "metrics": names, **result}

    def content_publishing_limit(self):
        """Read business publishing limit (Instagram) without writes."""
        if self.platform is not Platform.INSTAGRAM:
            raise MetaError("publishing_limit.get solo aplica a Instagram")
        self.identity()
        payload = self.request("GET", f"{self.account_id}/content_publishing_limit")
        if not isinstance(payload, dict):
            raise MetaError("publishing limit response lacks valid structure")
        return payload

    def comments(self, target_id, max_pages=10):
        self.content(target_id)
        fields = "id,message,from,is_hidden" if self.platform is Platform.FACEBOOK else "id,text,from,hidden"
        return self.listing(target_id, "comments", fields=fields, max_pages=max_pages)

    def reactions(self, target_id, max_pages=10):
        if self.platform is not Platform.FACEBOOK:
            raise MetaError("reactions documented only for Facebook")
        self.content(target_id)
        return self.listing(target_id, "reactions", fields="id,name,type", max_pages=max_pages)

    def subscriptions(self):
        self.identity()
        if self.platform is not Platform.FACEBOOK or self.token_mode != "facebook_page":
            raise MetaError("subscribed_apps is implemented for Page with Page token; does not enable Instagram Login or messaging")
        app_id = meta_id((self.brand.cuentas.get("facebook") or {}).get("app_id"))
        rows = self.listing(self.page_id, "subscribed_apps", fields="id,subscribed_fields")
        if not rows["complete"]:
            raise MetaError("suscripciones incompletas")
        matches = [r for r in rows["data"] if r.get("id") == app_id]
        if len(matches) > 1:
            raise MetaError("ambiguous subscription")
        fields = matches[0].get("subscribed_fields") if matches else []
        if not isinstance(fields, list) or any(not isinstance(f, str) for f in fields):
            raise MetaError("subscription fields unverifiable")
        return {"id": self.page_id, "app_id": app_id, "subscribed_fields": sorted(fields)}

    def mentioned_comment(self, comment_id, media_id):
        if self.platform is not Platform.INSTAGRAM or self.token_mode != "facebook_user":
            raise MetaError("mentions requires Instagram Facebook Login with USER token and comment permissions")
        self.identity()
        meta_id(comment_id)
        meta_id(media_id)
        result = self.request("GET", self.account_id, params={"fields": f"id,mentioned_comment.comment_id({comment_id}){{id,text,media{{id}}}}"})
        mention = result.get("mentioned_comment")
        if (result.get("id") != self.account_id or not isinstance(mention, dict) or mention.get("id") != comment_id
                or (mention.get("media") or {}).get("id") != media_id or not isinstance(mention.get("text"), str)):
            raise MetaError("exact mention of selected account was not verified")
        return {"id": comment_id, "media_id": media_id, "text": mention["text"]}

    def validate_action(self, edit):
        edit = validate_edit(edit)
        ig = self.platform is Platform.INSTAGRAM
        if edit.action in {"media-update", "media-delete", "mention-reply"} and not ig:
            raise MetaError("Instagram-only action")
        if ig and edit.action in {"profile-update", "post-update", "video-update", "post-delete", "video-delete", "like", "unlike", "schedule-reprogram", "schedule-cancel"}:
            raise MetaError("action undocumented for Instagram; content is not deleted/recreated for editing")
        self.identity(user_required=edit.action == "media-delete")
        if edit.action.startswith("subscription-"):
            if ig or self.token_mode != "facebook_page" or edit.target_id != self.page_id:
                raise MetaError("subscription requires selected Page and its Page token")
            if edit.subscribed_fields is not None and set(edit.subscribed_fields) - {"feed"}:
                raise MetaError("only feed is enabled; messaging/leadgen/other products are not activated")
        return edit

    def snapshot(self, edit):
        edit = self.validate_action(edit)
        if edit.action.startswith("subscription-"):
            return self.subscriptions()
        if edit.action == "mention-reply":
            if edit.target_id != self.account_id:
                raise MetaError("mention must target selected Instagram account")
            return self.mentioned_comment(edit.comment_id, edit.media_id)
        if edit.action == "schedule-reprogram":
            content = self.content(edit.target_id, "post")
            if content.get("is_published"):
                raise MetaError("published content cannot be rescheduled")
            if not isinstance(content.get("scheduled_publish_time"), int):
                raise MetaError("publication schedule cannot be verified")
            return content
        if edit.action == "schedule-cancel":
            content = self.content(edit.target_id, "post")
            if content.get("is_published"):
                raise MetaError("published content cannot be cancelled")
            if not isinstance(content.get("scheduled_publish_time"), int):
                raise MetaError("publication schedule cannot be verified")
            return content
        if edit.action == "profile-update":
            if edit.target_id != self.account_id:
                raise MetaError("profile belongs to another account")
            return self.profile(list(edit.patch))
        kind = "video" if edit.action.startswith("video-") else None
        content = self.content(edit.target_id, kind)
        if edit.action == "comment-moderate":
            result = self.comments(edit.target_id)
            rows = [r for r in result["data"] if r.get("id") == edit.comment_id]
            hidden = "is_hidden" if self.platform is Platform.FACEBOOK else "hidden"
            if len(rows) != 1 or type(rows[0].get(hidden)) is not bool:
                raise MetaError("comment/moderation status not verified on owned media")
            return {"id": edit.comment_id, "hidden": rows[0][hidden]}
        if edit.action in {"like", "unlike"}:
            result = self.listing(edit.target_id, "likes", fields="id")
            if not result["complete"]:
                raise MetaError("incomplete likes; absence not inferred")
            return {"id": edit.target_id, "liked_by_page": any(r.get("id") == self.page_id for r in result["data"])}
        if edit.patch:
            if set(edit.patch) - set(content):
                raise MetaError("resource did not return all fields to be edited")
            return {"id": edit.target_id, **{k: content[k] for k in edit.patch}}
        return content

    def expected(self, edit, before):
        if edit.action == "comment-moderate":
            return {**before, "hidden": edit.hide}
        if edit.action in {"like", "unlike"}:
            return {**before, "liked_by_page": edit.action == "like"}
        if edit.action == "schedule-reprogram":
            return {**before, "scheduled_publish_time": edit.scheduled_publish_time}
        if edit.action.startswith("subscription-"):
            return {**before, "subscribed_fields": sorted(edit.subscribed_fields or [])}
        return {**before, **(edit.patch or {})}

    def mutate(self, edit):
        edit = self.validate_action(edit)
        if edit.action in {"profile-update", "post-update", "video-update", "media-update"}:
            data = {k: (str(v).lower() if type(v) is bool else v) for k, v in edit.patch.items()}
            result = self.request("POST", edit.target_id, data=data)
        elif edit.action in {"post-delete", "video-delete", "media-delete"}:
            result = self.request("DELETE", edit.target_id)
        elif edit.action == "schedule-reprogram":
            result = self.request("POST", edit.target_id, data={"scheduled_publish_time": edit.scheduled_publish_time})
        elif edit.action == "schedule-cancel":
            result = self.request("DELETE", edit.target_id)
        elif edit.action == "comment-moderate":
            field = "is_hidden" if self.platform is Platform.FACEBOOK else "hide"
            result = self.request("POST", edit.comment_id, data={field: str(edit.hide).lower()})
        elif edit.action in {"like", "unlike"}:
            result = self.request("POST" if edit.action == "like" else "DELETE", f"{edit.target_id}/likes")
        elif edit.action.startswith("subscription-"):
            result = self.request("POST" if edit.action == "subscription-set" else "DELETE", f"{edit.target_id}/subscribed_apps",
                                  data={"subscribed_fields": ",".join(edit.subscribed_fields)} if edit.subscribed_fields else None)
        elif edit.action == "mention-reply":
            result = self.request("POST", f"{self.account_id}/mentions", data={"media_id": edit.media_id, "comment_id": edit.comment_id, "message": edit.message})
            try:
                return {"id": meta_id(result.get("id")), "accepted": True}
            except MetaError:
                raise MetaUncertain("mention reply lacks ID; do not repeat") from None
        else:
            raise MetaError("handler not yet implemented")
        if result.get("success") is not True:
            raise MetaUncertain("Meta did not confirm success=true; write will not be repeated")
        return {"success": True, "target_id": edit.target_id}
