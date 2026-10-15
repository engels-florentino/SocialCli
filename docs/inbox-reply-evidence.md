# Platform reply evidence

Inbox reads recover creator replies from YouTube, Facebook and Instagram even
when the workspace has no local reply journal, including a fresh installation
or a reattached account. No provider write occurs during this reconciliation.

`inbox-review` keeps two distinct fields:

- `reply_state` describes the workflow: `observed`, `draft`, `replied`, `blocked`
  or `uncertain`. `replied` can come from a verified local write or a verified
  owner reply observed on the platform. An uncertain local write stays uncertain
  even when another owner reply exists; it must be reconciled by its own receipt.
- `platform_reply_state` is `owner_reply_found`, `no_owner_reply_found` or
  `unknown`. `observed` does **not** mean unreplied. Older saved observations and
  incomplete reads without positive evidence have unknown platform coverage.

`reply_evidence` stores the platform, exact account, media, direct parent,
YouTube thread where applicable, reply IDs, observation time and coverage.
Owner authorship requires the exact authenticated account ID. Names, matching
text and missing authors never prove ownership. A reply to a top-level comment
is evidence for that parent only; it does not mark every nested sibling as
answered.

YouTube traverses the parent's replies with `comments.list`, including every
available page; a verified zero `totalReplyCount` avoids an empty reply request
on the inbox scan. Meta uses each verified parent's comments/replies edge and
collects owner replies before the inbox's `--since` filter excludes older
observations. This can recover an old answer to a parent observed today.

A partial read can prove an owner reply exists, but cannot prove none exists.
Missing author identity, invalid membership, failed pages, deadlines and a
resumed Meta tail keep absence unknown. A later incomplete scan preserves a
previously observed positive reply; its original observation timestamp remains
visible. A new top-level Meta read invalidates an older negative result until
the replies are checked again. Coverage is limited to provider-visible replies
for the requested scope and timestamp, not an atomic snapshot or a promise
about hidden/deleted comments.

Before `inbox-draft` or native `comments draft` creates or reuses an immutable
proposal, it performs a bounded live
reply read for that selected parent. Existing owner replies reject the default
draft with `existing_platform_reply`; incomplete coverage or unavailable author
identity rejects it with `reply_coverage_unknown`. Refresh the inbox and inspect
the evidence. Only selected parents receive these extra preparation reads. Both inbox entrypoints
share the same guard; missing, unknown or stale negative saved evidence never
replaces a fresh reply traversal.
A response added after this check remains a possible concurrent change; the
existing exact-preview, ownership and write-journal protections still apply.

The lower-level explicit comment reply workflow remains available for an
intentional additional response after inspection and approval of its exact
preview. Platform evidence does not adopt an existing remote reply as the receipt
for an uncertain local write, create proposals, approve replies or replay writes.
TikTok comment support is unchanged and remains unavailable through this client.

The implementation uses the documented [YouTube reply list](https://developers.google.com/youtube/v3/docs/comments/list),
[YouTube thread count](https://developers.google.com/youtube/v3/docs/commentThreads),
[Facebook comment replies](https://developers.facebook.com/docs/graph-api/reference/comment/comments),
[Graph pagination](https://developers.facebook.com/docs/graph-api/results), and
[Instagram comment replies](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-comment/replies).
