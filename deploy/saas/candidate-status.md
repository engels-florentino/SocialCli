# Isolated server candidate — 13 October 2026

Current code checkpoint: `1fbaf1d`, SocialCli **0.3.0rc1**. Public SocialCli remains
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

## Initial deployment evidence (historical checkpoint)

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


## Current private update — checkpoint1fbaf1d

Updated on14October2026 UTC (13October local server time), after a fresh encrypted
backup pair: backup service Result=success, ExecMainStatus=0, freshness report ok.
Billing maintenance timer was stopped during replacement, its current execution
was allowed to finish, and the timer resumed afterward. Database, Auth, provider-off,
reminder-off, Stripe test-only configuration and loopback8789 binding were preserved.

- Reviewed wheel SHA256: `743262d7ccaae9bbb26fde7de70c18faba584342b2648e53130867c3d2764804`.
- Reviewed sdist SHA256: `e2a3304eef6d82538b567d6a04cac27af21ece66136655cabd3934c3b5e32608`.
- Current image: `socialcli-saas-candidate:1fbaf1d`, ID
  `sha256:3c57eed7c341e5b38a95a2273c0c2c5f1771194858bf9fbcd8e10eed10d7e394`.
- Previous image retained as `socialcli-saas-candidate:pre-1fbaf1d`, ID
  `sha256:a14c99d178c5c5abe22a2bc4dc34e3596fac31442c8161e9e226771b2ce07592`.
  The previous wheel is retained privately under candidate/rollback-1fbaf1d.
- Build context was only1.379MB and used the reviewed wheel and allowlist;
  credentials and state were excluded. No customer media was transferred.
- **2,540 tests passed on macOS; 2,540 passed on Linux/Python3.12 in145.35s**.
  The Linux suite ran with networking disabled in a disposable verification
  container, against the new image's resolved dependencies. This includes
  Pydantic2.14.1, Typer0.27.3, FastAPI0.143.0 and Starlette1.8.0.
- Clean installed-wheel smoke in Linux passed workspace creation, encrypted
  credentials and packaged guide. An installed offline report computed the
  expected fictional growth10 without external calls; temporary fixtures removed.
- After replacement, /healthz, /agents and /account/login returned200 using the
  existing canonical Host socialcli.app; the guide contains the report command.
- Docker reports the candidate healthy. The public container ID was unchanged
  across replacement and its health remains healthy. The candidate billing timer
  is active; private operations diagnostics are checked after deployment.

This update includes exact customer scheduling, Linux user timers and offline
brand insights. It does not expose those changes publicly. `pilot.socialcli.app`
still lacks DNS; [pilot routing](pilot-proxy.md) and its Compose overlay are
prepared, not applied. The owner has been asked for the exact A record and for
which personal Google account or accessible Shared Drive to authorize for backup.
Google Drive authorization, external recovery, public pilot, signed webhook
receipt, package publication and real Muse/Grok pilots remain outstanding.

## Linux verification of client checkpoint15aaa05 — not deployed

On14October2026, the current committed source was transferred using `git archive`
into a separate verification directory on Florentino. No customer workspace,
secrets, database or media was transferred. A disposable container used the prior
verification image's Linux/Python3.12 dependencies, with external networking off,
read-only source/root and temporary writable test storage.

- Full suite: **2,687 passed in142.78s**, container exit0.
- Wheel SHA256 after transfer:
  `6558c1a0ae3b8aa8f35e94fe3244181a4f52f087fc1f207649b1ba459b521268`.
- A separate offline virtual environment installed that wheel with the same
  resolved dependencies. Installed smoke passed version, help, workspace creation,
  real encrypted-credential persistence and packaged agent instructions.
  `media check`, `inbox-draft` and `native-schedule` command help also passed.
- The first smoke attempt could not execute the CLI entrypoint because the test
  tmpfs was mounted noexec. Explicit exec on that disposable test mount resolved
  it; no product permissions or production mounts changed.
- This verifies Linux compatibility with those dependencies, not fresh resolution
  from PyPI, real agent runtimes, provider writes or a server redeployment.

The deployed candidate remains1fbaf1d and public remains0.2.6. The latest DNS
lookup still returns no A record for pilot.socialcli.app. Drive authorization and
external restore, HTTPS pilot, real signed webhooks, package publication and real
Muse/Grok pilots are not established by this verification.
