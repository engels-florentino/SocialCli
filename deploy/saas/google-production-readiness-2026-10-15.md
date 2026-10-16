# Google production readiness review — October 15, 2026

**Decision: not ready for public Google authorization. Keep Testing.**

Reviewed SocialCli 0.3.0rc8 source on main at `c94e80e`, public pages,
and the authenticated Google console for project `socialcli`. This is a
technical readiness review, not Google's approval or a comprehensive legal opinion.
At the original review, no provider setting, credentials, user data, public policy or deployed code was changed. Subsequent remediation is tracked below.

## Remediation checkpoint (source, not a deployed-release claim)

- Domain ownership completed: Namecheap TXT is publicly visible; Search Console confirmed the `socialcli.app` domain under the project administrator.
- G-01/G-07: versioned owner privacy and separate assistant data acknowledgment gates added to SaaS and standalone connection flows. Existing installations require owner relinking; unknown third-party AI configurations are not certified for production.
- G-02: immediate local denial, programmatic Google revocation and encrypted durable retries; restore-safe deletion identifiers. Cross-review also covers OAuth/disconnect races.
- G-03: explicit managed-store inventory, six-day YouTube expiry, disconnect/authorization failure purge, fail-closed cleanup, daily macOS/Linux service installer. Offline and arbitrary-export limitations remain explicit. Older weekly comparison baselines are unavailable; native remote receipts require regular reconciliation.
- G-04: absolute six-day repository cutoff, verified Drive mirror, six-hour independent retention with freshness/error alerts, and a quarantined restore workflow requiring a fresh current-authority deletion manifest plus separately verified Auth cleanup.
- G-05: Community Guidelines certification is displayed and bound to immediate and scheduled upload approvals. Old upload approvals require fresh review.
- Public-policy source and packaged agent guidance updated in English to reflect these changes. No deployment claim until public readback and operational verification are recorded.
- G-06 environment separation and G-08 real demo/submission remain launch gates. The operator backup authorization was subsequently migrated to the owner-approved, isolated `socialcli-operator-backups` project (In production, `drive.file` only); both encrypted repositories and a full backup were verified. This resolves the shared-project backup prerequisite, not the remaining customer launch gates. See [the cutover runbook](google-production-cutover.md). Google audience remains Testing. [Verification package](google-verification-package.md) contains exact scope justification, data-flow disclosure and recording script; it is not a completed recording or an approval.

The sections below preserve the original findings. See the release checkpoint for final test and deployment evidence.

## Verified evidence

- Homepage, privacy and terms returned HTTP 200 over HTTPS and matched the local
  `docs/index.html`, `docs/privacy.html` and `docs/terms.html` byte for byte.
- The policies identify Florentino.pro and `admin@socialcli.app`. They explain
  email authentication, Stripe, assistant access, local workspaces, temporary
  Instagram storage, trial markers and encrypted operator backups.
- Terms expressly bind YouTube integration users to YouTube's terms. Privacy
  links Google's privacy policy and the Google permissions/revocation page.
- Google Branding has the correct SocialCli name, logo, public homepage,
  privacy URL, terms URL and authorized domain `socialcli.app`.
- Audience is External / Testing. Verification Center's exemption is because
  of Testing; it is not a verification approval.
- Data Access lists `youtube.readonly`, `youtube.upload`, `youtube.force-ssl`,
  `yt-analytics.readonly`, and `drive.file`. No restricted scopes were listed.
  The Drive scope belongs to operator backups, not customer social connections.
- Source separates optional management and analytics consent from base YouTube
  connection scopes in `socialctl/connection_service/providers.py:61`.
- Upload code exposes title, description and the selected privacy value;
  default privacy is private (`socialctl/adapters/youtube.py:100`).

## Confirmed implementation gaps

### G-01 — Privacy acceptance is missing

The login and connection screens link legal pages but do not require agreement
to the privacy policy. No versioned acceptance check was found in the reviewed
account/connection routes. See `socialctl/saas/pages.py:22` and
`socialctl/connection_service/landing.py:66`.

Implement an owner-facing acceptance gate before enabling features, enforced
server-side. Record the accepted policy version and time. Cover existing users,
assistant pairing, direct connection paths and material policy changes.
This receipt design is the proposed implementation, not a mandated Google schema.

### G-02 — Disconnect does not revoke Google authorization

`socialctl/saas/lifecycle.py:43` removes vault records and the slot. Account
deletion also removes grant records without a provider revocation job. No Google
revocation endpoint call was found in `socialctl`. The current privacy wording
acknowledges that issued tokens may survive and asks users to revoke separately.

Implement Google revocation and durable handling of failed attempts without
losing the credential needed to finish revocation. Block local use immediately.
Keep assistant-only removal distinct from disconnecting the entire Google grant.
Do not accidentally revoke the separate operator backup authorization.

### G-03 — API data lifecycle is incomplete

- `socialctl/metricas/almacen.py:109` retains snapshots for 365 days and its
  cleanup leaves the consolidated files untouched.
- `socialctl/metricas/analytics_schema.py:170` describes authorization/deletion
  intervals but explicitly says `Task13; no automatic purge`.
- `socialctl/inbox.py:38` persists comment text, original text and edit history
  without an age-based purge in that store.
- `socialctl/saas/renewal.py` handles token state for operations; this does not
  establish periodic validation of all retained local datasets.
- Disconnect/account deletion cleans SaaS state, not these client datasets.

Inventory all provider-derived stores and exports, add refresh/deletion rules,
and verify authorization and video existence for retained historical statistics.
Design offline-runner behavior explicitly; do not promise deletion of arbitrary
copies already exported into third-party chats. Preserve customer-supplied originals.

G-01–G-03 map to YouTube policy sections III.A.2, III.D.2 and III.E.4:
privacy agreement before access, immediate programmatic consent revocation,
deletion within seven days for in-app revocation/deletion, and periodic validation
with the applicable 30-day rules. Historical authorized statistics can be kept
longer when the required checks are performed; 365 days is not inherently forbidden.
[YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies).

### G-04 — Backup deletion is unbounded

`deploy/saas/backup_host.py` creates and checks encrypted snapshots without a
forget/prune step. `backup-restore.md:193` explicitly records that remote pruning
was not introduced. Public privacy says earlier data remains until the operator
removes backups, with no fixed period. An independent host retention job was not
verified in this review.

Define and implement bounded retention for both local and Drive repositories,
deletion handling and restore suppression of deleted accounts/grants. Verify
these with fictional data before making a deletion guarantee. Merely shortening
the policy text would not remove historical snapshots.

### G-05 — Upload content certification is absent

Terms mention content rights and approval but no explicit YouTube Community
Guidelines certification was found in the upload approval path. Add this to the
owner's reviewed upload approval, including scheduled uploads and agent guidance.
Google's guide calls for uploaders to certify compliance with those guidelines.
[YouTube policy guide](https://developers.google.com/youtube/terms/developer-policies-guide).

## Evidence and configuration needed before submission

### G-06 — Domain ownership and production separation

The signed-in project administrator's Search Console property picker lists only
`florentino.pro`; `socialcli.app` is absent. The Authorized Domains entry alone
does not prove ownership. Verify the domain under a Google account associated
with this project, or supply evidence from an existing eligible owner.

`deploy/saas/google-project.md` records both public and pilot redirect URIs in
the SaaS client. Recheck the current client configuration and separate remaining
test environments from production before submission. Do not break existing
connections by rotating credentials during this audit.

Google requires owned/verified domains and separation of test and production
projects and client environments.
[OAuth production compliance](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).

### G-07 — Assistant data use needs an enforceable scope

Privacy says the selected assistant receives output and may obtain provider
access tokens, while its own policies apply. The token broker implementation
supports that disclosure (`socialctl/saas/connections.py:299`). This does not
establish compliance for every AI service advertised on the landing page.

Document the actual data flow, require contextual user consent, and determine
which assistant configurations satisfy Google's applicable use/transfer and
human-access restrictions. Distinguish customer-controlled software from a
third-party processor. Review training and retention settings per integration;
do not certify all providers based solely on SocialCli's own no-training policy.
Add an accurate Limited Use commitment only after the supported flow can uphold it.
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy).

### G-08 — Verification package is not complete

Prepare an English demonstration of the actual consent and feature flows,
justify every requested sensitive scope, and submit brand/scope verification
after the implementation and policy gaps are closed. Explain operator-only
Drive backup usage separately. Review whether the backup client should move to
an operator project rather than complicating the customer application's review.
The latter is an architectural recommendation, not a claim that Google mandates
a separate backup project.
[Sensitive-scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).

Support email in Branding is still the administrator's Gmail rather than the
public `admin@socialcli.app`. Align it where Google's eligible support-address
choices allow; the difference alone does not establish a policy violation.

## Avoid false blockers

- Simple same-source arithmetic and rankings in `metricas/insights.py` should
  not be classified automatically as prohibited derived metrics. Google's current
  guide explicitly permits simple calculations. Keep YouTube data/source/time
  clearly distinguished and do not invent cross-platform scores.
  [Metrics guidance](https://developers.google.com/youtube/terms/developer-policies-guide).
- The current `videos.insert` reference states that uploads from unverified API
  projects are not restricted to private viewing. Do not apply an outdated blanket
  private-upload claim. This does not replace OAuth verification or actual account
  eligibility checks.
  [Current upload reference](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).

## Completion evidence required

Before changing the readiness decision, demonstrate consent bypass rejection,
Google revocation failure/retry handling, account-isolated local data cleanup,
aging and external-revocation handling, offline behavior, backup retention and
restore deletion enforcement, and uploader certification. Use fictional fixtures
for destructive cases. Check owner-facing approval and data disclosures end to end.

Then update public terms/privacy to describe the implemented behavior, recheck
their deployed hashes, verify domain ownership and production clients, and attach
the reviewed scope/demo evidence. Google decides the verification outcome.

This audit did not re-run publishing operations or the full test suite. No finding
above is marked fixed, and no legal commitment was silently added to public pages.
