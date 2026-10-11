# SocialCli 0.2.4

October 11, 2026. Corrects provider-read failures reported against 0.2.3.

- YouTube replies can use verified observations when pagination fields are
  missing, explicitly retaining incomplete coverage. Malformed fields, changed
  totals, known truncation, invalid targets and uncertain prior writes still
  block the action. Search retains its documented approximate-total behavior.
- Facebook likes can target a verified top-level comment on an owned Page post.
  The proposal uses the post as `target_id` and the comment as `comment_id`,
  includes comment text, and requires explicit Page viewer like state. Existing
  approval, preflight, durable receipt and no-replay protections remain in place.
  See [the proposal example](capabilities.md#facebook-comment-likes).
- Inbox reads reserve time for later configured providers. Meta enumeration
  uses only IDs and ownership; successful media pages survive later request
  failures. Partial observations never establish an empty inbox.
- YouTube metrics retain fetched account/video data when optional enrichment or
  audience reads fail. Warnings appear in snapshots, Markdown and structured
  coverage; expired enrichment does not trigger further optional requests.
  Multi-provider stats also reserve time for later providers.

## Verification

Final full suite on **Python 3.13.5: 2,148 passed**, 36.66 seconds. One upstream
Starlette test-client deprecation warning remains. Regression tests were observed
failing before their corrections. Independent read-only review findings were
fixed and rechecked. `git diff --check`, source/wheel build and installed-wheel
command smoke checks passed. The clean installation imports from site-packages
outside the checkout. CI now includes Python 3.13 alongside 3.11 and 3.12.

The first Python 3.13 full run exposed a timing-sensitive test using a 10 ms
wall-clock budget. It was made deterministic with a controlled clock while
retaining its completed-platform and deadline assertions; the final full run
above passed.

## Update an existing workspace

Use the interpreter belonging to the existing workspace, retaining its credential
store and environment configuration:

```bash
.venv/bin/python -m pip install --upgrade --no-cache-dir "git+https://github.com/engels-florentino/SocialCli.git@v0.2.4"
.venv/bin/python -c 'from importlib.metadata import version; print(version("socialcli"))'
```

No new account connection or broker service upgrade is required by these client
changes. Existing creator consent still determines access.

## Remaining external verification

These checks use fictional provider transports. Repeat the reported reads in
the affected creator workspace after upgrading. Incomplete YouTube lists still
exit 1 and expose verified rows; reply preparation can continue only under its
explicit partial-coverage safeguards. Meta viewer-state availability, grants and
object eligibility must be checked against the actual connected Page. Nested
Facebook comment likes are not exposed. No live likes, replies or publications
were performed for this release.
