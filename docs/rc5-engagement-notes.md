# rc5 engagement behavior

Community batches remain sequential, resumable operations, not atomic cross-provider transactions. Each child must verify before the next starts. A received comment receipt now permits one additional bounded, read-only reconciliation during apply, covering delayed provider readback without another POST. A lost receipt, incomplete evidence or contradictory identity still stops the batch. Reconciliation never starts pending children.

Status output checks current child journals, and failed reconciliation retains each child’s durable status and safe error even when the historical batch journal is incomplete.

Batch JSON preserves existing keys and adds all children to `outcomes`, including `not_attempted`, with child index, provider, platform and change ID. `summary` includes total and verified/pending/uncertain/blocked counts and ID lists. A partial batch with only untouched children reports `batch_pending` and `action: apply_remaining`; an uncertain child reports `operation_uncertain` and `action: reconcile`. Continue the same approved batch with its exact fingerprint, retaining all verified outcomes. Progress is on stderr; stdout contains one JSON document.

`inbox-draft` errors include `failed.child_index` (one-based), platform, account/comment IDs, stage and a safe reason. Completed immutable children remain in `prepared` after a later failure. Diagnostics do not reproduce arbitrary provider or storage exception text.

Facebook reply preparation accepts an unavailable external comment author when exact comment membership and media ownership can be verified. The author remains null in the approved baseline. Changes to text, identity or membership block the write. Own-comment edit/delete and created-reply verification still require the authenticated account's exact author ID.

A nested observation can explicitly map a new thread reply to its observed top-level parent:

```yaml
version: 1
replies:
  - platform: youtube
    account_id: fictional-channel
    comment_id: observed-reply-id
    reply_parent_id: observed-top-level-comment-id
    text: Exact supplied reply text
```

The source and mapped parent must both be observed for the same account/media/thread. The parent must be top-level and satisfy the existing eligibility rules. Source membership/text/author are read again during preparation and immediately before the write. The full preview binds `before.reply_context` and the actual write parent. This is supported through the existing YouTube, Facebook and Instagram reply edges; it does not invent a deeper provider nesting relationship or imply a mention. Multiple selections mapping to the same parent are rejected. Existing replied/uncertain parent protections remain in effect. Inbox review links the resulting journal to both the parent and selected source observation.

The YouTube contract explicitly uses the top-level comment as the API reply parent: https://developers.google.com/youtube/v3/guides/implementation/comments . Context7 lookup was attempted but exhausted its quota; Meta reference reads returned HTTP 429/unavailable. No new undocumented Meta endpoint or deeper-nesting behavior is claimed.

Validation uses fictional local HTTP transports only: delayed readback advances two children with exactly two writes; lost receipts stop and never replay; partial/reconciled batches retain pending child IDs and correct next actions; hidden Facebook author replies work while own mutations and author drift stay blocked; explicit nested mappings work on YouTube/Facebook/Instagram and changed/unrelated contexts fail before writes.
