# Capabilities, permissions and limits

SocialCli 0.2.3 · verified implementation scope, October 10, 2026.

A supported command is not proof of access. Provider app eligibility/review,
creator consent, token grants, actor role and object eligibility all matter.
Adding permissions in a developer dashboard does not add them to existing tokens;
reconnect with the requested optional access and have the creator consent.
`--analytics` and `--management` request separate optional permissions.

| Action | SocialCli support | Required access and identity | Object and verification limits |
|---|---|---|---|
| Connect accounts | YouTube, Facebook, Instagram, TikTok through configured service | Creator OAuth consent; exact account binding; provider app eligible for this creator | Sandbox/Testing/development restrictions still apply. Agent hands off link, never opens it. |
| Account/content metrics | Provider-specific `stats`; YouTube Analytics optional | YouTube read/Analytics grants; Facebook `read_insights` and Page read access; Instagram `instagram_manage_insights` plus basic/Page access; TikTok authorized user/video read grants | Metrics vary by account/content and date. Unsupported metrics are null, not zero. Partial coverage exits 1. |
| Unified comment inbox | Owned YouTube uploads, Facebook feed, Instagram media | YouTube authenticated channel/read access; Facebook Page read/user-content grants; Instagram basic/comment grants and linked Page/professional account | Published YouTube threads/replies; Meta top-level/direct replies only. Provider visibility, page limits and budgets apply. TikTok inbox comments unsupported. |
| Add/reply to comments | YouTube and Meta exact supervised proposals | YouTube `youtube.force-ssl`; Facebook Page identity with `pages_manage_engagement` and applicable read grants; Instagram `instagram_manage_comments`, basic/Page grants | Exact actor and media/parent verification required. Missing pagination metadata never proves absence. Unknown previous writes block repeats. |
| Facebook like/unlike | Owned Page posts/videos through Meta management | Connected Page token, `pages_manage_engagement`, applicable Page task and read access | Current CLI verifies owned content and full relevant likes traversal. It does not expose a general comment-like command or arbitrary-profile actions. HTTP 400 alone does not identify a missing permission. |
| YouTube video rating | Like/dislike/none through community proposal | Authenticated channel with `youtube.force-ssl`; eligible video | A video rating is distinct from a comment like/heart. |
| Comment likes/hearts | Not exposed for YouTube, Instagram or TikTok; Facebook general comment-like command not exposed | Additional permissions cannot create an unimplemented endpoint | Do not substitute video ratings, comment replies or a guessed endpoint. |
| Publish content | Existing provider adapters with full preview and explicit approval | YouTube upload grants; Facebook `pages_manage_posts` plus Page access; Instagram `instagram_content_publish` plus basic/Page access; TikTok `video.upload` for inbox | Creator-supplied media only. Instagram downloads require accessible media URLs. TikTok inbox handoff is not public publication. Direct Post production UX/review remains separate. |
| Exact source link | Explicit version 1 post-slug map, included in preview | Same publication/comment grants as its placement | YouTube/Meta follow-up comment; TikTok caption. No automatic pinning; Instagram/TikTok clickability not promised. Changed maps invalidate pending mapped intent. |
| Community batch | YouTube reply/rating, Meta add/reply, Facebook owned-content like/unlike | One exact batch digest approved by creator; each child still verifies grants/actor/target | Ordered immutable list, sequential stop on failure/uncertainty; no transaction or rollback. Reconcile never starts pending children. |
| Disconnect | Local capability removal and service connection deletion | Exact selected brand/platform | Service disconnection is not provider-side revocation. Revoke app access in provider settings when required. |

Required permissions above describe the supported integration, not a claim that
any connected account currently holds them. Meta Business Manager roles and
features can impose additional requirements. External creators require the
provider's applicable Advanced Access, app verification/review or production
approval. The hosted pilot status is documented in [connections](connections.md).
Public GitHub availability does not establish production approval.

## Results and recovery

`stats --json` and `inbox --json` produce version 1 reports, with progress on stderr.
Exit 0 is complete; 1 is partial/failed; 2 is an invalid invocation. Parser errors
use standard CLI usage on stderr. Existing successful JSON schemas remain intact.
Snapshots retain provenance when previous data is reused. Complete watermarks
cover only the exact scope read; missing rows never prove deletion globally.

Direct Meta readback checks returned ID, actor, parent/media and exact text.
No guessed numeric/composite ID transformation or text-only match is proof.
A bounded readback window can leave a successful remote write uncertain. Inspect
its journal and reconcile; do not create another proposal just to repeat it.
Synthetic tests verify these safeguards but do not prove live eventual-consistency
behavior or provider production access.

Authenticated encrypted-file capability storage is an explicit Linux/macOS
option. Use one protected external store per brand, a separate external key,
verified migration and recoverable rotation. Default remains the OS keyring.
Provider application secrets and refresh tokens stay on the shared service.

## Provider references

Contracts were checked using current official documentation during implementation:
[Facebook comment](https://developers.facebook.com/docs/graph-api/reference/comment/),
[Facebook replies](https://developers.facebook.com/docs/graph-api/reference/comment/comments/),
[Instagram comment replies](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-comment/replies/),
[YouTube comments](https://developers.google.com/youtube/v3/docs/comments),
[YouTube video ratings](https://developers.google.com/youtube/v3/docs/videos/rate),
[TikTok app review](https://developers.tiktok.com/doc/app-review-guidelines/).
Provider policy and availability can change; these references do not certify a
particular app or account.
