# Agent permission boundaries — verified 14 October 2026

Status: fine-grained action enforcement remains open. The candidate now implements
owner-selected SocialCli connection platforms per installation, enforced by the
credential broker with schema7 and explicit legacy approval migration. This does
not attenuate provider scopes or prove provider approval.

## Current implementation

`socialctl/saas/pairing.py` binds a human-approved installation to its owner.
`socialctl/saas/connections.py` binds capabilities to installations, grants and
accounts, and checks commercial entitlement before issuing provider access tokens.
Its `/connections/{connection_id}/token` response exports `access_token` and actual
`granted_scopes`. `socialctl/connection_service/providers.py` requests provider
scope bundles, adding management and analytics only when selected.

These controls restrict which installation can obtain which connection. They do
not prevent a client that already holds a provider token from invoking any endpoint
allowed by that token. CLI approval fingerprints protect SocialCli workflows; they
cannot constrain a separate HTTP client using the same bearer token. Revoking an
installation stops future broker access but does not prove immediate invalidation
of previously exported provider tokens. Provider-level revocation is a separate
operation, with potentially wider effects on other installations.

## Provider evidence that changes the design

Documentation was resolved and queried through Context7; the official YouTube
comment reference was also opened directly because retrieval omitted its scope.

| Requested boundary | Verified evidence | Consequence |
| --- | --- | --- |
| YouTube upload without video deletion | videos.insert accepts youtube.upload; videos.delete lists youtube, youtube.force-ssl or youtubepartner, excluding youtube.upload. | A token whose actual scopes remain read/upload only can provide a provider-enforced distinction for those endpoints. Do not infer its scopes from requested consent options. |
| YouTube reply without video deletion | comments.insert requires youtube.force-ssl, also accepted by videos.delete. | Exporting that token cannot enforce reply-only access. Replies need a server-controlled operation path if delete must remain forbidden. |
| YouTube upload without exporting any provider bearer | The resumable-upload guide documents Authorization on binary PUT requests. | Do not assume the session URL alone provides an officially supported credential-free upload. Prove a supported upload-specific mechanism before making this claim. |
| Facebook publishing/management | The Graph API exposes destructive operations under publishing/engagement permissions; pages_manage_posts permits deleting milestones. PagePost deletion has separate eligibility limitations. | These are permission bundles, not an established universal publish-only security boundary. Do not advertise delete denial from a local toggle. |
| Instagram moderation | IG Comment supports hide/unhide and deletion alongside replies; the app currently requests instagram_manage_comments through Facebook Login. | Endpoint-by-endpoint provider scope/eligibility verification is required before advertising reply-only grants. A disabled CLI command does not establish enforcement. |
| TikTok creator review versus direct publication | Inbox upload and Direct Post are distinct flows; Direct Post requires approved video.publish, user consent and relevant client audit. | Keep creator review separate from direct publication. video.upload does not prove unattended direct-publication eligibility. |

Sources:
- https://developers.google.com/youtube/v3/docs/videos/insert
- https://developers.google.com/youtube/v3/docs/videos/delete
- https://developers.google.com/youtube/v3/docs/comments/insert
- https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- https://developers.facebook.com/docs/graph-api/reference/milestone
- https://developers.facebook.com/docs/graph-api/reference/page-post
- https://developers.facebook.com/docs/graph-api/reference/object/comments
- https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-comment
- https://developers.facebook.com/documentation/instagram-platform/private-replies
- https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post

## Completion requirements

1. Owner-defined permissions must be enforced outside the agent's editable client,
   bound to installation, account and operation. Unknown/missing policy denies.
2. Broad provider grants must stay server-side for restricted metadata operations.
   Use fixed typed operations, not a generic credentialed HTTP proxy. Recheck
   ownership, entitlement and policy immediately before effects and retain
   uncertainty/idempotency journals without replaying unknown writes.
3. Direct media transfer remains client to platform. No upload relay or hosted
   customer media is introduced implicitly to solve authorization. An upload path
   requiring a broad exported token cannot claim incompatible restrictions.
4. Any provider-enforced narrow token path must verify actual granted scopes,
   including renewed tokens and previously expanded authorization. Shared broad
   grants cannot silently supply a restricted installation. Migration and separate
   grant ownership must preserve other approved installations.
5. Human UI and machine-readable capabilities must show exact supported boundaries
   and distinguish pending revocation, broker revocation and provider revocation.
   Reuse shared UI components and the owner's explicit selection.
6. Verify denied calls directly against service endpoints, not just CLI guards;
   cover cross-installation/account access, refresh/upgrade, existing tokens,
   concurrent policy changes and per-operation partial failure. Real provider
   pilots are required to substantiate provider-enforced restrictions.

Do not mark critique point7 complete from this research. Local permission flags,
UI selections or tests of cooperative CLI behavior cannot satisfy these requirements.
