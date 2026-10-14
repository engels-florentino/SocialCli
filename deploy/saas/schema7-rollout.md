# Private schema7 rollout and compatible recovery

Scope: update only the existing loopback SocialCli candidate. Public0.2.6, NPM,
Auth, provider availability, reminder delivery and Stripe test configuration stay
unchanged. No customer media, account authorization or social write is involved.

## Reviewed inputs and recovery construction

Primary code checkpoint:7fbb6a2, wheel SHA256
`b0511592cd49c0ec90b9e3e4a9f1cf2ee0c36b1590cc9a8850339442b557b007`.
Image:`socialcli-saas-candidate:7fbb6a2`, ID
`sha256:8673546e75d79fb55c6a6e53abf2f58792c4d4ad3a5debb711d5952917951622`.

Recovery is an internal server artifact, never a client release or PyPI upload.
Start from `git archive 1fbaf1d`, copy exactly the11 changed files under
`socialctl/saas`, `socialctl/connection_service`, and `socialctl/ui` from7fbb6a2,
and set package version `0.3.0rc1+schema7recovery`. Build with `uv build --offline`.
Those files contain the reviewed policy enforcement, schema guard/migration and
matching server UI/guide; no other newer client feature is backported. Store the
per-file SHA256 manifest alongside the artifact. The resulting wheel and sdist
passed the existing release path/secret allowlist check.

- Recovery wheel SHA256:`5c42962d2108d8884964d90c288755d01e4b53251ff959687e0e3df798f66535`.
- Recovery sdist SHA256:`9e1611e9079549880dde4e55c7322a721bff27a697512ee6d498addebfeb27c0`.
- Build image from deployed1fbaf1d, install this wheel without index/dependency
  downloads, preserve the non-root runtime and existing dependencies.
- Server path:`/home/blades3/projects/socialcli-saas/recovery-schema7`.

The recovery artifact deliberately retains all schema7 security behavior. It does
not reverse connection-platform selections or undo the migration. A defect in
that shared security code requires repair with service access closed; reverting
to the original schema6 image is never a permitted fallback.

## Pre-switch evidence

- Primary full suite:2703 passed on both macOS and Linux; installed smoke passed.
- Primary migrated an authenticated schema6 candidate snapshot offline; all other
  SQL rows and decrypted records preserved, repeated initialization unchanged,
  old server rejected, HTTP smoke passed. Source snapshot remains unchanged.
- Recovery installed-wheel server suite:226 passed,2 client-only error taxonomy
  tests deselected,15.20s. Product module origins were asserted from the installed
  wheel, and all11 backported files matched the reviewed manifest. The read-only
  test source caused only a pytest-cache warning. No network was available.
- Recovery opened the already-migrated snapshot in a disposable copy with
  external networking disabled: all SQL/decrypted contents and cutoff remained
  identical, repeated initialization and old-code rejection passed, and the same
  HTTP smoke passed. Source snapshot stayed unchanged. This snapshot contains
  zero installations; installation-policy behavior is covered by fictional tests.
- Recovery image ID:
  `sha256:99a0bd80a3dca67251f589dcd164ceeec8f5d512c0a85d5725784f78ac11796b`.
- Whole-wheel provenance independently checked: every overlay file matches7fbb6a2
  and every other package file matches1fbaf1d.

## Coordinated switch procedure

1. Verify primary/recovery image IDs, private test-only/provider-off settings and
   current public container identity. Retain the current Compose configuration.
2. Start and verify a fresh candidate/Auth encrypted backup pair. Local success
   does not claim off-host Drive recovery. Keep keys and backup data on the server.
3. Stop candidate billing and backup timers; let any currently active services
   finish and verify their terminal results. Stop the candidate container to
   terminate every old in-container writer. Do not stop Auth or public service.
4. Set the candidate Compose image to the immutable primary tag and recreate
   using `--no-build --pull never`. Preserve environment, state mount, networks,
   loopback port and runtime restrictions exactly. Startup migrates to schema7.
5. Verify container health, schema7, health/guide/login/account routes and public
   container identity/health. Run private maintenance with the existing test-only
   settings; inspect operational diagnostics. Resume candidate timers afterward.
6. If primary startup/smoke fails, stop it completely and switch only its image
   to the tested compatible recovery image, using the existing current database.
   Verify recovery before resuming timers. If recovery also fails, keep this
   private candidate stopped and repair; do not restore an old database to downgrade.
7. Record actual image IDs, backup result, migration and post-switch evidence.

Neither the rehearsal nor this private rollout closes external release gates:
Drive recovery, pilot DNS/TLS, real webhook delivery, package publishing and actual
Muse/Grok pilots remain required. Fine-grained per-operation permissions remain
an open product requirement; schema7 enforces connection-platform selection only.

## Independent review decisions

One independent read-only review found no Critical, Important or blocking Minor
findings. It checked overlay/artifact provenance and the policy/migration/writer
shutdown paths. Verdict: ready for this private switch after observed fresh paired
backup and writer shutdown. No new feature implementation or fix pass was needed.

Ruling on review exclusions: remote image/configuration, backup and writer shutdown
must be observed during execution below; a reviewed procedure does not prove them.
Ruling on review exclusions: full/installed test runs were performed by the parent
and recorded above; the reviewer verified provenance without rerunning them.
Ruling on review exclusions: public release, DNS/TLS, off-host recovery, real webhook
and provider/agent pilots remain open launch requirements, not covered by this switch.
Ruling on review exclusions: action-level restrictions and invalidation of previously
issued provider tokens remain open; no such enforcement is claimed for schema7.
Ruling on review exclusions: recovery CLI feature parity is intentionally absent;
this artifact is server-only. Its hosted guide describes the current user client,
so operators must never distribute its older bundled CLI as the latest client.

## Execution result — 14 October 2026

All pre-switch observations and the coordinated primary switch completed. Fresh
paired backup succeeded before the switch; timers were stopped, services drained,
and the old container was observed stopped before recreation. No rollback was
required. Live schema7, route checks, image identity and runtime isolation passed.
Systemd maintenance returned0 and diagnostics reported healthy. Timers resumed;
a fresh post-migration encrypted backup also returned0 with healthy freshness.
Public container identity and health remained unchanged. Detailed evidence and
current image identifiers are in [candidate status](candidate-status.md).

Review exclusions resolved at execution: remote image/configuration, backup and
old-writer shutdown were directly observed above. External launch and per-operation
permission exclusions remain open. There were no deferred Minor findings.
