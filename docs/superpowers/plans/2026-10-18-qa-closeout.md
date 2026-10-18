# SocialCli QA closeout implementation plan

> For agentic workers: use superpowers:executing-plans. Continue natively under the owner’s instruction to leave no pending work; preserve prior approvals.

**Goal:** Resolve reproducible QA defects, exercise eligible live functions with reversible fixtures, and deliver reviewed fixes to the hosted service and assistant client.

**Architecture:** Keep canonical source on main. Keep provider journals and approvals intact. Classify each capability by live evidence, automated evidence and provider restrictions; never bypass uncertain-write protection to make a test pass.

**Tech Stack:** Python, Typer, HTTPX, pytest, SaaS container, existing provider APIs.

**Spec:** Owner’s complete-QA request; private capability-matrix.csv and qa-results.md.

## Global constraints
- Histopast only; supplied existing media only.
- Full previews before new remote writes. Preserve all sequence approvals.
- No false abuse reports, real charges, lost-response replay or destructive edits to existing content.
- Preserve unknown API values, account binding and immutable package versions.

## Review focus
- Time-pagination next URLs cannot change host, path or approved query scope.
- Repeated insight metric IDs must not double-count or overwrite contradictory values.
- Provider count disagreements must preserve rows and explicit incomplete coverage.
- Retention tombstones must preserve uncertain-write replay blocks.
- Deployment must preserve credentials/database/runtime restrictions and rollback.

## Execution
- [ ] Reproduce remaining read/mutation failures; add regression before each code fix.
- [ ] Correct bounded Meta insights pagination and moderation eligibility where documented.
- [ ] Select existing harmless upload fixtures; prepare complete previews and carry out approved publish/verify/cleanup sequences.
- [ ] Exercise other eligible management surfaces with temporary objects; verify cleanup.
- [ ] Full suite, independent review, installed artifact and archive checks.
- [ ] Stage reviewed server image, check fresh backup/rollback, deploy and verify platform-expansion flow.
- [ ] Prepare immutable client release through protected workflow; update Muse and verify actual installed behavior.
- [ ] Reconcile capability evidence and remaining external gates honestly.
