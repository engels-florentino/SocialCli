# Isolated server candidate — 13 October 2026

Code checkpoint: `a5461a3`, SocialCli **0.3.0rc1**. Public SocialCli remains
**0.2.6**. This is a private server deployment, not a public SaaS launch.

## Deployed boundaries

- Dedicated Compose project/container `socialcli-saas-candidate`.
- Host directory `/home/blades3/projects/socialcli-saas/candidate`, private to root.
- Independent SQLite vault and generated encryption key. No real social grants
  copied from the public service; no legacy migration performed.
- Shares only the existing private SocialCli Auth service/network.
- HTTP bound to host loopback `127.0.0.1:8789`, with canonical Host `socialcli.app`.
  No NPM route or public service replacement. Port 8788 was occupied, so the
  candidate uses 8789; the unrelated listener was not changed.
- Stripe test account/product/price/Portal configuration only. Webhook signature
  key is a local placeholder; no Stripe endpoint delivers to this candidate yet.
- Social providers and trial reminder delivery disabled. Candidate-only maintenance
  runs every five minutes; Auth OTP remains configured separately.
- Container runs as UID/GID 1000, root filesystem read-only, capabilities dropped,
  bounded memory/process/log limits. Build context explicitly excludes secrets
  and state via `candidate.dockerignore` installed as `.dockerignore`.

## Evidence

- Full Python suite including host backup orchestration: **2,416 passed**, one
  upstream Starlette deprecation warning. Backup orchestration runs on the host;
  the deployed application image remains at the code checkpoint above.
- Wheel and source archive passed the release allowlist/private-file verifier.
- Wheel SHA256: `edee7a79f82167f44770e327e845bc355803c3aba3eee880e024f5f745fe9acf`.
- Sdist SHA256: `bcbb28f97a0b9f3f4258514aa980edeed7a6a6e05884ec79bc60a638002e9512`.
- Candidate installed in its own Python 3.12 image; version is 0.3.0rc1.
- Local HTTP probes: `/healthz`, `/agents`, `/account/login` return 200;
  unauthenticated `/account/` redirects 303. Guide and login include expected
  candidate/brand content. No OTP request or authorization link was opened.
- Private Auth health returned 200. Real Stripe **read-only** configuration checks
  confirmed the configured account and fixed USD 10/month test price.
- Independent vault started with zero SaaS users and persistent legacy cutoff.
- Manual maintenance and separate read-only operational report exited 0 with no
  pending work; Docker reports healthy and verified loopback binding on 8789.

A fictional owner now exercises actual hosted Stripe Sandbox Checkout/Portal; see
[hosted billing evidence](hosted-billing-verification.md). The subscription was paid
with a test card and cancelled at period end. No real owner/social account was added.
The Portal timestamp cancellation fix was rechecked against that exact Stripe
subscription after candidate-only redeployment, with a verified pre-update snapshot.

The package-index release, complete browser registration/return, real signed webhook delivery,
public proxy routing, off-host recovery, alert delivery and
Muse/Grok pilot remain outstanding. This deployment does not establish these.

The candidate-only encrypted backup timer is now enabled hourly. A real systemd
run succeeded, and an exact local snapshot pair restored offline with both
repositories fully checked. See [backup evidence and limits](backup-restore.md).

The whole-branch session and CLI recovery fixes are now in the candidate image.
A fresh encrypted backup completed before recreation; installed hashes of all
three changed modules match the reviewed checkout. Candidate/public service
health and the four HTTP route checks above passed after deployment. See
[branch review](branch-review.md) for findings, fixes and acceptance limits.

Candidate maintenance timer activation: configuration rechecked as Stripe test
only, with reminder delivery and social providers disabled. Manual and systemd
runs exited zero with no pending/review work. Timer is enabled/active/waiting;
private operations report is healthy. Public-service maintenance remains untouched.
