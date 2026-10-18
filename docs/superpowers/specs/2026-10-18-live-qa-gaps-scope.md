# Remaining live QA scope

Requested on 18 October 2026. This is an evidence and diagnostic brief for existing
SocialCli functions, not approval to invent new product features.

## User requirements

- Use Superpowers and Context7 to plan resolution of every failed, inconclusive or
  unexercised real-provider function.
- Do not repeat successful live tests. Reuse their evidence. A previously verified
  function may only be used as necessary fixture setup, verification or cleanup,
  not as another certification exercise.
- For every remaining function distinguish documented permission requirements,
  requested OAuth scopes, observed grants, token type, provider review/eligibility,
  implementation defects and missing test fixtures.
- Use Histopast and restore originals; retain the original uncertain journals.
- The user separately authorized adding `instagram_manage_contents` during planning.
- Evaluate Meta Social Technologies MCP as an administrative tool.

## Completion rule

A real pass requires a named method/field/variant, the intended provider effect,
independent readback where supported, and verified restoration or fixture cleanup.
Acceptance and unverifiable readback are different facts. An API limitation needs
current method-specific official evidence; a 403 alone is not a universal ban.
Every unfinished row needs a concrete dependency and next action. Do not call a
documented blocker a live pass or claim 100% coverage with exceptions outstanding.

## Baseline

The catalog contains 163 records, 107 enabled. The private October 17 matrix has
later corrections recorded in the October 18 closeout; it is not a current verdict
by itself. rc13 passed 3,170 automated tests and was deployed to the hosted service.
Protected package publication and actual Muse adoption require fresh verification.
Successful Story publication/listing, Reporting job CRUD, four temporary uploads,
playlist CRUD/membership and historical Facebook insights must not be reopened.
Facebook reactions listing has a saved complete empty response and is excluded.

## Important current findings

- Meta's official SDK includes Page `bio` in `api_update`; missing baseline is
  still unknown, not empty. Never substitute `about` for `bio` silently.
- Facebook rejected hiding the Page's own temporary comment; a universal ban on
  own comments is not established. Diagnose eligibility separately from grants.
- Instagram media deletion requires a Facebook User token with `instagram_basic`
  and `instagram_manage_contents`. The latest saved grant lacks the latter.
- The actual SocialCli Meta console shows `instagram_manage_contents` as
  **Ready for testing**. The permission is already added there, not production-approved.
  The local management OAuth scope request was corrected during this planning task.
- YouTube Analytics group creation returned 403 even though a documented scope
  alternative (`yt-analytics.readonly`) appears in the saved grant. Diagnose request,
  identity and eligibility before proposing broader scopes.
- YouTube Reporting needs generated data; allow up to 48 hours before declaring
  the first report unavailable. Do not delete the job immediately after creation.
- TikTok has inbox and direct adapter branches. The hosted OAuth request currently
  includes `video.upload`, not `video.publish`, but the latest saved TikTok grant
  does include both. Do not classify the existing grant as missing video.publish.
  Inbox completion remains manual; direct publication still requires verified
  provider configuration, audit status and creator UX.
- Meta webhook helpers are offline verification/ingestion boundaries, not a
  deployed public listener. A fabricated signed fixture is not real delivery proof.
- The current SaaS selection path overwrites the user access token with the
  selected Page token (`socialctl/saas/connections.py`), and `MetaClient` defaults
  to `facebook_page`. Adding the OAuth scope alone does not provide the user-token
  route required for Instagram deletion. Resolve this in Task 2 before a live attempt.

## Constraints

All product files and documentation are English. Evidence with creator IDs, media,
credentials or provider receipts stays in the private brand workspace. New automated
tests use fictional accounts. Never generate/crop/reencode media. Never submit a
false abuse report or change truthful audience classification to manufacture coverage.
Do not contact platform support. Preserve existing account grants and approvals;
new exact previews need approval under the project's standing rules.

## Sources checked

Context7: official YouTube, Graph API, Instagram Platform and TikTok libraries.
The alternate project credential was used privately after the primary quota limit.
Retrieval can return stale/unrelated excerpts; verify the actual method, login type
and version. In particular an old User-node `bio` deprecation is not Page evidence.

- https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py
- https://developers.facebook.com/docs/graph-api/reference/comment/
- https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media
- https://developers.google.com/youtube/analytics/reference/groups/insert
- https://developers.google.com/youtube/reporting/v1/reports
- https://developers.tiktok.com/docs/en/content-posting-api-reference-upload-video
- https://developers.tiktok.com/docs/en/content-posting-api-get-started
- https://developers.tiktok.com/docs/en/content-sharing-guidelines
- https://developers.facebook.com/documentation/mcp/devtools-mcp

