# Unified inbox drafting and supervised batches

Use superpowers executing-plans inline; TDD and one independent final review.
Extends approved agent-readiness critique4/6, preserving explicit brand/preview,
no automatic writing, no media processing/storage and existing immutable journals.

## Contract and implementation

- `inbox-review --brand --json`: offline view of observed comments with linked
  durable reply proposals/outcomes. Current account only; explicit coverage limits
  (saved observations are not a complete or live inbox). Never infer absence of replies.
- `inbox-draft --file replies.yml --brand --dry-run --json --timeout`: explicit
  version1 list of platform/account_id/comment_id/text selected from saved inbox.
  Validate every selection before provider reads; prepare through existing services.
  YouTube only top-level replies; nested-target replies are explicitly unsupported.
  Meta uses inbox-bound drafts and its existing rate limit/journal service.
  Compare fresh target author/text/media with observed target before proposing.
  Preserve prepared child references on partial failure, never apply automatically.
- On success prepare existing immutable CommunityBatch and show full preview on
  stderr with JSON result on stdout. One fingerprint is approved explicitly.
- `changes prepare-batch/apply-batch/reconcile-batch/status-batch --json`: uniform
  result/errors; exact existing --approval remains supported, no implicit yes.
  Batch dispatch of inbox-bound Meta drafts must use apply/reconcile_inbox_draft,
  preserving frequency limits and durable status, then return child ChangeSet.
- Offline view joins the actual child stores rather than creating a second journal.
  Uncertain prior attempts prevent automatic fresh draft creation; verified replies
  are shown as observed results, not assumed to remain visible remotely forever.
- Tests use fictional accounts and HTTP simulations only. Full suite, guide/build,
  installed smoke and independent review before local commit. No real writes/release.

## Tasks

1. Community batch JSON/status and inbox-bound dispatch; RED→GREEN approval,
   interruption, partial outcomes, no replay and rate limits.
2. Unified review/draft orchestration; RED→GREEN mixed provider selection,
   changed target, explicit text, wrong account, nested reply, failed second prepare,
   saved evidence and no external writes. Guide + installed full workflow smoke.

## Review focus

No batch path bypasses Meta inbox locks/rate limits; partial draft failures retain
IDs; wrong brand/account/author and changed target fail closed; cached empty inbox
never means no comments; no uncertain retry or new reply silently sent; exact full
preview remains the sole approval input. Scope does not claim TikTok comment support.

## Verified completion of this increment

Both tasks implemented. Independent review found no Critical and2 Important issues:
Meta child reference lost after secondary inbox save failure; historical account
journals blocking unaffected current accounts. Both regression tests observed RED,
fixed in one pass; foreign-brand journals still fail closed. No deferred minors.

Final full suite:2650 passed in54.65s, one existing upstream Starlette warning.
19 new workflow tests also passed against installed wheel modules in isolated
candidate Python, including mixed provider prepare→approve→apply→offline review.
Every loaded socialctl module was asserted to originate in installed site-packages.
Artifact verifier and installed release smoke passed; no real provider writes.
Wheel SHA256:486e9d6eea358e66bf0aa0bc60256b94ff080caed93fff1ce41068aca7318b78.
Sdist SHA256:1154be61f072f3cfa9e2d5783b9ff6dcf1c8b2ddec8dd6b2d8a06b0ce21c776b.

Scope decision: extend existing CLI/inbox/batch services, no new web dashboard or
parallel journal; a future dashboard still needs its own UI. This completes the
candidate implementation for this workflow, not real agent/platform acceptance or
SaaS rollout. Pilot DNS A was rechecked and remains absent; Drive authorization,
external recovery, protected package publication and live pilots remain open.
