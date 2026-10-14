# Candidate distribution and publication

Candidate version: **0.3.0rc1**. The public baseline is still 0.2.6. A candidate
wheel, successful tests or a registered PyPI username do not establish a public
package, provider approval or deployment of the SaaS service.

The agent-facing guide is generated as `docs/agents.md` from the packaged
`socialctl/ui/assets/agents.md` and served at `/agents` by the candidate service.
Route that exact path through NPM when deploying the candidate. Keep `/agents`
public, but never cache or fetch single-use `/account/pair/` or `/connect/` links
on the user's behalf. The guide currently labels all PyPI instructions as pending.
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
2. Create the reviewed `v0.3.0rc1` tag and dispatch `Reviewed Python release` with
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
has been published. TestPyPI publisher setup remains pending. The public JSON
endpoints for `socialcli` on both indexes previously returned404; a pending
publisher does not reserve the name.

When dispatching the release workflow, select **the reviewed tag** in GitHub's
“Use workflow from” selector, as well as entering that same tag in the `tag` input.
Checking out a tag inside a workflow started from `main` does not make that run a
tag deployment; the environment tag policy applies to the workflow run ref.
