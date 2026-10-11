# SocialCli 0.2.5

October 11, 2026. Reduces inbox request cost and preserves valid comments when
individual Meta observations cannot be verified.

- Inbox-only clients reuse the pinned actor, owned media and verified parent
  bindings during one GET-only scan. Prepare/apply/reconcile retain fresh remote
  checks. In a fictional one-video fixture with six comments and no replies,
  requests fall from 34 to 3. With six reply-bearing threads they fall from 34
  to 9. A Meta top-level/reply scan falls from 9 requests to 4.
- Empty YouTube reply lists are skipped only for an exact integer reply count of
  zero. Unknown counts still trigger reads. A deadline stops subsequent reply
  reads and retains all already verified top-level observations; it is no longer
  misreported repeatedly as unrelated transport failures.
- An invalid Meta comment no longer discards other independently valid comments.
  Missing identity/text/author, incompatible parents, invalid times and conflicting
  duplicate IDs remain unverified. Coverage is partial; complete watermarks do
  not advance. Meta coverage includes counts of own, older and unverified rows
  skipped during the scan.
  Missing creation times are retained with incomplete interval coverage.
- Bounded reads honor environment proxy/NO_PROXY and CA configuration while
  retaining whole-operation deadlines, redirect blocking, byte caps and no
  automatic write retries. Fixed transport error labels distinguish connection,
  proxy, read and protocol failures without echoing raw exception messages.

## Metrics and time limits

Full YouTube stats still requests retention and traffic for each selected video,
plus audience data. It can require many Analytics requests. Use SocialCli's own
`--timeout` so partial data can be saved and reported; an external timeout that
kills the process first prevents the final report. `--since` filters video
publication dates, not the reporting period.

## Verification

Final full suite on Python 3.13.5: **2,173 passed**, 47.73 seconds. One upstream
Starlette test-client deprecation warning remains. Regression tests failed
before their corrections. Independent read-only review found no remaining
Critical or Important issues. Source/wheel build, installed-wheel imports and
CLI help outside the checkout, and `git diff --check` passed.

## Update and verify

Keep the existing workspace and credential environment:

```bash
.venv/bin/python -m pip install --upgrade --no-cache-dir "git+https://github.com/engels-florentino/SocialCli.git@v0.2.5"
.venv/bin/python -c 'from importlib.metadata import version; print(version("socialcli"))'
socialcli stats --brand MyBrand --only youtube --json --timeout 80
socialcli inbox --brand MyBrand --since 2026-10-09T00:00:00-04:00 --json --timeout 150
```

These corrections do not require reconnecting accounts or upgrading the broker.
No live likes, replies or publications were performed. Provider transport tests
use fictional accounts; actual latency, visibility and granted permissions must
still be checked in the affected creator environment. Missing YouTube pagination
metadata remains explicitly incomplete and does not prove absence.
