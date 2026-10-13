# Isolated server candidate — 13 October 2026

Code checkpoint: `8bbd094`, SocialCli **0.3.0rc1**. Public SocialCli remains
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

- Full Python suite: **2,396 passed**, one upstream Starlette deprecation warning.
- Wheel and source archive passed the release allowlist/private-file verifier.
- Wheel SHA256: `6d2ba5ce35e7d32767d72cf2efef33520392e8c2bd9bcd05b0a7f9d55022250e`.
- Sdist SHA256: `877f15d4459381221963d50367d13869ac1267d5fd6d1f69e3c8c53c7be4147a`.
- Candidate installed in its own Python 3.12 image; version is 0.3.0rc1.
- Local HTTP probes: `/healthz`, `/agents`, `/account/login` return 200;
  unauthenticated `/account/` redirects 303. Guide and login include expected
  candidate/brand content. No OTP request or authorization link was opened.
- Private Auth health returned 200. Real Stripe **read-only** configuration checks
  confirmed the configured account and fixed USD 10/month test price.
- Independent vault started with zero SaaS users and persistent legacy cutoff.
- Manual maintenance and separate read-only operational report exited 0 with no
  pending work; Docker reports healthy and verified loopback binding on 8789.

The package-index release, hosted browser checkout, real signed webhook delivery,
public proxy routing, backup scheduling/off-host recovery, alert delivery and
Muse/Grok pilot remain outstanding. This deployment does not establish these.
