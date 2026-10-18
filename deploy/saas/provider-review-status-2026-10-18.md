# Provider review status — October 18, 2026

These observations come from the current provider consoles. They supersede older
unchecked review steps; they do not establish approval.

| Provider | Verified status | Next required action |
| --- | --- | --- |
| Google | Data access under review; submitted form received. Homepage and branding reviewed October 17. Privacy, functionality, appropriate access and minimum scopes remain in progress. | Wait for Google's decision or concrete request; check again October 21. No automated follow-up is installed. |
| Meta | Current App Review draft says **Not submitted**; submission disabled. Twelve requested privileges still show **Get started** in Allowed usage; `public_profile` is complete. | Complete each permission's description, matching recording, successful API evidence and usage certification, then review data handling and reviewer instructions. |
| TikTok | Production configuration is **Draft**. Direct Post is off. | Compare existing recordings against the current SaaS flow and record only missing evidence before submission. |

## Google

The saved scope justification and YouTube demo are present:
`https://youtu.be/cjAxlw1VyxU`. Sensitive scopes remain unverified:
`youtube.readonly`, `youtube.upload`, `youtube.force-ssl`. The declared
non-sensitive scope is `yt-analytics.readonly`; there are no restricted scopes.
Do not request another recording solely because October 16 notes predate it.

[Verification center](https://console.cloud.google.com/auth/verification?project=socialcli)
and [declared scopes](https://console.cloud.google.com/auth/scopes?project=socialcli).

## Meta

The developer MCP requirements endpoint reports a previous submission in review,
but the actual current App Review UI shows an incomplete draft and disabled
submission. Retain both observations; do not report this draft as submitted.
`instagram_manage_contents` is included among the thirteen requested privileges.
Its presence does not establish approved advanced access.

Data handling contains prefilled responses identifying Florentino.pro as
controller and Google Drive as the encrypted backup processor. Responses still
require review against actual implementation. The current app-level webhook
subscription snapshot is empty. A local receiver test cannot certify delivery
until the hosted endpoint and exact subscription are installed and a genuine
event traverses owner-bound storage and consumption.

## TikTok

Current scopes are `user.info.basic`, `user.info.stats`, `video.list` and
`video.upload`. The supported posting mode sends a draft for creator completion
inside TikTok. The configured callback is
`https://pilot.socialcli.app/oauth/tiktok/callback`.

Three earlier demo recordings are present. The saved review description itself
says that they show an earlier version and need comparison with the current
SaaS flow. A prior rejection is historical; it is not a decision on this draft.
Do not enable or advertise Direct Post to resolve an inbox-upload review.

## Evidence boundary

Private operational records hold screenshots, exact draft identifiers and the
read-only observations. This review changed no provider permissions, submitted
no assessment, contacted no support and made no social publication. Successful
live QA variants remain excluded from repeat certification.
