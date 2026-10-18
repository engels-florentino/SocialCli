# Capabilities, permissions and limits

SocialCli SaaS candidate · implementation scope, October 14, 2026.

A supported command is not proof of access. Provider app eligibility/review,
creator consent, token grants, actor role and object eligibility all matter.
Adding permissions in a developer dashboard does not add them to existing tokens;
reconnect with the requested optional access and have the creator consent.
`--analytics` and `--management` request separate optional permissions.

| Action | SocialCli support | Required access and identity | Object and verification limits |
|---|---|---|---|
| Connect accounts | YouTube, Facebook, Instagram, TikTok through configured service | Creator OAuth consent; exact account binding; provider app eligible for this creator | Sandbox/Testing/development restrictions still apply. Agent hands off link, never opens it. |
| Account/content metrics | Provider-specific `stats`; YouTube Analytics optional | YouTube read/Analytics grants; Facebook `read_insights` and Page read access; Instagram `instagram_manage_insights` plus basic/Page access; TikTok authorized user/video read grants | Metrics vary by account/content and date. Unsupported metrics are null, not zero. Aggregate partial coverage exits 3. |
| Unified comment inbox | Owned YouTube uploads, Facebook Page posts, Instagram media | YouTube authenticated channel/read access; Facebook Page read/user-content grants; Instagram basic/comment grants and linked Page/professional account | Published YouTube threads/replies; Meta top-level/direct replies only. Provider visibility, page limits and budgets apply. TikTok inbox comments unsupported. |
| Add/reply to comments | YouTube and Meta exact supervised proposals | YouTube `youtube.force-ssl`; Facebook Page identity with `pages_manage_engagement` and applicable read grants; Instagram `instagram_manage_comments`, basic/Page grants | Exact actor and media/parent verification required. Missing pagination metadata never proves absence. Unknown previous writes block repeats. |
| Facebook like/unlike | Owned Page posts and their top-level comments through Meta management | Connected Page token, `pages_manage_engagement`, applicable Page task and read access | For comment likes, target_id identifies the owned post and comment_id identifies its top-level comment. Exact membership and Page viewer like state are required. Post likes retain the existing owned-content traversal. Arbitrary-profile actions and nested comment likes are not exposed. HTTP 400 alone does not identify a missing permission. |
| YouTube video rating | Like/dislike/none through community proposal | Authenticated channel with `youtube.force-ssl`; eligible video | A video rating is distinct from a comment like/heart. |
| Comment likes/hearts | Not exposed for YouTube, Instagram or TikTok; Facebook top-level comment likes supported as above | Additional permissions cannot create an unimplemented endpoint | Do not substitute video ratings, comment replies or a guessed endpoint. |
| Publish content | Existing provider adapters with full preview and explicit approval | YouTube upload grants; Facebook `pages_manage_posts` plus Page access; Instagram `instagram_content_publish` plus basic/Page access; TikTok `video.upload` for inbox | Creator-supplied media only. SaaS Instagram can stage originals on florentino.pro for15 minutes; independent hosts remain supported. TikTok inbox handoff is not public publication. Direct Post is disabled and excluded regardless of legacy audit settings; creator-reviewed inbox upload remains supported. Production approval is not established. |
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
For aggregate stats/inbox/report, exit 0 is complete, 3 is partial, 1 is failure, and 2 is an invalid invocation. Other read commands retain their documented exit contracts. Parser errors
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

## Facebook comment likes

Use explicit quoted IDs in a Meta proposal. Do not pass a comment ID as
`target_id`: that field is the owned Page post; `comment_id` selects its comment.

```yaml
action: like
target_id: "123_789"
comment_id: "789_800"
```

```bash
socialcli meta prepare --file like-comment.yaml --platform facebook --brand MyBrand --dry-run
```

Review the full saved proposal and obtain approval before applying. The preview
includes the comment text, media ID and observed Page like state. An unavailable
`has_liked` state or unverified target stops preparation. Use `action: unlike`
for a separately approved removal. Distinct comments on the same post can be
children of one community batch; duplicate targets are rejected.

`inbox` shares its total timeout among configured supported platforms so one
slow provider cannot consume the entire budget. Meta enumeration requests only
IDs and ownership; later request failures retain earlier verified media.
A partial report with zero observed comments does not establish an empty inbox.
YouTube `stats` retains fetched account/video metrics when optional enrichment
or audience reads fail, records warnings, and reports partial JSON coverage.

Unknown Meta authors are represented by `author_id: null`; coverage counts them as
`unknown_author`. This preserves visible comments without attributing them to a
person or proving own authorship. Own-comment edit/delete checks remain strict.
YouTube `comments_disabled` means the video disables comments, not that the
account needs to reconnect.

## Facebook comment IDs and deletion receipts

Facebook may return a video-scoped comment ID from creation and a different
post-scoped ID in the owned video's comments edge. Follow-up reads and proposals
resolve this only through the exact direct response and one matching specific
official comment permalink, with consistent author, text and target. The complete
preview shows the observed canonical target; matching numeric suffixes alone is
never sufficient.

A successful deletion receipt is distinct from independent proof of absence.
Meta video deletion retains `accepted_unverifiable`; comment deletion retains
`applied_unverified`. The output diagnostic `operation_accepted_unverified` means
the provider accepted the request. Preserve the receipt and do not repeat the
write. A generic missing/inaccessible object response is not definitive deletion
evidence. Comment apply still exits 1 when the effect cannot be independently
verified; agents must inspect the status and diagnostic instead of reporting a
provider rejection or repeatedly reconciling an inherently unverifiable result.

References: [Meta comments](https://developers.facebook.com/docs/graph-api/reference/object/comments/),
[Meta Comment](https://developers.facebook.com/docs/graph-api/reference/comment/).


## Agent execution and preflight

Commands with exact proposals accept `--approval-fingerprint` (`--approval` and
`--approval-digest` aliases) or explicit `--approval-stdin`. Without a terminal
or explicit approval, they fail before reading stdin. Publish and local schedule
also accept approved preview digests; their existing `--yes` workflow remains.
Each fresh preparation can create a new proposal ID and fingerprint. Keep them
together and never reuse an approval for another proposal.

`doctor` / `preflight` accept `--post`, repeatable `--media`, and exact
`--require-scope` with `--platform`. They combine credential, media, account and
stored-scope observations without requesting new permissions. Unknown scopes
remain unknown; no probe proves permission to write. TikTok identity probing
compares the provider open_id with the configured account using a read-only request. Use `--post` to validate a
specific publishing route, including Instagram JPEG requirements.

Noninteractive aggregate reads emit one final request-count/time summary on
stderr. Interactive terminals retain updates at most every five seconds.
Stdout JSON remains separate and final coverage determines success.
## Resumable batches and reply context

Community batches remain sequential, resumable operations, not atomic cross-provider transactions. Each child must verify before the next starts. A received comment receipt now permits one additional bounded, read-only reconciliation during apply, covering delayed provider readback without another POST. A lost receipt, incomplete evidence or contradictory identity still stops the batch. Reconciliation never starts pending children.

Batch JSON preserves existing keys and adds all children to `outcomes`, including `not_attempted`, with child index, provider, platform and change ID. `summary` includes total and verified/pending/uncertain/blocked counts and ID lists. A partial batch with only untouched children reports `batch_pending` and `action: apply_remaining`; an uncertain child reports `operation_uncertain` and `action: reconcile`. Continue the same approved batch with its exact fingerprint, retaining all verified outcomes. Progress is on stderr; stdout contains one JSON document.

`inbox-draft` errors include `failed.child_index` (one-based), platform, account/comment IDs, stage and a safe reason. Completed immutable children remain in `prepared` after a later failure. Diagnostics do not reproduce arbitrary provider or storage exception text.

Facebook reply preparation accepts an unavailable external comment author when exact comment membership and media ownership can be verified. The author remains null in the approved baseline. Changes to text, identity or membership block the write. Own-comment edit/delete and created-reply verification still require the authenticated account's exact author ID.

A nested observation can explicitly map a new thread reply to its observed top-level parent:

```yaml
version: 1
replies:
  - platform: youtube
    account_id: fictional-channel
    comment_id: observed-reply-id
    reply_parent_id: observed-top-level-comment-id
    text: Exact supplied reply text
```

The source and mapped parent must both be observed for the same account/media/thread. The parent must be top-level and satisfy the existing eligibility rules. Source membership/text/author are read again during preparation and immediately before the write. The full preview binds `before.reply_context` and the actual write parent. This is supported through the existing YouTube, Facebook and Instagram reply edges; it does not invent a deeper provider nesting relationship or imply a mention. Multiple selections mapping to the same parent are rejected. Existing replied/uncertain parent protections remain in effect. Inbox review links the resulting journal to both the parent and selected source observation.
