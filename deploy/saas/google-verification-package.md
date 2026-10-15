# Google verification package — prepared October 15, 2026

**Status: draft evidence package, not a submission or approval. Keep External / Testing.**

This package describes the current source changes and the demonstration to record
once they are deployed to the intended review environment. No recording, Google
submission, production audience change or deployment was
performed by preparing this file. Domain ownership was subsequently verified in Search Console. The October 15 console observations remain in
[the readiness review](google-production-readiness-2026-10-15.md); do not treat
local tests as evidence that those console settings or the public deployment
have changed.

## Product and ownership statement

SocialCli is creator-controlled software for connecting the creator's social
accounts, inspecting their content and analytics, preparing changes, and executing
explicitly approved publishing and management actions. The service operator is
Florentino.pro; the public support address is `admin@socialcli.app`.

SocialCli sign-in uses email codes. Google OAuth connects the owner's YouTube
channel; it is not the identity provider for SocialCli email sign-in. An assistant
installation receives a separate capability after the owner reviews it in their
browser. The owner selects the authorized YouTube channel in that browser. Brand
names are local labels, not evidence of channel ownership.

## Scope justification to review with the actual consent screen

The following names share the prefix `https://www.googleapis.com/auth/`.
These are the source request paths, not a declaration that Google has approved
them. Compare them with the live client and Data Access page before submission.

| Scope | Concrete implemented feature and API use | Why narrower access does not provide this feature | Consent context |
| --- | --- | --- | --- |
| `youtube.readonly` | Identify the authenticated channel with `channels.list(mine=true)`; enumerate owned uploads and read video details for inventory, account checks and reports. | Public/API-key reads do not establish the authenticated owner's channel or provide all authorized account data. | Base YouTube connection; exact channel selection follows Google's consent. |
| `youtube.upload` | Upload the creator's supplied video with its approved title, description and privacy using `videos.insert`. | Read-only scope cannot upload. This scope is narrower than requesting full account management solely for uploads. | Included with the base connection because the present connection offers both inventory and upload. Before submission, confirm the demo and product explanation reflect that bundle; a read-only-only offering would require a narrower connection path. |
| `youtube.force-ssl` | Optional owned-video metadata updates and supported community actions, including comment replies, edits and deletions. | Upload/read-only permissions do not authorize the implemented management methods. No full `youtube` or `youtubepartner` scope is requested by this connector. | Requested only when the caller explicitly requests management access; owner reviews the incremental permissions. Each write still requires its own exact preview and approval. |
| `yt-analytics.readonly` | Optional non-monetary channel/video reports from `youtubeanalytics.googleapis.com/v2/reports`, including views, watch time, audience and traffic dimensions when available. | Data API video statistics do not provide these Analytics reports. Monetary/revenue access is not requested. The existing `youtube.readonly` scope is also present. | Requested only for the analytics option, independently from management. |
| `drive.file` | Operator-only encrypted backup repository files, authorized through a separate Desktop client. | Access is confined to the application's relevant files; whole-Drive access is unnecessary. | Not included in customer YouTube authorization. The existing separate Desktop client is in the SAME Google project and is not isolated from same-user project-wide revocation. Migrate and verify backups in a separate project before deploying automated customer revocation. |

Source: `socialctl/connection_service/providers.py` (`Providers.scopes`),
`socialctl/adapters/youtube.py`, `socialctl/metricas/youtube.py` and the owned
YouTube/community management implementations. Method-specific scope evidence:
[uploads](https://developers.google.com/youtube/v3/docs/videos/insert),
[metadata updates](https://developers.google.com/youtube/v3/docs/videos/update),
[comments](https://developers.google.com/youtube/v3/guides/implementation/comments),
[Analytics reports](https://developers.google.com/youtube/analytics/reference/reports/query),
and [Drive file scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth).

## Data flow and assistant conditions

1. The owner signs in by email and accepts the versioned Privacy Policy. The
   unchecked choice is stored only after successful email verification, or through
   an authenticated, CSRF-protected review form. Declining leaves deletion and
   sign-out available, while feature access remains blocked.
2. Before pairing, the owner sees the installation label, platform selection,
   output/token disclosure and a separate unchecked assistant acknowledgment.
   The server stores a version/time receipt on that installation. Existing
   installations without the current acknowledgment must be revoked and linked
   again. Agents must hand the link to the owner and must never accept for them.
3. Google authorization and the selected account are bound to the owner and
   installation. The service encrypts stored credentials. The client receives
   scoped SocialCli capabilities; when allowed, the broker can also deliver
   provider access tokens. A SocialCli platform selection does not reduce the
   permissions carried by an already-issued provider token.
4. The local client calls Google for the requested operation and produces local
   records and output. The customer's assistant may read that output or workspace.
   If the assistant is a third-party service, its provider may receive the data.
   SocialCli does not operate an LLM or automatically route these results to a
   named AI provider.
5. Google data may be used only for the features the owner requests. Do not use it
   for model training or advertising. Human access requires a separately valid,
   specific authorization or applicable policy exception; the pairing checkbox
   is not permission for arbitrary human review. Retention, forwarding, logging,
   training controls and subprocessors must be checked for the exact environment.

| Environment | Conditions before use with Google data | Current evidence and limitation |
| --- | --- | --- |
| Customer-controlled software | Owner controls execution, workspace, logging and access; no unrelated transfers, model training or advertising; only requested features. | The broker enforces owner, installation, platform and consent gates. This does not audit arbitrary programs the owner runs afterward. |
| Third-party AI service or hosted runner | Record the exact provider, product, account tier, region where relevant, effective contract/settings, retention, training prohibition and human-access controls. Obtain contextual owner agreement for that transfer. | The contextual acknowledgment is implemented. No provider-wide certification or completed integration assessment is established by these changes. If settings cannot be verified, the owner must not link that configuration. |
| Unknown/default consumer configuration | Cannot establish the controls above. | Not an approved configuration for a production verification claim. A provider logo or the CLI's ability to run in its terminal is not evidence of compliance. |

Before production submission, attach a dated assessment for each environment
actually offered: responsible reviewer, exact configuration, evidence links,
allowed data/features, retention/deletion handling, and unresolved exceptions.
Do not copy general promises about an AI company into an integration approval.
The source gate can prevent future broker delivery; it cannot retract output,
external chat copies or provider tokens already delivered. The platform must be
represented honestly to Google. The applicable policy requires contextual,
accurate disclosure and constrains uses, transfers and human access; consent
alone does not make every downstream use permitted.
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy).

## English demonstration recording script

Use a dedicated test owner and test channel, supplied test media, and sanitized
fixtures for failures. Do not show tokens, email codes, account secrets or private
creator records. A real upload or remote management change needs a separately
reviewed exact preview and explicit owner approval; this script authorizes none.

| Segment | On-screen actions | Suggested narration / evidence to retain |
| --- | --- | --- |
| 1. Identity and policy | Show the public homepage, Privacy Policy and Terms. Sign in with email. Show the unchecked privacy choice and review gate. | “SocialCli connects accounts owned by the creator. Email sign-in and Google authorization are separate. The owner reviews the policy before features are enabled.” Retain policy version and deployed artifact hash. |
| 2. Decline and existing users | Decline acceptance in a fresh browser test; attempt a feature. Show deletion/sign-out remain available. Use a controlled fixture with an old policy receipt. | “New and existing users must review material policy changes. An assistant credential cannot accept the policy.” Show rejected API request without exposing credentials. |
| 3. Assistant pairing | Request an installation link; owner opens it. Show label, selected platforms and full data/token disclosure. Leave acknowledgment unchecked to show refusal; then let the owner review and select it. | “This grants access only to the chosen SocialCli platforms. Data and provider tokens may reach the selected assistant environment. Linking does not authorize a publication.” Identify the tested environment and its actual settings. |
| 4. Base Google consent | Owner opens Connect, explicitly accepts privacy, and continues to Google. Show application identity, requested scopes, exact callback host and channel selection. | Explain `youtube.readonly` and `youtube.upload` against the inventory and supplied-video uploader. Record the complete consent flow and actual review client; omit passwords and codes. |
| 5. Read feature | Retrieve the dedicated channel's uploads and inspect an owned video. | “The response identifies the source account and observation time. Missing or partial data is not reported as zero.” Show sanitized output and local storage description. |
| 6. Optional scopes | Request management and analytics separately. Show each additional consent and a corresponding implemented feature. | Show an owned-video change preview / comment-reply preview and a non-monetary Analytics report. Explain why those features need their particular scopes. Do not imply that granting a scope approves an operation. |
| 7. Upload approval | Show supplied media, destination, full metadata/privacy preview and Community Guidelines certification. Show both immediate and scheduled approval paths. | “The owner certifies the upload and approves this exact content and destination. Changing the approved proposal requires fresh approval.” If the reviewer needs execution evidence, collect it only with separate test-owner authorization. |
| 8. Disconnect and deletion | Revoke one installation, then demonstrate full Google disconnect with a test grant and account deletion. Show sanitized retry evidence from a fictional outage fixture. | Distinguish installation removal from revoking the underlying Google grant. Explain immediate broker denial, durable failed-revocation handling, local/offline retention limits and backup cleanup/restore controls as actually deployed. |

Recording URL: **not yet recorded**. Review deployment URL and revision:
**not yet attached**. Include an English voiceover or English captions and show
all scope-dependent features in the same coherent review flow. Google requests
scope justification and a demonstration; confirm its current submission fields
when the authorized operator opens Verification Center.
[Sensitive-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).

## Submission evidence checklist

| Item | Present status | Required completion evidence |
| --- | --- | --- |
| Policy and assistant gates | Implemented locally, fictional integration tests present. | Reviewed release revision, deployed gate demonstration and policy-version match. |
| Revocation, local retention and backups | Source remediation and tests are being assembled with this package. | Final passing test report plus production maintenance configuration, real deployment readback and controlled restore evidence. No automatic claim that every offline copy has been erased. |
| Upload certification | Source approval changes prepared. | Exact immediate/scheduled preview evidence and approved test-owner demonstration. |
| Domain ownership | Verified October 15: Namecheap TXT and Search Console domain property under the project administrator. | Search Console ownership tied to an eligible project account. Authorized Domains text alone is insufficient. |
| Production/test separation | Existing record includes public and pilot callbacks in one Web client. | Reviewed final production client/project and separate test environment; preserve existing grants during migration. |
| Branding/contact | Original audit found branding configured; public support and selected Google support address differ. | Current screenshots and eligible support-address alignment where available. |
| Scope inventory | Source justifications above; live console must be compared again. | Exact scope list, final clients and enabled APIs, optional consent evidence, backup-client explanation. |
| Assistant environments | Contextual disclosure implemented; no blanket third-party approval. | Completed per-configuration assessment for each supported production environment. |
| Demo and verification | Script prepared; recording and submission not performed. | Sanitized English recording URL, operator review, submission receipt, and Google's actual decision. |

Domain verification, environment separation and the accuracy of consent branding
must be established in the relevant Google account before requesting review.
[OAuth production compliance](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).

## Evidence collection notes

Current endpoint documentation was fetched on October 15, 2026. Context7's
primary credential was quota-limited; the existing alternate workspace credential
successfully resolved the official YouTube Data API documentation and queried
method scope requirements. Google policy and verification pages were read from
their official URLs. No credential is included in this package.

The code evidence is in `tests/test_saas_consent.py`,
`tests/test_connection_landing.py`, `tests/test_saas_platform_access.py` and the
revocation, retention, backup and upload-certification suites. Record the final
combined run separately after all changes are integrated. These tests use
fictional identities/providers and do not prove Google verification or production
operation. Follow the retention and revocation requirements in the
[YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies)
and attach the actual operational evidence before changing the readiness decision.
