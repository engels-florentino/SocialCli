# Candidate deployment history — superseded by 0.3.0rc4

**Current public pilot: SocialCli 0.3.0rc4**, deployed from immutable commit
`393b2b3` and published on TestPyPI. Use the [current rollout](rollout-rc4-2026-10-14.md),
[findings closure](findings-closure-2026-10-14.md) and [release instructions](release.md).
The current agent installation entry point is https://socialcli.app/agents?v=0.3.0rc4.

Everything below is historical deployment evidence. References to rc1, 0.2.6,
private-only access and then-pending deployment steps are not current status.

Historical checkpoint: `57efd47`, SocialCli 0.3.0rc1, schema7.

## Historical deployed boundaries

- Dedicated Compose project/container `socialcli-saas-candidate`.
- Host directory `/home/blades3/projects/socialcli-saas/candidate`, private to root.
- Independent SQLite vault and generated encryption key. No real social grants
  copied from the public service; no legacy migration performed.
- Shares only the existing private SocialCli Auth service/network.
- HTTP bound to host loopback `127.0.0.1:8789`, with canonical Host `socialcli.app`.
  No NPM route or public service replacement. Port 8788 was occupied, so the
  candidate uses 8789; the unrelated listener was not changed.
- Stripe test account/product/price/Portal configuration only. A real CLI-forwarded
  signed event was verified on14October; the candidate now has that private test
  listener's signing secret. No permanent public Stripe endpoint is configured.
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

The package-index release, complete browser registration/return, direct public webhook delivery,
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


## Previous private update — checkpoint1fbaf1d

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


## Schema7 preparation checkpoint — historical, before deployment

The reviewed client/service increment after340c7b8 introduces explicit installation
connection-platform policies and schema7.2703 macOS tests and16 installed-wheel
policy tests passed. At this checkpoint the server remained schema6/1fbaf1d. Before deployment,
verify Linux against the new artifact, take a fresh backup, stop every older service
and maintenance writer, then migrate. An old process already running does not
re-evaluate startup schema guards. Rollback must use schema7-compatible code;
never restore an old database simply to downgrade code. No new customer access,
provider revocation or fine-grained action permissions are claimed by this change.

## Schema7 image and offline migration rehearsal — 14 October 2026

Linux verification of checkpoint `7fbb6a2` is complete: **2,703 tests passed in
146.39s**, followed by successful installed-wheel smoke with networking disabled.
See the [connection-access verification](../../docs/superpowers/plans/2026-10-14-installation-platform-access.md).

Prepared immutable image `socialcli-saas-candidate:7fbb6a2`, ID
`sha256:8673546e75d79fb55c6a6e53abf2f58792c4d4ad3a5debb711d5952917951622`.
The image derives from the exact deployed `1fbaf1d` image, preserving its resolved
dependencies, and installs the reviewed wheel with `--no-index --no-deps` and build
networking disabled. Dependency declarations have not changed between these
checkpoints. Wheel SHA256:
`b0511592cd49c0ec90b9e3e4a9f1cf2ee0c36b1590cc9a8850339442b557b007`.
Build context was 1.407 MB and excluded private configuration, scripts, state and
rehearsal results. The image is prepared, not serving traffic.

A fresh online SQLite backup of the private candidate was authenticated before
use. Its schema6 source hash is
`c548a444e30db3466e1f2da5d70a9376cc52390b76b9df9e5fccaa10b7d47c65`.
The rehearsal mounted that snapshot read-only, used a disposable working copy,
disabled all external networking, and never mounted the active database.

Verified against that actual candidate snapshot:

- Migration reaches schema7 and is idempotent on a second initialization.
- All other SQL table contents and decrypted record values remain identical.
- The deployed schema6 implementation rejects the migrated database; rejection
  leaves its contents unchanged. This is a downgrade guard, not a usable rollback.
- The new image serves health, agent instructions and login through an isolated
  ASGI client; an unauthenticated account request redirects to sign-in.
- The original snapshot remains unchanged. The migrated snapshot authenticates
  successfully and retains the persistent legacy cutoff.

The snapshot contains one encrypted record and **zero installation records**.
This live-data rehearsal therefore does not establish legacy installation-policy
migration; the separate fictional migration and restriction tests cover that path.
No Stripe, Auth, SMTP or social-provider request was made by the rehearsal.

Private server evidence: `release-7fbb6a2/scripts/rehearse.py`,
`release-7fbb6a2/scripts/schema6_store.py`, and
`release-7fbb6a2/results/report.json`, relative to
`/home/blades3/projects/socialcli-saas`. The authenticated migrated snapshot stays
there; neither database nor keys were downloaded to the Mac. This local snapshot
does not establish off-host recovery or a fresh paired Auth restore.

After the rehearsal, authoritative inspection confirmed the deployed private
candidate still uses `1fbaf1d` and schema6; the public image is unchanged. Both
containers are healthy and candidate maintenance/backup timers are active.

Remaining before switching the private service: prepare and verify an independently
usable schema7-compatible recovery release, obtain a fresh paired backup, and stop
all old writers during the migration. Never use the schema6 image or restore an old
database as a code rollback. Pilot DNS still has no A record; public HTTPS routing,
Drive authorization/recovery and the other launch gates above remain incomplete.

## Current private deployment — checkpoint7fbb6a2, schema7

On14October2026 the reviewed image was deployed after the
[compatible recovery procedure](schema7-rollout.md) passed independent review.
The recovery wheel passed226 installed server tests and opened the already-migrated
candidate snapshot offline with unchanged data, cutoff and successful HTTP smoke.
The recovery image is `socialcli-saas-candidate:1fbaf1d-schema7-recovery`, ID
`sha256:99a0bd80a3dca67251f589dcd164ceeec8f5d512c0a85d5725784f78ac11796b`.
It retains schema7 enforcement; the original schema6 image is not a valid rollback.

Fresh paired encrypted backups completed with service Result=success and exit0
before replacement. Candidate billing/backup timers were stopped, both services
were observed inactive with successful terminal results, and the old container
was stopped and verified not running before recreation. No old in-container
writer remained during the schema change. Auth and public service were not stopped.

Compose now pins `socialcli-saas-candidate:7fbb6a2`; candidate build inputs also
contain the reviewed wheel and dependency-preserving Dockerfile. Recreate used
`--no-build --pull never`, without changing environment, state or network settings.

Post-switch verification:

- Running image ID matches the prepared primary image above; Docker is healthy.
- Live database reports schema7 and preserves the persistent legacy cutoff.
- Health, agent guide and login return200; unauthenticated account returns303.
  The served guide includes the current inbox-draft and media-check commands.
- Existing provider-off, reminder-off and Stripe test configuration verified.
- Non-root UID1000, read-only root, dropped capabilities, identity-only network
  and loopback8789 binding verified after recreation.
- Actual systemd maintenance completed with exit0; private diagnostics report
  status ok with no pending/overdue/review issues.
- Billing, backup and backup-check timers are active. A post-migration encrypted
  backup pair also completed with exit0 and freshness status ok.
- Public container ID remained
  `be1c2940a3a4be8e568a6c40eed5b1345b774b246a35d853466560edd55487fc`
  and its health remained healthy. No NPM/public route changed.

No fallback was needed. Recovery preserves current security/data rather than
undoing schema7. This private update does not finish the launch gates or imply
fine-grained per-action permissions, public package availability or real agent pilots.

Subsequent Stripe verification replaced only the placeholder webhook signing secret
and recreated this same image. A real signed test event was received through Stripe
CLI, durably queued, then processed by systemd maintenance with unchanged paid
access/cancellation. The temporary listener was stopped and timers resumed. See
[hosted billing evidence](hosted-billing-verification.md) for exact scope and the
remaining permanent-public-webhook gate.

## Private refresh to57efd47 — 14 October 2026

The reviewed UI copy and typed recovery-action fixes are now installed in the
private candidate. No dependency or database schema change was required.

- Linux source suite: **2,761 passed**,123.65s, network disabled.
- Wheel SHA256: `f3d930e381d29d70b921d5557947f9baebdbcb548fc6209b3fe1ea0f692af025`.
- Image: `socialcli-saas-candidate:57efd47`, ID
  `sha256:8a9f95aa275b060569525c6e1c0c8aabe871eb1ae8e0ccda477cd3d846107b5b`.
- Built offline from the previous7fbb6a2 image with unchanged dependencies;
  installed Linux smoke passed outside the checkout with fictional data and
  zero external calls. All188 packaged source/assets matched reviewed source.
- Fresh paired encrypted backup succeeded before the switch. Billing/backup
  timers stopped and services drained; old container stopped before replacement.
- An initial environment-list order comparison triggered automatic rollback.
  Comparing sorted complete entries resolved that overly strict deployment check;
  the repeated switch verified identical environment contents and runtime settings.
- Health, guide and login returned200; unauthenticated account returned303.
  Maintenance returned0; schema7 operational diagnostics reported no issues.
- Public0.2.6 container identity/image and healthy state stayed unchanged.
  Provider access and reminders remain disabled; Stripe remains test-only.
- Prior Compose/Dockerfile/wheel retained server-side in `rollback-7fbb6a2`.
  This schema7 image remains a compatible rollback using the current database.
- Pilot CNAME still absent at the public resolver. Off-host Drive recovery and
  the other external launch/agent acceptance gates above remain outstanding.

Post-switch paired encrypted backup returned0; all three candidate billing,
backup and freshness timers are active. Candidate/public health checks passed.
Auth exposes no Docker healthcheck; its HTTP health endpoint was checked directly.
