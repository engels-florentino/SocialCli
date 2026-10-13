# Isolated server candidate — 13 October 2026

Code checkpoint: `fe9700c`, SocialCli **0.3.0rc1**. Public SocialCli remains
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
- Social providers and trial reminder delivery disabled; no maintenance timer
  enabled. Auth OTP remains configured separately.
- Container runs as UID/GID 1000, root filesystem read-only, capabilities dropped,
  bounded memory/process/log limits. Build context explicitly excludes secrets
  and state via `candidate.dockerignore` installed as `.dockerignore`.

## Evidence

- Full Python suite: **2,401 passed**, one upstream Starlette deprecation warning.
- Wheel and source archive passed the release allowlist/private-file verifier.
- Wheel SHA256: `50bc17bd3f9132e81d9b92b335fd4dfa47bd0f8dbf641d8a3ebcfa44b7ebe552`.
- Sdist SHA256: `19578bc5b214ee354071b60d2956851e7d9f7f3936f7a75b23c7d2fcc339fc6e`.
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
public proxy routing, backup scheduling/off-host recovery, alert delivery and
Muse/Grok pilot remain outstanding. This deployment does not establish these.
