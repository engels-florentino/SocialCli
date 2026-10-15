# Candidate distribution and publication

Current source version: **0.3.0rc3**, consolidated in the repository root on
`main`. SocialCli SaaS supersedes the former 0.2.6 working copy; it is the same
product. Source includes post-tag fixes, so the immutable `v0.3.0rc3` artifact
must not be overwritten or represented as containing later commits.

This document retains historical release checkpoints below. A source version,
successful tests or a registered PyPI username do not establish a published
package, provider approval or deployment of the current source to the server.

The agent-facing guide is generated as `docs/agents.md` from the packaged
`socialctl/ui/assets/agents.md` and served at `/agents` by the candidate service.
Route that exact path through NPM when deploying the candidate. Keep `/agents`
public, but never cache or fetch single-use `/account/pair/` or `/connect/` links
on the user's behalf.
After the published wheel has actually been downloaded and verified, change the
canonical guide to the installed release and confirmed availability, then rebuild
its generated copy. Keep the same SocialCli UI for human-facing pages.

## Trusted publisher setup

Use the registered `socialcli` account on PyPI. TestPyPI needs its own account;
a PyPI login does not automatically create one there. For each index, configure
a pending publisher (or the existing project's publisher if already created):

| Field | Value |
| --- | --- |
| Project | `socialcli` |
| Repository owner | `engels-florentino` |
| Repository | `SocialCli` |
| Workflow filename | `release.yml` |
| GitHub environment | `testpypi` for TestPyPI; `pypi` for PyPI |

Create matching protected GitHub environments, with required human approval,
restricted release tags and no approval bypass. Protect release tags from
modification/deletion. Do not store long-lived PyPI tokens in this repository.
The workflow grants OIDC only to the publish job; test/build jobs have read-only
repository permissions. The publisher downloads the build artifact and does not
check out or execute repository code with its OIDC permission.

## Review and release sequence

1. Finish the outstanding rollout review and approve the concrete artifact. Run
   tests, UI asset checks and wheel/sdist builds. Record the commit and file SHA256
   values. Verify no workspace files, credentials or `.superpowers` artifacts are
   packaged. Do not silently publish a candidate while the public service remains
   incompatible.
2. Create a new reviewed version tag (never move an existing tag) and dispatch `Reviewed Python release` with
   `index=testpypi`. The workflow rejects branches, mismatched package versions and
   candidate tags sent to production PyPI. Build runs the full suite; separate
   Linux/macOS jobs install that same wheel into clean virtual environments and
   test help, workspace creation and encrypted credential persistence.
3. Approve the TestPyPI environment only after inspecting the exact build commit
   and artifacts. Download the uploaded wheel directly from its TestPyPI release
   file URL, verify SHA256 and install that local wheel in a new environment with
   dependencies from normal PyPI. Do not add TestPyPI as an extra dependency index.
4. Exercise the candidate against the isolated hosted test service, then pilot
   the documented installation from the real agent environments. Fixture tests
   do not prove Muse/Grok completion.
5. After approval of the stable artifact, bump to `0.3.0`, update the lock and guide,
   repeat validation, create reviewed tag `v0.3.0`, and publish the stable artifact
   to TestPyPI first. Verify its downloaded wheel. Dispatch the same immutable tag
   to `pypi` and approve that environment. Verify the real PyPI download before
   enabling `uv tool install socialcli` as the public onboarding command.

A new workflow run builds again; compare candidate/stable artifacts and their
hashes before approving publication. Never overwrite/reuse a published version.
No tags, GitHub pushes, publishers or package publications are created by adding
this workflow. External environment protection must be configured before running it.

Reference: [PyPA Trusted Publishing guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/).

## Local candidate verification, 2026-10-13 — checkpoint 2e3c51a

- Full suite: 2,382 passed, one upstream Starlette TestClient deprecation warning.
- Clean wheel installs: macOS/Python 3.13 and Linux/Python 3.12 outside the checkout;
  actual encrypted credential save/read/delete, workspace creation, help and
  packaged guide/mail assets. An isolated `uv tool install` also reported 0.3.0rc1.
- Wheel SHA256: `c14fa2f411c2cb1768b40da26e5d2e4dc79449258133c6672186fc3bc31d60aa`.
- Sdist SHA256: `cef00019f9a7a02fe3f1ffebc66f3addd5459988b67687e4d054c2e18f37c998`.
- The explicit source allowlist and pre-upload archive verifier passed. The earlier
  default source builder included private ignored worktree files; those unpublished
  local source archives were removed and replaced. Inspected wheels were clean.
  Neither format has been uploaded to a package index.
- Real encrypted-backend tests exposed missing installation/pairing/rotation
  bindings and backend migration gaps. Fixes preserve installation credentials,
  block unsafe reconfiguration and recover a lost pairing acknowledgement.
- Independent focused review found no remaining blockers. Before changing the
  stable guide, replace the smoke script's literal `Candidate` check with its
  stable heading; this is a tracked release-preparation detail.

These are local/container checks, not an executed GitHub workflow, downloaded
TestPyPI/PyPI verification, every host's keyring, or a real Muse/Grok pilot.

The newer maintenance/server checkpoint and artifact hashes are recorded in
[candidate-status.md](candidate-status.md). The hashes above identify the earlier
distribution verification, not the latest candidate deployment.

## External setup checkpoint

The GitHub `testpypi` and `pypi` environments now exist with `engels-florentino` as required
reviewer and administrator bypass disabled. Prevent-self-review remains off so
the owner can approve a manually initiated release; agents must not approve the
publication on the owner's behalf. Both allow only tags matching `v*`, with no
branch rules. Saved configuration was verified in Safari and the public GitHub
REST API. No workflow was dispatched.

The `Immutable release tags` ruleset (ID `25040400`) is saved with active enforcement, `v*`
targeting, no bypass actors, and restrictions on updates, deletions and force
pushes. Creation remains allowed. The owner completed GitHub email identity
verification; Safari and the public ruleset API confirm the saved configuration.

After the owner's explicit action-time approval, PyPI registered the pending
publisher for `socialcli`: `engels-florentino/SocialCli`, `release.yml`, environment
`pypi`. Safari showed the success notification and saved publisher row. No package
has been published. The owner also created the separate `socialcli` TestPyPI
account, verified its email and enabled TOTP. After separate explicit approval,
TestPyPI registered the same project/repository/workflow with environment
`testpypi`; its success notification and saved row were verified in Safari.
Both pending publishers are configured; no workflow or upload has run. The public JSON
endpoints for `socialcli` on both indexes previously returned404; a pending
publisher does not reserve the name.

When dispatching the release workflow, select **the reviewed tag** in GitHub's
“Use workflow from” selector, as well as entering that same tag in the `tag` input.
Checking out a tag inside a workflow started from `main` does not make that run a
tag deployment; the environment tag policy applies to the workflow run ref.


## Published pilot — 14 October 2026

Owner explicitly requested publication and approved the protected TestPyPI deployment.
Tag `v0.3.0rc1` points to `4dfb65149c7d7a3784a4fee6a1268623d3ef66cc`.
[Release run38594828306](https://github.com/engels-florentino/SocialCli/actions/runs/38594828306)
completed successfully: build/full tests, Linux and macOS clean-wheel smoke, and OIDC
publication. The independent CI matrices also passed Python3.11,3.12and3.13.
The downloaded GitHub artifacts and actual TestPyPI wheel matched these SHA256s:

- Wheel: `7ea4495e81a6937e6f1689055e9374be66b4391e11f08983ba655cbaa99b509a`.
- Sdist: `2e0a408edf41e9b96bbfefaa6128fae7e768d14e81c38f24aad7ec828aca2bc0`.

Downloaded the wheel from TestPyPI release metadata, checked its hash, installed it
in a clean Python3.12 environment outside the checkout with dependencies from normal
PyPI, and passed the installed smoke for version/help/workspace/encrypted credentials/
packaged guide. The human landing at https://socialcli.app and plain-text /agents
both return200; Safari verified the live copy button. The guide explicitly uses
the pilot origin and never directs agents to fetch single-use authorization links.
Candidate image4dfb651 is healthy with only YouTube enabled. GoogleTesting and
Stripe test-mode restrictions remain. Existing public0.2.6 connections are unchanged.

This is a published test candidate, not a stable PyPI release or proof of actual
Muse/Grok/Dots completion. The owner will perform those real-agent tests.
The separate GitHub Pages deployment hit an external deployment timeout; the
self-hosted socialcli.app deployment and all package release jobs succeeded.

## Meta and TikTok SaaS activation — 2026-10-14

Reused the existing Meta app 1551964416681979 and existing TikTok sandbox
7683933049130649621 under app 7683931582042720277. Provider credentials were
transferred entirely on the server from the previous connection service into
the candidate's protected environment. No customer account grants were migrated.
Facebook, Instagram and TikTok are enabled alongside YouTube; container health
and generated OAuth callback/scopes were verified. Real SaaS account authorization
and publishing have not been performed in this activation.

Meta accepted the Facebook/Instagram callback URLs for both socialcli.app and
pilot.socialcli.app. TikTok sandbox saved the pilot callback, new public website
and legal URLs; its existing target user and user.info.basic/video.upload scopes
remain unchanged, with Direct Post disabled.

TikTok Production remains Draft. Its SaaS description, review explanation, website,
legal URLs and Web callback were saved; socialcli.app URL-prefix ownership was
verified using TikTok's signature file. Nothing was submitted for review. Existing
demo recordings are from the previous version and must be replaced before a
future review submission. The running SaaS continues using sandbox credentials.

Server rollback copies: private/pre-meta-tiktok-service.env and
private/pre-meta-tiktok-compose.yaml (secrets stay server-side). Existing matched
local/Drive backup receipts were checked before deployment, maintenance timers
restored afterwards, and a new encrypted backup was started.

## Provider scope alignment — 2026-10-14

Server image socialcli-saas-candidate:4f49d1b requests TikTok
user.info.basic, video.upload, user.info.stats and video.list for new connections.
Missing read consent fails closed; legacy refresh uses its stored scope contract.
TDD: two failures before fix; 33 provider tests and full 2773-test suite passed.
Fresh review found no important issues. Production TikTok remains Draft with
read scopes documented; sandbox read scopes applied and persisted. No review
submission or Direct Post activation. Google owner approved force-ssl for
Histopast; exact channel verified and one comment thread read completely.
A different video returned 403 and remains an explicit per-video limitation.
Server deployment checked matching Drive backup receipts and resumed maintenance
timers. Health passed and running TikTok authorization scope set verified.
Public TestPyPI candidate remains immutable; this is a server-only scope fix.
