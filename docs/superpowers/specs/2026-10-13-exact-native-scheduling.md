# Exact native scheduling

Implements the approved SaaS scheduling release gate and scheduler critique without
customer media hosting. Work remains in feat/socialcli-saas; no remote writes.

New manifests opt in with `schedule: {mode: "exact", timezone: "Europe/Madrid"}`.
Their ISO publish_at must carry the actual local UTC offset for that IANA zone.
Preserve the requested instant, including seconds; reject past times, subsecond
values, nonexistent local times, mismatched offsets and occupied instants. The
explicit offset disambiguates a repeated DST hour. Never round or overflow exact
requests. Missing schedule retains legacy 09:00 America/New_York daily behavior.

Persist `schedule_mode` on NativeJob, default `legacy_daily`, with `exact` opt-in.
Bind exact mode in the immutable approval. Preserve the exact legacy approval
serialization so existing digests remain valid. Legacy jobs and pending actions
must survive reading/writing, including actions serialized before the new field.

A legacy reservation occupies its whole New York date; an exact reservation
occupies one UTC instant. Mixed reservations conflict if either legacy reservation
covers the other's instant. Two exact jobs may occupy different instants on one
date; the same instant conflicts regardless of displayed timezone. Account and
platform isolation remains. Pending reschedules hold both old and new reservations
until verified. External calendar observations keep all historical reservations;
exact jobs conflict with matching external instants, legacy jobs with the date.
Every claim and reschedule boundary checks current external reservations.

Keep the current SQLite structure and transactional BEGIN IMMEDIATE writes. Use
version 3 when admitting an exact job, so old clients reject rather than misread
its slot keys. Read-only access must not mutate or upgrade databases. No rewrite
of old jobs, evidence, approval digests or reservations. Version 3 must never be
downgraded by later writes. Legacy-only databases remain version 2.

Require complete fresh calendar coverage for the customer's local publication day.
Existing API eligibility, provider time windows, explicit previews/approval, immutable
media, uncertainty and reconciliation protections apply. Cancellation/rescheduling
remain approved official-UI actions. Exact mode does not authorize a remote write.

No new UI styles, dependencies, hosting, media transformations or provider claims.
Update the shared packaged/web guide and delivery discovery. Real provider pilots,
Linux supervision, insights and the remaining critique/launch gates stay tracked.
