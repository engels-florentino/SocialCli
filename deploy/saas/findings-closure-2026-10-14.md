# Findings closure — SocialCli 0.3.0rc4

Scope: the seven product improvements and H-01 through H-08 supplied by the
operator. Runtime source is immutable tag v0.3.0rc4 / 393b2b3. Later commits add
operational evidence and regression coverage, not a second product or runtime.

| Finding | Implemented result | Evidence |
| --- | --- | --- |
| Product1: enroll during trial | Checkout before expiry preserves the original free deadline; fixed USD10 monthly price; durable retry intent | test_saas_billing.py, test_saas_reminders.py |
| Product2: brand lock | Last disconnect frees the brand; account rename preserves connection IDs and trial eligibility | test_saas_lifecycle.py |
| Product3: Instagram local originals | Authenticated streaming to florentino.pro,900s expiry including upload, early cleanup, quotas, owner checks and host fallback; no transformation or backups | test_saas_temporary_media.py, test_hosted_media.py; live timer/proxy verification |
| Product4: legal/UI | English SaaS privacy/terms, retention and temporary-media disclosure, Account link using existing UI | Generated assets checked; public bytes match source |
| Product5 / H-01 | Owner-indexed lookups; account view decrypts only relevant owned records; complete account deletion retained | test_saas_storage_upgrade.py, test_saas_lifecycle.py |
| H-02 | WAL and deferred query-only reads; synchronous FULL retained for acknowledged credential/billing durability | Concurrent snapshot/writer regression and live WAL verification |
| H-03 | Trust overwritten X-Real-IP only from configured NPM peer; expired rate buckets pruned | test_connection_peer.py; deployed172.25.0.2/32 |
| H-04 | Foreign events ignored; processed blobs deleted; pending/review retention7days; durable raw/semantic hashes; schema7 migration covers all states | test_saas_webhooks.py, test_saas_storage_upgrade.py |
| H-05 | Explicit test/live configuration validates matching keys, prices, events and objects; deployed mode remains test | test_saas_billing.py and webhook tests |
| Product6 / H-06 | Paid SocialCli subscription line validated; credits/tax no longer rejected by total amount_paid | test_saas_billing.py |
| Product6 / H-07 | Encrypted refresh token,12h absolute web session,45s identity cache, serialized refresh and logout/revocation fences | test_saas_auth.py |
| H-08 | Root dist contains only rc4 wheel/sdist; no tracked historical archives; strict archive checker passes | scripts/release/check_artifacts.py |
| Product7 |1692-byte public bootstrap; complete packaged offline topic guides | Generated asset check, guide tests, installed wheel smoke |

## Additional edge cases closed

- Schema7 migration verifies done/ignored blob deletion, pending/review7day
  retention, original body hashes and semantic hashes across repeated startup.
- Concurrent DELETE during a streaming PUT prevents media resurrection, retains
  charged quota until the original expiry, and releases metadata via maintenance.
- www is explicitly updated by active ddclient, with good cached update status;
  its valid HTTPS certificate and canonical301 redirect were verified.

Full post-closure suite: **2874 passed** in59.62s, with one existing
Starlette/httpx deprecation warning. Generated UI and archive checks also passed.

These tests exercise fictional data only. They do not establish provider review
approval or claim every social-network operation was tested live in this rollout.

## Release and external gates

All listed implementation findings are addressed in the deployed rc4 code.
[Rollout evidence](rollout-rc4-2026-10-14.md) records backup, DNS, routing and CI.
The protected TestPyPI release completed successfully after owner approval:
https://github.com/engels-florentino/SocialCli/actions/runs/38666089357 .
Published wheel/sdist hashes match the reviewed build. A fresh installation of
the downloaded wheel passed its smoke check; the compact /agents bootstrap is
activated for0.3.0rc4. Public setup, security, terms and privacy now describe the
SaaS sign-in, storage, billing and retention behavior rather than legacy defaults.

Google/Meta production reviews and TikTok Draft are provider-controlled gates.
The operator's instruction not to submit TikTok review or switch Stripe Live
remains in effect. Billing code supports Live; activating real charges is not
necessary to close H-05 and is not done implicitly.

## Operator and contact confirmation

The operator confirmed Florentino.pro and admin@socialcli.app as the public
contact. README, security reporting, public documentation, terms and privacy
were updated accordingly, preserving the existing English UI. All ten changed
public documents were fetched over HTTPS and matched the local source hashes.

Stalwart now has an enabled admin@socialcli.app alias on the existing
access@socialcli.app mailbox. Account readback confirmed the alias, and SMTP
RCPT TO returned 250 2.1.5 OK. No email body was submitted; this verifies
recipient acceptance, not a human inbox delivery test. Existing application
sender credentials and operational alert recipients were preserved.
