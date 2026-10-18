# SaaS capabilities and permissions

Audit: 2026-10-14. Client tested: 0.3.0rc1. A supported implementation, a
developer-app permission, user consent and production approval are separate gates.
No scope alone proves that a feature works or authorizes a content change.

| Platform | Implemented scope | Required consent | Current verification and limits |
| --- | --- | --- | --- |
| YouTube | Channel/video reads, uploads, analytics; scoped metadata, comments and resource management; native video scheduling | youtube.readonly + youtube.upload; yt-analytics.readonly for analytics; youtube.force-ssl for management | Histopast identity, metrics and inventory passed. The initial test omitted force-ssl and comment reads failed. Management re-consent completed on 2026-10-14. A second owned video returned one complete comment thread; the first still returned 403, so per-video eligibility is not assumed. No live write/schedule test. Google remains Testing. |
| Facebook Pages | Identity, inventory, metrics; supported feed/photo/video publishing, metadata and comments, selected Page likes | pages_show_list, pages_read_engagement, pages_manage_posts; read_insights; pages_read_user_content, pages_manage_engagement, pages_manage_metadata for management | Histopast identity, metrics and inventory passed. All listed scopes were granted. Three comment reads had incomplete verifiable fields; more scopes do not prove a fix. Reel/Story and native scheduling routes must obey their capability gates; do not advertise blanket support. |
| Instagram professional | Identity, inventory, insights; media publishing; comment reads/replies/deletion and story reads; eligible media deletion | pages_show_list, pages_read_engagement, instagram_basic, instagram_content_publish; instagram_manage_insights; instagram_manage_comments, instagram_manage_contents and pages_manage_metadata for management | The earlier live grant did not include instagram_manage_contents. Management authorization now requests it; existing connections require explicit renewed consent. Media deletion also requires a Facebook User access token, not a Page token. App Review, granted permissions, token routing and live deletion remain separate checks. Caption/profile editing and native API scheduling are not offered by this implementation. Media publishing requires eligible reachable HTTPS media supplied by the customer. |
| TikTok | Account/metrics reads and creator-reviewed draft upload | user.info.basic + video.upload; user.info.stats + video.list for metrics/video reads | Existing sandbox target Histopast connected; metrics passed. The actual test grant includes stats/list scopes, and the corrected hosted OAuth request explicitly requests basic/upload/stats/list. Missing metrics consent is rejected on new connections; older grants retain their refresh contract. content sync and comment inbox are unsupported. Direct Post is disabled; a video.publish grant is not production approval. |

## Product boundaries

- No YouTube comment likes/hearts/pinning, cards/end screens or community polls
  through this CLI. See exact disabled rows in capabilities.yml.
- No TikTok comment management or automatic native future-time API publication.
- Instagram/TikTok native platform scheduling must not be represented as an
  implemented API feature. Facebook routes are gated by format and verified support.
- Customer media is not stored by the SaaS. Local scheduling needs a persistent
  customer-owned executor; a fresh ephemeral agent session cannot guarantee it.
- Read reports preserve missing/partial information. No empty partial inbox claim.
- Publishing, replies, deletion and scheduling require the exact preview and
  owner approval; this audit performed none of those writes.

## Configuration follow-up

1. YouTube management re-consent and one complete thread read verified. Investigate the remaining per-video 403 without assuming a missing global scope.
2. TikTok OAuth request fixed; sandbox scopes saved and production scopes saved as Draft. Deployed request verified with all four scopes; existing Histopast consent already includes them. A genuinely new-user consent is not yet tested.
3. Inspect developer consoles against this matrix; maintain Google Testing and
   TikTok Production Draft. Do not submit a provider review without instruction.
4. Keep unsupported capability gates closed; do not request unrelated ad, messaging
   or business-management permissions to compensate for missing implementation.

## Sources

- Code: socialctl/connection_service/providers.py, socialctl/capabilities.yml,
  socialctl/adapters and socialctl/metricas. Registry counts are not a complete
  product checklist (for example TikTok metrics/upload live outside its sole row).
- [YouTube comment authorization](https://developers.google.com/youtube/v3/docs/commentThreads/list)
- [YouTube comments guide](https://developers.google.com/youtube/v3/guides/implementation/comments)
- [TikTok Login Kit](https://developers.tiktok.com/docs/en/login-kit-web)
- [Instagram with Facebook Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/business-login-for-instagram)

Live sanitized evidence is stored in the operator acceptance workspace, not in
the public repository; no tokens or customer comment contents belong here.
