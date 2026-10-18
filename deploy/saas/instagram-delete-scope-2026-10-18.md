# Instagram deletion scope correction — local candidate

The existing deletion handler requires a Facebook User token with instagram_basic
and instagram_manage_contents, but management OAuth did not request the latter.
The last saved live grant lacked it. The SocialCli Meta console was inspected on
October 18: the permission is already added and marked Ready for testing, not
approved for production. No console permission was added or removed.

Providers.scopes now requests instagram_manage_contents for Instagram management
consent only. Ordinary publishing and analytics-only consent stay unchanged.
Permission validation refuses an incomplete management grant before account
discovery. Existing connections are not silently expanded.

Three focused cases failed before the correction. After it, 127 focused tests and
the full suite of 3,172 tests passed. There is one pre-existing Starlette/httpx
deprecation warning. A separate read-only review found no actionable findings.
No passed live-provider tests were repeated and no social content was written.

This is a local source correction, not a deployed or granted permission claim.
The SaaS selection route currently replaces the user access token with the selected
Page token, while MetaClient defaults to Page mode. Therefore the full deletion
fix also needs an operation-specific User-token route, deployment, owner consent
and a real deletion test. The remaining-QA plan tracks these independently.

References:
- https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media
- [Remaining QA plan](../../docs/superpowers/plans/2026-10-18-remaining-live-qa.md)
