# Meta webhook receiver candidate

Hosted delivery is **live-unverified**. The CLI commands `meta webhook-verify`,
`webhook-ingest`, `webhook-events` and `webhook-reconcile` remain offline helpers
for supplied challenges or saved payloads. They do not configure subscriptions,
read the hosted queue or promise automatic notifications.

The separate SaaS receiver is disabled by default. It has local integration tests;
this is not evidence of Meta delivery, app review or a working subscription.
No deployment or subscription change is included in this implementation.

## Enable only after subscription review

An operator configures `SOCIALCLI_META_WEBHOOK_VERIFY_TOKEN` as a protected secret
and uses the existing configured Meta app credentials. Facebook and Instagram,
when both enabled, must use the same app. The callback is the service's fixed
HTTPS origin followed by `/api/saas/v1/meta/webhook`.

GET verification returns the exact plain-text `hub.challenge` only when
`hub.mode=subscribe` and the single `hub.verify_token` matches. POST verifies
`X-Hub-Signature-256` against the exact raw bytes before parsing. Body size is
limited to 1 MiB, including chunked transfer, and batches to 1000 updates.

Before any separately authorized subscription changes, snapshot the app's
existing subscriptions and Page installation. Preserve real subscriptions. Test
read, set and remove separately using temporary approved changes, then restore
and verify the exact baseline. Read access to Meta Developer MCP does not
authorize subscription changes.

An account owner opens `/account/connections/facebook/webhooks` or
`/account/connections/instagram/webhooks` in their authenticated browser. The form
requires explicit consent to event storage and access by linked assistants,
with origin and CSRF validation. Submitting it makes **GET requests only** to Meta:

1. `/debug_token` verifies validity, app ID, documented Page `profile_id` and
   required scopes. A contradictory token type is rejected; the optional `type`
   field is not required to infer ownership.
2. `/me` verifies the Page identity; Instagram additionally verifies the Page's
   current `instagram_business_account` association.
3. `/{app-id}/subscriptions` must show the exact callback, active object and
   selected fields. Facebook uses `page/feed`; Instagram with Facebook Login uses
   `instagram/comments` and/or `instagram/mentions`.
4. `/{page-id}/subscribed_apps` must include this exact app with `feed` enabled.
   Instagram with Facebook Login also requires installation on its connected
   Page; this candidate deliberately supports that specific Page field.

This does not use the Instagram Login subscription/token model. Missing,
ambiguous, inaccessible or incompatible evidence blocks enrollment. The current
owner, grant and account are checked again after these reads to reject concurrent
disconnects or replacement. Enrollment never sets or removes subscriptions.
The verified local binding expires after at most one day or sooner if the stored
provider authorization expires. Renew it through the same browser review.

## Retention, isolation and backpressure

Events are read hints, not authoritative content. Only field, account, media/comment
IDs, source/receipt times, state, consent receipt and an out-of-order marker are retained. Dedupe
hashes incorporate the full change transiently so same-second edits are distinct;
comment text, author profiles, usernames and arbitrary payload values are not
stored. An Instagram mention is not proof of media ownership or mention-reply
permission.

Bindings and events use the existing encrypted vault, indexed to their owner and
grant. There is no hosted brand-folder store or second database. Each object and
account can have one active owner binding. Signed batches route registered
accounts independently; unknown accounts, unsupported fields and malformed hints
are ignored without discarding valid hints for other owners. Invalid top-level
structure is rejected. All valid hints commit before a successful response.

The queue retains pending and consumed hints for at most **six days** from receipt.
This exceeds Meta's documented 36-hour retry window. Source times older than six
days or more than five minutes in the future are ignored; older observations
within the window are marked out of order. Retained dedupe records count toward
the 2000-record per-owner and 20000-record global limits. At capacity or on storage
failure, the valid batch rolls back and receives 503; no dedupe history is silently
evicted. Provider retries can end after 36 hours: monitor backpressure and repair
availability promptly. These hints do not replace periodic owned reads.

Expired records are removed by the existing maintenance purge, also on callback
and event-list requests. Maintenance must remain scheduled even when there is no
callback traffic. Existing encrypted backups have a maximum six-day horizon;
restores wipe every binding and queued hint before releasing service. They must
be enrolled again against current provider subscriptions.

Disconnect or grant replacement removes dependent binding/event records in the
same database transaction. Owner deletion uses the existing owner-index cleanup.
Revoking an assistant blocks its consumption immediately; the owner's independent
social connection and hints remain available to their other authorized assistants.
Previously issued provider credentials retain their existing provider lifecycle.

## Authenticated consumption

A connection capability's bearer credential authenticates:

```text
GET  /api/saas/v1/connections/{connection_id}/meta/webhook-events?limit=100
POST /api/saas/v1/connections/{connection_id}/meta/webhook-events/{event_id}/ack
```

The service checks the installation, current owner/grant/account/platform, policy
and assistant consent, platform permission and commercial access. The response
contains only this connection's owner/grant/account hints. Reads remain pending
until explicitly acknowledged. Acknowledgment records consumption of a hint;
it does not certify a provider GET, successful reply or completed work. Dedupe
evidence remains until expiry.

Instagram media/caption mentions retain a media ID and `comment_id: null`;
comment mentions also retain the exact comment ID. Consumers must distinguish
these shapes and perform the corresponding media-mention or comment-mention GET
with current account/mention authorization before preparing any action. A mention
can reference another owner's media, so it does not establish ownership. A callback and its consumption never
publish, reply, moderate, react or otherwise trigger a social write. Existing
complete-preview and exact approval requirements still apply.

## Secret rotation

The current app secret comes from the existing provider configuration. During a
coordinated app-secret rotation, an optional protected
`SOCIALCLI_META_WEBHOOK_PREVIOUS_SECRET` may be accepted until the absolute Unix
UTC timestamp `SOCIALCLI_META_WEBHOOK_PREVIOUS_UNTIL`. Startup rejects an absent,
expired or more-than-36-hours-away deadline. Remove both settings afterward;
the old secret stops matching at the deadline even without a restart.

Replace the verification token in coordination with the configured Meta callback;
there is no automatic subscription mutation. Vault encryption-key rotation uses
the existing MultiFernet procedure. Keep secrets out of command arguments,
access logs, support reports and source control, including GET verification query
parameters and `/debug_token` request parameters.

## Live acceptance gate

Task 4 stays open until the deployed receiver captures one genuine event for each
offered subscribed field using an approved disposable fixture, followed by tracing
receipt, owner-bound durable storage, authenticated consumption and an owned GET.
Save those genuine events for offline-helper QA. Capture media/caption mentions
and comment mentions separately; a comment-mention event cannot certify the
media-only shape. Test mention reading and mention reply separately; a comments event cannot certify either. An HTTP 200, a synthetic
signed fixture or a subscription dashboard alone cannot close delivery.

Current Meta documentation was retrieved through Context7 on 2026-10-18:
[verification, signatures, batching and retries](https://developers.facebook.com/docs/graph-api/webhooks/getting-started),
[app subscriptions](https://developers.facebook.com/docs/graph-api/webhooks/subscriptions-edge),
[Instagram with Facebook Login webhooks](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-instagram),
[Instagram event fields](https://developers.facebook.com/docs/graph-api/webhooks/reference/instagram),
[media and comment mention examples](https://developers.facebook.com/documentation/instagram-platform/webhooks/examples),
[Page webhook setup](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-pages)
and [token inspection](https://developers.facebook.com/docs/graph-api/reference/debug_token).
