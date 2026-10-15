# Google production cutover

Reviewed October 15, 2026. This is a proposed operator runbook, not evidence of a
completed cutover or Google approval. Keep the Google audience **External /
Testing** until the actual review and release process is completed. No Cloud
settings, running service configuration, credentials or grants were changed by
this review.

## G-06 finding

Google requires distinct Cloud projects for actual deployment tiers such as
development, staging and production. Its production guidance excludes test,
private development and pre-release environments from production OAuth clients.
[OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies),
[Production compliance](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).

Two hostnames alone do not establish two deployment tiers. Our interpretation is
that aliases for the same public production release can belong to one production
project; Google does not state a one-project-per-hostname rule. Conversely, calling
a test deployment “production” or pointing two names at the same container does
not establish separation if either route still serves development, debugging or
pre-release use. This interpretation is not a Google acceptance decision.

Current evidence:

- [Google project record](google-project.md) identifies project `socialcli`, Web
  client `SocialCli SaaS`, and both `socialcli.app` and `pilot.socialcli.app`
  YouTube callbacks. The separate Desktop client in that project is used for
  operator Drive backups. The audience remains Testing.
- The operator reports that the two hostnames now reach the same deployment.
  Older rollout documents describe separate containers; do not use those older
  records as proof of the current routing. Capture current deployment, ingress,
  release and configuration evidence before cutover.
- Domain ownership was confirmed in Search Console on October 15, as recorded in
  `google-project.md`. That closes ownership verification, not environment
  separation or application verification.

G-06 remains open until an operator records which tier the running service serves,
removes or isolates genuine test use, and verifies the final production client
configuration. A new production client and secret rotation are not automatically
required: retaining the current project/client for production while moving actual
test tiers into their own projects can avoid disrupting existing grants.

## Preserve the working service origin

`socialctl/connection_service/settings.py` derives every platform callback from
one `SOCIALCLI_PUBLIC_URL`. `app.py` also uses that origin for host validation,
browser pairing links, origin checks, billing and temporary media links. Changing
it is a whole-service migration, not a Google-only correction. The provider adapter
also has one configured credential pair per provider, with no per-grant registry
for old and new Google clients.

Keep the present canonical origin and Google Web credentials during this review.
Do not globally replace `SOCIALCLI_PUBLIC_URL`, delete either callback, rotate a
secret, or exchange the configured Google client merely to resolve the word
“pilot.” A client migration requires a separate plan for existing grants and
owner reauthorization; the current adapter does not transparently migrate them.

## Backup authorization isolation is a separate launch prerequisite

Google documents that token revocation invalidates the user's scopes and issued
tokens across all OAuth clients in the project. Therefore a separate Desktop
client **within the same project** does not protect Drive backup authorization
from YouTube disconnection for the same Google user.
[Web Server OAuth: token revocation](https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke).

Before deploying or exercising provider revocation for an operator account that
also owns backups, establish independent backup authorization. Preferred approach:
a separate operator-backup Cloud project and Desktop client. Verify the actual
Google account/project relationship privately; a demonstrably different Google
account is another isolation boundary, but is not established by different client
IDs. Never print tokens or credentials as evidence.

Move backups in a controlled operation: authorize the replacement, verify it can
write and restore a fresh encrypted archive, confirm scheduled copy/retention
health, and retain access needed for the existing archive lifecycle. The
`drive.file` grant in a new project may not automatically expose files created by
the previous app; prove access or keep the old archive cleanup path operational
until its six-day retention expires. Only then retire the old backup grant after
checking its project-wide effect on other authorizations. Do not test revocation
against the current operator grant as a diagnostic.

This recommendation follows the observed shared-project risk and the requirement
to preserve backups; it is not a claim that Google categorically requires every
backup utility to have a separate project. Do not weaken customer revocation to
work around an operator backup configuration.

## Operator sequence

1. **Record the topology without changing it.** Capture the deployed artifact
   version, both ingress mappings, canonical service origin, runtime/data-store
   identity, enabled providers, Google project/client identifiers, callback list,
   and audience status. Store no secrets. State whether either hostname is still
   restricted testing, staging or debugging. Record the owner of this decision.
2. **Choose the actual tiers.** For one public production tier, designate the
   existing deployment/project as the production candidate and discontinue test
   behavior there before submission. For continuing development/staging, create
   separate Google projects and OAuth clients; use distinct runtime configuration,
   data stores and test accounts. Never copy production grants or customer data to
   staging. Separate containers on the same host can provide this isolation; a new
   physical server is not inherently necessary.
3. **Resolve backup isolation first.** Complete the migration and verification
   described above before owner-account disconnect testing or automatic
   revocation can affect the shared project. Record the replacement backup project
   and health evidence without exposing credentials.
4. **Review production callbacks.** Retain only callbacks that actually serve the
   production tier. If retiring an alias, stop new authorization starts there,
   allow outstanding state/pairing flows to expire according to deployed TTLs,
   and verify no current client or integration depends on that route before
   removing it. Do not redirect an OAuth callback blindly between origins: Google
   validates the redirect URI against the authorization request and registered
   client. [Web Server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server).
5. **Keep cross-platform changes separate.** If a canonical origin migration is
   desired later, inventory Meta and TikTok callbacks, cookies/pairing, billing,
   temporary media and proxy host forwarding first. Configure and test the new
   routes before switching traffic. Keep the current origin for this Google
   cutover when no functional change requires moving it.
6. **Complete the review evidence.** Use the
   [verification package](google-verification-package.md) and
   [readiness audit](google-production-readiness-2026-10-15.md): actual consent
   recording, scope justifications, public policy pages, supported assistant data
   flow, deletion/retention evidence, and isolated disconnect demonstration.
   Production use of sensitive/restricted scopes requires Google's verification
   process. [Production compliance](https://developers.google.com/identity/protocols/oauth2/production-readiness/policy-compliance).
7. **Release only after the real review.** Record the verification decision and
   approved scopes, then execute the planned publication/cutover. Confirm the
   saved audience status, public consent behavior, existing Google grant refresh,
   and Meta/TikTok connection health without publishing content. Monitor backup
   and deletion workers. Passing local tests or changing the audience toggle is
   not evidence of Google approval.

## Rollback and acceptance evidence

Before any future mutation, record the previous non-secret settings and securely
retain the existing deployment configuration. Roll back ingress/runtime changes
when health checks fail; do not restore deleted grants, purged API data or expired
backup archives. Provider revocation cannot be undone by restoring a database;
reconnection requires the owner.

The completed record must identify the final tier/project mapping, production
callback inventory, verified canonical origin, separate backup authorization and
restore evidence, deployed artifact, retention-worker health, and actual review
outcome. Until those are recorded, G-06 remains a launch gate and Testing remains
the documented audience. This file does not certify overall Google or YouTube
compliance.

Documentation lookup used Context7's current Google OAuth reference through the
alternate configured credential after the primary quota was exhausted, plus the
official policy pages linked above. No credential values were recorded.
